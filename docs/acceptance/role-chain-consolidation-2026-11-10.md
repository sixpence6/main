# 多角色链路修复验收

日期：2026-11-10（Asia/Shanghai）。范围：当前工作区代码、隔离数据库与受控模型测试。状态：实现完成，规定检查通过；本轮未执行部署或真实项目数据库迁移命令，不代表 v3 整体发布验收。

## 修复结果

- 程序按字段累积有效需求。模型返回 null、空数组或省略信息时，保留已知用途、主体、风格等；明确撤销必须有本轮用户来源与 clear 操作。unknown/conflict 不抹掉旧值，也不能被下一次空返回静默消除。
- 每个运行持久化 requirements_state_json。报告、后续输入与刷新后回答共用有效状态；参考图改变时保留有用户来源的字段，清除过期图像观察。旧运行尚无新状态时，从同一运行/参考用途的已验证部分记录补回较早的已知字段。
- 新任务使用结构化澄清：问题 ID、字段、选项 ID/值与补充要求一起保存。自由描述题不生成虚假的字母示例；“其他，请给原文”保持待补充，即使模型误报 ready 也不能提前批准。
- 新任务统一需求/归因，创意与视觉一次产出，交付清单由程序生成。正常完整路径在 WORKFLOW_GATE_MODE=off 下，无图 3 次模型调用，有图 4 次；测试走到提示词确认与交付，图片确认前提交为 0，确认后只提交 1 次。
- 需求确认、最终提示词确认、发布确认继续显式执行；用户原文不会因临时来源 ID 绑定而被重写。模型输出非法增量时保留原状态，停在可人工恢复的失败状态，恢复重放原请求。由确认阶段发起的修订在语义校验失败后，将恢复检查点绑定到已经入库的新版本，仍保留原模型请求，不发生旧阶段/版本错位。
- 对有效提示词继续修订但预算已满时，只拒绝新修订，保留最后一个可确认候选及原意图。没有有效候选的预算耗尽路径仍按原规则结算占用。

核心实现：

- [requirements.py](../../main/backend/app/workflow/requirements.py)：字段累积、来源、结构化答案、持久化。
- [consolidated.py](../../main/backend/app/workflow/consolidated.py)：统一需求调用、原文绑定、合并视觉产出。
- [role_graph.py](../../main/backend/app/workflow/role_graph.py) / [角色图](../architecture/role-dag-v3.svg)：实际活动模型工序与程序/用户门禁。
- [回归用例](../../backend/tests/test_role_chain_consolidation.py) / [迁移用例](../../backend/tests/test_role_chain_migration.py)。

## 策略与迁移兼容

迁移 0017 在主项目与测试迁移目录保持一致，给 workflow_runs 增加 role_policy 和 requirements_state_json。已有行默认为 legacy；新任务在创建时使用 WORKFLOW_ROLE_POLICY，默认 consolidated。已经运行的任务与它派生的新版本继承原策略，不会因环境配置变化而切换中途协议。

旧路由、需求顾问、创意、视觉导演、交付等角色保留兼容契约和历史请求恢复；默认新 DAG 不调用这些模型角色。内容门禁配置独立保留，strict/requirements_only 的审查调用不能按默认 off 的三次/四次计数解释。

迁移版本写入失败会回滚两个新增字段；存在新需求状态或新策略运行时，拒绝会丢失这些事实的 schema 降级。开发验证全部使用临时 SQLite。运行实例加载本次修改时，应经原有备份/升级入口应用 0017；本次没有执行真实数据升级。

## 规定检查

根目录执行：

```powershell
npm run check
```

最终结果：全部通过。

| 检查 | 结果 |
| --- | --- |
| 前端依赖图 | 一个实际 vue-router 安装，通过 |
| 前端类型检查 | 通过 |
| 前端测试 | 29 文件，226 passed |
| 后端 lint | 通过 |
| 定向后端测试 | 127 passed |

[完整检查输出](evidence/role-consolidation-20261110/check.log)。新增回归和迁移用例已纳入 scripts/check.mjs；另外纳入了提示词预算满后保留有效候选的既有回归用例。

原调查的独立探针也转绿：

```powershell
.\backend\.venv\Scripts\python.exe .\scripts\diagnostics\role-chain-state-probe.py
```

它复用隔离测试环境，结束后删除临时测试模块，不接触真实项目库。

## 扩展回归的明确限制

额外将全部后端测试文件分成 issues/GF、基础、工作流三组尝试运行。各组使用 --maxfail=1，遇到下列断言后停止，因此**扩展回归没有全绿，也没有执行完全部用例**；不能将规定检查通过写成 npm run check:full 通过。

| 分组 | 停止时结果 | 不一致断言 |
| --- | --- | --- |
| Issues/GF | 157 passed，1 failed | test_issue_119_t07.py::test_s07_requirements_change_settles_old_intent_and_invalidates_preview 期待回复包含“需求版本”，实际是结构化需求对齐报告 |
| 基础 | 71 passed，1 failed | test_t04_brief.py::test_confirm_requires_ready_prompt 期待 BRIEF_NOT_READY，已关闭的旧接口实际返回 LEGACY_FLOW_DISABLED |
| 工作流 | 179 passed，1 failed | test_v2_t08_visual_final_brief.py::test_visual_technical_failure_has_zero_automatic_retries 期待 SERVICE_UNAVAILABLE，实际返回精确的 LLM_TIMEOUT |

这些断言涉及现行接口关闭、错误码和报告文案；本轮保持对应行为，不通过恢复旧接口或改回通用错误码消除失败。日志：[Issues/GF](evidence/role-consolidation-20261110/backend-issues.log)、[基础](evidence/role-consolidation-20261110/backend-foundation.log)、[工作流](evidence/role-consolidation-20261110/backend-workflow.log)。

## 交付范围

本轮修改后端状态与角色编排、协议/提示词/DAG、配置示例、0017 迁移及相关测试/文档。原有阶段测试和子进程替身显式固定到 legacy 兼容策略；新路径有独立的真实 HTTP/SQLite 测试，外部模型及图片供应商由受控适配器替换。测试不代表真实模型成图质量或供应商耗时测量。

工作区在本轮开始时已有其他未提交修改；本轮没有提交、推送或部署这些修改，也没有恢复或删除其他人的工作。
