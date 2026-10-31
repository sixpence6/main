# R1 全出口权限与安全验收关口记录

- **Issue**：#64 V3 T12 [R1-GATE]：R1 全出口权限与安全验收关口
- **父 Issue**：#52 V3 规格实施；R1 阶段规范遵循 #51，本票不关闭 #51
- **分支**：ticket/64（基于 integration/v3 78e813e）
- **代码／测试提交**：17fb815de0ddfbcf8142910a508e2910773d7a1d
- **验收日期**：2026-10-31
- **状态**：R1-GATE 的本阶段条件通过；全量后端 542、前端 33 文件／206 测试、迁移 0001–0007、真实 HTTP 59 项冒烟全部通过。合并由 integration/v3 执行

## 1. 结论与边界

本票在 #55–#63 的 R1 实现之上，用真实路由清单、权限状态矩阵、独立连接并发、故障注入、秘密标记扫描、迁移链和真实 HTTP／临时 SQLite／完整 App 冒烟，证明当前注册的 33 个 OpenAPI 操作没有匿名旁路，管理员、账号 A、账号 B、匿名、双会话、合法、阻断和未认领对象的组合符合 R1 边界。门禁测试以精确集合相等断言 33 个操作；新增出口若不同步更新清单，测试会失败，而不是静默漏测。

本票提交只新增 backend/tests/test_t64_r1_gate.py，不修改生产代码。生产树与 integration/v3 78e813e 相同；因此全量回归同时适用于本票基座和提交后的测试树。

本票只承接 #64 列出的 R1 阶段条件，不表示整个 v3 完成。明确留待后继阶段的条件：

| 留待项 | 归属 |
|---|---|
| 真实浏览器多标签、切号、历史返回、缓存层、缩略图组件与可读存储 | #71–#77（R3）；本票仅前端单元测试与 HTTP 契约 |
| R2 新生成动作、R3 新工作台动作的 CSRF／Origin／对象矩阵 | #65–#77；本票验证当前出口，后继出口需持续加入清单 |
| 真实模型数据流、真实供应商零保留边界、真实生成／发布闭环 | R2／R3／Release；本票不配置真实供应商 |
| 完整前端构建包、备份归档的全制品秘密标记扫描与最终诊断权限矩阵 | Release #78／#79／#85；本票完成响应／业务库／日志／文件名阶段扫描 |
| 真实局域网设备、可信 HTTPS、代理后来源与部署边界 | Release；本票为 127.0.0.1 真实 uvicorn |
| 真实旧数据来源的维护者选择、授权和真实数据认领演练 | Release；本票只用临时夹具，绝不触碰 backend/data 或 main/backend/data |
| 90 天审计保留、事件目录全矩阵、恢复／轮换／账本缺口 | Release；本票验证审计故障回滚和当前安全事件视图 |

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t64 |
| 分支／基座 | ticket/64 / integration/v3 78e813e |
| 代码／测试提交 | 17fb815de0ddfbcf8142910a508e2910773d7a1d |
| 操作系统 | Windows（本地开发环境） |
| Python | 3.12.13（backend/.venv） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 数据库 | 临时 SQLite（aiosqlite，WAL），迁移 0007；未使用真实业务数据 |
| Web | 真实 uvicorn 127.0.0.1:18777，完整 App，临时 DATA_DIR／WEB_DIST_DIR |
| HTTP 客户端 | httpx 0.28.1（真实进程与 ASGI 测试） |

## 3. 实现摘要

### 3.1 全出口清单与默认拒绝

main/backend/app/main.py 当前 OpenAPI 共 33 个操作，分为 6 个公开入口和 27 个受保护操作。公开入口是 GET /api/health、GET /api/config/status、GET /api/auth/csrf、POST /api/auth/login、GET /api/auth/link、POST /api/auth/link。门禁测试断言实际 OpenAPI 操作集合与预期集合完全相等，然后逐一匿名请求 27 个受保护操作，全部返回 401 AUTH_REQUIRED；6 个公开入口按设计可达。匿名访问未知 /api 路由返回 401 AUTH_REQUIRED，已登录访问未知路由返回 404 ROUTE_NOT_FOUND，未认证请求不能通过探测路由存在性获得额外信息。

### 3.2 权限与状态矩阵

同一测试创建管理员、账号 A、账号 B 和待设置账号，覆盖：

- A 读取本人项目 200；A 读 B 项目、B 读 A 项目均为统一 404；管理员读 A／B 项目也为 404，管理员没有业务数据代看豁免。
- B 读 A 工作流、修改 A 需求卡、向 A 项目发消息、执行 A 工作流动作均为 404。
- A 上传真实 PNG 参考图后读取本人资源得到精确字节；B 和管理员读取同一资源均 404。
- 篡改 A 参考图字节后，A 自己读取也返回 409，响应不含原始字节；恢复文件后字节再次精确一致，B 仍为 404。
- 未认领 owner 为空的旧项目，A、B、管理员读取均 404，且不出现在管理员项目列表中。
- 创建项目时伪造 owner_account_id／is_admin 字段返回 422。
- 待设置账号不能登录，返回通用 401 INVALID_CREDENTIALS。
- 禁用 B 后，B 的项目列表立即 401；A 的项目与详情不受影响。

### 3.3 双会话、CSRF、Origin 与角色边界

- 同一账号建立两个独立会话；会话列表恰好 2 条且恰好 1 条 current；撤销另一会话后，另一会话下一请求 401，当前会话仍可用。
- 缺少会话 CSRF 的不安全请求返回 403 CSRF_INVALID；Origin 为 https://evil.example 时返回 403 ORIGIN_INVALID；恢复 Origin 与 CSRF 后正常。
- 普通用户调用管理员建号接口返回 403 FORBIDDEN，管理员安全事件视图存在 permission_denied 记录。
- 退出当前会话后当前请求上下文立即 401；不把退出、过期和禁用混为同一种状态。

### 3.4 限流、哈希预算、审计回滚与数据库故障

- 冻结时钟下连续 5 次错误管理员登录均为通用 401，第 6 次为 429 RATE_LIMITED，带正数 Retry-After；推进 901 秒后窗口恢复。
- HashGate(running=2, queued=8) 下 10 个并发工作者稳定观测到 2 个运行、8 个等待；第 9 个等待者进入时抛 ApiError AUTH_BUSY。释放后全部完成。
- 注入审计写入故障后，管理员禁用账号抛错；直接查 SQLite 确认 is_disabled 仍为 0、该账号仍有 1 个未撤销会话，用户随后仍能读取 /api/auth/session，敏感变更与审计同事务回滚。
- 注入会话加载数据库故障后，用户请求 /api/auth/session 返回 503，而 /api/health 仍为 200；鉴权故障不降级为匿名业务访问。

### 3.5 独立连接并发初始化、链接与认领

- 四个独立 Engine／SessionFactory 同时 init_admin：恰好 1 个 Account 成功、3 个 AlreadyInitialized；数据库 accounts 与 instance_bootstrap 各 1 行。为避免连接池首次激活 WAL 的竞争污染业务竞争，测试先逐个 Engine 执行 SELECT 1 预热连接池，但保留四个独立引擎与连接；业务唯一性仍由数据库与初始化事务保证。
- 同一一次性设置链接被两个独立客户端并发提交：响应恰好为 200 与 400，只有一个赢家。
- 两个独立连接并发 claim_legacy：至少一个 ClaimResult，恰好 1 行 legacy_claims、1 条 legacy_projects_claimed 审计，项目 owner 已写入，无重复事实。认领使用全新临时迁移源，避免对已完全迁移的竞争数据库降级。

### 3.6 秘密标记扫描

门禁测试构造三类标记：管理员设置的新密码、模型密钥、一次性链接明文 token，以及管理员／用户会话 Cookie 与 CSRF token。扫描结果：

- 11 个受保护响应正文（本人／管理员会话、会话列表、安全事件、项目列表与详情、管理账号列表、管理安全事件、配置状态）不含密码、模型密钥、链接 token 或会话 Cookie。
- 业务数据目录下所有文件名和文件原始字节不含密码、模型密钥、链接 token、会话 Cookie、CSRF token。
- caplog 捕获的日志文本不含上述存储型标记。
- /api/auth/session 按设计只向当前已认证会话返回其自己的 CSRF token，便于前端发起不安全请求；该 token 不落库明文，也不出现在其他账号响应中。因此响应正文扫描把 CSRF token 单独排除，而数据库、日志和文件名仍扫描全部存储型标记。

### 3.7 迁移链与数据边界

迁移在独立临时数据目录执行：fresh 链从空库升级到 0007；chain 链从 0001 开始，依序执行 0003、0004、0005、0006、0007。两条链 alembic_version 均为 0007，SQLite integrity_check 均为 ok，业务表均为 22 张，包含 accounts、account_links、sessions、projects、legacy_claims 等。两条迁移目录 main/backend/migrations/versions 与 backend/migrations/versions 保持同一版本序列。未读取、写入或修改 backend/data、main/backend/data 或任何真实业务数据根。

## 4. 执行命令与结果

### 4.1 门禁目标测试（连续 3 次）

工作目录：D:/Desktop/新建文件夹/.worktrees/t64/backend

    .venv/Scripts/python.exe -m ruff check tests/test_t64_r1_gate.py
    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-gate6-run1 tests/test_t64_r1_gate.py
    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-gate6-run2 tests/test_t64_r1_gate.py
    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-gate6-run3 tests/test_t64_r1_gate.py

结果：Ruff All checks passed；三次均为 6 passed，分别 6.68s、6.67s、6.70s，exit 0。并发初始化用例在修复前曾出现 SQLite database is locked 的测试夹具竞争；预热连接池后连续 5 次、10 次压力运行与整套 3 次均通过，未降低唯一赢家断言。

### 4.2 R1 全量负责测试

     .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-r1-final       tests/test_t55_auth_foundation.py tests/test_t56_account_links.py       tests/test_t57_sessions.py tests/test_t58_account_status.py       tests/test_t59_isolation.py tests/test_t60_ownership_closure.py       tests/test_t61_history_recovery_gate.py tests/test_t62_background_ownership.py       tests/test_t63_legacy_claim.py tests/test_t64_r1_gate.py

结果：97 passed, 2 warnings in 74.26s，exit 0。分文件为 t55 31、t56 16、t57 12、t58 9、t59 5、t60 3、t61 4、t62 5、t63 6、t64 6。

### 4.3 全量后端：R1 与继承的 v2 回归

工作目录：D:/Desktop/新建文件夹/.worktrees/t64/backend。为避开单条命令 600 秒上限，按非 v2 与 v2 两组执行，两组文件不重叠且覆盖 tests 下全部测试文件。

    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-full-final-nonv2       tests/test_openai_chat_adapter.py tests/test_openai_images_adapter.py tests/test_t*.py       > D:/Desktop/新建文件夹/.scratch/t64-full-final-nonv2.log 2>&1

结果：307 passed, 2 warnings in 269.64s。

    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-full-final-v2       tests/test_v2_*.py       > D:/Desktop/新建文件夹/.scratch/t64-full-final-v2.log 2>&1

结果：235 passed, 1 warning in 258.52s。

合计：542 passed，3 warnings，无失败、无 skip、无 unknown。两组合计覆盖全部 tests/test_*.py；v2 继承回归 235 项全部通过。

### 4.4 前端回归、类型检查与构建

工作目录：D:/Desktop/新建文件夹/.worktrees/t64

    npm test -- --reporter=dot
    npm run typecheck
    npm run build

结果：33 个测试文件、206 个测试全部通过，19.70s；vue-tsc --noEmit exit 0；Vite 8.3.5 构建成功 764ms，1662 modules，dist/index.html 0.47 kB、dist/assets/index-CoQKJWll.css 412.64 kB、dist/assets/index-B9cC436c.js 1,175.91 kB。仅有既存 chunk > 500 kB 警告与测试中的 fireEvent.change 警告。日志：.scratch/t64-frontend-final/vitest.log、typecheck.log、build.log。

### 4.5 迁移链

工作目录：D:/Desktop/新建文件夹/.worktrees/t64/main/backend

    APP_DATA_DIR=D:/Desktop/新建文件夹/.scratch/t64-migration-final/fresh-data     WEB_DIST_DIR=D:/Desktop/新建文件夹/.scratch/t64-migration-final/fresh-web     PYTHONPATH=D:/Desktop/新建文件夹/.worktrees/t64/main/backend     D:/Desktop/新建文件夹/.worktrees/t64/backend/.venv/Scripts/python.exe       -m alembic -c alembic.ini upgrade head

    APP_DATA_DIR=D:/Desktop/新建文件夹/.scratch/t64-migration-final/chain-data     WEB_DIST_DIR=D:/Desktop/新建文件夹/.scratch/t64-migration-final/chain-web     PYTHONPATH=D:/Desktop/新建文件夹/.worktrees/t64/main/backend     D:/Desktop/新建文件夹/.worktrees/t64/backend/.venv/Scripts/python.exe       -m alembic -c alembic.ini upgrade 0003
    # 再依序 upgrade 0004、0005、0006、0007

结果：fresh 从 0001 升到 0007；chain 从 0003 升到 0007；两者当前版本均为 0007 (head)，integrity_check 均为 ok，22 张业务表一致。日志：.scratch/t64-migration-final/fresh.log、chain.log。

### 4.6 真实 HTTP + 临时 SQLite + 完整 App 冒烟

脚本：.scratch/t64-gate-smoke/rerun.py；被测实现：.worktrees/t64/main/backend；解释器：.worktrees/t64/backend/.venv/Scripts/python.exe；服务：真实 uvicorn 127.0.0.1:18777；数据：.scratch/t64-gate-smoke/run2/data/app.db；Web：.scratch/t64-gate-smoke/run2/web；迁移 0007。

    python D:/Desktop/新建文件夹/.scratch/t64-gate-smoke/rerun.py

结果：stdout {"ok": true, "checks": 59}；result.json 的 environment.git_head 为 17fb815de0ddfbcf8142910a508e2910773d7a1d，alembic_version 为 0007，python 3.12.13，httpx 0.28.1。59 项检查全部通过，关键场景：

| 场景 | 结果 |
|---|---|
| 27 个受保护操作逐一匿名请求 | 全部 401 AUTH_REQUIRED |
| 6 个公开入口 | 健康／配置／CSRF 200；链接 GET 400、链接 POST 与登录 POST 在无预认证 CSRF 时 403，均按设计可达 |
| 匿名与已登录未知 API | 401 AUTH_REQUIRED / 404 ROUTE_NOT_FOUND |
| 管理员建三个账号并经一次性链接设置密码 | 全部 200／201，新账号项目列表为空 |
| A 读本人项目、B／管理员读 A 项目与工作流 | 200 / 均 404 |
| 伪造 owner／role 字段 | 422 |
| A 上传 PNG 并读本人参考图 | 200，字节精确一致 |
| B／管理员读 A 资源 | 均 404 |
| 篡改 A 参考图字节 | 409，原始字节不泄漏；恢复后字节精确一致 |
| 未认领旧项目 | 所有账号（含管理员）404，且不出现在列表 |
| 同账号双会话 | 列表 2 条、1 条 current；撤销另一会话后其下一请求 401，当前会话可用 |
| 缺会话 CSRF | 403 CSRF_INVALID |
| 外部 Origin | 403 ORIGIN_INVALID |
| 普通用户调用管理建号 | 403 FORBIDDEN，管理员安全事件有 permission_denied |

冒烟服务日志：.scratch/t64-gate-smoke/run2/uvicorn.log；结果：.scratch/t64-gate-smoke/run2/result.json。测试未配置真实 LLM／视觉供应商，未调用外部模型，未使用真实业务数据。

### 4.7 静态检查与差异检查

    .venv/Scripts/python.exe -m ruff check tests/test_t64_r1_gate.py
    git diff --check

结果：Ruff All checks passed；git diff --check 无输出；提交前 git status 仅显示测试文件修改。提交后工作树干净。

## 5. 验收覆盖表

| V3-AC | 本票 R1 阶段条件 | 证据 | 结论 |
|---|---|---|---|
| V3-AC-03 本机初始化、并发唯一、禁止重入、无默认密码和网络抢注 | 四个独立连接并发初始化只有一个 Account 与一个 bootstrap；无密码参数；已有管理员拒绝重入 | test_concurrent_initialization_link_and_claim_use_independent_connections；t55 的 CLI 非 TTY、无密码参数、并发初始化测试；真实 HTTP 建号 | 通过（R1 阶段） |
| V3-AC-04 管理员建待设置普通账号、邮箱唯一、普通用户无管理权限 | 管理员创建待设置账号；普通用户建号 403；邮箱规范化唯一由 t56 覆盖 | t64 权限矩阵；test_admin_creates_pending_account_and_unique_email；test_ordinary_user_cannot_manage_accounts；真实 HTTP 三账号 | 通过（R1 阶段） |
| V3-AC-05 激活／禁用／改密／本机恢复正确，恢复不复活旧凭证 | 待设置不能登录；禁用立即 401；恢复不复活会话与链接；改密／本机恢复由 t57／t58 覆盖 | t64 权限矩阵；t57 改密与会话；t58 禁用／恢复；t56 重置保持旧密码 | 通过（R1 阶段；完整 UI 属 R3） |
| V3-AC-06 链接用途、过期、重发、并发原子消费；GET 不消费或登录 | 两客户端并发提交同一链接恰好 200／400；GET 不消费；重发撤销旧链接 | test_concurrent_initialization_link_and_claim_use_independent_connections；t56 目标测试；真实 HTTP 设置链接 | 通过（R1 阶段） |
| V3-AC-07 凭证原文不落库／日志／跳转／错误，无 Host 注入或第三方 Referer 泄漏 | 密码、链接 token、会话 Cookie、CSRF 不进入响应／DB／日志／文件名；Host／Origin 校验 | test_secret_markers_do_not_reach_responses_database_or_filenames；t55 Cookie／Header 测试；真实 HTTP 日志 | 通过（R1 阶段；全构建包与备份归档秘密扫描留 Release） |
| V3-AC-08 密码长度、Unicode／空格、弱口令与随机盐 Argon2id 成本正确，无降级 | 15–128 Unicode code point、无裁剪、弱口令 fail-closed、Argon2id 参数与 rehash | t55 密码策略、弱口令、rehash 目标测试；t56 密码设置复用同一策略 | 通过（R1 阶段） |
| V3-AC-09 失败响应不枚举，后端多维限流及并发资源预算有效 | 未知／待设置／禁用／错误密码同一 401；账号 5／15 分钟与来源 30／15 分钟；HashGate 2 运行／8 等待／第 9 拒绝 | test_rate_hash_budgets_audit_rollback_and_database_outage_fail_closed；t55 登录限流、半开窗口、来源限流；真实 HTTP 第 6 次 429 | 通过（R1 阶段；代理后真实来源留 Release） |
| V3-AC-10 正式 Cookie 与登录换发正确，前端及可读存储无登录秘密 | __Host-session HttpOnly／Secure／SameSite 等属性、登录换发、伪造 Cookie 401、前端认证测试无 Web Storage 秘密 | t55 会话 Cookie、伪造 Cookie；前端 33 文件 206 测试；t64 双会话 | 通过（R1 阶段；真实浏览器多标签属 R3） |
| V3-AC-11 空闲／绝对临界与轮询不续期，过期不复活 | 30 分钟空闲、12 小时绝对；X-Polling 不续期；主动 mutation 续期；普通 GET 不续期 | t55 test_session_idle_absolute_and_polling_does_not_renew、test_active_mutation_renews_idle_but_plain_get_does_not；t57 会话投影 | 通过（R1 阶段；浏览器定时器属 R3） |
| V3-AC-12 多端指定撤销、退出本端、改密／重置／禁用及他账号隔离 | 双会话指定撤销、退出当前、改密／重置／禁用撤销所有会话、跨账号 404 | t64 双会话；t57 改密与撤销；t58 禁用／恢复；t56 重置；真实 HTTP | 通过（R1 阶段） |
| V3-AC-13 所有变更动作的 CSRF、来源和返回地址，无后继新动作旁路 | 缺 CSRF 403、外部 Origin 403、Host 校验、GET 不变更；当前不安全动作全部覆盖 | t64 双会话；t55 CSRF／Origin／Host；t56 链接；真实 HTTP | 通过（当前出口）；R2／R3 新动作必须继续加入 |
| V3-AC-15 真实出口清单全覆盖，匿名／他人／不存在对象不泄漏 | 33 操作精确集合；27 受保护匿名全 401；未知匿名 401、已登录 404；A／B／管理员统一 404；未认领 404 | t64 路由清单与权限矩阵；t61 PROTECTED_ROUTES；真实 HTTP 59 项 | 通过（当前 33 出口）；后继出口需持续加入 |
| V3-AC-16 所有关联 ID 交换、缓存、缩略图和实际字节无越权 | 项目、工作流、参考图、资源字节 ID 交换均为 404；篡改摘要 409；真实字节精确 | t64 权限矩阵；t61 字节门禁；t60 归属；真实 HTTP | 通过（R1 HTTP 与真实字节）；浏览器缓存／缩略图属 R3 |
| V3-AC-17 伪造角色／所有者无效，管理员无他人创作读取豁免 | 伪造 owner_account_id／is_admin 422；管理员读 A／B 项目、工作流、资源均 404 | t64 权限矩阵；t59 伪造字段；t60 客户端字段忽略；真实 HTTP | 通过（R1 阶段） |
| V3-AC-18 自有待审／撤销／过期及 locator／静态历史路径不能绕过 G08，认领不批准 | 待审／未发布／撤销／过期 409；篡改参考图 409；静态／locator 路径 404；认领不改变审批 | t61 目标测试；t64 篡改参考图；t63 claim；真实 HTTP | 通过（R1 阶段）；前端缩略图与供应商 locator 全协议留 R3／Release |
| V3-AC-19 退出／过期与禁用不同，原任务归属不变，未知不伪称取消或自动续发 | 退出不改变任务 owner；禁用停止下一 poll；remote_task_id 保留；不自动重发；发布前重查 | t62 五个目标测试；t64 双会话退出；真实 HTTP | 通过（R1 阶段）；R2／R3 集成矩阵后补 |
| V3-AC-20 不可信输入、文件名、路径、SQL、模型输出与定位符不造成注入／越界访问 | 恶意上传文件名不落地为路径；路径穿越、解压炸弹、注入、locator 形状 URL 回归；SQL 绑定参数 | t64 秘密文件名；t60 上传文件名；t05／v2_t04／t03 既有安全回归随 542 全量通过 | 通过（回归层）；独立安全扫描不属本票 |
| V3-AC-21 响应、前端、业务库、日志、文件名及备份不泄漏认证秘密／模型密钥 | 秘密标记扫描 11 个响应、caplog、数据目录文件名与原始字节；模型密钥不返回；备份排除秘密文件由 t54 覆盖 | test_secret_markers_do_not_reach_responses_database_or_filenames；t54 备份排除 .env／私钥；t55 审计秘密；真实 HTTP 日志 | 通过（R1 阶段）；完整前端构建包与备份归档扫描留 Release |
| V3-AC-33 核源、冲突选择、备份前置、幂等认领、内容隔离及新账号空工作区 | 单一／冲突来源、摘要确认、有效备份、幂等重复、审计回滚、未认领 404、新账号空列表；并发认领单事实 | t63 六个目标测试；t64 并发认领；真实 HTTP 新账号空工作区；真实来源未授权不触碰 | 通过（R1 临时夹具）；真实来源授权与演练留 Release |
| V3-AC-34 必要审计、前后值、正确视图及秘密隔离，审计故障回滚敏感变更 | 禁用审计故障回滚 is_disabled 与会话；permission_denied 可见；安全事件视图安全脱敏；认领审计同事务 | test_rate_hash_budgets_audit_rollback_and_database_outage_fail_closed；t58 审计与事件；t63 审计回滚；真实 HTTP | 通过（R1 阶段）；保留清理与全事件目录留 Release |
| V3-AC-39 首次输入前告知模型数据流，不发送认证秘密，继承 v2 最小持久化且不承诺零保留 | DescribePage 外部模型说明在首次输入前显示；不发送密码／会话／恢复秘密；明确不承诺零保留；真实模型未配置 | 前端 tests/t60-dataflow-notice.spec.ts；前端 206 测试；gate 秘密标记不返回模型密钥 | 通过（R1 说明与秘密边界）；真实供应商数据流留 R2／R3／Release |
| V3-AC-40 鉴权／库／审计／归属故障按职责安全失败，另一操作者可复现维护步骤 | 审计故障回滚；会话加载故障 503 而健康 200；归属失败统一 404／ACCOUNT_DISABLED；运维步骤见配套 operations 文档 | t64 故障用例；t55 数据库故障；t62 归属与账号状态；本记录命令可重复执行 | 通过（R1 阶段）；完整维护恢复演练留 Release |

## 6. 证据文件

- backend/tests/test_t64_r1_gate.py（6 个门禁测试，543 行）
- .scratch/t64-gate6-run1、run2、run3（目标测试三次）
- .scratch/t64-r1-final（R1 全量 97）
- .scratch/t64-full-final-nonv2.log（307 passed）
- .scratch/t64-full-final-v2.log（235 passed）
- .scratch/t64-frontend-final/vitest.log、typecheck.log、build.log
- .scratch/t64-migration-final/fresh.log、chain.log 及 fresh-data/app.db、chain-data/app.db
- .scratch/t64-gate-smoke/run2/result.json（59 checks，git_head 17fb815）
- .scratch/t64-gate-smoke/run2/uvicorn.log
- docs/operations/r1-gate.md（重复执行步骤）

## 7. 残余风险与边界

1. 本票的真实 HTTP 冒烟运行在 127.0.0.1，不是另一台局域网设备或可信 HTTPS；Release 必须复验 V3-AC-01／02。
2. 未配置真实 LLM／视觉供应商；AC-39 只证明说明、最小数据流和秘密边界，不证明供应商侧零保留或真实生成闭环。
3. 前端为 Vitest／jsdom 与构建产物验证，不是真实浏览器；多标签、切号、缓存、历史返回留 R3。
4. 秘密扫描覆盖响应、业务数据目录、日志与文件名；构建后的整个前端包和备份归档的全制品扫描留 Release。
5. 限流来源维度在真实反向代理后的客户端 IP 解析留 Release 的部署与代理配置复验；本票不声称跨主机分布式限流。
6. 并发初始化测试预热各 Engine 连接池，仅移除 SQLite WAL 首次激活的夹具竞争；业务竞争仍由四个独立引擎和数据库唯一约束证明，不降低唯一赢家断言。
7. 真实旧数据来源未经维护者明确选择与授权，本票只用临时夹具；未触碰 backend/data 或 main/backend/data。
8. 本票不关闭 #51，也不把 #51 的状态作为 R1-GATE 的替代证据。
