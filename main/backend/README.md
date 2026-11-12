# 后端工作区

后端源码在 app/（唯一源码），本目录同时放依赖锁、迁移、配置、数据库和图片。

## 安装依赖

    cd backend
    uv sync

## 迁移 + 单 worker 跑在 loopback

    uv run alembic upgrade head
    uv run python -m app.production --host 127.0.0.1 --port 8000

旧入口 `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1` 仍作为兼容
入口保留。生产入口的检查命令是 `uv run python -m app.production --check --json`。

首次使用空库时，在交互终端执行 `uv run python -m app.auth.cli init-admin` 创建唯一首管理员；
密码只从终端读取。实例已有管理员时会拒绝重入。

后端默认托管 ../dist（前端构建产物）。需要页面时在上一级目录执行 `npm ci && npm run build`。

## 配置

运行配置来自本目录的 .env（首次部署从 .env.example 复制后填 provider 值）。关键项：

- LLM_ADAPTER / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（需求整理）
- LLM_VISION_MODEL / LLM_VISION_BASE_URL / LLM_VISION_API_KEY（可选，参考图识别）
- VISUAL_ADAPTER / VISUAL_BASE_URL / VISUAL_API_KEY / VISUAL_MODEL / VISUAL_IMAGE_SIZE
- APP_PUBLIC_URL（账号设置／重置链接使用的浏览器源；未设置时相关链接操作会拒绝执行）

两个角色都留空时后端仍能启动，只在配置诊断里报 not ready。

## 数据

data/ 保存 SQLite 库（app.db，可能带 -wal/-shm）以及 generated/、references/、tmp/。
数据库里只存相对路径，所以整个 data/ 目录就是全部运行数据；Docker 里同样挂载到这里。

设置 `APP_BACKUP_DIR`（必须位于 data/ 与 dist/ 之外）后，运行期会按
`APP_BACKUP_INTERVAL_SECONDS`（默认 86400 秒）做周期快照，保留最近
`APP_BACKUP_KEEP=7` 份已验证快照，并在启动时补做到期任务。Docker Compose 默认把
宿主 `./backups` 挂到 `/app/backups` 并启用；本地直接运行时可在 .env 中设置
`APP_BACKUP_DIR=./backups`。状态可用本地 CLI 查看：

    uv run python -m app.maintenance.cli backup-status --output ./backups --json

备份目录含完整业务数据，按敏感数据保护；快照不包含 .env、模型密钥或 TLS 私钥。

运行期和启动时还按类型清理到期内容：终态未发布候选图片 24 小时、普通诊断 30 天、安全审计 90 天；已发布/未认领 legacy/审批/恢复证据保留。候选文件删除后行保留并标记 retention_expired，未知缺失仍阻断备份。

生产恢复使用本机 CLI，必须先停服务并验证备份：`restore --source ./data --backup ./backups/backup-<utc>-<hex> --output ./backups --confirm-restore RESTORE --service-stopped`。恢复会撤销全部会话和未使用链接、保留审批/交付撤销状态，并写入缺口闸门；核对账本后用 `clear-restore-gap --confirm-clear CLEAR_RESTORE_GAP` 关闭。详见 docs/operations/release-restore-recovery.md。

## 代码检查

    uv run ruff check .

测试代码不随部署包发布，需要时从开发仓库 backend/tests 复制回来。

## 目录

- pyproject.toml / uv.lock：依赖与锁
- .env.example：空配置样例（无密钥）
- alembic.ini / migrations：数据库迁移（head = 0011）
- app/：FastAPI 应用（config.py 同时兼容本目录为项目根和旧仓库布局）
- data/：运行数据，启动时自动创建缺失目录
