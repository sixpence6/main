# 电商生图智能体（自包含发布包）

本目录是发布包的根：前端源码与构建产物、后端源码、数据库迁移、依赖锁、Docker 配置和
空的数据目录都在这里，不依赖仓库根目录。目标机器只需要 Docker（含 Compose v2）即可
运行；如果不用 Docker，也可以在本机用 Python 直接启动后端并由后端托管 `dist/`。

发布包由开发仓库的 `release/package-manifest.json` 白名单生成，根目录的
`RELEASE-MANIFEST.json` 记录版本、源码提交和逐文件 SHA256，`SHA256SUMS` 可用于校验。

## 目录结构

    .dockerignore
    README.md / THIRD_PARTY_NOTICES.md
    RELEASE-MANIFEST.json / SHA256SUMS
    docker-compose.yml
    docker-compose.lan.yml     # 受控 HTTPS 局域网叠加层
    docker/
      Dockerfile                 # 前端：node 构建 -> nginx 托管
      backend.Dockerfile         # 后端：uv 装依赖 -> python slim 运行
      nginx.conf.template        # HTTP 开发入口：反代 /api 到 backend
      nginx-tls.conf.template    # HTTPS 入口：80 跳转 / 443 TLS
      security-headers.conf      # CSP 与统一安全头
      hsts.conf                  # HSTS 头
    package.json / package-lock.json
    tsconfig.json / vite.config.ts
    index.html / src/ / dist/    # 前端源码与随包构建产物
    backend/
      app/                       # 后端源码（唯一源码）
      pyproject.toml / uv.lock   # 后端依赖与锁
      alembic.ini / migrations/  # 数据库迁移（0001 -> 0011 head）
      .env.example               # 空配置模板（无密钥）
      data/                      # 空目录（.gitkeep 占位），运行时写入库与图片
      backups/                   # 可选：周期备份输出目录（不在 data 内）

发布包不包含测试、虚拟环境、node_modules、缓存、真实 `.env`、历史数据库或用户图片。

## 前置条件

- Docker Engine / Docker Desktop，支持 Compose v2（`docker compose version` 可运行）。
- 首次构建需要网络：拉取 node、nginx、uv/python 基础镜像和 npm/pypi 依赖。
- 目标机器不需要安装 Node、Python、uv 或 nginx。

## 首次部署（Docker）

先准备配置。发布包只有空模板，必须复制一份再填写：

    cp backend/.env.example backend/.env

至少设置：

- `APP_PUBLIC_URL`：浏览器实际访问的源，例如 `http://127.0.0.1:8090`。
- `ALLOWED_ORIGINS`：JSON 数组，必须包含浏览器源，例如 `["http://127.0.0.1:8090"]`；
  写请求会做精确 Origin 校验，不能用通配符。
- 回环 HTTP 访问必须显式设置 `ALLOW_LOOPBACK_HTTP=true` 与
  `AUTH_COOKIE_SECURE=false`；这两个例外只允许本机回环开发，不能用于局域网或生产。
- 模型配置可留空启动，但模型操作会因能力未就绪而保护性阻断。

启动：

    docker compose up -d --build
    docker compose ps
    docker compose logs -f backend

后端启动时先执行 `alembic upgrade head`（空 data 会自动建库并升到 0011），再通过
`python -m app.production` 拉起单 worker uvicorn；前端容器等后端健康检查通过后启动
nginx。

首次启动后，在服务器本机交互终端创建唯一首管理员（密码只从终端读取）：

    docker compose exec backend python -m app.auth.cli init-admin

实例已有管理员时该命令会拒绝重入。本应用不提供网页注册或网页首管理员初始化。

访问：

- 工作台 http://127.0.0.1:8090
- API 文档 http://127.0.0.1:8000/api/docs
- 健康检查 http://127.0.0.1:8000/api/health

## 受控 HTTPS 局域网入口

默认 Compose 只适合本机回环开发。局域网使用必须显式叠加
`docker-compose.lan.yml`，并准备受信任的服务器证书。该叠加层只把 nginx 的 HTTPS
端口绑定到 `PUBLIC_BIND_ADDRESS`，后端仍只绑定 `127.0.0.1:8000`，不会在局域网网卡上
监听；应用内的 `ALLOWED_HOSTS`、`ALLOWED_ORIGINS` 和 `APP_PUBLIC_URL` 都是精确值，
HTTP 开发例外不会因叠加层自动打开。

### 1. 准备证书

在受控机器上为实际使用的局域网地址签发证书（示例为 IP SAN；如用域名，替换
`subjectAltName`）。私钥只放在维护者控制的目录，权限设为仅服务账号可读：

    mkdir -p certs
    openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes \
      -keyout certs/server.key -out certs/server.crt \
      -subj "/CN=10.13.51.8" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:10.13.51.8"
    chmod 600 certs/server.key

把 `certs/server.crt`（或签发它的内部 CA）安装到每台允许访问的客户端信任存储。
浏览器必须通过 `https://10.13.51.8:8443` 这类证书已覆盖的地址访问；不要用 IP 与
证书 SAN 不一致的地址，也不要把私钥放进发布包或 Git。

### 2. 启动

    export PUBLIC_HOSTNAME=10.13.51.8
    export PUBLIC_ORIGIN=https://10.13.51.8:8443
    export PUBLIC_BIND_ADDRESS=10.13.51.8
    export PUBLIC_HTTPS_PORT=8443
    export TLS_CERT_DIR=./certs
    docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build

`PUBLIC_ORIGIN` 必须与浏览器地址栏完全一致（含端口），并与 `PUBLIC_HOSTNAME` 对应。
不要设置 `ALLOW_LOOPBACK_HTTP=true` 或 `AUTH_COOKIE_SECURE=false`；叠加层会强制
HTTPS、Secure Cookie 和受控代理来源。只开放防火墙上的 `PUBLIC_HTTPS_PORT`，
不要开放 8000 或数据目录。

### 3. 验证与运维

    curl --cacert certs/server.crt https://10.13.51.8:8443/api/health
    docker compose -f docker-compose.yml -f docker-compose.lan.yml ps
    docker compose -f docker-compose.yml -f docker-compose.lan.yml logs -f frontend

HTTP 的 80 端口只返回健康检查或 308 跳转到 HTTPS；不要用 HTTP 登录。证书更新时替换
`certs/server.crt` 与 `certs/server.key`，重启 frontend，并先在受控客户端验证新证书
链和 SAN。证书过期、私钥权限过宽或 Host/Origin 配错时，应用会拒绝启动或拒绝请求，
按 `docs/operations/release-https-lan.md` 排查。正式开放前仍需完成 #85 的 Release-GATE。

## 本地启动（不使用 Docker）

发布包已带 `dist/`，正常启动不需要 Node：

    cd backend
    cp .env.example .env
    uv sync --frozen --no-dev
    uv run alembic upgrade head
    uv run python -m app.production --host 127.0.0.1 --port 8000

首次使用空库时，在交互终端执行 `uv run python -m app.auth.cli init-admin`。生产入口自检：

    uv run python -m app.production --check --json

旧入口 `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1` 仍兼容。

如需重新构建前端（需要 Node）：

    npm ci
    npm run build        # 产物写 dist/，后端直接托管
    npm run typecheck

## 配置

配置全部在 `backend/.env`（从 `.env.example` 复制，不含真实值）：

- `LLM_ADAPTER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`
- `LLM_VISION_MODEL` 可选（参考图多模态识别），为空则纯文本
- `VISUAL_ADAPTER` / `VISUAL_BASE_URL` / `VISUAL_API_KEY` / `VISUAL_MODEL` /
  `VISUAL_IMAGE_SIZE`（默认 3840x2160）
- `APP_PUBLIC_URL` / `ALLOWED_ORIGINS` / `ALLOW_LOOPBACK_HTTP` / `AUTH_COOKIE_SECURE`
- `APP_DATA_DIR`（默认 `backend/data`）与 `DATABASE_URL` 仅在需要自定义数据根时修改。

改完 `.env` 后重新创建容器才会生效：

    docker compose up -d --force-recreate backend

填写后的 `.env` 含密钥，不要提交、打包或放到公共网盘。

## 数据与备份

`backend/data` 通过 volume 挂到容器 `/app/backend/data`。首次启动会创建：

- `app.db`（SQLite；可能同时存在 `app.db-wal` / `app.db-shm`）
- `generated/`（生成图）、`references/`（参考图上传）、`tmp/`

数据库只存相对路径，整目录就是全部运行数据。手工备份前先 `docker compose stop backend`，
再复制整个 data 目录；不要只拷 `app.db` 而丢掉 WAL，否则最近写入会丢。

### 周期备份与轮换

默认 Compose 把宿主机 `./backups` 挂到 `/app/backups`，并设置
`APP_BACKUP_DIR=/app/backups`，所以后端运行期间会自动周期备份；服务启动时若已过期会
立即补做。备份是 SQLite 在线快照加 `references/`、`generated/`、`published/` 文件清单，
新快照会独立校验恢复成功后才写入 `latest.json`；校验失败不会删除上一份可用快照。

- 默认周期 `APP_BACKUP_INTERVAL_SECONDS=86400`（24 小时），保留最近
  `APP_BACKUP_KEEP=7` 份已验证快照；旧快照只有在当前仍可校验时才会被删除。
- 目录必须位于 `APP_DATA_DIR` 和 `WEB_DIST_DIR` 之外；可由维护者改到受限本机磁盘。
  不要把备份目录放进网页静态目录，应用也不提供网页整库导出。
- 快照不包含 `.env`、模型密钥或 TLS 私钥。备份目录本身含完整业务数据，按敏感数据
  限制访问权限并单独做介质备份。
- 状态文件 `status.json` 记录最近尝试、成功、失败原因和下次到期时间；本地查看：

      docker compose exec backend python -m app.maintenance.cli backup-status --output /app/backups --json

  手工补做仍使用本地 CLI（先停服务）：

      docker compose stop backend
      docker compose run --rm backend python -m app.maintenance.cli backup --source /app/backend/data --output /app/backups --service-stopped
      docker compose start backend

轮换和恢复校验都不等于把备份切换为生产数据。生产恢复必须停服务，由本机维护 CLI 在验证备份并保存 safety backup 后切换；恢复会撤销全部会话和未使用链接、保留审批/交付撤销状态，并打开账本缺口闸门。详见 `docs/operations/release-restore-recovery.md`。

### 保留期清理

运行期和每次启动都会按类型清理到期内容：终态未发布候选图片 24 小时、普通诊断 30 天、安全审计 90 天。已发布图片、未认领 legacy 资源、审批/确认/风险/恢复证据不会被删除；候选文件删除后数据库行保留并标记 `retention_expired`，未知文件缺失仍会使备份失败。清理与文件发布/备份使用同一维护协调器，不提供网页清理或整库导出。备份兼容和秘密扫描说明见 `docs/operations/release-retention-secrets.md`。

## 受控恢复

生产恢复只允许本机维护 CLI，不提供网页整库导入。先停止完整 App，再执行：

    python -m app.maintenance.cli restore --source /app/backend/data --backup /app/backups/backup-<utc>-<hex> --output /app/backups --confirm-restore RESTORE --service-stopped --json

真实数据根还需要维护者明确授权并加 `--allow-real-data-root`。恢复后旧密码和旧启用状态不会复活，所有旧会话与未使用链接失效；新受理会被缺口闸门阻断。核对账号、项目、审批、交付和用量后，用 `clear-restore-gap --confirm-clear CLEAR_RESTORE_GAP` 审计关闭。不要手工改数据库绕过闸门。

从旧机器迁移时，先停机，再把旧 data 目录整体覆盖到本目录后启动。迁移 0002 会把旧
生成图标记为 `legacy_unreviewed` 并回填 `legacy_items`；历史图不会自动算作已验收，
维护者须完成来源核实与认领，旧内容仍需独立审查。

## 端口与远程访问

默认端口只绑 `127.0.0.1`。跨机器访问推荐 SSH 隧道：

    ssh -L 8090:127.0.0.1:8090 -L 8000:127.0.0.1:8000 user@目标机器

如需修改绑定地址，先在受信 HTTPS 入口后部署并完成安全评估；不要直接暴露后端端口。
本应用要求登录，但任何能访问 8090 的人仍可尝试登录并使用已授权账号的模型额度。

## 发布包校验

    sha256sum -c SHA256SUMS
    python -m json.tool RELEASE-MANIFEST.json >/dev/null

发布包中不应出现 `.env`、`node_modules`、`.venv`、`tests`、`__pycache__`、
`*.db` 或任何用户数据。若发现，请停止使用并向维护者报告。

## 常见问题

- backend 一直 unhealthy：看 `docker compose logs backend`，确认 `backend/.env` 已填写、
  `backend/data` 对容器可写、迁移能正常执行。
- 前端起来但接口 404/502：确认 backend 已 healthy；nginx 默认反代 `http://backend:8000`，
  可用 `API_UPSTREAM` 覆盖。
- 8090 打不开：端口只绑了 127.0.0.1，见「端口与远程访问」。
- 构建慢/失败：多为镜像源网络问题，重试 `docker compose build --no-cache`。
