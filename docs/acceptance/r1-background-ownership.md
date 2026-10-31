# R1 后台任务持久归属与禁用后截断验收记录

- **Issue**：#62 V3 T10 [R1]：后台任务持久归属与禁用后截断
- **父 Issue**：#52 V3 规格实施
- **分支**：ticket/62（基于 integration/v3 935b025）
- **代码提交**：8941b5386e18e5c007c92691a14e169175286ab6
- **验收日期**：2026-10-31
- **状态**：本票实现、目标测试、相关回归、全量后端／前端、构建与真实 HTTP 冒烟均通过；合并由 integration/v3 执行

## 1. 结论与边界

本票在 #55—#61 的认证、项目归属、参考图／工作流归属和历史字节门禁之上，把后台生成任务的真实所有者、实际操作者和授权关联持久化到数据库，不再依赖当前浏览器会话账号。每次新的模型调用、远端轮询／取回以及发布落盘前都会重新校验持久归属与账号可用状态；账号被禁用后，后续模型调用与发布被阻断，但已经发往远端的任务事实（remote_task_id）保留，不伪称取消或删除。恢复账号不会自动续发旧任务。

本票只承担 ticket #62 的阶段条件，不代表整个 R1 已完成。明确未覆盖：

| 未覆盖项 | 归属 |
|---|---|
| 来源核验的幂等旧数据认领 | #63 T11 |
| R1 全阶段真实路由全清单、跨专题矩阵、全制品秘密标记扫描 | #64 T12 R1-GATE |
| 交付、审批、历史、恢复的完整 R2／R3 跨账号与跨版本矩阵 | #65–#70（R2）、#71–#77（R3） |
| 真实供应商侧的取消语义、供应商 locator 协议和真实模型生成闭环 | 后续 R2／R3 与 Release 阶段；本票只验证应用侧不伪称取消、不越权发布 |
| 浏览器前端对停用状态的全部提示与轮询行为 | R3 工作台投影与最终发布验证 |

真实 HTTP 冒烟使用暂停的 VisualStub 假适配器，验证真实 HTTP、完整 App 和临时 SQLite 下的后台任务控制与持久事实；没有连接真实视觉供应商。发布场景的 Delivery 夹具直接写入临时数据库，用于在发布请求进行中触发禁用竞争，不代表真实供应商生成结果。该边界在第四节和第六节如实标注。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t62 |
| 分支／基座 | ticket/62 / integration/v3 935b025 |
| 代码提交 | 8941b5386e18e5c007c92691a14e169175286ab6 |
| 操作系统 | Windows（本地开发环境） |
| Python | 3.12.13（backend/.venv） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 数据库 | SQLite 临时数据目录（aiosqlite，WAL）；迁移版本 0006 |
| Web | 真实 uvicorn + 构建后的 dist；不使用真实业务数据 |
| 模型 | 测试用 LLM／Visual 假适配器；真实 HTTP 冒烟使用暂停的 VisualStub |

## 3. 实现摘要

### 3.1 持久化任务归属和授权

- main/backend/app/workflow/tables.py 的 GenerationJob 增加 owner_account_id、operator_account_id、authorization_ref，并增加 owner_account_id 与 created_at 的索引。
- main/backend/app/models.py 的 Generation 增加相同三列，兼容旧 v1 生成路径。
- 新迁移 0006 同时放在 main/backend/migrations/versions/0006_r1_generation_task_ownership.py 和 backend/migrations/versions/0006_r1_generation_task_ownership.py：batch_alter_table 增加列、指向 accounts 的外键和索引，并按 projects.owner_account_id 回填已有 Generation 与 GenerationJob 的 owner_account_id。owner 外键为 RESTRICT，operator 外键为 SET NULL，允许旧数据和已删除操作者保留可审计事实。
- main/backend/app/generation_guard.py 提供统一校验：任务或生成记录的 owner 必须与项目 owner 一致；owner 必须存在且 can_login；operator 存在时必须 can_login；authorization_ref 必须与服务端重算结果一致；违反时抛出带稳定 code 的 GenerationGuardError。

### 3.2 任务创建时写入服务端事实

- S08 的 _create_job 从 ctx.project 写入 owner_account_id，从 ctx.principal 写入 operator_account_id，并用项目、工作流 run、request_id、request_digest 和 confirmed_snapshot_digest 计算 authorization_ref；客户端传来的 owner／role／approval 字段不能被当作授权。
- S11 和 s11_retrieval 的 GenerationJob 创建／更新也写入 owner、operator 与 authorization_ref，覆盖旧图片发布和找回结果落库路径。
- 迁移回填只解决历史 owner 归属；操作者和授权关联仍由新任务创建时写入，旧任务缺 operator 时按可空字段处理，不伪造操作者。

### 3.3 每次模型调用与发布前重验

- main/backend/app/workflow/roles.py 的 run_role 和 run_role_request 在每一次工作流模型调用前调用 ensure_project_owner_active；账号停用时返回 409 ACCOUNT_DISABLED，不会发出模型请求。
- main/backend/app/services/generations.py 的 _execute_claimed 在适配器提交前、_poll_until_result 在每次 poll 前、_deliver_result 在 fetch_result 前和最终写事务内校验 generation 的持久 owner／operator／authorization_ref；失败时按是否已有 remote_task_id 将状态置为 unknown 或 failed，保留错误码 ACCOUNT_DISABLED／ACCOUNT_INACTIVE。
- S08 的 _run_job 在开始和结果处理阶段校验 job；账号禁用时 _block_for_account 将任务和生成置为安全状态，把结果资产标记为 rejected，并把运行阻塞在 S08 ACCOUNT_DISABLED。已有 remote_task_id 时保留远端事实，不宣称取消；没有远端事实时为 failed。
- S10 的 _publish 在写发布文件前和最终事务内重验 job 的 owner／operator／授权关联；禁用账号的发布请求返回 409 ACCOUNT_DISABLED，不写入 published 文件，不改变 Delivery 为 published。

### 3.4 恢复不自动续发

S08 的 ACCOUNT_DISABLED 不在可自动 recover 的阻塞码集合内；恢复账号只恢复登录和项目浏览，不会重新调度、重发或自动审批旧任务。后台任务保持 unknown／failed 及其 remote_task_id、error_code，等待用户自行重新发起新的生成授权。

### 3.5 审计与安全视图

main/backend/app/auth/security_events.py 将 generation_account_blocked 加入安全事件类型，并只暴露 stage、remote_task_recorded 等安全 detail 字段。禁用导致的后台停止在审计中记录 system actor、目标账号、generation_job 目标 ID 和脱敏 subject_ref；个人与管理安全事件视图不返回会话令牌、密码、模型密钥或原始 provider 错误。

### 3.6 测试覆盖

backend/tests/test_t62_background_ownership.py 共 5 个测试，使用真实 ASGI App、临时 SQLite、AuthClient、暂停的 VisualStub 和真实管理员账号：

1. 退出不改变持久任务归属，原任务继续到 quarantine。
2. 禁用阻断下一次远端调用，保留 remote_task_id，不发布结果资产；恢复后不重发。
3. 任务创建持久化服务端授权，不接受客户端伪造 owner／is_admin。
4. 禁用 owner 时发布预检失败，不产生 published 文件。
5. 禁用 owner 时工作流模型调用被拒绝，LLM 没有收到请求。

## 4. 执行命令与结果

### 4.1 目标测试

工作目录：D:/Desktop/新建文件夹/.worktrees/t62/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t62-temp TMP=D:/Desktop/新建文件夹/.scratch/t62-temp     D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t62-temp-basetemp tests/test_t62_background_ownership.py

结果：5 passed in 4.93s，exit 0。

### 4.2 相关回归

同一工作目录：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings       tests/test_v2_t48_retrieval.py tests/test_t62_background_ownership.py

结果：10 passed in 10.87s，exit 0。

更早的相关组合（t62、t11、t13、t17、t20、t44、契约测试）结果：68 passed, 1 warning in 91.90s，exit 0。

### 4.3 全量后端回归

工作目录：D:/Desktop/新建文件夹/.worktrees/t62/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t62-pytest TMP=D:/Desktop/新建文件夹/.scratch/t62-pytest     D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t62-final-basetemp-4       > D:/Desktop/新建文件夹/.scratch/t62-backend-full.log 2>&1

结果：530 passed, 3 warnings in 581.90s (0:09:41)，exit 0。日志保存在 .scratch/t62-backend-full.log；warning 仅为测试临时目录 cwd 提示。

### 4.4 前端回归、类型检查与构建

工作目录：D:/Desktop/新建文件夹/.worktrees/t62

    D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --reporter=dot
    D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p D:/Desktop/新建文件夹/.worktrees/t62/tsconfig.json
    D:/Desktop/新建文件夹/node_modules/.bin/vite.cmd build

结果：33 个测试文件、206 个测试通过，22.78s；vue-tsc exit 0；Vite 构建成功 757ms，输出 dist/index.html、dist/assets/index-CoQKJWll.css（412.64 kB）、dist/assets/index-B9cC436c.js（1,175.91 kB）。仅有既存 chunk > 500 kB 警告。

本票未新增或修改前端代码；前端回归用于确认后端改动没有破坏现有页面与类型。

### 4.5 静态检查

对 generation_guard.py、auth/security_events.py、models.py、services/generations.py、workflow/roles.py、workflow/stages/s08.py、workflow/stages/s10.py、workflow/stages/s11.py、workflow/stages/s11_retrieval.py、workflow/tables.py 和 test_t62_background_ownership.py 执行 Ruff，结果 All checks passed。tables.py 中既存超长行不计入本票新增问题。

### 4.6 迁移证据

脚本：.scratch/t62-migration/migration_check.py
结果：.scratch/t62-migration/result.json

| 检查项 | 结果 |
|---|---|
| 全新建库 alembic upgrade head | fresh_version 0006 |
| generations 新列 | owner_account_id、operator_account_id、authorization_ref 存在 |
| generation_jobs 新列 | owner_account_id、operator_account_id、authorization_ref 存在 |
| owner／operator 外键 | owner -> accounts RESTRICT；operator -> accounts SET NULL |
| 索引 | ix_generations_owner_created、ix_generation_jobs_owner_created 存在 |
| 0005 -> 0006 保留检查 | 既有 account、project、workflow_run、generation、generation_job 保留，project owner 保留，generation／job owner 从 projects 回填 |
| 两份迁移文件 | migration_copies_identical true |

### 4.7 真实 HTTP + 临时 SQLite 冒烟

脚本：.scratch/t62-background-smoke/smoke.py
结果：.scratch/t62-background-smoke/result.json
服务日志：.scratch/t62-background-smoke/uvicorn.log

环境：完整 App，uvicorn 127.0.0.1:18774，DATA_DIR=.scratch/t62-background-smoke/data，WEB_DIST_DIR=.worktrees/t62/dist，APP_PUBLIC_URL=http://127.0.0.1:18774，ALLOWED_HOSTS=127.0.0.1/localhost，ALLOW_LOOPBACK_HTTP=true，AUTH_COOKIE_SECURE=false。管理员、owner62 与 other62 均通过临时 SQLite 建立；所有业务数据在临时目录。

| 场景 | 结果 |
|---|---|
| 匿名 GET /api/projects | 401 |
| 任务运行中退出登录 | logout 204；原任务继续并完成到 job=quarantined、generation=succeeded；owner／operator／authorization_ref 仍为原所有者事实 |
| 暂停提交后管理员禁用 owner | generation=unknown、error=ACCOUNT_DISABLED、stage=done；job=unknown、error=ACCOUNT_DISABLED；remote_task_id=remote-62 保留；result_asset_id=null；poll_calls 为空 |
| 禁用后 owner 访问项目 | 401；other／admin 读取项目均 404 |
| 恢复账号后读取项目／工作流 | 200；S08 仍 blocked ACCOUNT_DISABLED；job 仍 unknown，remote-62 保留，没有自动重发 |
| 已批准未发布 Delivery 在发布请求进行中触发禁用 | 发布请求 409 ACCOUNT_DISABLED；Delivery 仍为 approved；published 目录为空；owner 访问项目 401 |
| 服务日志中出现管理员／用户密码 | false |

冒烟真值说明：VisualStub 为假供应商，提交被暂停以稳定重现禁用／返回竞争；Delivery 夹具直接写入临时数据库。该冒烟验证应用侧持久事实、HTTP 行为和实际文件边界，不声称真实供应商生成或取消协议。

## 5. 验收覆盖表

| V3-AC | 本票条件 | 本票证据 | 结论 |
|---|---|---|---|
| V3-AC-19 | 生成中退出／过期只保留原任务归属；禁用阻止下一模型调用与发布；已发远端请求不宣称取消；恢复不自动再调用 | 5 个目标测试覆盖退出后继续、禁用后 poll 停止、remote_task_id 保留、无发布文件、恢复不重发、模型调用被拒绝；真实 HTTP 冒烟重复验证 | 本票阶段条件已覆盖；真实供应商取消语义和三账号浏览器矩阵留待 R2／R3 |
| V3-AC-21 | 用测试秘密标记扫描响应、前端包、业务库、日志、文件名与备份；模型密钥及认证秘密不被返回，普通用户无完整内部诊断 | 安全事件视图只返回安全字段；真实 HTTP 服务日志未出现密码；目标测试不返回 owner／operator 令牌或 provider 错误 | 本票仅覆盖生成后台相关响应／日志；全制品标记扫描和诊断权限矩阵由 #64 R1-GATE 复核 |
| V3-AC-34 | 必要事件和变更前后值可追踪，本人／管理员视图正确且无秘密；审计写入故障不静默完成敏感管理变更 | generation_account_blocked 进入 SECURITY_EVENT_TYPES；禁用测试断言结果 denied 且 detail 仅含 stage、remote_task_recorded；既有 #58 审计回滚回归通过 | 后台停止审计与安全视图覆盖；全事件矩阵及审计故障演练由 R1-GATE 复核 |
| V3-AC-40 | 鉴权配置、数据库、审计或归属校验不可用时按职责安全失败，无匿名兼容访问；维护说明可由另一位操作者在局域网环境复现 | generation_guard 在 owner／operator 缺失、停用、授权摘要不匹配时拒绝；所有 API 仍需认证；真实 HTTP 临时 SQLite 冒烟和操作说明可复现 | 归属校验失败安全已覆盖；数据库／审计基础设施断供的系统级演练留待 R1-GATE |

## 6. 证据文件

- 代码：main/backend/app/generation_guard.py、main/backend/app/workflow/tables.py、main/backend/app/models.py、main/backend/app/services/generations.py、main/backend/app/workflow/roles.py、main/backend/app/workflow/stages/s08.py、main/backend/app/workflow/stages/s10.py、main/backend/app/workflow/stages/s11.py、main/backend/app/workflow/stages/s11_retrieval.py、main/backend/app/auth/security_events.py
- 迁移：main/backend/migrations/versions/0006_r1_generation_task_ownership.py、backend/migrations/versions/0006_r1_generation_task_ownership.py
- 测试：backend/tests/test_t62_background_ownership.py
- 全量日志：.scratch/t62-backend-full.log
- 迁移结果：.scratch/t62-migration/result.json
- 冒烟脚本：.scratch/t62-background-smoke/smoke.py、seed.py、smoke_app.py
- 冒烟结果：.scratch/t62-background-smoke/result.json；服务日志 .scratch/t62-background-smoke/uvicorn.log
- 相关文档：docs/operations/r1-background-ownership.md

## 7. 残余风险

- 真实 HTTP 冒烟使用假视觉适配器；真实供应商的取消／轮询协议，以及远端已返回内容与本地禁用的时间竞争，需要在后续真实环境边界验证。
- 迁移 0006 为已有行回填 owner，但旧任务没有 operator 和 authorization_ref；这些旧任务只能按 owner 校验和兼容路径处理，不能伪造操作者或授权摘要。
- 本票没有新增前端状态提示；停用账号或后台阻断的最终用户提示需要在 R3 工作台投影和发布验收中复核。
- #64 R1-GATE 仍需汇总所有真实路由、全制品秘密扫描、审计故障和跨专题矩阵；本票不据此声称 R1 已完成。
