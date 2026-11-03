from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class Catalog:
    """合成素材库与工具费用表（只读）。"""

    def __init__(self, data_dir: Path | str = DATA_DIR):
        d = Path(data_dir)
        self.materials = json.loads((d / "materials.json").read_text(encoding="utf-8-sig"))["materials"]
        costs = json.loads((d / "tool_costs.json").read_text(encoding="utf-8-sig"))
        self.tool_costs = costs["tools"]
        self.engines = costs["tools"]["generate_script"]["engines"]
        self.work_hours_per_day = costs.get("work_hours_per_day", 8)

    def material(self, material_id: str) -> dict | None:
        return next((m for m in self.materials if m["id"] == material_id), None)

    def search(self, topic: str) -> list[dict]:
        t = topic.strip().lower()
        hits = [m for m in self.materials if any(tag.lower() in t or t in tag.lower() for tag in m["topics"])]
        return sorted(hits, key=lambda m: (-m["quality"], m["license_cost"]))