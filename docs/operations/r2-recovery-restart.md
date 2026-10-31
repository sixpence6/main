# R2 原任务核实、结果找回与重启恢复操作说明

- Issue: #68
- 父 Issue: #52
- 分支: ticket/68
- 代码提交: 6befb7357078b52c6ba1f9fd4613dba7192b692b
- 更新日期: 2026-11-01
- 适用阶段: R2。本地跟踪结案与新任务风险确认见 #69；R2 门禁见 #70。

## 1. 目的与边界

这份说明面向本地维护者和操作者，用来处理已受理的生成任务在执行中出现的四类情况：

- 编制提示词失败：任务停在 S07，计数和占用保留，图片服务没有调用。
- 远端结果未知：任务停在 S08 blocked GENERATION_UNKNOWN，计数和占用保留，系统不自动重发。
- 结果文件暂时取不回：任务停在 S08 blocked RESULT_RETRIEVAL_PENDING，可对原 locator 再取回。
- 进程中断或服务重启：启动修复只写本地事实；用户可核实，再显式找回原任务。

本说明不提供自动重试、后台轮询或“删除任务重来”的快捷方式。不得删除 generation_intents、generation_usage、generation_slots、generation_jobs、generations 中的记录来清额度或绕过限制；这些记录就是审计和恢复依据。

本说明不涉及真实模型供应商的计费、限流或远端任务保留政策。生产环境中的远端未知仍应由供应商控制台或人工渠道核实。

## 2. 状态与动作对照

| 场景 | 工作流位置与状态 | 可用动作 | 计数与槽位 | 系统是否自动调用模型 |
| --- | --- | --- | --- | --- |
| 提示词编制失败 | S07 failed | 修改需求（如界面提供）；按工作流重新确认 | 意图仍 accepted／executing，一次用量，槽位保留 | 否 |
| 远端结果未知 | S08 blocked GENERATION_UNKNOWN | 核实原任务、找回原结果 | 意图、一次用量、槽位全部保留 | 否 |
| 结果文件暂时取不回 | S08 blocked RESULT_RETRIEVAL_PENDING | 核实原任务、找回原结果 | 同上 | 否 |
| 进程中断、可证明未提交 | S08 blocked PROCESS_INTERRUPTED | 核实原任务、找回原结果 | 同上；找回复用原 job 提交一次 | 否，只有用户点击找回后才提交 |
| 明确终态失败 | S08 blocked GENERATION_FAILED | 核实原任务；不提供找回 | 意图 failed，一次用量保留，三个槽位已释放 | 否 |
| 账号被禁用 | 任务保持原归属，下一模型调用和发布被阻止 | 由维护者按 R1 恢复账号；不自动续发 | 保留原计数和占用直至可证终态 | 否 |

“一次用量”是受理时写入的滚动窗口计数，不会因为核实、找回、重试式点击或服务重启而减少。只有明确终态失败才释放槽位；远端未知、结果取回失败、账号禁用和进程中断都保留槽位。

## 3. 日常操作步骤

### 3.1 先读取工作流状态

打开项目的生成工作流页面，或直接调用：

~~~
GET /api/projects/{project_id}/workflow
~~~

确认：

- stage 是否为 S07／S08；
- status 是否为 active、failed 或 blocked；
- issue.code 是否为 GENERATION_UNKNOWN、RESULT_RETRIEVAL_PENDING、PROCESS_INTERRUPTED 或 GENERATION_FAILED；
- available_actions 是否包含 verify_generation／recover_generation；
- usage 是否仍显示“今日已受理 X / Y 次”和活跃槽位；
- intent 是否存在，状态是否为 accepted／executing／delivered／failed／abandoned。

GET 和页面刷新只读取工作流投影，不调用 LLM，也不调用图片适配器。

### 3.2 核实原任务

点击“核实原任务”，或提交工作流动作 verify_generation：

~~~
POST /api/projects/{project_id}/workflow/actions/verify_generation
{
  "expected_revision": <当前项目 revision>
}
~~~

核实是只读动作：

- 不调用 LLM；
- 不调用图片 submit／poll／fetch；
- 不新增意图、用量或槽位；
- 只确认当前 job、generation、intent 的持久化状态和可用恢复路径。

如果核实结果仍是未知或结果取回失败，保持 blocked；不要重复创建新项目或新任务来“绕过”占用。

### 3.3 找回原结果

点击“找回原结果”，或提交：

~~~
POST /api/projects/{project_id}/workflow/actions/recover_generation
{
  "expected_revision": <当前项目 revision>
}
~~~

找回动作按以下顺序处理原任务：

1. 如果 job 是 PROCESS_INTERRUPTED，且 generation.submit_attempted 为空、没有 remote_task_id、没有 result_locator_json：把原 generation 重置为 queued／waiting_slot，把原 job 重置为 submit_pending，然后调度原 job。只提交一次，不创建新 job、不新增用量。
2. 如果已有 remote_task_id：只 poll 原 remote_task_id，不提交新图片。poll 返回 pending 就保持等待；返回 unknown 就重新落到 GENERATION_UNKNOWN；返回明确 failed 就进入 GENERATION_FAILED 并释放槽位；返回 result_available 就记录 locator。
3. 如果只有 result_locator_json、没有 remote_task_id：用原 locator 做一次 fetch_result，不提交新图片。
4. 如果既没有远端标识也没有 locator：不能证明远端状态，保持 GENERATION_UNKNOWN，不猜测为“未受理”。

成功取得结果字节后，系统校验 sha256、把资产置为 quarantined，并进入 S09 内容审查；仍必须经过 S10 人工批准和发布，文件到达不等于发布。

### 3.4 明确终态失败

当供应商明确拒绝、明确失败或结果已被证明不可取回时，系统会把原意图标记 failed、保留一次用量、释放该意图的三个槽位，并显示 GENERATION_FAILED。

这时：

- 不要手工把槽位行改成 released；系统已在同一事务中完成；
- 不要删除 usage 行；一次计数必须保留；
- 页面只提供“核实原任务”，不提供自动重新生成；
- 如用户需要新的生成，应按正常流程重新确认需求，形成新的明确任务（#69 的边界）。

### 3.5 服务重启与进程中断

服务重启后：

1. 等待启动修复完成；启动修复只读本地数据库并写本地状态，不调用 LLM、不 poll、不 submit。
2. 打开工作流页面，确认 S08 是否 blocked GENERATION_UNKNOWN、RESULT_RETRIEVAL_PENDING 或 PROCESS_INTERRUPTED。
3. 先点“核实原任务”，再点“找回原结果”。不要重复重启来期待自动恢复；重启不会自动补发。
4. 如果是提交前中断，找回会复用原 job 并只提交一次；如果是提交后中断，找回只 poll 原 remote_task_id。
5. 找回后检查 usage 仍是原来的一次、intent 仍是原来的 id、槽位没有被清零或复制。

进程退出测试与 smoke 的重启验证都使用同一数据库和同一批临时 SQLite 文件；生产环境应按常规备份策略保护数据库，不要把删除数据库当作恢复手段。

## 4. 审计与可追踪性

相关审计事件：

- generation_intent_accepted：受理时写入，detail 记录请求、限额和前后用量；
- generation_intent_failed：明确终态失败时写入，result=failure，reason_code 记录 generation_terminal:*，同一事务释放槽位；
- generation_intent_abandoned：需求漂移或提示词需要重新确认时写入（#66），不删除用量；
- generation_account_blocked：账号禁用导致下一个模型调用／发布被阻止时写入（#62）。

核实动作本身不写业务账本，也不产生模型调用；找回动作只更新原 job／generation 的恢复事实，不新增受理记录。审计写入失败时，终态失败事务整体回滚，不会出现“状态已改但审计缺失”的静默完成。

## 5. 配置与运行前提

- 数据库迁移必须到 0009；本票没有新增迁移。启动时若提示数据库未迁移，先执行：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m alembic -c D:/Desktop/新建文件夹/backend/alembic.ini upgrade head
~~~

- 应用需要有效的 APP_DATA_DIR、WEB_DIST_DIR、APP_PUBLIC_URL；真实本地运行使用 127.0.0.1 与允许的 Host／Origin。
- 生成额度仍按 #67 的配置解析：账号覆盖 > 实例覆盖 > Settings 默认值；本票不改变额度默认值。
- 图片模型必须配置 LLM_VISION_MODEL 等必要字段，否则提交前会以 CAPABILITY_NOT_READY 拒绝，不会产生远端任务。
- 恢复动作不要求新的配置；它使用原 job 保存的 adapter、model、remote_task_id、result_locator 和原 prompt 摘要。

## 6. 验证命令

完整的 #68 验证命令和结果见 docs/acceptance/r2-recovery-restart.md。日常排障可先跑：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings tests/test_t68_recovery.py
~~~

前端：

~~~
npm test -- --reporter=dot tests/t68-workflow-recovery.spec.ts
~~~

真实 HTTP／临时 SQLite smoke 入口：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t68-smoke/smoke68.py
~~~

smoke 使用 fake LLM／Visual，但走真实 uvicorn、真实 HTTP、完整 App。不要把 fake 结果当作真实供应商行为。

## 7. 禁止事项

- 不要删除或改写 generation_intents、generation_usage、generation_slots、generation_jobs、generations 中的未知／失败记录来“重置”额度。
- 不要把 GENERATION_UNKNOWN 当作远端未受理；不要据此自动提交第二次图片任务。
- 不要仅因为超时就释放槽位或判定失败；只能依据明确终态事实。
- 不要在未确认原任务归属和账号状态时手动 ALTER 数据库。
- 不要把核实／找回包装成自动重试；它们必须由用户显式触发。
- 不要绕过 S09 内容审查或 S10 人工批准直接展示／发布 quarantined 图片。

## 8. 回滚与故障处理

- 本票无数据库迁移；回滚代码不会自动删除已有意图、用量、槽位或任务行。已 blocked／unknown 的原任务应继续保留，按本说明核实和找回。
- 若恢复动作返回 RECOVERY_NOT_AVAILABLE，说明原 job 不存在或已不属于该项目；先核对项目归属和 job id，不要新建替代任务。
- 若找回返回 ADAPTER_UNAVAILABLE，说明图片适配器配置缺失或不完整；先修复配置，再核实；不要调用生成接口补发。
- 若找回返回未知，保持任务和占用，稍后再次核实；不要清除额度。
- 若审计或数据库事务报错，检查数据库完整性和日志；任何终态状态都应与审计在同一事务中提交。
