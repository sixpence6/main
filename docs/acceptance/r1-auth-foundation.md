# R1 认证基线与默认拒绝验收记录（ticket #55）

- **Issue**：GitHub #55《V3 T03 [R1]：本地首管理员、登录、会话、CSRF、审计、限流与默认拒绝》
- **父规格**：GitHub #52；权威需求见 `issue51.md` 的 R1 范围
- **分支**：`ticket/55`
- **代码版本**：`ee55888`（R1 首提交）＋ `dd8e94f`（认证失败处理与会话 CSRF 硬化）＋ `25ad419`（匿名不安全请求先返回 401）＋ `90e1ced`（工作流测试确定性）＋本记录提交；`d86d4e9` 已将 `integration/v3` `99ceaac` 合并入本分支；最终以 `ticket/55` HEAD 为准
- **记录日期**：2026-10-31（Asia/Shanghai）
- **状态**：**R1 认证基础已实现并通过本记录列出的自动化、临时 SQLite 迁移与真实 HTTP 证据；不表示完整 R1、局域网 HTTPS 或 v3 验收通过。**

## 1. 结论与边界

本票交付的是 R1 的认证与边界基础：本机首管理员初始化、邮箱／密码登录、服务端会话、登录前与会话内 CSRF、Host／Origin 校验、私有响应 no-store、最小公共 allowlist、审计、登录与来源限流、16 KiB 认证 JSON 上限、Argon2id 密码策略、以及所有既有业务 API 的默认拒绝。

以下内容明确不属于本票，也没有在本次记录中宣称完成：

| 未完成项 | 归属 |
|---|---|
| 管理员创建普通账号、一次性设置链接、账号管理与配额 | #56 |
| 用户自助改密、多会话列表／撤销 UI、完整账号页 | #57 |
| 禁用／恢复账号的完整管理流程 | #58 |
| 项目及子资源的所有者归属、ACL-01／ACL-04／ACL-05 的完整业务隔离 | #59 |
| 品牌化登录／账号页面、移动端视觉验收 | #76 |
| 局域网 HTTPS、受控反向代理、证书与真实多设备验收 | 后续 R1／发布票 |

本记录没有任何真实数据操作：没有读取、迁移、备份、移动或删除 `backend/data` 与 `main/backend/data`。所有临时数据库和 HTTP 测试均位于未跟踪的 `.scratch/t55-*` 临时根下，命令中使用显式临时 `APP_DATA_DIR`。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 分支 | `ticket/55` |
| 基线 | `fdcf8d9`（`integration/v3`） |
| R1 首提交 | `ee55888` |
| 报告前合并 | `integration/v3` `99ceaac`（维护备份模块，不改认证语义） |
| 操作系统 | Windows 10（10.0.19045，64 位） |
| Python | 3.12.13（`backend/.venv/Scripts/python.exe`） |
| pytest | 9.2.0 |
| argon2-cffi | 25.1.0（`argon2.low_level.Type.ID`，即 Argon2id） |
| Node.js | v26.12.1 |
| 临时数据根 | `.scratch/t55-auth-migration/work`、`.scratch/t55-auth-smoke/data` |

## 3. 实现摘要

### 3.1 账号、会话与审计数据

迁移 `0003_r1_auth.py`（从 `0002`）新增：

- `accounts`：不可变 UUID 字符串主键、`email_normalized` 唯一、可空 `password_hash`、`password_set_at`、`password_policy_version`、`is_admin`、`is_disabled`、`disabled_at/by`、`session_epoch`、`last_login_at`、创建／更新时间；唯一索引保证规范化邮箱唯一，部分唯一索引保证至多一个管理员；状态由列派生为 `pending_setup`／`active`／`disabled`。
- `instance_bootstrap`：单例 `id=1`、管理员账号外键和 `local_cli` 方法标记，作为首管理员已初始化的持久事实。
- `sessions`：只保存会话 token 的 SHA-256 摘要和 CSRF 摘要、账号引用、账号 epoch、创建／最后活动／空闲到期／绝对到期、撤销时间与原因、客户端与来源摘要。
- `audit_events`：UTC 时间、事件类型、操作者种类／账号、目标、结果、原因码、关联 ID、来源摘要及变更前后 JSON。
- `rate_limit_facts`：持久滚动窗口事实，带 class／key／occurred_at／expires_at／weight／outcome 和查询索引。

两处迁移副本（`backend/migrations` 与 `main/backend/migrations`）逐字节一致；启动时的 schema 校验仍要求数据库版本等于当前 head。

### 3.2 认证包与入口

`main/backend/app/auth/` 提供：

| 模块 | 作用 |
|---|---|
| `models.py` | 账号、会话、审计、限流事实、单例 bootstrap 的 ORM 模型 |
| `service.py` | 邮箱规范化、首管理员初始化、登录、登出、登录失败释放／结算 |
| `passwords.py` | Argon2id 参数、密码长度／Unicode／空格策略、版本化本地弱口令表、哈希并发门 |
| `sessions.py` | 随机不透明 token、SHA-256 持久化、空闲／绝对过期、轮换、撤销 |
| `rate_limit.py` | 持久滚动窗口、账号与来源独立维度、过期清理、20,000 bucket 上限 |
| `audit.py` | 同事务审计写入、受控 JSON 截断、主体／来源摘要 |
| `middleware.py` | 唯一 Host／Origin／CSRF／默认拒绝／no-store 中间件 |
| `router.py` | `/api/auth/csrf|login|session|logout` |
| `cli.py` | 仅本机 TTY 的首管理员初始化命令 |

### 3.3 HTTP 边界

- 公共 allowlist 严格为：`/api/health`、`/api/config/status`、`/api/auth/login`、`/api/auth/logout`、`/api/auth/session`、`/api/auth/csrf`，以及静态构建资源和登录页。`/api/auth/logout` 在无会话时仍由路由返回 401，不构成匿名业务入口。
- 所有其他 `/api/*`（包括未知路径和未来的业务路由）在无有效会话时统一返回 `401 AUTH_REQUIRED`，不暴露路由是否存在。
- `Host` 必须匹配显式 allowlist；`Origin` 只接受配置的可信 scheme／host。默认不信任 `X-Forwarded-*`，只有显式代理配置才可改变信任边界。
- 所有 POST／PATCH／PUT／DELETE 需要 `Origin` 和 `X-CSRF-Token`；登录前使用预认证 CSRF cookie，登录后使用会话绑定的 CSRF 摘要。GET 不产生认证状态变更。
- 所有 `/api` 响应附加 `Cache-Control: no-store`、`Pragma: no-cache`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、`X-Frame-Options: DENY`。
- 认证 JSON（登录等）在读取前限制 16,384 字节；超过时返回 413 `AUTH_BODY_TOO_LARGE`，不进入哈希。
- 会话 Cookie 使用 `__Host-session`：`Secure`、`HttpOnly`、`SameSite=Strict`、`Path=/`、无 `Domain`；CSRF Cookie 为 `__Host-csrf`，仅允许前端读取 token。生产默认 `AUTH_COOKIE_SECURE=true`；仅显式本机回环开发时才允许关闭 Secure，局域网默认不降级。
- 登录失败响应统一为 `401 INVALID_CREDENTIALS` 的同一 JSON 体；未知账号执行 dummy Argon2 校验，以保持与真实校验同类的工作量。
- 登录限流：规范化账号 5 次失败／15 分钟；可信客户端来源 30 次失败／15 分钟；两个维度独立，窗口为 `(now-window, now]`，过期事实清理，单 class 至多 20,000 个 bucket。
- 哈希并发：同时运行最多 2 个，排队最多 8 个；第 11 个立即返回 503 `AUTH_BUSY`，不挂起连接；该失败不消耗账号／来源失败额度。

### 3.4 首管理员 CLI

入口为 `main/backend/app/auth/cli.py`，典型命令：

```powershell
cd main/backend
python -m app.auth.cli init-admin
```

- 只在本机 TTY 中运行；非 TTY 退出码 2。
- 密码只通过 `getpass` 两次读取，不回显、不接受 `--password` 或命令行明文；邮箱可位置参数或交互输入。
- 空库并发初始化由数据库唯一约束／事务保证只有一个成功；已有管理员时拒绝重入；账号与初始化审计在同一事务内提交，审计失败会回滚账号。
- 不启动网络监听，不提供 HTTP 首管理员路由。

### 3.5 前端最小接入

- 新增 `/login` 路由和最小登录页；登录成功后只接受站内安全 `redirect`。
- `requestJson` 使用同源 credentials，对不安全方法发送 `X-CSRF-Token`；用户主动 GET 发送 `X-Auth-Activity: user`，轮询 GET 发送 `X-Polling: 1`。
- 会话 token／CSRF token 只保存在模块内存，不写入 `localStorage` 或 `sessionStorage`。
- 路由守卫在未认证时跳转 `/login`，保留安全的原目标路径；完整品牌与账号 UI 留给 #76。

## 4. 执行命令与结果

以下命令均在本 worktree 内执行；后端工作目录为 `backend`（pytest 的 `pythonpath` 指向 `../main/backend`）。

### 4.1 定向认证测试

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings tests/test_t55_auth_foundation.py
```

结果：**31 passed，14.21s，exit 0**。覆盖首管理员并发／重入／无默认密码、密码 Unicode／空格／边界／弱口令、登录泛化失败与非枚举、Cookie／轮换／伪造、空闲／绝对／轮询不续期、CSRF／Host／Origin／no-store、限流维度与半开窗口、16 KiB 上限、哈希队列 2+8、默认拒绝矩阵（含匿名不安全业务请求先 401）、审计回滚、Argon2 不可用 fail-closed。

### 4.2 后端全量回归

```powershell
cd backend
D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings
```

本次最终复跑结果（2026-10-31，worktree 内，exit 0）：

```text
........................................................................ [ 15%]
........................................................................ [ 30%]
........................................................................ [ 45%]
........................................................................ [ 60%]
........................................................................ [ 75%]
........................................................................ [ 90%]
............................................                             [100%]
476 passed, 2 warnings in 500.44s (0:08:20)
```

`--collect-only` 同样报告 **476 tests collected**。R1 首提交前基线为 **426 passed**；本票新增定向认证测试 31 个，另有本票对既有测试夹具的认证改造。

> 说明：完整套件包含真实 uvicorn 子进程测试；这些测试使用临时 SQLite、显式回环 Host／Origin、真实 CSRF／登录和 cookie jar，未绕过认证。

### 4.3 前端测试与类型检查

```powershell
npm test -- --reporter=dot
npm run typecheck
```

本次结果：**29 个测试文件、195 个测试通过，exit 0**；typecheck exit 0。控制台中的 `fireEvent.change`／`fireEvent.input` 是既有测试写法告警，不是失败。

### 4.4 Ruff

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m ruff check main/backend/app/auth main/backend/app/main.py main/backend/app/config.py main/backend/app/errors.py main/backend/migrations/versions/0003_r1_auth.py backend/migrations/versions/0003_r1_auth.py backend/tests/test_t55_auth_foundation.py backend/tests/test_t09_reference_versions.py backend/tests/test_v2_t16_unknown_recovery.py
```

结果：**All checks passed!，exit 0**。

### 4.5 临时 SQLite 迁移验证

脚本：`.scratch/t55-auth-migration/migration_check.py`（未跟踪，仅使用 `.scratch` 临时根）。

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe D:\Desktop\新建文件夹\.scratch\t55-auth-migration\migration_check.py
```

结果（exit 0）：

- 全新数据库升级到 **0003**；存在 `accounts`、`sessions`、`audit_events`、`rate_limit_facts`、`instance_bootstrap`；关键列齐全。
- 先升级到 **0002**，插入一个项目和一条业务行，再升级到 **0003**；原项目行完整保留，数据库版本为 0003。
- `backend/migrations/versions/0003_r1_auth.py` 与 `main/backend/migrations/versions/0003_r1_auth.py` 逐字节一致。

完整 JSON 结果保存在 `.scratch/t55-auth-migration/result.json`。

### 4.6 真实 HTTP + 临时 SQLite 冒烟

脚本：`.scratch/t55-auth-smoke/smoke.py`（未跟踪）。脚本在 `.scratch/t55-auth-smoke/data` 建库，启动真实 uvicorn（端口 18767），以显式 `ALLOW_LOOPBACK_HTTP=true`、`AUTH_COOKIE_SECURE=false`、显式 Host／Origin 仅作本机回环开发验证。

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe D:\Desktop\新建文件夹\.scratch\t55-auth-smoke\smoke.py
```

结果（exit 0，摘要）：

| 检查 | 结果 |
|---|---|
| 本机 service 首管理员初始化 | ok |
| 重复初始化 | rejected |
| 匿名 `GET /api/projects` | 401 |
| 预认证 CSRF | 200 |
| 无 CSRF 登录 | 403 |
| 正确登录 | 200，账号 `admin@example.com`、状态 active、is_admin true |
| Set-Cookie | `__Host-session`、`__Host-csrf`；session 带 HttpOnly、SameSite=strict |
| 登录后 session | 200 |
| 登录后项目列表 | 200 |
| 创建项目 | 201 |
| 登出 | 204 |
| 登出后 session／项目 | 401／401 |
| CLI 非 TTY | exit 2 |

完整输出保存在 `.scratch/t55-auth-smoke/result.json`。该证据是真实 uvicorn + 临时 SQLite，不是 TestClient；未使用真实数据或真实模型密钥。

## 5. 验收覆盖表（仅限本票部分）

| 验收项 | 本票覆盖 | 证据与测试 | 未覆盖／剩余 |
|---|---|---|---|
| **V3-AC-03** 首管理员 | 部分覆盖：空库仅本机 service／CLI；并发只成功一个；有管理员拒绝重入；无默认密码；邮箱规范化唯一 | `test_first_admin_concurrent_init_exactly_one_and_no_default_password`、`test_email_normalization_is_shared_and_rejects_invalid`、CLI 非 TTY 测试、迁移唯一索引 | 普通账号创建／一次性链接属 #56；管理恢复、账号删除等属后续票 |
| **V3-AC-08** 密码策略 | 部分覆盖：15／128 Unicode code point、空格与 Unicode 保留、无截断、版本化本地弱口令、Argon2id m=19456 t=2 p=1、随机盐、rehash | `test_password_policy_boundaries_and_unicode_spaces`、`test_weak_password_denylist_is_casefold_and_fail_closed`、`test_password_needs_rehash_handles_invalid_hash`、`test_password_needs_rehash_on_login_upgrades_hash` | 自助改密属 #57；完整密码历史／外部泄露库未接入 |
| **V3-AC-09** 登录泛化与限流 | 部分覆盖：未知／待设置／禁用／错误密码同一对外响应；dummy Argon2；账号 5／15 分钟、来源 30／15 分钟独立；半开窗口；持久事实 | `test_login_failures_are_generic_for_unknown_pending_disabled_and_wrong`、`test_login_rate_limit_account_dimension_and_recovery`、`test_login_source_limit_is_independent`、`test_rate_limit_window_is_half_open` | 局域网代理后的真实来源需在受控代理配置下复验；未实现分布式多实例限流 |
| **V3-AC-10** 会话 Cookie 与轮换 | 部分覆盖：`__Host-session` 属性、仅摘要持久化、登录换发、伪造 cookie 401、CSRF cookie 非 HttpOnly、前端无 Web Storage | `test_session_cookie_flags_and_server_side_digest`、`test_login_success_rotates_session_cookie_and_audit_has_no_secret`、`test_forged_session_cookie_is_rejected`、前端认证测试 | 多会话 UI／撤销指定会话属 #57；真实浏览器多标签属 #76 |
| **V3-AC-11** 空闲／绝对／轮询 | 部分覆盖：注入时钟下 30 分钟空闲、12 小时绝对；轮询 `X-Polling` 不续期；真实主动 mutation 续期；普通 GET 不续期 | `test_session_idle_absolute_and_polling_does_not_renew`、`test_active_mutation_renews_idle_but_plain_get_does_not` | 浏览器端定时器与断网恢复属前端后续验收；客户端时间不被信任 |
| **V3-AC-13** CSRF／Origin | 部分覆盖：登录预认证 CSRF、会话绑定 CSRF、所有不安全方法 Origin + header、错误／缺失 403、Host 校验、安全 `redirect`；GET 不变更认证状态 | `test_csrf_origin_host_and_no_store`、`test_authenticated_csrf_fetch_then_mutation_uses_same_token`、`test_get_csrf_does_not_clobber_session_csrf_and_session_still_works`、前端登录重定向测试 | 一次性链接页的预认证 CSRF 属 #56；上传／确认路径复用会话 CSRF，但完整上传验收仍属既有 v2 回归与 #59 归属票 |
| **V3-AC-15** 真实路由默认拒绝 | 部分覆盖：对当前注册业务路由的匿名请求统一 401；公共 allowlist 与未知 `/api` 行为符合预期；匿名不因路由存在性不同而泄漏 | `test_default_deny_matrix_and_public_allowlist`；`test_health`／配置路由保持公共；真实 uvicorn 冒烟匿名项目 401 | 所有者隔离、他人对象不可见、列表／下载／轮询全矩阵属 #59；本票只保证认证门与默认拒绝 |
| **V3-AC-20** 注入／路径 | 部分覆盖：认证输入无动态 SQL、无文件路径拼接；邮箱／密码边界校验；迁移静态 SQL | 代码审查与定向测试；默认拒绝阻断匿名业务入口 | 不宣称完整应用注入／路径遍历验收；既有业务资产路径仍按 v2 门禁与 #59 复验 |
| **V3-AC-21** 秘密不泄漏 | 部分覆盖：登录／审计测试确认密码与 token 不进入审计；错误体泛化；前端无 localStorage／sessionStorage 认证秘密；响应附加 no-store | `test_login_success_rotates_session_cookie_and_audit_has_no_secret`、`test_audit_json_truncation_stays_valid_json`、前端认证测试 | 未做完整前端包、日志、文件名、备份的独立秘密扫描；模型密钥管理仍属既有 v2／安全票 |
| **V3-AC-34** 审计完整性 | 部分覆盖：首管理员账号与初始化审计同事务；强制审计失败回滚账号；登录成功／失败／限流／登出记录；审计 JSON 合法截断 | `test_init_audit_failure_rolls_back_account`、`test_login_success_rotates_session_cookie_and_audit_has_no_secret`、`test_audit_json_truncation_stays_valid_json` | 完整事件目录（创建／禁用／恢复／改密／链接等）随 #56／#57／#58；90 天保留清理未在本票实现 |
| **V3-AC-40** 安全失败 | 部分覆盖：Argon2／弱口令表不可用 503 fail-closed；会话／认证数据库故障时不降级为匿名业务；无匿名兼容分支 | `test_init_fails_closed_when_argon2_is_unavailable`、`test_init_fails_closed_when_weak_list_is_unavailable`、`test_session_endpoint_returns_503_on_db_failure` | 归属校验、审计配置、完整维护恢复演练和局域网复现属 #59／后续运维票 |

## 6. 证据文件

| 路径 | 内容 |
|---|---|
| `backend/tests/test_t55_auth_foundation.py` | 31 个定向认证测试 |
| `.scratch/t55-auth-migration/migration_check.py`／`result.json` | 全新 0003 与既有 0002 升级证据 |
| `.scratch/t55-auth-smoke/smoke.py`／`result.json` | 真实 uvicorn + 临时 SQLite HTTP 冒烟 |
| `.scratch/t55-backend-full.log` | 最终后端全量输出（报告前复跑） |
| `docs/operations/r1-local-admin-init.md` | 本机首管理员操作说明 |

## 7. 明确 TODO 与残余风险

1. **#56**：管理员创建／禁用普通账号、一次性设置链接、配额；当前 schema 已预留 `pending_setup`、`password_hash` 可空和账号状态，但未实现 HTTP 管理接口。
2. **#57**：用户自助改密、多会话列表与撤销 UI；当前只有服务端 helper 与单一登录会话，不宣称多会话 UI 完成。
3. **#58**：禁用／恢复完整流程与恢复不复活旧凭证；本票只有状态列和登录拒绝，未暴露管理操作。
4. **#59**：项目所有者、跨账号对象隔离与 ACL-01／04／05 全矩阵；本票的默认拒绝不等于对象级授权已完成。
5. **局域网／TLS**：本票只验证本机回环 HTTP 开发模式；正式 LAN HTTPS、受控代理、证书、真实设备与 Host／Origin 配置需发布前单独验收。
6. **审计保留**：审计表已持久化，但 90 天保留清理和完整事件目录未在本票实现。
7. **安全评审**：AC-20／AC-21 只记录本票新增面，不替代对整个应用的独立注入、路径和秘密扫描。

**本记录不声称完整 R1 或 v3 已验收。**
