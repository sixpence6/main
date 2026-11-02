# P1 / T02 — 服务端规则目录与统一有效裁决（#88）

- 票：#88（T02，P1）；父问题 #86；批次 P1（R1/R2 门禁第一步）
- 集成分支：integration/v3；起点 `ef92b33`（T01 基线）；规则集版本 `RULESET_VERSION = "v3.rules.1"`；提示词版本 `2.0.0`（未变更）
- 环境：隔离临时 SQLite（`tests.support.apps`），适配器计数替身，网络全部 stub
- 证据：`docs/acceptance/evidence/p1-t02.json`；测试：`backend/tests/test_v3_p1_t02_adjudication.py`（20 项）、`backend/tests/test_v3_p0_baseline.py`（F1/F2 由 strict-xfail 转正）

## 1. 交付内容

1. 新增 `main/backend/app/workflow/adjudication.py`：服务端规则目录 + 统一有效裁决。不是通用规则引擎，只描述
   “某目标种类有哪些规则、谁 blocking/谁 advisory、需要什么合法证据、重复实例如何区分、声明与事实冲突如何处理”。
   - `Rule(rule_id, label, kind, evidence_types, applicable, reason, instance)`：`kind` 仅 `blocking`/`advisory`；
     不适用必须带服务端理由；`evidence_types` 限定该规则可接受的证据类型（`artifact/prompt_plan/image/reference/requirement/technical/report/snapshot`，未知前缀按 `artifact`）。
   - `RuleSet(target_kind, rules, version, decisions)`：`.blocking()`/`.advisory()`/`.slot()`/`.describe()`；`describe()` 可回灌（`ruleset_from_payload`）以便恢复时用同一规则集复算。
   - `adjudicate(ruleset, *, target_id, result_target_id, declared_decision, checks, allowed_evidence) -> Adjudication`。
   - `s07_rules(*, echo_rules=())`：S07 目录 = blocking `G03_CONFIRMATION`、`G05_EXECUTION_BOUNDARY` + advisory `advisory:aesthetics`、`advisory:composition`、`advisory:lighting`、`advisory:creative`；`echo_rules` 把简报里的编制覆盖规则以 advisory 身份登记（覆盖已由服务端候选校验强制，不允许再当放行条件）。
2. `s07.py`：`_validate_review` 改为调用 `adjudicate`，审查输入里下发 `ruleset_version` + `rules` 目录，`required_rule_ids` 只取 blocking 规则；审查结果用 `{"ruleset_version","checks"}` 持久化到 `state.add_review` 与 `state.add_approval`。
3. `s12.py`：`_resume_review` 增加 S07 分支 `_resume_s07_review`——重放持久化的审查请求，用持久化目录（缺失回退 `s07_rules()`）复算，产出一致动作（通过→S08 + `generation_prompt` 批准；拒绝→S07 blocked `REVIEW_{DECISION}`）。
4. `contracts.py`：`Check` 增加可选 `instance`（用于同一规则多次核验），向后兼容，不升版本。
5. `prompts.py`：`output_reviewer` 增量同步为“程序给出的 rules 是唯一规则清单 / advisory 不改变放行 / 清单外规则不可用 / 不要用 not_applicable 规避 blocking”，并说明 `instance` 语义。

## 2. 裁决语义（统一口径）

| 情形 | 结果 |
| --- | --- |
| blocking 规则缺结果 | 协议错误 `MISSING_RESULT` |
| blocking 规则（适用）给 `not_applicable` | 协议错误 `CONTRADICTION`（不允许用 N/A 规避） |
| blocking 规则（服务端判定不适用）出现在结果里 | 必须 `not_applicable`，否则 `CONTRADICTION`；缺省视为已豁免 |
| 结果引用目录外规则 | 协议错误 `UNKNOWN_RULE` |
| 证据不在允许集合或缺证据 | 协议错误 `ILLEGAL_EVIDENCE` |
| 证据类型与规则要求不符 | 协议错误 `ILLEGAL_EVIDENCE` |
| 结果 `target_id` 与目标不符 | 协议错误 `WRONG_TARGET` |
| 同一 `(rule_id, instance)` 重复 | 协议错误 `DUPLICATE_INSTANCE`；不同实例逐条核验 |
| 声明 `pass` 但存在 fail/unknown | 协议错误 `CONTRADICTION` |
| 声明 `revise/reject` 但 blocking 全 pass | 协议错误 `CONTRADICTION` |
| blocking 全 pass + advisory 任意（pass/fail/unknown/N-A） | 通过，advisory 只记录 |
| blocking 有 fail | 阻断（`PROMPT_REVIEW_REJECTED`），advisory 不能救 |

协议错误统一映射为 `409 PROMPT_REVIEW_INVALID`（既有错误码，不新增对外码），**不是** `REVIEW_UNAVAILABLE`：
模型调用失败才是能力/技术故障，无效审查结果不再是“审查服务不可用”，也不会把阶段置 failed。

## 3. 验收覆盖

- **WG3-AC-01**：blocking 规则全 `not_applicable`/空证据/缺失/unknown 不再放行。HTTP 级断言：`test_s07_review_all_not_applicable_cannot_approve` → 409 `PROMPT_REVIEW_INVALID`，`visual.submit_calls == []`（零图像调用），`prompt_plan` 仍为 `candidate`、`payload is None`、无 `generation_prompt` 批准；正例对照 `test_s07_review_pass_unlocks_s08`。
- **WG3-AC-02**：blocking 全 pass 时 advisory `fail`/`unknown`/`not_applicable` 均不改变放行（单元 + HTTP `test_s07_advisory_failure_does_not_block` → 200，S08，`prompt_plan` 批准）；反向 `test_s07_blocking_failure_survives_advisory_pass` → 409 `PROMPT_REVIEW_REJECTED`，证明 advisory 不能救。
- **WG3-AC-03**：伪造规则（`UNKNOWN_RULE`）、非法证据（`ILLEGAL_EVIDENCE`）、错目标（`WRONG_TARGET`）、声明与事实矛盾（`CONTRADICTION`）、重复实例（`DUPLICATE_INSTANCE`）全部在协议边界被拒；`test_v2_t40_prompt_review.py::test_duplicate_rule_instance_is_rejected_as_protocol_issue` 由“允许一次规则多次核验”翻转为 409。
- **恢复一致性**：`test_s07_recovery_replays_same_candidate_with_same_catalogue`：首次审查超时 503 + `REVIEW_UNAVAILABLE` + `available_actions == ["continue_workflow"]`；显式继续后重放同一候选、同一目录，得到 200 S08、同一 `prompt_plan` 批准、`engineer_calls == [1]`、`review_calls == [1, 1]`、`visual.submit_calls == []`（不重生图、不重生候选）。

## 4. 未迁移消费者（本轮明确保持原样）

`s01`（输入/意图审查）、`s02`（参考图审查）、`s03`（需求审查）、`s04`（创意审查）、`s05`（视觉审查）、`s09`（质检/图像批准）、`s10`（交付清单）、`s11`（报告/交付历史）仍使用各自的 `_validate_review`/`_validate_quality`，
无效审查结果仍可能落到 `REVIEW_UNAVAILABLE`。它们分别由 #89（s01/s02/s03 + S12）、#90（s04/s05/s06）、#91（s09/s10/s11 + 历史）迁移；
#92 负责在迁移完成后收敛被取代的分支并跑 WG3-AC-01/02/03/12 全量正反例。本轮未删除任何既有校验，未改变未迁移阶段的对外行为。

## 5. 复核命令

```
cd backend
.\.venv\Scripts\python.exe -m pytest tests\test_v3_p1_t02_adjudication.py -q                  # 20 passed
.\.venv\Scripts\python.exe -m pytest tests\test_v3_p0_baseline.py -q                          # 4 passed, 1 xfailed（F4 留给 #96）
.\.venv\Scripts\python.exe -m ruff check --select E501,F,E9,I ..\main\backend\app\workflow\adjudication.py ..\main\backend\app\workflow\stages\s12.py tests\test_v3_p1_t02_adjudication.py
```

## 6. 残余未知

- 其余 8 个阶段的目录化仍待 #89/#90/#91；在此之前 `RULESET_VERSION` 只覆盖 S07。
- 前端 `rules` 呈现在 P5（#101/#102）统一，本轮不改 UI。
- 既有红色基线 3 项（`test_t03_intent_review` 1 项、`test_t11_retrieve` 2 项）与本票无关，见 `p0-baseline.json`。
