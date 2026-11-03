from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

OK_STATUSES = ("success", "partial")


@dataclass
class Step:
    index: int
    attempt: int
    action: str
    args: dict
    status: str
    summary: str
    reason: str
    result: dict | None = None
    error: str | None = None
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def ok(self) -> bool:
        return self.status in OK_STATUSES


@dataclass
class RunState:
    run_id: str
    request: dict
    idempotency_key: str | None = None
    attempt: int = 1
    status: str = "running"
    steps: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    outcome: dict = field(default_factory=dict)
    counters: dict = field(default_factory=dict)
    llm: str = ""

    @staticmethod
    def new_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def next_result_id(self, prefix: str) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}_{n}"

    def add_step(self, **kwargs) -> Step:
        step = Step(index=len(self.steps) + 1, attempt=self.attempt, **kwargs)
        self.steps.append(step)
        return step

    def ok_steps(self, action: str | None = None) -> list[Step]:
        return [s for s in self.steps if s.ok and (action is None or s.action == action)]

    def find_result(self, result_id: str | None) -> dict | None:
        if not result_id:
            return None
        for s in reversed(self.steps):
            if s.ok and s.result and s.result.get("id") == result_id:
                return s.result
        return None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RunState":
        data = dict(data)
        steps = [Step(**s) for s in data.pop("steps", [])]
        return cls(steps=steps, **data)


class Store:
    """运行状态与已保存方案的本地持久化。"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.saved_dir = self.root / "saved"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.saved_dir.mkdir(parents=True, exist_ok=True)

    def save_run(self, state: RunState) -> Path:
        path = self.runs_dir / f"{state.run_id}.json"
        path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_run(self, run_id: str) -> RunState:
        path = self.runs_dir / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"找不到运行记录 {run_id}（{path}）")
        return RunState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def plan_path(self, key: str) -> Path:
        return self.saved_dir / f"{key}.json"

    def saved_plan(self, key: str | None) -> dict | None:
        if not key:
            return None
        path = self.plan_path(key)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def write_plan(self, key: str, plan: dict) -> bool:
        path = self.plan_path(key)
        if path.exists():
            return False
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return True