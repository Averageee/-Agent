"""重新生成 examples/：每组写入 input.json、output.json、trace.md，并汇总到 examples/summary.md。"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from content_agent.agent import ContentPlanningAgent  # noqa: E402
from content_agent.faults import FaultInjector  # noqa: E402
from content_agent.llm import MockLLM  # noqa: E402
from content_agent.state import Store  # noqa: E402
from content_agent.trace import render_markdown  # noqa: E402

BASE = {"topic": "运动相机新品", "deadline": "2026-11-10", "today": "2026-11-03"}
CASES = [
    {"name": "01-normal", "scenario": "正常完成", "request": {**BASE, "budget": 20}},
    {"name": "02-repeat", "scenario": "重复执行（幂等命中）", "request": {**BASE, "budget": 20}},
    {"name": "03-missing-input", "scenario": "缺预算与截止时间", "request": {"topic": BASE["topic"], "today": BASE["today"]}},
    {"name": "04-tool-failure", "scenario": "脚本工具失败 → 改用模板引擎并重新估算", "request": {**BASE, "budget": 12},
     "fail": ["generate_script:error:1"]},
    {"name": "05-budget-overrun", "scenario": "预算超限：初始 12 → 改选 8", "request": {**BASE, "budget": 10}},
    {"name": "06-no-feasible-plan", "scenario": "无可行方案 → 未完成及缺口", "request": {**BASE, "budget": 3}},
    {"name": "07-fail-then-resume", "scenario": "两种引擎均失败 → 停止；--resume 续跑只补失败步骤",
     "request": {**BASE, "budget": 15}, "fail": ["generate_script:error:2"], "resume": True},
    {"name": "08-partial-search", "scenario": "检索部分成功 → 只在返回素材中规划", "request": {**BASE, "budget": 18},
     "fail": ["search_materials:partial"]},
    {"name": "09-max-steps", "scenario": "达到步数上限 → 明确未完成", "request": {**BASE, "budget": 16}, "max_steps": 2},
]


def command(case: dict) -> str:
    parts = ["python run.py"]
    for key in ("topic", "budget", "deadline", "today"):
        if key in case["request"]:
            parts.append(f"--{key} {case['request'][key]}")
    parts += [f"--fail {f}" for f in case.get("fail", [])]
    if case.get("max_steps"):
        parts.append(f"--max-steps {case['max_steps']}")
    return " ".join(parts)


def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    os.chdir(ROOT)
    work = Path(".agent_data") / "examples"
    shutil.rmtree(work, ignore_errors=True)
    store = Store(work)
    rows = []
    for case in CASES:
        out_dir = ROOT / "examples" / case["name"]
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        dump(out_dir / "input.json", {**case, "command": command(case)})
        agent = ContentPlanningAgent(MockLLM(), store, faults=FaultInjector(case.get("fail", [])),
                                     max_steps=case.get("max_steps", 12))
        results = [agent.run(case["request"])]
        if case.get("resume"):
            results.append(ContentPlanningAgent(MockLLM(), store).resume(results[0]["run_id"]))
        for i, r in enumerate(results, 1):
            suffix = "" if len(results) == 1 else f"-{i}"
            dump(out_dir / f"output{suffix}.json", r)
            (out_dir / f"trace{suffix}.md").write_text(render_markdown(r), encoding="utf-8")
        final = results[-1]
        flow = " → ".join(f"{s['action']}({s['status']})" for s in final["steps"]) or "（未调用工具）"
        rows.append(f"| [{case['name']}]({case['name']}/) | {case['scenario']} | {final['status']} / {final['reason']} | "
                    f"{final['cost']['plan_total']} / {final['cost']['budget']} | {flow} |")
        print(case["name"], final["status"], final["reason"], final["cost"]["plan_total"])
    summary = [
        "# 示例汇总",
        "",
        "由 `python scripts/run_examples.py` 生成（离线 MockLLM，today 固定为 2026-11-03）。Mock 只验证流程与护栏，不证明真实模型决策效果。",
        "",
        "| 用例 | 场景 | 最终状态 | 费用/预算 | 执行路径 |",
        "|---|---|---|---|---|",
        *rows,
    ]
    (ROOT / "examples" / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()