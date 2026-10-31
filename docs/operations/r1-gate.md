# R1 全出口权限与安全验收关口操作说明

- **适用版本**：v3 R1，ticket #64（测试提交 17fb815 及之后；生产代码基座为 integration/v3 78e813e）
- **适用对象**：部署／运维人员、管理员、验收人员
- **前置阶段**：C0 备份与维护基线（#54）、R1 认证与账号（#55–#58）、项目归属隔离（#59）、参考图与工作流归属闭环（#60）、历史与真实字节门禁（#61）、后台任务归属（#62）、旧数据认领（#63）已合并
- **状态**：本说明覆盖 R1 全出口门禁的可重复验收步骤。它不替代 #51，#51 的关闭须由维护者在完整 R1 验收后决定

## 1. 范围与硬边界

本票不是新增业务功能，而是对当前注册的 33 个 HTTP 操作做全出口复核，并用临时数据完成权限、并发、故障和秘密标记检查。日常运维需要知道：

1. 当前出口固定为 6 个公开入口和 27 个受保护操作。任何新增 API 都必须同步更新 backend/tests/test_t64_r1_gate.py 的清单，否则门禁测试因集合不相等而失败。
2. 匿名访问任何受保护 API 返回 401 AUTH_REQUIRED；匿名访问未知 /api 路由也返回 401，已登录访问未知路由返回 404 ROUTE_NOT_FOUND。
3. 管理员没有业务数据代看豁免。管理员读取普通用户的项目、工作流、消息、资源或历史，与陌生账号一样得到统一 404。
4. 项目、工作流、资源、历史 ID 的跨账号交换一律不可获得；不要通过数据库改 owner 或直接读文件来“核对”他人内容。
5. 参考图摘要或尺寸不匹配返回 409，生成结果未通过 G08／交付／发布门禁返回 409 或 404；不得为了页面显示放宽检查。
6. 未认领的旧项目 owner 为空，所有账号（含管理员）读取均 404；只有按 #63 的核源、备份、确认流程完成认领后才归属指定管理员。
7. 秘密标记检查覆盖响应、业务数据目录、日志和文件名。认证 Cookie、CSRF token、链接 token、密码和模型密钥不得进入普通响应、业务库明文或日志。

明确不允许：

- 为了验收通过而临时关闭 Host／Origin／CSRF 校验、降低 Argon2 参数或放宽会话撤销。
- 在真实业务数据上做并发初始化、链接并发、认领或故障注入演练；这些操作只用临时 DATA_DIR 和临时 SQLite。
- 读取、写入或修改 backend/data、main/backend/data 或维护者未明确授权的旧数据来源。
- 用管理员账号读取普通用户项目来“帮助排查”；应让用户在本人会话中复现，或使用临时数据演练。
- 在审计写入失败时手工补一条审计来让业务继续；敏感变更必须与审计同事务回滚。

## 2. 前置条件

| 项 | 要求 |
|---|---|
| 服务版本 | 已包含 #55–#63 的构建；本票测试提交 17fb815 |
| 数据库 | SQLite；迁移版本 0007（legacy_claims）；本票无新增迁移 |
| 迁移树 | main/backend/migrations/versions 与 backend/migrations/versions 的版本序列一致；不得修改已应用的 0003–0007 |
| Python | backend/.venv 内 Python 3.12.13；pytest 9.2.0；httpx 0.28.1 |
| Node.js | v26.12.1；前端依赖已安装 |
| 配置 | APP_DATA_DIR 与 WEB_DIST_DIR 为不同目录且互不包含；ALLOWED_HOSTS 含实际访问主机；APP_PUBLIC_URL 为绝对 http／https 源 |
| 网络边界 | 门禁演练使用 127.0.0.1；真实部署只在可信内网／局域网，不得直接暴露公网 |
| 账号 | 至少一个已初始化管理员；演练用普通账号由管理员经一次性链接创建 |
| 备份 | 任何升级或迁移前，按 docs/operations/maintenance-backup.md 完成一致性备份并验证 |
| 数据 | 真实业务数据禁止用于演练；演练使用 .scratch 下的临时目录，结束后可整体删除 |

## 3. 操作步骤

### 3.1 升级前备份与版本确认

1. 按 docs/operations/maintenance-backup.md 停止业务写入或进入维护窗口，执行一致性备份并验证清单。
2. 记录备份目录、备份 ID 和校验摘要；未验证备份不得继续迁移或认领。
3. 在 main/backend 执行 alembic current，确认当前版本为 0007。低于 0007 时按迁移顺序补齐，不得跳版。
4. 记录即将部署的代码提交：git rev-parse HEAD。验收记录必须写明该提交，不能用“最新代码”代替。

### 3.2 运行门禁测试

工作目录：仓库工作树的 backend 目录。

    .venv/Scripts/python.exe -m ruff check tests/test_t64_r1_gate.py
    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-gate6-run1       tests/test_t64_r1_gate.py

预期：Ruff All checks passed；6 passed，约 7 秒，exit 0。再运行两次到不同 basetemp，确认并发初始化用例稳定。若出现 database is locked，先确认测试是否已预热各 Engine 连接池；不得通过删断言或串行化业务竞争来消除失败。

六个测试分别覆盖：

| 测试 | 内容 |
|---|---|
| test_registered_operations_are_exact_and_default_deny | 33 个 OpenAPI 操作精确集合；27 个受保护操作匿名 401；6 个公开入口可达；未知路由行为 |
| test_role_and_object_matrix_has_no_bypass | 管理员、A、B、匿名、未认领、伪造字段、待设置、禁用矩阵 |
| test_dual_sessions_csrf_origin_and_admin_role | 双会话撤销、CSRF、Origin、普通用户管理接口 403、permission_denied 审计 |
| test_rate_hash_budgets_audit_rollback_and_database_outage_fail_closed | 登录限流、HashGate 预算、审计故障回滚、会话加载数据库故障 503 |
| test_concurrent_initialization_link_and_claim_use_independent_connections | 独立连接并发初始化唯一赢家、一次性链接原子消费、并发旧数据认领单事实 |
| test_secret_markers_do_not_reach_responses_database_or_filenames | 密码／模型密钥／链接 token／Cookie／CSRF 不出现在 11 个响应、日志、文件名和文件字节 |

### 3.3 运行 R1 与继承回归

    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-r1-final       tests/test_t55_auth_foundation.py tests/test_t56_account_links.py       tests/test_t57_sessions.py tests/test_t58_account_status.py       tests/test_t59_isolation.py tests/test_t60_ownership_closure.py       tests/test_t61_history_recovery_gate.py tests/test_t62_background_ownership.py       tests/test_t63_legacy_claim.py tests/test_t64_r1_gate.py

预期 97 passed（t55 31、t56 16、t57 12、t58 9、t59 5、t60 3、t61 4、t62 5、t63 6、t64 6）。

全量后端因单条命令可能超过 600 秒，分为互不重叠的两组：

    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-full-final-nonv2       tests/test_openai_chat_adapter.py tests/test_openai_images_adapter.py tests/test_t*.py

    .venv/Scripts/python.exe -m pytest -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t64-full-final-v2       tests/test_v2_*.py

预期合计 542 passed，3 warnings，无失败、无 skip、无 unknown。两组必须覆盖 tests 下全部 test_*.py；不要只跑新增测试。

### 3.4 前端回归与构建

工作目录：工作树根，不是 main/。

    npm test -- --reporter=dot
    npm run typecheck
    npm run build

预期：33 个测试文件、206 个测试通过（约 20 秒）；vue-tsc --noEmit exit 0；Vite 构建成功（约 0.8 秒，1662 modules）。只允许既存 chunk-size 警告与 fireEvent.change 警告。若出现未识别的一次性失败，重跑并记录两次结果，不得直接忽略。

### 3.5 迁移链复核

使用临时目录，不要指向真实数据根：

    APP_DATA_DIR=D:/Desktop/新建文件夹/.scratch/t64-migration-final/fresh-data     WEB_DIST_DIR=D:/Desktop/新建文件夹/.scratch/t64-migration-final/fresh-web     PYTHONPATH=D:/Desktop/新建文件夹/.worktrees/t64/main/backend     D:/Desktop/新建文件夹/.worktrees/t64/backend/.venv/Scripts/python.exe       -m alembic -c alembic.ini upgrade head

再建一条从 0003 开始的链，依次 upgrade 0004、0005、0006、0007。两条链都应到达 0007 (head)，SQLite integrity_check 为 ok，业务表 22 张，包含 accounts、account_links、sessions、projects、legacy_claims。若 fresh 与 chain 结果不同，停止并核对迁移树，不要手工改 alembic_version。

### 3.6 真实 HTTP 冒烟

使用真实 uvicorn、临时 SQLite 和完整 App。脚本与结果放在 .scratch，不得放进 main/。

    python D:/Desktop/新建文件夹/.scratch/t64-gate-smoke/rerun.py

脚本要求：被测解释器为工作树 backend/.venv/Scripts/python.exe；PORT=18777；APP_PUBLIC_URL=http://127.0.0.1:18777；PYTHONPATH 指向工作树 main/backend；DATA_DIR 与 WEB_DIST_DIR 指向 .scratch/t64-gate-smoke/run2。否则会出现 FileNotFoundError 或 403 ORIGIN_INVALID。

预期 stdout 为 {"ok": true, "checks": 59}，result.json 的 environment.git_head 等于当前测试提交，alembic_version 为 0007。59 项检查覆盖：27 个受保护操作匿名 401；6 个公开入口可达；未知路由匿名 401／登录 404；管理员创建三个账号并完成一次性链接；新账号空工作区；A 的项目对 B／管理员 404；伪造 owner／role 422；真实 PNG 字节精确；B／管理员读 A 资源 404；篡改参考图 409 且不泄漏字节；未认领项目对所有账号 404；双会话指定撤销；缺 CSRF 403；外部 Origin 403；普通用户管理接口 403 且审计 permission_denied。

### 3.7 秘密标记复核

门禁第六个测试已自动扫描。人工复核时只查看测试输出的通过／失败，不要把真实密码、会话 Cookie 或模型密钥写进命令行历史、工单或截图。若需要新增标记，使用明显假的测试字符串，并同时扫描响应正文、caplog、DATA_DIR 下文件名与文件原始字节。注意 /api/auth/session 按设计向当前会话返回其自己的 CSRF token，这是唯一允许的自身令牌回显；该 token 不得出现在其他账号响应、数据库明文、日志或文件名中。

## 4. 失败处理

| 现象 | 处理 |
|---|---|
| 门禁测试报操作集合不相等 | 有新出口未纳入清单，或旧出口被删除。先核对 OpenAPI，再更新清单和对应权限测试；不得直接改期望集合让测试通过。 |
| 匿名访问受保护 API 返回 200 | 立即停止发布；检查路由依赖和中间件默认拒绝。这是阻断级缺陷。 |
| 管理员能读取普通用户项目 | 检查 require_owned_project 是否被绕过；管理员不应有代看豁免。阻断级缺陷。 |
| 跨账号读取返回 403 或泄露对象存在性 | 统一改为 404；403 会暴露对象存在。 |
| 参考图返回 200 但字节被篡改 | 检查 sha256 与图片尺寸核验；不得更新摘要掩盖篡改。 |
| 生成结果在未发布状态返回字节 | 检查 G08／Delivery／GenerationJob 门禁；阻断级缺陷。 |
| 缺 CSRF 或错误 Origin 仍成功 | 检查中间件顺序和请求方法判断；不得只在前端隐藏按钮。 |
| 审计写入失败但账号已禁用 | 说明审计与业务不在同一事务；按 #58／#64 回滚并修复，不得手工补审计。 |
| 会话数据库故障返回 200 或匿名业务数据 | 鉴权故障必须 503 失败关闭；检查异常处理和默认拒绝。 |
| 并发初始化出现两个赢家 | 阻断级缺陷；检查数据库唯一约束和初始化事务，不得用重试掩盖。 |
| 并发链接两次都成功 | 阻断级缺陷；检查原子消费条件。 |
| 并发认领出现重复 legacy_claims 或双审计 | 阻断级缺陷；检查幂等键唯一约束和事务。 |
| SQLite database is locked（仅测试夹具） | 先确认各 Engine 是否预热连接池；业务唯一性断言不得删除。若真实服务出现，检查 busy_timeout 与写事务。 |
| 冒烟 403 ORIGIN_INVALID | 检查 APP_PUBLIC_URL、Origin 和 ALLOWED_ORIGINS 是否一致；不要关闭 Origin 校验。 |
| 冒烟 FileNotFoundError | 检查脚本解释器、PYTHONPATH 和工作树路径；不要复制测试进 main/。 |

## 5. 回滚

本票只新增测试，不改生产代码，也没有新增迁移。若门禁失败需要回退：

1. 停止发布或进入维护窗口。
2. 将应用代码回退到上一已验证提交；数据库保持 0007，不需要降级。
3. 保留 accounts、sessions、projects、legacy_claims 与审计记录；不要删除数据或审计来“恢复干净”。
4. 若必须降级数据库或恢复文件，先完成一致性备份并演练恢复，按 #54 与 #63 的维护流程执行。
5. 回退后重跑健康检查、门禁目标测试和真实 HTTP 冒烟，记录新提交与结果。

## 6. 证据与复核

- 验收记录：docs/acceptance/r1-gate.md
- 门禁测试：backend/tests/test_t64_r1_gate.py（6 passed；连续 3 次 6.68／6.67／6.70 秒）
- R1 全量：97 passed in 74.26s，.scratch/t64-r1-final
- 全量后端：307 passed in 269.64s + 235 passed in 258.52s = 542 passed，.scratch/t64-full-final-nonv2.log、.scratch/t64-full-final-v2.log
- 前端：33 文件／206 测试通过 19.70s；vue-tsc 0；Vite build 764ms；.scratch/t64-frontend-final/
- 迁移：fresh 0001→0007、chain 0003→0007；.scratch/t64-migration-final/
- 真实 HTTP：.scratch/t64-gate-smoke/run2/result.json（59 checks）；服务日志 .scratch/t64-gate-smoke/run2/uvicorn.log
- 代码／测试提交：17fb815de0ddfbcf8142910a508e2910773d7a1d
