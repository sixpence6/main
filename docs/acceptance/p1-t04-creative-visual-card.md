# P1 / T04 — 创意、视觉与最终需求卡统一裁决（#90）

- 票：#90（T04，P1）；父问题 #86；批次 P1（接 #88 规则目录、#89 输入/需求审查）
- 集成分支：integration/v3；交付起点 `2d70095`（#89 合并后）；规则集版本 `RULESET_VERSION = "v3.rules.1"`；提示词版本 `2.0.0`（未变更）
- 环境：隔离临时 SQLite、计数型适配器替身、网络全部 stub；无真实数据根、无部署配置改动
- 证据：`docs/acceptance/evidence/p1-t04.json`；测试：`backend/tests/test_v3_p1_t04_production_reviews.py`（21 项）以及既有 `test_v2_t37_creative.py`、`test_v2_t08_visual_final_brief.py`、`test_v2_t09_confirmation.py`、`test_t04_brief.py`、`test_v2_t15_recovery.py`、`test_t68_recovery.py`、`test_v2_t17_restart_recovery.py`

## 1. 交付内容

1. `stages/s04.py`（创意方向）：删除 `_ADVISORY_RULE_RE` 正则；`RULESET = governance_ruleset("creative_direction", REQUIRED_RULES)`，其中 `REQUIRED_RULES = (G03_SOURCE, G03_COPY, G03_REQUIREMENTS, G03_SINGLE_DIRECTION)` 是唯一 blocking 清单，其余登记项一律 advisory；`_validate_review_shape` 改用 `review_output`，协议缺陷统一 `409 REVIEW_INVALID`，且把阶段写为 `blocked`（不是 `failed`）；模型调用失败仍是唯一的 `503 REVIEW_UNAVAILABLE`；审查输入增加 `ruleset_version` 与 `rules` 描述，审查记录 `checks_json` 保存 `ruleset_version`。
2. `stages/s05.py`（视觉方案 + 最终需求卡）：删除 `_ADVISORY_RULE_RE`；`RULESET_VISUAL = governance_ruleset("visual_plan", REQUIRED_VISUAL_RULES, evidence_types=("artifact","reference"))`、`RULESET_CARD = governance_ruleset("final_card", REQUIRED_CARD_RULES, evidence_types=("artifact","reference"))`，由 `_ruleset_for(required_rules)` 按目标选择；`_validate_review` 改用 `review_output`；`_review_artifact` 对 `REVIEW_INVALID/REVIEW_REJECTED` 写 `blocked` 专用 issue 而不是服务故障；审查输入与 `add_review` 记录都带 `ruleset_version`。
3. `stages/s12.py`：`_resume_s04_review`、`_resume_s05_review` 复用同一套 `_validate_review` 与同一 `RuleSet`（从持久化审查请求重建），协议缺陷走 `blocked REVIEW_INVALID/REVIEW_REJECTED`，只有真实调用故障才 `REVIEW_UNAVAILABLE`；恢复时重放已持久化的审查请求，不重新生成候选。
4. 审查提示词与契约：`output_reviewer` 的增量说明明确“程序给出的 rules 是唯一规则清单；blocking 逐条给 outcome 与证据；advisory 报告但不改变放行；清单外规则不可用；不得用 not_applicable 规避 blocking”；`Check` 支持 `instance` 以表达合法的同规则多实例核验。

## 2. 统一口径（与 #88 目录语义一致）

| 情形 | 结果 |
| --- | --- |
| blocking 规则缺结果 / 全 `not_applicable` / 空证据 | 协议错误（`MISSING_RESULT` / `CONTRADICTION` / `ILLEGAL_EVIDENCE`）→ `409 REVIEW_INVALID`，候选不可见 |
| 结果引用目录外规则（含阶段性伪规则 `S05_EXTRA_RULE`） | `UNKNOWN_RULE` → `409 REVIEW_INVALID` |
| 同一 `(rule_id, instance)` 重复 | `DUPLICATE_INSTANCE` → `409 REVIEW_INVALID` |
| blocking 全 pass + advisory `pass/fail/unknown/not_applicable` | 通过；advisory 仅记录 |
| blocking 有 fail 但声明 `pass` | `CONTRADICTION` → `409 REVIEW_INVALID` |
| blocking 有 fail + 声明 `revise/reject/unknown` | 走各自的合法修复/拒绝路径，不自动重试 |
| 审查模型调用失败 | `503 REVIEW_UNAVAILABLE`（技术故障唯一口径） |

## 3. 验收覆盖

- **WG3-AC-02**：blocking 全 pass 时 advisory 任意结论不改变放行——单元 `test_advisory_unknown_never_changes_a_satisfied_gate`、`test_advisory_pass_cannot_rescue_a_blocking_failure`；HTTP `test_s04_advisory_failure_does_not_block`、`test_s05_advisory_failure_does_not_block`（均进下一阶段、`visual.submit_calls == []`）。
- **WG3-AC-03**：目录外规则、非法证据、错目标、矛盾声明、重复实例在 S04/S05/S06 卡片全部是键入式 `409 REVIEW_INVALID`——单元 `test_rule_outside_the_catalogue_is_a_protocol_issue`、`test_duplicate_rule_instance_is_a_protocol_issue`、`test_blocking_rule_cannot_be_waived_as_not_applicable`；HTTP `test_s04_protocol_defects_never_approve`（missing/duplicate/not_applicable/unknown_rule）、`test_s05_protocol_defects_never_approve`（同四类）、`test_s06_card_protocol_defect_keeps_card_unconfirmed`、既有 `test_v2_t37_creative.py::test_review_with_forged_evidence_never_approves`、`test_v2_t08_visual_final_brief.py::test_review_missing_duplicate_foreign_checks_never_approve`。
- **WG3-AC-12**：失败审查不泄漏候选正文（`payload is None`）、不进入下一阶段、零图像调用；恢复只重放持久化审查，不重新生成——`test_s04_recovery_replays_the_same_creative_candidate`（同一 `creative_direction` id，`creative_planner` 仅 1 次，审查 2 次）、`test_s05_recovery_replays_the_same_visual_candidate`（同一 `visual_plan` id，`visual.submit_calls == []`）。
- **独立审查职责未降低**：S04/S05/S06 卡片仍各自由 `output_reviewer` 独立调用，生产与审查分离；`required_rule_ids` 仍由服务端清单给出，advisory 不能替代 blocking。

## 4. 未迁移范围（明确保留）

- S09/S10/S11（图像质检、交付清单、历史/旧数据复核）仍使用各自 `_validate_review`，由 #91 迁移。
- S06 最终需求卡的字段合成、强度/软硬分层与不可变执行快照属于 P3（#96–#98）；本票只迁移审查裁决，不改写历史 payload、不重新解释既有约束。
- `dispatch.available_actions` 在 `failed` 状态下仍并列阶段复核动作与 `continue_workflow`（#93 统一收口）。

## 5. 验证记录

```
cd backend
.\.venv\Scripts\python.exe -m pytest tests\test_v3_p1_t04_production_reviews.py -q                         → 21 passed
.\.venv\Scripts\python.exe -m pytest tests\test_v2_t37_creative.py tests\test_v2_t08_visual_final_brief.py -q → 36 passed, 1 failed（已知基线红）
.\.venv\Scripts\python.exe -m pytest tests\test_v2_t09_confirmation.py tests\test_t04_brief.py -q              → 16 passed
.\.venv\Scripts\python.exe -m pytest tests\test_t04_brief.py tests\test_v2_t15_recovery.py -q                  → 20 passed, 3 failed（已知基线红）
.\.venv\Scripts\python.exe -m pytest tests\test_t68_recovery.py tests\test_v2_t17_restart_recovery.py -q        → 17 passed
```

已知基线红（非本票引入，均为 #93 的 `available_actions` 泄漏）：

- `tests/test_v2_t08_visual_final_brief.py::test_visual_technical_failure_has_zero_automatic_retries`（`['plan_visual','continue_workflow']`）
- `tests/test_v2_t15_recovery.py::test_vision_producer_failure_resumes_with_original_image`
- `tests/test_v2_t15_recovery.py::test_unconfigured_text_capability_stays_blocked_until_explicit_continue_after_fix`
- `tests/test_v2_t15_recovery.py::test_unconfigured_vision_capability_blocks_until_fixed_and_resumes_original_request`
