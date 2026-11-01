# Release T26 自包含生产入口运行手册

本文说明如何使用 `main/` 自包含包启动、检查、停止和排查生产入口。它只覆盖 #78 的入口与兼容启动；HTTPS、局域网入口、备份轮换、恢复切换与保留期清理属于后续 Release 票。

## 1. 入口与目录边界

- Python 入口：`app.main:app`，由 `python -m app.production` 启动。
- 唯一源码：`main/backend/app`；`main/backend/migrations` 为迁移来源。
- 前端产物：`main/dist`，默认由后端静态托管；容器中由 nginx 托管。
- 数据根：由 `APP_DATA_DIR`（或兼容的 `DATA_DIR`）显式指定；默认是 `main/backend/data`。
- 旧开发工作区：仓库根 `backend/` 只保留测试、开发依赖与兼容包装器；不要把它当成第二套生产源码。

生产入口会打印但不记录密钥类配置；`--check --json` 只输出路径、解释器和迁移头。

## 2. 前置条件

- Python 3.12；依赖由 `main/backend/uv.lock` 锁定。
- 数据库已迁移到 head 0010；本地启动不会自动执行迁移。
- 需要账号登录。空实例必须先在本机交互终端创建唯一首管理员。
- `APP_PUBLIC_URL` 必须是浏览器实际访问的绝对 http/https 源，不能带路径、查询或片段；账号设置／重置链接依赖它。
- `ALLOWED_ORIGINS` 必须列出浏览器实际访问源（JSON 数组，不允许通配）；Compose 的 8090 前端源与本地 8000 源不同，不能只依赖端口推断。
- 生产环境必须使用 HTTPS 并保持 `AUTH_COOKIE_SECURE=true`。只有本机回环 HTTP 开发可显式设置 `ALLOW_LOOPBACK_HTTP=true` 与 `AUTH_COOKIE_SECURE=false`。

## 3. 本地启动

在 `main/backend` 执行：

```powershell
cd D:/Desktop/新建文件夹/main/backend
uv sync --frozen --no-dev
uv run alembic upgrade head
uv run python -m app.production --check --json
uv run python -m app.production --host 127.0.0.1 --port 8000
```

首次使用空库时，另开一个交互终端，在同一个工作目录创建管理员：

```powershell
cd D:/Desktop/新建文件夹/main/backend
uv run python -m app.auth.cli init-admin
```

该命令只在 TTY 中读取两次密码，没有 `--password` 参数；已有管理员时拒绝重入。不要通过网页、环境变量或命令行明文设置首管理员密码。

本机 HTTP 登录需要临时在 `main/backend/.env` 中设置：

```dotenv
APP_PUBLIC_URL=http://127.0.0.1:8000
ALLOWED_ORIGINS=["http://127.0.0.1:8000"]
ALLOW_LOOPBACK_HTTP=true
AUTH_COOKIE_SECURE=false
```

这三个值只适用于本机回环开发；局域网或正式环境必须改成受信 HTTPS 源并保持安全 Cookie。

## 4. Docker Compose 启动

在 `main` 目录准备 `backend/.env`，至少填写 provider 配置与 `APP_PUBLIC_URL`；然后：

```powershell
cd D:/Desktop/新建文件夹/main
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f backend
docker compose exec backend python -m app.production --check --json
docker compose exec backend python -m app.auth.cli init-admin
```

后端容器启动时先执行 `alembic upgrade head`，成功后才启动生产入口；前端容器在后端健康检查通过后启动 nginx。默认只绑定 `127.0.0.1` 的 8090 与 8000 端口。

若在本机用 `http://127.0.0.1:8090` 登录，同样在 `backend/.env` 设置回环 HTTP 例外，并把 ALLOWED_ORIGINS 设为 `["http://127.0.0.1:8090"]`；正式环境不要设置这些回环例外。

## 5. 检查启动来源

生产入口检查示例：

```powershell
cd D:/Desktop/新建文件夹/main/backend
uv run python -m app.production --check --json
```

重点核对：

| 字段 | 期望 |
| --- | --- |
| `entrypoint` | `app.main:app` |
| `import_root` | `<包根>/main/backend/app` |
| `backend_dir` | `<包根>/main/backend` |
| `workspace_dir` | 旧开发工作区 `backend`（不代表生产源码） |
| `migration_head` | `0010` |
| `data_dir` / `database_path` | 当前部署显式指定的目录与 `app.db` |
| `web_dist_dir` | 前端构建产物目录 |

容器内执行同一命令时，路径应位于容器的 `/app/backend` 与 `/app/backend/data`（或 compose 指定挂载）。

## 6. 兼容入口

旧入口仍可用，但只作为扩展期兼容与回退：

```powershell
cd D:/Desktop/新建文件夹/backend
python run.py --check --json
python run.py --host 127.0.0.1 --port 8000
# 或
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

兼容入口仍要求数据库先迁移到 0010，并且工作目录与 `PYTHONPATH` 能解析 `main/backend/app`。不要用它启动第二个 worker 或绕过生产入口的数据根配置。

## 7. 停止与重启

- 本地：在运行终端按 Ctrl+C；确认进程退出后，数据库文件与图片目录保留。
- Compose：`docker compose stop backend` 停止后端；`docker compose down` 移除容器但保留绑定数据目录。不要在未停机时直接复制 SQLite 文件。
- 重启前先执行 `--check --json`，确认数据根、数据库路径和迁移头没有变化；启动顺序始终是迁移、检查、服务。
- 本票没有自动重置、自动迁移回滚、自动备份轮换或自动恢复；这些操作必须按后续运维票显式执行。

## 8. 故障排查

| 现象 | 含义／处理 |
| --- | --- |
| `StartupError: 数据库未完成迁移，缺少 alembic_version` | 数据库未迁移或指向了错误数据根；先执行 `alembic upgrade head`，再核对 `--check --json` 的 `database_path` |
| `ModuleNotFoundError: No module named app` | 工作目录或 `PYTHONPATH` 不对；本地应在 `main/backend`，兼容入口应使用 `backend/run.py` |
| `address already in use` | 端口被占用；换端口或在 `--check` 后确认旧进程已退出 |
| 登录后立即回到登录页／Cookie 未保存 | 通过 HTTP 访问但 `AUTH_COOKIE_SECURE` 仍为 true；本机回环可显式开启 HTTP 例外，正式环境应改用 HTTPS |
| `/api/config/status` 显示模型能力未就绪 | provider 配置为空或不完整；检查 `LLM_*`、`LLM_VISION_MODEL`、`VISUAL_*`，这不会伪造生成成功 |
| 容器内无法访问宿主测试 provider | 宿主服务不能写成容器内的 `127.0.0.1`；开发时使用 `host.docker.internal`，生产应使用可达的受信服务地址 |
| 容器启动即退出 | 先看 `docker compose logs backend`；迁移失败会阻止生产入口启动，这是有意的 fail-closed 行为 |

## 9. 数据、安全与回退边界

- 不要把 `backend/data`、`main/backend/data`、`.env`、`.venv`、模型密钥或 TLS 私钥复制到镜像构建上下文；`.dockerignore` 已排除这些路径。
- `--check` 输出可以交给维护者复现路径，但不要把含密钥的环境文件贴到工单、日志或聊天。
- 停止服务不等于备份完成；需要可恢复副本时按 `docs/operations/maintenance-backup.md` 和后续备份票执行。
- 本票不切换真实数据源，也不删除任何真实数据。任何真实数据迁移、认领或恢复演练必须先由维护者明确来源并完成已验证备份。

## 10. 相关文档

- 验收记录：`docs/acceptance/release-production-entry.md`
- 首管理员：`docs/operations/r1-local-admin-init.md`
- 备份与维护：`docs/operations/maintenance-backup.md`
- Docker 与包说明：`main/README.md`、`main/backend/README.md`

