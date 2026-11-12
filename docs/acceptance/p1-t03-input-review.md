# P1 / T03 — 输入、参考与需求审查统一裁决（#89）

- 票：#89（T03，P1）；父问题 #86；批次 P1（R1/R2 门禁，接 #88 规则目录）
- 集成分支：integration/v3；交付起点 `cae4a43`（#87 合并后的验收基线）；规则集版本 `RULESET_VERSION = "v3.rules.1"`；提示词版本 `2.0.0`（未变更）
- 环境：隔离临时 SQLite（`tests.support.apps`）、计数型适配器替身、网络全部 stub；无真实数据根、无部署配置改动
- 证据：`docs/acceptance/evidence/p1-t03.json`；测试：`backend/tests/test_v3_p1_t03_review_catalogue.py`（14 项）以及既有 `test_t03_intent_review.py`、`test_v2_t04_reference_analysis.py`、`test_v2_t05_requirements.py`、`test_v2_t06_reference_requirements.py`、`test_v2_t15_recovery.py`

## 1. 交付内容

1. `main/backend/app/workflow/adjudication.py` 增加治理阶段共用入口（不是通用规则引擎，只是把 #88 的目录/裁决能力复用给 S01–S03）：
   - `ADVISORY_GOVERNANCE_RULES`：显式登记的 G01–G08 治理观察项集合（含 `G02_OBSERVATION_GROUNDED`、`G03_*`、`G05_*`、`G08_AUTH`、`review:quality_report`），**不再用正则**认定“advisory 身份”。
   - `governance_ruleset(target_kind, blocking, *, decisions, evidence_types, version)`：blocking 规则按调用方清单登记，其余登记项一律是 advisory（`治理观察项（不改变放行结论）`）。
   - `review_output(output_data, *, target_id, ruleset, allowed_evidence)`：先做“恰好一个结果 / 一个目标 / 非空 checks”的形状检查，再交给 `adjudicate`。
2. `stages/s01.py`（意图路由审查）：删除 `_ADVISORY_RULE_RE` 正则；`_RULESET = governance_ruleset("intent_route", (G01_SCOPE, G01_ACTION))`；`_validate_review` 改用 `review_output`，保留“note 不得含外部链接”，非 pass 结论 → `409 REVIEW_REJECTED`；协议缺陷 → `409 REVIEW_INVALID`，都写 `blocked` 阶段而不是 `failed`。
3. `stages/s02.py`（参考图报告审查）：`_RULESET = governance_ruleset("reference_report", REQUIRED_REVIEW_RULES, evidence_types=("artifact","reference"))`；`_validate_review` 改用 `review_output`，保留“必须有一条非 N/A 检查直接引用实际参考图（`reference:<asset_id>`）”这条独立对照；`_set_failure` 把 `REVIEW_INVALID/REVIEW_REJECTED` 映射为 `blocked`，模板不再是 `REVIEW_UNAVAILABLE`。
4. `stages/s03.py`（需求草稿审查）：`_RULESET = governance_ruleset("requirement_draft", REVIEW_RULE_IDS, evidence_types=("artifact","reference"))`；`_validate_review` 返回 `(decision, checks, repair_codes)`，`repair_codes` 只取 blocking 槽位；`revise` 仍是修复循环（候选 ≤ 3 个），`unknown` 落 `rejected`，其余 `_stop_review` → `blocked REVIEW_REJECTED`；协议缺陷 → `blocked REVIEW_INVALID`，只有真实模型调用失败才是 `failed REVIEW_UNAVAILABLE`。
5. `stages/s12.py`：`RETRYABLE_REVIEW_ISSUES = frozenset({"REVIEW_UNAVAILABLE","REVIEW_INVALID"})`——畸形审查结果在契约可修复后允许显式重放，实质拒绝（`REVIEW_REJECTED`）不允许无输入变更的重试；`_is_recoverable_checkpoint` 与 `available()` 使用同一集合。S01/S02/S03 的恢复分支复用同一 `_validate_review`，因此正常与恢复路径口径一致。
6. `workflow/dispatch.py`：`available_actions` 对 `blocked + REVIEW_INVALID` 只暴露 `continue_workflow`（重放已持久化的审查请求），不再把“回答需求问题”等阶段动作混进协议缺陷状态；`failed` 与 `blocked + REVIEW_UNAVAILABLE` 仍保留阶段复核动作（当前 S09 候选复核），其余 `available_actions` 泄漏由 #93 统一收口。
7. `workflow/templates.py`：新增 `REVIEW_INVALID`、`REVIEW_REJECTED` 两个固定模板（内容不含用户/候选文本）。

## 2. 统一口径（与 #88 的目录语义一致）

| 情形 | 结果 |
| --- | --- |
| blocking 规则缺结果 / 全 `not_applicable` / 空证据 | 协议错误（`MISSING_RESULT` / `CONTRADICTION` / `ILLEGAL_EVIDENCE`）→ `409 REVIEW_INVALID` |
| 结果引用目录外规则 | `UNKNOWN_RULE` → `409 REVIEW_INVALID` |
| 同一 `(rule_id, instance)` 重复 | `DUPLICATE_INSTANCE` → `409 REVIEW_INVALID`（旧“同一必审规则可重复分组”语义作废；需要多次核验必须给 `instance`） |
| blocking 全 pass + advisory 任意结论 | 通过；advisory 只记录，不改变放行 |
| blocking 有 fail + 声明 `pass` | `CONTRADICTION` → `409 REVIEW_INVALID` |
| blocking 有 fail + 声明 `revise`（S03） | `revise` 修复循环，最多 3 个候选 |
| blocking 有 fail + 声明 `reject/unknown` | `blocked REVIEW_REJECTED`（无自动重试） |
| 审查模型调用失败 | `failed REVIEW_UNAVAILABLE`（唯一的技术故障口径） |

## 3. 验收覆盖

- **WG3-AC-02**：blocking 全 pass 时 advisory `pass/fail/unknown/not_applicable` 均不改变结论（单元 4 组；HTTP 正例 `test_review_accepts_advisory_governance_checks` 仍进 S03）；反向证明 advisory 不能救 blocking fail（`test_advisory_pass_cannot_rescue_a_blocking_failure`）。
- **WG3-AC-03**：伪造规则（`UNKNOWN_RULE`）、非法证据/非本次证据（`ILLEGAL_EVIDENCE`）、错目标（`WRONG_TARGET`）、声明与事实矛盾（`CONTRADICTION`）、重复实例（`DUPLICATE_INSTANCE`）在 S01/S02/S03 全部是 **typed 409**，不是 `REVIEW_UNAVAILABLE`，也不会把 run 置 `failed`；HTTP 负例：`test_review_failure_blocks_and_is_not_retried`（revise/missing/unknown_rule/foreign_evidence/external_url）、`test_repeated_required_checks_without_instance_are_a_protocol_defect`、`test_visual_review_contract_negatives_never_approve`、`test_review_failure_is_a_typed_protocol_error_without_candidate_leak`。
- **WG3-AC-12（本批范围）**：草稿候选未通过审查时 `payload is None`、不进入下一阶段、不产生图像调用；恢复路径重放同一持久化审查请求，不重新生成候选（`test_s03_contract_defect_blocks_then_replays_without_regenerating`：`brief_calls == 1`、同一 `brief_draft` id、`visual.submit_calls == []`）。
- **恢复口径**：`REVIEW_INVALID` 阻塞态的 `available_actions` 只有 `continue_workflow`；`REVIEW_REJECTED` 不提供 `continue_workflow`（必须有用户输入变更）。

## 4. 未迁移范围（明确保留）

- S04/S05（创意、视觉）仍使用各自的 `_validate_review`，由 #90 迁移；S06 最终需求卡与 P3 的强度/快照分层也在 #90、#96–#98。
- S09/S10/S11（图像质检、交付、历史复核）仍使用各自校验，由 #91 迁移。
- `dispatch.available_actions` 在 `failed` 状态下仍会并列阶段复核动作与 `continue_workflow`；这是 #93（WG3-AC-10）的整改项，本票只在协议缺陷（blocked）路径收口。

## 5. 验证记录

```
cd backend
.\.venv\Scripts\python.exe -m pytest tests\test_v3_p1_t03_review_catalogue.py -q          → 14 passed
.\.venv\Scripts\python.exe -m pytest tests\test_v2_t05_requirements.py tests\test_v2_t06_reference_requirements.py -q → 39 passed
.\.venv\Scripts\python.exe -m pytest tests\test_t03_intent_review.py tests\test_v2_t04_reference_analysis.py -q      → 46 passed, 3 failed（均为已知基线红）
.\.venv\Scripts\python.exe -m pytest tests\test_v2_t15_recovery.py tests\test_v3_p0_baseline.py -q              → 14 passed, 1 xfailed, 3 failed（同上）
```

已知基线红（非本票引入，分别由 #93 的 `available_actions` 泄漏与既有 T03/T11 恢复用例承担）：

- `tests/test_t03_intent_review.py::test_failed_s01_review_can_be_continued_from_completed_checkpoint`（failed 态泄漏 `submit_request`/`answer_intent_clarification`）
- `tests/test_v2_t04_reference_analysis.py::test_technical_vision_failure_is_not_retried_automatically`
- `tests/test_v2_t04_reference_analysis.py::test_selected_usage_survives_failed_reanalysis_and_retry`
- `tests/test_v2_t15_recovery.py` 三个能力未配置用例（同上 AC-10 泄漏）
