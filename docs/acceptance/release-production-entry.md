# Release T26 自包含生产入口验收记录

- Issue: #78
- 父 Issue: #52
- 分支: ticket/78
- 基线: integration/v3 e95f69e
- 代码提交: 88b704f9370c4f032c42217a3cbca2991491fc07
- 验收日期: 2026-11-01
- 状态: 通过（Release T26 阶段条件；#51 仍由 Release-GATE 关闭）

## 1. 结论与边界

本票把 `main/` 作为单一生产包，新增唯一的 Python 生产入口 `app.main:app`，并让仓库根目录的旧开发入口与旧容器入口在扩展期继续可用。生产入口、依赖锁、迁移、前端产物、数据根和容器构建来源现在可以分别追溯；`main` 与旧布局在临时 SQLite 上跑通了同一套登录、受理、生成、审批、发布与下载流程。

1. 唯一生产入口是 `main/backend/app/production.py` 中的 `ENTRYPOINT = "app.main:app"`；运行 `python -m app.production` 时导入根固定为 `main/backend/app`，不使用仓库根目录的开发工作区。
2. `python -m app.production --check --json` 只解析配置并输出有效路径、迁移头与 Python 解释器，不创建数据目录、不启动服务、不调用模型；输出不含 API key、密码、会话 token 或私钥。
3. `main/package.json`、`main/package-lock.json`、`main/backend/pyproject.toml`、`main/backend/uv.lock` 是生产依赖来源；仓库根的同名文件只作为开发工作区副本，测试逐字节核对锁文件与前端依赖清单一致。
4. 两份迁移树 `main/backend/migrations/versions` 与 `backend/migrations/versions` 的 0001–0010 文件逐字节一致，`head_revision()` 返回 `0010`；本票没有新增迁移。
5. 根目录与 `main` 的 Dockerfile 都从 `main/` 读取应用、迁移、前端与 nginx 模板；后端镜像 CMD 为 `alembic upgrade head && exec python -m app.production --host 0.0.0.0 --port 8000`，不再依赖 `--app-dir ../main/backend` 的旧启动形态。
6. 旧 `python -m uvicorn app.main:app` 与 `backend/run.py` 仍可作为兼容入口；专项测试启动旧 uvicorn 入口验证 `/api/health` 与静态首页，停止测试进程后不残留。
7. 本地生产入口从全新临时 SQLite 启动：迁移到 0010，创建管理员与普通账号，登录后完成一次 `confirm_requirements` 受理，后台链路执行 S07–S10，人工批准并发布交付，`GET /api/assets/{id}?download=true` 返回 `image/png` 与 29,373 字节。
8. 根镜像对与 `main` 镜像对在独立端口、独立临时数据根上得到相同可比结果：S10 completed、一张图片、一次生成、相同角色调用序列、相同下载字节数与 content type；文件名只因随机 UUID 不同。
9. 前端生产构建、类型检查、全量前端、全量后端与专项测试全部通过；全量后端为 359 个非 v2 + 235 个 v2 = 594 个通过，无失败、跳过或未知。
10. 本票不实现 HTTPS、局域网监听、备份轮换、恢复切换、保留期清理或真实数据认领；这些分别属于 #80–#85。也没有删除重复运行配置或构建最终发布包，那属于 #79。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t78 |
| 分支 | ticket/78 |
| 基线 | integration/v3 e95f69e |
| 操作系统 | Windows，Git Bash 工作树；容器为 Linux |
| Node | v26.12.1 |
| npm | 11.20.0 |
| Vite | 8.3.5 |
| Vitest | 5.0.5 |
| Python | 3.12.13（复用既有 t75 工作树 .venv 依赖；生产代码来自 t78） |
| Docker | 29.8.2；docker-compose v5.5.1 |
| 基础镜像 | node:24-alpine、nginx:1.29-alpine、python:3.12.12-slim-bookworm、ghcr.io/astral-sh/uv:python3.12-bookworm-slim |
| 数据库 | 临时 SQLite；本地与容器行为均迁移至 alembic 0010 |
| 模型适配器 | 本地 fake OpenAI-compatible provider；无真实供应商调用 |

## 3. 实现摘要

### 3.1 唯一生产入口

- 新增 `main/backend/app/production.py`：`RuntimeInfo` 冻结数据类记录 entrypoint、import_root、repository_root、backend_dir、workspace_dir、main_dir、data_dir、database_path、web_dist_dir、migration_head、python_executable、working_directory。
- `main(argv)` 支持 `--check`、`--json`、`--host`、`--port`；非检查模式调用 `uvicorn.run("app.main:app", workers=1, proxy_headers=False, server_header=False)`。
- `runtime_info()` 把生产包固定为 `MAIN_DIR / "backend"`，因此即使从仓库根目录执行，也不会误导入开发布局中的另一个 `app`。
- `backend/run.py` 作为旧工作区包装器，把 `main/backend` 插入 `sys.path` 后调用 `app.production.main()`；旧 uvicorn 入口继续可用。

### 3.2 单一依赖、迁移与构建来源

- 根 `docker/backend.Dockerfile` 与 `main/docker/backend.Dockerfile` 都复制 `main/backend/pyproject.toml`、`main/backend/uv.lock`、`main/backend/alembic.ini`、`main/backend/migrations`、`main/backend/app`。
- 根 `docker/Dockerfile` 与 `main/docker/Dockerfile` 都使用 `main/package.json`、`main/package-lock.json`、`main/src` 与 `main/docker/nginx.conf.template` 构建前端。
- `.dockerignore` 新增 `main/backend/.env`、`main/backend/.venv`、`main/backend/data`、测试缓存、`main/node_modules` 与 `main/dist`，避免把配置秘密、运行数据或宿主机产物带入构建上下文。
- `main/README.md` 与 `main/backend/README.md` 更新为 head 0010、`python -m app.production`、首管理员本机 CLI、`APP_PUBLIC_URL`、`ALLOWED_ORIGINS` 与回环 HTTP 例外说明；`.env.example` 给出对应注释示例。

### 3.3 扩展期兼容入口

- 旧入口 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1` 保留；`backend/run.py` 也可执行 `--check` 或启动。
- `test_workspace_wrapper_and_legacy_uvicorn_entry_remain_compatible` 实际启动旧 uvicorn 入口，等待 `/api/health` 返回 `{"status":"ok"}` 并验证首页标记，然后终止子进程。
- 本票没有把默认启动切换到生产入口之外的其他路径，也没有删除开发验证入口；最终收缩留给 #79。

### 3.4 本地与容器启动

- 本地：在 `main/backend` 执行 `uv sync --frozen --no-dev`、`uv run alembic upgrade head`、`uv run python -m app.production --check --json`、`uv run python -m app.production --host 127.0.0.1 --port 8000`。
- 容器：后端镜像启动先运行 `alembic upgrade head`，成功后 `exec python -m app.production`；单 worker，代理头与 Server 头关闭。
- 运行数据通过 `APP_DATA_DIR` 或 `DATA_DIR` 显式指定；数据库文件与图片目录由配置解析，不使用隐含的仓库根 data。

### 3.5 全新环境业务流程

- 本地生产 smoke：临时 SQLite + 临时数据根 + `main/dist` + fake provider；创建管理员、普通账号和项目，种子 S06 最终需求卡，提交 `confirm_requirements`，轮询到 S10，批准并发布交付，下载当前资产。
- 容器 smoke：先用宿主侧脚本把临时 SQLite 迁移到 0010，再启动根镜像对与 main 镜像对；通过前端 nginx 入口登录和提交，后端仍执行同一套服务代码。
- fake provider 记录 6 次 chat 调用和 1 次 image 调用；角色顺序为 `generation_engineer`、`output_reviewer`、`quality_compliance`、`output_reviewer`、`delivery_manager`、`output_reviewer`。

### 3.6 失败与边界处理

- 生产入口启动前不自动迁移；本地必须先执行 `alembic upgrade head`，否则 `prepare_database` 以 `StartupError` 拒绝启动，避免在缺表数据库上提供服务。
- 容器 CMD 把迁移和启动串成 `&&`，迁移失败不会启动 uvicorn；没有自动重试或自动切换数据源。
- 模板配置中的 provider 值为空时服务仍可启动，但配置诊断会把模型能力标为未就绪，实际模型操作会失败而不是伪造成功。

## 4. 命令与结果

### 4.1 专项生产入口测试与 Ruff

```powershell
cd D:/Desktop/新建文件夹/.worktrees/t78/backend
C:/Users/28425/.local/bin/uv.exe run python -m pytest tests/test_t78_production_entry.py -q
C:/Users/28425/.local/bin/uv.exe run python -m ruff check tests/test_t78_production_entry.py ../main/backend/app/production.py
```

结果：5 个测试通过，6.64s，退出码 0；Ruff 退出码 0。测试覆盖布局与锁文件一致、runtime_info 有效路径、无副作用 `--check`、全新迁移 SQLite 启动生产入口、旧包装器与旧 uvicorn 兼容。日志 `.scratch/t78-evidence/t78-test.log`、`t78-ruff.log`。

### 4.2 本地生产入口 smoke

```powershell
D:/nodejs/node.exe D:/Desktop/新建文件夹/.scratch/t78-prod/run_local.mjs
```

结果：退出码 0。临时 SQLite 迁移到 0010；生产入口 `--check --json` 返回 `entrypoint=app.main:app`、`migration_head=0010`；S10 completed，`delivery_asset_id=ab4df721-4675-461a-942f-17b23aa10168`，`delivery_filename=delivery-ab4df721-r1.png`，`download_bytes=29373`，`content_type=image/png`；usage daily_limit 20、used 1、remaining 19；fake stats chat 6、images 1、角色序列符合预期。结果文件 `.scratch/t78-prod/result.json`，运行日志 `.scratch/t78-evidence/local-prod-smoke.log`。

### 4.3 容器构建与根/main 等价性

```powershell
docker build -f docker/backend.Dockerfile -t t78-root-backend:local .
docker build -f docker/Dockerfile -t t78-root-frontend:local .
docker build -f main/docker/backend.Dockerfile -t t78-main-backend:local main
docker build -f main/docker/Dockerfile -t t78-main-frontend:local main
D:/nodejs/node.exe D:/Desktop/新建文件夹/.scratch/t78-prod/run_containers.mjs
```

结果：四个镜像构建成功，镜像 ID 分别为 `b4298c41f795`、`c133173af3f6`、`2e1a80dbb1e1`、`5b582398acf1`。容器等价性脚本退出码 0，`equivalent=true`：根与 main 均为 S10 completed、`image/png`、29,373 字节、usage_used 1、images 1、相同六个角色调用；文件名仅随机 UUID 不同。结果 `.scratch/t78-prod/container-equivalence.json`；构建日志 `.scratch/t78-evidence/docker-build-{root-backend,root-frontend,main-backend,main-frontend}.log`。

容器 smoke 的 fake provider 通过 `host.docker.internal` 访问宿主测试服务；未访问真实供应商。临时 SQLite 只在隔离临时根，未使用 `backend/data` 或 `main/backend/data`。

### 4.4 前端全量、类型检查与构建

```powershell
cd D:/Desktop/新建文件夹/.worktrees/t78
npm test -- --run --reporter=dot
npm run typecheck
npm run build
npm --prefix main run build
```

结果：前端 38 个文件／173 个测试通过，31.51s，退出码 0；typecheck 退出码 0；根构建 Vite 8.3.5、1664 个模块、1.44s，退出码 0；`main` 生产包构建 1.03s，退出码 0，产物 `dist/assets/index-B2Fixx5N.css` 430.37 kB、`dist/assets/index-CFEBsUU2.js` 1,159.11 kB，只有 chunk 体积警告。日志 `.scratch/t78-evidence/frontend-full.log`、`typecheck.log`、`build.log`、`main-build.log`。

### 4.5 后端全量回归

```powershell
cd D:/Desktop/新建文件夹/.worktrees/t78/backend
C:/Users/28425/.local/bin/uv.exe run python -m pytest [显式非 v2 测试文件] -q
C:/Users/28425/.local/bin/uv.exe run python -m pytest [显式 v2 测试文件] -q
```

结果：非 v2 359 个测试通过（四个分块 60 + 55 + 55 + 40，加独立组 149），2 个警告；v2 235 个测试通过；合计 594 个通过，无失败、跳过或未知。警告仅为 `backend/tests/support/auth.py:42` 的 per-request cookie DeprecationWarning。执行测试时只设置 `PYTHONPATH`，未导出 `APP_DATA_DIR`／`DATA_DIR`／`WEB_DIST_DIR`，避免覆盖子进程自己的临时数据目录。日志 `.scratch/t78-evidence/backend-nonv2-chunk1..4.log`、`backend-nonv2-2.log`、`backend-v2.log`。

### 4.6 运行数据边界

本票所有本地与容器 smoke 使用 `.scratch/t78-prod` 下的临时数据目录；未读取、写入、迁移或删除 `backend/data` 与 `main/backend/data`。容器使用独立镜像标签与临时端口，没有停止或覆盖已在运行的 `ecommerce-image-workspace:local` / `ecommerce-image-backend:local`。

## 5. 验收条件对照

| 验收项 | 本票证据 | 结论 |
| --- | --- | --- |
| V3-AC-20：不可信输入、文件名、路径、SQL、模型输出与定位符不造成注入／越界访问 | 生产入口不新增业务 HTTP 路由；路径来自配置解析与本地 CLI 参数，不拼接请求文件名；Docker 构建上下文限定 `main` 声明文件；既有 R1/R2 输入、文件名、定位符与所有权测试全量通过 | 阶段条件通过 |
| V3-AC-21：响应、前端、业务库、日志、文件名及备份不泄漏认证秘密／模型密钥 | `RuntimeInfo` 不包含任何密钥字段；`.dockerignore` 排除 `.env`、`.venv`、data；镜像只复制声明源码；smoke 使用 `fake-key`，日志未出现真实密钥；全量认证与所有权回归通过 | 阶段条件通过 |
| V3-AC-40：鉴权／库／审计／归属故障按职责安全失败，另一操作者可复现维护步骤 | 生产入口在迁移不完整时拒绝启动；容器 CMD 迁移失败即退出；`--check --json` 提供可复现路径清单；README 与运行手册记录启动、管理员初始化、检查与故障步骤 | 阶段条件通过 |

## 6. 证据位置

- 生产入口：D:/Desktop/新建文件夹/.worktrees/t78/main/backend/app/production.py
- 兼容包装器：D:/Desktop/新建文件夹/.worktrees/t78/backend/run.py
- 专项测试：D:/Desktop/新建文件夹/.worktrees/t78/backend/tests/test_t78_production_entry.py
- 本地 smoke：D:/Desktop/新建文件夹/.scratch/t78-prod/run_local.mjs、result.json；日志 .scratch/t78-evidence/local-prod-smoke.log
- 容器 smoke：D:/Desktop/新建文件夹/.scratch/t78-prod/run_containers.mjs、container-equivalence.json；构建日志 .scratch/t78-evidence/docker-build-*.log
- 前端日志：.scratch/t78-evidence/frontend-full.log、typecheck.log、build.log、main-build.log
- 后端日志：.scratch/t78-evidence/backend-nonv2-chunk1..4.log、backend-nonv2-2.log、backend-v2.log
- 运行手册：docs/operations/release-production-entry.md

## 7. 残余风险与未覆盖边界

1. 容器 smoke 使用本地 fake OpenAI-compatible provider，不验证真实供应商的协议差异、生成质量、内容政策或可用性；真实模型闭环属于 #85。
2. 容器等价性比较的是业务结果、角色顺序、下载字节数与 content type；没有逐字节比较两个 PNG，也没有验证两边图像内容完全一致。
3. Docker 构建在本机 Docker 29.8.2 的经典构建器上完成；buildx 不可用，未验证多平台镜像、镜像签名或供应链证明。
4. 本票保留根目录与 `main` 的重复配置与迁移副本；最终发布包收缩、测试/缓存/秘密排除清单由 #79 完成。
5. 本票只验证回环、临时 SQLite、完整 App 与本地容器；没有验证 HTTPS 证书信任、真实局域网设备、防火墙或跨设备 Cookie，这些属于 #80 与 #85。
6. 本票没有实现备份轮换、恢复切换、保留期清理、真实数据认领或账号恢复演练；这些分别属于 #81–#84。
7. 生产入口固定单 worker；多 worker、进程管理器和系统服务托管不在本票范围，正式部署需在 #80 的受控入口下确认。
8. 本票不关闭 #51；Release 阶段后续票仍需完成全部四十项验收与真实设备/模型复核。

