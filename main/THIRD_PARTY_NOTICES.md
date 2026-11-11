# 第三方依赖与许可说明

本发布包只包含本项目自身的源代码、配置模板、迁移和前端构建产物，不包含第三方依赖的
源码、二进制缓存或虚拟环境。运行时依赖由显式锁文件安装：

- 前端：`package.json` + `package-lock.json`
- 后端：`backend/pyproject.toml` + `backend/uv.lock`

锁文件记录了完整依赖树和版本；安装时请使用 `npm ci` 与 `uv sync --frozen`，不要用
无锁解析替换。以下列出直接运行依赖及其许可证标识；传递依赖的许可证以锁文件中的包
元数据和各包自带 LICENSE 为准。

## 前端运行时依赖

| 包 | 版本 | 许可证 |
| --- | --- | --- |
| element-plus | 2.14.7 | MIT |
| vue | 3.5.44 | MIT |
| vue-router | 4.6.4 | MIT |

## 后端运行时依赖

| 包 | 许可证 |
| --- | --- |
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| SQLAlchemy | MIT |
| aiosqlite | MIT |
| alembic | MIT |
| pydantic-settings | MIT |
| httpx | BSD-3-Clause |
| pillow | MIT-CMU |
| python-multipart | Apache-2.0 |
| argon2-cffi | MIT |

## 分发注意

- 本仓库当前没有项目级 LICENSE 文件；对外分发本项目自身代码前，维护者应先确定并补充
  项目许可证。本文件只记录第三方依赖，不改变项目自身授权。
- 若重新构建前端或更新锁文件，应同步复核本表；`npm ls --omit=dev`、`uv tree --no-dev`
  可用于核对直接依赖。
- 本包不附带 `.env`、真实密钥、历史数据库或用户图片；`backend/.env.example` 只是空模板。
