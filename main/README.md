# 电商生图智能体（自包含部署包）

本目录就是项目根：前端源码 + 后端源码 + 迁移 + 依赖锁 + Docker 配置 + 空的运行数据目录
这里，不依赖仓库根目录的任何文件。目标机器只需要装好 Docker（含 Compose v2），复制本
文件夹后即可构建运行。

    cd main
    docker compose up -d --build

## 目录结构

    main/
      docker-compose.yml           # backend + frontend 两个服务
      docker/
        Dockerfile                 # 前端：node 构建 -> nginx 托管
        backend.Dockerfile         # 后端：uv 装依赖 -> python slim 运行
        nginx.conf.template        # 前端 nginx 反代 /api 到 backend
      package.json / package-lock.json
      tsconfig.json / vite.config.ts
      index.html / src / dist      # 前端源码与上一次构建产物
      backend/
        app/                       # 后端源码(唯一源码)
        pyproject.toml / uv.lock   # 后端依赖
        alembic.ini / migrations/  # 数据库迁移(0001 -> 0002 head)
        .env / .env.example        # 运行配置(包内是空白模板，部署前填写)
        data/                      # 空目录(.gitkeep 占位)，运行时写入库与图片
      README.md                    # 本文件

## 前置条件

- Docker Engine / Docker Desktop，支持 Compose v2（`docker compose version` 能跑）。
- 首次构建需要网络：拉取 node:24-alpine、nginx:1.29-alpine、
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim、python:3.12.12-slim-bookworm，
  以及 npm/pypi 依赖。
- 不需要在目标机器安装 Node、Python、uv、nginx。

## 首次部署

先把 backend/.env 填好（内容与 .env.example 相同，都是空白模板；按目标机器填入
provider 地址和 key。全部为空的配置也能启动，只是模型操作会失败）：

    cd main
    docker compose up -d --build
    docker compose ps
    docker compose logs -f backend

后端启动时会先执行 `alembic upgrade head`（包内 data 是空的，会自动建库并升到 0002
head），然后拉起 uvicorn；前端容器等后端健康检查通过后再起 nginx。

访问：

- 工作台 http://127.0.0.1:8090
- API 文档 http://127.0.0.1:8000/api/docs
- 健康检查 http://127.0.0.1:8000/api/health

## 配置

Provider 配置全部在 backend/.env（包内的 .env 与 .env.example 完全相同，都是空白模板，
不含任何真实 key 或 URL；部署前按目标机器填写）：

- LLM_ADAPTER / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
- LLM_VISION_MODEL 可选（参考图多模态识别），为空则纯文本
- VISUAL_ADAPTER / VISUAL_BASE_URL / VISUAL_API_KEY / VISUAL_MODEL / VISUAL_IMAGE_SIZE

改完 .env 后重新创建容器才会生效：

    docker compose up -d --force-recreate backend

.env 默认是空白模板；填写后的 .env 含真实密钥，不要提交到公开仓库或公共网盘。

## 数据与备份

backend/data 整目录通过 volume 挂到容器 /app/backend/data。包内它是空的（只有 .gitkeep
占位），首次启动会在这里自动创建：

- app.db（SQLite；可能同时存在 app.db-wal / app.db-shm）
- generated/（生成图）、references/（参考图上传）、tmp/

所以现在整个应用是全新状态：没有历史项目、没有历史生成图。要把旧机器上的项目带过来，
先 `docker compose stop backend`，再把旧 data 目录整个覆盖到本目录后启动即可（数据库里
assets 只存相对路径，整目录搬就行）。

备份时停机或至少 `docker compose stop backend` 后整目录复制。**不要只拷 app.db 而丢掉
app.db-wal**，否则最近写入会丢。

## 端口与远程访问

默认两个端口都只绑 127.0.0.1，只有本机能访问，公网暴露风险为零。要在别的机器访问，
推荐 SSH 隧道：

    ssh -L 8090:127.0.0.1:8090 -L 8000:127.0.0.1:8000 user@目标机器

或者改 docker-compose.yml 的 ports 为 "0.0.0.0:8090:80" 之前，先想清楚：这个应用没有登录
鉴权，任何能访问 8090 的人都能操作项目和消耗模型额度。

## 旧数据升级说明

如果你把旧机器上的 data 目录覆盖过来再启动，迁移 0002 会把 kind=generated 的旧资产统一
标记为 review_status=legacy_unreviewed（并回填 legacy_items）。这些历史生成图需要在工作台
走一次「遗留确认」流程，不会自动算作已验收。包内 data 默认是空的，全新部署不会出现这
一步。

## 常见问题

- backend 一直 unhealthy：
  `docker compose logs backend` 看是否 alembic 或 provider 配置报错；
  确认 backend/.env 已按目标机器填好 LLM/VISUAL 配置、backend/data 目录对容器可写（容器内是 root 运行）。
- frontend 起来但页面接口 404/502：确认 backend 已 healthy；nginx 反代目标默认
  http://backend:8000，可用环境变量 API_UPSTREAM 覆盖。
- 8090 打不开：端口只绑了 127.0.0.1，见上面「端口与远程访问」。
- 构建慢/失败：镜像源网络问题；重试 `docker compose build --no-cache`。

## 本地开发（可选，不需要 Docker）

后端：

    cd backend
    uv sync
    uv run alembic upgrade head
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

前端（另一个终端，在 main 下）：

    npm ci
    npm run dev        # http://127.0.0.1:5173，/api 代理到 127.0.0.1:8000
    npm run build      # 产物写 dist/，后端会直接托管它
    npm run typecheck

代码检查（可选）：

    cd backend && uv run ruff check .

测试代码不随部署包发布：单元/集成测试留在开发仓库的 backend/tests 与根 tests/ 目录里，
需要时再复制回来。

## 说明

这个 main/ 是从原仓库整合出来的自包含部署副本：原仓库根目录的 docker/、
docker-compose.yml、package*.json、tsconfig.json、vite.config.ts、backend/ 下的配置/
迁移/数据等已复制到这里。原仓库布局仍然保留可继续开发；如果后续又改了后端代码，
只需要把 backend/app 的最新代码放进本目录，必要时重新复制 migrations/ 即可。
