import json

import pytest

from content_agent.agent import ContentPlanningAgent
from content_agent.cli import main
from content_agent.data import Catalog
from content_agent.faults import FaultInjector
from content_agent.llm import Decision, MockLLM
from content_agent.state import RunState, Store
from content_agent.tools import ToolContext, execute_tool

BASE = {"topic": "运动相机新品", "deadline": "2026-11-10", "today": "2026-11-03"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def make(store, fails=(), max_steps=12, llm=None):
    return ContentPlanningAgent(llm or MockLLM(), store, faults=FaultInjector(list(fails)), max_steps=max_steps)


def actions(result, attempt=None):
    return [s["action"] for s in result["steps"] if attempt is None or s["attempt"] == attempt]


def plan_status(result):
    return {p["tool"]: p["status"] for p in result["task_plan"]}


def saved_files(store):
    return list(store.saved_dir.glob("*.json"))


class Scripted:
    name = "scripted"

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def decide(self, state, messages):
        return self.decisions.pop(0)


def test_normal_completes_and_saves(store):
    r = make(store).run({**BASE, "budget": 20})
    assert r["status"] == "completed" and r["reason"] == "finished"
    assert actions(r) == ["search_materials", "estimate_cost", "generate_script", "save_plan", "finish"]
    assert r["cost"] == {**r["cost"], "budget": 20, "plan_total": 12, "remaining": 8}
    assert r["saved_plan"]["material"]["id"] == "M01"
    assert all(v == "success" for v in plan_status(r).values())
    assert len(saved_files(store)) == 1


@pytest.mark.parametrize("drop", ["budget", "deadline"])
def test_missing_budget_or_deadline(store, drop):
    req = {**BASE, "budget": 10}
    req.pop(drop)
    r = make(store).run(req)
    assert r["status"] == "incomplete" and r["reason"] == "invalid_input"
    assert r["gap"]["missing_fields"] == [drop]
    assert r["steps"] == []
    assert saved_files(store) == []


@pytest.mark.parametrize("patch,field", [
    ({"budget": "abc"}, "budget"),
    ({"budget": -1}, "budget"),
    ({"deadline": "2026-11-01"}, "deadline"),
    ({"deadline": "11/10"}, "deadline"),
])
def test_invalid_input_is_rejected(store, patch, field):
    r = make(store).run({**BASE, "budget": 10, **patch})
    assert r["reason"] == "invalid_input"
    assert field in r["gap"]["invalid_fields"]
    assert r["steps"] == []


def test_budget_overrun_replans_to_cheaper_option(store):
    r = make(store).run({**BASE, "budget": 10})
    assert r["status"] == "completed"
    totals = [(e["material_id"], e["total_cost"], e["within_budget"]) for e in r["cost"]["estimates"]]
    assert totals == [("M01", 12, False), ("M02", 8, True)]
    assert r["cost"]["plan_total"] == 8 and r["cost"]["remaining"] == 2
    assert r["saved_plan"]["material"]["id"] == "M02"


def test_no_feasible_plan_returns_gap_without_saving(store):
    r = make(store).run({**BASE, "budget": 3})
    assert r["status"] == "incomplete" and r["reason"] == "gave_up"
    assert actions(r).count("estimate_cost") == 4
    assert "save_plan" not in actions(r)
    assert r["gap"]["min_cost"] == 5 and r["gap"]["budget_gap"] == 2
    assert r["cost"]["plan_total"] is None
    assert saved_files(store) == []


def test_tool_failure_switches_engine_and_keeps_success(store):
    r = make(store, ["generate_script:error:1"]).run({**BASE, "budget": 12})
    assert r["status"] == "completed"
    failed = [s for s in r["steps"] if s["status"] == "failed"]
    assert [(s["action"], s["args"]["engine"]) for s in failed] == [("generate_script", "ai")]
    assert actions(r).count("search_materials") == 1
    assert r["saved_plan"]["script"]["engine"] == "template"
    assert r["cost"]["plan_total"] == 11


def test_repeated_failure_stops_and_never_marks_success(store):
    r = make(store, ["generate_script:error:2"]).run({**BASE, "budget": 15})
    assert r["status"] == "incomplete" and r["reason"] == "gave_up"
    status = plan_status(r)
    assert status["search_materials"] == "success" and status["estimate_cost"] == "success"
    assert status["generate_script"] == "failed" and status["save_plan"] == "pending"
    assert "generate_script" not in r["tool_results"]
    assert saved_files(store) == []


def test_resume_reruns_only_failed_steps(store):
    first = make(store, ["generate_script:error:2"]).run({**BASE, "budget": 15})
    r = make(store).resume(first["run_id"])
    assert r["status"] == "completed" and r["attempt"] == 2
    assert actions(r, attempt=2) == ["generate_script", "save_plan", "finish"]
    assert actions(r).count("search_materials") == 1
    assert r["cost"]["plan_total"] == 12


def test_resume_of_completed_run_does_nothing(store):
    first = make(store).run({**BASE, "budget": 20})
    r = make(store).resume(first["run_id"])
    assert r["status"] == "completed" and len(r["steps"]) == len(first["steps"])


def test_repeat_request_is_idempotent(store):
    first = make(store).run({**BASE, "budget": 20})
    again = make(store).run({**BASE, "budget": 20})
    assert again["reason"] == "idempotent_hit" and again["steps"] == []
    assert again["saved_plan"]["plan_id"] == first["saved_plan"]["plan_id"]
    assert again["cost"]["plan_total"] == 12
    assert len(saved_files(store)) == 1


def test_max_steps_returns_incomplete_then_resume_finishes(store):
    r = make(store, max_steps=2).run({**BASE, "budget": 16})
    assert r["status"] == "incomplete" and r["reason"] == "max_steps_reached"
    assert len(r["steps"]) == 2
    assert r["gap"]["pending_stages"] == ["生成脚本", "保存方案"]
    done = make(store).resume(r["run_id"])
    assert done["status"] == "completed"


def test_partial_search_only_uses_returned_materials(store):
    r = make(store, ["search_materials:partial"]).run({**BASE, "budget": 18})
    assert r["steps"][0]["status"] == "partial"
    assert plan_status(r)["search_materials"] == "partial"
    assert r["saved_plan"]["material"]["id"] == "M03"


def test_finish_before_save_is_rejected(store):
    llm = Scripted([Decision("finish", {"summary": "done"}, "too early"), Decision("give_up", {"message": "stop"}, "stop")])
    r = make(store, llm=llm).run({**BASE, "budget": 20})
    assert r["steps"][0]["status"] == "rejected"
    assert r["status"] == "incomplete" and r["reason"] == "gave_up"


def test_unknown_action_is_rejected(store):
    llm = Scripted([Decision("dance", {}, "?"), Decision("give_up", {"message": "stop"}, "stop")])
    r = make(store, llm=llm).run({**BASE, "budget": 20})
    assert r["steps"][0]["status"] == "rejected"


def _ctx(store, budget):
    state = RunState(run_id="t", request={**BASE, "budget": budget}, idempotency_key="k" * 16)
    return ToolContext(state=state, store=store, catalog=Catalog(), faults=FaultInjector())


def _call(ctx, name, args):
    status, result, error = execute_tool(ctx, name, args)
    ctx.state.add_step(action=name, args=args, status=status, summary="", reason="", result=result, error=error)
    return status, result, error


def test_save_plan_guard_rejects_over_budget(store):
    ctx = _ctx(store, 10)
    _, est, _ = _call(ctx, "estimate_cost", {"material_id": "M01", "script_engine": "ai"})
    _, scr, _ = _call(ctx, "generate_script", {"material_id": "M01", "engine": "ai"})
    status, _, error = _call(ctx, "save_plan", {"material_id": "M01", "script_id": scr["id"], "estimate_id": est["id"]})
    assert status == "failed" and "预算校验失败" in error
    assert saved_files(store) == []


def test_save_plan_guard_rejects_engine_mismatch(store):
    ctx = _ctx(store, 10)
    _, est, _ = _call(ctx, "estimate_cost", {"material_id": "M02", "script_engine": "ai"})
    _, scr, _ = _call(ctx, "generate_script", {"material_id": "M02", "engine": "template"})
    status, _, error = _call(ctx, "save_plan", {"material_id": "M02", "script_id": scr["id"], "estimate_id": est["id"]})
    assert status == "failed" and "引擎" in error


def test_cost_equals_license_plus_engine_fee(store):
    ctx = _ctx(store, 100)
    catalog = ctx.catalog
    for m in catalog.materials:
        for engine, spec in catalog.engines.items():
            _, est, _ = _call(ctx, "estimate_cost", {"material_id": m["id"], "script_engine": engine})
            assert est["total_cost"] == m["license_cost"] + spec["fee"]
            assert est["total_hours"] == m["production_hours"] + spec["hours"]


def test_fault_spec_parsing():
    f = FaultInjector(["a:partial:2", "b:3", "c"])
    assert f.queue == {"a": ["partial", "partial"], "b": ["error"] * 3, "c": ["error"]}
    with pytest.raises(ValueError):
        FaultInjector(["a:boom"])


def test_cli_end_to_end(tmp_path):
    out, trace = tmp_path / "out.json", tmp_path / "trace.md"
    rc = main(["--topic", "运动相机新品", "--budget", "10", "--deadline", "2026-11-10", "--today", "2026-11-03",
               "--workdir", str(tmp_path / "w"), "--out", str(out), "--trace", str(trace)])
    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8"))["cost"]["plan_total"] == 8
    text = trace.read_text(encoding="utf-8")
    assert "estimate_cost" in text and "改选" in text


def test_cli_missing_input_exit_code(tmp_path):
    assert main(["--topic", "x", "--workdir", str(tmp_path / "w")]) == 2