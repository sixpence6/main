# Issue #119 T07 验收记录：纯交流、澄清与实质修改的对话分流

## 1. 范围

- 工单：#119（V3 纯对话 T07，父票 #112）。
- 目标：同一 `POST /api/projects/{project_id}/messages` 入口承接交流、补充与实质修改；问候、解释、暂缓、“好的／继续”等纯交流不新增需求版本、准备意图或图片授权；缺阻塞项只澄清、足够时收敛；明确创作或实质修改用稳定请求 ID 受理，旧等待先失效并合法结算，再进入新需求版本的准备、展示与最终确认。
- 本票只涉及 R2 后端纵向切片，无前端改造；新增 `messages.turn_kind` 迁移 0014，以便区分工作流消息、纯交流消息和提示词修改消息。未改变 SRS 参数、次数口径、图片尺寸、预算或保留期；未新增免费预览、自动等待超时、隐式排队或自动图片重试。

## 2. 实现

- `main/backend/app/models.py` 与两份迁移目录
  - `Message` 新增 `turn_kind`：`workflow`（参与需求证据）、`conversation`（纯交流）、`prompt`（提示词修改），默认 `workflow`，并加 CHECK 约束。
  - 新增 `main/backend/migrations/versions/0014_messages_turn_kind.py`，同时同步到 `backend/migrations/versions/0014_messages_turn_kind.py`（测试实际读取后者）。迁移只加列和约束，不修改既有消息语义。
- `main/backend/app/workflow/contracts.py` 与 `main/backend/app/workflow/prompts.py`
  - 新增严格契约 `ConversationData`：只输出 `kind`（`conversation`/`clarification`/`requirements_change`/`reference_usage_change`/`prompt_change`）、`reference_usage` 和 `evidence_ids`，不生成自由文本，不执行动作。
  - 新增 `conversation_router` 角色、envelope 与提示词增量；分类器只做归因；“确认／继续／好的”明确不等于最终提交或生成授权。
- `main/backend/app/services/messages.py`
  - 保留无 `WorkflowRun` 项目的旧 organize 路径；有工作流时不再返回 `LEGACY_FLOW_DISABLED`，改走同一消息入口的工作流分支。
  - 请求幂等：`SendMessageRequest.request_id` 为可选 UUID4，存入 `Message.operation_id`。同 ID 同内容且已有 assistant 消息时返回原 exchange，不重复分类、调用或计数；同 ID 异内容返回 409 `COMMAND_ID_CONFLICT`；只有 user 没有 assistant 返回 409 `REQUEST_INCOMPLETE`，不自动重试。
  - 忙时拒绝：恢复占用返回 409 `PROJECT_BUSY`；存在 queued/running Generation 或未收敛 `GenerationJob`（created/submit_pending/submitted/remote_pending/unknown/quarantined）返回 409 `ACTIVE_GENERATION_EXISTS`；不做隐式排队。
  - 纯交流：调用 `conversation_router` 归因后只写 user/assistant 两条 `turn_kind=conversation` 消息，`project.revision`、`requirement_revision`、intent、预算、slots、授权均不变；固定文案来自 `workflow/templates.py`。
  - 澄清：S01 调用 `answer_intent_clarification`，S03 调用 `answer_requirements_clarification`，S04/S05 仅在对应 `revise_creative`/`revise_visual` 可用时调用；仍由既有阶段执行独立审查和收敛。
  - 实质修改：S06/S07 调用既有 `revise_requirements`（内部先按 T06 的 `settle_unsubmitted_intent` 合法结算无 job 的 accepted intent，再启动需求修订）；S03 调用 `revise_brief_draft`；S02/S04/S05 直接走 `s03.start_requirements_revision`；S01 走 `submit_request` 或澄清动作。需求版本递增、旧等待失效后重新准备与展示。
  - 提示词修改：仅 S07 调用既有 `revise_prompt`；服务层预写 `turn_kind=prompt` 的 user 消息，阶段先检查持久候选预算、失效旧 awaiting 快照、回到 active，再完整 `_compile` 和独立审查，旧文本授权不能继承。
  - 参考图用途修改：仅当 `s02.current_reference_usage(ctx)` 与分类器结果不同且项目已有参考图时调用 `confirm_reference_usage`；用途相同只返回固定文案，不调用阶段，避免把 S07 误退回 S03。
  - 动作成功后统一把新 user/assistant 消息的 `operation_id` 绑定到 request_id，并在缺 assistant 时补固定模板；动作失败时也 stamp 已写消息，使重放得到 `REQUEST_INCOMPLETE`，不自动重试。
- `main/backend/app/prompt_preparation.py` 与阶段
  - 新增 `invalidate_awaiting`：把当前 project/run（可选 intent）的 awaiting 快照置 `invalidated`，并写 `prompt.invalidated` 审计。S03 需求文本变化时调用；S02 参考用途变化时调用；S07 既有 `_invalidate_awaiting_prompts` 继续用于提示词修复。
  - `s03._user_messages` 只读取 `turn_kind=workflow` 的消息，避免纯交流与提示词修改污染需求证据；`s01`/`s03` 新建的 canonical 用户消息显式标记 workflow。
  - `s06._revise_requirements` 允许 S07 在尚未最终确认时修改需求；若已有 confirmation，仍要求其与当前 artifact 匹配，旧授权不继承。
- `main/backend/app/schemas.py`、`presenters.py`、`routers/messages.py`
  - `MessageView` 增加 `turn_kind` 与 `request_id`；`MessageExchangeResponse` 增加可选 `workflow: WorkflowView`，公开 `project_revision`、`requirement_revision`、intent、usage、slots 与预览状态。
  - 路由继续使用既有身份、所有者、对象关联、内容门禁、CSRF/来源校验；新增 `message_send` operator audit，记录 request_id、turn_kind、revision、消息 ID，失败/拒绝也审计。
- `main/backend/app/workflow/templates.py`
  - 新增 `CONVERSATION_ACK`、`CONVERSATION_CLARIFY`、`CONVERSATION_CHANGE_ACCEPTED`、`CONVERSATION_PROMPT_CHANGE`、`REFERENCE_USAGE_UPDATED` 固定文案，明确不自动排队、不自动超时、不自动重试，且最终确认前不发起图片生成。

## 3. 测试与证据

新增 `backend/tests/test_issue_119_t07.py`，共 7 个测试函数、8 个用例（忙时用例按 recovery/job 两态参数化）。全部使用真实临时 SQLite、公开 HTTP/工作流接口、受控 `LLMStub`/`VisualStub`，不 mock 内部阶段：

1. `test_conversation_does_not_create_requirement_or_intent_and_is_idempotent`：纯交流（含“先暂缓”“好的，继续”）不改变 project/requirement revision、intent、preview、usage、预算和 slots；只新增两条 conversation 消息；标题 PATCH 是纯元数据；同 request_id 重放返回原消息，异内容 409 `COMMAND_ID_CONFLICT`；读取 workflow 不推进任何状态；审计存在。
2. `test_s07_requirements_change_settles_old_intent_and_invalidates_preview`：S07 消息触发的需求修改进入 S04 active，需求 revision 增加；旧 intent 变为 abandoned、slots 归零、旧 preview 变为 invalidated、usage 仍为 1；无 GenerationJob、无图片调用。
3. `test_s07_prompt_change_rebuilds_candidate_without_new_intent_or_usage`：S07 提示词修改生成新的 awaiting preview，预算 used 从 1 变为 2，旧 preview invalidated；intent/request_id 不变、usage 不变、无新 GenerationJob；S07 output reviewer 只新增一次审查，无图片调用。
4. `test_reference_usage_change_from_message_settles_preview_and_revises_requirements`：参考图用途从 product 改为 style，旧 intent abandoned、旧 preview invalidated、需求 revision 增加，新的 approved reference_report 记录 `reference_usage=style`，无图片调用。
5. `test_s03_clarification_uses_same_message_and_records_requirement_revision`：S03 needs_input 时补充“杯身是白色”，复用同一消息入口并收敛到 S04 active，需求 revision 增加；不提前创建 GenerationIntent，不调用图片。
6. `test_busy_workflow_rejects_new_message_without_queueing`：恢复占用返回 `PROJECT_BUSY`；已提交 job 返回 `ACTIVE_GENERATION_EXISTS`；消息数与角色调用数不变，证明没有排队或自动执行。
7. `test_message_entry_enforces_identity_owner_origin_csrf_and_request_id`：匿名 401、他人项目 404、错误 Origin/缺失 CSRF 403、非法 request_id 422、空白 content 422；拒绝后不产生业务副作用。

旧入口迁移：`backend/tests/test_t03_intent_review.py` 中原“工作流启动后 `/messages` 必须 409 `LEGACY_FLOW_DISABLED`”的单测，已改为断言同一消息入口的 conversation 语义；其他无 `WorkflowRun` 的旧 organize 测试未改。该文件最终 22 passed（与 #119、T06 合计 40 passed in 87.02s，basetemp `t119-adj1`）。

实际运行证据（cwd 均为 #119 worktree 的 `backend`，隔离 basetemp，均带 `-p no:cacheprovider`）：

```powershell
& $py -m pytest tests/test_issue_119_t07.py -q -p no:cacheprovider --basetemp D:\Desktop\issue-112-context\t119-6
# 8 passed in 19.59s
```

TDD 红证据：在基线 `cfe2db1` 的临时 worktree 中放入同一测试文件运行，测试因旧入口不识别 request_id、仍返回旧语义而全部失败，结果 `8 failed in 24.05s`（basetemp `D:/Desktop/issue-112-context/t119-red1`）。实现后为 `8 passed in 19.59s`（basetemp `t119-6`），最终复跑为 `8 passed in 20.15s`（basetemp `t119-final1`）。

主验收集（七文件）：`119 passed in 405.73s`（basetemp `D:/Desktop/issue-112-context/t119-main2`）。

迁移与兼容：`tests/test_issue_113_migration.py` 与 `tests/test_v3_p1_t12_legacy_compat.py` 合计 `9 passed in 6.65s`（basetemp `t119-mig3`）；`tests/test_issue_114_migration.py` 单独 `3 passed in 4.58s`（basetemp `t119-mig2`）。生产入口/发布包：`tests/test_t78_production_entry.py` 与 `tests/test_t79_release_package.py` 合计 `7 passed, 1 skipped in 9.27s`（basetemp `t119-release1`）。

旧 `/messages` 非生成回归（无 `WorkflowRun` 的 organize 路径）：8 个文件合计 `55 passed in 98.42s`（basetemp `t119-msg2`）。更大范围旧 V2 回归中存在的 67 个 `FINAL_CONFIRMATION_REQUIRED` 失败，已在集成基线 `cfe2db1` 复现，属既有基线红，与本票无关。

静态检查：新增测试与 `services/messages.py`、`routers/messages.py`、`models.py`、`schemas.py`、`presenters.py`、`prompt_preparation.py`、`contracts.py`、`s01.py`、`s03.py`、`s06.py` 均通过 `ruff check --no-cache`；`s02.py` 与 `templates.py` 仅报告既有 E501（`s02.py:333`、`s02.py:454`、`templates.py:48`），`prompts.py` 仅既有长行；`git diff --check` 无输出。

## 4. 逐条验收判定

| 票面要求 | 判定 | 证据 |
|---|---|---|
| 问候/解释/暂缓/“好的／继续”不新增需求版本、准备意图或图片授权；允许的交流角色可独立归因，不承诺所有 TEXT 成本为零 | 通过 | 测试 1：revision/intent/preview/usage/budget/slots 全不变，仅两条 conversation 消息；`conversation_router` 独立角色登记 RoleCall |
| 缺少阻塞项只澄清、足够时收敛；逐项需求审查、不可变快照和必要确认事实保留，不依赖旧复杂卡片 | 通过 | 测试 5：S03 needs_input 补充后收敛 S04 active；既有 S03 独立审查与 artifact/approval 机制不变 |
| 明确创作／实质修改用稳定 request ID 受理；旧等待失效并合法结算后，新版本准备、重新展示并最终确认 | 通过 | 测试 2 旧 intent abandoned、旧 preview invalidated；T06 的 `settle_unsubmitted_intent` 复用；测试 3 新 preview 重审 |
| 需求、参考用途与提示词修改均由同一消息语义承接；忙时拒绝新执行且不排队 | 通过 | 测试 2/3/4 覆盖三类修改；测试 6 recovery/job 均 409，消息与调用数不变 |
| 公开接口证明消息、需求 revision、原／新 intent、usage／slots 与实际调用次数；读取不推进工作流 | 通过 | 响应 `workflow` 视图与消息 `turn_kind`/`request_id`；测试 1 重放/读取不增加调用；测试 2/3/4 断言 usage、slots、无 visual 调用 |
| 仅在隔离环境验证，保护用户未提交修改，测试和文档不进入应用本体 | 通过 | 全部测试使用临时 SQLite/受控适配器；测试在 `backend/tests`，验收文档在 `docs/acceptance`；未操作原用户树 |
| 新增入口遵守身份、所有者、对象关联、内容门禁、CSRF／来源与审计 | 通过 | 测试 7 覆盖 401/404/403/422；路由 `require_principal`、`require_project_scope`、CSRF/Origin 与 `message_send` 审计 |
| 不改变 SRS 参数或次数口径，不新增免费预览、自动等待超时、隐式排队或自动图片重试 | 通过 | 无新预算/尺寸参数；usage 只读不改；忙时直接拒绝；固定模板明确不自动排队/超时/重试；无 visual submit |

## 5. 残留风险

- `conversation_router` 的分类质量依赖受控模型；不可确定的输入会落为固定交流回复，不会擅自执行修改或生成。
- S02/S04/S05 的澄清只在既有 `revise_creative`/`revise_visual` 可用时接入；其余阶段返回固定澄清/交流文案，后续可按阶段补齐更细的交互，但不影响本票主切面。
- 同一项目锁覆盖请求幂等检查、忙检查与动作执行；多进程部署时仍依赖数据库事务与项目锁策略，极端并发窗口未在本票用真实多进程压测覆盖。
- 旧 V2 `/generations` 回归存在基线 `FINAL_CONFIRMATION_REQUIRED` 失败；已在 `cfe2db1` 复现，非本票引入，但会持续影响旧链路的全量绿色度。
- 未覆盖真实浏览器、手机和前端展示；R3 后按 #129 补真实浏览器与设备证据。
