# Issue #112 / T03：失效矩阵与登录上下文验收记录

## 1. 背景与范围

T03 处理的是“确认之后、真正提交图片之前”的授权边界。旧实现的问题不是缺少确认按钮，而是确认时生成的最终命令可能继续引用已经变化的需求、提示词、批准记录、参考图片、输出参数或视觉服务配置；同时，登录会话的生命周期变化、账号禁用以及跨项目/运行/意图的快照复用，都必须在产生副作用之前被拒绝。另一个关键点是：已发布历史仍需可读，但历史页面上的旧确认按钮不能再次执行。

本票的服务端原则是：客户端提交的 `snapshot_id`、`snapshot_digest`、`command_id` 和 `command_context` 只代表“曾经看过并确认过”，不能代表“现在仍然有效”。每次确认、每次幂等重放、每次后台提交和每次发布，都必须重新对照当前数据库与当前服务配置复验。

本票不涉及前端视觉改版，也不新增迁移；数据库结构沿用集成分支 tip `da459a4` 已有表。

## 2. 验收条目、测试名与证据

规格文件：`backend/tests/test_issue_115_t03.py`（28 个测试，禁止修改/放宽）。以下“测试名”均为该文件中的实际测试函数；参数化用例会在括号中列出。

| 验收条目 | 测试名 | 期望与证据 |
|---|---|---|
| 需求/提示词/批准/参考版本/输出参数/服务配置变化后，旧最终命令必须被拒，且不得产生图片提交 | `test_invalidation_matrix_rejects_old_final_command_without_attempt`（12 个参数：`requirement_revision`、`prompt_content`、`approval_record`、`reference_file`、`reference_version`、`output_size`、`output_format`、`output_count`、`service_model`、`service_base_url`、`service_timeout`、`service_credential`） | 每种变化都断言 HTTP 409；需求版本返回 `REVISION_CONFLICT`，其余返回 `PROMPT_STALE`；服务配置错误消息含“服务配置”，参考图错误含“参考”。同时断言 `prompt_preview` 被清空、`generation_job` 为空、用量仍为 1、`generations/generation_jobs/final_authorizations` 全为 0、`visual.submit_calls` 为空、LLM 角色调用次数不增加。 |
| 只改项目标题不失效 | `test_title_only_rename_keeps_effective_snapshot_and_attempt` | 标题 PATCH 后项目 revision 不变，预览 id 与 `request_digest` 不变；随后确认返回 200，只提交一次图片，用量为 1。 |
| 同账号重新登录只能读回自己的命令上下文，不能复用旧登录的上下文 | `test_same_account_relogin_reads_own_context_without_authorization_or_idle_refresh` | 登出再登录后，预览的 `request_digest` 相同，但 `command_context` 不同；未产生 `final_authorizations`；读取工作流不刷新 idle 过期时间；用旧上下文确认返回 409 `FINAL_CONTEXT_CHANGED`；新上下文确认成功且只提交一次。 |
| 旧登录载荷（expired/revoked/epoch_changed/account_disabled）在授权前被拒 | `test_old_login_lifecycle_payload_is_rejected_before_authorization`（`expired`、`revoked`、`epoch_changed`、`account_disabled`） | 四种情况均在确认接口返回 401 `AUTH_REQUIRED`，`final_authorizations` 为 0，`visual.submit_calls` 为空。 |
| 外来快照绑定被拒（wrong_project/wrong_run/wrong_intent） | `test_foreign_snapshot_binding_is_rejected`（`wrong_project`、`wrong_run`、`wrong_intent`） | 直接篡改 `prepared_prompts` 的归属列；三者均返回 409；`wrong_intent` 明确返回 `PROMPT_STALE` 且消息含“准备意图”；三者都无最终授权、无图片提交。 |
| 幂等重放仍复验当前门（approval_revoked/service_changed/revision_changed） | `test_idempotent_replay_rechecks_current_gates`（3 个参数） | 第一次确认成功且只提交一次；随后撤销批准、修改视觉模型或提升项目 revision，再用同一命令重放，均返回 409；仍只有一次提交，用量仍为 1，任务 id 与第一次相同。 |
| 账号禁用阻断下一次图片调用与发布 | `test_disabled_account_blocks_next_image_call_without_attempt`、`test_disabled_account_blocks_publish_and_keeps_delivery_unpublished` | 禁用后确认接口返回 401 `AUTH_REQUIRED`，无最终授权、无图片提交；发布接口同样 401，交付仍为 `approved`，`published` 目录为空或不存在。 |
| 禁用后迟到的 provider 结果不得写回 | `test_late_provider_result_after_disable_does_not_write_back` | 提交已开始后禁用账号，再放行远端结果；任务状态为 `unknown` 且错误码 `ACCOUNT_DISABLED`；保留 `remote_task_id`，但 `result_asset_id` 为空、生成错误码为 `ACCOUNT_DISABLED`；不再轮询，也不产生交付记录。 |
| 已发布历史可读，但旧按钮不可执行 | `test_published_history_readable_but_old_command_not_executable` | 发布后改服务配置，工作流仍能读到 `prompt_plan` 的 payload，但 `prompt_preview` 为 `None`；用旧快照确认返回 409 `PROMPT_STALE`，图片提交次数保持 1。 |

## 3. RED → GREEN 证据

合法 runner（Windows PowerShell，工作目录为本 worktree 的 `backend`）：

```powershell
$py = 'C:\Users\28425\.codex\worktrees\issue-112-integration\新建文件夹\backend\.venv\Scripts\python.exe'
Set-Location 'C:\Users\28425\.codex\worktrees\issue-112-t03\新建文件夹\backend'
$env:TEMP='<backend>\.scratch\t03\temp'; $env:TMP=$env:TEMP
& $py -m pytest tests\test_issue_115_t03.py -q -p no:cacheprovider --basetemp 'D:\Desktop\issue-112-context\t03-pytest'
```

- 首次基线（规格文件尚为 502 行版本）：`8 failed, 20 passed`。失败集中在：需求 revision 的错误码、`output_format/output_count` 与 `service_*` 的失效标签，以及 `wrong_intent` 的快照绑定。
- 规格文件在实施期间被外部更新为最终 497 行版本（sha256 `21dacb182d9a1928` 前缀，最终内容以提交中的测试文件为准）；实现按最终版本收敛。
- 最终完整运行：`28 passed in 111.19s`。
- 回归：`tests\test_issue_114_t02.py` 与 `tests\test_issue_113_t01.py` 合计 `49 passed in 196.31s`。
- 静态检查：`python -m ruff check main/backend/app/confirmation_invalidation.py main/backend/app/prompt_preparation.py backend/tests/test_issue_115_t03.py --no-cache` 全部通过；`git diff --check` 无输出。

## 4. 关键实现位置

### 4.1 失效分类与统一拒绝

新增 `main/backend/app/confirmation_invalidation.py`：

- `InvalidationReason` 与中文标签：19-39 行。覆盖快照完整性、需求版本、提示词内容、批准记录、参考内容、输出参数、服务配置、准备意图。
- `_FIELD_REASONS`：41-62 行。把冻结请求字段映射到上述类别；未知字段降级为“快照完整性”，避免静默放行。
- `invalidation_reasons(frozen, actual)`：65-78 行。逐字段比较“确认时的冻结请求”与“现在重新构建的实际请求”，按固定顺序去重返回原因。
- `raise_for_reasons(reasons)`：81-92 行。统一抛出 409 `PROMPT_STALE`，消息包含具体变化类别，例如“提示词快照已失效（服务配置已变化），请重新读取预览并再次确认。”。
- `require_snapshot_binding(snapshot, frozen)`：95-103 行。校验快照的 `project_id/run_id/intent_id` 与冻结请求一致；不一致按“准备意图”失效处理。

### 4.2 在确认链路中接入复验

`main/backend/app/prompt_preparation.py`：

- 导入新模块：19-23 行。
- `validated_request`：404-416 行。先校验批准与冻结请求摘要，再调用 `require_snapshot_binding`（407 行），随后用 `build_request` 重建实际请求，并通过 `invalidation_reasons` + `raise_for_reasons`（411-413 行）拒绝任何有效字段漂移。原先只比较“任一字段不同”的粗粒度分支已替换为可分类原因。
- 该函数从确认、重放和提交前门禁复用，因此失效不是只发生在第一次确认；重放时也会重新构建并复验。

### 4.3 既有登录与禁用边界（本票依赖并验证）

- `main/backend/app/auth/sessions.py` 59-76 行：`load_session` 在读取会话时拒绝已撤销、绝对/空闲过期、账号 `can_login` 为假、`account_epoch` 不匹配的会话，统一按未认证处理。
- `main/backend/app/auth/principal.py` 21-32 行：请求 principal 只来自当前请求状态，缺失即 401。
- `main/backend/app/final_confirmation.py` 64-77 行：`command_context` HMAC 绑定登录会话 id、账号 epoch、快照 id 与请求摘要；同账号重登后上下文必然不同。194-252 行负责确认时的会话、快照、上下文、revision 与幂等重放复验。
- `main/backend/app/generation_guard.py` 74-86 行与 89-176 行：后台任务/生成在继续前复核 owner/operator 账号仍可用；存在 `FinalAuthorization` 时调用 `require_authorized_attempt`，让失效矩阵同样约束后台路径。
- `main/backend/app/workflow/stages/s08.py` 455-503 行：账号禁用时把任务与生成标记为 `ACCOUNT_DISABLED`，并把关联资产置为 rejected；1052-1066 行阻止禁用后迟到的 provider 完成结果写回；642-659 行确保此类失败不被误记为可重试终态。
- `main/backend/app/workflow/stages/s08.py` 1052-1066 行与 `main/backend/app/generation_guard.py` 74-86 行共同保证 `result_asset_id` 不被写入。
- `main/backend/app/acceptance.py` 234-238 行：发布/验收路径把禁用账号映射为 `ACCOUNT_DISABLED`/认证失败，保证交付保持未发布。

## 5. 逐条判定

1. **失效矩阵**：通过。12 个参数覆盖需求、提示词、批准、参考文件内容与版本、输出尺寸/格式/数量、服务模型/地址/超时/凭据；全部在授权前拒绝，且断言无任何生成/任务/授权行与图片提交。
2. **纯标题修改**：通过。标题不在冻结请求中，项目 revision 不变，旧快照仍可确认一次。
3. **同账号重登**：通过。预览摘要可复用，但命令上下文因会话绑定而不同；旧上下文返回 `FINAL_CONTEXT_CHANGED`，新上下文才可执行，且读取不刷新 idle。
4. **旧登录载荷**：通过。过期、撤销、epoch 变化、账号禁用均在授权前 401。
5. **外来快照绑定**：通过。项目、运行、意图三种篡改均 409，意图错误带“准备意图”标签。
6. **幂等重放**：通过。批准撤销、服务配置变化、需求 revision 变化都会让重放 409，不会重复提交或重复计数。
7. **账号禁用阻断**：通过。确认与发布都被 401 拦截，交付与 published 目录保持未发布。
8. **迟到结果**：通过。任务保持 `unknown/ACCOUNT_DISABLED`，保留远端 id 但不写回资产、不轮询、不建交付。
9. **已发布历史**：通过。历史 payload 可读，预览为空，旧命令无法执行。

## 6. 已知残留风险与未覆盖项

- 规格文件在本实施期间被外部替换过一次；本记录以最终 497 行版本为准。提交前应再次确认测试文件未被继续改写，且提交中包含的就是最终版本。
- 失效比较以 `build_request` 重建出的“有效请求”为基准。若未来新增输出/服务字段但未加入 `build_request` 或 `_FIELD_REASONS`，该字段可能不会被本矩阵覆盖；新增字段时必须同步补映射与参数化用例。
- `invalidation_reasons` 当前只遍历实际请求的字段，冻结请求中多出的历史字段不会单独触发失效；这是为避免把仅用于审计的旧 bookkeeping 字段误判为业务变化，但也意味着未来若字段被删除，需要显式迁移/断言。
- 本票验证的是服务端拒绝与不产生副作用；前端按钮禁用/隐藏的视觉回归不在本票范围内，仍由前端测试覆盖。
- 测试依赖 SQLite 直接改库模拟外部变化，能证明服务端复验，但不模拟真实并发下“复验后、提交前”最后一刻的配置变更；该窗口仍依赖提交路径的再次 guard 与数据库事务边界。
- 未新增数据库迁移；若部署环境已有旧版 `prepared_prompts` 行，首次读取会按 `PROMPT_STALE` 要求重新预览，这是预期行为。
