# 内容任务规划 Agent

题目：**内容任务规划 Agent**。帮助运营人员在预算与截止时间内完成一份内容活动方案：给出任务计划、步骤状态、工具结果、费用合计，以及已保存方案或未完成原因。

- 原生 Python 实现 ReAct 循环（模型选动作 → 执行工具 → 观察结果 → 模型决定下一步 / finish / give_up），CLI 运行，运行时只依赖标准库。
- 模型接入：任意 OpenAI 兼容接口（function calling）；默认使用**离线 MockLLM**。
- **边界说明**：`examples/` 中的 Trace 全部由 MockLLM 生成。MockLLM 是读取观察结果的规则策略，只验证流程、状态与护栏，**不证明真实模型的决策效果**，也不满足题目对“模型驱动 ReAct”的要求。真实模型路径已实现，但尚未用真实 Key 跑过。

## 评审入口

| 想看什么 | 位置 |
|---|---|
| 验收点 → 用例对照 | [docs/03-evaluation.md](docs/03-evaluation.md) |
| 全部用例一览 | [examples/summary.md](examples/summary.md) |
| 预算超限 12 → 8 | [examples/05-budget-overrun/trace.md](examples/05-budget-overrun/trace.md) |
| 工具失败后重规划 | [examples/04-tool-failure/trace.md](examples/04-tool-failure/trace.md) |
| 失败后续跑（加分项） | [examples/07-fail-then-resume/](examples/07-fail-then-resume/) |
| 循环与护栏代码 | [src/content_agent/agent.py](src/content_agent/agent.py) |
| 设计取舍 | [docs/02-solution.md](docs/02-solution.md) |

## 环境与安装

Python 3.10+（开发与测试用 3.14.0，Windows）。运行时无第三方依赖，`pytest` 仅用于测试。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

默认无需任何配置（离线 Mock），变量说明见 [.env.example](.env.example)。

## 运行

```bash
# 预算超限示例：M01 估算 12 > 10，改选 M02 估算 8，再生成脚本并保存
python run.py --topic 运动相机新品 --budget 10 --deadline 2026-11-10 --today 2026-11-03 --trace out/trace.md

# 缺预算或截止时间 → 未完成，列出缺失字段，不调用工具
python run.py --topic 运动相机新品

# 故障注入：TOOL[:MODE[:COUNT]]，MODE 为 error / timeout / partial，可重复
python run.py --topic 运动相机新品 --budget 15 --deadline 2026-11-10 --today 2026-11-03 --fail generate_script:error:2

# 续跑：用上一条输出里的 run_id，只补做失败或未完成的步骤
python run.py --resume <run_id>

# 步数上限（默认 12）
python run.py --topic 运动相机新品 --budget 16 --deadline 2026-11-10 --today 2026-11-03 --max-steps 2
```

也可以用 `--input request.json` 传入 `topic / budget / deadline / today`。`--today` 不传时取当天日期；固定它可以复现结果。输出为 JSON（`--out` 写文件，`--trace` 写 Markdown Trace）。退出码：`0` 完成，`2` 未完成或配置错误。运行状态与已保存方案在 `.agent_data/`（`--workdir` 可改）。同一请求保存后再次执行会幂等命中；想重新规划，删除 `.agent_data/` 或换 `--workdir`。

## 测试与示例

```bash
python -m pytest -q              # 24 个用例
python scripts/run_examples.py   # 重新生成 examples/ 与 examples/summary.md
```

## 接入真实模型（未验证）

复制 `.env.example` 为 `.env`，设 `LLM_PROVIDER=openai` 并填 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`（任意支持 function calling 的 OpenAI 兼容服务），或在命令行加 `--llm openai`。本仓库尚未用真实模型跑出 Trace。

## 目录

```
run.py                     CLI 入口
src/content_agent/
  agent.py                 ReAct 循环、输入校验、护栏、幂等、续跑、结果组装
  llm.py                   MockLLM、OpenAI 兼容客户端、系统提示词
  tools.py                 4 个业务工具 + finish/give_up 的 schema、执行与摘要
  state.py                 RunState / Step 与本地持久化
  faults.py                故障注入
  trace.py                 Markdown Trace
  data.py                  读取 data/
data/                      合成素材库、工具费用与耗时表
tests/                     pytest
examples/                  9 组输入、输出、Trace
scripts/run_examples.py    生成 examples/
docs/                      01 需求 / 02 方案 / 03 评估 / 04 AI 使用 / 05 复盘
```

已知限制见 [docs/03-evaluation.md](docs/03-evaluation.md#已知限制)。