# Issue #117 T05 验收记录：四态恢复与旧路径守卫

## 1. 范围

- 工单：#117（V3 纯对话 T05）。
- 目标：等待、已授权未调用、已提交、未知四态在真实进程重启后仍可判定；`recover_generation` 只在“已授权未调用”时消费授权，已提交/未知只能核实或取回；启动修复只修本地事实，绝不自动补调用；旧入口与旧发布路径统一要求最终授权；迁移不补造旧授权。
- 本票只涉及 R2 后端行为，无前端改动、无新迁移。

## 2. 实现

- 新增 `main/backend/app/recovery_states.py`
  - `RECOVERY_STATES` 四态与 `allowed_actions`：waiting → continue_preparation；authorized_not_called → consume_authorization；submitted → verify_submission/retrieve_result；unknown → verify_submission。
  - `RecoveryFacts` / `classify`：任一提交痕迹（submit_attempted、持久化提交审计、远端 id、结果 locator/资产、已派发 job 状态、response_lost、行不一致）都不会降级为可再次消费授权的状态。
  - `load_recovery_record` 读取 job/generation/最终授权并给出一致性校验（job↔generation↔authorization 绑定、locator JSON 合法性）。
  - `require_legacy_authorization(None)` 直接 409 FINAL_CONFIRMATION_REQUIRED，不做回填。
- `main/backend/app/generation_recovery.py`
  - `_load` 改为 `_load_record`，恢复前先分类；`resume_interrupted` 仅 `AUTHORIZED_NOT_CALLED` 且 `require_action` 通过才继续，仍保留 `require_authorized_job`。
  - `recover_remote` 仅允许 SUBMITTED/UNKNOWN，且要求 `verify_submission`，永不调用 `adapter.submit`。
- `main/backend/app/workflow/stages/s08.py`
  - `_recover`：WAITING 不铸授权并拒绝；AUTHORIZED_NOT_CALLED 走原恢复路径；SUBMITTED/UNKNOWN 只核实远端；其余拒绝。
  - `_mark_submit_started`：job 派发标记与 `generation.submit_started` 审计在同一事务提交，形成持久提交边界证据。
- `main/backend/app/services/recovery.py`
  - 启动修复用同一分类器重写 uncertain 判定：任何提交证据或未知结果都标 `unknown/VISUAL_OUTCOME_UNKNOWN`，不自动调用、不重建授权。
- `main/backend/app/workflow/stages/s10.py`
  - 发布前无条件 `execution_request`（内部 `require_authorized_attempt`），删除“有授权才检查”的旧分支，旧行无授权不能发布。

## 3. 测试与证据

`backend/tests/test_issue_117_t05.py`（13 个测试函数，14 个用例）使用真实 uvicorn 子进程在精确边界崩溃并重启同一数据目录：

- 四态与允许动作表、保守分类（submission audit / response_lost / corrupt 均 unknown）。
- 只有“可证未提交”的授权可被消费；缺失授权、任何提交痕迹均拒绝。
- 新库迁移后 `final_authorizations` 为空（不补造）。
- waiting 跨重启仍等待，只有显式确认才提交。
- 最终确认提交前崩溃：无授权、无 generation/job；重启后仍可确认。
- 最终确认提交后崩溃：1 授权、1 job、submit_attempted=0；重启后 S08 blocked PROCESS_INTERRUPTED，只有显式 `recover_generation` 才提交一次，job id 不变。
- 提交标记后崩溃：unknown 且永不重新提交；审计 trace 丢失计数器时仍 unknown。
- 供应商边界崩溃（已调用未得结果）：unknown，无第二次 submit。
- 轮询丢响应：核实的是原远端任务，不新建。
- 旧行无最终授权：执行与发布均被 409 拒绝。

运行（cwd = T05 worktree `backend`，隔离 basetemp）：

```powershell
& $py -m pytest tests/test_issue_117_t05.py -q -p no:cacheprovider --basetemp D:\Desktop\issue-112-context\t05-run3
```

实际结果：`14 passed in 79.60s`。日志：`backend/.scratch/t05/run3.log`。

说明：首次全量运行出现 `test_crash_after_final_commit_resumes_only_on_explicit_recovery` 失败（1 failed, 13 passed），该用例单独重跑与随后的全量重跑均通过（1 passed in 9.43s）；失败点是重启后工作流响应断言，属崩溃时序抖动，已记录为残留风险。

## 4. 逐条验收判定

| 票面要求 | 判定 | 证据 |
|---|---|---|
| 四态可恢复且动作受限 | 通过 | `RECOVERY_STATES`/分类器单测；真实重启场景 |
| 启动只修本地事实，不自动补调用 | 通过 | 启动修复后无 adapter 调用；等待与授权未调用状态保持 |
| 已授权未调用只能显式恢复一次 | 通过 | crash-after-confirm 场景 job id 不变、submit 恰一次 |
| 已提交/未知绝不重复提交 | 通过 | submit marker / provider boundary / lost response 三场景 |
| 旧路径统一最终授权守卫 | 通过 | 旧行无授权时执行与发布均 409；发布不再走“有授权才检查”分支 |
| 迁移不补造旧授权 | 通过 | 新库迁移后授权表为空；`require_legacy_authorization` fail closed |

## 5. 残留风险

- 崩溃时序为真实进程终止，个别重启断言依赖端口与进程退出时机，首次全量运行出现过一次抖动（见上）；后续合并验收需再跑一次全量确认。
- 提交审计依靠 `AuditEvent` 行；若未来有人清理审计表，分类会退回基于 job/generation 字段的判定。
- 本票未覆盖真实供应商网络故障与前端呈现；供应商边界用受控 adapter 模拟。
