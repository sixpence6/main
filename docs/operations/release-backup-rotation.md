# Release T29 周期备份与安全轮换运行手册

本文说明如何在运行中的完整 App 上启用本机周期备份、查看状态、手工校验和轮换最近七份已验证快照。它只覆盖 #81 的备份调度与轮换；生产恢复切换、保留期/密钥收口和最终发布属于 #82、#83、#85。

## 1. 目标与边界

- 备份目录必须是显式配置的本机受限目录，位于 APP_DATA_DIR 与 WEB_DIST_DIR 之外。
- 运行期至少每 24 小时备份一次；停机后启动会立即补做到期任务。
- 每次先写隔离 staging，独立校验通过后才发布为 backup-* 并更新 latest.json。
- 最近七份已验证快照保留；旧快照只有重新校验通过才允许删除。
- 故障、超时、文件发布或到期删除竞争不会删除上一份可用备份。
- 应用不提供网页整库导出；备份只通过本机 CLI 和文件系统访问。
- 快照不包含 .env、模型密钥、TLS 私钥、缓存、构建产物或未登记文件。

## 2. 目录与权限

推荐布局（发布包根）：

    backend/data/          业务数据（SQLite、references、generated、published）
    backups/               周期备份（不得位于 data 或 dist 内）
      backup-<utc>-<hex>/  单份快照
      latest.json          最近一次成功快照指针
      status.json          最近尝试/成功/失败与下次到期

- 备份目录只允许服务账号和维护者读取；不要放在网页静态目录、共享网盘或可被普通用户写入的位置。
- 备份目录含完整账号、所有者、业务和审批数据，按敏感数据保护，并另做受控介质备份。
- 不要把证书私钥、.env 或模型密钥复制进备份目录；这些文件不在快照允许清单内。

## 3. 配置

在发布包 backend/.env 中设置：

~~~dotenv
APP_BACKUP_DIR=./backups
APP_BACKUP_INTERVAL_SECONDS=86400
APP_BACKUP_KEEP=7
APP_BACKUP_TIMEOUT_SECONDS=60
~~~

规则：

- APP_BACKUP_DIR 未设置时后台调度关闭，手工 CLI 仍可用；Docker Compose 默认设置 /app/backups。
- APP_BACKUP_INTERVAL_SECONDS 必须大于 0 且不超过 86400 秒；缩短后不需要等旧的持久化 next_due_at。
- APP_BACKUP_KEEP 在 1..100 之间，默认 7。
- APP_BACKUP_TIMEOUT_SECONDS 是等待在途业务写和文件发布/到期删除结束的上限；超时会记录失败并恢复写入门。
- 备份目录等于、位于或包含 APP_DATA_DIR 或 WEB_DIST_DIR 时配置校验失败，服务拒绝启动。

Docker Compose 默认：

~~~yaml
environment:
  APP_BACKUP_DIR: /app/backups
volumes:
  - ./backend/data:/app/backend/data
  - ./backups:/app/backups
~~~

## 4. 启动与状态

启动完整 App（Docker 或 python -m app.production）后，调度器作为 lifespan 任务运行。首次没有 status.json 会立即补做；之后按 status.next_due_at 到期执行。

查看状态：

~~~bash
docker compose exec backend python -m app.maintenance.cli backup-status --output /app/backups --json
~~~

本地生产入口：

~~~bash
uv run python -m app.maintenance.cli backup-status --output ./backups --json
~~~

status.json 的关键字段：

| 字段 | 含义 |
| --- | --- |
| schema | maintenance-backup-status/v1 |
| last_attempt_at | 最近一次调度尝试时间 |
| last_success_at | 最近一次成功快照时间；失败时保留 |
| last_failure_at / last_error | 最近失败时间与 code/message；成功后清空 error |
| backup_id / backup_dir | 最近成功快照及目录名 |
| next_due_at | 下一次到期时间；失败后最多 300 秒重试 |
| keep / interval_seconds | 当前保留数与周期 |
| pruned / rotation_skipped | 最近轮换删除与跳过的候选 |

## 5. 快照内容与校验

每份 backup-<utc>-<hex> 包含：

- data/app.db：SQLite 在线备份（包含已提交的 WAL 内容），记录 alembic_version、integrity_check、assets 摘要和全部非 sqlite_ 表行数。
- data/references/、data/generated/、data/published/：仅允许数据库 assets 登记且路径安全的资源文件，逐个记录相对路径、大小与 SHA256。
- manifest.json：备份 id、创建时间、源、数据库摘要、文件清单、missing 列表、边界标记。

独立校验：

~~~bash
docker compose run --rm backend python -m app.maintenance.cli verify /app/backups/backup-<utc>-<hex>
~~~

期望：exit 0，输出 verified true、数据库 integrity ok、alembic version 0010、文件摘要/大小一致、数据库 assets 与文件清单交叉一致、没有未预期文件。校验失败只报告，不删除任何旧备份。

## 6. 轮换与失败处置

1. 新快照先在 .staging-* 中生成并 verify；verify 失败会清理 staging，latest.json 和旧 backup-* 不变。
2. verify 成功后 os.replace 为 backup-*，再写 latest.json（最后一步）。
3. 轮换扫描 backup-*：对每份重新 verify + load_manifest；非法目录跳过并记录 rotation_skipped，不删除。
4. 新快照受保护；其余按创建时间从新到旧保留 keep 份，超出的已验证旧快照才删除。
5. 维护窗口会等待在途 business_write、file_publication、expiry_deletion；超时返回 MAINTENANCE_TIMEOUT，恢复写入门并写失败状态，不发布半成品。

故障时：

- 查看 status.last_error.code/message 和 backend 日志中的 scheduled backup cycle failed。
- 不要手工删除 latest.json 或最后一个可用 backup-*；先确认磁盘空间、目录权限、SQLite 完整性和源目录位置。
- 修复后用 backup-status 确认状态；到期或重启会再次补做。
- 如需立即补做，在服务停止后使用手工 CLI（见第 7 节）。

## 7. 手工备份与恢复演练

手工备份要求服务停止，避免运行中复制不一致：

~~~bash
docker compose stop backend
docker compose run --rm backend python -m app.maintenance.cli backup   --source /app/backend/data --output /app/backups --service-stopped
docker compose start backend
~~~

本地入口：

~~~bash
python -m app.maintenance.cli backup --source ./backend/data --output ./backups --service-stopped
~~~

恢复只做隔离演练，不切换生产：

~~~bash
python -m app.maintenance.cli restore-verify   --backup ./backups/backup-<utc>-<hex>   --output ./restore-rehearsal --confirm-restore REHEARSE
~~~

输出目录必须为空且与源分离；命令不会覆盖生产 data，也不会自动切换数据库。生产恢复流程由 #82 单独验收。

## 8. 与业务发布、到期删除的竞争

备份窗口通过 MaintenanceCoordinator 协调三种租约：

- business_write：请求/后台任务提交业务事务；
- file_publication：写入 references/generated/published 不可变文件；
- expiry_deletion：保留期清理删除过期候选或文件。

备份先关闭新租约，再等待在途操作结束；因此不会在数据库事务中间或文件写入中间截取快照。超时则失败退出，恢复开放状态并保留上一份成功快照。不要绕过应用直接写 data 目录，否则无法保证一致性。

## 9. 数据、安全与发布边界

- 没有 /api/backups 或 /api/maintenance 导出路由；认证用户也无法通过网页下载整库。
- 快照排除 .env、TLS 私钥、模型 key；但数据库本身包含账号、所有者、业务、审批和受理记录，必须按敏感数据保管。
- 备份目录不要提交 Git、不要打进发布 ZIP；发布包只包含空目录占位或部署时创建的目录。
- 真实数据根 backend/data 与 main/backend/data 未经维护者选择和验证备份前不得移动、覆盖或删除。
- 本手册不替代 #82 的生产恢复切换、#83 的保留期/密钥收口和 #85 的最终发布关口。

## 10. 常见问题

| 现象 | 处理 |
| --- | --- |
| 启动报 APP_BACKUP_DIR must be outside... | 把备份目录移到 data 与 dist 之外，不要用相对路径指向 data 内部 |
| backup-status 返回 ok false | 尚未产生 status.json，或路径不对；确认 APP_BACKUP_DIR 与 --output 一致 |
| status 显示 last_error DATABASE... | 源库损坏/不可读；先停机检查 SQLite，不要删除上一份可用快照 |
| 轮换没有删除旧目录 | 旧目录可能 verify 失败或不是 backup-*；看 rotation_skipped，不要手工强删 |
| 维护超时 MAINTENANCE_TIMEOUT | 有长事务/文件发布/清理未结束；查日志后重试，应用已恢复写入门 |
| 备份目录越来越大 | 确认 APP_BACKUP_KEEP、轮换日志和是否有无法校验的 backup-*；只清理确认可弃的目录 |
| 网页找不到备份下载 | 设计如此；备份只允许本机 CLI/文件系统访问 |

## 11. 关联文档

- docs/acceptance/release-backup-rotation.md：本票验收证据与边界。
- docs/operations/maintenance-backup.md：C0 停机备份/恢复演练。
- docs/operations/release-production-entry.md：生产入口。
- docs/operations/release-https-lan.md：受控 HTTPS 局域网入口。
- 后续 #82 生产恢复、#83 保留期/密钥收口、#85 最终发布。
