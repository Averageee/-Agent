from __future__ import annotations

MODES = ("error", "timeout", "partial")


class FaultInjector:
    """按 "tool[:mode][:count]" 注入故障，例如 generate_script:error:1、search_materials:partial。

    故障只在当前进程内生效，不会持久化，因此 --resume 续跑时故障已清除。
    """

    def __init__(self, specs: list[str] | None = None):
        self.queue: dict[str, list[str]] = {}
        for spec in specs or []:
            self.add(spec)

    def add(self, spec: str) -> None:
        parts = [p.strip() for p in spec.split(":") if p.strip()]
        if not parts:
            raise ValueError(f"无效的故障配置：{spec!r}")
        tool, mode, count = parts[0], "error", 1
        if len(parts) >= 2:
            if parts[1].isdigit():
                count = int(parts[1])
            else:
                mode = parts[1]
        if len(parts) >= 3:
            count = int(parts[2])
        if mode not in MODES:
            raise ValueError(f"未知故障模式 {mode}，可选 {MODES}")
        self.queue.setdefault(tool, []).extend([mode] * count)

    def take(self, tool: str) -> str | None:
        pending = self.queue.get(tool)
        return pending.pop(0) if pending else None