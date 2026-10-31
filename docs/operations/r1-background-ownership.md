# R1 后台任务持久归属与禁用后截断操作说明

- **适用版本**：v3 R1，ticket #62（代码提交 8941b5386e18e5c007c92691a14e169175286ab6 及之后的文档提交）
- **适用对象**：部署／运维人员、管理员、普通用户
- **前置阶段**：C0 备份与维护基线（#54）、R1 认证与账号（#55–#58）、项目归属隔离（#59）、参考图与工作流归属闭环（#60）、历史与真实字节门禁（#61）已合并
- **状态**：本说明只覆盖后台任务持久归属、账号禁用后的截断、恢复不自动续发和发布前重验；不覆盖旧数据认领或 R1 全阶段验收

## 1. 范围与硬边界

本票上线后，服务端执行：

1. 新生成任务在数据库中持久化 owner_account_id、operator_account_id 和 authorization_ref；任务归属不再依赖当前浏览器账号。
2. 每次模型调用、远端轮询、取回结果和发布写入前重新校验 owner／operator 的可用状态与授权摘要。
3. 账号被禁用后，不再发起新的模型调用或发布；已经发往远端的请求保留 remote_task_id 和相关状态，不宣称取消，也不把已返回内容当成已发布。
4. 恢复账号只恢复登录和正常业务访问；旧任务不会自动重发、自动轮询或自动发布。
5. 被禁用期间已经存在的结果资产保持 rejected／隔离状态，不能通过页面、历史或下载接口绕过字节门禁。
6. generation_account_blocked 作为安全事件记录，个人和管理员视图只展示安全字段。

明确不允许：

- 为了让后台任务继续而手工改 owner_account_id、operator_account_id 或 authorization_ref。
- 通过删除任务、清空 remote_task_id 或改数据库状态来“修复”禁用后的 unknown。
- 在恢复账号后自动重新调度旧任务或批量审批旧结果。
- 用管理员账号代看或代发普通用户的生成结果；管理员没有业务数据读取豁免。
- 把真实视觉供应商密钥写入日志、数据库明文字段或冒烟脚本输出。
- 在真实业务数据上做本票演练；演练使用临时 DATA_DIR。

## 2. 前置条件

| 项 | 要求 |
|---|---|
| 服务版本 | 已包含 #55–#62 的构建 |
| 数据库 | SQLite；迁移版本为 0006；本票新增 0006 |
| 迁移树 | main/backend 与 backend 两处迁移文件一致；不得修改已应用的 0003／0004／0005／0006 |
| 配置 | APP_DATA_DIR 与 WEB_DIST_DIR 分离；APP_PUBLIC_URL、ALLOWED_HOSTS、ALLOWED_ORIGINS 已按部署配置 |
| 模型适配器 | 正式环境需配置真实视觉适配器；测试／演练可用暂停的假适配器 |
| 账号 | 至少一个可用管理员；普通用户通过管理员创建并完成一次性链接设置 |
| 备份 | 升级前按 docs/operations/maintenance-backup.md 完成一致性备份并验证 |
| 数据 | 禁止在真实业务数据上做验收演练；演练使用临时 DATA_DIR |

## 3. 操作步骤

### 3.1 升级前备份

1. 按 docs/operations/maintenance-backup.md 停止业务写入或进入维护窗口。
2. 使用 C0 维护 CLI 生成一致性备份，确认 SQLite、引用文件、生成文件与清单校验通过。
3. 记录备份目录和校验摘要；不要在未验证备份的情况下继续。

### 3.2 部署与迁移

1. 部署包含代码提交 8941b5386e18e5c007c92691a14e169175286ab6 的构建。
2. 在 main/backend 目录执行 alembic current，确认目标为 0006；新库执行 alembic upgrade head，已有 0005 的库执行 alembic upgrade 0006。
3. 确认两份迁移文件内容一致；不要手工修改 migration 文件或直接改表结构。
4. 启动正式入口 uvicorn app.main:app，确认 /api/health 返回 200。

### 3.3 验证退出后原任务继续

使用临时 DATA_DIR 和两个普通账号：

1. owner 创建项目，提交生成并使任务进入运行。
2. owner 退出登录；确认任务仍保留原 owner_account_id、operator_account_id、authorization_ref。
3. 等待任务完成；预期任务进入 quarantine 或同等待审状态，结果属于原 owner，另一个账号和管理员读取项目均 404。
4. 不要把退出解释为取消；也不要用另一个浏览器账号继续操作该任务。

### 3.4 验证禁用后的截断

使用暂停的假视觉适配器：

1. owner 提交生成，使 submit 或 poll 处于暂停状态。
2. 管理员通过 POST /api/admin/accounts/{account_id}/disable 禁用 owner。
3. 预期：不再发起新的 poll／fetch；generation 和 job 变为 unknown（已有 remote_task_id 时）或 failed（尚未记录远端事实时）；error_code 为 ACCOUNT_DISABLED；remote_task_id 保留；result_asset_id 为空；S08 运行标记 blocked ACCOUNT_DISABLED。
4. owner 的既有会话下一次请求应 401；其他账号和管理员读取该项目应 404。
5. 恢复账号后登录；项目和工作流可读取，但旧 job 仍为 unknown，remote_task_id 仍在，poll_calls 为空，不得自动重发或自动发布。

### 3.5 验证发布前重验

1. 准备一个已批准但尚未发布的 Delivery。
2. 在 publish_delivery 请求处理期间禁用 owner。
3. 预期发布请求返回 409 ACCOUNT_DISABLED；Delivery 保持 approved；published 目录没有新文件；owner 下一次请求 401。
4. 不要通过手工把 Delivery 改为 published 或复制文件来绕过；先恢复账号并由本人重新走当前工作流。

### 3.6 验证审计事件

具有管理员权限的账号读取 GET /api/admin/security-events，按 generation_account_blocked 过滤：

- 事件应有 result=denied。
- detail 只应包含 stage 和 remote_task_recorded 等安全字段。
- 不应出现密码、会话令牌、模型密钥、完整 provider 错误或完整图片内容。
- 普通用户只能读取自己的安全事件；不得读取他人项目、任务或管理员事件。

### 3.7 停用与恢复的用户沟通

管理员应告知用户：

- 停用会立即阻止新请求、撤销会话和未使用的一次性链接。
- 恢复不会复活旧会话、旧链接或旧后台任务。
- 需要继续生成时，由本人在恢复后重新确认需求并发起新的生成授权。
- 已发远端任务可能保留供应商侧记录，但应用不会宣称取消，也不会越权发布其结果。

## 4. 失败处理

| 现象 | 处理 |
|---|---|
| 启动或迁移显示版本低于 0006 | 先在 main/backend 执行 alembic upgrade head；确认两份迁移树一致；不要手工加列或跳过版本。 |
| 禁用后任务显示 unknown | 正常的安全截断；检查 remote_task_id 是否保留，不要改成 failed 来掩盖远端事实。 |
| 生成请求返回 409 ACCOUNT_DISABLED | 该账号已停用；恢复后不会自动续发，由本人重新发起新授权。 |
| 发布返回 409 ACCOUNT_DISABLED | owner 在发布前被禁用或授权摘要不匹配；确认 Delivery 仍为 approved，不要手工改为 published。 |
| 发布目录出现空目录但没有文件 | 正常回滚结果；核对 Delivery 状态和错误 body，不要复制旧文件顶替。 |
| 管理员看到 generation_account_blocked | 正常审计；按 detail 中 stage／remote_task_recorded 判断是否已有远端事实。 |
| 恢复后旧任务仍 blocked／unknown | 预期行为；恢复只恢复登录和正常访问，不自动重发。需要继续时重新走当前工作流。 |
| 真实供应商已返回但本地未保存 | 不要把未验证内容写成 approved／published；按当前结果门禁和供应商协议另行处理。 |

## 5. 回滚

本票有新增迁移 0006。建议优先保留 0006 schema 并回退应用代码到 #61 构建，因为旧代码可以忽略新增的可空列，避免无谓降级；若必须降级：

1. 停止服务并完成一致性备份。
2. 在隔离环境演练 alembic downgrade 0005 与恢复；确认不会删除仍需要的业务数据。
3. 部署上一版 #61 构建，启动后执行健康检查、本人项目读取和一次生成任务查询。
4. 保留审计记录、任务记录和已发布文件；不要删除它们来“恢复干净”。
5. 任何数据库降级或文件清理都必须按 #54 维护流程执行，不得直接操作真实数据根。

## 6. 证据与复核

- 验收记录：docs/acceptance/r1-background-ownership.md
- 目标测试：backend/tests/test_t62_background_ownership.py（5 passed in 4.93s）
- 相关回归：10 passed in 10.87s；更早相关组合 68 passed in 91.90s
- 全量后端：530 passed, 3 warnings in 581.90s，日志 .scratch/t62-backend-full.log
- 迁移证据：.scratch/t62-migration/result.json（fresh_version 0006；0005 -> 0006 保留检查通过）
- 前端：33 个测试文件、206 个测试通过；vue-tsc exit 0；Vite build 757ms
- 真实 HTTP：.scratch/t62-background-smoke/result.json；服务日志 .scratch/t62-background-smoke/uvicorn.log
- 冒烟脚本：.scratch/t62-background-smoke/smoke.py、seed.py、smoke_app.py
- 代码提交：8941b5386e18e5c007c92691a14e169175286ab6

真值说明：真实 HTTP 冒烟使用假视觉适配器；它验证应用侧持久事实、HTTP 行为和文件边界，不证明真实供应商取消／轮询协议。正式环境必须按实际供应商协议和当前工作流门禁复核。
