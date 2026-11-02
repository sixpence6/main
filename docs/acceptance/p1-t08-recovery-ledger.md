# P2 / T08 — 持久恢复预算、零技术自动重试与远端未知只读核实（#94）

- 票：**#94（T08，P2）**；父问题 **#86**；批次 P2（接已完成的 P1 #92 / T07 #93）
- 集成分支：**integration/v3**；运行基线 **cb478a7**（T07 验收文档提交后）；本票代码提交 **f43779a**；证据提交见本文件所在提交
- 规则目录版本：**RULESET_VERSION = v3.rules.1**；提示词版本 **2.0.0**；迁移头 **0011**
- 环境：隔离临时 SQLite、计数型 **LLMStub**、**VisualStub**、数据根为临时目录、网络全部 stub；无真实 provider、无真实图片生成
- 本票代码文件：**main/backend/app/services/recovery.py**（启动修复清理残留 claim）、**main/backend/app/workflow/state.py**（原子 claim/release）、**tables.py**（持久化 claim 列）、**stages/s07.py**（持久化请求精确重放与预算校验）、**stages/s12.py**（S12 恢复路由与 claim 包裹）、**backend/migrations/versions/0011_recovery_claims.py** 与镜像迁移（新增两列）、**backend/tests/test_v3_p1_t08_recovery.py**（新增 9 项）
- 证据 JSON：**docs/acceptance/evidence/p1-t08.json**

## 1. 结论与交付内容

本票把技术失败后的恢复从“重新编制/重新抽样”改为“重放持久化的原请求并复用原候选预算”。失败检查点记录**同一 operation_id、角色、任务、请求载荷、候选 revision 和 attempt**；S12 的 **continue_workflow** 在验证运行状态、快照/依赖/预算后，以独立数据库连接原子占用 **WorkflowRun.recovery_claim_id**，再精确重放原请求。进程重启只恢复只读状态，不自动调用模型；刷新、状态查询、重登与重复 continue 均不产生模型调用。远端提交事实未知时，S07 在模型调用前停止并返回 **GENERATION_UNKNOWN**，不重发图片；只读核实与合法原任务恢复走既有 GenerationJob 路径，不能变成新提交。

实现要点：

- **recovery.py**：启动修复时清空 **WorkflowRun.recovery_claim_id / recovery_claimed_at**，避免硬崩溃遗留 claim 永久阻塞手动恢复。
- **state.py**：**claim_recovery(ctx, operation_id, stage=...)** 在 **BEGIN IMMEDIATE** 写事务中读取运行行，要求 run 为 **blocked/failed** 且 stage 匹配；已有 claim 时返回 **409 PROJECT_BUSY**；**release_recovery** 只清除匹配 operation_id 的 claim。两个独立 session 竞争时只有一个成功。
- **s07.py**：恢复路径从检查点读取 **request_json**，要求角色为 **generation_engineer**，读取 attempt/input_data/repair_codes；校验持久化 **RevisionBudget.used == attempt**、该 revision 尚无 Artifact、最终 brief / confirmed_revision / hard_requirements 未变；构造 **RevisionReservation** 并调用 **run_role_request** 重放同一请求，不调用 reserve、不新增 intent/usage/slot。自动修复仅在正常路径启用（recovery is None）；恢复失败记录 producer_failed 并按类型落状态。
- **s12.py**：**_resume_s07_producer** 与 **_resume_s07_review** 分别精确重放 producer/reviewer 请求；reviewer 路径校验持久化 output_reviewer task/target、最新候选 prompt_plan，并按原 attempt 调用 **run_role_request(capability='TEXT', attempt=...)**。整个恢复过程由 claim/release 包裹，异常路径最终释放 claim。
- **tables.py + 0011 migration**：持久化 **recovery_claim_id**（String 64）与 **recovery_claimed_at**（String 32），根 backend 与 main/backend 两份迁移镜像保持一致。

## 2. 验收覆盖（票面八项）

| 票面验收 | 覆盖与结果 |
| --- | --- |
| 1. 正常失败、S12、进程重启、并发手动恢复共享原产物持久化候选预算；失败/崩溃不重置、不重复消费 | **test_s07_producer_failure_replays_exact_request_without_new_budget**：失败后 used=1，S12 重放同一 payload 后仍 used=1、一个 approved revision 1；**test_s07_replay_budget_and_ledger_survive_process_restart**：重建 app 后 used=1 且无额外模型调用，手动 continue 后仍 1；**test_s12_review_resume_replays_exact_reviewer_request_without_new_budget**：reviewer 失败后重放同一 review payload，仍 used=1；**test_concurrent_manual_recovery_shares_one_budget_and_one_replay**：重叠请求只有一个重放，第二个 409，总预算 1 |
| 2. 技术故障自动重试为零；刷新、重登、状态查询、重启不调用模型；故障后仅真实可用、明确授权的手动恢复可继续调用 | 失败测试中连续两次 workflow 查询不增加 role call；S08 后重复 continue 返回 200 且仍为 3 次 role call；restart 测试在重建 app 后查询仍为 1 次 call，直到显式 continue；**test_disabled_account_blocks_manual_recovery_before_model_call** 证明禁用账号的 continue 在模型前被 401 拒绝；无任何后台自动重试 |
| 3. 已提交/远端受理未知时先核实原任务，超时不推断未提交，不借修编制或恢复重发图片；无法证明未提交即停止自动推进 | **test_s07_recovery_stops_when_image_submission_is_unknown**：持久化 GenerationJob=unknown、submit_attempted=1 时，continue 返回 409 **GENERATION_UNKNOWN**，预算仍 1、无 artifact、visual submit 为空；支持组 **test_t68_recovery.py::test_unknown_verify_is_read_only_and_recover_polls_original** 证明 verify 只读、recover 轮询原 remote_task_id 且 submit_calls 仍为 1；**test_v2_t16_unknown_recovery.py::test_original_submission_replay_returns_same_task_without_provider_call** 证明重放返回同一 job/request_id，不新增生成 |
| 4. 并发恢复用独立数据库连接验证预算与意图互斥；重复/丢回执/跨标签重放只对应原意图及原计数 | **test_recovery_claim_is_atomic_across_independent_connections**：两个独立 **state.load_context** 竞争 claim，恰好一个成功、另一个 **PROJECT_BUSY**，启动修复清理遗留 claim；**test_concurrent_manual_recovery_shares_one_budget_and_one_replay**：HTTP 层重叠 continue 只有一个进入模型，第二个在模型前返回 409，最终仍 1 intent/1 usage/3 slots |
| 5. 账号禁用、授权/快照/依赖失效阻止新增调用和发布；已返回结果安全隔离，不假称远端取消成功 | **test_disabled_account_blocks_manual_recovery_before_model_call**：禁用 owner 后 continue 返回 401 **AUTH_REQUIRED**，无额外 role call、无 artifact、预算不变、claim 释放；**test_s07_recovery_rejects_changed_dependency_before_model_call**：项目 revision 变化返回 409 **STALE_RECOVERY** 且模型未调用。支持组 **test_t62_background_ownership.py::test_disable_stops_next_remote_call_and_keeps_remote_fact_without_publishing** 保留 remote_task_id、job=unknown、不 poll/不发布；**::test_publish_rechecks_persisted_owner_before_writing_public_bytes** 证明发布前重查禁用 owner 且不写 published；**::test_disabled_owner_cannot_make_another_model_call** 证明禁用后不能新增模型调用 |
| 6. 只读核实、合法原任务恢复和文本修复不多计受理；失败不抹除受理账本；活跃槽位按持久化任务终态结算 | 所有 focused 场景保持 **1 intent / 1 GenerationUsage / 3 unreleased GenerationSlot**，不因重放增加计数；失败测试确认预算 used 保持 1；支持组 **test_t68_recovery.py::test_terminal_provider_failure_counts_once_and_releases_slots** 证明终态失败仍只 1 次 usage 且 3 个 slot 全释放；**::test_result_fetch_failure_keeps_slot_and_recover_fetches_without_submit** 证明取回失败不重提交、按原任务恢复 |
| 7. 通过 HTTP、可计数模型/图片适配器、真实临时 SQLite、可重建应用验证中断前后及恢复次数，覆盖 WG3-AC-07/08 | 9 项 focused 测试全部走真实 HTTP 应用工厂、真实临时 SQLite、**LLMStub** 计数与 **VisualStub**；restart 用同一临时库重建应用对象；并发用独立 HTTP 请求和独立 session；所有场景断言模型调用次数、预算、artifact、intent/usage/slot 和 visual submit。WG3-AC-07（原意图且未提交时修复不多计、预算跨恢复/并发不重置、耗尽即停）与 WG3-AC-08（格式错误不强迫改需求、超时/重启/远端未知不自动重发图片）均有对应用例 |
| 8. 使用既有测试 seam；测试、临时文件、验收证据放业务目录外；列明基线、命令/结果、调用/计数对照和残余未知 | 复用 **tests.test_t65_generation_cycle._environment/_setup_s06/_drain/_rows**、**tests.test_t68_recovery._confirm/_normal_responder** 等既有 seam；新增测试仅在 **backend/tests**，证据仅在 **docs/acceptance**；未在 **main/** 写测试脚手架或临时证据；本文与 JSON 记录基线、实际命令、16 次计数调用、预算/受理对照与残余未知 |

## 3. 验证记录

以下命令均在工作树 **D:\Desktop\新建文件夹\.worktrees\t94\backend**，使用 **D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe**；环境为临时 SQLite、计数 stub、无网络。

| 命令 / 范围 | 结果 |
| --- | --- |
| **ruff check --ignore E501**（5 个源码文件、2 份 0011 迁移、focused 测试） | **All checks passed!** |
| **pytest -q tests/test_v3_p1_t08_recovery.py -x** | **9 passed in 15.83s** |
| **pytest -q -rxX** focused + t68 + v2 t16 + v2 t17 + t62 + t07 + t69 | **55 passed in 143.97s (0:02:23)** |

本票没有改动前端或 UI；未执行浏览器/设备验收。没有真实 provider、TLS、发布环境或真实数据根操作。

### 3.1 调用 / 计数对照（计数型 stub）

| 场景 | generation_engineer | output_reviewer | prompt artifact | 预算 used | 受理 | 图片提交 |
| --- | ---: | ---: | --- | ---: | --- | ---: |
| 正常失败（失败前） | 1（失败） | 0 | 0 | 1 | 1 intent / 1 usage / 3 slots | 0 |
| 正常失败后手动恢复 | 1 次重放 | 1 | 1 approved revision 1 | 1 | 不变 | 0 |
| 进程重启后手动恢复 | 1 次重放 | 1 | 1 approved | 1 | 不变 | 0 |
| S12 reviewer 失败（恢复前） | 1 | 1（失败） | 1 candidate | 1 | 1 intent / 1 usage / 3 slots | 0 |
| S12 reviewer 手动恢复 | 0 | 1 次重放 | 同一 candidate → approved | 1 | 不变 | 0 |
| 并发手动恢复 | 1 次重放 | 1 | 1 approved | 1 | 不变；第二请求 409 | 0 |
| 预算耗尽 | 0（模型前停止） | 0 | 0 | 3（强制 used=max） | 保持 | 0 |
| 依赖 revision 变化 | 0（模型前停止） | 0 | 0 | 1 | 保持 | 0 |
| 远端提交未知 | 0（模型前停止） | 0 | 0 | 1 | 保持 | 0 |
| 账号禁用 | 0（鉴权拒绝） | 0 | 0 | 1 | 保持 | 0 |
| 独立连接 claim 竞争 | 0 | 0 | 0 | 0 | 0 次受理 | 0 |

**合计**：model_text = **16**（含失败调用），model_vision = **0**，image_generation = **0**，visual submit = **0**。全部为计数型 stub 观测，不是生产 provider 的 token/延迟/费用数据。

## 4. 已知红色与残余未知

### 4.1 已知红色（不作为本票通过证据）

T07 已记录的既有基线仍存在，但本批 focused/regression 未重新运行这些隔离用例：

- **tests/test_t11_retrieve.py::test_failed_download_can_retrieve_same_version_and_downloads_real_file**
- **tests/test_t11_retrieve.py::test_retrieval_stays_failed_while_waiting_and_blocks_other_write**
- **tests/test_v2_t48_retrieval.py::test_retrieve_failed_result_updates_same_generation_after_gates**
- **tests/test_v2_t48_retrieval.py::test_g06_unknown_keeps_retrieved_asset_quarantined**
- **tests/test_t83_retention_secrets.py::test_secret_markers_stay_out_of_api_database_filenames_and_backup**

这些是既有 retrieval / 环境数据根基线，不属于 T08 改动，也不能反向证明 T08 通过。

### 4.2 残余未知

1. **没有单次全量 pytest 汇总**：本批实际执行 focused 9 项和受影响组 55 项；T07 记录的全量超时仍未解决，不能声称全量套件通过。
2. **计数为 stub 观测**：model_text=16 来自 LLMStub，真实 provider 的 token、延迟、费用、重试策略未测量；无真实图片生成。
3. **远端核实仍是本地事实**：unknown 路径由持久化 GenerationJob 与只读 t68 用例验证，没有真实远端任务可核对；生产远端核实能力需在具备真实 provider 的验收层单独取证。
4. **启动清理的进程假设**：**repair_leftover_state** 启动时无条件清理 recovery claim；当前为单进程 SQLite 应用，未覆盖“另一进程仍在恢复时启动新进程”的多进程竞争。
5. **跨标签/丢回执**：后端 HTTP 幂等与并发边界已覆盖；完整浏览器跨标签、丢回执和 UI 恢复属于前端门禁，不在本票后端证据内。
6. **发布/取消文案**：发布前 owner 重查、禁用后不发布/不新增调用由支持组 t62 覆盖；没有新的 T08 专用发布测试，也没有真实远端取消操作（实现不宣称取消成功）。

## 5. 下一入口

T08 源码、focused 测试与本次证据提交完成后，进入 **#95/T09（多句覆盖与语义准确）**。P3 的 #96–#98 与 P4 仍未完成；本文件不构成 v3 发布或全量通过声明。
