# P1 / T05 — 图像质检、交付与历史复核统一裁决（#91）

- 票：#91（T05，P1）；父问题 #86；批次 P1（接 #88 规则目录、#89 输入/需求审查、#90 创意/视觉/需求卡）
- 集成分支：integration/v3；交付起点 **f3f5262**（#90 合并后）；规则集版本 **RULESET_VERSION = "v3.rules.1"**；提示词版本 **2.0.0**（未变更）
- 环境：隔离临时 SQLite、计数型适配器替身、网络全部 stub；无真实数据根、无部署配置改动
- 证据：**docs/acceptance/evidence/p1-t05.json**；测试：**backend/tests/test_v3_p1_t05_image_delivery.py**（10 项）以及既有 **test_v2_t12_quality.py**、**test_v2_t13_delivery.py**、**test_v2_t49_legacy_review.py**、**test_t75_delivery_history.py**

## 1. 交付内容

1. **S09 图像质检与图像批准**：删除按正则授予 advisory 身份的 **_ADVISORY_REVIEW_RULE** 与宽泛的 **_REVIEW_RULE_IDS**；改为服务端目录 **review:quality_report**（报告目标）与每张图像目标各自的 blocking 规则表。图像目录逐条保留 **hard:subject / hard:product_consistency / hard:quantity / hard:text_logo / hard:forbidden / hard:composition / hard:size_format** 的证据类型与适用性；商品参考图缺失时 **hard:product_consistency** 以服务端理由标为不适用。报告与图像的目标结果由 **review_results** 精确匹配，报告和图像分别裁决，互不替代。
2. **S10 交付清单**：新增 **_MANIFEST_RULESET**，blocking 仅为 **G07_MANIFEST_ASSET / G07_MANIFEST_REQUIREMENT / G07_MANIFEST_EVIDENCE / G07_MANIFEST_FILENAME / G07_MANIFEST_CAPTION**；其余已登记 G07 语义项只作 advisory，不再用 **startswith("G07_")** 放行任意伪造规则。模型调用失败仍是唯一 **503 REVIEW_UNAVAILABLE**；契约缺陷为 **409 REVIEW_INVALID**，合法拒绝为 **409 REVIEW_REJECTED**。
3. **S11 历史与旧数据复核**：文本、提示词、质检报告、图像与交付清单五类历史目标都通过 **_review_ruleset(target_kind, required_rules)** 构造显式目录；**LEGACY_TEXT_RULES / LEGACY_PROMPT_RULES / LEGACY_REPORT_RULES / LEGACY_DELIVERY_RULES** 原样保留为各自的 blocking 清单，不因统一目录而放宽。每个历史目标仍按原始生成快照解释，同时校验当前身份、项目归属、撤销、过期和文件可访问性。
4. **恢复路径**：S09 的 S12 恢复重放持久化审查请求后复用同一 **_apply_review**，因此与正常路径使用同一目录和同一证据规则；S11 遗留复核本身可重入，未知规则等协议缺陷保持条目隔离，不产生批准。S10 的交付准备在模型失败时仍走技术故障口径，可重新发起，不写批准。
5. **审查输入与审计**：S09/S10/S11 的审查输入都携带 **ruleset_version** 和 **rules** 描述；**ArtifactReview.checks_json**、质检报告批准、图像批准、交付清单批准与历史批准记录的 **checks** 都写入 **ruleset_version**，规则版本可追溯。
6. **资源字节出口**：报告可被批准同时如实描述图像失败/未知，但图像失败或未知时资产保持隔离，**GET /api/assets/{id}** 与 **?download=true** 都不返回字节；交付清单通过也不等于已发布，发布仍由交付校验和原子发布步骤控制。

## 2. 统一口径

| 情形 | 结果 |
| --- | --- |
| blocking 全 pass，advisory 为 pass/fail/unknown/not_applicable | 放行；advisory 仅记录，不改变结论 |
| blocking 缺结果 / 适用规则被标 not_applicable / 缺证据 | 协议错误（MISSING_RESULT / CONTRADICTION / ILLEGAL_EVIDENCE）→ 409 REVIEW_INVALID |
| 目录外规则、伪造 review:* 规则 | UNKNOWN_RULE → 409 REVIEW_INVALID |
| 证据不在 allowed_evidence_ids 或证据类型不匹配 | ILLEGAL_EVIDENCE → 409 REVIEW_INVALID |
| 同一 (rule_id, instance) 重复 | DUPLICATE_INSTANCE → 409 REVIEW_INVALID |
| 结果目标不在本次调用内或重复声明目标 | WRONG_TARGET → 409 REVIEW_INVALID |
| blocking 有 fail 但声明 pass，或全 pass 却声明 revise/reject | CONTRADICTION → 409 REVIEW_INVALID |
| 合法拒绝（审查结论 revise/reject） | 409 REVIEW_REJECTED，进入隔离/用户处理，不作为服务故障 |
| 审查模型调用失败 | 503 REVIEW_UNAVAILABLE（技术故障唯一口径） |
| 生产者自身字段/证据契约不合法 | 502 CONTRACT_ERROR（不冒充审查协议缺陷） |

## 3. 验收覆盖

- **WG3-AC-02**：blocking 全 pass 时 advisory 任意结论不改变放行——单元 **test_review_results_requires_each_expected_target_exactly_once**、**test_s09_rule_sets_separate_report_image_and_advisories**、**test_s10_and_s11_catalogues_keep_registered_legacy_rules**；HTTP 质检报告通过而图像失败/未知的两个场景保持报告批准但不产生图像批准。
- **WG3-AC-03**：目录外规则、非法证据、错目标、矛盾声明、重复实例都是键入式 **409 REVIEW_INVALID**，不是 5xx——**test_s09_duplicate_report_rule_is_a_typed_protocol_error**、**test_manifest_duplicate_rule_is_a_typed_protocol_error**、**test_legacy_unknown_rule_stays_isolated_and_typed**；既有 **test_v2_t12_quality.py** 的缺规则/未知规则/重复报告规则与 **test_v2_t13_delivery.py** 的重复清单规则均已改为 409。
- **WG3-AC-12**：报告通过但图像失败/未知时无预览、无缩略图、无下载、无提前发布——**test_report_pass_with_image_failure_never_exposes_bytes**、**test_report_pass_with_image_unknown_never_exposes_bytes**；交付拒绝不是服务故障——**test_manifest_rejection_is_not_reported_as_an_outage**；历史目标按原快照隔离——**test_legacy_unknown_rule_stays_isolated_and_typed**；发布后资源字节按身份/归属校验，普通用户 A 可读自己的已发布图像，普通用户 B 与管理员均被拒绝且不泄漏字节——**test_published_image_identity_matrix_blocks_other_users_and_admin**。
- **职责独立**：S09 的报告审查、图像审查与 S10 交付清单审查仍是三个独立目标和独立结论；报告批准不能替代图像批准，交付清单通过不能替代发布校验。

## 4. 未迁移范围（明确保留）

- S06 最终需求卡的字段合成、强度/软硬分层与不可变执行快照属于 P3（#96–#98）；本票不改写历史 payload、不重新解释既有约束。
- **dispatch.available_actions** 在 failed 状态下仍并列阶段动作与 continue_workflow（#93 统一收口）。
- S11 的旧生产者字段校验（_validate_quality/_validate_delivery）保留为兼容层；历史目标只迁移“审查裁决”，不改变旧数据语义。

## 5. 验证记录

    cd backend
    ..venvScriptspython.exe -m pytest tests	est_v3_p1_t05_image_delivery.py -q      → 10 passed
    ..venvScriptspython.exe -m pytest tests	est_v2_t12_quality.py tests	est_v2_t13_delivery.py -q → 38 passed
    ..venvScriptspython.exe -m pytest tests	est_v2_t49_legacy_review.py -q          → 9 passed
    ..venvScriptspython.exe -m pytest tests	est_t75_delivery_history.py -q          → 1 passed
    ..venvScriptspython.exe -m pytest tests	est_v3_p0_baseline.py tests	est_v3_p1_t02_adjudication.py -q → 25 passed, 1 xfailed
    ..venvScriptspython.exe -m pytest tests	est_v3_p1_t03_review_catalogue.py tests	est_v3_p1_t04_production_reviews.py -q → 35 passed
    ..venvScriptspython.exe -m pytest tests	est_t11_retrieve.py tests	est_t75_delivery_history.py -q → 2 failed（已知基线红）, 9 passed

**已知基线红**（非本票引入）：**tests/test_t11_retrieve.py::test_failed_download_can_retrieve_same_version_and_downloads_real_file**、**tests/test_t11_retrieve.py::test_retrieval_stays_failed_while_waiting_and_blocks_other_write**。

**静态检查**：ruff --select E,F,I,UP,B,ASYNC,SIM 对本次改动文件全部通过。
