# Release T29 运行中周期一致备份与安全轮换验收记录

- Issue: #81
- 父 Issue: #52
- 分支: ticket/81
- 基线: integration/v3 56c3cc5
- 代码提交: d657750599f6ce4be3de9b2132a4f0f762c47252
- 验收日期: 2026-11-01
- 状态: 通过（Release T29 阶段条件；正式发布由 #85 Release-GATE 决定）

## 1. 结论与边界

本票把 C0 的停机备份/恢复演练机制扩展为运行期周期备份：在显式配置 APP_BACKUP_DIR 后，完整 App 启动即补做到期任务，之后按不超过 24 小时的周期生成 SQLite 在线快照、独立校验、发布 latest 指针并轮换最近七份已验证快照。备份覆盖完整数据库账本（账号、所有者、业务、审批、受理与用量）和允许目录中的资源文件清单；失败、超时或文件发布/到期删除竞争不会删除上一份可用备份。

1. 配置 APP_BACKUP_DIR 才启用后台调度；Docker Compose 默认把宿主机 ./backups 挂到 /app/backups 并设置 APP_BACKUP_DIR=/app/backups。APP_BACKUP_INTERVAL_SECONDS 默认且最大为 86400 秒（24 小时），APP_BACKUP_KEEP 默认 7，APP_BACKUP_TIMEOUT_SECONDS 默认 60。
2. 启动时读取 status.json 计算下次到期；无状态或已过期会立即补做；当前 interval 缩短后，以 min(持久化 next_due_at, last_success_at + 当前 interval) 为准，不会被旧的长周期拖延。
3. SQLite 使用在线 backup API，包含已提交的 WAL 内容；快照切到独立 staging，数据库记录 alembic_version、integrity_check、assets 摘要以及所有非 sqlite_ 表的精确行数。文件只允许 references/、generated/、published/ 下的登记资源，逐个校验大小与 SHA256，拒绝绝对路径、..、逃逸、符号链接/重解析点、秘密文件和未登记文件。
4. 新快照先在隔离目录 verify_backup：manifest 摘要、允许顶层项、数据库完整性/外键/schema 版本/表行数、每个文件摘要/大小、数据库 assets 交叉检查全部通过后，才原子发布 backup-* 并更新 latest.json；失败会清理 staging，不改变 latest 和旧快照。
5. 轮换只处理 backup-* 目录；对每个候选重新 verify_backup 并读取 manifest，非法候选跳过并记入 rotation_skipped，不删除无法验证的目录；新快照受保护，旧快照按 created_at 从新到旧保留，超过 keep 才删除。
6. 备份窗口使用既有的三通道 MaintenanceCoordinator（business_write、file_publication、expiry_deletion）：先关闭新租约，再等待在途业务写、同步文件发布和到期删除结束；超时会恢复开放状态并写入失败状态，不留下半发布快照。
7. 失败状态保留 last_success_at、backup_id、backup_dir 和上一份 latest；last_error 记录 code/message，next_due_at 最多 300 秒后重试；后台循环记录异常日志后等待 300 秒，不删除旧备份。
8. 不提供网页整库导出：认证后的 /api/backups、/api/maintenance/status、/api/maintenance/backups 均返回 404 ROUTE_NOT_FOUND；仅供本机维护 CLI 使用 backup-status、backup、verify、restore-verify，手工 backup 仍要求 --service-stopped。
9. 快照不复制 .env、模型密钥、TLS 私钥、缓存、历史数据库或网页构建；备份目录必须显式位于 APP_DATA_DIR 与 WEB_DIST_DIR 之外，含完整业务数据，按敏感数据限制本机权限。
10. 用完整 App、真实临时 SQLite、fake OpenAI-compatible provider 完成了一条真实 HTTP 受理到发布/下载链路，并验证初始备份、启动补做、轮换、状态与无导出路由。没有触碰 backend/data 或 main/backend/data，也没有调用真实模型供应商。本票不替代 #82 的生产恢复切换、#83 的保留期/密钥收口或 #85 的最终发布关口；#51 保持打开。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t81 |
| 分支 | ticket/81 |
| 基线 | integration/v3 56c3cc5 |
| 操作系统 | Windows；Python 3.12.13；Node v26.12.1；npm 11.20.0 |
| 前端 | Vite 8.3.5；Vitest 5.0.5；Vue 3.5 |
| 后端测试解释器 | D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe |
| 数据库 | 临时 SQLite；alembic 0010；26 张表，integrity ok |
| 模型 | 本地 fake OpenAI-compatible provider；无真实供应商调用 |
| 数据根 | 仅 .scratch/t81-backup 临时目录；未触碰 backend/data、main/backend/data |

## 3. 实现摘要

### 3.1 配置与调度

- main/backend/app/config.py 增加 backup_dir（APP_BACKUP_DIR/BACKUP_DIR）、backup_interval_seconds（默认 86400，gt=0，le=86400）、backup_keep（默认 7，1..100）、backup_timeout_seconds（默认 60，gt=0）；空字符串视为未配置。
- 同一模型校验要求 resolved backup dir 不在 resolved APP_DATA_DIR 或 WEB_DIST_DIR 内、也不包含它们；否则拒绝启动配置。
- 新增 main/backend/app/maintenance/automation.py：status 读写、next_backup_due、run_backup_cycle、backup_loop。backup_loop 在 lifespan 中作为独立任务启动；无 APP_BACKUP_DIR 时调度器关闭，手工 CLI 仍可用。
- status.json 使用 maintenance-backup-status/v1，原子写入 .status.json.<pid>.tmp 后 os.replace；记录 source/output、last_attempt_at、last_success_at、last_failure_at、last_error、backup_id、backup_dir、next_due_at、keep、interval_seconds、pruned、rotation_skipped。
- main.py 在 retention 任务之后启动 backup_task，并在退出时统一取消 retention_task 与 backup_task。

### 3.2 快照、账本与文件清单

- backup.py 的 DatabaseSnapshot 增加 table_counts；_snapshot_database 在 SQLite 在线快照上统计全部非 sqlite_ 表，避免只校验 assets 而漏掉账号、审批、受理等账本。
- manifest.py 在存在 table_counts 时逐个比较精确行数，缺失或变化抛 DATABASE_TABLE_COUNTS_MISMATCH；旧 manifest 没有 table_counts 时保持兼容。
- 原有 manifest 继续记录数据库 sha256、alembic_version、asset_count、每个资源文件的 relative_path/sha256/size，以及 included_dirs、excluded、web_whole_db_export=false、periodic_scheduler、production_restore_switch=false 等边界。

### 3.3 轮换、失败与协调

- 新增 prune_verified_backups：只扫描 backup-*，对每个候选 verify_backup + load_manifest；非法候选跳过并记录，不删除；保护新快照，按 created_at/name 倒序保留 keep 份，其余才 rmtree。
- run_backup_cycle 先 select_source（只接受显式配置的源根），再 _validate_output，再打开 coordinator 维护窗口；窗口内 inspect_candidate、_create_backup_sync(periodic_scheduler=True)、prune_verified_backups。
- MaintenanceTimeout 转换为 BackupError MAINTENANCE_TIMEOUT 并写失败状态；任何 BackupError 在输出目录可用时写失败状态，保留上一份 latest 和备份；staging 由 _create_backup_sync 清理。
- backup_loop 捕获 CanceledError 直接退出；其他异常记录日志并等待 RETRY_DELAY_SECONDS=300；成功时写 status 并打印 backup id。

### 3.4 本机入口与部署

- main/backend/app/maintenance/cli.py 增加 backup-status --output PATH --json；原有 backup/verify/restore-verify 不变。
- main/docker-compose.yml 后端设置 APP_BACKUP_DIR=/app/backups，并挂载 ./backend/data 与 ./backups；.gitignore 忽略 backend/backups/ 与 main/backend/backups/。
- main/backend/.env.example、main/README.md、main/backend/README.md 记录周期、启动补做、七份保留、状态文件、手工 CLI、无网页导出和秘密排除。
- 没有新增 HTTP 路由或迁移；head 仍为 0010。

### 3.5 测试

- 新增 backend/tests/test_t81_backup_schedule.py 10 个测试：备份目录重叠拒绝、24 小时上限、next_due 边界、状态/manifest/table_counts/秘密排除、CLI status、10 次循环轮换保留 7 且跳过非法候选、损坏数据库保留上一份、文件发布租约等待、到期删除租约等待、表行数篡改检测、lifespan 启动补做且无导出路由。
- 复用 backend/tests/test_t54_maintenance_backup.py 的 19 个停机备份/恢复测试；两项合计 29 个测试。

## 4. 命令与结果

### 4.1 针对性后端测试

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest tests/test_t54_maintenance_backup.py tests/test_t81_backup_schedule.py -q
~~~

结果：29 passed in 5.82s，exit 0。日志 .scratch/t81-evidence/backend-targeted.log。

### 4.2 Ruff

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m ruff check main/backend/app/config.py main/backend/app/maintenance/automation.py main/backend/app/maintenance/backup.py main/backend/app/maintenance/manifest.py main/backend/app/maintenance/cli.py main/backend/app/main.py backend/tests/test_t81_backup_schedule.py
~~~

结果：All checks passed!，exit 0。日志 .scratch/t81-evidence/ruff.log。

### 4.3 前端回归与构建

~~~powershell
npm test -- --run --reporter=dot
npm run typecheck
npm run build
~~~

结果：38 个测试文件、173 个测试通过（22.87s，exit 0）；vue-tsc 无输出（exit 0）；Vite 8.3.5 生产构建 1,664 modules、896ms，dist/index.html 0.47 kB、CSS 430.37 kB、JS 1,159.11 kB，仅 chunk 体积提示（exit 0）。日志 .scratch/t81-evidence/frontend-full.log、typecheck.log、build.log。

### 4.4 后端全量回归

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest tests/test_openai_chat_adapter.py ... -q
~~~

非 v2 采用 43 个测试文件分三组运行：162 passed in 152.50s、133 passed with 2 warnings in 121.01s、82 passed in 90.09s，合计 377 passed；v2 为 19 个文件、235 passed in 306.71s。总计 612 passed，无 failed/skipped/unknown；仅有既有的 DeprecationWarning（backend/tests/support/auth.py:42）。日志 .scratch/t81-evidence/backend-nonv2-1.log、backend-nonv2-2.log、backend-nonv2-3.log、backend-v2.log。

### 4.5 真实临时环境 smoke

~~~powershell
D:/nodejs/node.exe D:/Desktop/新建文件夹/.scratch/t81-backup/run_backup.mjs
~~~

结果：exit 0，ok true；appPort 3747、fakePort 3746；完整 App 使用临时 SQLite（alembic 0010）和 fake provider，完成管理员登录、创建用户/设置密码、用户登录、项目创建、S06 确认、S07–S10 后台链路、批准交付、发布交付和下载。

- project 648ee768-c0e6-4b7d-80a6-6ed36ee34d1f
- account bd0b2473-9257-4cfe-b0d0-af1d5e4ec27a
- request a85750b9-9b56-4e58-b24c-7882ee5b496e
- stage S10 / status completed
- delivery asset 6d98fec0-f3cd-4c9d-b6c2-9c319a3bc5a1，filename delivery-6d98fec0-r1.png
- download 29373 bytes，content-type image/png
- usage daily_limit 20 / used 1 / remaining 19；活动槽位 personal 0／instance 0／project 0，上限 1／2／1
- fake provider chat 6 / images 1；角色顺序 generation_engineer、output_reviewer、quality_compliance、output_reviewer、delivery_manager、output_reviewer

初始备份 1 份；Phase B 将 status.next_due_at 改为 2020-01-01 并以 interval=2 重启，启动立即补做并轮换。最终 backup_count=3，pruned 一份（backup-20261101T062613Z-776a258e），latest backup-20261101T062620Z-cdd3e9cb，last_success 2026-11-01T06:26:20.036Z；三份均 verify 成功（20261101T062615Z-1cb7f21c、20261101T062618Z-a07e88ea、20261101T062620Z-cdd3e9cb），file_count 1、total_bytes 29373，交付图片 published/26a9960f-929a-4401-8028-a05f2dc99844.png，download SHA256 c82db60aa03cdc6fdcea028953e88bc5b05feade528a8a4f6d799885f3a7c835。每份账本包含 accounts 2、projects 1、generations 1、generation_intents 1、generation_usage 1、deliveries 1、approvals 5、assets 1、generation_slots 3，alembic 0010；missing 为空，无秘密文件。结果见 .scratch/t81-backup/result.json、backup-verify.json、smoke.json、download.png。

### 4.6 状态、路由与手工校验

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t81-backup/check_routes.py
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t81-backup/verify_backup.py
~~~

结果：authenticated GET /api/backups、/api/maintenance/status、/api/maintenance/backups 均为 404 ROUTE_NOT_FOUND；/api/health 为 200；app.production --check exit 0；verify true。日志 .scratch/t81-evidence/backup-smoke.log；结果 .scratch/t81-backup/routes.json、runtime-check.json。

## 5. AC 证据映射

| AC | 阶段条件 | 证据 |
| --- | --- | --- |
| V3-AC-21 | 运行期周期备份、启动补做、账本与文件清单一致 | 3.1–3.3、4.1、4.5、4.6；status.json、table_counts、manifest、29 个针对性测试 |
| V3-AC-35 | 新快照独立校验、失败保留可用副本、轮换不删除最后可用 | 3.2–3.4、4.1、4.5；隔离 verify、latest 保留、损坏数据库/超时/非法候选测试 |
| V3-AC-36 | 本机受限目录、无网页整库导出、秘密排除、时间/故障/竞争可重复 | 3.1、3.3–3.5、4.2、4.6；404 路由、秘密文件断言、file_publication/expiry_deletion 租约测试 |

本票只证明上述 Release 阶段条件；#82 的生产恢复切换、#83 的保留期/密钥收口和 #85 的最终关口仍需独立验收。#51 不因本票关闭。

## 6. 证据清单

- .scratch/t81-evidence/backend-targeted.log
- .scratch/t81-evidence/ruff.log
- .scratch/t81-evidence/frontend-full.log
- .scratch/t81-evidence/typecheck.log
- .scratch/t81-evidence/build.log
- .scratch/t81-evidence/backend-nonv2-1.log、backend-nonv2-2.log、backend-nonv2-3.log、backend-v2.log
- .scratch/t81-evidence/backup-smoke.log
- .scratch/t81-backup/result.json、backup-verify.json、smoke.json、routes.json、runtime-check.json、download.png、server-a.log、server-b.log、fake.log

## 7. 剩余风险

1. 真实环境只使用临时 SQLite 与本地 fake provider；没有等待真实 24 小时、真实供应商、真实磁盘故障或跨机器备份介质。
2. Phase B 为可重复验证把 interval 缩短为 2 秒，生产默认仍为 86400 秒；缩短配置的上限由 24 小时校验保证。
3. 轮换测试使用 10 次连续快照证明最近 7 份，未覆盖超大备份目录的长时间删除耗时。
4. Windows 文件系统语义与 POSIX 权限不同；备份目录的操作系统 ACL 由部署维护者按运行手册设置，本票没有自动修改 ACL。
5. 没有重启并切换生产恢复数据；restore_verify 仍只在隔离目录演练，生产恢复开关留给 #82。
6. 发布包 ZIP 未在本票重新构建；#85 最终关口应重新生成并校验。
7. #51 仍开放；本票不构成正式发布。
