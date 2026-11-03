# Issue #120 T08 验收记录：图文消息、参考用途与稳定版本贯穿

## 1. 范围

- 工单：#120（V3 纯对话 T08，父票 #112）。
- 目标：图文共同发送后，图片属于当时消息；自然语言参考用途和稳定资产版本贯穿需求、预览、最终确认与实际请求；发送前暂存与最终业务引用分离，替换引用使旧依赖失效；历史读取保留当时的附件快照，不补造旧消息附件；所有新增入口满足身份、所有者、对象关联、内容门禁、CSRF/来源与审计约束；不改变 SRS 参数或次数口径，不新增免费预览、自动等待超时、隐式排队或自动图片重试。
- 本票只涉及 R2 后端纵向切片，无前端改动。T09 门槛通过前不实施 R3 界面替换，也未制作前端附件选择/粘贴/拖入 UI。

## 2. 实现

- `main/backend/app/models.py`、`main/backend/migrations/versions/0015_message_attachments.py`、`backend/migrations/versions/0015_message_attachments.py`
  - 新增 `MessageAttachment` 与 `message_attachments` 表：消息、资产、项目归属；`usage`（none/product/style/composition）；稳定 `content_version`（sha256）；mime/宽/高/字节数；创建时间。
  - 约束：每个消息最多一个附件、每个资产最多绑定一次、资产删除受限、消息/项目级联删除、usage/尺寸/字节数 CHECK。迁移 `0015` 从 `0014` 升级，downgrade 可恢复，且不猜测补造旧消息附件。
- `main/backend/app/services/message_attachments.py`
  - `stage_message_attachment`：只在 `attachments/{asset_id}.{ext}` 写文件并创建 `Asset(kind=reference)`；**不修改 `project.revision`、不设置 `project.reference_asset_id`、不创建需求版本或意图**。
  - `discard_staged_attachment`：仅允许删除未绑定任何消息且不是当前业务引用的暂存资产；已发送或当前引用分别返回 409。
  - `bind_message_attachment`：发送动作完成后，将资产绑定到当时用户消息，冻结 `content_version`、mime、尺寸、字节数和用途；同一资产不能再次绑定。
  - `load_pending_attachment`：读取最新 `usage=none` 的消息附件，供后续文本明确用途时提升。
- `main/backend/app/routers/assets.py`
  - 新增 `POST /api/projects/{project_id}/message-attachments`：multipart 单文件，先 `require_project_owner`，复用图片内容门禁/大小限制/`inspect_image`；成功返回 `StagedMessageAttachmentView`（asset、usage=none、content_version/sha256、preview_url）。
  - 新增 `DELETE /api/projects/{project_id}/message-attachments/{asset_id}`：删除未发送的暂存附件。
  - 两个入口都写 operator audit（`message_attachment_staged` / `message_attachment_discarded`）；预览字节继续走现有 `/api/assets/{asset_id}` 的身份、所有者、保留期与 `can_access_asset` 门禁。
- `main/backend/app/services/messages.py`
  - `send_message` 新增 `attachment_asset_id`；项目已有 WorkflowRun **或** 带附件消息时进入 workflow 消息入口；旧无 WorkflowRun 且无附件的 legacy organize 路径保持原样。
  - 带附件消息先验证资产属于本项目、是 reference、未被发送、文件 sha/dimensions 未变化、可访问；不明确用途时只记录 user+assistant 会话消息和 `usage=none` 附件，返回固定澄清模板，不改变业务引用、需求版本、意图、预算或槽位。
  - 明确用途时：结算旧未提交意图；S01 路径先设业务引用并重新走 intent 路由/S02 分析；其他阶段先记录用户文本、替换业务引用、失效旧需求/报告/等待快照，再重新分析参考图并启动 S03 需求修订；最后把附件绑定到本次用户消息并冻结 usage。
  - “先发图、后补用途”路径：最新 `usage=none` 附件在后续文本明确用途时提升，更新原附件 usage 和其消息的 `turn_kind=workflow`，不伪造新的需求 revision。
  - 响应 `MessageExchange` 增加 attachment，幂等重放也返回同一附件快照。读取历史不触发任何工作流动作。
- `main/backend/app/schemas.py`、`main/backend/app/presenters.py`、`main/backend/app/services/projects.py`、`main/backend/app/routers/projects.py`
  - 新增 `MessageAttachmentView`、`StagedMessageAttachmentView`；`MessageView.attachment`；`SendMessageRequest.attachment_asset_id`。
  - 项目详情批量读取 `message_attachments` 并按 `message_id` 挂到消息视图；历史附件使用保存的快照，不根据当前 `project.reference_asset` 反推。
- `main/backend/app/workflow/contracts.py`、`prompts.py`、`templates.py`
  - `conversation_router` 支持 `has_reference_attachment` 输入；带图片且用途明确时可归为 `requirements_change` 并给出 `reference_usage`，用途不明确必须置 none 交程序澄清；非参考用途 change 不允许携带 usage。
  - 新增 `REFERENCE_USAGE_CLARIFY` 固定文案。
- `main/backend/app/workflow/stages/s03.py`
  - 新增 `record_revision_text` 公共窄入口；`start_requirements_revision(..., record_user_text=False)` 允许附件流程先记录一次用户消息后复用，避免重复 revision 和重复消息。
- 迁移头断言从 `0014` 更新为 `0015`：`backend/tests/test_issue_113_migration.py`、`test_issue_114_migration.py`、`test_t78_production_entry.py`、`test_t79_release_package.py`、`test_v3_p1_t12_legacy_compat.py`。

## 3. 测试与证据

新增 `backend/tests/test_issue_120_t08.py`，共 6 个测试函数、6 个用例，全部使用真实临时 SQLite、公开 HTTP/工作流接口和受控 LLM/Visual 适配器，不 mock 内部阶段：

1. `test_staging_is_separate_from_business_reference_and_message_history_keeps_snapshot`：暂存不增加 revision、不设业务引用、不产生消息；预览可读且字节一致；发送后附件绑定到当时用户消息，usage/content_version/mime/尺寸正确；删除业务引用后历史附件快照仍保留；暂存附件可删除且无副作用。
2. `test_ambiguous_attachment_waits_for_usage_then_promotes_without_inventing_revision`：带图但用途不明时只产生 conversation 消息和 usage=none 附件，不创建意图/预览/业务引用；后续文本明确“风格参考”后提升原附件，业务引用指向原资产，附件 usage=style，原消息进入需求证据，revision 只增加一次。
3. `test_s07_attachment_replacement_invalidates_old_preview_and_binds_usage`：S07 等待最终确认时替换参考图，旧 intent 变 abandoned、旧 prepared_prompt 变 invalidated、旧预览消失、需求 revision 增加，新附件 usage=style；不产生视觉调用。
4. `test_attachment_usage_reaches_frozen_prompt_reference_after_requirements_flow`：从带附件消息开始完成 S03/S04/S05/finalize/confirm，冻结请求中的 `references` 恰为一张，asset_id、usage=product、content_version/sha256 与上传字节一致；最终确认后实际 Visual 请求的 `reference_bytes` 与上传字节一致，`reference_mime_type=image/png`。
5. `test_attachment_entry_enforces_identity_owner_origin_csrf_and_asset_scope`：匿名 401、他人项目 404、错误 Origin/缺失 CSRF 403、非法图片 422、文件内容在暂存后变化 409 `ATTACHMENT_NOT_AVAILABLE`、跨项目附件 404、已发送资产重用 409、已绑定附件删除 409、匿名/他人预览 401/404；附件元数据、缩略图地址与实际字节均受权限保护。
6. `test_0015_upgrade_preserves_existing_messages_without_inventing_attachments`：从 0014 升级到 0015 后旧消息内容与 turn_kind 保留、`message_attachments` 为空（不回填、不猜测补造），downgrade 到 0014 后旧消息仍保留且附件表删除。

TDD 红绿证据：

- RED：在基线 `82cdb1e` 的独立 detached worktree 中复制同一测试文件，未实现本票功能，运行得到 `6 failed in 10.19s`（basetemp `D:/Desktop/issue-112-context/t120-red1`）。失败点分别为暂存路由不存在、0015 迁移不存在、附件范围/权限测试无法建立，证明测试覆盖的是新增行为而非既有实现。
- GREEN：实现后在本票 worktree 运行同一文件，`6 passed in 15.64s`（basetemp `D:/Desktop/issue-112-context/t120-final-green`）。

相邻/主验收回归（T120 worktree，隔离 basetemp）：

```powershell
& $py -m pytest tests/test_issue_120_t08.py tests/test_issue_119_t07.py tests/test_issue_118_t06.py tests/test_issue_114_migration.py tests/test_issue_113_migration.py tests/test_t78_production_entry.py tests/test_t79_release_package.py tests/test_v3_p1_t12_legacy_compat.py -q -p no:cacheprovider --basetemp D:/Desktop/issue-112-context/t120-final-adj
```

结果：`43 passed, 1 skipped in 84.90s`。

主验收（既有 119 条 + T06 10 条 + T07 8 条 + T08 6 条）：

```powershell
& $py -m pytest tests/test_issue_117_t05.py tests/test_issue_115_t03.py tests/test_issue_116_t04.py tests/test_issue_114_t02.py tests/test_issue_114_migration.py tests/test_issue_113_t01.py tests/test_issue_113_migration.py tests/test_issue_118_t06.py tests/test_issue_119_t07.py tests/test_issue_120_t08.py -q -p no:cacheprovider --basetemp D:/Desktop/issue-112-context/t120-main2
```

结果：`143 passed in 493.37s`。

静态检查：`main/backend/app/models.py`、`message_attachments.py`、`messages.py`、`projects.py`、`routers/assets.py`、`routers/messages.py`、`routers/projects.py`、`schemas.py`、`presenters.py`、`workflow/contracts.py`、`s03.py` 与新增测试均通过 `ruff check --no-cache`；`workflow/prompts.py` 仅报告 60 条既有 E501，`workflow/templates.py` 仅报告既有 `IMAGE_NOT_APPROVED` 单行过长，无其他类型错误，本次新增说明未引入检查错误。`git diff --check` 无错误，`compileall` 通过。

## 4. 逐条验收判定

| 票面要求 | 判定 | 证据 |
|---|---|---|
| 补齐公开图文消息命令和历史读取关系，保存稳定资产、实际版本/内容摘要与用途；不从当前项目缩略图反推历史 | 通过 | 测试 1、4：staged asset、发送绑定、历史 `MessageView.attachment`、content_version/sha256、usage、preview_url；删除业务引用后历史快照仍保留 |
| 遵守单参考图产品边界，发送前暂存与最终业务引用分开；选择/预览/移除不伪装已经发送的需求变更 | 通过 | 测试 1：暂存 revision=0、无 reference、无消息；预览可读；删除暂存无副作用；测试 2：用途不明不设业务引用、不升 revision |
| 自然语言用途明确绑定对应图片，不明确则澄清；替换引用使旧快照失效，新请求依赖与批准展示一致 | 通过 | 测试 2、3、4：usage=style/product 绑定、clarify 模板、旧 intent/preview invalidated、新冻结请求 references 与上传字节一致 |
| 拒绝跨账号、跨项目、错版、文件变化或不可访问资产；消息元数据、缩略图与实际字节均有权限保护 | 通过 | 测试 5：401/404/403/422、暂存后文件变化 409、跨项目 404、已发送重用与删除 409，预览字节按 owner/retention/`can_access_asset` 保护 |
| 临时库升级保留旧消息与历史，不猜测补造旧消息附件；公开接口结合请求捕获证明完整链路 | 通过 | 测试 6：0014→0015 旧消息保留、附件表为空、downgrade 保留消息；测试 4：冻结请求 references 与 Visual `reference_bytes` 一致 |
| 仅隔离环境验证，不操作真实用户数据/凭据/配置，保护用户未提交修改；测试、文档、截图与临时产物不进入应用本体 | 通过 | 全部测试使用临时 SQLite 与 `D:/Desktop/issue-112-context` basetemp；测试在 `backend/tests`，文档在 `docs/acceptance`；未触碰原用户树 |
| 所有新增入口从接入时遵守身份/所有者/对象关联/内容门禁/CSRF/来源/审计；后继安全复验不是延期补基本安全的许可 | 通过 | 测试 5 覆盖 staging/消息/预览边界；路由先校验 owner，附件按 project 校验，写 `message_attachment_staged`/`message_attachment_discarded` 审计；CSRF/Origin 由现有中间件验证 |
| 不改变 SRS 既定参数或次数口径，不新增免费预览、自动等待超时、隐式排队或自动图片重试；按当前证据声明通过，缺失证据保持未知 | 通过 | 未改 SRS 参数、预算、槽位或 usage 口径；测试中无自动重试/定时器/排队；未实现 R3 预览，真实前端/设备证据保持未知（#129） |

## 5. 残留风险

- 只支持单参考图；多图/附件选择 UI、粘贴/拖入和真实浏览器行为不在本票范围，留待 T09/#129。
- 暂存文件与数据库行之间仍存在文件系统与数据库的窄窗口；失败路径会删除文件，进程崩溃时可能留下不可引用孤儿文件，需后续清理策略。
- 同一资产只能绑定一次，重复上传同一字节会生成不同资产；未做跨消息资产去重，符合“图片属于当时消息”的保守解释，但会增加存储。
- `usage=none` 附件提升只取最新一条待定附件；更早未明确用途的附件不会自动成为业务引用，保留为历史附件。
- 未覆盖真实浏览器、手机、前端附件交互；R3 后按 #129 补真实浏览器和设备证据。

## 6. 合并复核（Lead）

- 功能提交：`23eb28c feat(workflow): attach images to messages with stable reference usage (#120)`（22 个文件，+1509/-23）。
- 集成合并：`8a41efe merge: T08 image-text messages with stable reference usage (#120)`，以 `--no-ff` 合入 `codex/issue-112-integration`（父提交 `82cdb1e`）。
- 合并后主验收使用同一 10 个测试文件与隔离临时库：`143 passed in 493.37s`（basetemp `D:/Desktop/issue-112-context/t120-main2`）。
- 测试文件与功能提交中的版本一致（`git diff 23eb28c..8a41efe -- backend/tests/test_issue_120_t08.py` 无输出）；合并前静态检查为仅既有 E501、`compileall` 通过、`git diff --check` 无错误。
- 结论：T08 后端纵向切片验收通过，`#120` 可关闭；R3 前端仍须先通过 #121 的 R2→R3 门槛，真实浏览器/设备证据留待 #129。
