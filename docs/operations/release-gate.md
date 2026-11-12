# V3 Release-GATE 操作与复核手册

本手册面向发布维护者，给出从干净发布物到受控局域网使用、真实模型闭环、备份恢复和故障安全关闭的完整复核步骤。与 `docs/acceptance/release-gate.md` 配套；任何一项未通过都不得对外宣称发布完成。

## 1. 环境与边界

- 主机：Windows 10 10.0.19045，Python 3.12.13，SQLite 3.50.4，Docker 29.8.2（Compose v5.5.1 可用时）。
- 发布包：`ecommerce-image-workspace-0.1.0.zip`，含 `RELEASE-MANIFEST.json` 与 `SHA256SUMS`。
- 数据根：默认 `backend/data`。**不得**把验收数据写入仓库内 `D:/Desktop/新建文件夹/backend/data` 或 `main/backend/data`；这些是真实/受保护数据根。测试必须使用 `APP_DATA_DIR` 指向的临时空目录。
- 入口：`backend/app/production.py`，ASGI 应用 `app.main:app`，单 worker，`proxy_headers=False`，`server_header=False`。
- 证书与私钥只放在受控目录，不进入发布包、不打印、不写入日志。

## 2. 解包与摘要核验

~~~powershell
$pkg = "D:/Desktop/新建文件夹/.worktrees/t85/release-artifacts/ecommerce-image-workspace-0.1.0"
Get-Content "$pkg/SHA256SUMS" -Encoding UTF8
python tools/release_package.py --check --require-dist --json
python tools/secret_scan.py --root $pkg --json
~~~

期望：SHA256SUMS 行数与 manifest 文件数一致且逐行匹配；`check` 返回 `ok: true`、`issues: []`；`secret_scan` 返回 `ok: true`、`hits: []`。

## 3. 干净环境启动

~~~powershell
$env:APP_DATA_DIR = "D:/scratch/release-gate/data"
$env:APP_BACKUP_DIR = "D:/scratch/release-gate/backups"
$env:APP_BACKUP_INTERVAL_SECONDS = "86400"
$env:APP_BACKUP_KEEP = "7"
$env:APP_PUBLIC_URL = "https://10.13.51.8:8443"
$env:ALLOWED_HOSTS = '["10.13.51.8","localhost","127.0.0.1"]'
$env:ALLOWED_ORIGINS = '["https://10.13.51.8:8443"]'
$env:ALLOW_LOOPBACK_HTTP = "false"
$env:AUTH_COOKIE_SECURE = "true"
python -m alembic upgrade head
python -m app.production --check --json
python -m app.production --host 127.0.0.1 --port 8000
~~~

期望：迁移到 `0010`；`--check` 退出 0 且 JSON 含 `ok: true`、`entrypoint: app.main:app`；进程只监听 `127.0.0.1:8000`，不监听 `0.0.0.0`。

首次管理员只能在交互终端创建，不接受管道/重定向/密码参数：

~~~powershell
python -m app.auth.cli init-admin
~~~

期望：提示邮箱与两次密码；密码 15–128 个 Unicode 码点；无默认密码；非 TTY 运行退出 2 并打印“本机维护命令只能在本机交互终端中运行”。

## 4. HTTPS 与防火墙

1. 用受信任 CA 签发服务器证书，SAN 至少含实际访问主机名/IP；私钥仅服务端可读。
2. 按 `docs/operations/release-https-lan.md` 启动 nginx TLS 容器；后端只发布到 `127.0.0.1:8000`，nginx 对外发布 443，80 仅做 308 跳转。
3. 在另一台局域网设备上安装/信任同一 CA，访问 `https://<host>:8443`，确认浏览器无证书警告，不点击“继续访问”。
4. 验证：

~~~powershell
curl.exe -I https://10.13.51.8:8443/
curl.exe -I http://10.13.51.8/
curl.exe -H "Origin: https://evil.example" -X POST https://10.13.51.8:8443/api/auth/login
curl.exe -H "Host: evil.example" http://127.0.0.1:8000/api/health
~~~

期望：HTTPS 200 且含 HSTS/CSP/nosniff/Referrer-Policy/X-Frame-Options/Permissions-Policy；HTTP 308 到 HTTPS；坏 Origin 403 `ORIGIN_INVALID`；直连后端坏 Host 403 `HOST_INVALID`；后端与数据/备份目录不可从客户端直连。

**当前验收边界：** 真实手机/第二台局域网设备由用户按 m22428 自测；容器 TLS 客户端只证明协议与头/Cookie，不替代物理设备。用户补做时应在验收表 AC-01 写明设备型号/系统/浏览器版本和证书信任方式。

## 5. 真实模型闭环

使用真实供应商配置（不要用替身冒充）：

- 文本/视觉：`LLM_ADAPTER=openai-chat`，`LLM_BASE_URL=https://api.deepseek.com/v1`，`LLM_MODEL=deepseek-flash`，`LLM_VISION_MODEL=deepseek-flash`。
- 图片：`VISUAL_ADAPTER=openai-images`，`VISUAL_BASE_URL=https://usfast.ailzd.com/v1`，`VISUAL_MODEL=gpt-image-2-4k`，`VISUAL_IMAGE_SIZE=3840x2160`。

步骤：管理员建普通账号 → 一次性链接设密 → 登录 → 新建项目并输入需求 → 确认参考/需求卡 → 一次确认生成 → 轮询工作流 → 审批候选 → 准备交付 → 批准/发布 → 下载当前交付 → 请求新版本。

期望：确认前无图片模型调用；确认后 S07 真实提示词编制/审查、S08 真实图片、S09 真实质量审查、S10 真实交付审查；最终 PNG 为 3840×2160，交付摘要与文件 SHA256 一致。记录每次真实调用的角色、时间、模型指纹；若供应商拒绝或内容审查不通过，保留真实失败状态，不自动重生、不伪造发布。

参考成功证据：`.scratch/t85-real-loop/run24/result.json`，六次真实调用，PNG SHA256 `c297fccf0f6e4783e544e4a55c1e6ed926d6573a62ab860af72fac1b9f1b7381`。

## 6. 备份与恢复

启动时若 `APP_BACKUP_DIR` 已配置，会执行 catch-up 备份；之后按 `APP_BACKUP_INTERVAL_SECONDS`（≤86400）执行，保留最近 7 个已验证快照。运行中备份使用 SQLite Online Backup，包含已提交 WAL，并生成文件 SHA256 清单。

~~~powershell
python -m app.maintenance.cli backup-status --json
python -m app.maintenance.cli verify --backup <backup-dir> --json
Stop-Service <service>   # 或停止 uvicorn/容器
python -m app.maintenance.cli restore --service-stopped --confirm-restore RESTORE --backup <backup-dir>
python -m app.maintenance.cli clear-restore-gap --confirm-clear CLEAR_RESTORE_GAP
~~~

期望：备份状态显示 verified 快照与 latest；verify 通过 manifest/SQLite 完整性；restore 前自动创建 safety backup，恢复后撤销全部会话和未使用链接，保留当前密码/禁用底线，不复活过期批准；恢复账本缺口期间新生成 409 `RESTORE_GAP_BLOCKED`，清除缺口后恢复受理。损坏快照必须拒绝，原数据保持可回退。网页无整库导出/秘密配置出口。

## 7. 故障安全关闭与重启

1. 在 S08 提交前后分别 kill 进程；重启后检查原任务归属、`remote_task_id`、额度计数和槽位。
2. 期望：不自动重新提交；未知状态不解释为“远端未受理”；不伪造取消；不允许匿名兼容访问；禁用账号阻止下一模型调用与发布。
3. 关闭顺序：停止接收新请求 → 等待运行中任务到安全点或记录未知 → 停止 uvicorn/容器 → 确认无监听端口 → 保留数据/备份目录。

## 8. 发布前检查清单

- [ ] 45 条 SRS 正文需求均有测试/证据行。
- [ ] 37 个真实路由出口：31 protected 匿名 401，未知/他人对象 404，管理员无业务读豁免。
- [ ] V3-AC-01..40 全部有版本/环境/命令/结果/证据；无阻断失败/未知。
- [ ] 后端 643 passed、前端 38 files/173 tests、typecheck/build 通过。
- [ ] 迁移 fresh 0001→0010 与链式升级保留数据。
- [ ] 真实模型六角色闭环、真实 4K 图片与交付摘要一致。
- [ ] 备份/恢复/故障安全由独立操作者按本手册复现（本票已 PASS）。
- [ ] 发布包摘要、必需文件、秘密扫描全部通过。
- [ ] 残余风险经维护者签字；物理设备项由用户自测，不得以容器/仿真冒充。

## 9. 当前残余风险

见 `docs/acceptance/release-gate.md` §9：物理手机/第二台局域网设备缺失、操作者环境 Docker Compose v2 缺失、Pixel API 计费比例暂停未确认、真实闭环使用 S06 前置夹具、真实数据仅副本演练、供应商内容审核不确定性。
