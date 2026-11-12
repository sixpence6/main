# P2 / T07 — 类型化原因与原意图有界文本修复（#93）

- 票：#93（T07，P2）；父问题 #86；批次 P2（接已完成的 P1 #92）
- 集成分支：integration/v3；运行基线 **2ea9e39**（#92 验收文档提交后）；本票代码提交 **d2e6293**
- 规则目录版本：**RULESET_VERSION = v3.rules.1**；提示词版本 **2.0.0**；迁移头 **0010**
- 环境：隔离临时 SQLite、计数型 **LLMStub**、**VisualStub**、数据根为临时目录、网络全部 stub；无真实 provider、无真实图片生成
- 本票文件：**main/backend/app/workflow/reasons.py**（新增类型化原因）、**state.py**（预算扣占）、**dispatch.py**（失败态动作收口）、**stages/s07.py**（有界修复）、**stages/s09.py**（显式质量重试的已知拒绝例外）、**backend/tests/test_v3_p1_t07_text_repair.py**（新增 8 项），以及 t40/t66/t87/t65/t02 的兼容性回归更新
- 证据：**docs/acceptance/evidence/p1-t07.json**

## 1. 结论与交付内容

本票把 S07 提示词编制的状态转移从“错误文案子串判断”改为服务端类型化原因，并把文字候选预算改为**模型调用前持久化扣占**。可修复的覆盖映射缺陷只在原意图内、原快照／确认／依赖未变且图片可证明未提交时，按最多三个文字候选（初次加最多两次修订）定向修复并重新独立审查；真实漂移、内容拒绝、缺证据、协议错误、技术故障与远端未知分别停在各自的安全边界，不自动转成图片重试。

### 1.1 类型化原因

新增 **reasons.py** 的 **ReasonCode**，服务端只读取原因代码和候选缺陷代码，不读取本地化文案：

| 原因代码 | 触发来源 | 允许的下一动作 |
| --- | --- | --- |
| **missing_information** | MISSING_INPUT / CONFLICTING_INPUT，或未归类的输入缺失 | 回 U1 澄清；不创建文字候选 |
| **repairable_composition** | 纯覆盖映射缺陷且已确认内容仍在；reviewer 返回 revise | 原意图内定向修复，预算内重新独立审查；不新增意图／确认／生成次数 |
| **true_drift** | 已确认内容缺失、尺寸／数量／参考图变化、REQUIREMENT_DRIFT、项目 revision／依赖变化 | 隔离候选；放弃原意图并回 U1 重新确认；不自动抽样刷通过 |
| **content_rejection** | reviewer reject；UNSAFE_REQUEST / OUT_OF_SCOPE / MIXED_SCOPE | 保留隔离与安全说明；不自动重生成图片 |
| **missing_evidence** | reviewer unknown；MISSING_EVIDENCE / UNREADABLE_IMAGE | 保持待核实／未放行；补证或实际可用恢复动作 |
| **protocol_error** | 契约校验失败、审查协议错误、非法审查输出 | 4xx／协议错误；不冒充技术故障，不自动重试 |
| **technical_failure** | SERVICE_UNAVAILABLE / CAPABILITY_NOT_READY / REVIEW_UNAVAILABLE / LLM_TIMEOUT | 记录技术故障检查点；自动重试为零，仅保留显式手动恢复动作 |
| **remote_unknown** | GenerationJob 为 unknown／submitted 且无法证明未提交；GENERATION_UNKNOWN | 停止自动推进；只读核实原结果，不重发图片 |
| **authorization_invalid** | ACCOUNT_DISABLED / ACCOUNT_INACTIVE / UNAUTHORIZED_ACTION | 在下一模型调用前阻断；不创建候选 |

候选缺陷也使用独立代码：**DUPLICATE_COVERAGE / UNKNOWN_COVERAGE / BINDING_MISMATCH / COVERAGE_MISMATCH / MISSING_COVERAGE / SIZE_MISMATCH / COUNT_MISMATCH / REFERENCE_MISMATCH / CONTRACT_INVALID**。只有“纯 COVERAGE_MISMATCH 且 content_intact=true”允许自动修复；尺寸、数量、参考图或内容缺失一律归为 **true_drift**，不会因改一句错误文案而改变动作。

### 1.2 有界文字修复

**state.reserve_artifact_revision(ctx, prompt_plan)** 在同一个数据库事务中读取／创建 **RevisionBudget**，检查 used < max_allowed 后立即把 used 加一并提交；**start_artifact(..., revision_no=reserved_revision)** 只复用该扣占，不再二次加一。因此：

- 每次 producer 调用前预算已经持久化，刷新、重启或模型失败都不会把已扣占的次数“退回”；预算表约束仍为 used >= 0、used <= max_allowed、max_allowed = 3。
- 初次候选和最多两次修订共用一个 **prompt_plan** 预算；耗尽后下一次动作在调用模型前返回 **409 REVISION_BUDGET_EXHAUSTED**，不会降级为图片重试预算。
- 有效候选才创建 **Artifact**；无效候选只留下 **prompt_candidate_reserved / candidate_invalid / auto_repair** 诊断，不向客户端暴露候选 payload。
- 预算扣占不等于新的受理：不创建 **GenerationIntent**、不写 **GenerationUsage**、不新增 **GenerationSlot**，也不增加 S08 的图片提交次数。

修复循环只处理“格式／覆盖映射”这一类非危险偏差：同一轮请求内可自动重跑 **generation_engineer**，每次带 **attempt** 与 **repair_codes**；达到三候选后立即停止。reviewer 的 **revise／reject／unknown** 不自动抽样重跑，仍由既有显式动作处理；reviewer 的 **pass** 才写 **generation_prompt** 批准。

### 1.3 原意图与未提交证明

每次尝试开头重新加载运行上下文并执行 **s07._require_confirmed**：最终 brief、requirements_card 批准、项目 brief_status／confirmed_revision、运行 requirement_revision、参考图归属和使用方式、配置尺寸都必须仍是原快照。任一变化都会记录 **precondition_failed**，并按类型阻断；项目 revision／requirement revision 变化归为 **true_drift**。

**s07._ensure_image_unsubmitted** 读取该 run 最新的 **GenerationJob**：只有不存在 job，或 submit_attempted == 0 且状态为 **created／submit_pending** 且没有 **remote_task_id** 时，才允许继续文本修复。未知或已提交事实会记录 **remote_unknown / stop_auto_advance**，把 S07 置为 **blocked / GENERATION_UNKNOWN**，不发起第二次模型调用，也不重发图片。S09 的显式 **revise_prompt_and_retry** 是唯一的已拒绝／失败图片例外，仍通过既有新受理路径增加计数和槽位，不属于文本修复旁路。

### 1.4 兼容性与安全读取

- 公共错误码和既有客户端可见文案保持原样；新增原因只写 **DiagnosticEvent**（事件 **prompt_transition**，含 reason／action／ruleset_version／attempt／rule_ids，不含 reviewer 原文）。
- **build_workflow_view** 对 candidate artifact 仍只返回 id／kind／revision／status／digest，不返回 payload；未批准候选和 reviewer 原文不会泄露。
- **dispatch.available_actions** 在 run 为 **failed** 或 **blocked + REVIEW_UNAVAILABLE** 时只返回 S12 的 **continue_workflow**，不再同时暴露阶段动作（WG3-AC-10、原 t15 三条动作泄漏回归）。完整的新原因／动作响应契约留到 P4。

## 2. 验收覆盖（票面九项）

1. **类型化原因**：**test_typed_reason_taxonomy_covers_every_transition_class** 与 **test_typed_reasons_do_not_depend_on_error_copy** 覆盖缺信息、可修复编制、真实漂移、内容拒绝、缺证据、协议错误、技术故障、远端未知与授权失效；动作只依赖代码，修改错误文案不改变分类或授权。
2. **原意图修复前提**：**test_budget_is_reserved_before_model_and_repair_keeps_original_intent** 通过 HTTP 受理到 S07 连续执行验证原快照、意图、预算；**test_dependency_change_between_attempts_stops_before_second_call** 验证依赖变化；**test_disabled_account_between_attempts_blocks_before_second_model_call** 验证账号禁用；**test_unknown_image_submission_blocks_repair_before_another_model_call** 验证图片未提交证明。
3. **三候选与持久化扣占**：预算在模型 gate 未释放时即可读到 used == 1、artifacts 为空；**test_three_invalid_text_candidates_exhaust_budget_without_image_retry** 断言三次无效后 used == 3、第四次动作 409、图片提交为零。
4. **不新增受理计数**：所有修复场景保持一个 intent、一个 usage、三个未释放 slot；**test_budget_is_reserved_before_model_and_repair_keeps_original_intent** 还断言仅一次 visual submit；失败不退回 accepted 计数，槽位只在真实终态释放。
5. **真实漂移隔离**：**test_true_drift_still_abandons_intent_without_prompt_candidate** 使用未确认卖点触发 **PROMPT_REQUIRES_RECONFIRMATION**，无 prompt artifact、intent 为 abandoned、槽位释放、无图片提交；reviewer reject 不进入自动修复。
6. **兼容读取**：T07 HTTP 测试只读取 workflow view 的公开字段；候选 artifact 的 payload 为 null，诊断只含类型和规则 ID，不含审核员原文；既有错误码保持。
7. **连续 HTTP 执行断言**：T07 从 confirm_requirements 受理开始，经过 S07 编制、候选审查、S08 图片提交，直接读取临时 SQLite 的 **WorkflowRun / Artifact / RevisionBudget / DiagnosticEvent / GenerationIntent / GenerationUsage / GenerationSlot**；覆盖原快照、意图、候选预算、受理计数和图片提交次数。
8. **WG3-AC-07/08 持久化证据**：预算扣占、候选尝试、auto_repair、批准均写 **DiagnosticEvent**；扣占在模型调用前可见，恢复／刷新不会重置预算；格式映射错误不再要求用户改需求，超时／未知不自动重发图片。重放测试 **test_s07_recovery_replays_same_candidate_with_same_catalogue** 另断言同一 persisted candidate 和 **RevisionBudget.used == 1** 在 S12 恢复前后不变。
9. **测试位置与取证**：新增测试在 **backend/tests/test_v3_p1_t07_text_repair.py**，验收文档在 **docs/acceptance/**；没有测试脚手架、临时文件或证据写入 **main/** 业务目录。

## 3. 验证记录

以下命令均在 **D:\Desktop\新建文件夹\backend** 使用 **.venv\Scripts\python.exe**；环境为临时 SQLite、stub 适配器、无网络。

| 命令／范围 | 结果 |
| --- | --- |
| **ruff check --ignore E501**（本票变更文件） | **All checks passed!** |
| **pytest tests/test_v3_p1_t07_text_repair.py -q** | **8 passed in 7.61s** |
| P1 全套 + t15：p0/t87/t02/t03/t04/t05/t06/t07/t15 | **100 passed, 8 xfailed in 84.54s** |
| 提示词／漂移／恢复：t40/t66/t87/t03/t04/t15/t17 | **100 passed, 7 xfailed in 178.29s** |
| R2：t65/t66/t67/t68/t69/t70 | **46 passed in 76.41s** |
| 相邻资源／历史：t12/t13/t16/t44/t49/t59/t61/t75 | **78 passed in 103.76s** |
| 基础／适配器分块：t01/t02/t03/t04/t05/t06/t07/t08/t09/t10/t11/t15/t20 + adapter | **183 passed, 2 failed in 219.48s** |
| 安全／运维分块：t54/t55/t56/t57/t58/t60/t62/t63/t64/t71/t78/t79/t80/t81/t82/t83 | **143 passed, 1 failed in 127.15s** |
| v2 兼容分块：v2 t01/t02/t05/t06/t08/t09/t11/t20/t37/t48 | **122 passed, 2 failed in 172.68s** |
| 全量 **pytest -q -rxX** | 达到 600000ms 执行上限，收集到 **762** 项，未得到单次全量汇总；改用上述互有重叠的分块完成覆盖 |

### 3.1 调用／计数对照（计数型 stub）

| 场景 | generation_engineer | output_reviewer | prompt artifact | 预算 used | 受理 | 图片提交 |
| --- | --- | --- | --- | --- | --- | --- |
| 正常有效候选 | 1 | 1 | 1 approved | 1 | 1 intent / 1 usage / 3 slots | 1 |
| 一次覆盖映射修复后通过 | 2 | 1 | 1 approved（revision 2） | 2 | 不变 | 1 |
| 三次均无效 | 3 | 0 | 0 | 3 | 1 intent accepted，slots 未释放 | 0 |
| 真实漂移（未确认卖点） | 1 | 0 | 0 | 1 | intent abandoned，slots 释放 | 0 |
| 图片提交事实未知 | 1（gated） | 0 | 0 | 1 | 保持 accepted，slots 未释放 | 0 |
| 依赖 revision 变化 | 1 | 0 | 0 | 1 | 保持 | 0 |
| 账号禁用 | 1 | 0 | 0 | 1 | 保持 | 0 |

**说明**：计数型 stub 的 model_text 为 10 次（T07 八项测试的生产／恢复 probe 合计）；model_vision=0；image_generation=0；没有真实 provider 网络调用。预算扣占在模型 gate 前即可见，是持久化事实而不是事后统计。

## 4. 已知红色与残余未知

### 4.1 已知红色（不作为本票通过证据）

- **tests/test_t11_retrieve.py::test_failed_download_can_retrieve_same_version_and_downloads_real_file** 与 **::test_retrieval_stays_failed_while_waiting_and_blocks_other_write**：既有 retrieval 路径，隔离运行仍因 **RESULT_FETCH_FAILED** 失败。
- **tests/test_v2_t48_retrieval.py::test_retrieve_failed_result_updates_same_generation_after_gates** 与 **::test_g06_unknown_keeps_retrieved_asset_quarantined**：同一 **RESULT_FETCH_FAILED**，隔离运行仍失败，属于既有 retrieval 基线，不是 S07 修复回归。
- **tests/test_t83_retention_secrets.py::test_secret_markers_stay_out_of_api_database_filenames_and_backup**：仓库已有非空 **main/backend/data/app.db**，测试发现多个非空候选数据源而报 **SOURCE_CHOICE_REQUIRED**；隔离运行仍失败，属于环境／既有数据根状态，不是本票改动。
- P1/P2 的 **xfail** 仍为 #95／#96／#97 及历史 #90 标签，本票未解除；其中数字／数量假通过由 #95 负责。

### 4.2 残余未知

1. **全量单次执行**受 600000ms 工具上限限制，未生成一次性的 762 项全量汇总；已用分块覆盖所有测试文件，但分块彼此有重叠，不能伪造“全量通过”。
2. **模型调用计数**来自计数型 LLMStub，不代表真实 provider 的 token／延迟／费用；真实网络未接入。
3. **未提交证明**依赖最新 **GenerationJob** 的 submit_attempted／status／remote_task_id；历史遗留行没有 job 时按未提交处理，真正的远端核实由 #94/T08 扩展到恢复／并发场景。
4. **进程崩溃窗口**：已覆盖“预算已扣占、模型尚未返回”的可见状态，但未做进程级 crash／并发恢复测试；#94 负责真实崩溃和并发恢复。
5. **S12 的 S07 review 恢复**继续复用持久化 review request 并保持预算不重置；它不重新执行 producer，也不产生新的自动修复尝试；完整的原因／动作响应契约由 P4 处理。
6. **reviewer revise** 只记录 typed reason，不自动循环抽样；用户显式 revise 的旧行为保留，后续是否统一为响应契约由 P4 决定。

## 5. P2 入口

下一票 **#94/T08**：恢复不重置预算、不重发图片，覆盖正常失败、S12、重启和并发恢复共用同一持久化候选预算；随后 **#95/T09** 多句覆盖与准确语义防误判。P3 的 #96–#98 仍不因本票文档或历史通过记录而视为完成。
