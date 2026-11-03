# Issue #112 GF-03 验收记录：封闭旧确认/生图写入旁路

## 1. 范围与结论

- 工单：GF-03（`version/v3/workflow-gate-remediation-v3.md` §2.1），父任务 #112。
- 目标：把旧确认/生图网络入口收拢为稳定的无副作用 tombstone；确认每个确认、生图、重试、恢复、发布写入口只有一个归属；保留历史详情、结果找回、下载的归属/内容批准/当前访问校验。
- 基线：`integration/v3`，从 `fa2158b`（codex/issue-112-integration 合并后的集成基线）开始。
- 本票生产代码变更：`main/backend/app/workflow/access.py`、`main/backend/app/routers/briefs.py`、`main/backend/app/routers/generations.py`；测试/契约更新：`backend/tests/test_gf03_legacy_write_closure.py`、`backend/tests/test_t15_contract.py`；`main/backend/app/routers/assets.py` 仅补齐新附件删除路由的 422 公共错误信封文档。
- 结论：旧 `POST /api/projects/{id}/confirm` 与 `POST /api/projects/{id}/generations` 对真实项目（包括没有 WorkflowRun/legacy 项的全新项目）返回稳定 409 `LEGACY_FLOW_DISABLED`，不解析业务载荷、不修改项目、不创建任务、不调用模型或图片供应商。历史 GET 与未知任务找回保持只读/按当前权限拒绝。

## 2. 路由归属清单

### 2.1 正常 v3 写入口

| 路由 | 归属 | 说明 |
|---|---|---|
| `POST /api/projects/{project_id}/workflow/actions/{action_id}` | `workflow.dispatch.handle_action` | 唯一 v3 工作流命令入口；每个 action 由 `ACTION_OWNER` 唯一映射到 S01—S12。 |
| `POST /api/projects` | `services.projects.create_project` | 项目生命周期入口，不确认提示词、不生图。 |
| `PATCH /api/projects/{project_id}` | `services.projects.update_project` | 项目元数据（如标题），不确认提示词、不生图。 |
| `POST /api/projects/{project_id}/reference`、`DELETE` 同路径 | `services.assets` | 参考图资产管理；v3 参考用途仍由 S02 命令确认。 |
| `POST /api/projects/{project_id}/message-attachments`、`DELETE .../{asset_id}` | `services.message_attachments` | 待发送图片附件暂存/丢弃，不是生成授权。 |
| `POST /api/projects/{project_id}/messages` | `services.messages.send_message` | 消息入口；有 WorkflowRun/附件时进入 v3 工作流消息路径，否则旧路径受 legacy 守卫约束。 |
| `PATCH /api/projects/{project_id}/brief` | `services.briefs.update_brief` | 旧需求编辑入口，内部执行 legacy 守卫；不是提示词最终确认。 |

### 2.2 旧确认/生图 tombstone

| 路由 | 归属 | 行为 |
|---|---|---|
| `POST /api/projects/{project_id}/confirm` | `routers.briefs.post_confirm` | 先校验当前登录与项目归属，再无副作用返回 409 `LEGACY_FLOW_DISABLED`；不再反序列化旧确认请求，不调用 `confirm_brief`。 |
| `POST /api/projects/{project_id}/generations` | `routers.generations.submit_generation_endpoint` | 先校验当前登录与项目归属，再无副作用返回 409 `LEGACY_FLOW_DISABLED`；不调用 `submit_generation_and_schedule`，不创建/调度任务。 |

两条旧路由在 OpenAPI 中标记 `deprecated=True`，不再声明 200/202 成功模型或 `GenerationResponse`；仅公开 401/404/409/422 错误信封。

### 2.3 只读历史与找回入口

| 路由 | 归属 | 行为 |
|---|---|---|
| `GET /api/projects`、`GET /api/projects/{project_id}` | `services.projects` / presenters | 按当前账号和项目归属读取历史详情。 |
| `GET /api/projects/{project_id}/workflow` | `routers.workflow` | 只读工作流快照，不执行 action。 |
| `GET /api/generations/{generation_id}` | `services.generations.load_generation` | 按 generation 归属读取；未知/他人 404。 |
| `POST /api/generations/{generation_id}/retrieve-result` | `services.retrieval.retrieve_result` | 只找回既有结果，不创建新生成；受 legacy 守卫与当前访问校验约束。 |
| `GET /api/assets/{asset_id}` | `routers.assets` | 历史和交付资产读取；保留内容批准、交付状态、归属校验，不公开未批准候选。 |

### 2.4 workflow action 唯一归属（S01—S12）

| 阶段 | action |
|---|---|
| S01 | `submit_request`、`answer_intent_clarification` |
| S02 | `analyze_reference`、`confirm_reference_usage` |
| S03 | `compose_brief`、`answer_requirements_clarification`、`revise_brief_draft` |
| S04 | `plan_creative`、`revise_creative` |
| S05 | `plan_visual`、`revise_visual`、`finalize_requirements` |
| S06 | `confirm_requirements`、`revise_requirements` |
| S07 | `compile_prompt`、`revise_prompt`、`confirm_prompt`、`abandon_generation` |
| S08 | `submit_generation`、`verify_generation`、`retry_generation`、`recover_generation`、`close_generation_tracking`、`start_new_generation` |
| S09 | `review_candidate`、`recheck_candidate`、`retry_rejected_generation`、`revise_prompt_and_retry` |
| S10 | `prepare_delivery`、`approve_delivery`、`publish_delivery`、`request_new_version` |
| S11 | `retrieve_original_result`、`review_legacy_item` |
| S12 | `continue_workflow`、`recover_submission`、`resume_after_restart`、`cleanup_expired` |

## 3. TDD 反例（RED）

新增 `backend/tests/test_gf03_legacy_write_closure.py`，全部通过真实 HTTP、真实临时 SQLite、受控 LLM/Visual adapter：

- `test_fresh_project_legacy_confirm_and_generation_are_side_effect_free_tombstones`：全新项目没有 WorkflowRun/legacy 项，旧访问策略原本会放行；修复前旧 `/confirm` 在无 body 时返回 422 `VALIDATION_ERROR`，旧 `/generations` 在合法 request_id 下返回 409 `FINAL_CONFIRMATION_REQUIRED`，证明旧入口仍可到达服务层并参与确认/生成判断。
- 修复前 RED 运行：`2 failed, 1 passed in 5.59s`（首次运行另有一次测试夹具 `generation_slots.project_id` 查询错误，已修正后重跑）。
- 覆盖输入：无参数、空对象、旧客户端合法载荷、伪造 `approval_id/approved/force`、同 request_id 重放、`acknowledge_unknown`；断言全部 409 `LEGACY_FLOW_DISABLED`，且项目 revision/brief_status/confirmed_revision/prompt_revision/updated_at、messages、generations、generation_jobs、generation_intents、generation_usage、generation_slots、approvals、prepared_prompts、final_authorizations 均不变；LLM/Visual 调用计数不变。
- 历史只读：旧入口封闭后 `GET /api/projects/{id}` 仍 200，未知 generation 的 `retrieve-result` 仍 404，匿名旧 confirm 仍 401。
- 账号禁用：owner 账号被 admin 禁用后，旧 confirm/generations 均 401 `AUTH_REQUIRED`，零写入、零模型/图片调用。

## 4. GREEN 与回归证据

工作目录：`D:/Desktop/新建文件夹/backend`；解释器：`.venv/Scripts/python.exe`。

本票新增测试：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_gf03_legacy_write_closure.py -q -p no:cacheprovider
```

结果：`3 passed`。

本票 + 契约测试：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_t15_contract.py tests/test_gf03_legacy_write_closure.py -q -p no:cacheprovider
```

结果：`8 passed in 13.78s`。

既有 LEGACY_FLOW_DISABLED 真实路由回归：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_t70_r2_gate.py::test_legacy_confirmation_and_generation_entries_are_closed tests/test_v2_t01_legacy.py::test_legacy_write_entrances_are_paused_without_provider_calls -q -p no:cacheprovider
```

结果：两个用例通过（与 t61 一起的批量结果为 `7 passed, 4 failed`；四项失败见 §6，均非 GF-03 旧入口回归）。

OpenAPI 契约更新：
- `test_openapi_documents_every_public_operation`：当前公开 operation 数为 40（分支新增附件路由后原常量 38 已过期），更新为 40。
- `test_error_responses_use_the_public_envelope`：补上 tombstone 与新附件删除路由的 422 `ErrorResponse` 文档，所有 4xx/5xx 使用公共错误信封。
- `test_generation_contract_documents_retired_entrance`：旧 generations 不再声明 202/200 `GenerationResponse`，只保留 409/422 `ErrorResponse`。

## 5. 受影响旧场景与处理

以下旧 v1/v2 套件直接使用旧 `/confirm` 或 `/generations` 并期待成功；GF-03 后它们收到 409 `LEGACY_FLOW_DISABLED`，属于预期的旧场景退役，不再作为 v3 行为证明：

- `backend/tests/test_t04_brief.py`
- `backend/tests/test_t06_generation.py`
- `backend/tests/test_t06_process.py`
- `backend/tests/test_t07_idempotency.py`
- `backend/tests/test_t08_async.py`
- `backend/tests/test_t09_reference_versions.py`
- `backend/tests/test_t10_restart.py`
- `backend/tests/test_t11_retrieve.py`

这些场景的 v3 替代证据为 `test_issue_113_t01.py`—`test_issue_121_t09.py` 中的工作流准备/最终确认/恢复/发布用例；未通过旧入口成功来证明任何新行为。没有新增仅测试可见的生产旁路开关。

## 6. 残余与边界

- `tests/test_t61_history_recovery_gate.py::test_published_bytes_are_exact_and_unapproved_states_stay_closed` 在 `publish_delivery` 处收到 409 `FINAL_CONFIRMATION_REQUIRED`（来自 `test_v2_t13_delivery.py:336` 的旧夹具）；该路径不使用旧 `/confirm` 或 `/generations`，不是 GF-03 改动导致，属于 GF-01/02/04 最终执行授权对旧交付夹具的既有影响，需在后续 GF 项重新对照并修复/迁移。
- `test_t61_history_recovery_gate.py` 其余 3 个用例（未登录 401、跨账号历史/资产不泄露、参考摘要/定位路径失败关闭）通过，支撑历史读取门禁。
- `test_t15_contract.py` 原先把旧 generations 当 202 成功契约；本票按 GF-03 改为 tombstone 契约，不是生产行为回退。
- 未执行真实浏览器/手机/供应商验收；本票只证明后端公开 HTTP 行为与调用计数。
- 未改动 SRS 参数、配额口径、数据库迁移；历史旧 v1/v2 套件未整体迁移，按上述退役清单保留记录。

## 7. 结论

GF-03 的旧确认/生图写旁路已在真实路由上封闭为稳定、无副作用、可审计的 409 tombstone；正常 v3 写命令仍唯一归属 workflow action 分发；历史详情、结果找回、资产读取保留当前账号/归属/内容批准门禁。模型与图片调用次数在每次旧入口尝试中均为零。
