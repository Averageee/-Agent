from __future__ import annotations

import hashlib
import json
from datetime import date

from .data import Catalog
from .faults import FaultInjector
from .llm import SYSTEM_PROMPT, Decision, LLMError
from .state import RunState, Store
from .tools import TOOLS, ToolContext, available_hours, execute_tool, summarize

STAGES = (
    ("search_materials", "检索素材"),
    ("estimate_cost", "成本估算"),
    ("generate_script", "生成脚本"),
    ("save_plan", "保存方案"),
)
NUDGE = "请调用一个工具，或使用 finish / give_up 结束。"
RESUME_NOTE = "续跑：之前的工具故障已排除。已成功步骤的结果仍然有效，不要重复，只补做失败或未完成的步骤。"


def validate_request(raw: dict | None) -> tuple[dict, list[str], dict]:
    req = dict(raw or {})
    missing: list[str] = []
    invalid: dict[str, str] = {}

    topic = str(req.get("topic") or "").strip()
    if not topic:
        missing.append("topic")

    budget = req.get("budget")
    if budget in (None, ""):
        missing.append("budget")
        budget = None
    else:
        try:
            value = float(budget)
        except (TypeError, ValueError):
            invalid["budget"] = "必须是数字"
            budget = None
        else:
            if value <= 0:
                invalid["budget"] = "必须大于 0"
                budget = None
            else:
                budget = int(value) if value.is_integer() else value

    today = str(req.get("today") or date.today().isoformat())
    today_date = None
    try:
        today_date = date.fromisoformat(today)
    except ValueError:
        invalid["today"] = "日期格式应为 YYYY-MM-DD"

    deadline = req.get("deadline")
    if deadline in (None, ""):
        missing.append("deadline")
        deadline = None
    else:
        deadline = str(deadline)
        try:
            d = date.fromisoformat(deadline)
        except ValueError:
            invalid["deadline"] = "日期格式应为 YYYY-MM-DD"
        else:
            if today_date and d <= today_date:
                invalid["deadline"] = f"必须晚于 today（{today}）"

    return {"topic": topic, "budget": budget, "deadline": deadline, "today": today}, missing, invalid


def idempotency_key(req: dict) -> str:
    basis = json.dumps({"topic": req["topic"].lower(), "budget": req["budget"], "deadline": req["deadline"]},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def task_plan(state: RunState) -> list[dict]:
    plan = []
    reused = state.outcome.get("reason") == "idempotent_hit"
    for tool, label in STAGES:
        tried = [s for s in state.steps if s.action == tool]
        if reused:
            status = "reused"
        elif not tried:
            status = "pending"
        else:
            status = tried[-1].status if tried[-1].ok else "failed"
        plan.append({"stage": label, "tool": tool, "status": status, "calls": len(tried),
                     "failed_calls": sum(1 for s in tried if s.status == "failed")})
    return plan


def build_result(state: RunState, store: Store) -> dict:
    req = state.request
    budget = req.get("budget")
    estimates = [s.result for s in state.ok_steps("estimate_cost")]
    saves = state.ok_steps("save_plan")
    chosen = state.find_result(saves[-1].args.get("estimate_id")) if saves else None
    saved_plan = store.saved_plan(state.idempotency_key) if state.status == "completed" else None
    if chosen:
        plan_total = chosen["total_cost"]
    elif saved_plan:
        plan_total = saved_plan["cost"]["total_cost"]
    else:
        plan_total = None
    tool_results = {}
    for s in state.steps:
        if s.ok:
            tool_results[s.action] = s.result
    return {
        "run_id": state.run_id,
        "status": state.status,
        "reason": state.outcome.get("reason"),
        "message": state.outcome.get("message"),
        "llm": state.llm,
        "attempt": state.attempt,
        "request": req,
        "idempotency_key": state.idempotency_key,
        "task_plan": task_plan(state),
        "steps": [{k: getattr(s, k) for k in ("index", "attempt", "action", "args", "status", "summary", "reason")}
                  for s in state.steps],
        "tool_results": tool_results,
        "cost": {
            "budget": budget,
            "plan_total": plan_total,
            "remaining": round(budget - plan_total, 2) if budget is not None and plan_total is not None else None,
            "estimates": [{k: e[k] for k in ("id", "material_id", "script_engine", "total_cost", "within_budget", "within_deadline")}
                          for e in estimates],
        },
        "saved_plan": saved_plan,
        "gap": state.outcome.get("gap"),
    }


class ContentPlanningAgent:
    def __init__(self, llm, store: Store, catalog: Catalog | None = None,
                 faults: FaultInjector | None = None, max_steps: int = 12):
        self.llm = llm
        self.store = store
        self.catalog = catalog or Catalog()
        self.faults = faults or FaultInjector()
        self.max_steps = max_steps

    def run(self, request: dict) -> dict:
        req, missing, invalid = validate_request(request)
        state = RunState(run_id=RunState.new_id(), request=req, llm=self.llm.name)
        if missing or invalid:
            parts = []
            if missing:
                parts.append("缺少 " + "、".join(missing))
            if invalid:
                parts.append("；".join(f"{k} {v}" for k, v in invalid.items()))
            state.status = "incomplete"
            state.outcome = {"reason": "invalid_input",
                             "message": "，".join(parts) + "；未调用任何工具，请补全后重试",
                             "gap": {"missing_fields": missing, "invalid_fields": invalid}}
            return self._finish(state)

        state.idempotency_key = idempotency_key(req)
        existing = self.store.saved_plan(state.idempotency_key)
        if existing:
            state.status = "completed"
            state.outcome = {"reason": "idempotent_hit",
                             "message": (f"相同请求已于 {existing['saved_at']} 保存方案 {existing['plan_id']}"
                                         f"（run {existing['run_id']}），直接返回，未重复调用工具或计费")}
            return self._finish(state)

        state.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({**req, "available_hours": available_hours(req, self.catalog)},
                                                   ensure_ascii=False)},
        ]
        return self._loop(state)

    def resume(self, run_id: str) -> dict:
        state = self.store.load_run(run_id)
        if state.status == "completed" or state.outcome.get("reason") == "invalid_input":
            return self._finish(state)
        state.attempt += 1
        state.status = "running"
        state.outcome = {}
        state.llm = self.llm.name
        if state.messages:
            state.messages.append({"role": "user", "content": RESUME_NOTE})
        return self._loop(state)

    def _loop(self, state: RunState) -> dict:
        ctx = ToolContext(state=state, store=self.store, catalog=self.catalog, faults=self.faults)
        used = 0
        while state.status == "running":
            if used >= self.max_steps:
                state.status = "incomplete"
                state.outcome = {
                    "reason": "max_steps_reached",
                    "message": f"已达到单次运行步数上限 {self.max_steps}，任务未完成；已成功步骤已保留，可 --resume 续跑",
                    "gap": {"pending_stages": [p["stage"] for p in task_plan(state) if p["status"] != "success"]},
                }
                break
            try:
                decision = self.llm.decide(state, state.messages)
            except LLMError as e:
                state.status = "incomplete"
                state.outcome = {"reason": "llm_error", "message": f"模型调用失败：{e}；已成功步骤已保留，可 --resume 续跑"}
                break
            used += 1
            self._apply(state, ctx, decision)
            self.store.save_run(state)
        return self._finish(state)

    def _apply(self, state: RunState, ctx: ToolContext, d: Decision) -> None:
        if d.action in TOOLS:
            status, result, error = execute_tool(ctx, d.action, d.args)
            state.add_step(action=d.action, args=d.args, status=status, summary=summarize(d.action, status, result, error),
                           reason=d.reason, result=result, error=error)
            self._reply(state, d, {"status": status, "result": result, "error": error})
        elif d.action == "finish":
            if not state.ok_steps("save_plan"):
                state.add_step(action="finish", args=d.args, status="rejected",
                               summary="护栏拒绝：方案尚未保存成功，不能结束", reason=d.reason)
                self._reply(state, d, {"status": "rejected", "error": "方案尚未保存成功，不能 finish"})
                return
            summary = str(d.args.get("summary") or "方案已保存")
            state.add_step(action="finish", args=d.args, status="final", summary=summary, reason=d.reason)
            self._reply(state, d, {"status": "accepted"})
            state.status = "completed"
            state.outcome = {"reason": "finished", "message": summary}
        elif d.action == "give_up":
            message = str(d.args.get("message") or d.reason or "模型判断无法完成")
            state.add_step(action="give_up", args=d.args, status="final", summary=message, reason=d.reason)
            self._reply(state, d, {"status": "accepted"})
            state.status = "incomplete"
            state.outcome = {"reason": "gave_up", "message": message, "gap": d.args.get("gap") or {}}
        else:
            state.add_step(action=d.action or "no_action", args=d.args, status="rejected",
                           summary=f"无效动作 {d.action}，已提示模型重新选择", reason=d.reason)
            if d.raw and d.call_id:
                self._reply(state, d, {"status": "rejected", "error": f"未知工具 {d.action}"})
            else:
                state.messages.append({"role": "assistant", "content": d.reason or ""})
                state.messages.append({"role": "user", "content": NUDGE})

    @staticmethod
    def _reply(state: RunState, d: Decision, payload: dict) -> None:
        if not d.raw or not d.call_id:
            return
        state.messages.append(d.raw)
        state.messages.append({"role": "tool", "tool_call_id": d.call_id,
                               "content": json.dumps(payload, ensure_ascii=False)})

    def _finish(self, state: RunState) -> dict:
        self.store.save_run(state)
        return build_result(state, self.store)