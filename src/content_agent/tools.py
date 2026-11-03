from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data import Catalog
    from .faults import FaultInjector
    from .state import RunState, Store


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    state: "RunState"
    store: "Store"
    catalog: "Catalog"
    faults: "FaultInjector"

    @property
    def request(self) -> dict:
        return self.state.request


def available_hours(request: dict, catalog: "Catalog") -> float:
    days = (date.fromisoformat(request["deadline"]) - date.fromisoformat(request["today"])).days
    return days * catalog.work_hours_per_day


def _brief(m: dict) -> dict:
    return {k: m[k] for k in ("id", "title", "format", "quality", "license_cost", "production_hours")}


def search_materials(ctx: ToolContext, topic: str, max_results: int = 5) -> dict:
    topic = str(topic).strip()
    if not topic:
        raise ToolError("topic 不能为空")
    hits = ctx.catalog.search(topic)[: max(1, int(max_results))]
    return {
        "id": ctx.state.next_result_id("search"),
        "topic": topic,
        "count": len(hits),
        "materials": [_brief(m) for m in hits],
    }


def estimate_cost(ctx: ToolContext, material_id: str, script_engine: str = "ai") -> dict:
    m = ctx.catalog.material(material_id)
    if m is None:
        raise ToolError(f"未知素材 {material_id}")
    eng = ctx.catalog.engines.get(script_engine)
    if eng is None:
        raise ToolError(f"未知脚本引擎 {script_engine}，可选 {sorted(ctx.catalog.engines)}")
    budget = ctx.request["budget"]
    hours_avail = available_hours(ctx.request, ctx.catalog)
    total = m["license_cost"] + eng["fee"]
    hours = m["production_hours"] + eng["hours"]
    return {
        "id": ctx.state.next_result_id("est"),
        "material_id": material_id,
        "script_engine": script_engine,
        "breakdown": {"license_cost": m["license_cost"], "script_fee": eng["fee"]},
        "total_cost": total,
        "total_hours": hours,
        "budget": budget,
        "available_hours": hours_avail,
        "within_budget": total <= budget,
        "within_deadline": hours <= hours_avail,
        "budget_gap": round(max(0, total - budget), 2),
        "hours_gap": round(max(0, hours - hours_avail), 2),
    }


def generate_script(ctx: ToolContext, material_id: str, engine: str = "ai") -> dict:
    m = ctx.catalog.material(material_id)
    if m is None:
        raise ToolError(f"未知素材 {material_id}")
    if engine not in ctx.catalog.engines:
        raise ToolError(f"未知脚本引擎 {engine}，可选 {sorted(ctx.catalog.engines)}")
    topic = ctx.request["topic"]
    outline = [
        f"开场：一句话点出「{topic}」的核心卖点",
        f"主体：基于素材《{m['title']}》展开 3 个使用场景",
        "转化：限时福利 + 评论区互动引导",
        "结尾：品牌口号与关注引导",
    ]
    return {
        "id": ctx.state.next_result_id("scr"),
        "material_id": material_id,
        "engine": engine,
        "title": f"{topic}｜{m['title']}",
        "outline": outline,
        "note": "AI 生成初稿" if engine == "ai" else "模板生成，需人工润色",
    }


def save_plan(ctx: ToolContext, material_id: str, script_id: str, estimate_id: str) -> dict:
    key = ctx.state.idempotency_key
    existing = ctx.store.saved_plan(key)
    if existing:
        return {
            "id": existing["plan_id"],
            "idempotent_hit": True,
            "path": str(ctx.store.plan_path(key)),
            "total_cost": existing["cost"]["total_cost"],
        }
    est = ctx.state.find_result(estimate_id)
    scr = ctx.state.find_result(script_id)
    if not est or "total_cost" not in est:
        raise ToolError(f"找不到成功的估算结果 {estimate_id}")
    if not scr or "outline" not in scr:
        raise ToolError(f"找不到成功的脚本结果 {script_id}")
    if est["material_id"] != material_id or scr["material_id"] != material_id:
        raise ToolError("素材、脚本、估算三者引用的素材不一致")
    if est["script_engine"] != scr["engine"]:
        raise ToolError("估算所用脚本引擎与实际脚本不一致，需要重新估算")
    if not est["within_budget"]:
        raise ToolError(f"预算校验失败：方案费用 {est['total_cost']} > 预算 {est['budget']}")
    if not est["within_deadline"]:
        raise ToolError(f"工期校验失败：需 {est['total_hours']}h > 可用 {est['available_hours']}h")
    m = ctx.catalog.material(material_id)
    req = ctx.request
    plan_id = "plan_" + key[:8]
    plan = {
        "plan_id": plan_id,
        "idempotency_key": key,
        "run_id": ctx.state.run_id,
        "topic": req["topic"],
        "budget": req["budget"],
        "deadline": req["deadline"],
        "material": _brief(m),
        "script": scr,
        "cost": {
            "total_cost": est["total_cost"],
            "breakdown": est["breakdown"],
            "budget": est["budget"],
            "remaining": round(est["budget"] - est["total_cost"], 2),
        },
        "schedule": {"total_hours": est["total_hours"], "available_hours": est["available_hours"]},
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    ctx.store.write_plan(key, plan)
    return {"id": plan_id, "idempotent_hit": False, "path": str(ctx.store.plan_path(key)), "total_cost": est["total_cost"]}


TOOLS = {
    "search_materials": {
        "fn": search_materials,
        "description": "按主题检索合成素材库，返回候选素材（按质量降序），含授权费与制作工时。",
        "properties": {
            "topic": {"type": "string", "description": "活动主题或关键词"},
            "max_results": {"type": "integer", "description": "最多返回条数，默认 5"},
        },
        "required": ["topic"],
    },
    "estimate_cost": {
        "fn": estimate_cost,
        "description": "估算某素材 + 脚本引擎组合的费用与工时，并对比预算和截止时间。",
        "properties": {
            "material_id": {"type": "string"},
            "script_engine": {"type": "string", "enum": ["ai", "template"], "description": "ai 费用 2/1h；template 费用 1/2h"},
        },
        "required": ["material_id", "script_engine"],
    },
    "generate_script": {
        "fn": generate_script,
        "description": "基于素材生成内容脚本大纲。",
        "properties": {
            "material_id": {"type": "string"},
            "engine": {"type": "string", "enum": ["ai", "template"]},
        },
        "required": ["material_id", "engine"],
    },
    "save_plan": {
        "fn": save_plan,
        "description": "保存最终方案。必须引用同一素材、同一引擎的成功估算与脚本；超预算或超工期会被拒绝；相同请求幂等。",
        "properties": {
            "material_id": {"type": "string"},
            "script_id": {"type": "string"},
            "estimate_id": {"type": "string"},
        },
        "required": ["material_id", "script_id", "estimate_id"],
    },
}

CONTROL = {
    "finish": {
        "description": "方案已保存成功后结束任务。",
        "properties": {"summary": {"type": "string", "description": "结果摘要"}},
        "required": ["summary"],
    },
    "give_up": {
        "description": "没有可行方案或工具持续失败时停止，返回未完成原因与缺口。",
        "properties": {
            "message": {"type": "string", "description": "未完成原因"},
            "gap": {"type": "object", "description": "缺口，如预算差额、缺失素材、失败步骤"},
        },
        "required": ["message"],
    },
}


def tool_schemas() -> list[dict]:
    out = []
    for name, spec in {**TOOLS, **CONTROL}.items():
        props = {
            **spec["properties"],
            "reason": {"type": "string", "description": "一句话说明选择该动作的依据（基于哪个观察结果）"},
        }
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": {"type": "object", "properties": props, "required": [*spec["required"], "reason"]},
            },
        })
    return out


def execute_tool(ctx: ToolContext, name: str, args: dict) -> tuple[str, dict | None, str | None]:
    spec = TOOLS[name]
    mode = ctx.faults.take(name)
    try:
        if mode in ("error", "timeout"):
            raise ToolError(f"[注入故障] {name} {'调用超时' if mode == 'timeout' else '服务异常'}")
        result = spec["fn"](ctx, **args)
    except ToolError as e:
        return "failed", None, str(e)
    except TypeError as e:
        return "failed", None, f"参数错误：{e}"
    except Exception as e:  # noqa: BLE001
        return "failed", None, f"{type(e).__name__}: {e}"
    if mode == "partial":
        if name == "search_materials" and result["materials"]:
            mats = result["materials"]
            keep = mats[len(mats) // 2:]
            result = {**result, "materials": keep, "count": len(keep), "partial": True,
                      "warning": "[注入故障] 部分素材源不可用，只返回部分结果"}
            return "partial", result, None
        return "failed", None, f"[注入故障] {name} 返回不完整，结果不可用"
    return "success", result, None


def summarize(action: str, status: str, result: dict | None, error: str | None) -> str:
    if status == "failed":
        return f"失败：{error}"
    r = result or {}
    if action == "search_materials":
        items = "、".join(f"{m['id']}(质量{m['quality']}/授权{m['license_cost']})" for m in r.get("materials", []))
        text = f"找到 {r.get('count', 0)} 个素材" + (f"：{items}" if items else "")
        return text + (f"；{r['warning']}" if r.get("partial") else "")
    if action == "estimate_cost":
        flags = []
        if not r["within_budget"]:
            flags.append(f"超预算 {r['budget_gap']}")
        if not r["within_deadline"]:
            flags.append(f"超工期 {r['hours_gap']}h")
        return (f"{r['material_id']}+{r['script_engine']} 费用 {r['total_cost']}/预算 {r['budget']}，"
                f"工时 {r['total_hours']}h/可用 {r['available_hours']}h，" + ("；".join(flags) if flags else "可行"))
    if action == "generate_script":
        return f"{r['id']} 脚本生成成功（{r['engine']} 引擎，{len(r['outline'])} 段）"
    if action == "save_plan":
        if r.get("idempotent_hit"):
            return f"幂等命中，返回已保存的 {r['id']}"
        return f"已保存 {r['id']}，费用 {r['total_cost']}"
    return json.dumps(r, ensure_ascii=False)[:120]