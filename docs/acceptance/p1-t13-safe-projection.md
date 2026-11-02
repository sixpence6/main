# P4 / T13 — 安全原因、动作与任务状态投影（#99）

- 票：#99（T13，P4）；父问题 #86；集成分支基线 **aae4d6c**，本票从 **aae4d6c** 展开
- 分支：**ticket/99**；实现提交 **9597541**（docs 证据另提一提交）
- 快照：schema **1.1.0**，仍可只读识别 **1.0.0**；contract **1.0.0**；规则版本 **v3.rules.2**
- 迁移头：**0011_recovery_claims**（T13 无迁移）
- 关键文件：**main/backend/app/workflow/projection.py**、**schemas_workflow.py**、**state.py**、**reasons.py**、**dispatch.py**、**main/src/api/workflow.ts**、**backend/tests/test_v3_p1_t13_safe_projection.py**
- 证据 JSON：**docs/acceptance/evidence/p1-t13.json**

## 1. 实现摘要

T13 把“任务现在是什么状态、为什么停、下一步真实能做什么”集中到服务端投影，前端不再从提示文案或本地推断拼状态。

- **GET /api/projects/{project_id}/workflow** 在原有字段上兼容新增 **task_state**，包含 progress、reason、binding、constraints、resource_state。
- progress 只表达阶段、阶段标签、状态、是否等待用户、是否终态；没有假百分比、没有本地估算进度。
- reason 只从持久化的 issue_code / template_id / template_params 生成安全类别与安全文案；**不读取 run.issue_message**，审核员原文、未审说明、候选正文、私有 locator 不进入投影。
- action 的 effect、read_only、requires_model_call、retry_mode、automatic_retry 由服务端动作目录推导；只读核实与可能继续模型调用的手动恢复分开，所有动作 **automatic_retry=false**，不存在无依据的 cancel 动作。
- ApprovalView.checks 只保留 **confirmation** 信封；历史 reviewer_note/candidate_body/private_locator 即使仍在数据库里，也不会通过 HTTP 返回。
- binding 带项目、run、revision、intent、job、delivery 与 confirmed/active snapshot digest；revision 或 digest 不匹配时 **current=false**。resource_state 只有当前绑定且 job+delivery 匹配时才给 current_version，旧发布版本只能进 history，不能把当前待处理任务标成完成。

## 2. 原因与动作

### 2.1 安全原因类别

| 类别 | 典型来源 | 语义 |
| --- | --- | --- |
| missing_information | MISSING_INPUT / CONFLICTING_INPUT | 需要用户补信息 |
| repairable_composition | REVIEW_REVISE / 覆盖缺陷 | 可修编后重新审查 |
| true_drift | REQUIREMENT_DRIFT / HARD_REQUIREMENT_FAILED / PROMPT_REQUIRES_RECONFIRMATION | 与已确认需求不一致，需要新版本或重新确认 |
| content_rejection | REVIEW_REJECTED / UNSAFE_REQUEST / OUT_OF_SCOPE / SCOPE_REFUSAL | 内容或范围硬失败，不放行 |
| missing_evidence | MISSING_EVIDENCE / UNREADABLE_IMAGE / CANDIDATE_EXPIRED | 缺证据，不能猜测通过 |
| protocol_error | CONTRACT_ERROR / PROMPT_REVIEW_INVALID / REVIEW_INVALID | 协议或契约异常，停止并保留状态 |
| technical_failure | SERVICE_UNAVAILABLE / REVIEW_UNAVAILABLE / PROCESS_INTERRUPTED | 技术故障，零自动重试 |
| remote_unknown | GENERATION_UNKNOWN / RESULT_RETRIEVAL_PENDING / LOCAL_TRACKING_CLOSED | 远端未知，只读核实，不重发 |
| authorization_invalid | ACCOUNT_DISABLED / UNAUTHORIZED_ACTION / ASSET_UNAVAILABLE | 授权或资源访问失效 |

未知模板不会回显原文，使用固定安全中文文案；未知 issue code 默认缺信息，而不是猜测成功或失败。

### 2.2 动作效果

| 动作 | effect | read_only | requires_model_call | retry_mode |
| --- | --- | --- | --- | --- |
| verify_generation / recover_generation / recover_submission / retrieve_original_result | read_only | true | false | manual_only 或 none |
| compile_prompt / revise_prompt / review_candidate / retry_generation 等 | model_call | false | true | 手动恢复类为 manual_only |
| confirm_requirements / publish_delivery 等 | write | false | false | none |

所有动作的 automatic_retry 都是 false；动作列表仍来自 dispatch.available_actions，T13 没有新增取消或自动重试入口。

## 3. 绑定与资源状态

- binding.current 同时检查 **project.revision == run.requirement_revision** 与 job 的 **confirmed_snapshot_digest** 是否等于当前有效授权的 digest。任何一项变化都表示旧成功不能代表当前任务。
- resource_state 的 current_version 只在 current=true 且 job.id / delivery.asset_id 对应当前版本时填充。
- 当前已有发布但绑定失效时，resource_state.current_state=history_only，current_version=null，旧图只出现在 history。
- job 处于 created/submit_pending/submitted/remote_pending/result_fetched/approved 时为 pending；failed/rejected 为 failed；unknown 为 unknown；quarantined 为 blocked。未知状态不猜测为成功。
- 当前没有 run 时，task_state 仍返回 empty progress、当前项目 revision 和 none 资源状态，前端无需另建空状态协议。

## 4. HTTP 权限与正向/反向测试

测试文件 **backend/tests/test_v3_p1_t13_safe_projection.py** 共 16 项（6 个场景 + 9 类原因参数化 + 1 个补充动作映射）：

- 正常确认后读取 task_state：phase=prompt、stage=S07、binding.current=true、must_include=hard、background=soft、无百分比、compile_prompt 为 model_call 且无 cancel_generation。
- approval checks 注入 reviewer_note/candidate_body/private_locator 三个 sentinel 后，GET 响应中不出现 sentinel，checks 只含 confirmation。
- 未批准的 prompt_plan 候选 artifact 返回 payload=null，原始候选正文不出现在响应中。
- S12 failed + GENERATION_UNKNOWN + issue_message sentinel：reason 为 remote_unknown，severity=unknown，不读取原文，continue_workflow 为 model_call/manual_only。
- 旧成功版本 + 新 revision/digest：binding.current=false，resource_state=history_only，current_version=null；只有 revision 与 digest 恢复匹配时才重新成为 current。
- 普通用户 B 读 A 项目 404；管理员读 B 项目 404；匿名 401；禁用账号 401/403。
- S08 failed 与 S12 recovery 对同一持久化 issue 生成完全相同的 reason 对象。
- 参数化覆盖 9 类 reason 分类，并恢复 UNSAFE_REQUEST 等内容拒绝映射，保持 T07 既有契约。

## 5. 验收映射

| 验收点 | 结论 | 证据 |
| --- | --- | --- |
| WG3-AC-10 | 通过（服务端） | task_state 提供安全 reason、状态、约束强度与真实动作；只读核实/手动恢复/写动作分离，零自动重试、无假百分比 |
| FE3-AC-13 | 通过（服务端部分） | 明确区分失败/未知/内容未放行；只暴露 available_actions；旧成功不冒充当前完成 |
| FE3-AC-15 | 通过（服务端部分） | 卡片所需 reason 类别、hard/soft 约束、绑定与 current/history 均由服务端提供；按钮布局与交互留给 T15-T22 |
| 权限矩阵 | 通过 | A/B、管理员越权、匿名、禁用/失效会话、未批准资源均有 HTTP 正反测试 |
| 兼容性 | 通过 | 新增字段为可选扩展；vue-tsc 通过；T03/T65/T68 既有动作断言和全 P1 回归仍绿 |

## 6. 实际命令与结果

工作目录：**D:\Desktop\新建文件夹\.worktrees\t99\backend**；Python：**D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe**。

| 命令 | 结果 |
| --- | --- |
| pytest tests/test_v3_p1_t13_safe_projection.py -q | **16 passed in 6.43s** |
| pytest 全 P1 T02–T13 -q | **133 passed in 81.72s** |
| pytest T13 + T10/T11/T12 + t03/t65/t68/t06 -q | **73 passed in 61.65s** |
| pytest t59/t60/t71 -q | **9 passed in 10.75s** |
| ruff check projection/state/schemas/reasons/T13 测试 | **All checks passed!** |
| vue-tsc --noEmit -p tsconfig.json | **exit 0，无诊断** |

T13 定向测试不调用模型或图片服务；P1 回归使用计数 stub，但没有把计数汇总成统一账本，因此不写成真实 provider 账单。候选预算由既有 RevisionBudget 数据负责，T13 没有重置或退款代码。

## 7. 剩余未知

- 未重跑完整 backend 全量套件；证据边界是 T13 16 项、P1 133 项、受影响工作流组 73 项、隔离/所有权/会话 9 项及前端类型检查。
- 未调用真实模型、图片服务或浏览器，也未在生产数据库上执行迁移。
- **main/backend/app/workflow/dispatch.py** 的紧凑 _ACTIONS 表存在 35 处既有 E501；T13 未重排该表，因此 ruff 结论不包含该文件。
- 本票只关闭服务端投影契约；单聊天卡片、结果页、响应式/无障碍等 UI 验收仍属 T15-T22。
- 不声称 R3 或 V3 Release 已验收。

## 8. 结论

T13 已满足“安全原因、意图/版本绑定、执行进度、约束强度、当前/历史资源状态、真实动作、权限矩阵与旧消费者兼容”的服务端要求，可以进入 P4 的 UI 工作（T15-T22）。
