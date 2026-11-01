# V3 T33 Release-GATE 最终发布验收记录

## 1. 结论与边界

本记录是 GitHub issue #52 的最终发布关口（#85 / V3 T33）验收证据，汇总 C0、R1、R2、R3 与 Release 各阶段的可复现结果。生产代码基线为 `f8f30d9af93e8632e1253a0b45a193b1245c5496`；发布包 `source_commit` 为文档提交 `2cb9d7f7adbecefef51b86ae7f292e0d953318ef`；合并前基线为 `integration/v3 5304734`。

结论：**代理可执行范围内的发布关口通过；物理手机与真实第二台局域网设备由用户按其指令 m22428 自行测试，代理不代签。** 没有发现可在现有环境中复现的阻断性代码失败；所有自动化测试、迁移、构建、发布包、秘密扫描、真实供应商闭环、HTTPS 容器客户端、独立操作者本机维护与容器 HTTPS 复现均已执行。物理设备自测、Pixel API 计费比例暂停与操作者环境 Docker Compose 插件缺失列在 §9 残余风险。

本记录不是对公网、生产机房、真实企业局域网防火墙或供应商数据保留政策的保证；测试数据根全部位于 `.scratch` 临时目录，未修改 `backend/data` 与 `main/backend/data`。

## 2. 版本、环境与代码

| 项目 | 值 |
|---|---|
| 仓库 | D:/Desktop/新建文件夹 |
| 验收 worktree | D:/Desktop/新建文件夹/.worktrees/t85 |
| 分支 | ticket/85 |
| 合并基线 | integration/v3 5304734 |
| 生产代码提交 | f8f30d9af93e8632e1253a0b45a193b1245c5496 |
| 发布包 source_commit | 2cb9d7f7adbecefef51b86ae7f292e0d953318ef |
| 迁移头 | 0010 |
| Python | 3.12.13 |
| 操作系统 | Windows 10.0.19045 SP0 |
| SQLite | 3.50.4 |
| FastAPI | 0.162.0 |
| Pydantic | 2.14.1 |
| SQLAlchemy | 2.1.6 |
| Alembic | 1.20.1 |
| Node.js / npm | v26.12.1 / 11.20.0 |
| Docker / Compose CLI | 29.8.2 build 7fc2dff；本机无 docker compose v2 插件，使用 docker run |
| 真实浏览器 | Microsoft Edge 156.0.4314.58（headless，CDP） |
| 真实文本供应商 | https://api.deepseek.com/v1，deepseek-flash |
| 真实图片供应商 | https://usfast.ailzd.com/v1，gpt-image-2-4k |
| 临时数据根 | .scratch/t85-*、.scratch/t85-evidence、.scratch/t85-real-loop |

所有后端测试命令均从 worktree 根执行，并清除继承的 `PYTHONIOENCODING`、`APP_DATA_DIR`、`DATA_DIR`、`WEB_DIST_DIR`；设置 `PYTHONPATH=D:/Desktop/新建文件夹/.worktrees/t85/main/backend`；解释器为 `D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe`。

## 3. 发布关口执行总表

| 关口 | 命令或步骤 | 结果 | 证据 |
|---|---|---|---|
| 专项发布测试 | pytest test_t64_r1_gate.py test_t70_r2_gate.py test_t75_image_size_gate.py test_t78_production_entry.py test_t79_release_package.py test_t80_https_lan.py test_t81_backup_schedule.py test_t82_restore.py test_t83_retention_secrets.py -q | 55 passed，47.47s | .scratch/t85-evidence/release-gate-tests.log |
| 后端非 v2 全量 | pytest 46 个非 v2 测试文件，分块执行 | 396 passed，2 warnings，415.87s | .scratch/t85-evidence/backend-nonv2-final.log |
| 后端 v2 全量 | pytest 19 个 v2 测试文件 | 247 passed，335.46s | .scratch/t85-evidence/backend-v2-final.log |
| 后端总计 | 非 v2 + v2 | 643 passed，0 failed | 以上两份日志 |
| 前端测试 | npm run test | 38 files / 173 tests passed，19.75s | .scratch/t85-evidence/frontend-test-final.log |
| 前端类型检查 | npm run typecheck | exit 0 | .scratch/t85-evidence/frontend-typecheck-final.log |
| 前端生产构建 | npm run build | 1664 modules，704ms；dist/index.html、index-Ci7ACHHo.css 430.41 kB、index-C26fmNBb.js 1,159.11 kB | .scratch/t85-evidence/frontend-build-final.log |
| 迁移全新链 | 临时 APP_DATA_DIR，alembic upgrade head | 0001→0010，26 表，db 503,808 bytes | .scratch/t85-evidence/migration-final.log |
| 迁移保留链 | 0001 创建项目/消息后升级到 0010 | 项目 revision 0、brief_status draft、消息保留；owner 与 intent 列存在 | .scratch/t85-evidence/migration-final.log |
| 真实供应商闭环 | real_loop.py run24，临时 SQLite + 完整 App + DeepSeek + usfast | S10 completed，发布 PNG 3840×2160，8,440,394 bytes，SHA256 c297fc… | .scratch/t85-real-loop/run24/result.json、published.png、.scratch/t85-evidence/run24-image.json |
| 登录/账号浏览器 | Edge CDP，HTTPS，42 项检查 | ok true，42 checks，无模型调用 | .scratch/t85-evidence/auth-browser-42.json |
| 工作台浏览器 | Edge CDP，HTTPS，35 项检查，1440/768/320 | ok true；docWidth 1440/768/320，无横向溢出 | .scratch/t85-evidence/workbench-browser-35.json |
| TLS 入口本机 | nginx TLS 容器 + 127.0.0.1:18822 | health/index/CSRF/login/session/logout/Origin/CSRF/HTTP 308 全部符合 | .scratch/t85-evidence/tls-client-http-smoke.json |
| TLS 第二网络命名空间客户端 | node:24-alpine 容器，独立网络，CA 校验 | 12 项检查 ok true；TLS authorized true；无证书绕过 | .scratch/t85-evidence/container-tls-client.json |
| 路由清单 | route_inventory.py | 37 operations = 6 public + 31 protected；31 匿名全部 401 | .scratch/t85-evidence/route-inventory.json |
| 发布包 | tools/release_package.py --build --no-build；--check --require-dist | 185 files，zip 983,274 bytes；179 tracked，24 required，issues [] | .scratch/t85-evidence/release-build-final.json、release-check-final.json |
| 发布包秘密扫描 | tools/secret_scan.py --root … --zip … | ok true，markers 0，hits [] | .scratch/t85-evidence/secret-scan-release-final.json |
| 独立操作者 | 独立上下文复制发布包、全新数据、TTY 首管理员、启动、备份、恢复、故障关闭、容器 HTTPS 入口 | 本地运维路径 PASS；容器 HTTPS 12 项 PASS；生产 LAN/防火墙/物理设备由用户自测 | .scratch/t85-independent-operator/operator-report.md |
| 真实数据演练 | #84 真实根只读核源 + 字节副本迁移/认领/恢复 | 实际根未修改；副本演练完成 | docs/acceptance/release-real-data-rehearsal.md、.scratch/t84-evidence |

## 4. 执行命令与结果

### 4.1 专项发布关口测试

~~~text
Set-Location D:/Desktop/新建文件夹/.worktrees/t85
Remove-Item Env:PYTHONIOENCODING, Env:APP_DATA_DIR, Env:DATA_DIR, Env:WEB_DIST_DIR -ErrorAction SilentlyContinue
$env:PYTHONPATH='D:/Desktop/新建文件夹/.worktrees/t85/main/backend'
& D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_t64_r1_gate.py backend/tests/test_t70_r2_gate.py backend/tests/test_t75_image_size_gate.py backend/tests/test_t78_production_entry.py backend/tests/test_t79_release_package.py backend/tests/test_t80_https_lan.py backend/tests/test_t81_backup_schedule.py backend/tests/test_t82_restore.py backend/tests/test_t83_retention_secrets.py -q
~~~

结果：`55 passed in 47.47s`。日志：`.scratch/t85-evidence/release-gate-tests.log`。

### 4.2 后端全量回归

非 v2 共 46 个测试文件，按三块执行以避免 600 秒工具上限；v2 共 19 个文件单独执行。最终结果：

~~~text
非 v2: 396 passed, 2 warnings in 415.87s
v2:    247 passed in 335.46s
合计:  643 passed
~~~

日志：`.scratch/t85-evidence/backend-nonv2-final.log`、`.scratch/t85-evidence/backend-v2-final.log`。

回归期间发现并修复了一个真实兼容问题：`main/backend/app/workflow/stages/s12.py` 的 `_s09_recovery_inputs` 仍按旧签名调用 `s09._rule_payload(rules, requirement_text, forbidden, size)`，而 #85 已把 S09 签名改为包含 `snapshot`。首次 v2 全量出现 1 个超时失败（`test_v2_t17_restart_recovery.py::test_force_kill_during_review_restart_is_read_only_and_continue_reuses_same_image`）；修正调用并单测复跑 `1 passed` 后，v2 全量 245 passed；最终 v2 为 247 passed（含新增回归测试）。

### 4.3 前端回归、类型检查与构建

~~~text
Set-Location D:/Desktop/新建文件夹/.worktrees/t85
npm run typecheck
npm run test
npm run build
~~~

结果：typecheck exit 0；Vitest 38 files / 173 tests passed in 19.75s；Vite build 1664 modules，704ms，产物 `main/dist/index.html`、`assets/index-Ci7ACHHo.css`（430.41 kB）、`assets/index-C26fmNBb.js`（1,159.11 kB）。日志：`.scratch/t85-evidence/frontend-typecheck-final.log`、`frontend-test-final.log`、`frontend-build-final.log`。

#77 删除旧四页后测试数从 44 files / 255 tests 降为 38 / 173；被删除的 90 个旧页面测试已在 `docs/acceptance/r3-gate.md` 逐项映射到替代覆盖，不是降低验收门槛。最终新增 `tests/t77-r3-gate.spec.ts` 及 t73/t74/t75/t76 测试保留。

### 4.4 迁移链与数据边界

脚本：`.scratch/t85-acceptance/migration_check.py`。结果：

- 全新 `APP_DATA_DIR`：alembic 从空库升级到 `0010`；`alembic_version=0010`；26 张表，包括 `accounts`、`account_links`、`sessions`、`rate_limit_facts`、`generation_intents`、`generation_usage`、`generation_slots`、`generation_quota_limits`、`generation_jobs`、`legacy_claims`、`workflow_runs`；数据库 503,808 bytes。
- 保留链：先在 `0001` 创建项目「迁移保留项目」revision 0、brief_status draft 和消息「迁移保留消息」，再升级到 `0010`；两条记录均保留，`projects.owner_account_id` 与 `generations.intent_id` 列存在。
- 两套迁移目录 `backend/migrations/versions` 与 `main/backend/migrations/versions` 均包含 `0001_initial_schema.py` 至 `0010_r2_local_tracking_closure.py`。

日志：`.scratch/t85-evidence/migration-final.log`。

### 4.5 真实供应商闭环（run24）

运行脚本：`.scratch/t85-acceptance/real_loop.py`，临时 SQLite + 完整 `app.production`，端口 18813。真实文本供应商为 DeepSeek 官方 `https://api.deepseek.com/v1`、模型 `deepseek-flash`；真实图片供应商为 `https://usfast.ailzd.com/v1`、模型 `gpt-image-2-4k`。Pixel API 的 `room-488` 因 `group_ratio_changed` 暂停且未确认计费比例，未用于本次验收。

结果（`.scratch/t85-real-loop/run24/result.json`）：

| 项目 | 值 |
|---|---|
| base | http://127.0.0.1:18813 |
| project_id | d7921cfd-3c24-4f74-87ff-95ca73db43b3 |
| account_id | 2a8ba26d-ef14-4238-9289-51495470636c |
| request_id | f4767099-1f00-4d3f-a1e7-6619cde49f73 |
| stage / status | S10 / completed |
| delivery | a3bce863-78da-46fa-a034-6b9b4b1e86ee，status published |
| asset | c80ce4af-0273-4829-b71b-811ace44b0bf，filename delivery-c80ce4af-r1.png |
| download | 8,440,394 bytes，image/png |
| image | 3840×2160 RGB，SHA256 c297fccf0f6e4783e544e4a55c1e6ed926d6573a62ab860af72fac1b9f1b7381 |
| job | 2ca24033-0233-4d44-9740-95c2542fe9b2，status published |
| intent | accepted→executing→delivered；usage used 1/20，personal 0/1，instance 0/2，project 0/1 |
| 角色调用 | S07 generation_engineer、S07 output_reviewer、S09 quality_compliance、S09 output_reviewer、S10 delivery_manager、S10 output_reviewer 均 status ok；fingerprint openai-chat:deepseek-flash:83a32c1dbe5db310a60b019a4d0f3584 |
| artifacts | final_brief、prompt_plan、quality_report、delivery_manifest 均 approved；四个输出审批均由 output_reviewer 签发，需求卡审批为 user |

真实闭环的六次角色调用时间（UTC）：12:21:25.787→12:21:41.135、12:21:41.155→12:21:54.655、12:22:56.125→12:23:03.489、12:23:03.511→12:23:24.470、12:23:24.627→12:23:27.404、12:23:27.425→12:23:58.065。所有调用均来自真实 DeepSeek 适配器；S08 为真实 usfast 图片生成，下载字节与资产摘要一致。

本票还修复了真实模型适配的以下发布关口问题，并保留回归测试：

- `openai_chat.py`：`max_tokens` 提升到 32768；`_extract_json` 只修复“恰好缺少一个最外层右花括号”的供应商输出，拒绝其他截断/畸形内容。
- `roles.py`：把供应商放在 `data` 内的 `issues/questions` 规范提升到角色包络顶层；非法原响应仍原样记录。
- `s07.py`、`s09.py`、`s10.py`：容忍自然语言覆盖与真实审查员的重复/附加 `review:*`、`G07_*` 语义检查；权威硬规则仍必须逐项出现并通过，未知/错误证据仍拒绝。
- `prompt_guard.py`：只把未被否定的营销风险词判为漂移；“不添加赠品”等明确否定不再误报。
- `s09.py`：质量审查的 composition/text/size 规则读取已确认快照；size 允许 technical+requirement+image 证据，但技术类主证据仍必需。

### 4.6 真实浏览器与三视口

两个 Edge CDP harness：

- `.scratch/t85-browser-auth/run_auth.mjs`：HTTPS + 临时 SQLite + 完整 App，42 项检查，`ok=true`；覆盖登录、账号安全、管理员账号页、一次性链接、移动卡片、中文 Unicode 密码、无注册/邮件入口、无模型调用、无外部请求。结果 `.scratch/t85-evidence/auth-browser-42.json`。
- `.scratch/t85-browser-workbench/run_browser.mjs`：35 项检查，`ok=true`；覆盖旧四路由无副作用跳转、U1 单页原位确认、四步执行状态、提示词折叠、轮询仅 GET、批准/发布/下载、历史标签、反馈新版本。结果 `.scratch/t85-evidence/workbench-browser-35.json`。

三视口严格断言：1440/1440、768/768、320/320（docWidth/innerWidth），tablet 溢出元素为空。修复了 `main/src/styles/workspace.css` 中 `.sidebar-toggle` 缺少 `position: relative` 导致隐藏提示使用初始包含块、768px 文档宽度被撑到 1020px 的问题。

真实 Android 手机和第二台物理局域网设备由用户按 m22428 自测；headless Edge 是真实桌面浏览器，Android 设备模拟未冒充物理手机。该边界见 §9。

### 4.7 HTTPS 入口与网络边界

本机 nginx TLS 容器把 `https://127.0.0.1:18822` 代理到 `127.0.0.1:18821` 的完整 App，HTTP :18823 仅 308 跳转。结果 `.scratch/t85-evidence/tls-client-http-smoke.json`：

- `/api/health` 200；`/` 200 text/html；CSRF 200；登录 200；session 200；projects 200 count 0；logout 204。
- 响应头：HSTS `max-age=31536000; includeSubDomains`、CSP、nosniff、Referrer-Policy no-referrer、X-Frame-Options DENY、Permissions-Policy 相机/麦克风/定位为空。
- Cookie：`__Host-session` 与 `__Host-csrf` 均 `Secure; HttpOnly; SameSite=strict; Path=/`。
- 错误 Origin 403 `ORIGIN_INVALID`；缺失 CSRF 403 `CSRF_INVALID`；HTTP 308 到 HTTPS。
- 直接访问后端并伪造 Host `evil.example` 403 `HOST_INVALID`；匿名 `/api/projects` 401 `AUTH_REQUIRED`。

第二网络命名空间客户端：`node:24-alpine` 容器，独立网络，挂载测试 CA 并以 `NODE_EXTRA_CA_CERTS` 校验证书，12 项检查全部通过；TLS authorized true，subject CN=host.docker.internal，issuer CN=T80 Local Smoke CA，SAN 包含 host.docker.internal/localhost/127.0.0.1/10.13.51.8；登录 session 96be9fc5-a6d8-4284-82b9-842c67d3d9e0。结果 `.scratch/t85-evidence/container-tls-client.json`。这是受控容器网络，不是物理局域网设备。

证书 SAN 与信任测试来自本地测试 CA（CN=T80 Local Smoke CA），测试后私钥和容器已清理/停止；不是生产证书。Windows `curl.exe --cacert` 受 Schannel 影响返回 000，最终以 Python httpx + CA 和 Node TLS 结果为准。

### 4.8 发布包与秘密扫描

从 worktree 根执行：

~~~text
python tools/release_package.py --build --no-build --output-root release-artifacts --json
python tools/release_package.py --check --require-dist --json
python tools/secret_scan.py --root release-artifacts/ecommerce-image-workspace-0.1.0 --zip release-artifacts/ecommerce-image-workspace-0.1.0.zip --json
~~~

结果：`ecommerce-image-workspace 0.1.0`，185 files，zip 983,274 bytes，source_commit `2cb9d7f7adbecefef51b86ae7f292e0d953318ef`；check 选中 179 个 tracked 文件、dist 3 个、required 24 个、issues []；secret scan ok true、markers 0、hits []。日志：`.scratch/t85-evidence/release-build-final.json`、`release-check-final.json`、`secret-scan-release-final.json`。

发布包只包含 `backend`、`dist`、`src`、`docker`、配置/文档与必要元数据；排除 `.env`、`.venv`、`tests`、`__pycache__`、数据库、日志和临时目录。唯一允许的敏感外观路径为 `backend/.env.example` 与 `backend/data/.gitkeep`。

### 4.9 独立操作者复现

独立上下文操作者使用 `.worktrees/t85/release-artifacts/ecommerce-image-workspace-0.1.0`，复制到 `.scratch/t85-independent-operator/package`，在全新 `APP_DATA_DIR` 上复现：

| 步骤 | 结果 |
|---|---|
| 183 条 SHA256SUMS 与 manifest 校验 | PASS |
| 空库迁移 0001..0010 | PASS |
| `python -m app.production --check --json` | exit 0 |
| 交互式 TTY `python -m app.auth.cli init-admin` | PASS；operator@example.test，无命令行明文密码 |
| 生产入口 health/CSRF/login/session/projects | PASS（回环 HTTP） |
| 启动备份追赶、verify --backup | PASS；2 个快照，chosen backup-20261101T142055Z-673ae510 |
| 停服恢复、会话撤销、restore gate | PASS；sessions_revoked 2，safety backup 20261101T142300Z-cb0ca974，gate 到 2026-11-02T14:23:00.137Z；旧会话 401，新登录 200 |
| 崩溃/重启故障安全 | PASS；job PROCESS_INTERRUPTED，submit_attempted=0，remote_task_id=null，无自动重提 |
| HTTPS/LAN 与防火墙 | 首轮无 compose v2；follow-up 以 `docker run` 启动打包 nginx TLS，独立 node:24-alpine 客户端 12 项 PASS（TLS authorized、health/index/CSRF/login/session/projects/logout、坏 Origin 403、缺 CSRF 403、HTTP 308）；生产 LAN/防火墙由用户自测 |

报告同时发现文档缺陷：`docs/operations/release-restore-recovery.md` 曾写 `verify <backup-dir>`，实际 CLI 为 `verify --backup <backup-dir>`；本票已修正该文档。独立操作者结论为本地发布运维路径 PASS、容器 HTTPS 入口 PASS；未声明物理手机、生产 LAN 或真实模型生成已验证。

### 4.10 备份、恢复、故障安全与真实数据演练

- 周期备份与轮换：`test_t81_backup_schedule.py` 10 passed；真实 smoke 产生 3 个 verified 快照并安全轮换，状态文件原子、无秘密文件、无网页导出路由。
- 受控恢复：`test_t82_restore.py` 5 passed；真实 smoke 旧密码 401、新密码 200、撤销交付 409、restore gate 阻断新意图、clear 后 S07 可受理。
- 保留期与秘密隔离：`test_t83_retention_secrets.py` 7 passed；真实 smoke 旧候选文件删除、legacy/published 保留、120 天审计删除、受保护记录保留、备份 verify 通过、秘密扫描 0。
- 真实数据演练：#84 对实际根 `D:/Desktop/新建文件夹/backend/data` 只读核源，内容摘要 `2b56fa60ed855defc6669c779646a11762c9ee349490b7fc11134a5e8210aee9`，14 files/30,599,431 bytes；副本迁移 0001→0010、备份、认领、恢复演练完成；实际根摘要未变。详见 `docs/acceptance/release-real-data-rehearsal.md` 与 `.scratch/t84-evidence`。

## 5. SRS 正文 45 项需求覆盖

来源：`D:/Desktop/新建文件夹/vesion/v3/SRS_v3.md` 第 3–11 节，共 45 个正文编号。每行“证据”均为本票实际执行或本仓库已提交测试；环境与命令见 §2、§4。

| 编号 | 需求摘要 | 最终证据 | 结论 |
|---|---|---|---|
| V3-NET-01 | 受控 HTTPS 同源入口；显式监听/主机/防火墙；后端、数据库、数据与备份不直接发布 | t80 + §4.7 nginx TLS；后端仅绑 127.0.0.1:18821；容器客户端 12 项；t64 Host/Origin | 代理证据通过；物理 LAN 设备由用户自测 |
| V3-NET-02 | 本地证书信任/更新/排障；HTTP 仅回环开发；不全局关闭 TLS 校验 | t80 5 passed；TLS 容器客户端 rejectUnauthorized=true；HTTP 308；配置校验拒绝非回环 HTTP | 通过 |
| V3-NET-03 | 校验 Host/Origin、限制 CORS、可信代理；默认拒绝；数据库/鉴权故障安全关闭 | t64 37 路由默认拒绝；t80 代理 CIDR/X-Real-IP；t55 数据库故障 503；TLS 坏 Host/Origin | 通过 |
| V3-ACC-01 | 首管理员仅本机维护命令；并发只成功一次；有管理员拒绝重入；无默认密码/网页抢注 | t55 31；t64 并发初始化；独立操作者 TTY init-admin；CLI 非 TTY 拒绝 | 通过 |
| V3-ACC-02 | 管理员列表/建普通用户/禁用恢复/签发链接/配额；普通用户不可；无删除/改邮箱/代登 | t56 16；t64 权限矩阵；t58 9；t67 5；前端 AdminAccountsPage | 通过 |
| V3-ACC-03 | 不可变账号 ID；邮箱 trim+大小写不敏感唯一；待设置/可登录/禁用；禁用撤销会话与链接 | t55 邮箱规范化；t56 唯一；t58 禁用/恢复；t64 状态矩阵 | 通过 |
| V3-ACC-04 | 登录后改密校验旧密码；忘记密码只联系管理员；管理员签发一次性链接；本机恢复；无网络恢复 | t57 12；t58 9；t56 链接；docs/operations/r1-local-admin-recovery.md | 通过 |
| V3-ACC-05 | 256 位随机链接、摘要存储、账号/用途/时间绑定、30 分钟、签发新链接撤销旧链接、明文仅一次 | t56 16；t64 并发链接；浏览器 auth 42；迁移 0004 | 通过 |
| V3-ACC-06 | 链接来自 APP_PUBLIC_URL；无第三方资源；Referrer no-referrer；GET 不消费；POST 原子消费 | t56；t64 并发 200/400；浏览器 42；TLS headers；日志扫描 | 通过 |
| V3-PWD-01 | 15–128 Unicode 码点、空格/Unicode/粘贴/密码管理器、弱口令列表、不静默截断 | t55 密码策略；浏览器中文密码；t56 设置复用 | 通过 |
| V3-PWD-02 | Argon2id m>=19456 KiB、t>=2、p=1、随机盐、算法元信息、库缺失失败 | t55 参数/盐/失败；代码审计 `app/auth/passwords.py` | 通过 |
| V3-PWD-03 | 登录/链接签发/验证后端限流与资源上限；失败响应不可枚举；跨客户端并发有效 | t55 登录 5/15m + 30/15m、HashGate；t56 签发 10/15m + 3/30m、验证 5/15m + 30/15m；t64 | 通过 |
| V3-SES-01 | 高熵服务端会话、摘要存储、Secure/HttpOnly/SameSite=Strict/Path=/ __Host-、登录换发、无秘密入 URL/Storage/包 | t55 Cookie/换发/伪造；t64 双会话；浏览器 42；前端构建扫描 | 通过 |
| V3-SES-02 | 空闲 30 分钟/绝对 12 小时、服务端时间、主动操作续空闲、轮询不续期、过期不复活 | t55 可控时钟；t57 会话投影；t64 双会话 | 通过 |
| V3-SES-03 | 多端会话、列表/撤销指定/撤销其他、退出当前、改密/重置/禁用撤销全部、其他账号不受影响 | t57 12；t58 9；t64 12；独立操作者恢复撤销 2 会话 | 通过 |
| V3-SES-04 | 所有状态变更 CSRF + 来源校验；GET 不承担；同源返回地址；无外部跳转 | t64 全变更动作；t55/t56；浏览器 42 坏 Origin/缺 CSRF；TLS 403 | 通过 |
| V3-SES-05 | 失效隐藏业务、保留原任务归属、退出/切号清理、迟到响应隔离、历史返回重验、no-store | t71 session binding；t72 跨标签；浏览器 42/35；私有响应 no-store | 通过 |
| V3-SEC-01 | 后端主体/角色/归属/门禁；不信客户端字段；参数化查询；受控文件根 | t59 5；t60 3；t62 5；t64；t61 静态/路径 | 通过 |
| V3-SEC-02 | 不可信文本/模型输出安全渲染；CSP；上传/解码/压缩炸弹/提示注入继承 v2 | t64 秘密/注入回归；t05/v2_t04/v2_t03 全量；TLS CSP；浏览器无外部脚本 | 通过 |
| V3-SEC-03 | 模型密钥仅后端；不进入 DB/日志/提示词/前端/文件名/下载；错误收敛；不返回内部栈 | t64 秘密标记；release secret_scan 0；t83；浏览器 42 | 通过 |
| V3-ACL-01 | 每个项目稳定所有者；子对象经项目关系继承；覆盖消息/资产/生成/run/artifact/review/approval/delivery/diagnostic | t59 项目 owner；t60 归属闭合；t62 job/generation owner；0005/0006 迁移 | 通过 |
| V3-ACL-02 | 列表/分页/搜索/详情/历史/缓存/错误/轮询/缩略图/下载全部默认拒绝并过滤；静态/源文件/locator 不可绕 | t61 4；t64 37 路由；t59 列表过滤；t75 历史过滤；route-inventory | 通过 |
| V3-ACL-03 | 无会话 401；非管理员管理操作拒绝；非本人/不存在统一不可获得；本人未放行按 v2 安全状态；管理员无超管分支 | t64 权限矩阵；t59 404 统一；t61 资产 409/404；t60 | 通过 |
| V3-ACL-04 | 创建/引用/下载/找回/后台任务校验账号与项目一致；记录实际操作者；保留业务审查角色 | t60 跨账号 404/审计；t62 授权引用；t65 意图绑定；t67 审计 | 通过 |
| V3-ACL-05 | 后台任务持久化 owner/授权/版本；退出不撤销已授权任务；禁用阻断新模型调用/发布；未知不伪称取消；恢复不自动重发 | t62 5；t68 7；t19/t69；run24 真实任务 | 通过 |
| V3-UX-01 | 浅灰青视觉、品牌「电商生图工作台」、本地资源、无参考站业务、尊重 reduced motion | t76；浏览器 auth 42；前端构建无外部请求 | 通过 |
| V3-UX-02 | 邮箱/密码/显示隐藏/提交中/失败/受限/联系管理员；标签/焦点/错误/autocomplete；链接错误安全；账号/管理页完整 | t76；浏览器 auth 42；t56；AdminAccountsPage | 通过 |
| V3-UX-03 | 桌面左沟通参考/右需求卡或结果；手机单列；状态替代四页；细节折叠；小屏不横向溢出 | t73/t77；workbench 35；1440/768/320 严格 docWidth | 通过 |
| V3-UX-04 | 历史图不改当前需求/不触发模型；修改生成新版本；旧图标注；执行提示词批准后才可折叠 | t73/t75；projection.ts；workbench 35；t74 | 通过 |
| V3-UX-05 | 三视口可操作；键盘/中文输入/标签/焦点/状态；错误不依赖颜色；草稿仅页面内存并绑账号 | t76/t77；浏览器 42/35；t72 清理；t73 | 通过（真实手机缺失） |
| V3-GEN-01 | 最终卡独立批准后一次主操作；后端校验账号/项目/revision/摘要/参考/配置/额度并持久化意图 | t65 5；t66 6；t70 12；run24 | 通过 |
| V3-GEN-02 | 意图绑定账号/项目/需求版本/卡片摘要/参考用途/尺寸/稳定请求 ID；先批提示词再生图；漂移回 U1 | t65/t66；s07/prompt_guard 回归；run24 六角色 | 通过 |
| V3-GEN-03 | 同请求幂等返回原意图/计数；同 ID 变参拒绝；确认失败不受理；受理后失败不自动重跑；未知先核实 | t65/t66/t68/t70；真实 run24 | 通过 |
| V3-GEN-04 | 仅 G06–G08 与身份/归属同时满足才发布；返回图片/下载；候选/落盘/未审不发布；所有访问重验 | t61/t75/t65；run24 published；t64 资产门禁 | 通过 |
| V3-LIM-01 | 默认 20/24h、个人 1、实例 2、项目 1；管理员可配正整数；普通用户只读；管理员无豁免；降额不取消/清零 | t67 5；t70 独立连接竞争；前端 quota 面板 | 通过 |
| V3-LIM-02 | 同一事务检查账号/归属/幂等/额度/槽位并计数；拒绝不计；受理后失败/未知计；恢复/核实/找回/重放不另计；不排队 | t65/t69/t70；run24 usage | 通过 |
| V3-LIM-03 | 账本/槽位/恢复持久化；并发/重启/窗口切换不穿透/清零；未知不自动当未受理；恢复遵循 v2 | t68 独立进程；t70 窗口边界；t82 恢复 gate；t69 | 通过 |
| V3-DATA-01 | 识别真实数据根；多源冲突停止选择；验证备份后管理员确认认领；未认领不可见；幂等；失败隔离 | t63 6；真实数据演练 #84；t84 文档 | 通过（副本演练） |
| V3-DATA-02 | 认领只定 owner，不自动批准 legacy；不伪造操作者；发布/参考/审批随版本保存；清理不误删 | t63；t83；t84；r1-legacy-claim | 通过 |
| V3-DATA-03 | 最小持久化；凭证原文不落库；角色候选/原始响应必要摘要；业务记录不混入普通日志 | t55/t56；t83 审计/诊断清理；release 扫描；v2 保留策略 | 通过 |
| V3-AUD-01 | 登录成功/失败/限流、退出/撤销、设置/重置/改密、建号、禁用/恢复、额度、拒绝、认领、本机维护；时间/类型/主体/结果/原因/关联 ID | t58 9；t63 6；t67 5；t55；审计目录 | 通过 |
| V3-AUD-02 | 普通用户仅本人安全事件；管理员必要审计不含正文/密钥/Cookie/链接/Base64；敏感变更同事务；审计失败回滚；UTC | t58；t63；t67；t83 90 天；t64 审计回滚 | 通过 |
| V3-OPS-01 | 受限备份目录；运行时每日一致快照；WAL；文件/清单与 DB 一致；账号/归属/图片/审批/额度；验证后轮换；失败保留；无网页导出 | t81 10；t54 19；t83；真实 smoke 3 快照 | 通过 |
| V3-OPS-02 | 维护状态、停写、验证、回退点；恢复核验 owner/文件/审批；撤销会话/链接；到期规则；不自动重发；缺口阻断 | t82 5；t70；独立操作者恢复；t84 副本演练 | 通过 |
| V3-OPS-03 | 启动/LAN/证书/管理员/数据根/备份/恢复/故障说明；最小权限；不承诺物理擦除/同盘容灾；不可核验则停止 | docs/operations 全套；独立操作者 report 含容器 HTTPS；t80 配置校验 | 通过（本机与容器网络；物理 LAN 由用户自测） |

## 6. V3-AC-01..40 最终覆盖表

环境见 §2；命令、步骤与结果见 §4；下表每行均指向已执行的测试或真实检查。

| AC | 对应需求 | 最终证据 | 结论 |
|---|---|---|---|
| V3-AC-01 | V3-NET-01/02 | §4.7 nginx TLS + 独立容器客户端；TLS authorized true、SAN 正确、无证书绕过；后端仅 127.0.0.1；直接后端坏 Host 403；HTTP 308。物理第二台 LAN 设备由用户按 m22428 自测 | 代理证据通过；物理设备用户自测 |
| V3-AC-02 | V3-NET-02/03 | t80 5 passed：坏 Host/Origin、伪造 X-Forwarded-For/X-Real-IP、非受信代理、HTTP 开发开关、配置校验；TLS 坏 Origin/缺 CSRF；正常代理 200 | 通过 |
| V3-AC-03 | V3-ACC-01 | t55 31、t64 并发初始化 1 个 Account + 3 个 AlreadyInitialized；CLI 非 TTY 拒绝；独立操作者 TTY 首管理员；无默认密码/密码参数 | 通过 |
| V3-AC-04 | V3-ACC-02/03 | t56 16：管理员建 pending、邮箱规范化唯一；t64 普通用户建号/管理 403；前端 AdminAccountsPage 无删除/改邮箱/代登 | 通过 |
| V3-AC-05 | V3-ACC-03/04 | t55 pending/disabled 不能登录；t57 改密校验旧密码；t58 禁用/恢复撤销会话与链接；t56 重置保持旧密码；无网络 recovery | 通过 |
| V3-AC-06 | V3-ACC-05/06 | t56 过期/重复/重发/错用途/禁用；t64 并发消费 200/400；GET 不消费不登录；签发不改旧密码；浏览器 42 | 通过 |
| V3-AC-07 | V3-ACC-05/06、SEC-03 | t64 秘密标记扫描响应/DB/日志/文件名；release secret_scan 0；浏览器认证包无秘密；Host 不能改链接 origin；Referrer no-referrer | 通过 |
| V3-AC-08 | V3-PWD-01/02 | t55 15/128 边界、空格、Unicode、长口令、粘贴、弱口令、Argon2id 参数/随机盐/rehash；浏览器中文密码 | 通过 |
| V3-AC-09 | V3-PWD-03 | t55 未知/pending/disabled/wrong 统一 401 + dummy hash；账号 5/15m、来源 30/15m；HashGate 2/8/第 9 503；t64 并发 429；非按钮防抖 | 通过 |
| V3-AC-10 | V3-SES-01 | t55 __Host Cookie 属性、登录换发、伪造/固定会话 401；t64 双会话；浏览器 42 无 Web Storage 秘密；前端包扫描 | 通过 |
| V3-AC-11 | V3-SES-02 | t55 可控时钟空闲/绝对边界、X-Polling 不续期、主动 mutation 续 idle、普通 GET 不续；t57 投影 | 通过 |
| V3-AC-12 | V3-SES-03、ACC-03 | t57 多端/撤销指定/其他；t58 改密/重置/禁用撤销全部；t64 双会话；独立操作者恢复 sessions_revoked 2；其他账号不受影响 | 通过 |
| V3-AC-13 | V3-SES-04 | t64 登录/退出/链接/上传/确认/管理动作缺 CSRF/错 Origin 全部拒绝；同源返回地址校验；浏览器 42 坏 Origin 403 | 通过 |
| V3-AC-14 | V3-SES-05、UX-05 | t71 会话绑定、t72 跨标签/pageshow/popstate/迟到响应/旧恢复键；t77 单工作台；前端 173 + 浏览器 35 | 通过 |
| V3-AC-15 | V3-ACL-01/02/03 | route-inventory 37（6 public+31 protected）；t64 31 匿名全部 401；认证未知 404；A/B/管理员统一 404；t61 静态/资产出口 | 通过（37 个当前出口） |
| V3-AC-16 | V3-ACL-01/02/04 | t59/t60/t61/t64 A/B 交换项目/参考/任务/artifact/approval/delivery/history/recovery ID 均 404；真实字节仅自己的 published 200；缩略图/历史过滤 | 通过 |
| V3-AC-17 | V3-ACC-02、SEC-01、ACL-03 | t59/t60 伪造 owner/is_admin 422、客户端字段忽略；t64 管理员读 A/B 项目/工作流/资源 404；无 superadmin 分支 | 通过 |
| V3-AC-18 | V3-GEN-04、ACL-02、DATA-02 | t61 待审/未发布/撤销/过期/缺失 409/404；静态/源文件/locator URL 404；t63 认领不批准 legacy；t75 历史过滤 | 通过 |
| V3-AC-19 | V3-ACL-05、SES-05 | t62 5：退出不改 owner、禁用停下一 poll、保留 remote_task_id、不自动重发、发布前重查；t68 恢复；run24 真实任务 | 通过 |
| V3-AC-20 | V3-SEC-01/02 | t60 恶意文件名不落地路径；t61 路径/locator；v2 上传/解压/注入回归随 643 全量；SQL 参数化；无外部任意请求 | 通过 |
| V3-AC-21 | V3-SEC-03、AUD-02 | t64 11 个响应/caplog/数据文件名/字节秘密扫描；release 包 secret_scan 0；t83 备份不含密钥；普通用户无完整诊断 | 通过 |
| V3-AC-22 | V3-UX-01/02 | t76 AuthFrame/BrandMark/PasswordField；浏览器 auth 42：本地品牌、无假注册/邮件、无参考站资源、账号页/管理页完整 | 通过 |
| V3-AC-23 | V3-UX-03/04/05 | t73/t74/t75/t77；旧四页删除且 /describe /confirm /generate /results 重定向保留 query/hash；workbench 35 单页原位三状态 | 通过 |
| V3-AC-24 | V3-GEN-01/02 | t65 5、t66 6、t70 12；确认前无 visual；S07 提示词批准后才 S08；run24 真实六角色；无预先虚构最终提示词 | 通过 |
| V3-AC-25 | V3-GEN-01/02、ACL-04 | t66 篡改 revision/卡摘要/参考/尺寸 409；t70 同请求变参不旁路；S07 漂移回 U1；t75 当前版本快照 | 通过 |
| V3-AC-26 | V3-GEN-03、LIM-02 | t65 稳定 request_id；t66 同 ID 变参冲突；t70 独立连接并发只一个意图/一次计数/应用侧至多一次提交；浏览器双击/重放 | 通过 |
| V3-AC-27 | V3-GEN-04、UX-04 | t74 轮询仅 GET；t71/t72 刷新/历史/重登不触发生成；workbench 35 无额外 POST；t66 查询不增调用；run24 无导航模型调用 | 通过 |
| V3-AC-28 | V3-GEN-03/04 | t68 未知/PROCESS_INTERRUPTED；t69 本地结案 remote_cancelled=false；t70 失败/未知保留；t75 未审不预览；无自动重生/假发布 | 通过 |
| V3-AC-29 | V3-GEN-02、DATA-02 | t75 单图+3840×2160+历史尺寸快照；t75_image_size_gate；run24 实际 3840×2160 PNG；反馈新版本不覆盖旧输出 | 通过 |
| V3-AC-30 | V3-LIM-01/03 | t67 配额覆盖/管理员无豁免/降额不取消；t70 独立引擎 personal/instance/project 竞争 exactly one；窗口下界 | 通过 |
| V3-AC-31 | V3-LIM-02 | t65/t69/t70：提交前拒绝不计；受理后失败/未知计一次；核实/恢复/找回/幂等重发不另计；新任务另计；无队列 | 通过 |
| V3-AC-32 | V3-LIM-03、OPS-02 | t68 两个独立子进程崩溃/重启；t70 数据库锁重试；t82 恢复 gate/账本底线；run24 重启不自动重发 | 通过 |
| V3-AC-33 | V3-DATA-01/02 | t63 6：单源/冲突源、备份前置、幂等、审计回滚、未认领 404；t84 副本迁移/认领/恢复；实际根未改；新账号空列表 | 通过（副本演练） |
| V3-AC-34 | V3-AUD-01/02 | t58 事件/视图/脱敏；t63 认领审计同事务；t67 额度 before/after/回滚；t64 审计故障回滚；t83 90 天清理 | 通过 |
| V3-AC-35 | V3-DATA-02/03、AUD-02 | t83 startup retention 保留 published/reference/approval/unclaimed；t84 迁移副本 14 文件/资产摘要不变；普通日志不含完整正文 | 通过 |
| V3-AC-36 | V3-OPS-01 | t81 10 + 真实 3 快照轮换；t54 19；t83 备份 verify/allowlist；无网页整库导出；秘密不进入备份 | 通过 |
| V3-AC-37 | V3-OPS-02 | t82 5：损坏快照拒绝、原数据可回退、恢复核验关联、sessions_revoked、链接撤销、过期不复活、账本缺口 409 | 通过 |
| V3-AC-38 | V3-UX-02/03/05 | Edge 三视口 1440/768/320 docWidth 严格相等，workbench 35；auth 42 标签/焦点/键盘/中文输入/无溢出；真实手机/桌面由用户按 m22428 自测 | 代理证据通过；真实设备用户自测 |
| V3-AC-39 | V3-SEC-03、DATA-03 | t60 外部模型说明在首次输入前；浏览器 42 无密码/会话/恢复秘密输入；run24 真实模型请求不含认证秘密；v2 247 回归保留；不承诺零保留 | 通过 |
| V3-AC-40 | V3-NET-03、OPS-03 | t64 审计/会话加载故障 503、归属失败 404/ACCOUNT_DISABLED；t55 DB 故障；独立操作者本机维护 PASS；操作者容器 HTTPS 12 项 PASS；生产 LAN/防火墙由用户自测 | 通过（本机 + 容器 HTTPS；物理 LAN 用户自测） |

## 7. 真实路由、资源出口与权限矩阵

### 7.1 37 个 OpenAPI 业务/资源出口

`D:/Desktop/新建文件夹/.scratch/t85-acceptance/route_inventory.py` 枚举当前 FastAPI OpenAPI，结果保存 `.scratch/t85-evidence/route-inventory.json`：**37 operations = 6 public + 31 protected**。`test_t64_r1_gate.py` 同时断言该集合精确等于 `PROTECTED_ROUTES`，并逐项对 31 个 protected 匿名请求断言 401 `AUTH_REQUIRED`；认证后的未知路由为 404 `ROUTE_NOT_FOUND`。

| # | 方法 | 路径 | 类别 | 匿名结果 | 权限/资源证据 |
|---:|---|---|---|---|---|
| 1 | GET | /api/health | public | 200 | 最小健康状态，无业务数据 |
| 2 | GET | /api/config/status | public | 200 | 仅能力/服务商说明，不含密钥 |
| 3 | GET | /api/auth/csrf | public | 200 | 仅签发 CSRF cookie/token |
| 4 | POST | /api/auth/login | public | 200/401 | 登录；限流与统一失败 |
| 5 | GET | /api/auth/link | public | 200/400 | 只读打开，不消费 |
| 6 | POST | /api/auth/link | public | 200/400 | 原子设置密码/消费链接 |
| 7 | GET | /api/admin/accounts | protected | 401 | 管理员账号列表 |
| 8 | POST | /api/admin/accounts | protected | 401 | 管理员建 pending 普通用户 |
| 9 | POST | /api/admin/accounts/{account_id}/disable | protected | 401 | 禁用并撤销会话/链接 |
| 10 | GET | /api/admin/accounts/{account_id}/generation-limits | protected | 401 | 读取账号额度覆盖 |
| 11 | PUT | /api/admin/accounts/{account_id}/generation-limits | protected | 401 | 修改账号额度并审计 |
| 12 | POST | /api/admin/accounts/{account_id}/links | protected | 401 | 签发一次性设置/重置链接 |
| 13 | POST | /api/admin/accounts/{account_id}/restore | protected | 401 | 恢复账号，不复活旧凭证 |
| 14 | GET | /api/admin/generation-limits | protected | 401 | 实例额度 |
| 15 | PUT | /api/admin/generation-limits | protected | 401 | 修改实例额度并审计 |
| 16 | GET | /api/admin/security-events | protected | 401 | 管理员安全审计视图 |
| 17 | GET | /api/auth/session | protected | 401 | 当前账号/会话投影 |
| 18 | POST | /api/auth/logout | protected | 401 | 撤销当前会话 |
| 19 | POST | /api/auth/password/change | protected | 401 | 校验旧密码并撤销全部会话 |
| 20 | GET | /api/auth/security-events | protected | 401 | 本人安全事件 |
| 21 | GET | /api/auth/sessions | protected | 401 | 本人会话列表，不含 token |
| 22 | POST | /api/auth/sessions/revoke-others | protected | 401 | 撤销本人其他会话 |
| 23 | DELETE | /api/auth/sessions/{session_id} | protected | 401 | 撤销本人指定会话 |
| 24 | GET | /api/assets/{asset_id} | protected | 401 | 仅本人且内容门禁放行的资产字节/下载 |
| 25 | GET | /api/generations/{generation_id} | protected | 401 | 本人生成记录/恢复状态 |
| 26 | POST | /api/generations/{generation_id}/retrieve-result | protected | 401 | 只读找回原结果，不重新提交 |
| 27 | GET | /api/projects | protected | 401 | 本人项目列表，服务端过滤后分页 |
| 28 | POST | /api/projects | protected | 401 | 创建本人项目 |
| 29 | GET | /api/projects/{project_id} | protected | 401 | 本人项目详情 |
| 30 | PATCH | /api/projects/{project_id}/brief | protected | 401 | 本人需求修订 |
| 31 | POST | /api/projects/{project_id}/confirm | protected | 401 | 旧确认入口，仅兼容路径并受归属/门禁 |
| 32 | POST | /api/projects/{project_id}/generations | protected | 401 | 旧生成入口，有 WorkflowRun 时 409 LEGACY_FLOW_DISABLED |
| 33 | POST | /api/projects/{project_id}/messages | protected | 401 | 本人对话消息 |
| 34 | POST | /api/projects/{project_id}/reference | protected | 401 | 本人参考上传，先校验归属再解析 multipart |
| 35 | DELETE | /api/projects/{project_id}/reference | protected | 401 | 本人参考移除 |
| 36 | GET | /api/projects/{project_id}/workflow | protected | 401 | 本人工作流投影，只读 |
| 37 | POST | /api/projects/{project_id}/workflow/actions/{action_id} | protected | 401 | 本人且当前阶段允许的动作；服务端 revision 校验 |

资源字节另按以下边界验证：`/api/assets/{id}` 仅 published+approved+available 返回 200；待审、撤销、隔离、过期、缺失 409/404；`/references/{id}`、`/generated/{id}`、`/published/{id}`、`/data/*` 静态路径及供应商 locator 形状 URL 全部 404；只有本人 published 资产可 inline/下载，跨账号/管理员无豁免。

### 7.2 权限矩阵（SRS 7.1）逐格验证

| 能力/对象 | 未登录 | 普通用户 | 应用管理员 | 自动化/真实证据 |
|---|---|---|---|---|
| 登录页、公共品牌、最小健康状态 | 允许 | 允许 | 允许 | health/config/csrf 公开；login/link 公开；t64 |
| 有效一次性链接的密码设置页 | 仅该凭证用途 | 同左 | 同左 | t56 GET/POST link；GET 不消费；错用途/过期拒绝 |
| 个人密码、自己的会话列表及撤销 | 拒绝 | 仅自己 | 仅自己 | t57；t64 双会话；管理员不能读他人会话 |
| 项目、对话、需求、工作流、版本与参考图 | 拒绝 | 仅自己项目 | 仅自己项目 | t59/t64；管理员读 A/B 404；列表 owner 过滤 |
| 图片预览、缩略图、下载、交付/审批摘要 | 拒绝 | 仅自己且门禁放行 | 仅自己且门禁放行 | t61/t64/t75；未审/撤销/过期 409/404 |
| 上传、澄清、确认、提交生成、手动安全恢复 | 拒绝 | 仅自己且门禁放行 | 仅自己且门禁放行 | t60/t62/t65/t68；跨账号 404；revision/摘要校验 |
| 必要账号列表、普通用户建号/禁用/恢复/重置 | 拒绝 | 拒绝 | 允许，记录审计 | t56/t58/t64；普通用户 403；审计同事务 |
| 额度调整、实例配置诊断、必要安全审计 | 拒绝 | 仅本人额度/安全视图 | 允许管理，不含他人正文和秘密 | t67；t58 安全事件投影；管理员视图脱敏 |
| 他人项目代看、代登、公开图片链接、整库网页导出 | 拒绝 | 拒绝 | 拒绝 | t59/t61/t64；无代登/导出路由；管理员无读豁免 |
| 首次初始化、管理员遗失恢复、整库备份/恢复 | 无网络入口 | 无网络入口 | 应用角色不授权；仅本机维护者 | CLI 非 TTY 拒绝；独立操作者 TTY；无 HTTP route |

## 8. 证据清单与可复核入口

所有临时数据库、截图、JSON 与日志都在仓库外 `D:/Desktop/新建文件夹/.scratch/`，不进入发布包，也不写入真实数据根。主要入口如下：

| 证据 | 路径 | 复核方式 |
|---|---|---|
| 最终代码提交 | `f8f30d9af93e8632e1253a0b45a193b1245c5496` | `git show --stat f8f30d9` |
| 发布包构建提交 | `2cb9d7f7adbecefef51b86ae7f292e0d953318ef` | `git show --stat 2cb9d7f` |
| 最终后端回归 | `.scratch/t85-evidence/backend-nonv2-final.log`、`backend-v2-final.log` | 396 + 247 = 643 passed，0 failed |
| 聚焦发布门禁 | `.scratch/t85-evidence/release-gate-tests.log` | 9 个门禁文件 55 passed |
| 前端 | `.scratch/t85-evidence/frontend-typecheck-final.log`、`frontend-test-final.log`、`frontend-build-final.log` | typecheck 0；38 files/173 tests；build 1664 modules |
| 迁移 | `.scratch/t85-evidence/migration-final.log` | fresh 0001→0010；链式升级保留项目/消息；26 表 |
| 真实供应商闭环 | `.scratch/t85-real-loop/run24/result.json`、`published.png`、`.scratch/t85-evidence/run24-image.json` | 六次真实角色调用；PNG 3840×2160；交付摘要一致 |
| 真实供应商原始探针 | `.scratch/t85-acceptance/probe_reviewer_full.py`、`probe_image_real.py`、`real-image-3840x2160.png` | 真实 DeepSeek 文本/视觉；真实 usfast 4K 图片 |
| 浏览器认证 | `.scratch/t85-evidence/auth-browser-42.json` | 42 checks，ok true |
| 浏览器工作台 | `.scratch/t85-evidence/workbench-browser-35.json` | 35 checks，docWidth 1440/768/320 严格不溢出 |
| 容器 TLS 客户端 | `.scratch/t85-evidence/container-tls-client.json`、`tls-client-http-smoke.json` | 12 checks；TLS authorized true；安全头与 Cookie |
| 发布物 | `release-artifacts/ecommerce-image-workspace-0.1.0.zip`、`.scratch/t85-evidence/release-build-final.json`、`release-check-final.json`、`secret-scan-release-final.json` | 185 files；179 tracked；24 required；0 issues；0 secret hits |
| 独立操作者 | `.scratch/t85-independent-operator/operator-report.md` | 183+183 摘要；迁移/首管/备份/恢复/故障安全 PASS |
| 路由清单 | `.scratch/t85-evidence/route-inventory.json` | 37 operations = 6 public + 31 protected |
| 真实数据演练 | `docs/acceptance/release-real-data-rehearsal.md`、`.scratch/t84-evidence/` | 副本迁移/认领/恢复；实际根摘要未变 |

## 9. 残余风险与真实边界

1. **物理设备由用户自测（AC-01 的“另一台局域网设备”与 AC-38 的“真实手机”部分）**：用户已在 m22428 明确“真实设备由我自己测试，其他的内容继续”。代理没有把独立 node:24-alpine 容器或 Edge 仿真冒充物理设备；容器 HTTPS 12 项与 Edge 三视口仅作为代理可执行证据。该项的最终签收由用户完成。
2. **Pixel API 计费比例暂停**：项目原配置的 Pixel API group room-488 返回 403 `group_ratio_changed`（0.05→0.1，需控制台确认）；确认会接受翻倍计费，未由代理擅自确认。最终真实闭环改用 DeepSeek 官方 `deepseek-flash` 与 usfast `gpt-image-2-4k`，均记录真实调用指纹；Pixel API 仍保持未使用。
3. **独立操作者环境的 Docker Compose v2 插件缺失**：其本机维护、迁移、首管、备份、恢复、故障安全均为 PASS；HTTPS 一项已改用 `docker run` 单容器方式完成独立客户端 12 项 PASS；生产 LAN、DNS、真实 CA 与主机防火墙仍由用户自测。
4. **真实闭环前置夹具**：为隔离 S01–S05 的自然语言不确定性，run24 使用测试夹具直接写入已审 `final_brief`，随后从 S06 确认起全部走真实状态机、真实账号、真实额度、真实模型与真实图片；夹具不进入生产代码，也不改变确认/门禁逻辑。该点已在 §4 明示。
5. **真实数据只做副本演练**：`backend/data` 与 `main/backend/data` 未被修改；#84 仅对字节副本执行 0001→0010、认领、备份/恢复和启动保留，实际根摘要未变。生产数据迁移仍需维护者在发布切换窗口按 runbook 执行。
6. **供应商内容审核不可控**：真实模型可能对某些商品图给出拒绝/修正意见；系统按真实状态保留 review 失败并允许修订，不伪造发布。run24 的通过只证明该次真实请求闭环，不保证任意提示词均一次通过。
7. **发布包 185 文件 vs 179 tracked**：差异是 3 个构建产物与 3 个包元数据/摘要文件，均为允许清单内文件；`--check --require-dist` 的 24 个必需文件全部存在，issues 为空。

## 10. 发布结论

**结论：PASS（代理可执行范围）；物理设备由用户按 m22428 自测，代理不代签。**

- 通过：45 条 SRS 正文需求全部映射；37 个真实路由出口逐项匿名/认证/归属检查；10 组权限矩阵逐格检查；V3-AC-02..37、39、40 共 38 项有最终证据；AC-01、AC-38 的代理证据通过，物理设备签收由用户按 m22428 自测。
- 用户自测项：AC-01 的“另一台实际局域网设备”、AC-38 的“真实手机/桌面浏览器”中的真实手机部分；代理不以容器/仿真代签。独立操作者已在本机维护与容器 HTTPS 入口上通过，生产 LAN/防火墙仍由用户自测。
- 因此本文件**不声称代理已代签物理设备**；但按用户 m22428 的明确分工，代理可执行范围已完成并可发布。当前代码、迁移、备份恢复、真实模型闭环、自动化回归、容器 HTTPS 与独立操作者本机维护均已通过，未发现代码级阻断失败。
