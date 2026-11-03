# 03 评估

环境：Windows，Python 3.14.0，pytest 9.2.0，离线 MockLLM，`today` 固定为 2026-11-03（可用工时 56h）。

## 如何复现

```bash
python -m pytest -q
python scripts/run_examples.py
```

实际结果（2026-11-03）：`24 passed`；9 组示例状态与预期一致，汇总见 [examples/summary.md](../examples/summary.md)。

## 验收点对照

| 验收点 | 预期 | 实际 | 证据 |
|---|---|---|---|
| 正常完成 | 预算 20，选 M01+ai=12，保存 | completed，合计 12，剩余 8 | [01-normal](../examples/01-normal/trace.md)；`test_normal_completes_and_saves` |
| 缺预算或截止时间 | incomplete，列缺失字段，不调用工具 | invalid_input，缺 budget、deadline，0 步 | [03-missing-input](../examples/03-missing-input/output.json)；`test_missing_budget_or_deadline`、`test_invalid_input_is_rejected` |
| 工具失败 | 失败记 failed，改引擎并重新估算，检索不重做 | ai 脚本失败 → 估算 M01+template=11 → 保存，合计 11/12 | [04-tool-failure](../examples/04-tool-failure/trace.md)；`test_tool_failure_switches_engine_and_keeps_success` |
| 工具持续失败 | 停止，失败步骤不记成功，保留成功结果 | gave_up；任务计划中 generate_script=failed、save_plan=pending | [07 trace-1](../examples/07-fail-then-resume/trace-1.md)；`test_repeated_failure_stops_and_never_marks_success` |
| 预算超限 | 预算 10：12 超限 → 改选 8 → 保存 | M01=12（超 2）→ M02=8 → 脚本 → 保存，剩余 2 | [05-budget-overrun](../examples/05-budget-overrun/trace.md)；`test_budget_overrun_replans_to_cheaper_option` |
| 无可行方案 | 未完成及缺口，不保存 | 预算 3：4 个候选均超，最低 5，缺口 2 | [06-no-feasible-plan](../examples/06-no-feasible-plan/trace.md)；`test_no_feasible_plan_returns_gap_without_saving` |
| 重复执行 | 不重复调用工具与保存 | idempotent_hit，0 步，返回同一 plan_id | [02-repeat](../examples/02-repeat/output.json)；`test_repeat_request_is_idempotent` |
| 步数上限 | 明确未完成 | max_steps_reached，列出未完成阶段 | [09-max-steps](../examples/09-max-steps/trace.md)；`test_max_steps_returns_incomplete_then_resume_finishes` |
| 失败续跑（加分） | 只补失败步骤 | 第 2 次尝试只有 generate_script → save_plan → finish | [07 trace-2](../examples/07-fail-then-resume/trace-2.md)；`test_resume_reruns_only_failed_steps` |
| 部分成功 | 只用返回的素材 | 检索 partial，选 M03=6 | [08-partial-search](../examples/08-partial-search/trace.md)；`test_partial_search_only_uses_returned_materials` |

另有护栏测试：未保存就 finish 被拒绝、未知动作被拒绝、超预算或引擎不一致时 `save_plan` 失败、费用公式核对、故障配置解析、CLI 端到端。

## 已知限制

- **没有真实模型 Trace**。全部示例由 MockLLM（规则策略）生成，只证明流程、状态与护栏正确；按题目要求，这不算完成模型驱动的 ReAct 决策。`OpenAICompatLLM` 未用真实服务验证过。
- 部分成功只对 `search_materials` 做了模拟；其他工具的 partial 按失败处理。
- 故障注入只在进程内生效，续跑时自动视为已恢复。
- 幂等没有文件锁，并发执行同一请求时可能出现竞态。
- run_id 与时间戳每次重新生成示例都会变，其余内容稳定。
- 素材检索是标签子串匹配，不是语义检索。