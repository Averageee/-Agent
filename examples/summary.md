# 示例汇总

由 `python scripts/run_examples.py` 生成（离线 MockLLM，today 固定为 2026-11-03）。Mock 只验证流程与护栏，不证明真实模型决策效果。

| 用例 | 场景 | 最终状态 | 费用/预算 | 执行路径 |
|---|---|---|---|---|
| [01-normal](01-normal/) | 正常完成 | completed / finished | 12 / 20 | search_materials(success) → estimate_cost(success) → generate_script(success) → save_plan(success) → finish(final) |
| [02-repeat](02-repeat/) | 重复执行（幂等命中） | completed / idempotent_hit | 12 / 20 | （未调用工具） |
| [03-missing-input](03-missing-input/) | 缺预算与截止时间 | incomplete / invalid_input | None / None | （未调用工具） |
| [04-tool-failure](04-tool-failure/) | 脚本工具失败 → 改用模板引擎并重新估算 | completed / finished | 11 / 12 | search_materials(success) → estimate_cost(success) → generate_script(failed) → estimate_cost(success) → generate_script(success) → save_plan(success) → finish(final) |
| [05-budget-overrun](05-budget-overrun/) | 预算超限：初始 12 → 改选 8 | completed / finished | 8 / 10 | search_materials(success) → estimate_cost(success) → estimate_cost(success) → generate_script(success) → save_plan(success) → finish(final) |
| [06-no-feasible-plan](06-no-feasible-plan/) | 无可行方案 → 未完成及缺口 | incomplete / gave_up | None / 3 | search_materials(success) → estimate_cost(success) → estimate_cost(success) → estimate_cost(success) → estimate_cost(success) → give_up(final) |
| [07-fail-then-resume](07-fail-then-resume/) | 两种引擎均失败 → 停止；--resume 续跑只补失败步骤 | completed / finished | 12 / 15 | search_materials(success) → estimate_cost(success) → generate_script(failed) → estimate_cost(success) → generate_script(failed) → give_up(final) → generate_script(success) → save_plan(success) → finish(final) |
| [08-partial-search](08-partial-search/) | 检索部分成功 → 只在返回素材中规划 | completed / finished | 6 / 18 | search_materials(partial) → estimate_cost(success) → generate_script(success) → save_plan(success) → finish(final) |
| [09-max-steps](09-max-steps/) | 达到步数上限 → 明确未完成 | incomplete / max_steps_reached | None / 16 | search_materials(success) → estimate_cost(success) |
