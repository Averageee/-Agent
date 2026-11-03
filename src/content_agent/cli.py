from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .agent import ContentPlanningAgent
from .faults import FaultInjector
from .llm import LLMError, build_llm
from .state import Store
from .trace import render_markdown


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="content-agent", description="内容任务规划 Agent（ReAct，默认离线 Mock）")
    p.add_argument("--input", help="JSON 请求文件，字段 topic / budget / deadline / today")
    p.add_argument("--topic")
    p.add_argument("--budget")
    p.add_argument("--deadline", help="YYYY-MM-DD")
    p.add_argument("--today", help="YYYY-MM-DD，默认今天；固定它可让结果可复现")
    p.add_argument("--resume", metavar="RUN_ID", help="续跑一次未完成的运行，保留已成功步骤")
    p.add_argument("--fail", action="append", default=[], metavar="TOOL[:MODE[:COUNT]]",
                   help="故障注入，MODE 为 error / timeout / partial，可重复")
    p.add_argument("--max-steps", type=int, default=12, help="单次运行的最大决策步数")
    p.add_argument("--llm", choices=["mock", "openai"], help="覆盖环境变量 LLM_PROVIDER")
    p.add_argument("--workdir", default=".agent_data", help="运行状态与已保存方案的目录")
    p.add_argument("--out", help="把结果 JSON 写入文件")
    p.add_argument("--trace", help="把 Markdown Trace 写入文件")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    load_dotenv(Path.cwd() / ".env")
    try:
        llm = build_llm(args.llm or os.getenv("LLM_PROVIDER", "mock"))
        faults = FaultInjector(args.fail)
    except (LLMError, ValueError) as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2
    agent = ContentPlanningAgent(llm=llm, store=Store(args.workdir), faults=faults, max_steps=args.max_steps)
    try:
        if args.resume:
            result = agent.resume(args.resume)
        else:
            req: dict = {}
            if args.input:
                req.update(json.loads(Path(args.input).read_text(encoding="utf-8-sig")))
            for key in ("topic", "budget", "deadline", "today"):
                value = getattr(args, key)
                if value is not None:
                    req[key] = value
            result = agent.run(req)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.trace:
        Path(args.trace).parent.mkdir(parents=True, exist_ok=True)
        Path(args.trace).write_text(render_markdown(result), encoding="utf-8")
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())