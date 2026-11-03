# Issue #112 GF-05 验收记录：修正图登记与复核绑定

## 1. 范围与结论

- 工单：GF-05（`version/v3/workflow-gate-remediation-v3.md` §2.3），父任务 #112。
- 目标：修正图必须成为独立、不可变的执行事实，记录原任务、原资产、修正来源／操作者、实际字节摘要和生成／编辑适配器调用；复核只接受当前任务登记的修正版本，拒绝任意项目资产、跨任务资产和已被替换的候选；原任务与原资产保持历史事实，修正图重新执行完整图片门禁与交付绑定。
- 基线：`integration/v3` 的 `668a064`（GF-01/02/04 完成后）。
- 本次生产变更：`main/backend/app/models.py`、`main/backend/app/corrections.py`、`main/backend/app/prompt_preparation.py`、`main/backend/app/final_confirmation.py`、`main/backend/app/services/generations.py`、`main/backend/app/workflow/stages/s09.py`、`main/backend/app/workflow/state.py`、`main/backend/app/workflow/dispatch.py`、迁移 `0016_image_corrections.py`。
- 结论：修正图不再由客户端资产 ID 直接送入复核。`request_correction` 先创建新的准备意图并重新编制、审查、展示完整请求；最终确认后由服务端使用原图字节作为编辑来源调用图片适配器，交付后登记不可变 `ImageCorrection` 事实。复核只接受该登记记录，原任务的原资产、原任务结果和历史批准不被改写。

## 2. 修复的旁路

修复前：

- S09 `recheck_candidate` 接受任意同项目 `generated`、`quarantined` 资产 ID，直接把该资产当作当前任务候选复核，并在通过后把 `GenerationJob.result_asset_id` 覆盖成该资产。客户端资产 ID 实际成为派生证明。
- 没有服务端登记的修正来源、修正操作者、实际字节摘要或适配器调用记录；跨任务资产、无关资产和被替换的候选都能进入复核。
- 原任务的原资产与结果绑定可被复核结果覆盖，历史事实不保真。

修复后：

- 新增 `ImageCorrection` 表与迁移 0016：记录项目、run、新准备意图、原任务 `source_job_id`、原资产 `source_asset_id`／摘要、操作者、修正说明、状态、适配器／模型、请求摘要、修正生成／任务和结果资产／摘要。
- 新增 S09 动作 `request_correction`：验证修正说明，加载当前任务与原始资产，验证原执行尝试的最终授权仍有效，创建新的准备意图（新受理计数与三类槽位），登记 `ImageCorrection(status=prepared)`，进入 S07 重新编制、审查并展示完整请求，等待最终确认。此阶段不创建新 GenerationJob、不调用图片适配器。
- `prompt_preparation.build_request` 对修正意图使用原生成资产作为唯一 `edit_source` 参考，并在冻结请求中写入 `correction` 块（修正 ID、原任务、原资产、原资产摘要、说明）。最终确认仍走 `final_confirmation._confirm_prompt_once` 的 `BEGIN IMMEDIATE` 重验，并关联修正记录。
- `services/generations._execute_claimed` 在执行时再次加载原资产、验证摘要和原执行尝试的 `require_authorized_attempt`，把原图字节作为 `reference_bytes` 交给现有图片适配器；OpenAI Images 适配器因此走 `/images/edits`，并记录真实适配器调用。
- 交付时在同一事务内登记修正结果资产、实际摘要和 `reviewing` 状态；复核通过／拒绝时更新修正状态。原任务 `result_asset_id` 不再被覆盖。
- S09 复核只接受 `correction_id`：任意 `asset_id`、跨项目／跨 run 的修正、未登记修正、非复核状态或字节摘要不匹配均返回 409 `CORRECTION_NOT_REGISTERED`。无 `correction_id` 的复核仅允许服务端已绑定的原候选，用于技术性复核恢复，不接受客户端资产。
- `workflow/state.add_approval` 新增 `revoke_existing` 开关：修正意图新增提示词批准时不撤销原任务提示词批准，保证原执行尝试与历史批准仍可验证；普通提示词修订仍撤销旧批准。

## 3. TDD 反例（RED）

新增 `backend/tests/test_gf05_correction_binding.py`，全部通过真实 HTTP、临时 SQLite 和受控 LLM／Visual adapter 驱动，不以内部 helper 代替公开入口。

| 反例 | 修复前事实 | RED 结果 |
|---|---|---|
| 任意项目资产直接复核 | 旧 `test_recheck_approves_corrected_derivative_without_report_budget` 直接插入无关 `generated` 资产并 POST `recheck_candidate`，可进入 S10 并覆盖任务结果 | 新用例 `test_recheck_rejects_an_arbitrary_or_foreign_asset` 要求 409 `CORRECTION_NOT_REGISTERED`；修复前会得到 200 并批准无来源资产 |
| 修正执行绕过原授权 | 新用例先经 `request_correction` 展示并最终确认，再执行编辑；修复前在 `require_authorized_attempt(source_job.generation_id)` 处返回 409 `FINAL_CONFIRMATION_REQUIRED` | 稳定测试夹具后运行：`1 failed, 1 passed in ~7s`，第一例停在 S08 blocked `GENERATION_UNKNOWN`，根因是修正准备新增批准撤销了原任务提示词批准 |
| 被替换的修正候选 | 第三例登记修正并让修正复核失败，再替换结果文件字节、恢复复核状态，期望 409 `CORRECTION_NOT_REGISTERED` | 修复前 `_load_candidate` 先因摘要不符返回 503 `SERVICE_UNAVAILABLE`；增加 `expected_sha256` 后返回 409 |

## 4. GREEN 与回归证据

工作目录：`D:/Desktop/新建文件夹/backend`；解释器：`.venv/Scripts/python.exe`。

本票新增测试：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_gf05_correction_binding.py -q -p no:cacheprovider
```

结果：`3 passed in 13.58s`。

覆盖：

- `test_correction_is_a_registered_reviewed_execution_fact`：首次图片质量失败 → `request_correction` 返回 S07 `needs_input` 与完整预览，零新任务、零新图片调用；修正记录绑定原任务、原资产和实际摘要；最终确认后进入 S10，恰好第二次 Visual 调用，且 `reference_bytes` 等于原图字节、提示词含修正说明；原任务 `result_asset_id` 不变，修正记录 `approved` 且结果资产等于第二次任务结果。
- `test_recheck_rejects_an_arbitrary_or_foreign_asset`：任意 `asset_id` 与未知 `correction_id` 均 409 `CORRECTION_NOT_REGISTERED`，图片调用数不变。
- `test_correction_cannot_cross_tasks_or_survive_byte_replacement`：另一账号／项目的修正 ID 在第一个项目复核时 409；第一个项目登记的修正结果文件被替换后复核 409，原登记与历史仍保留。

旧质量套件迁移后完整运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_v2_t12_quality.py tests/test_gf05_correction_binding.py -q -p no:cacheprovider
```

结果：`20 passed in 41.97s`。

## 5. SRS 验收映射

| 需求／验收 | 本票证据 |
|---|---|
| V3-GEN-02 展示即执行 | `request_correction` 先进入 S07 展示完整修正请求；最终确认消费冻结快照，实际 Visual 请求使用同一提示词和原图参考字节。 |
| V3-GEN-03 幂等与恢复 | 修正使用新的准备意图、受理计数和三类槽位；最终确认仍由 `_confirm_prompt_once` 创建至多一个任务；原执行授权不因修正准备被撤销。 |
| V3-AC-25 执行消费同一展示快照 | 修正预览与最终提交请求共用冻结 `request_digest`；预览前不调用图片，最终确认后才执行。 |
| V3-AC-29 新版本与原快照不被覆盖 | 修正结果作为新的 `ImageCorrection` 结果资产登记；原任务 `result_asset_id`、原资产摘要和历史批准保持不变。 |
| V3-AC-46 未批准图片不可预览 | 修正结果在复核前保持隔离；任意资产和替换字节均不能进入批准或交付。 |

## 6. 受影响旧场景与残余

- `backend/tests/test_v2_t12_quality.py` 的三个旧复核用例按 GF-05 契约迁移：
  - 原“任意修正派生资产无报告预算即可批准”改为“未登记修正资产拒绝，资产保持隔离，质量报告预算不重置”。
  - 原“评审活跃时可直接复核外部资产”改为“外部资产拒绝，任务保持原结果绑定”。
  - 原“技术失败后无资产复核恢复”保留，但不再通过客户端资产 ID 指定候选，改由服务端原任务候选绑定，并断言质量报告产物。
- 旧 `test_recheck_can_recover_a_failed_role_contract` 的 `quality_recheck` 产物预期改为 `quality_report`：只有登记修正才产生 `quality_recheck`；无修正的技术复核是原候选的质量报告重跑，不伪造新图片版本。
- 残余：修正入口在 S09 质量失败／可复核状态下提供；若原任务结果已过期或原资产不可读，必须重新准备而不是修正。前端修正入口的文案与按钮属于后续 UI 工作，本票只保证后端公开命令与绑定。

## 7. 检查

- `ruff check`：上述变更文件全部通过。
- `compileall -q main/backend/app backend/migrations/versions`：通过。
- `git diff --check`：无空白错误。
