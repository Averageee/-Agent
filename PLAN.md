# 内容任务规划 Agent 实施计划

## 1. 目标
帮助运营在预算与截止时间内产出内容活动方案。输出：任务计划、步骤状态、工具结果、费用合计、已保存方案或未完成原因。

## 2. 技术选型
- Python 3.10+，原生实现 ReAct，CLI 运行
- 模型：OpenAI 兼容接口（function calling），.env 配置
- 离线 MockLLM：按观察结果选动作的脚本策略，只证明流程，不证明模型决策效果
- pytest 测试

## 3. 架构
- Agent 循环：模型选工具 -> 执行 -> 观察 -> 模型决定下一步 / finish / give_up
- RunState：步骤列表（pending/success/failed/skipped）、已花费用、工具结果缓存，持久化到 runs/<run_id>.json
- 护栏（代码强制）：最大步数上限 -> incomplete；保存前校验预算；失败步骤不记成功
- 工具：search_materials、generate_script、estimate_cost、save_plan；数据来自 data/ 合成素材、工具费用与耗时表
- 故障注入：--fail-tool <name>:<次数>
- 幂等：请求哈希作为幂等键，重复执行返回已保存方案，不重复计费/保存
- 续跑（加分）：--resume <run_id> 只重跑失败步骤，保留已成功结果

## 4. 验收用例（examples/）
1. 正常完成
2. 缺预算或截止时间 -> 未完成 + 缺失字段
3. 工具失败 -> 换替代或停止，保留成功结果
4. 预算超限：预算 10，初始 12 -> 改选 8 -> 再估算 -> 保存；另有无可行方案 -> 返回缺口
5. 重复执行 -> 幂等命中
6. 加分：失败后续跑
每组保存输入、输出、Trace（步骤/参数/返回摘要/下一步/决策依据）；至少 2 条真实模型 Trace。

## 5. 交付目录
README.md, src/, tests/, examples/, data/, docs/01-requirements.md ~ 05-retrospective.md, requirements.txt, .env.example, .gitignore

## 6. 时间安排（约 3 小时）
| 阶段 | 内容 | 用时 |
|---|---|---|
| 1 | 骨架、合成数据、工具 | 30m |
| 2 | Agent 循环、状态、护栏、Mock | 60m |
| 3 | 幂等、故障注入、续跑 | 25m |
| 4 | pytest + 生成 examples Trace | 35m |
| 5 | README 与 docs 01~05 | 30m |

## 7. 待确认
- 可用的模型服务与 API Key（用于真实 Trace）
- GitHub 仓库由用户创建，本地仅 git init
