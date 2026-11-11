# 电商生图智能体（自包含发布包）

本目录是发布包的根：前端源码与构建产物、后端源码、数据库迁移、依赖锁、Docker 配置和
空的数据目录都在这里，不依赖仓库根目录。目标机器只需要 Docker（含 Compose v2）即可
运行；如果不用 Docker，也可以在本机用 Python 直接启动后端并由后端托管 `dist/`。

此包为全新部署包：不包含旧机器的账号、会话、聊天、数据库、上传图片、生成图片、备份或密钥。
旧数据如需迁移，应通过受控本地备份单独传输，不要上传 GitHub。

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
      alembic.ini / migrations/  # 数据库迁移（0001 -> 0017 head）
      .env.example               # 空配置模板（无密钥）
      data/                      # 空目录（.gitkeep 占位），运行时写入库与图片
    backups/                     # 运行后创建的备份目录，与 backend/ 同级

发布包不包含测试、虚拟环境、node_modules、缓存、真实 `.env`、历史数据库或用户图片。

## 前置条件

- Docker Engine / Docker Desktop，支持 Compose v2（`docker compose version` 可运行）。
- 首次构建需要网络：拉取 node、nginx、uv/python 基础镜像和 npm/pypi 依赖。
- 目标机器不需要安装 Node、Python、uv 或 nginx。

## 首次部署（Windows / Linux，Docker）

### 1. 下载并解压

从 GitHub Release 下载 `ecommerce-image-workspace-0.1.0.zip`。Windows 可右键“全部解压”；Linux 可使用 `unzip`。打开终端，进入解压后包含 `docker-compose.yml` 的目录。以下命令均在此目录执行。

Windows 请先安装并启动 Docker Desktop，使用 Linux 容器模式，并完成 Docker Desktop 提示的 WSL 2 / 虚拟化设置。Linux 需要 Docker Engine 和 Compose v2。

确认 Docker 服务已经启动（`docker version` 应同时显示 Client 和 Server）：

```text
docker version
docker compose version
```

首次构建需要联网下载镜像与依赖，此 ZIP 不是离线镜像包。

### 2. 创建目标设备的配置

只在首次部署时复制模板，已有 `.env` 时直接编辑并保留原配置。

Windows PowerShell：

```powershell
Copy-Item backend/.env.example backend/.env
notepad backend/.env
```

Linux：

```bash
cp backend/.env.example backend/.env
nano backend/.env
```

在 `backend/.env` 中取消对应注释或添加以下配置，本机访问统一使用 `127.0.0.1`：

```dotenv
APP_PUBLIC_URL=http://127.0.0.1:8090
ALLOWED_ORIGINS=["http://127.0.0.1:8090"]
ALLOWED_HOSTS=["127.0.0.1","localhost","[::1]"]
ALLOW_LOOPBACK_HTTP=true
AUTH_COOKIE_SECURE=false
```

这组 HTTP 例外只适用于当前机器的回环访问。局域网访问请使用下一节的 HTTPS 配置。

### 3. 填写模型配置

仍在 `backend/.env` 中填写：

```dotenv
LLM_ADAPTER=openai-chat
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=REPLACE_WITH_YOUR_LANGUAGE_MODEL_KEY
LLM_MODEL=REPLACE_WITH_YOUR_LANGUAGE_MODEL_NAME

VISUAL_ADAPTER=openai-images
VISUAL_BASE_URL=https://your-image-provider.example/v1
VISUAL_API_KEY=REPLACE_WITH_YOUR_IMAGE_MODEL_KEY
VISUAL_MODEL=REPLACE_WITH_YOUR_IMAGE_MODEL_NAME
VISUAL_IMAGE_SIZE=3840x2160
```

以上地址、密钥、模型名均为占位示例，必须替换为实际供应商配置。Base URL 填 API 根路径，适配器自行拼接接口路径；图片尺寸须为供应商实际支持的尺寸。

参考图多模态识别可另填 `LLM_VISION_MODEL`；需要不同接口地址或密钥时再填 `LLM_VISION_BASE_URL`、`LLM_VISION_API_KEY`。其余参数见 `.env.example`。

也可先将模型字段留空，验证页面、登录和数据库；此时模型操作会提示能力未就绪。不要将占位示例当作实际凭据。

默认 Docker 部署不要复制旧设备的绝对数据路径。密钥只保存在目标设备的 `.env` 中，不上传 GitHub。

### 4. 启动并创建管理员

```text
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 backend
```

后端先执行数据库迁移，将空数据库升级到 `0017`，再启动单 worker 服务；前端等待后端健康检查通过后启动。

在可交互终端创建首管理员：

```text
docker compose exec backend python -m app.auth.cli init-admin
```

按提示输入邮箱和密码；输入密码时不会显示字符。已有管理员时跳过该命令。应用不提供网页自行注册。

### 5. 打开页面并验证

在目标设备浏览器中访问 **http://127.0.0.1:8090**，使用刚创建的管理员登录。健康检查为 `http://127.0.0.1:8090/api/health`。

Windows PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:8090/api/health
```

Linux：

```bash
curl --fail http://127.0.0.1:8090/api/health
```

未登录访问项目列表返回 `401` 是正常权限保护。填写模型配置后，再做一次实际对话与图片生成，验证目标设备到供应商的网络和额度。

### 6. 日常启动、停止和更新配置

| 操作 | 命令 |
| --- | --- |
| 查看状态 | `docker compose ps` |
| 启动已有容器 | `docker compose start` |
| 停止服务 | `docker compose stop` |
| 查看近期日志 | `docker compose logs --tail=100` |
| 持续查看日志 | `docker compose logs -f backend`，Ctrl+C 退出查看 |
| 修改 `.env` 后应用配置 | `docker compose up -d --force-recreate backend frontend` |
| 修改源码或升级发布文件后重建 | `docker compose up -d --build` |

环境变量在容器创建时注入；仅 `restart` 不会加载新的 `.env` 值。升级前先备份数据，并保留本机的 `.env`、`backend/data/`、`backups/` 和证书目录。

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
      -subj "/CN=192.168.1.10" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:192.168.1.10"
    chmod 600 certs/server.key

把 `certs/server.crt`（或签发它的内部 CA）安装到每台允许访问的客户端信任存储。
浏览器必须通过 `https://192.168.1.10:8443` 这类证书已覆盖的地址访问；不要用 IP 与
证书 SAN 不一致的地址，也不要把私钥放进发布包或 Git。

### 2. 启动

Windows PowerShell（证书已准备好，IP 替换为实际服务器地址）：

```powershell
$env:PUBLIC_HOSTNAME='192.168.1.10'
$env:PUBLIC_ORIGIN='https://192.168.1.10:8443'
$env:PUBLIC_BIND_ADDRESS='192.168.1.10'
$env:PUBLIC_HTTPS_PORT='8443'
$env:TLS_CERT_DIR='./certs'
docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build
```

Linux：

    export PUBLIC_HOSTNAME=192.168.1.10
    export PUBLIC_ORIGIN=https://192.168.1.10:8443
    export PUBLIC_BIND_ADDRESS=192.168.1.10
    export PUBLIC_HTTPS_PORT=8443
    export TLS_CERT_DIR=./certs
    docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build

`PUBLIC_ORIGIN` 必须与浏览器地址栏完全一致（含端口），并与 `PUBLIC_HOSTNAME` 对应。
不要设置 `ALLOW_LOOPBACK_HTTP=true` 或 `AUTH_COOKIE_SECURE=false`；叠加层会强制
HTTPS、Secure Cookie 和受控代理来源。只开放防火墙上的 `PUBLIC_HTTPS_PORT`，
不要开放 8000 或数据目录。

### 3. 验证与运维

    curl --cacert certs/server.crt https://192.168.1.10:8443/api/health
    docker compose -f docker-compose.yml -f docker-compose.lan.yml ps
    docker compose -f docker-compose.yml -f docker-compose.lan.yml logs -f frontend

启用该叠加层后，HTTP 入口只返回健康检查或跳转到 HTTPS；不要用 HTTP 登录。证书更新时替换
`certs/server.crt` 与 `certs/server.key`，重启 frontend，并先在受控客户端验证新证书
链和 SAN。证书过期、私钥权限过宽或 Host/Origin 配错时须先修复再开放访问。

以后在该 HTTPS 部署执行 `up`、`ps`、`logs` 时继续带上这两个 `-f` 文件。重新打开终端后先重新设置环境变量，避免用只有基础配置的 `up` 命令覆盖 HTTPS 配置。

## 本地启动（不使用 Docker）

需要安装 Python 3.12 或兼容版本以及 uv。发布包已带 `dist/`，正常启动不需要 Node。

先从发布包根目录创建 `backend/.env`（见首次部署步骤），将本地访问源改为：

```dotenv
APP_PUBLIC_URL=http://127.0.0.1:8000
ALLOWED_ORIGINS=["http://127.0.0.1:8000"]
ALLOW_LOOPBACK_HTTP=true
AUTH_COOKIE_SECURE=false
```

Linux：

    cd backend
    mkdir -p data
    uv sync --frozen --no-dev
    uv run --no-dev alembic upgrade head
    uv run --no-dev python -m app.production --host 127.0.0.1 --port 8000

Windows PowerShell（从发布包根目录开始）：

```powershell
Set-Location backend
New-Item -ItemType Directory -Force data | Out-Null
uv sync --frozen --no-dev
uv run --no-dev alembic upgrade head
uv run --no-dev python -m app.production --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000`，后端同时托管页面和 API。首次使用空库时，在另一个终端进入 `backend` 目录并执行 `uv run --no-dev python -m app.auth.cli init-admin`。关闭运行终端会停止本地进程。生产入口自检：

    uv run --no-dev python -m app.production --check --json

已有管理员时跳过初始化；日常使用保留单 worker 的生产入口。

如需重新构建前端，安装 Node 24，回到**发布包根目录**执行：

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

轮换和恢复校验都不等于把备份切换为生产数据。生产恢复必须停服务，由本机维护 CLI 在验证备份并保存 safety backup 后切换；恢复会撤销全部会话和未使用链接、保留审批/交付撤销状态，并打开账本缺口闸门。具体参数可用 `docker compose run --rm backend python -m app.maintenance.cli restore --help` 查看。

### 保留期清理

运行期和每次启动都会按类型清理到期内容：终态未发布候选图片 24 小时、普通诊断 30 天、安全审计 90 天。已发布图片、未认领 legacy 资源、审批/确认/风险/恢复证据不会被删除；候选文件删除后数据库行保留并标记 `retention_expired`，未知文件缺失仍会使备份失败。清理与文件发布/备份使用同一维护协调器，不提供网页清理或整库导出。备份本身仍含私有业务数据，不应上传 GitHub。

## 受控恢复

生产恢复只允许本机维护 CLI，不提供网页整库导入。先停止完整 App，使用本机帮助确认恢复参数，并将示例备份目录替换为已验证的实际目录：

    docker compose run --rm backend python -m app.maintenance.cli restore --help

本机原生部署应在 `backend` 目录使用 `uv run --no-dev python -m app.maintenance.cli restore --help`，将路径替换为宿主机实际数据与备份路径。

真实数据根还需要维护者明确授权并加 `--allow-real-data-root`。恢复后旧密码和旧启用状态不会复活，所有旧会话与未使用链接失效；新受理会被缺口闸门阻断。核对账号、项目、审批、交付和用量后，用 `clear-restore-gap --confirm-clear CLEAR_RESTORE_GAP` 审计关闭。不要手工改数据库绕过闸门。

从旧机器迁移时，先停服务并生成已验证的备份，通过受控渠道传输到新设备，再由维护者执行受控恢复。不要将旧 data 或备份上传 GitHub，也不要覆盖仍在运行的新库。若提示存在多个数据源，应先核实来源与摘要，不能自动选库或合并数据。

## 端口与远程访问

默认端口只绑 `127.0.0.1`。跨机器访问推荐 SSH 隧道：

    ssh -L 8090:127.0.0.1:8090 -L 8000:127.0.0.1:8000 user@目标机器

如需修改绑定地址，先在受信 HTTPS 入口后部署并完成安全评估；不要直接暴露后端端口。
本应用要求登录，但任何能访问 8090 的人仍可尝试登录并使用已授权账号的模型额度。

## 发布包校验

Release 附件中的外部 `SHA256SUMS` 校验 ZIP、报告和清单；解压目录内的同名文件校验包内文件。

Windows 下载后可执行以下命令，将结果与外部校验文件比较：

```powershell
Get-FileHash .\ecommerce-image-workspace-0.1.0.zip -Algorithm SHA256
```

Linux 在相应目录执行：

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

- Docker 无法连接服务：启动 Docker Desktop / daemon，确认 `docker version` 有 Server 输出；Windows 使用 Linux 容器模式。
- `.env` 找不到：确认实际文件名是 `backend/.env`，而非 `.env.txt`。
- 登录或写操作提示 Host/Origin 错误：统一使用配置中的协议、地址和端口，修改 `.env` 后重新创建容器。
- 模型操作失败：核对适配器、Base URL、密钥、模型名、输出尺寸、供应商网络与额度。
- 忘记管理员密码：在服务器交互终端执行 `docker compose exec backend python -m app.auth.cli recover-admin` 并按提示操作。
- 端口或容器名冲突：用 `docker ps` 先确认已有实例，不要同时从两份发布目录启动默认同名实例。
