# R1 账号禁用、恢复与最小安全事件视图验收记录（ticket #58）

- **Issue**：GitHub #58《V3 T06 [R1]：账号禁用／恢复与最小安全事件视图》
- **父规格**：GitHub #52；R1 阶段同时遵循 issue #51 的认证、审计与敏感变更约束
- **分支**：ticket/58（基于 integration/v3 bd9acda）
- **代码版本**：d823592 feat(auth): add account disable restore and security events (#58)；982a28f feat(auth): audit permission denials and cover role-field authorization (#58)；本记录随同一分支文档提交
- **记录日期**：2026-10-31（Asia/Shanghai）
- **状态**：本票范围已实现并通过下方列出的定向、全量、前端与真实 HTTP 证据；不表示完整 R1 或 v3 验收通过。

## 1. 结论与边界

本票交付管理员对普通账号的停用与恢复，以及普通用户／管理员两种最小安全事件视图。实现包含：

- **POST /api/admin/accounts/{account_id}/disable**：管理员停用普通账号。账号立即进入 disabled 状态；该账号全部会话被撤销、全部未使用 setup／reset 链接被撤销、会话纪元递增；下一请求因会话失效或账号不可登录而被拒绝。
- **POST /api/admin/accounts/{account_id}/restore**：管理员恢复普通账号。恢复只清除禁用状态，不恢复旧会话、不恢复旧链接、不重置密码、不自动建立会话；原密码仍可用于新的正常登录。从未设置密码的 pending_setup 账号恢复后仍是 pending_setup，不能跳过设置。
- **GET /api/admin/security-events**：管理员查看必要管理／安全审计事件；包含稳定引用、操作者／对象标签、受限的变更前后值与安全 detail；未知账号只显示掩码引用。
- **GET /api/auth/security-events**：普通用户只查看与本人必要安全事件，字段不含会话 ID、token、密码、他人内容或内部诊断。
- **权限拒绝审计**：已登录普通用户调用管理端时，除返回统一 403 外，还记录 permission_denied 事件（仅方法、路由与稳定账号引用，不记录请求体或凭证）；审计写入故障不会把 403 变成成功或 500。
- **前端**：账号管理页提供停用／恢复按钮与安全事件面板；账号安全页提供本人安全事件面板，不渲染秘密字段。

明确不属于本票、也未在本次宣称完成的内容：

| 未完成项 | 归属 |
|---|---|
| 项目、会话、需求、参考图、生成任务等业务对象的所有者隔离与管理员读取 B 数据失败 | #59、#60–#62 |
| 生成中退出、后台任务持久归属、禁用阻止下一模型调用／发布、恢复不自动再调用 | #62（本票只验证新的 HTTP 请求被拒绝） |
| 额度调整审计与用量视图 | #67 |
| 品牌化登录／管理页完整视觉、移动端与真实设备验收 | #76、#85 |
| 90 天审计保留清理、备份中秘密隔离 | #83 |
| 邮件发送、自助注册、管理员代登、删除账号、修改邮箱、直接提升管理员 | 明确不做 |

本记录没有读取、迁移、备份、移动或删除 backend/data 与 main/backend/data。全部临时数据库、构建产物和服务日志位于未跟踪的 .scratch/t58-* 临时根或工作树内未跟踪 dist。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 分支 | ticket/58 |
| 基线 | bd9acda（integration/v3，已合并 #57） |
| 代码提交 | d823592、982a28f |
| 操作系统 | Windows 10（10.0.19045，64 位） |
| Python | 3.12.13（backend/.venv/Scripts/python.exe） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 临时数据根 | .scratch/t58-account-status-smoke/data、.scratch/t58-final-basetemp-2 |
| 数据库迁移 | 本票无新迁移；沿用 #56 的 0004_r1_account_links 及 #55 的 0003_r1_auth |

## 3. 实现摘要

### 3.1 账号状态与目标范围

- 账号状态由 is_disabled 与是否已设置密码共同投影：disabled 优先；否则无 password_hash 为 pending_setup；否则 active。只有 active 可以登录。
- 禁用与激活资格分离：is_disabled 是独立字段，禁用不改密码、不清 password_hash、不改变 pending_setup／active 的潜在资格。
- 管理端只可操作普通账号。目标为管理员、不存在 ID 或普通用户伪造目标时统一返回 404 ACCOUNT_NOT_FOUND；普通用户调用管理端统一 403 FORBIDDEN。
- 请求模型使用 extra=forbid；普通用户在创建账号请求中夹带 is_admin、role 等字段返回 422，且不会创建账号；在管理读取请求中夹带 is_admin／role 查询参数不会获得权限，仍是 403。客户端角色字段从不参与授权。

### 3.2 停用与恢复语义

- 停用在同一写事务内更新 is_disabled、disabled_at、disabled_by、updated_at，递增 session_epoch，并批量撤销该账号全部会话及全部未使用账号链接。
- 停用后的下一请求由会话装载和账号可登录状态共同拒绝；旧 Cookie、旧 CSRF、旧 setup／reset 链接均不再可用。
- 恢复在同一写事务内清除 is_disabled 与禁用元数据，递增 session_epoch，并撤销残留的未使用链接。恢复不创建新会话、不自动登录、不重启后台任务。
- 恢复后旧会话仍失效，旧链接仍失效；用户必须重新登录。若账号此前从未设置密码，恢复后仍为 pending_setup，必须重新签发 setup 链接。
- 重复停用／重复恢复为幂等空操作，不重复写审计，也不改变已有状态。

### 3.3 审计与原子性

- account_disabled／account_restored 事件在同一事务写入；actor_kind=admin，记录 actor_account_id、target_account_id、稳定 subject_ref、result、reason_code 与 source_ref。
- before_json／after_json 仅包含 status、is_admin、is_disabled、session_epoch 四个白名单字段；detail 仅包含 sessions_revoked、links_revoked 等数量，不含密码、令牌、Cookie、请求体或用户创作内容。
- 审计写入失败时整个事务回滚：账号状态、会话撤销、链接撤销与审计均不落库，接口返回失败而非静默成功。定向测试通过 monkeypatch 让 write_audit 抛错验证这一点。
- permission_denied 事件由 require_admin 在确认调用者已认证但非管理员后写入；只记录 method 与路由路径，不记录请求体、查询值、密码或 token。该审计为尽力而为，写入失败仍返回 403，绝不把拒绝变成放行。
- 不提供任何网页审计修改、删除、清空或导出接口。

### 3.4 安全事件视图

- 普通用户视图按 target_account_id 严格过滤本人事件，只返回 id、occurred_at、event_type、result、reason_code、actor_kind、detail；不含 actor／target 账号 ID、邮箱、会话 ID、token、before／after 或他人事件。
- 管理员视图返回稳定引用、已知账号邮箱标签、未知账号“未知账号（ref）”掩码、target_kind、subject_ref、before／after 与安全 detail；响应中不会出现原始未知 ID、密码、令牌、Cookie 或完整内部诊断。
- 两类视图均按 occurred_at 与 id 倒序，限制最多 100 条；事件类型白名单只包含认证、账号管理、链接、恢复与权限拒绝等必要类型。

### 3.5 前端

- /admin/accounts：账号表格显示邮箱、状态、创建时间与操作；非管理员行提供停用／恢复按钮；管理员行提示由本机 CLI 管理；安全事件面板显示时间、事件、结果、操作者、对象与状态变化。
- /account/security：新增账号安全事件面板，只显示本人事件的类型、结果、时间、操作者与原因；不显示事件 ID、会话 ID、before／after 或 detail 原始结构。
- 所有状态变更走统一 CSRF store 与精确 Origin；GET 均不改变状态；429／503 显示稳定提示。

## 4. 执行命令与结果

以下命令均在 D:/Desktop/新建文件夹/.worktrees/t58 内执行；后端工作目录为 backend。

### 4.1 定向后端测试

~~~powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings tests/test_t58_account_status.py tests/test_t15_contract.py
~~~

结果：**14 passed，8.74s，exit 0**。包含 9 个 #58 账号状态／安全事件测试与更新后的 OpenAPI 契约测试。

### 4.2 后端全量回归

~~~powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings --basetemp=D:\Desktop\新建文件夹\.scratch\t58-final-basetemp-2
~~~

环境变量：TEMP=TMP=D:\Desktop\新建文件夹\.scratch\t58-pytest。结果（exit 0）：

~~~text
513 passed, 2 warnings in 490.67s (0:08:10)
~~~

其中包含既有 504 个测试与本次新增 9 个测试。摘要日志保存在 .scratch/t58-backend-full.log。

### 4.3 前端测试、类型检查与生产构建

~~~powershell
cmd.exe /c "D:\Desktop\新建文件夹\node_modules\.bin\vitest.cmd run --reporter=dot"
cmd.exe /c "D:\Desktop\新建文件夹\node_modules\.bin\vue-tsc.cmd --noEmit -p D:\Desktop\新建文件夹\.worktrees\t58\tsconfig.json"
cmd.exe /c "D:\Desktop\新建文件夹\node_modules\.bin\vite.cmd build"
~~~

结果：

| 检查 | 结果 |
|---|---|
| Vitest | 32 个测试文件、205 个测试通过，16.84s，exit 0；仅既有 fireEvent.change／fireEvent.input 告警 |
| vue-tsc | exit 0，无输出 |
| Vite build | 成功，655ms；输出 dist/index.html、dist/assets/index-DMeDRV3c.css 412.14 kB、dist/assets/index-BPQA8OzL.js 1,175.35 kB；仅有大于 500 kB 的既有 chunk 提示 |

### 4.4 Ruff

~~~powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m ruff check main/backend/app/auth backend/tests/test_t58_account_status.py backend/tests/test_t15_contract.py
~~~

结果：**All checks passed!，exit 0**。

### 4.5 真实 HTTP + 临时 SQLite 冒烟

脚本：.scratch/t58-run-smoke.py；结果：.scratch/t58-account-status-smoke/result.json。脚本在 .scratch/t58-account-status-smoke/data 建库，启动真实 uvicorn（127.0.0.1:18770，临时 SQLite，完整 App），使用显式回环 Host／Origin、ALLOW_LOOPBACK_HTTP=true、AUTH_COOKIE_SECURE=false，仅用于本机开发验证。

~~~powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe D:\Desktop\新建文件夹\.scratch\t58-run-smoke.py
~~~

结果（exit 0，摘要）：

| 检查 | 结果 |
|---|---|
| 匿名访问业务 API | 401 |
| 管理员登录 | 200 |
| 创建普通账号 | 201，pending_setup，is_admin=false |
| setup 链接与用户设置密码 | 链接状态 200；提交 200；不建立会话 |
| 用户登录与会话内业务访问 | 200／200 |
| pending 账号停用后恢复 | 均 200；恢复后仍 pending_setup；旧链接 400；登录 401 |
| 普通用户读取管理事件／执行停用 | 403／403 |
| 用户会话在停用前 | 200 |
| 无 CSRF 停用 | 403 CSRF_INVALID |
| 停用账号 | 200，disabled |
| 停用后该账号会话／业务／登录 | 401／401／401 |
| 停用后 setup／reset 链接 | 400／400 |
| 停用事件前后值 | before {is_disabled:false,status:active,session_epoch:1}；after {is_disabled:true,status:disabled,session_epoch:2} |
| 停用事件计数 | detail.sessions_revoked=1，links_revoked=1；actor_label=admin@example.com |
| 管理事件秘密字段 | false |
| 未知账号停用 | 404 ACCOUNT_NOT_FOUND |
| 恢复账号 | 200，active |
| 恢复后旧会话／旧链接／旧密码 | 401／400／200（旧密码可重新登录；不自动建立会话） |
| 恢复后新登录 | 200 |
| 本人事件字段 | 仅 actor_kind、detail、event_type、id、occurred_at、reason_code、result；无禁用字段 |
| 本人事件包含权限拒绝 | 是；permission_denied 由普通用户管理调用产生 |
| 账号视图字段 | 仅 created_at、email、id、is_admin、last_login_at、status；无秘密 |
| 管理页／账号安全页 | 200／200 |
| OpenAPI 新路径 | disable、restore、admin security-events、personal security-events 四条 |
| 服务日志出现密码 | false |

完整输出在 .scratch/t58-account-status-smoke/result.json；服务日志在 .scratch/t58-account-status-smoke/uvicorn.log。

### 4.6 数据库迁移说明

本票不新增表或列，因此没有 0005 迁移；账号禁用字段、会话纪元、账号链接表均已在 #55／#56 建立。全量测试与真实冒烟均在全新的临时 SQLite 上执行 alembic upgrade head，并验证了从空库完整迁移到当前 head 的可用性。

## 5. 验收覆盖表（仅限本票部分）

| 验收项 | 本票覆盖 | 证据 | 未覆盖／剩余 |
|---|---|---|---|
| **V3-AC-05** 密码设置／修改 | 禁用与恢复不改变密码、不复活链接；改密／重置链路已在 #57／#56 验证 | test_disable_revokes_sessions_and_links_and_blocks_next_request、test_restore_keeps_old_sessions_and_one_time_links_dead、真实 HTTP | 完整跨票密码生命周期属 #56／#57 |
| **V3-AC-09** 统一外部失败 | 禁用后错误密码、正确密码与未知账号均返回通用 401；不提示账号是否存在；限流继承 #55 | test_disable_revokes_sessions_and_links_and_blocks_next_request、真实 HTTP 登录段 | 更完整枚举与预算矩阵见 #55／#64 |
| **V3-AC-12** 禁用即时撤销 | 禁用撤销该账号全部会话；下一请求、业务 API、登录均 401；恢复不复活旧会话 | test_disable_revokes_sessions_and_links_and_blocks_next_request、test_restore_keeps_old_sessions_and_one_time_links_dead、真实 HTTP | 跨账号互不误撤销的扩展矩阵并入 #64 R1-GATE |
| **V3-AC-17** 角色字段与管理员读取 | 普通用户直接调用管理端 403；伪造 is_admin／role 字段 422 且不创建账号；管理端目标账号 404；管理员不能经管理接口读取业务数据 | test_client_role_fields_do_not_authorize_admin_calls、test_only_admin_can_change_status_and_admin_targets_are_not_addressable、真实 HTTP | 管理员读取 B 业务数据失败与对象级隔离属 #59 |
| **V3-AC-19** 禁用阻止下一模型调用／发布 | 本票验证新的 HTTP 请求与会话立即被拒绝 | 真实 HTTP 停用段、test_disable_revokes_sessions_and_links_and_blocks_next_request | 后台任务持久归属、模型调用／发布截断、恢复不自动调用属 #62 |
| **V3-AC-34** 审计与视图 | account_disabled／account_restored 同事务 before／after；审计失败回滚；permission_denied 记录；本人／管理员视图安全且脱敏 | test_audit_failure_rolls_back_disable_and_keeps_session_valid、test_personal_security_events_are_self_only_and_have_safe_shape、test_admin_security_events_expose_before_after_and_mask_unknown_accounts、真实 HTTP | 90 天保留清理、备份秘密隔离与最终完整事件矩阵属 #83／#85 |

本票不宣称上述 AC 已跨阶段全部通过；例如 #59 的对象级隔离、#62 的后台任务归属与 #76／#85 的视觉和真实设备验收仍需各自完成。

## 6. 证据文件

| 路径 | 内容 |
|---|---|
| backend/tests/test_t58_account_status.py | 9 个定向账号状态／安全事件／角色字段测试 |
| tests/t58-accounts-security.spec.ts | 3 个账号管理页与本人事件前端测试 |
| .scratch/t58-run-smoke.py／.scratch/t58-account-status-smoke/result.json | 真实 uvicorn + 临时 SQLite 停用／恢复／事件冒烟 |
| .scratch/t58-backend-full.log | 513 passed 全量后端摘要 |
| docs/operations/r1-account-status.md | 账号停用／恢复与安全事件操作说明 |

## 7. 残余风险与后续

1. **#59**：项目及其子资源的所有者隔离；本票的账号状态与会话认证不等于对象级授权。
2. **#62**：后台任务持久归属、禁用阻止下一模型调用／发布、恢复不自动再调用；本票不宣称覆盖。
3. **#64**：R1-GATE 的跨账号、跨资源、失败矩阵与全路由覆盖。
4. **#76／#85**：品牌化页面、移动端、真实设备与真实模型闭环验收。
5. **审计保留**：90 天保留与清理策略尚待发布阶段执行；本票只保证事件写入、视图与敏感变更回滚。
6. **权限拒绝审计为尽力而为**：审计依赖故障时仍拒绝访问，但该次拒绝可能没有审计记录；这是安全优先的取舍，发布阶段仍需监控审计依赖可用性。

**本记录不声称完整 R1 或 v3 已验收。**