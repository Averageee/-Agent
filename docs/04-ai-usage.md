# 04 AI 使用

## AI 辅助内容

- 工具：Codex（编码 Agent）。
- 范围：读取题目、拆分计划（`PLAN.md`）、生成合成数据、`src/` 全部代码、`tests/`、`scripts/run_examples.py`、README 与 docs 初稿。

## 个人修改与决策

> 待候选人填写：哪些设计是自己定的、改了哪些代码或文档、为什么。

已由候选人确定的：使用离线 Mock 先跑通流程；仓库地址 `https://github.com/Averageee/-Agent`。

## 验证

- 每个验收场景先用冒烟脚本跑过，核对费用与步骤状态（如预算 10 时 12 → 8）。
- 冒烟中发现任务计划把部分成功的检索显示为 success，已改为保留 partial。
- `pytest` 24 个用例通过，`scripts/run_examples.py` 生成的 9 组结果与预期一致（见 03-evaluation.md）。
- 未验证：真实模型路径；Linux/macOS 下运行。

## 实际用时

> 待候选人填写。