# Release T27 重复配置收缩与干净发布包验收记录

- Issue: #79
- 父 Issue: #52
- 分支: ticket/79
- 基线: integration/v3 61a0440
- 代码提交: 051ffa39866485f89cc7886d7eec729553ba327e
- 验收日期: 2026-11-01
- 状态: 通过（Release T27 阶段条件；#51 仍由 Release-GATE 关闭）

## 1. 结论与边界

本票把 `main/` 确认为唯一生产包，删除仓库根目录已经没有消费者的重复构建入口，建立显式发布清单，并生成可在全新隔离根目录独立安装、迁移、启动和完成业务冒烟的发布包。根目录仍然保留开发测试、真实配置和既有数据根；没有删除任何真实数据，也没有把测试、虚拟环境、缓存、秘密配置或历史数据库放进发布产物。

1. `release/package-manifest.json` 是发布包的唯一允许清单；只有 `include` 中列出、且被 Git 跟踪的 `main/` 文件可以进入产物，未跟踪文件、测试、缓存、虚拟环境、`.env`、数据库与日志一律排除。
2. `tools/release_package.py` 提供无副作用的 `--check` 和可重复的 `--build`；构建先执行 `npm run build`，再复制清单文件与 `main/dist`，最后写出逐文件 SHA-256 的 `RELEASE-MANIFEST.json` 与 `SHA256SUMS`。
3. 仓库根的 `vite.config.ts` 与 `docker/` 副本已删除；根 `package.json` 的 dev/build/preview 脚本显式转发到 `main/vite.config.ts`，根 `docker-compose.yml` 通过 Compose `include` 复用 `main/docker-compose.yml`。根目录不再维护第二套前端构建或容器定义。
4. 根目录仍保留 `package-lock.json`、`tsconfig.json`、`vitest.config.ts`、`backend/pyproject.toml`、`backend/uv.lock`、`backend/migrations` 与 `backend/tests`，因为它们是开发验证入口、测试运行环境和既有 `.env`／数据目录的宿主；这些不是运行时副本，已在验收记录中列为残余配置。
5. 发布包内容为扁平的生产包根：`backend/`、`src/`、`docker/`、`dist/`、`package.json`、`docker-compose.yml`、`vite.config.ts`、README、第三方声明与发布清单；不包含任何测试目录、`.venv`、`node_modules`、`__pycache__`、`pytest`／Ruff 缓存、`.env`、数据库文件或日志。
6. 发布包在全新隔离根目录中执行 `uv sync --frozen --no-dev` 成功，创建独立 Python 3.12.13 虚拟环境并安装 31 个运行时依赖；随后 `alembic upgrade head` 从空 SQLite 迁移到 0010，`python -m app.production --check --json` 报告 `app.main:app` 与 0010。
7. 同一发布包启动完整 App 后完成管理员初始化、账号登录、项目创建、S06 受理、S07–S10 后台链路、人工批准与发布、下载当前交付；结果为 S10 `completed`、一张图片、下载 29,373 字节 `image/png`，用量为 20 日限／已用 1／剩余 19。
8. fake provider 记录 6 次 chat 与 1 次 image 调用，角色顺序为 `generation_engineer`、`output_reviewer`、`quality_compliance`、`output_reviewer`、`delivery_manager`、`output_reviewer`；没有调用真实供应商。
9. 前端全量 38 个文件／173 个测试通过，typecheck 退出 0，生产构建 1664 个模块、860 ms；后端非 v2 362 个测试与 v2 235 个测试全部通过，合计 597 个通过，无失败、跳过或未知。
10. 本票不实现 HTTPS／局域网入口、备份轮换、恢复切换、保留期清理或真实数据认领；这些属于 #80–#85。也没有验证多平台镜像、镜像签名或供应链证明。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t79 |
| 分支 | ticket/79 |
| 基线 | integration/v3 61a0440 |
| 操作系统 | Windows；Python 3.12.13；Node v26.12.1；npm 11.20.0 |
| 前端构建 | Vite 8.3.5；Vitest 5.0.5 |
| 安装器 | uv（C:/Users/28425/.local/bin/uv.exe） |
| 数据库 | 全新隔离 SQLite；alembic 0010 |
| 模型适配器 | 本地 fake OpenAI-compatible provider；无真实供应商调用 |
| 发布包输出 | .worktrees/t79/.scratch/t79-release2-final 与 .scratch/t79-fresh |

## 3. 实现摘要

### 3.1 显式发布清单

- `release/package-manifest.json` 声明包名、版本来源、允许路径、必需文件、禁止组件／文件／后缀、生成文件和构建命令。
- 允许清单只含生产运行时需要的前端源码、后端应用、迁移、锁文件、Docker 配置、配置样例、文档和构建后的 `dist`。
- 必需文件在检查阶段逐个验证；缺失时 `--check` 返回非零并列出问题。

### 3.2 发布包工具

- `tools/release_package.py` 通过 `git ls-files main` 取得跟踪文件，用 `fnmatch` 匹配允许清单，拒绝任何禁止路径；不会把未跟踪文件带入产物。
- `--build` 先运行 `npm run build`，再把选中文件复制到临时目录，创建空的 `backend/data/.gitkeep`，生成逐文件哈希清单和 `SHA256SUMS`，最后原子替换输出目录并生成 ZIP。
- `--json` 输出单行 JSON，便于测试和 CI 解析；`--check` 不创建目录、不构建、不启动服务。

### 3.3 根目录重复配置收缩

- 删除根 `vite.config.ts`、根 `docker/Dockerfile`、根 `docker/backend.Dockerfile`、根 `docker/nginx.conf.template` 和根 `.dockerignore`。
- 根 `package.json` 的 dev/build/preview 改为 `vite ... --config main/vite.config.ts`；根 `tsconfig.json` 去掉已删除配置的 include。
- 根 `docker-compose.yml` 只保留 `name` 与 `include: main/docker-compose.yml`，不再复制服务定义。
- 根目录保留的测试、锁文件、迁移副本和真实配置作为开发验证入口与数据宿主，明确记录为残余项；没有移动或删除 `backend/data`、`main/backend/data`、`.env` 或既有真实数据。

### 3.4 发布文档与第三方声明

- `main/README.md` 改为发布包根目录视角，说明 `RELEASE-MANIFEST.json`／`SHA256SUMS`、Docker 前必须复制 `.env.example`、首管理员、回环 HTTP 例外、生产入口与数据目录。
- `main/backend/README.md` 说明独立后端包的生产入口与迁移。
- `main/THIRD_PARTY_NOTICES.md` 列出直接依赖及其许可证，并指向锁文件追踪间接依赖；没有伪造项目 LICENSE。

### 3.5 发布包验证

- `backend/tests/test_t79_release_package.py` 覆盖清单检查无副作用、禁止路径、产物排除秘密／测试／数据、逐文件哈希、ZIP 内容与 `source_commit`。
- `backend/tests/test_t78_production_entry.py` 更新为根目录不再维护第二套构建配置、根 Compose 引用 main、锁文件仍一致；5 个测试通过。

## 4. 命令与结果

### 4.1 清单检查

```powershell
cd D:/Desktop/新建文件夹/.worktrees/t79
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe tools/release_package.py --check --json
```

结果：退出码 0，`ok=true`，`selected_tracked_files=170`，`required=20`，`issues=[]`；`main/dist` 缺失时仅输出警告。

### 4.2 发布包构建

```powershell
D:/nodejs/node.exe D:/Desktop/新建文件夹/node_modules/vite/bin/vite.js build --config main/vite.config.ts
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe tools/release_package.py --build --output-root .scratch/t79-release2 --json
```

结果：前端构建退出码 0，Vite 8.3.5、1664 个模块、807 ms，产物 CSS 430.37 kB、JS 1,159.11 kB；发布包 `file_count=176`、`zip_bytes=956513`、`dist_files=3`。产物目录与 ZIP 均为 `ecommerce-image-workspace-0.1.0`；174 个文件有 SHA-256（另加 `RELEASE-MANIFEST.json` 与 `SHA256SUMS`）。测试运行时构建日志由 `PATH` 提供 `git` 与 npm。

### 4.3 全新隔离根安装与迁移

```powershell
cd .scratch/t79-fresh/ecommerce-image-workspace-0.1.0/backend
C:/Users/28425/.local/bin/uv.exe sync --frozen --no-dev
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m app.production --check --json
```

结果：`uv sync` 退出 0，创建 Python 3.12.13 虚拟环境并安装 31 个运行时依赖；迁移从 0001 到 0010 全部执行；`--check` 返回 `entrypoint=app.main:app`、`migration_head=0010`、`backend_dir=<包根>/backend`、`web_dist_dir=<包根>/dist`。

### 4.4 发布包业务冒烟

```powershell
D:/nodejs/node.exe D:/Desktop/新建文件夹/.worktrees/t79/.scratch/t79-prod/run_fresh.mjs
```

结果：退出码 0。`stage=S10`、`status=completed`、`delivery_filename=delivery-52f84ec1-r1.png`、`download_bytes=29373`、`content_type=image/png`；usage 为 daily_limit 20、used 1、remaining 19；fake stats 为 chat 6、images 1、角色序列符合预期。服务使用发布包内的 `backend/app` 与 `dist`；冒烟脚本只作为开发侧外部调用。

### 4.5 前端全量与构建

```powershell
cd D:/Desktop/新建文件夹/.worktrees/t79
npm test -- --run --reporter=dot
npm run typecheck
npm run build
```

结果：38 个文件／173 个测试通过，24.35s；typecheck 退出 0；构建退出 0，Vite 8.3.5、1664 个模块、860 ms，CSS 430.37 kB、JS 1,159.11 kB，仅有 chunk 体积警告。日志 `.scratch-t79/frontend-full.log`、`typecheck.log`、`build.log`。

### 4.6 后端全量回归

```powershell
cd D:/Desktop/新建文件夹/.worktrees/t79/backend
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest [显式非 v2 测试文件] -q
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest [显式 v2 测试文件] -q
```

结果：非 v2 362 个测试通过，402.62s，2 个警告；v2 235 个测试通过，317.84s；合计 597 个通过，无失败、跳过或未知。唯一警告为 `backend/tests/support/auth.py:42` 的 per-request cookie DeprecationWarning。执行前只设置 `PYTHONPATH`，没有导出 `APP_DATA_DIR`／`DATA_DIR`／`WEB_DIST_DIR`。

### 4.7 发布包专项测试

```powershell
cd D:/Desktop/新建文件夹/.worktrees/t79/backend
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest tests/test_t79_release_package.py tests/test_t78_production_entry.py -q
```

结果：3 个发布包测试与 5 个生产入口测试通过，共 8 个通过；发布包测试覆盖清单、禁止文件、哈希、ZIP 与 `source_commit`，生产入口测试覆盖布局、锁文件、路径、`--check`、全新迁移启动与旧入口兼容。

## 5. 验收条件对照

| 验收项 | 本票证据 | 结论 |
| --- | --- | --- |
| V3-AC-20：不可信输入、文件名、路径、SQL、模型输出与定位符不造成注入／越界访问 | 发布清单只允许显式相对路径；工具拒绝路径穿越组件与禁止文件；发布包不含测试、秘密、数据库；既有 R1/R2 全量回归通过 | 阶段条件通过 |
| V3-AC-21：响应、前端、业务库、日志、文件名及备份不泄漏认证秘密／模型密钥 | 发布包排除 `.env`、`.venv`、`node_modules`、数据库与日志；README/第三方声明不含密钥；发布包冒烟使用 fake-key；后端认证与所有权回归通过 | 阶段条件通过 |
| V3-AC-40：鉴权／库／审计／归属故障按职责安全失败，另一操作者可复现维护步骤 | 发布包内 `--check`、迁移、启动、首管理员与故障排查步骤写入 README／运行手册；全新隔离根可独立复现；迁移失败不启动服务 | 阶段条件通过 |

## 6. 证据位置

- 清单：`release/package-manifest.json`
- 工具：`tools/release_package.py`
- 发布包专项测试：`backend/tests/test_t79_release_package.py`
- 生产入口测试：`backend/tests/test_t78_production_entry.py`
- 发布包文档：`main/README.md`、`main/backend/README.md`、`main/THIRD_PARTY_NOTICES.md`
- 构建日志：`.scratch-t79/*.log`
- 发布包：`.scratch/t79-release2-final/ecommerce-image-workspace-0.1.0` 与同名 ZIP
- 全新根冒烟：`.scratch/t79-prod/run_fresh.mjs`、`.scratch/t79-prod/result.json`
- 运行手册：`docs/operations/release-clean-package.md`

## 7. 残余风险与未覆盖边界

1. 发布包冒烟使用本地 fake OpenAI-compatible provider，不验证真实供应商协议、生成质量、内容政策或可用性；真实模型闭环属于 #85。
2. 本票验证已有构建器生成的 Linux 镜像配置引用 main 声明文件，但没有在收缩后重新构建并运行全部容器组合；该验证在 #78 完成，最终 Release-GATE 应复核。
3. 根目录仍保留开发用锁文件、迁移副本、测试与 `.env` 宿主；它们不属于发布包，但在同一 Git 工作树中共存，维护者必须按 README 的包根路径操作，避免误用根路径。
4. `main/backend/data/.gitkeep` 在源码检出中可能不存在（目录被忽略），发布工具会生成空目录占位；这是预期行为，真实数据不会进入包。
5. `--check` 在没有 `main/dist` 时只警告并返回成功；需要发布产物的流水线必须使用 `--require-dist` 或先构建。
6. 本票没有验证多平台镜像、镜像签名、SBOM 或供应链证明。
7. 本票不关闭 #51；Release 后续票仍需完成 HTTPS、备份、恢复、保留期和真实数据边界验证。
