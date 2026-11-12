# P1 / T06 — 审查消费者收口与受影响出口回归（#92）

- 票：#92（T06，P1）；父问题 #86；批次 P1（接 #88 规则目录、#89 输入/需求审查、#90 创意/视觉/需求卡、#91 图像/交付/历史）
- 集成分支：integration/v3；运行基线 **e160f2e**（#91 合并后）；本票提交 **9c9eb42**（提交前以工作树运行），代码变更仅见第 1 节
- 规则目录版本：**RULESET_VERSION = "v3.rules.1"**；提示词版本 **2.0.0**（未变更）
- 环境：隔离临时 SQLite、计数型 **LLMStub**、数据根为临时目录、网络全部 stub；无真实 provider、无真实图片生成
- 证据：**docs/acceptance/evidence/p1-t06.json**；新增测试：**backend/tests/test_v3_p1_t06_gate.py**（5 项）；关联测试：**test_v3_p1_t02_adjudication.py**、**test_v3_p1_t03_review_catalogue.py**、**test_v3_p1_t04_production_reviews.py**、**test_v3_p1_t05_image_delivery.py**、**test_v2_t13_delivery.py**、**test_v2_t44_versions.py**、**test_v2_t49_legacy_review.py**、**test_t59_isolation.py**、**test_t61_history_recovery_gate.py**、**test_t75_delivery_history.py**

## 1. 结论与交付内容

本票完成 P1 的消费者收口，不新增审查语义，也不改写旧快照。P0 第 4 节列出的每个正常、S12、历史、交付审查入口，要么已迁移到统一目录并保留原规则证据类型，要么明确标为历史兼容入口；没有遗漏入口，也没有用旧文档中的通过记录替代本票运行结果。

### 1.1 消费者矩阵

| 阶段 | P0 消费者入口 | 统一目录 / 裁决 | 正常入口 | S12 / 历史入口 | 结论 |
| --- | --- | --- | --- | --- | --- |
| S01 | **submit_request**、**answer_intent_clarification** | **G01_SCOPE / G01_ACTION** | 共享裁决 | **s12._resume_s01_review** | 已迁移（#89） |
| S02 | **analyze_reference**、**confirm_reference_usage** | **G02_IMAGE_VISIBLE / G02_OBSERVATION_GROUNDED / G02_TEXT_IS_DATA / G02_NO_UNSUPPORTED_INFERENCE** | 共享裁决 | **s12._resume_s02_review** | 已迁移（#89） |
| S03 | **compose_brief**、**answer_requirements_clarification**、**revise_brief_draft** | **G03_STRUCTURE / G03_SUBJECT_PURPOSE / G03_SOURCE_BASIS / G03_TEXTS / G03_CONSTRAINTS / G03_SIZE / G03_UNRESOLVED** | 共享裁决 | **s12._resume_s03_review** | 已迁移（#89） |
| S04 | **plan_creative**、**revise_creative** | **G03_SOURCE / G03_COPY / G03_REQUIREMENTS / G03_SINGLE_DIRECTION** | 共享裁决 | **s12._resume_s04_review** | 已迁移（#90） |
| S05 | **plan_visual**、**revise_visual**、**finalize_requirements** | 8 条 **S05_*** 视觉规则；**finalize_requirements** 使用 10 条 **S06_*** 卡片规则 | 共享裁决 | **s12._resume_s05_review** | 已迁移（#90） |
| S06 | **confirm_requirements**、**revise_requirements**（最终卡片生成） | 最终卡片生成不是 **output_reviewer** 校验器；无规则版本 | 卡片生成 | 不适用 | 明确排除；字段/强度由 #96/#97 处理 |
| S07 | **compile_prompt**、**revise_prompt** | **G03_CONFIRMATION / G05_EXECUTION_BOUNDARY**，可选 advisory 只记录 | 共享裁决 | **s12._resume_s07_review**（#88） | 已迁移（#88） |
| S08 | **submit_generation**、**verify_generation**、**retry_generation**、**recover_generation**、**close_generation_tracking**、**start_new_generation** | 生成阶段，不调用 **output_reviewer** | 生成/恢复 | 不适用 | 明确排除；不是 P0 审查消费者 |
| S09 | **review_candidate**、**recheck_candidate**、**retry_rejected_generation**、**revise_prompt_and_retry** | **review:quality_report** 报告目标 + 每张图像 7 条 `hard:*` 规则 | 共享裁决 | **s12._resume_s09_review** | 已迁移（#91） |
| S10 | **prepare_delivery**、**approve_delivery**、**publish_delivery**、**request_new_version** | **G07_MANIFEST_ASSET / G07_MANIFEST_REQUIREMENT / G07_MANIFEST_EVIDENCE / G07_MANIFEST_FILENAME / G07_MANIFEST_CAPTION** | **s10._prepare** | **s12._resume_s10_review**（本票新增）；**request_new_version** 的历史复制见 1.3 | 正常与恢复已迁移（#91/#92）；历史复制明确兼容 |
| S11 | **retrieve_original_result**、**review_legacy_item** | **LEGACY_TEXT_RULES / LEGACY_PROMPT_RULES / LEGACY_REPORT_RULES / LEGACY_DELIVERY_RULES** + 动态图像规则 | **s11.review_legacy_item** | 无 **s12._resume_s11_review**；复核动作本身可重入 | 历史兼容（#91） |
| S12 | **continue_workflow**、**recover_submission**、**resume_after_restart**、**cleanup_expired** | 复用 S01、S02、S03、S04、S05、S07、S09、S10 的目录与裁决；不消费 S11 | **s12._resume_review / _resume_producer** | 自身即恢复入口 | 已迁移（#92） |

**S10 历史复制路径**：**request_new_version** 起始新版本时会调用 **s10._copy_reference_report**（**main/backend/app/workflow/stages/s10.py:767**）。该函数只复制同一 **reference_asset_id** 的既有已批准参考报告，写入 **carried_from_report / carried_from_run** 追溯字段，不调用模型、不生成新的审查结论；已有 **backend/tests/test_v2_t44_versions.py::test_new_version_carries_reference_file_and_previous_approved_brief** 验证复制结果与后续 approved_inputs。它是历史兼容入口，不是新的 5 条 G07 交付审查消费者。

### 1.2 分支处理：没有为了“收口”而误删有效行为

- #89—#91 已删除各阶段按正则授予 advisory 身份的旧分支（**_ADVISORY_RULE_RE**、**_ADVISORY_REVIEW_RULE**、**startswith("G07_")** 等）；本票复核代码中不再存在这些旁路。
- 各阶段仍保留 **stage-specific _validate_review** 包装，因为它们还负责目标 URL/证据选择、producer 契约校验、**add_review**、阶段状态和修复动作；它们不是纯转发层，删除会丢失行为，因此本票不删除。
- **s10._accept_manifest_review**（**main/backend/app/workflow/stages/s10.py:296**）只是正常与恢复共用的“落库副作用”函数，不是新的审查层：两条路径仍各自完成目标定位、审批/文件校验和 **s10._validate_review**，再共用同一套 **add_review / add_approval / set_stage**。
- **S11** 没有延迟到 S12 的审查：**review_legacy_item** 是正常历史动作，重复调用保持条目隔离和幂等（**test_v2_t49_legacy_review.py::test_repeated_review_is_idempotent_and_restart_reuses_approval**）。因此没有为了形式统一而添加空的 **_resume_s11_review**。

### 1.3 新增 S10 恢复分支

**main/backend/app/workflow/stages/s12.py:1026** 的 **_resume_s10_review** 处理第一次 **prepare_delivery** 在 reviewer 调用失败后留下的持久化候选：

1. 从 checkpoint 的原始请求解析 **review_targets[0].target_id**，要求形如 **delivery_manifest:<artifact-id>**；只接受项目/运行范围内、仍为最新且未批准的 **delivery_manifest** 候选。
2. 重新加载同一 **GenerationJob / Asset**，要求目标中的 **asset_id** 与 **requirement_revision** 仍匹配当前运行；先执行 **s10._require_image_approval** 与 **s10._verify_image_file**，审批撤销、摘要不符、文件被篡改都在 reviewer 调用前失败。
3. 用保存的 **request_raw**、同一 **allowed_evidence_ids**（并补入目标 ID）、同一图像字节和 **capability="TEXT"** 重放 **output_reviewer**，不重新调用 **delivery_manager**，不新建 artifact。
4. 用 **s10._validate_review** 复核后，重新检查项目 revision / requirement revision；若期间变化则把候选标为 superseded，不写批准。
5. 通过后调用 **s10._accept_manifest_review**，写入与正常路径相同的 **ArtifactReview**、**delivery_manifest** 批准（含 **asset_id / asset_digest / target_id / requirement_revision / ruleset_version**）、模型指纹和 **DELIVERY_MANIFEST_READY** 阶段状态。

**main/backend/app/workflow/stages/s10.py:296** 的 **_accept_manifest_review** 同时被 **s10._prepare** 和 **s12._resume_s10_review** 调用，保证正常/恢复的审查记录和批准字段一致。**backend/tests/test_v3_p1_t06_gate.py::test_s10_recovery_replays_persisted_manifest_without_new_producer** 断言恢复前后的 **artifact id** 完全相同，角色调用严格为 **delivery_manager, output_reviewer, output_reviewer**；**test_s10_recovery_invalidates_approval_before_reviewer_retry** 断言撤销图像批准后继续动作在 reviewer 调用前返回 409 **IMAGE_NOT_APPROVED**。

### 1.4 正常与恢复的一致裁决

- 正常与 S12 恢复使用同一 **RuleSet**、同一 **RULESET_VERSION**、同一目标 ID 和同一证据集合；S10 的恢复请求直接复用持久化的 **request_raw**，不会重新构造更宽松的目录。
- **test_every_registered_review_catalogue_uses_one_version** 对 S01、S02、S03、S04、S05 视觉/卡片、S07、S09 报告/图像、S10、S11 四类历史目标逐一断言版本与 blocking 规则集合；S10 的批准 **checks** 和 **ArtifactReview.checks_json** 都写入 **ruleset_version**，可追溯到 **v3.rules.1**。
- 旧审批/旧快照不被迁移改写：S10 交付使用 **GenerationJob.confirmed_snapshot_digest** 对应的不可变快照；S10 历史复制保留来源报告 ID；S11 复核仍按原始生成快照解释。

## 2. 统一口径

| 情形 | 结果 |
| --- | --- |
| blocking 全 pass，advisory 为 pass/fail/unknown/not_applicable | 放行；advisory 只记录，不改变结论 |
| blocking 全 not_applicable、缺失、空证据或 unknown | 协议错误（**CONTRADICTION / MISSING_RESULT / ILLEGAL_EVIDENCE**）→ **409 REVIEW_INVALID** |
| 目录外规则或伪造 `review:*` 规则 | **UNKNOWN_RULE** → **409 REVIEW_INVALID** |
| 证据不在 allowed_evidence_ids 或证据类型不匹配 | **ILLEGAL_EVIDENCE** → **409 REVIEW_INVALID** |
| 同一 **(rule_id, instance)** 重复 | **DUPLICATE_INSTANCE** → **409 REVIEW_INVALID** |
| 结果目标不在本次调用内或重复声明目标 | **WRONG_TARGET** → **409 REVIEW_INVALID** |
| blocking 失败却声明 pass，或全 pass 却声明 revise/reject | **CONTRADICTION** → **409 REVIEW_INVALID** |
| 合法拒绝（revise/reject） | **409 REVIEW_REJECTED**，进入隔离/用户处理 |
| 审查模型调用失败 | 只有 **503 REVIEW_UNAVAILABLE** |
| producer 自身字段/证据契约不合法 | **502 CONTRACT_ERROR**，不冒充审查协议缺陷 |
| 图像审批撤销/摘要变化/文件变化 | 在 reviewer 调用前 fail closed（**IMAGE_NOT_APPROVED / ASSET_UNAVAILABLE / DIGEST_MISMATCH**） |

## 3. 验收覆盖

### 3.1 票面八项

1. **全部 P0 消费者有归属**：1.1 矩阵覆盖 S01—S12 的正常、S12、历史与交付入口；S06/S08 明确为非审查消费者，未用“未列出”代替排除。T06 的 **test_s12_review_recovery_covers_every_artifact_consumer** 锁定 **s12._ARTIFACT_KIND_BY_STAGE** 与 S01/S02/S03/S04/S05/S07/S09/S10 恢复函数，并断言 S11 的正常历史动作存在。
2. **不误删分支、不加纯转发层、不降证据要求**：1.2 记录保留理由；旧正则 advisory 旁路已无残留；S10 共用函数只复用副作用，不跳过审查；S11 无空恢复分支。各目录 blocking 集合在 **test_every_registered_review_catalogue_uses_one_version** 中逐项精确匹配。
3. **WG3-AC-01/02/03/12 的正反例**：见 3.2；S07 waiver（全 N/A）、四种 advisory 结果、未知规则、重复实例、错目标、非法证据、审批失效均有测试。
4. **受影响 HTTP/历史/资源字节出口的 R1 身份→归属→内容矩阵**：**test_t59_isolation.py** 的 owner/list/subobject/forged role/unowned/anonymous 五项与 **test_t61_history_recovery_gate.py** 的私路由认证、跨账号历史/资产元数据、未批准状态字节关闭、reference digest/static/locator 四项全部通过；S10 恢复测试还断言发布前 **GET /api/assets/{id}** 返回 409 且不含字节，发布后 **?download=true** 返回逐字节相同内容。**test_v3_p1_t05_image_delivery.py::test_published_image_identity_matrix_blocks_other_users_and_admin** 覆盖他人和管理员不能读取他人已发布图像。
5. **正常/恢复同一目标一致裁决、版本可追溯、旧版本不被改写**：见 1.4；T06 S10 测试断言同一 manifest artifact id 和同一 approval 结果；**test_every_registered_review_catalogue_uses_one_version** 断言所有目录版本；S10 批准 checks 含 **ruleset_version**；**test_v2_t44_versions.py::test_new_version_carries_reference_file_and_previous_approved_brief** 断言旧 run 保持 completed、旧报告按来源复制而不是重写。
6. **R1 门禁在 P2 前无未解决失败/unknown**：本轮 **test_t59_isolation.py + test_t61_history_recovery_gate.py = 9 passed**；没有 R1 失败或 unknown。其他历史阶段只以受影响范围的现有测试作为证据（t13/t44/t49/t75），没有把旧阶段的旧绿灯当作当前通过记录。
7. **基线、命令、模型/图像调用、审批/资源结果、残余未知与 P2 入口**：记录在本文件第 4、5 节与 **p1-t06.json**；真实模型/图像调用为 0，计数 stub 调用只用于行为断言；审批与资源结果见 1.3/3.1 第 4 项。
8. **测试/临时文件/证据不进入业务目录**：新增测试只在 **backend/tests**，新增证据只在 **docs/acceptance**；**main/** 仅包含本票必要的 **s10.py / s12.py** 应用代码，没有把测试脚手架或证据放进业务目录。

### 3.2 WG3-AC 对照

- **WG3-AC-01（S07 全 N/A／空证据／缺必需项拒绝，完整 pass 放行）**：**test_v3_p1_t02_adjudication.py::test_all_not_applicable_blocking_rules_cannot_open_the_gate**、**test_s07_all_not_applicable_is_rejected_without_approval_or_image**、**test_v3_p1_t03_review_catalogue.py::test_a_blocking_rule_may_not_be_waived_by_the_reviewer**、**test_v3_p1_t04_production_reviews.py::test_blocking_rule_cannot_be_waived_as_not_applicable**、**test_t87_gate_regressions.py::test_s07_rejects_all_not_applicable_prompt_review**；正例 **test_v2_t40_prompt_review.py::test_compile_prompt_builds_only_confirmed_candidate_and_gets_independent_review**。
- **WG3-AC-02（已登记 advisory 的四种结果不改变有效门禁，正常及 S12）**：**test_advisory_results_never_change_a_satisfied_blocking_gate** 覆盖 pass/fail/unknown/not_applicable；**test_s07_advisory_failure_does_not_block_an_approved_prompt** 和 **test_s07_recovery_replays_same_candidate_with_same_catalogue** 在 S07 正常/恢复均放入四种 advisory；**test_v3_p1_t03_review_catalogue.py::test_advisory_findings_never_change_a_satisfied_gate**、**test_v3_p1_t04_production_reviews.py::test_advisory_unknown_never_changes_a_satisfied_gate**、**test_v3_p1_t05_image_delivery.py::test_s09_rule_sets_separate_report_image_and_advisories** 覆盖其他阶段。
- **WG3-AC-03（伪造规则、非法来源、矛盾裁决不放行，真实失败不被 advisory 吞掉）**：**test_unknown_rule_is_a_protocol_issue_not_a_gate_pass**、**test_evidence_must_come_from_the_allowed_set_and_type**、**test_failing_rule_without_evidence_is_unproven**、**test_wrong_target_is_rejected**、**test_declared_pass_cannot_hide_a_blocking_failure**、**test_advisory_pass_cannot_rescue_a_failing_blocking_rule**；t03/t04 的 unknown/duplicate/protocol 参数化；t05 的 **test_s09_duplicate_report_rule_is_a_typed_protocol_error**、**test_manifest_duplicate_rule_is_a_typed_protocol_error**、**test_legacy_unknown_rule_stays_isolated_and_typed**。
- **WG3-AC-12（报告 pass 但图片 fail/unknown 无字节；历史批准、规则变化、正常/恢复同一放行边界）**：**test_v3_p1_t05_image_delivery.py::test_report_pass_with_image_failure_never_exposes_bytes**、**test_report_pass_with_image_unknown_never_exposes_bytes**；**test_v2_t13_delivery.py::test_invalid_image_evidence_blocks_before_model_call**（missing_approval / incompatible_rules / wrong_requirement / wrong_digest / missing_file 五个变体）、**test_tampered_file_rule_change_and_revocation_fail_closed**、**test_revoked_delivery_cannot_be_reapproved_with_old_record**、**test_tampered_manifest_payload_fails_closed**；T06 的 S10 恢复两项测试；**test_t75_delivery_history.py::test_current_delivery_content_gate_and_feedback_preserve_history**。

## 4. 验证记录

以下均为本票运行时的实际命令与输出；解释器为 **backend/.venv/Scripts/python.exe**，工作目录为 **backend**。

### 4.1 静态检查和新增测试

```text
$ .venv\Scripts\python.exe -m ruff check tests/test_v3_p1_t06_gate.py ../main/backend/app/workflow/stages/s10.py ../main/backend/app/workflow/stages/s12.py
All checks passed!

$ .venv\Scripts\python.exe -m pytest tests/test_v3_p1_t06_gate.py -q
5 passed in 2.18s
```

### 4.2 P1 全量（含 xfail 原因）

```text
$ .venv\Scripts\python.exe -m pytest tests/test_v3_p0_baseline.py tests/test_t87_gate_regressions.py tests/test_v3_p1_t02_adjudication.py tests/test_v3_p1_t03_review_catalogue.py tests/test_v3_p1_t04_production_reviews.py tests/test_v3_p1_t05_image_delivery.py tests/test_v3_p1_t06_gate.py -q -rxX
79 passed, 9 xfailed in 45.67s
```

9 个 xfail 是已登记的后续票，不是本票失败：**test_f4_creative_style_lighting_are_not_hard_requirements**（P3 #96）、**test_visual_gate_ignores_advisory_failure_when_blocking_checks_pass**（已由 #90 修复，保留历史断言）、**test_prompt_coverage_format_error_keeps_original_intent**（#93）、**test_soft_visual_preferences_are_not_hard_prompt_coverage_requirements[hard:creative/style/lighting]**（#97，三项）、**test_image_composition_rule_does_not_merge_soft_preferences**（#97）、**test_final_card_preserves_dedicated_visual_fields**（#96）、**test_numeric_quantity_change_is_not_false_passed**（#95）。

### 4.3 关联回归

```text
$ .venv\Scripts\python.exe -m pytest tests/test_v2_t13_delivery.py tests/test_v2_t44_versions.py tests/test_v2_t49_legacy_review.py -q
41 passed in 46.74s

$ .venv\Scripts\python.exe -m pytest tests/test_t59_isolation.py tests/test_t61_history_recovery_gate.py -q
9 passed in 12.04s

$ .venv\Scripts\python.exe -m pytest tests/test_t75_delivery_history.py -q
1 passed in 2.17s

$ .venv\Scripts\python.exe -m pytest tests/test_v2_t40_prompt_review.py::test_compile_prompt_builds_only_confirmed_candidate_and_gets_independent_review -q
1 passed in 1.21s
```

### 4.4 已记录的基线红灯（不冒充通过）

本票相关回归集 **test_t59_isolation.py + test_t61_history_recovery_gate.py + test_v2_t12_quality.py + test_v2_t13_delivery.py + test_v2_t15_recovery.py + test_v2_t17_restart_recovery.py + test_v2_t44_versions.py + test_v2_t49_legacy_review.py** 的实际结果为 **84 passed, 3 failed in 161.00s**。三项失败全部是 P0 已记录的 WG3-AC-10 动作泄漏，归 #93：

- **test_v2_t15_recovery.py::test_vision_producer_failure_resumes_with_original_image**
- **test_v2_t15_recovery.py::test_unconfigured_text_capability_stays_blocked_until_explicit_continue_after_fix**
- **test_v2_t15_recovery.py::test_unconfigured_vision_capability_blocks_until_fixed_and_resumes_original_request**

失败原因是 **available_actions** 同时返回阶段动作（**analyze_reference** / **submit_request**）和 **continue_workflow**，不是本票改动的 S10/S12 分支或裁决结果。P0 的其他基线红灯（t03/t04/t11 等）属于其他票或历史范围，本票未把它们作为当前通过证据。

## 5. 残余未知与 P2 入口

- **前端**：本票未改 UI，也未做浏览器/设备验收；#93 负责 action 可见性和前端错误文案。
- **真实 provider**：本轮全部是计数 stub；没有真实文本/视觉模型或图片生成调用，**model_text / model_vision / image_generation 的真实数值为 0**（stub 调用计数见 evidence JSON）。
- **S11**：没有专门的 S12 延迟分支是设计选择，不是遗漏；**review_legacy_item** 本身可重入，已有重复调用/重启复用批准测试。
- **与 #91 文档的关系**：**p1-t05-image-delivery-history.md** 中“S10 recovery 暂无专门 S12 分支”的残余未知记录的是 #91 当时状态；本票已补 **_resume_s10_review**，当前结论以本文件与 **p1-t06.json** 为准。
- **S10 历史复制**：**request_new_version** 的参考报告复制不重跑审查，保留来源字段；这是历史兼容路径，已有 t44 覆盖，未在本票扩大为新的模型审查。
- **P2 入口**：#93（WG3-AC-10 的失败态 action 泄漏与前端映射），随后 #94、#95；S10/S12 的 T06 收口以本文件和第 4 节输出为当前证据。

**结论**：T06 范围内 P0 正常/S12/历史/交付消费者已全部归类，S10 恢复路径已补齐并与正常路径共用同一裁决和批准副作用；受影响 R1 门禁 9 项全通过，无未解决的 R1 失败或 unknown，具备进入 P2 的条件。
