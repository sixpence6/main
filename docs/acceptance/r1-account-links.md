# R1 管理员建号与一次性设置／重置链接验收记录（ticket #56）

- **Issue**：GitHub #56《V3 T04 [R1]：管理员建号与一次性设置／重置闭环》
- **父规格**：GitHub #52；R1 阶段同时遵循 issue #51 的认证与链接约束
- **分支**：`ticket/56`（基于 `integration/v3` `5897b37`）
- **代码版本**：`2f7c2d7` `feat(auth): add admin account links and one-time password setup (#56)`；本记录为同一分支的文档提交
- **记录日期**：2026-10-31（Asia/Shanghai）
- **状态**：**本票范围已实现并通过下方列出的定向、全量、迁移与真实 HTTP 证据；不表示完整 R1、完整账号管理或 v3 验收通过。**

## 1. 结论与边界

本票交付管理员在最小管理视图创建普通账号，并只一次取得限定用途的一次性链接；用户通过链接主动设置或重置密码，随后回到正常登录。实现包含：

- 管理员 HTTP 接口：创建待设置账号、列出账号、签发 setup／reset 链接。
- 账号链接的持久模型、迁移 `0004`、摘要存储、用途绑定、TTL、撤销与单次消费。
- 链接状态与提交接口、预认证 CSRF、用途／过期／重复／禁用／并发校验。
- 管理员账号页与公开设置页；链接 token 只从 URL fragment 读取，不进入请求行、查询参数或存储。
- 审计与限流：建号、签发、使用、重置均写审计；签发和验证使用独立预算；审计失败回滚敏感变更。

明确不属于本票、也未在本次宣称完成的内容：

| 未完成项 | 归属 |
|---|---|
| 用户自助改密、多会话列表／撤销、本机首管理员恢复 | #57 |
| 禁用／恢复普通账号的完整管理流程与恢复后凭证语义 | #58 |
| 项目／会话／需求等业务对象的所有者隔离 | #59 |
| 品牌化登录、账号页和移动端视觉验收 | #76 |
| 邮件发送、自助注册、管理员代登、公开互联网入口 | 明确不做 |

本记录没有读取、迁移、备份、移动或删除 `backend/data` 与 `main/backend/data`。全部临时数据库、构建产物和服务日志位于未跟踪的 `.scratch/t56-*` 临时根或工作树内未跟踪 `dist`。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 分支 | `ticket/56` |
| 基线 | `5897b37`（`integration/v3`，已合并 #55） |
| 代码提交 | `2f7c2d7` |
| 操作系统 | Windows 10（10.0.19045，64 位） |
| Python | 3.12.13（`backend/.venv/Scripts/python.exe`） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5（`vite build`） |
| 临时数据根 | `.scratch/t56-account-link-smoke/data`、`.scratch/t56-migration/work` |

## 3. 实现摘要

### 3.1 数据与迁移

迁移 `main/backend/migrations/versions/0004_r1_account_links.py`（`down_revision = 0003`，两处迁移目录逐字节一致）新增 `account_links`：

- `id` 不可变 UUID 字符串主键；`account_id` 外键到 `accounts`，删除账号时级联删除链接。
- `purpose` 仅允许 `setup`／`reset`；`token_digest` 唯一，仅存 SHA-256 摘要，不存明文 token。
- `created_at`／`expires_at`；`used_at`；`revoked_at`／`revoked_reason`；`created_by`。
- 约束保证用途合法、过期时间晚于创建时间、撤销必有原因、`used_at` 与 `revoked_at` 不同时出现；索引覆盖账号＋用途＋创建时间和过期时间。

### 3.2 后端入口

| 路由 | 行为 |
|---|---|
| `GET /api/admin/accounts` | 管理员列出账号；普通用户 403 |
| `POST /api/admin/accounts` | 管理员创建 `pending_setup` 普通账号；邮箱规范化唯一；不返回密码或 token |
| `POST /api/admin/accounts/{account_id}/links` | 管理员签发 `setup`／`reset` 链接；响应只在此次响应中带明文链接 |
| `GET /api/auth/link` | 用 `X-Link-Token` 查看掩码邮箱、用途、过期时间；**不消费、不改密码** |
| `POST /api/auth/link` | 一次性设置／重置密码；成功不建立登录会话、不自动登录 |

关键安全语义：

- 链接地址由配置项 `APP_PUBLIC_URL` 生成，绝不从请求 `Host` 推导；未配置时签发返回 503，不降级。
- 明文 token 只放在 `#token=` fragment；不进入查询字符串、Referer、服务端访问日志或审计。服务端只保存摘要。
- token 使用现有 `new_token()`（32 字节 CSPRNG，base64url，≥256 bit 熵）；TTL 30 分钟。
- 新签发同一账号的链接会在同一事务内撤销该账号所有旧的未使用链接；旧链接立即失效。
- `setup` 只允许 `pending_setup`，`reset` 只允许 `active`；禁用账号、错误用途、过期、重复提交统一返回 `400 LINK_INVALID`，不泄漏账号是否存在。
- `POST /api/auth/link` 在 `PasswordPolicyError` 时返回 422；在认证依赖不可用时返回 503，不创建密码；成功后设置密码、消费链接、递增 `session_epoch`、撤销该账号所有会话与其他未使用链接，并在同一事务写审计。
- 并发消费由写事务与链接行状态复核保证恰好一个成功；失败方收到 `LINK_INVALID`。
- 管理员签发预算：每管理员 10 次／15 分钟；每目标账号 3 次／30 分钟；验证预算：每 token 摘要 5 次失败／15 分钟，每来源 30 次／15 分钟。签发成功保留计数事实，验证成功清除验证尝试事实。
- 失败路径的审计写入失败会让创建账号／签发链接／设置密码整体回滚（测试覆盖）。

### 3.3 前端

- `/admin/accounts`：仅管理员可见；列出邮箱、状态、创建时间；创建账号；生成 setup／reset 链接；链接只在当前页面显示一次，可复制或关闭丢弃；不写 `localStorage`／`sessionStorage`。
- `/set-password`：公开路由；只从 `route.hash` 的 `#token=` 读取 token；GET 状态，提交前获取预认证 CSRF；成功后清空密码字段并移除 URL fragment；不自动登录，引导回 `/login`。
- 登录／管理页使用现有统一 CSRF store；公开设置页的 POST 带预认证 `X-CSRF-Token` 与精确 `Origin`。
- `main/index.html` 增加 `Referrer-Policy: no-referrer`；所有 API 响应继续由认证中间件附加私有响应头。

## 4. 执行命令与结果

以下命令均在 `D:/Desktop/新建文件夹/.worktrees/t56` 内执行；后端工作目录为 `backend`。

### 4.1 定向账号链接测试

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings tests/test_t56_account_links.py
```

结果：**16 passed，10.73s，exit 0**。覆盖配置 URL 校验、建号唯一、普通用户拒绝、摘要与一次性链接、重置不改旧密码、过期／错用途／禁用／重复、并发单赢家、签发与验证预算、审计回滚、公开路由 CSRF／私有响应头、迁移表存在。

### 4.2 后端全量回归

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings --basetemp=D:\Desktop\新建文件夹\.scratch\t56-final-basetemp
```

结果（exit 0）：

```text
492 passed, 2 warnings in 474.15s (0:07:54)
```

其中包含既有 476 个测试与本次新增 16 个账号链接测试。全量日志保存在 `.scratch/t56-backend-full.log`。

### 4.3 前端测试与类型检查

```powershell
cmd.exe /c "D:\Desktop\新建文件夹\node_modules\.bin\vitest.cmd run --reporter=dot"
cmd.exe /c "D:\Desktop\新建文件夹\node_modules\.bin\vue-tsc.cmd --noEmit -p D:\Desktop\新建文件夹\.worktrees\t56\tsconfig.json"
```

结果：**30 个测试文件、198 个测试通过，15.93s，exit 0**；typecheck exit 0。控制台仅有既有 `fireEvent.change`／`fireEvent.input` 告警。

### 4.4 Ruff

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\ruff.exe check main/backend/app/auth main/backend/app/main.py main/backend/app/config.py main/backend/app/routers/web.py main/backend/migrations/versions/0004_r1_account_links.py backend/migrations/versions/0004_r1_account_links.py backend/tests/test_t56_account_links.py backend/tests/test_t15_contract.py
```

结果：**All checks passed!，exit 0**。

### 4.5 临时 SQLite 迁移验证

脚本：`.scratch/t56-migration/migration_check.py`；结果：`.scratch/t56-migration/result.json`。

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe D:\Desktop\新建文件夹\.scratch\t56-migration\migration_check.py
```

结果（exit 0）：

| 检查 | 结果 |
|---|---|
| 全新数据库升级版本 | `0004` |
| `account_links` 表 | 存在；10 列与 2 个业务索引 |
| 已存在 `0003` 数据库升级 | `0003` → `0004` |
| 原有项目 | `preserved project`／`draft` 保留 |
| 原有账号 | `preserved@example.com` 保留 |
| 两处迁移副本 | 逐字节一致 |

### 4.6 真实 HTTP + 临时 SQLite 冒烟

脚本：`.scratch/t56-run-smoke.py`；结果：`.scratch/t56-account-link-smoke/result.json`。脚本在 `.scratch/t56-account-link-smoke/data` 建库，启动真实 uvicorn（127.0.0.1:18768，临时 SQLite，完整 App），使用显式回环 Host／Origin、`ALLOW_LOOPBACK_HTTP=true`、`AUTH_COOKIE_SECURE=false`，仅用于本机开发验证。

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe D:\Desktop\新建文件夹\.scratch\t56-run-smoke.py
```

结果（exit 0，摘要）：

| 检查 | 结果 |
|---|---|
| 匿名 `GET /api/projects` | 401 |
| 管理员登录 | 200，session 为 `admin@example.com` |
| 创建普通账号 | 201，状态 `pending_setup`，`is_admin=false` |
| 管理员账号列表 | 200 |
| 签发 setup 链接 | 201，地址前缀 `http://127.0.0.1:18768/set-password`，无查询串 |
| `GET /api/auth/link`（消费前） | 200，掩码邮箱 `u***@example.com` |
| 无 CSRF 提交链接 | 403 |
| 正确提交链接 | 200，未下发 `__Host-session` |
| 消费后 GET／重复提交 | 400／400 |
| 用户正常登录 | 200 |
| 签发 reset 链接后旧密码登录 | 200（签发本身不改旧密码） |
| 重置提交 | 200 |
| 重置后旧会话 | 401 |
| 重置后旧密码登录 | 401 |
| 重置后新密码登录 | 200 |
| `GET /set-password` 页面 | 200，`Referrer-Policy: no-referrer`，`Cache-Control: no-store` |
| 服务日志含明文 token | false |

完整输出在 `.scratch/t56-account-link-smoke/result.json`；服务日志在 `.scratch/t56-account-link-smoke/uvicorn.log`。

## 5. 验收覆盖表（仅限本票部分）

| 验收项 | 本票覆盖 | 证据 | 未覆盖／剩余 |
|---|---|---|---|
| **V3-AC-04** 管理员建号 | 管理员创建普通账号；邮箱规范化唯一；`pending_setup`；普通用户无管理接口；无自助注册、无邮件、无代登 | `test_admin_creates_pending_account_and_unique_email`、`test_ordinary_user_cannot_manage_accounts`、真实 HTTP 建号 | 完整账号管理 UI 与账号页属 #76；禁用／恢复属 #58 |
| **V3-AC-05** 密码设置／重置 | 链接方式设置与重置；15–128 规则与弱口令；重置后旧密码／旧会话失效；成功不自动登录 | `test_link_submission_is_atomic_no_session_and_consumes_once`、`test_reset_issuance_keeps_old_password_and_consumption_revokes_sessions`、真实 HTTP | 用户已登录后的自助改密、多会话列表属 #57 |
| **V3-AC-06** 一次性链接 | 绑定配置地址与用途；仅摘要；30 分钟；新签发撤销旧链接；GET 不消费；重置不改旧密码；过期／重复／错用途／禁用／并发 | `test_issuance_uses_configured_url_digest_only_and_supersedes_old`、`test_missing_trusted_app_url_fails_closed`、`test_expired_wrong_purpose_disabled_and_wrong_token_are_rejected`、`test_concurrent_consumption_has_exactly_one_winner` | 真实邮件投递明确不做 |
| **V3-AC-07** 原子消费 | POST 密码／消费／撤销同事务；并发单赢家；审计失败回滚 | `test_link_submission_is_atomic_no_session_and_consumes_once`、`test_concurrent_consumption_has_exactly_one_winner`、`test_audit_failure_rolls_back_account_link_and_password` | 完整备份恢复后的跨版本演练属发布票 |
| **V3-AC-08** 密码策略 | 设置／重置路径复用 Argon2id 与 15–128 Unicode code point、无裁剪、弱口令拒绝 | `test_link_verification_credential_budget`、`test_expired_wrong_purpose_disabled_and_wrong_token_are_rejected`、T55 密码策略测试 | 密码历史／外部泄露库仍不在范围 |
| **V3-AC-09** 登录与链接限流 | 签发：管理员 10／15 分钟、目标 3／30 分钟；验证：摘要 5／15 分钟、来源 30／15 分钟；认证父预算仍生效 | `test_link_rate_limits_admin`、`test_link_rate_limits_target`、`test_link_verification_credential_budget`、`test_link_verification_source_budget` | 代理后真实来源需按发布票配置复验 |
| **V3-AC-13** CSRF／Origin | 链接 POST 使用预认证 CSRF；缺失 403；Origin 精确；GET 不改状态；私有响应 no-store／no-referrer | `test_public_link_routes_and_private_headers`、真实 HTTP 无 CSRF 403、页面响应头 | 真实浏览器跨标签页／历史返回属 #72 |
| **V3-AC-34** 审计完整性 | 建号、签发、使用、重置事件；稳定 ID；不记录明文 token；审计失败回滚账号／链接／密码 | `test_issuance_uses_configured_url_digest_only_and_supersedes_old`、`test_audit_failure_rolls_back_account_link_and_password`、服务日志 token 缺失检查 | 90 天保留清理与完整事件目录随发布票 |

本票不宣称 AC-04／05／06／07／08／09／13／34 已跨阶段全部通过；例如 #57 的自助改密、#58 的禁用恢复、#59 的对象级隔离仍需各自验收。

## 6. 证据文件

| 路径 | 内容 |
|---|---|
| `backend/tests/test_t56_account_links.py` | 16 个定向账号链接测试 |
| `.scratch/t56-migration/migration_check.py`／`result.json` | 0004 全新与 0003→0004 保留数据证据 |
| `.scratch/t56-run-smoke.py`／`.scratch/t56-account-link-smoke/result.json` | 真实 uvicorn + 临时 SQLite 冒烟 |
| `.scratch/t56-backend-full.log` | 492 passed 全量后端输出 |
| `tests/t56-account-links.spec.ts` | 前端设置页、管理页、守卫测试 |
| `docs/operations/r1-account-links.md` | 管理员建号与链接操作说明 |

## 7. 残余风险与后续

1. **#57**：用户登录后的自助改密、多会话列表／撤销、本机首管理员恢复；本票只有一次性链接方式。
2. **#58**：禁用／恢复完整流程；当前仅状态列与链接拒绝，未实现管理操作。
3. **#59**：项目及子资源的所有者隔离；本票的账号认证不等于对象级授权。
4. **#76**：品牌化登录、账号页、移动端与真实浏览器多标签页视觉验收。
5. **发布阶段**：#78 统一生产入口与兼容启动、#80 局域网 HTTPS、#81–#84 备份／恢复／保留清理、#85 真实设备闭环。
6. **APP_PUBLIC_URL**：生产必须配置为可信 HTTPS 根地址；本票的 HTTP 回环地址只用于本机冒烟。

**本记录不声称完整 R1 或 v3 已验收。**
