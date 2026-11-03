from __future__ import annotations

import json


def _cell(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(result: dict) -> str:
    req = result["request"]
    lines = [
        f"# Trace {result['run_id']}",
        "",
        f"- 输入：主题「{req.get('topic')}」，预算 {req.get('budget')}，截止 {req.get('deadline')}（today {req.get('today')}）",
        f"- 模型：{result['llm']}；尝试次数：{result['attempt']}",
        f"- 结果：**{result['status']}**（{result['reason']}）{result['message'] or ''}",
        "",
        "## 步骤",
        "",
        "| # | 尝试 | 动作 | 参数 | 状态 | 返回摘要 | 决策依据 | 后续动作 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    steps = result["steps"]
    for i, s in enumerate(steps):
        nxt = steps[i + 1]["action"] if i + 1 < len(steps) else "结束"
        cells = [s["index"], s["attempt"], s["action"], s["args"], s["status"], s["summary"], s["reason"], nxt]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    if not steps:
        lines.append("| - | - | （未调用工具） | - | - | - | - | - |")

    lines += ["", "## 任务计划", "", "| 阶段 | 工具 | 状态 | 调用次数 | 失败次数 |", "|---|---|---|---|---|"]
    for p in result["task_plan"]:
        lines.append(f"| {p['stage']} | {p['tool']} | {p['status']} | {p['calls']} | {p['failed_calls']} |")

    cost = result["cost"]
    lines += ["", "## 费用", "", f"- 预算 {cost['budget']}，方案合计 {cost['plan_total']}，剩余 {cost['remaining']}"]
    for e in cost["estimates"]:
        ok = "可行" if e["within_budget"] and e["within_deadline"] else "不可行"
        lines.append(f"- {e['id']}：{e['material_id']} + {e['script_engine']} = {e['total_cost']}（{ok}）")

    if result.get("saved_plan"):
        plan = result["saved_plan"]
        lines += ["", "## 已保存方案", "", f"- {plan['plan_id']}：{plan['material']['title']}，脚本 {plan['script']['id']}（{plan['script']['engine']}）"]
    if result.get("gap"):
        lines += ["", "## 缺口", "", "~~~json", json.dumps(result["gap"], ensure_ascii=False, indent=2), "~~~"]
    return "\n".join(lines) + "\n"