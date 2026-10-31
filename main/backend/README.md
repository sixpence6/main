# 后端工作区

后端源码在 app/（唯一源码），本目录同时放依赖锁、迁移、配置、数据库和图片。

## 安装依赖

    cd backend
    uv sync

## 迁移 + 单 worker 跑在 loopback

    uv run alembic upgrade head
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

后端默认托管 ../dist（前端构建产物）。需要页面时在上一级目录执行 `npm ci && npm run build`。

## 配置

运行配置来自本目录的 .env（首次部署从 .env.example 复制后填 provider 值）。关键项：

- LLM_ADAPTER / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（需求整理）
- LLM_VISION_MODEL / LLM_VISION_BASE_URL / LLM_VISION_API_KEY（可选，参考图识别）
- VISUAL_ADAPTER / VISUAL_BASE_URL / VISUAL_API_KEY / VISUAL_MODEL / VISUAL_IMAGE_SIZE

两个角色都留空时后端仍能启动，只在配置诊断里报 not ready。

## 数据

data/ 保存 SQLite 库（app.db，可能带 -wal/-shm）以及 generated/、references/、tmp/。
数据库里只存相对路径，所以整个 data/ 目录就是全部运行数据；Docker 里同样挂载到这里。

## 代码检查

    uv run ruff check .

测试代码不随部署包发布，需要时从开发仓库 backend/tests 复制回来。

## 目录

- pyproject.toml / uv.lock：依赖与锁
- .env.example：空配置样例（无密钥）
- alembic.ini / migrations：数据库迁移（head = 0002）
- app/：FastAPI 应用（config.py 同时兼容本目录为项目根和旧仓库布局）
- data/：运行数据，启动时自动创建缺失目录
