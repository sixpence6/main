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
    docker/
      Dockerfile                 # 前端：node 构建 -> nginx 托管
      backend.Dockerfile         # 后端：uv 装依赖 -> python slim 运行
      nginx.conf.template        # 前端 nginx 反代 /api 到 backend
    package.json / package-lock.json
    tsconfig.json / vite.config.ts
    index.html / src/ / dist/    # 前端源码与随包构建产物
    backend/
      app/                       # 后端源码（唯一源码）
      pyproject.toml / uv.lock   # 后端依赖与锁
      alembic.ini / migrations/  # 数据库迁移（0001 -> 0010 head）
      .env.example               # 空配置模板（无密钥）
      data/                      # 空目录（.gitkeep 占位），运行时写入库与图片

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

后端启动时先执行 `alembic upgrade head`（空 data 会自动建库并升到 0010），再通过
`python -m app.production` 拉起单 worker uvicorn；前端容器等后端健康检查通过后启动
nginx。

首次启动后，在服务器本机交互终端创建唯一首管理员（密码只从终端读取）：

    docker compose exec backend python -m app.auth.cli init-admin

实例已有管理员时该命令会拒绝重入。本应用不提供网页注册或网页首管理员初始化。

访问：

- 工作台 http://127.0.0.1:8090
- API 文档 http://127.0.0.1:8000/api/docs
- 健康检查 http://127.0.0.1:8000/api/health

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

数据库只存相对路径，整目录就是全部运行数据。备份前先 `docker compose stop backend`，
再复制整个 data 目录；不要只拷 `app.db` 而丢掉 WAL，否则最近写入会丢。

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
