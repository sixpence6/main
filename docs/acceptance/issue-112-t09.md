# Issue #121 T09 验收记录：后端停点验收与 R2→R3 进入门槛复验

## 1. 范围与结论

- 工单：#121（V3 纯对话 T09，父票 #112）。
- 目标：在同一当前基线上，用公开接口证明 PC3-01—09 与 MV3-05—09 后端行为，并复验当前受影响的 R1/R2；失败或未知未收敛时不签发 R3 进入结论。
- 验收基线：集成分支 `codex/issue-112-integration` 的 `387f302`（T08 合并后）。本票从该提交新建 `codex/issue-112-t121` worktree。
- 本票变更：只新增 `backend/tests/test_issue_121_t09.py`（10 个测试函数、15 个用例）和本验收文档；没有修改生产代码、迁移、SRS 参数、次数/槽位口径或前端。
- 结论：本票覆盖的 PC3-01—09、MV3-05—09 后端行为均通过当前基线证据；受影响的 R1 身份/对象/资源出口与 R2 计数/并发/故障/恢复均通过。后端可进入 R3，但这不替代 R3 界面、真实浏览器/手机或供应商验收；这些仍保持未知并留待后续票。

## 2. 测试面

新增 `backend/tests/test_issue_121_t09.py`，全部使用：

- 已登录用户与公开 HTTP/工作流接口；不直接调用内部阶段或私有 helper 作为行为证明。
- 真实临时 SQLite（`full_settings`/Alembic 临时库），受控 `LLMStub`/`VisualStub`；不 mock 工作流内部阶段。
- 必要的独立进程重启：T01 只读重启应用与 T05 崩溃/未知任务进程。
- 隔离 `TEMP/TMP`、`--basetemp D:/Desktop/issue-112-context/...`，不接触原用户树。

15 个用例按函数编号如下（后文用 G1—G10 引用）：

- G1 `test_gate_journey_text_reference_repair_and_frozen_publish`：带图消息进入 S01，完成 S03→S06、预览、重复读取、纯对话、标题改名、提示词修复、需求实质修改、重新准备、最终确认/重放、发布；同时检查请求摘要、参考内容版本、负向提示词、槽位和 `project_active`。
- G2 `test_gate_candidate_waits_for_independent_review_and_two_restarts`：独立审核未完成前候选不可见、不可确认；审核完成后等待确认；两个独立进程重启后仍等待且零图像调用。
- G3 `test_gate_preparation_double_click_and_same_request_id_keep_one_acceptance`：准备阶段双击只接受一次；同一 request_id 改参数冲突；意图/用量/槽位不变。
- G4 `test_gate_final_dual_connection_and_lost_response_keep_one_submission`：两个独立 HTTP 应用用同一最终命令并发确认，只产生一个任务/一次提交；响应丢失后的重放返回同一任务。
- G5 `test_gate_stale_login_wrong_owner_and_wrong_project_fail_before_attempt`：缺载荷 422、他人账号 404、错误项目 404/409、登出重登后的旧命令 409；旧登录上下文不产生授权/任务/图像调用。
- G6 `test_gate_final_write_fault_rolls_back_and_retry_submits_once`（5 个参数点）：final_authorizations、generation_jobs、generations、audit_events、prepared_prompts 写入故障均 500 回滚；移除故障后同一命令只提交一次。
- G7 `test_gate_abandon_releases_slots_keeps_usage_and_never_claims_remote_cancel`：S07 放弃后 `PROMPT_ABANDONED`、预览失效、意图 abandoned、槽位释放、用量保留、无任务/无远端取消声明；重放幂等。
- G8 `test_gate_busy_message_is_rejected_without_queue_or_model_call`（2 个参数点）：恢复占用与未收敛任务占用时消息入口 409；不排队、不增加消息、不增加模型调用、不提交图像。
- G9 `test_gate_unknown_submit_after_crash_is_never_resubmitted`：提交标记后崩溃得到 unknown 任务；重启后 `verify_generation`/`recover_generation` 只能核验原任务，绝不重发。
- G10 `test_gate_0015_upgrade_preserves_history_without_inventing_attachments`：0014→0015 升级保留旧消息/turn_kind，不回填附件；downgrade 保留消息并删除附件表。

## 3. 命令与结果

工作目录：`C:/Users/28425/.codex/worktrees/issue-112-t121/新建文件夹/backend`；变量 `py` 指向唯一 Python runner：

```powershell
$py = 'C:/Users/28425/.codex/worktrees/issue-112-integration/新建文件夹/backend/.venv/Scripts/python.exe'
```

本票 gate 文件：

```powershell
& $py -m pytest tests/test_issue_121_t09.py -q -p no:cacheprovider --basetemp D:/Desktop/issue-112-context/t121-full2
```

结果：`15 passed in 54.67s`。

当前受影响 R1/R2/T01—T08 复验分三批运行（均使用隔离 basetemp）：

```powershell
& $py -m pytest tests/test_issue_113_t01.py tests/test_issue_114_t02.py tests/test_issue_115_t03.py -q -p no:cacheprovider --basetemp D:/Desktop/issue-112-context/t121-affected1b
& $py -m pytest tests/test_issue_116_t04.py tests/test_issue_117_t05.py tests/test_issue_118_t06.py -q -p no:cacheprovider --basetemp D:/Desktop/issue-112-context/t121-affected2
& $py -m pytest tests/test_issue_119_t07.py tests/test_issue_120_t08.py tests/test_issue_121_t09.py -q -p no:cacheprovider --basetemp D:/Desktop/issue-112-context/t121-affected3
```

结果：

- T01—T03：`77 passed in 239.64s`。
- T04—T06：`46 passed in 180.35s`。
- T07/T08/T121：`29 passed in 93.47s`。
- 三批合计：`152 passed`（其中 15 个为本票新增 gate 用例；其余为当前基线既有受影响回归）。

静态检查：

```powershell
& $py -m ruff check --no-cache tests/test_issue_121_t09.py
& $py -m compileall -q tests/test_issue_121_t09.py
git diff --check
```

结果：ruff `All checks passed!`；compileall 通过；`git diff --check` 无输出。测试仅新增于 `backend/tests`，文档仅位于 `docs/acceptance`，未写入 `main/` 应用本体。

## 4. PC3-01—09 逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| PC3-01 未批准或已失效候选不公开展示、不可确认；完整实际 prompt 与依赖可安全读取 | 通过 | G2 审核暂停时 `prompt_preview=None`、prompt_plan 为 candidate 且 payload 为空、无 `confirm_prompt`；G1 预览可读完整正向/负向 prompt、输出参数与参考版本；G1 需求修改后旧预览消失、旧 prepared_prompt invalidated |
| PC3-02 明确准备完成且独立审核结束后，最终点击前零图像尝试/提交；多次读取和进程重启仍等待 | 通过 | G1 最终点击前 `visual.submit_calls==[]` 且多次 GET 预览稳定；G2 释放审核后两个独立进程重启仍保持同一预览（仅 command_context 不同）、零提交、调用日志为空 |
| PC3-03 最终捕获请求逐字段/摘要匹配展示快照，包括真实负向处理与参考内容版本 | 通过 | G1 实际 Visual 请求 prompt 等于冻结请求 prompt，包含负向提示词，image_size/count/response_format 相等，reference_bytes 等于上传字节且 mime/path 正确；请求 references `content_version` 等于上传 SHA-256 |
| PC3-04 需求/prompt/批准/输出/参考变化以及错误项目/账号/旧登录上下文拒绝旧确认，且零额外调用 | 通过 | G1 需求修改后旧 confirm_prompt 409、无任务；G1 prompt_change 产生新预览并失效旧预览；G5 缺载荷/他人账号/错误项目/登出重登旧命令均不产生授权、任务或图像调用；当前 T03 套件覆盖 approval/output/reference/service 变化矩阵（T01—T03 批 77 passed） |
| PC3-05 准备和最终确认各自双击、响应丢失、独立连接竞争、同 ID 异参数均只产生一个意图/用量/槽位/提交 | 通过 | G3 准备双击与同 request_id 异参数；G4 两独立应用并发最终命令只产生一个 job/一次提交，重放同 job；G1 最终确认重放仍同 job；当前 T04 套件覆盖独立连接/三客户端竞争（T04—T06 批 46 passed） |
| PC3-06 每个授权/执行/审计写入点故障完全回滚；最终确认不执行新的计数接受 | 通过 | G6 覆盖 final_authorizations/generation_jobs/generations/audit_events/prepared_prompts 五个最终写入点，均无授权/任务/生成记录且可重试；当前 T04 套件覆盖 generation_slots/generation_usage/audit_events 准备写入点与最终写入点，T01 套件覆盖 approvals/artifact_reviews/workflow_runs 等写入点；最终确认路径用量保持 1 |
| PC3-07 未授权恢复/旧动作不能生成；真实 submitted/unknown 只核验原任务，崩溃前后有证据 | 通过 | G9 提交标记后崩溃：job `submit_attempted=1`、重启后 `GENERATION_UNKNOWN`，verify/recover 均不产生 visual 调用，job 仍 unknown，授权仍为 1；G5 旧登录上下文确认被拒且零任务；当前 T05 套件覆盖崩溃前后/恢复矩阵（T04—T06 批） |
| PC3-08 放弃、实质修改、改名、纯对话、忙拒绝分别断言需求版本/账本/槽位/调用次数 | 通过 | G1 纯对话和改名不改变 project/requirement revision、预览或 ledger；G1 需求修改增加 revision、放弃旧 intent、旧预览失效、live slots=0；G7 放弃保留 usage、释放槽位、零任务；G8 忙拒绝不排队、不增加消息/模型调用/图像调用 |
| PC3-09 历史升级保留；旧 unknown 任务永不重发；原内容门禁和发布仍生效 | 通过 | G10 0014→0015 旧消息/turn_kind 保留且无补造附件，downgrade 后消息保留；G9 unknown 任务不重发；G1 发布后 asset GET 200、版本绑定同一 job、`project_active=0`；当前 T08 套件覆盖附件历史快照与内容门禁 |

## 5. MV3-05—09 后端逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| MV3-05 最终确认按钮出现时完整真实已批准 prompt 可见/可复制；未批准不公开 | 通过（后端读取面） | G2 审核暂停时无预览/无确认动作；G1 预览包含完整 positive/negative prompt、输出和参考依赖，批准后才 `can_confirm=True`；复制交互属 R3 UI，本票不冒充浏览器验收 |
| MV3-06 已批准但未最终确认零图像提交；等待跨刷新/重启保持 | 通过 | G1 最终点击前零提交；G2 独立进程重启后仍等待、零提交、调用日志为空 |
| MV3-07 实际提交的 prompt/参考依赖/输出参数匹配已确认快照 | 通过 | G1 实际 Visual 请求逐字段匹配冻结请求，含负向提示词、图片尺寸/数量/格式、参考字节/mime/path |
| MV3-08 需求/prompt/批准/账号/项目上下文变化后服务端拒绝旧授权 | 通过 | G1 需求修改与 prompt 修复后旧确认/旧预览失效；G5 账号/项目/旧登录上下文拒绝且无任务；当前 T03 套件覆盖 approval/reference/output/service 变化 |
| MV3-09 双击、响应丢失、并发重放不重复接受/计数/提交；unknown 不自动重发 | 通过 | G3 准备双击；G4 最终双连接与响应丢失；G1 最终重放；G9 unknown 重启后只核验不重发 |

## 6. 受影响 R1/R2 复验

| 面 | 结果 | 证据 |
|---|---|---|
| R1 身份/所有者/对象关联/资源出口 | 通过 | G5 覆盖匿名/他人账号/错误项目/旧登录；G1 发布资产 owner 读取；当前 T08 附件安全套件与 T01/T03 身份/对象边界套件均通过（77/29 passed 批次） |
| R2 计数：usage/intent/slot/ledger | 通过 | G1/G3/G7/G8 直接断言语义；当前 T04/T06 套件继续断言准备/最终写入回滚、每日配额、窗口和三作用域槽位 |
| R2 并发：双击/独立连接/竞争 | 通过 | G3 准备双击与同 ID 异参数；G4 两应用最终并发；当前 T04 套件覆盖两/三客户端竞争与独立连接，均只一次授权/提交 |
| R2 故障与恢复：写入故障、崩溃、放弃、忙拒绝 | 通过 | G6 五个最终写入点回滚重试；G7 放弃幂等且不声称远端取消；G8 忙拒绝不排队；G9 crash-after-submit-mark unknown 不重发；当前 T01/T04/T05/T06 套件通过 |
| 不新增免费预览、自动等待超时、隐式排队、自动图片重试 | 通过 | G2 等待跨重启；G9 unknown 只核验；G8 忙拒绝无队列/无模型调用；测试未发现定时器或自动重试路径 |

## 7. 残余未知与边界

- 未做真实浏览器、手机、R3 前端交互或供应商验收；prompt 的“可复制”仅证明后端公开读取面，不冒充 UI 行为。真实设备证据留待 #129。
- 使用受控 `LLMStub`/`VisualStub`，不代表真实模型/图像供应商的时延、限流、错误码或内容审核结果。
- T05 崩溃路径为当前测试设施中的 crash-after-submit-mark 进程路径；不能穷尽所有操作系统/进程崩溃时序，但已验证未知任务不会被自动重发。
- 迁移证据覆盖 0014→0015 与 downgrade；更早历史版本的兼容由既有迁移套件承担，本票不重复声明未验证的旧版本组合。
- 本票只验证隔离临时库和测试适配器；未操作真实用户数据、凭据或运行配置，未触碰原用户树未提交改动。

## 8. 合并复核（Lead）

- 待功能分支合并后填写：功能提交、`--no-ff` 合并提交、合并后主验收命令/结果、测试文件一致性核对、静态检查与关闭结论。
