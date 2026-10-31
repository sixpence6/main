# R2 本地跟踪结案与风险确认新任务运维说明

- Issue: #69
- 父 Issue: #52
- 分支: ticket/69
- 代码提交: 92f4c9d1150acb299adc5a1aa89e83b0ea4ca83a
- 更新日期: 2026-11-01
- 适用范围: R2 生成任务在远端结果未知或暂时无法找回时的本地结案，以及明确风险确认后的新任务

## 1. 目的与边界

本说明面向运行维护和值班人员，说明如何识别 S08 blocked 任务、如何安全结束本地跟踪、如何在用户明确确认风险后启动一个重新计数的新任务，以及如何核对审计、额度和槽位。

必须明确：

- “结束本地跟踪”只结束本系统对原任务的追踪，不取消远端供应商任务，不代表未受理，也不能阻止远端继续执行或计费。
- 系统不会自动重发、不会自动轮询、不会自动把 unknown 解释成远端未受理。
- 新任务不是原任务的重试，而是新的受理：新 request_id、新意图、新计数、新槽位，必须重新满足当前需求和额度。
- 两种动作都只通过项目工作流动作调用，不新增直连生成接口；不要手工改数据库状态。

## 2. 状态与可用动作

| 运行状态 | issue_code | 可用动作 | 含义 |
| --- | --- | --- | --- |
| S08 blocked | GENERATION_UNKNOWN | 核实原任务、找回原结果、结束本地跟踪 | 远端结果未知，原意图／一次计数／槽位保留 |
| S08 blocked | RESULT_RETRIEVAL_PENDING | 核实原任务、找回原结果、结束本地跟踪 | 结果文件暂时无法找回，原意图／一次计数／槽位保留 |
| S08 blocked | PROCESS_INTERRUPTED | 核实原任务、找回原结果 | 本地中断，先核实或找回原任务；不提供直接结案 |
| S08 blocked | GENERATION_FAILED | 核实原任务 | 明确终态失败，计数保留、槽位已释放，不提供自动重生 |
| S08 blocked | LOCAL_TRACKING_CLOSED | 确认风险并生成新任务 | 原跟踪已结案，原计数保留、原槽位释放 |
| S08 active | 无 | 提交生成 | 正常执行路径，不需要人工结案 |

查看方式：项目页工作流面板会显示阶段、状态、issue 和按钮；接口为 GET /api/projects/{project_id}/workflow。

## 3. 结束本地跟踪

### 3.1 前置检查

1. 确认任务处于 S08 blocked，issue 为 GENERATION_UNKNOWN 或 RESULT_RETRIEVAL_PENDING；PROCESS_INTERRUPTED 只能核实／找回，GENERATION_FAILED 只能核实，均不能直接结案后重开。
2. 确认当前登录账号是项目所有者；管理员没有业务读取豁免，也不能替其他账号读取或操作项目。
3. 确认页面显示“原受理计数已保留；本系统未取消远端任务……”。不要向用户承诺远端已取消或不再计费。
4. 如账号已禁用，先按 R1 账号恢复流程处理；恢复后用户必须重新登录，系统不会自动续发。

### 3.2 页面操作

1. 打开项目工作流页，找到 S08 blocked 提示。
2. 点击“结束本地跟踪”。
3. 勾选“我确认结束本地跟踪；原受理计数保留，且这不代表取消远端任务”。
4. 确认提交。

### 3.3 HTTP 等价操作

请求：

~~~
POST /api/projects/{project_id}/workflow/actions/close_generation_tracking
Content-Type: application/json
X-CSRF-Token: <session csrf>
Origin: <app origin>

{
  "expected_revision": <项目当前 revision>,
  "payload": {
    "acknowledge_local_close": true
  }
}
~~~

成功返回 200 和工作流视图，stage=S08、status=blocked、issue.code=LOCAL_TRACKING_CLOSED，available_actions 只包含 start_new_generation。

可能错误：

- 409 ACTION_NOT_AVAILABLE：当前不是可结案的不确定状态。
- 409 LOCAL_CLOSE_ACK_REQUIRED：缺少 acknowledge_local_close=true。
- 409 INTENT_STATE_INVALID：原意图不是 accepted／executing（可能已经结案或失败）。
- 409 OWNERSHIP_MISMATCH：当前账号不是意图所有者。
- 401 AUTH_REQUIRED：未登录、会话过期或账号被禁用。
- 409 PROJECT_BUSY：同一项目同时有另一个写操作，稍后重试并先刷新。

### 3.4 结果核对

结案后应满足：

- GenerationIntent.status = closed。
- GenerationUsage 行数不变，原一次受理仍在滚动窗口内。
- 原意图的三个 GenerationSlot 全部 released_at 非空，release_reason=local_tracking_closed。
- AuditEvent 新增一条 generation_tracking_closed，result=success，before active_slots=3，after active_slots=0，detail.remote_cancelled=false。
- 不新增 Generation、GenerationJob、图片文件或 provider 调用。

核对 SQL（只读）：

~~~
select id, status, request_id from generation_intents where project_id = '<project-id>' order by created_at;
select count(*) from generation_usage where account_id = '<owner-account-id>';
select scope, released_at, release_reason from generation_slots where intent_id = '<intent-id>';
select event_type, result, before_json, after_json, detail_json
from audit_events where event_type = 'generation_tracking_closed' order by created_at desc limit 1;
~~~

## 4. 确认风险并生成新任务

### 4.1 前置检查

1. 当前必须是 S08 blocked LOCAL_TRACKING_CLOSED，且原任务已经有 closed 意图。
2. 页面必须显示原受理计数保留、未取消远端任务；用户必须理解新任务会重新计数并占用槽位，旧任务可能仍在远端执行或计费。
3. 检查当前项目 revision、最终需求卡、参考资产、尺寸没有被修改；若需求已变化，先回到 U1 修改并重新确认需求，不要用旧卡直接新任务。
4. 检查个人滚动用量、个人槽位、实例槽位、单项目槽位；额度不足会在创建前 429，不会排队或自动重试。
5. 账号必须 active；禁用账号返回 401。不要用管理员账号代替用户创作。

### 4.2 页面操作

1. 在 LOCAL_TRACKING_CLOSED 提示下点击“确认风险并生成新任务”。
2. 勾选“我确认这是新任务，将重新计数并占用槽位；旧任务仍可能在远端执行或计费”。
3. 确认提交。页面随后进入 S07 编制／审查、S08 提交、S09 质量审查、S10 交付。

### 4.3 HTTP 等价操作

~~~
POST /api/projects/{project_id}/workflow/actions/start_new_generation
Content-Type: application/json
X-CSRF-Token: <session csrf>
Origin: <app origin>

{
  "expected_revision": <项目当前 revision>,
  "payload": {
    "acknowledge_risk": true,
    "previous_generation_id": <可选，原 Generation id；省略时服务端使用当前任务>
  }
}
~~~

成功返回 200 和工作流视图；会创建新的 intent／usage／三个 slot，并以 attempt=2 关联原任务。

可能错误：

- 409 ACTION_NOT_AVAILABLE：当前不是 LOCAL_TRACKING_CLOSED，或已经启动过新任务。
- 409 RISK_ACK_REQUIRED：缺少 acknowledge_risk=true 或 previous_generation_id 不匹配。
- 409 INTENT_STATE_INVALID：原意图不是 closed，或新意图已不再可执行。
- 429 GENERATION_LIMIT_REACHED：个人滚动 24h 额度已满；不产生新意图／用量／槽位，不调用模型。
- 429 GENERATION_SLOT_BUSY：个人、实例或项目活跃槽位已满；不排队。
- 409 REQUIREMENTS_CHANGED／CARD_DIGEST_MISMATCH／REQUIREMENT_REVISION_CONFLICT：当前需求或卡片已变化，回 U1 重新确认。
- 401 AUTH_REQUIRED：账号被禁用或会话失效。

### 4.4 结果核对

- 原 intent 仍为 closed，原 usage 保留。
- 新 intent 为新 request_id，status 为 accepted 或 executing；新 usage 增加一行；新三个 slot 占用。
- 新 GenerationJob.attempt=2，previous_generation_id 指向原任务；原 job 仍保留 unknown／历史。
- 图片提交只发生一次；S09 通过后才进入 S10，人工 approve／publish 后才是可交付结果。
- 重复点击或重复请求返回 409，不会重复创建。

核对 SQL：

~~~
select id, status, request_id, created_at from generation_intents where project_id = '<project-id>' order by created_at;
select count(*) from generation_usage where account_id = '<owner-account-id>';
select scope, released_at, release_reason from generation_slots where intent_id = '<new-intent-id>';
select id, status, attempt, previous_generation_id from generation_jobs where project_id = '<project-id>' order by created_at;
~~~

## 5. 审计与视图

- 本人视图只显示本人账号的用量、限制和本人项目的安全事件；不会显示其他账号的项目或额度明细。
- 管理员账号列表可看到账号状态、额度调整和 generation_tracking_closed 事件，但不显示项目内容。
- generation_tracking_closed 事件详情包含 before／after 活跃槽位和 remote_cancelled=false；不要把它误读为“远端已取消”。
- 审计写入与状态变更在同一事务；若审计失败，结案或额度变更应整体回滚，不能静默完成敏感变更。
- 禁止直接修改 generation_intents.status、generation_slots 或 usage 行来“修复”状态；必须使用工作流动作，保留审计链。

## 6. 重启、禁用与额度

- 服务重启后启动修复只写本地状态，不自动 poll／submit；unknown 任务仍保持 blocked，等待用户核实／找回或结案。
- 账号禁用会阻止下一模型调用和发布，并撤销会话；不会自动取消远端任务，也不会自动释放原占用。恢复账号后必须重新登录并显式操作。
- 降低额度不会取消已受理任务或清零已用记录；只影响后续新受理。
- 滚动窗口为 UTC 毫秒 (now-24h, now]，恰好下界不计入；跨零点、刷新、重启都不清零。
- 没有余额、计费或费用封顶承诺；本系统只限制受理次数和活跃槽位。

## 7. 迁移与回滚边界

- 当前 head 为 0010；0010 只扩展 generation_intents.status 的 CHECK 以允许 closed。
- 升级命令：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m alembic -c D:/Desktop/新建文件夹/backend/alembic.ini upgrade head
~~~

- 回滚会把 closed 映射为 abandoned，再恢复旧约束；不得删除 intent、usage、slot、audit 行。生产回滚前必须先停止服务并做已验证备份，具体按 maintenance 运维流程执行。
- 两个迁移树必须保持字节一致：main/backend/migrations/versions 和 backend/migrations/versions。

## 8. 故障排查

| 现象 | 检查 | 处理 |
| --- | --- | --- |
| 页面没有“结束本地跟踪” | issue 是否为 GENERATION_UNKNOWN／RESULT_RETRIEVAL_PENDING | 刷新工作流；PROCESS_INTERRUPTED 只能核实／找回，GENERATION_FAILED 只能核实 |
| 结案 409 LOCAL_CLOSE_ACK_REQUIRED | 请求 payload 是否缺少 acknowledge_local_close | 页面重新勾选后提交 |
| 结案 409 INTENT_STATE_INVALID | 意图是否已 closed／failed／abandoned | 刷新；不要重复提交 |
| 新任务 429 | used／daily_limit、personal／instance／project active | 等窗口或释放槽位；不要绕过额度 |
| 新任务 409 REQUIREMENTS_CHANGED | 项目 revision、卡片摘要、参考、尺寸是否变化 | 回 U1 修改并重新确认，不要伪造旧卡 |
| 页面显示成功但无图片 | S09／S10 是否仍需人工审查或批准 | 按质量审查和交付流程处理；文件到达不等于发布 |
| 重启后仍 unknown | 这是预期保守状态 | 使用“核实原任务”或“找回原结果”，不要直接重建任务 |

## 9. 验证清单

- [ ] 结案后 usage 不变，三槽释放，intent=closed，审计一条且 remote_cancelled=false。
- [ ] 结案/新任务重复或并发只成功一次，未产生额外 provider 调用。
- [ ] 新任务使用新 request_id、attempt=2，usage 再计一次，三新槽占用。
- [ ] 旧任务历史仍在，未删除、未篡改、未补造操作者或批准。
- [ ] 当前需求、参考、尺寸未变化；若变化已回 U1 重新确认。
- [ ] 未触碰真实数据根，未绕过审计或额度。
