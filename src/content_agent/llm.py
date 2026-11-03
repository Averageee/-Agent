from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .tools import tool_schemas

SYSTEM_PROMPT = """你是内容活动规划 Agent，帮助运营在预算与截止时间内完成一份内容活动方案。
可用工具：search_materials、estimate_cost、generate_script、save_plan；控制动作：finish、give_up。
规则：
1. 每次只调用一个工具，并在 reason 中用一句话写明依据。
2. 方案费用 = 素材授权费 + 脚本引擎费用，以 estimate_cost 的返回为准，不要自行计算。
3. 估算超预算或超工期时，改选其他素材或更便宜的脚本引擎（ai 费用 2，template 费用 1），并重新估算。
4. 工具失败时根据错误信息调整：换参数、换替代方案，或停止；不要重复已经成功的步骤。
5. save_plan 必须引用同一素材、同一引擎的 estimate_id 与 script_id。
6. 只有 save_plan 成功后才能 finish；没有可行方案时调用 give_up，并在 gap 中写明缺口（如预算差额）。"""


class LLMError(Exception):
    pass


@dataclass
class Decision:
    action: str
    args: dict = field(default_factory=dict)
    reason: str = ""
    call_id: str | None = None
    raw: dict | None = None


def _give_up(message: str, gap: dict, reason: str) -> Decision:
    return Decision("give_up", {"message": message, "gap": gap}, reason)


class MockLLM:
    """离线策略：只读取 RunState 中的观察结果来选择下一步。

    用于在没有模型时验证 ReAct 流程、状态与护栏，不代表真实模型的决策质量。
    """

    name = "mock"

    def decide(self, state, messages) -> Decision:  # noqa: C901
        req = state.request
        cur = [s for s in state.steps if s.attempt == state.attempt]
        prefix = "续跑：沿用已成功步骤。" if state.attempt > 1 and not cur else ""

        def failures(action: str, **match) -> int:
            return sum(1 for s in cur if s.action == action and s.status == "failed"
                       and all(s.args.get(k) == v for k, v in match.items()))

        def latest_ok(action: str, **match):
            for s in reversed(state.steps):
                if s.ok and s.action == action and all(s.result.get(k) == v for k, v in match.items()):
                    return s
            return None

        search = latest_ok("search_materials")
        if search is None:
            n = failures("search_materials")
            if n >= 2:
                return _give_up("素材检索连续 2 次失败，无法形成方案", {"missing": "materials", "failed_step": "search_materials"},
                                "检索工具持续不可用，继续重试意义不大，停止并报告")
            reason = "尚无素材，先按主题检索" if n == 0 else "上次检索失败（疑似临时故障），重试一次"
            return Decision("search_materials", {"topic": req["topic"]}, prefix + reason)

        materials = search.result["materials"]
        if not materials:
            return _give_up(f"没有与主题「{req['topic']}」匹配的素材", {"missing": "materials"}, "检索结果为空，没有可规划的素材")

        engine = "template" if failures("generate_script", engine="ai") else "ai"
        partial_note = "（检索仅部分成功，只在返回的素材中选择）" if search.result.get("partial") else ""
        rejected: list[dict] = []

        for m in materials:
            est_step = latest_ok("estimate_cost", material_id=m["id"], script_engine=engine)
            if est_step is None:
                if failures("estimate_cost", material_id=m["id"], script_engine=engine) >= 2:
                    return _give_up("成本估算连续失败，无法校验预算", {"failed_step": "estimate_cost"}, "估算工具不可用，无法保证不超预算，停止")
                if rejected:
                    r = rejected[-1]
                    if not r["within_budget"]:
                        why = f"{r['material_id']} 估算 {r['total_cost']} 超出预算 {r['budget']}"
                    else:
                        why = f"{r['material_id']} 需 {r['total_hours']}h，超过可用 {r['available_hours']}h"
                    reason = f"{why}，改选次优候选 {m['id']}（质量 {m['quality']}）"
                elif engine == "template":
                    reason = f"AI 脚本引擎失败，改用模板引擎，费用结构变化，需重新估算 {m['id']}"
                else:
                    reason = f"先估算质量最高的候选 {m['id']}{partial_note}"
                return Decision("estimate_cost", {"material_id": m["id"], "script_engine": engine}, prefix + reason)

            est = est_step.result
            if not (est["within_budget"] and est["within_deadline"]):
                rejected.append(est)
                continue

            scr_step = latest_ok("generate_script", material_id=m["id"], engine=engine)
            if scr_step is None:
                if failures("generate_script", material_id=m["id"], engine=engine):
                    return _give_up("脚本生成在 AI 与模板引擎下均失败；已成功的检索与估算已保留，可用 --resume 续跑",
                                    {"failed_step": "generate_script", "material_id": m["id"]},
                                    "两种脚本引擎都不可用，没有替代方案，停止并保留成功结果")
                reason = f"{m['id']} 估算 {est['total_cost']} ≤ 预算 {est['budget']} 且工期可行，生成脚本（{engine}）"
                return Decision("generate_script", {"material_id": m["id"], "engine": engine}, prefix + reason)

            save_step = latest_ok("save_plan")
            if save_step is None:
                n = failures("save_plan")
                if n >= 2:
                    return _give_up("方案保存连续失败", {"failed_step": "save_plan"}, "保存工具持续失败，停止并保留已生成的脚本与估算")
                reason = "预算、工期、脚本均已就绪，保存方案" if n == 0 else "上次保存失败，重试保存"
                return Decision("save_plan", {"material_id": m["id"], "script_id": scr_step.result["id"], "estimate_id": est["id"]},
                                prefix + reason)

            return Decision("finish", {"summary": f"已保存方案 {save_step.result['id']}，费用 {est['total_cost']}/{est['budget']}"},
                            "方案已保存，任务完成")

        min_cost = min(e["total_cost"] for e in rejected)
        min_hours = min(e["total_hours"] for e in rejected)
        avail = rejected[0]["available_hours"]
        gap = {
            "budget": req["budget"],
            "min_cost": min_cost,
            "budget_gap": round(max(0, min_cost - req["budget"]), 2),
            "available_hours": avail,
            "min_hours": min_hours,
            "hours_gap": round(max(0, min_hours - avail), 2),
            "script_engine": engine,
        }
        message = (f"全部 {len(rejected)} 个候选均不可行：最低费用 {min_cost}（预算 {req['budget']}），"
                   f"最短工时 {min_hours}h（可用 {avail}h）")
        return _give_up(message, gap, "所有候选都已估算且不可行，没有替代方案，返回未完成及缺口")


class OpenAICompatLLM:
    """任意 OpenAI 兼容 Chat Completions 接口（需支持 function calling）。"""

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", timeout: int = 60):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.name = f"openai-compat:{model}"

    def decide(self, state, messages) -> Decision:
        payload = {"model": self.model, "messages": messages, "tools": tool_schemas(), "tool_choice": "auto", "temperature": 0}
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise LLMError(str(e)) from e
        try:
            msg = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"响应格式异常：{str(body)[:300]}") from e
        calls = msg.get("tool_calls") or []
        if not calls:
            return Decision("no_action", {}, (msg.get("content") or "").strip())
        call = calls[0]
        fn = call.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments 不是对象")
        except ValueError:
            args = {"_invalid_arguments": fn.get("arguments")}
        reason = str(args.pop("reason", "") or msg.get("content") or "").strip()
        raw = {"role": "assistant", "content": msg.get("content"), "tool_calls": [call]}
        return Decision(fn.get("name") or "no_action", args, reason, call_id=call.get("id"), raw=raw)


def build_llm(kind: str | None = "mock"):
    kind = (kind or "mock").lower()
    if kind == "mock":
        return MockLLM()
    if kind == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise LLMError("LLM_PROVIDER=openai 需要设置 OPENAI_API_KEY")
        return OpenAICompatLLM(key, os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                               os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    raise LLMError(f"未知 LLM_PROVIDER：{kind}")