# Issue #112 GF-06 验收记录：恢复审查使用服务端规则目录

## 1. 范围与结论

- 工单：GF-06（`version/v3/workflow-gate-remediation-v3.md` §2.4），父任务 #112。
- 目标：持久化规则目录版本与目标绑定；恢复只能取得当前受支持版本的服务端目录，历史目录仅用于解释旧证据；不受支持或不兼容的版本保持阻断并要求按新契约重新审查；正常路径与 S12 恢复使用同一裁决函数，不能接受历史请求对阻断属性、适用性或证据权限的改写；旧批准内容可在明确版本兼容下读取，但新授权不得继承旧审查。
- 基线：`integration/v3` 的 `c34580b`（GF-05 完成后）。
- 本票生产代码变更：`main/backend/app/workflow/adjudication.py`、`main/backend/app/workflow/roles.py`、`main/backend/app/workflow/stages/s12.py`、`main/backend/app/workflow/templates.py`。
- 结论：S12 不再用持久化请求里的规则强度、适用性或证据权限做裁决。恢复先用 `recovery_ruleset` 校验目标类型与规则版本，再按当前服务端 `s07_rules` 重建目录；重放请求也改写为服务端目录和当前 `prompt_plan`／最终需求证据，历史请求只保留为证据。不受支持版本进入 `RULESET_UNSUPPORTED` 阻断，不调用审查模型、不生成批准。

## 2. 修复的旁路

修复前：

- S12 `_resume_s07_review` 直接调用 `ruleset_from_payload(input_data) or s07_rules()`，完整信任持久化请求中的 `target_kind`、`ruleset_version`、每条规则的 `kind/applicability/evidence_types` 和 `task.allowed_evidence_ids`。
- 只要历史请求被改写，恢复时就可以把阻断规则降为建议、删除适用规则，或把图片／外部证据加入允许集合；裁决仍会按被改写后的目录执行。
- 恢复重放仍把原始持久化请求交给 `run_role_request`，因此即使裁决侧换了目录，模型看到的仍是历史权限。

修复后：

- `SUPPORTED_RULESET_VERSIONS` 明确列出当前支持版本（`v3.rules.1`）；`recovery_ruleset` 要求 `target_kind == "prompt_plan"`、受支持版本、非空且不超过 256 条的合法规则 ID 列表，然后从服务端 `s07_rules(echo_rules=...)` 重建目录。历史规则只作为 advisory echo，不参与阻断判定。
- `ruleset_from_payload` 明确降级为历史解释／展示用途，禁止用于裁决。
- S12 在调用审查模型前执行 `s07._require_confirmed(ctx)`，以服务端当前最终需求与当前候选构造允许证据集合 `{artifact.id, final_artifact.id}`，不再使用历史 `task.allowed_evidence_ids`。
- 重放请求在发送前改写 `input_data.target_kind/ruleset_version/rules` 与 `task.allowed_evidence_ids`；模型看到的目录和证据权限与实际裁决完全一致。
- 不支持的版本或目标类型返回 409 `RULESET_UNSUPPORTED`，将 S07 置为 `blocked`，不调用审查模型、不产生 `generation_prompt` 批准、不调用图片适配器。
- 恢复检查点记录 `ruleset_version`，便于审计历史证据与当前契约的对应关系。

## 3. TDD 反例（RED）

新增 `backend/tests/test_gf06_recovery_ruleset.py`，全部通过真实 HTTP、临时 SQLite 和受控 LLM／Visual adapter：

| 反例 | 修复前事实 | RED／GREEN 结果 |
|---|---|---|
| 篡改历史目录削弱阻断 | 持久化 S07 审查检查点被改写：删除 `G05_EXECUTION_BOUNDARY`，并把 `G03_CONFIRMATION` 降为 advisory；旧实现会信任该目录 | 初始运行 `ImportError: cannot import name 'SUPPORTED_RULESET_VERSIONS'`；首次实现后 `2 failed, 2 passed in 8.12s`，第二审查请求仍收到历史目录（`G05` 缺失），修复请求重放后 `4 passed in 7.44s` |
| 篡改证据权限 | 历史 `task.allowed_evidence_ids` 改为 `image:external`，所有规则 `evidence_types` 改为 `image`；旧实现会把外部图片证据当作合法证据 | 同一轮 RED 中第二例失败：第二审查请求仍包含 `image:external`；修复后 409 `PROMPT_REVIEW_INVALID`，服务端证据集合不含外部图片 |
| 不受支持版本 | 历史 `ruleset_version` 改为 `v99.unsupported` | 预期并验证 409 `RULESET_UNSUPPORTED`，第二次审查不调用模型，S07 `blocked`，零批准、零图片调用 |
| 正常／恢复等价 | 每个受支持版本都必须保证正常编制与恢复重放看到相同目录 | `test_every_supported_version_has_equivalent_normal_and_recovery_catalogues` 对 `SUPPORTED_RULESET_VERSIONS` 参数化；正常与恢复均到达同一 S07 等待预览，`input_data.rules` 完全一致 |

RED 起始命令与结果：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_gf06_recovery_ruleset.py -q -p no:cacheprovider
# ImportError: cannot import name 'SUPPORTED_RULESET_VERSIONS'
```

首次实现后：

```text
2 failed, 2 passed in 8.12s
```

## 4. GREEN 与回归证据

工作目录：`D:/Desktop/新建文件夹/backend`；解释器：`.venv/Scripts/python.exe`。

本票新增测试：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_gf06_recovery_ruleset.py -q -p no:cacheprovider
```

结果：`4 passed in 7.44s`（加入检查点断言后复跑 `4 passed in 7.37s`）。

相邻恢复与裁决回归：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_gf06_recovery_ruleset.py tests/test_v3_p1_t08_recovery.py tests/test_v3_p1_t02_adjudication.py -q -p no:cacheprovider
```

结果：`33 passed in 32.15s`。

覆盖：篡改目录不放行、篡改证据权限不放行、不受支持版本 fail closed、正常／恢复目录等价、恢复检查点记录版本，以及既有 S07 生产者／审查者恢复、预算不重置、并发恢复单次重放。

## 5. SRS 验收映射

| 需求／验收 | 本票证据 |
|---|---|
| V3-GATE-01 服务端裁决 | 恢复目录只来自当前 `s07_rules`；历史规则强度、适用性、证据类型不再参与裁决；不受支持版本拒绝。 |
| V3-GATE-06 审查职责 | 正常 S07 与 S12 `_resume_s07_review` 使用同一 `adjudicate` 路径与同一服务端目录；恢复不能复用被改写的历史批准权限。 |
| V3-AC-41 裁决一致性 | 正常／恢复等价测试覆盖每个受支持版本；篡改历史目录与证据权限均无法放行。 |
| V3-AC-46 未批准内容不可见 | 不支持版本或篡改目录只留下 S07 blocked，不创建 `generation_prompt` 批准、不展示图片、不调用 Visual。 |

## 6. 受影响旧场景与残余

- `backend/tests/test_v3_p1_t08_recovery.py` 的 4 个旧恢复用例原预期 `continue_workflow` 后直接进入 S08 active；GF-01／T02 后恢复审查应停在 S07 `needs_input` 并展示 `prompt_preview.awaiting_confirmation`。本票已迁移其阶段断言：
  - 生产者／审查者恢复、重启恢复、并发恢复均改为等待最终确认，同时保留预算不重置、零图片调用、同一候选批准的断言。
  - 一次性 `continue_workflow` 重放改为 409 `ACTION_NOT_AVAILABLE`，并断言不再调用模型。
  - “图片提交未知”用例的伪造 `GenerationJob` 补上当前 `intent_id`，使 `_ensure_image_unsubmitted` 能证明该任务属于当前意图；仍断言 409 `GENERATION_UNKNOWN`、零模型／图片调用、恢复认领为空。
- 残余（不属于 GF-06）：`backend/tests/test_v3_p1_t10_execution_snapshot.py::test_execution_snapshot_flows_confirm_compile_qc_and_history` 仍按旧“编制后立即质检”语义断言，尚未加入最终确认点击；该夹具迁移归入 GF-07／GF-08 的快照与约束验收。
