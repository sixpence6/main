# Release T27 干净发布包运行手册

本文说明如何从显式清单生成、校验、安装、启动和排查发布包。它只覆盖 #79 的重复配置收缩与干净发布包；HTTPS、备份轮换、恢复切换、保留期清理和真实数据认领属于后续 Release 票。

## 1. 产物与边界

- 唯一生产源码：`main/`；发布包根是 `main/` 内容扁平化后的目录，不再多一层 `main`。
- 唯一允许清单：`release/package-manifest.json`；只有清单中列出且被 Git 跟踪的文件可以进入发布包。
- 构建工具：`tools/release_package.py`；`--check` 只校验，`--build` 生成目录与 ZIP。
- 默认输出：`release-artifacts/ecommerce-image-workspace-<version>/` 与同名 ZIP；本票证据输出到 `.scratch/t79-release2`。
- 发布包包含 `backend/data/.gitkeep` 空目录占位，不包含任何真实数据库、图片、`.env`、虚拟环境、缓存或测试。
- 仓库根仍保留开发测试、真实配置和既有数据根；不要把根目录当成发布包根运行。

## 2. 前置条件

- Git 可用（工具通过 `git ls-files` 读取允许清单中的跟踪文件）。
- Node/npm 可用（构建前端 `main/dist`）。
- Python 3.12 与 uv 可用（安装后端锁文件）。
- 已确认 `main/dist` 由当前源码构建，不能把旧产物混入新包。
- 真实数据目录、`.env`、备份和 TLS 私钥不参与本流程。

## 3. 生成发布包

在仓库根执行：

```powershell
cd D:/Desktop/新建文件夹
npm run build
python tools/release_package.py --build --output-root release-artifacts --json
```

若使用本机 uv 环境，可执行：

```powershell
C:/Users/28425/.local/bin/uv.exe run --project D:/Desktop/新建文件夹/.worktrees/t75/backend python tools/release_package.py --build --output-root release-artifacts --json
```

工具会先构建前端，再复制清单允许的跟踪文件、`main/dist` 和生成的空 `backend/data/.gitkeep`，最后写出 `RELEASE-MANIFEST.json` 与 `SHA256SUMS`。`--json` 输出单行 JSON 供流水线读取。

## 4. 校验发布包

```powershell
python tools/release_package.py --check --json
python tools/release_package.py --check --require-dist --json
```

重点确认：

| 字段 | 期望 |
| --- | --- |
| `ok` | `true` |
| `selected_tracked_files` | 大于 100，且与当前提交匹配 |
| `required` | 20 个必需文件全部存在 |
| `issues` | 空数组 |
| `dist_files` | 有前端构建产物 |

发布包内可独立复核：

```powershell
cd release-artifacts/ecommerce-image-workspace-0.1.0
Get-Content RELEASE-MANIFEST.json
Get-Content SHA256SUMS
```

`RELEASE-MANIFEST.json` 记录 `source_commit`、`migration_head` 与每个文件的 SHA-256、大小；`SHA256SUMS` 用于标准校验工具。

## 5. 全新环境安装

```powershell
cd <包根>/backend
uv sync --frozen --no-dev
uv run alembic upgrade head
uv run python -m app.production --check --json
```

发布包默认数据目录为 `<包根>/backend/data`；若要使用其他隔离目录，先创建目录并显式设置 `APP_DATA_DIR`。数据库迁移失败会阻止生产入口启动，这是有意的 fail-closed 行为。

## 6. 启动与首管理员

```powershell
cd <包根>/backend
uv run alembic upgrade head
uv run python -m app.production --host 127.0.0.1 --port 8000
```

空实例首次使用需在交互终端创建唯一首管理员：

```powershell
cd <包根>/backend
uv run python -m app.auth.cli init-admin
```

本机回环 HTTP 需要按 README 临时设置 `APP_PUBLIC_URL`、`ALLOWED_ORIGINS`、`ALLOW_LOOPBACK_HTTP=true` 与 `AUTH_COOKIE_SECURE=false`；正式环境必须使用 HTTPS 并保持安全 Cookie。Docker 使用前必须从 `backend/.env.example` 复制 `backend/.env` 并填写 provider 配置。

## 7. 包内检查与停止

- `uv run python -m app.production --check --json` 检查导入根、数据根、数据库路径、前端产物和迁移头。
- `uv run python -m app.production --host ...` 启动单 worker；停止时按 Ctrl+C，等待进程退出，保留数据目录。
- 不要在服务运行时直接复制 SQLite；备份与恢复按 `docs/operations/maintenance-backup.md` 和后续票据执行。
- 发布包不包含运行时数据；升级时替换代码与锁文件，保留独立的数据目录和已验证备份。

## 8. 故障排查

| 现象 | 含义／处理 |
| --- | --- |
| `git ls-files failed` | 当前目录不是 Git 工作树或 Git 不在 PATH；在仓库根执行并检查 Git |
| `frontend dist is missing` | 先执行 `npm run build`，或用 `--require-dist` 让检查失败 |
| `missing required file` | 清单必需文件被删除或未跟踪；恢复文件后重新检查 |
| `selected file violates package policy` | 清单匹配到禁止路径；检查 manifest 与源树，不要把测试/秘密/数据加入允许清单 |
| `unable to open database file` | 显式 `APP_DATA_DIR` 不存在；先创建目录或用默认包内数据目录 |
| `StartupError: 数据库未完成迁移` | 先执行 `alembic upgrade head`，并核对 `--check --json` 的 `database_path` |
| 包内没有 `.env` | 这是安全边界；从 `backend/.env.example` 复制并填入部署配置，绝不把真实 `.env` 放进包 |

## 9. 数据、安全与回退边界

- 发布包永远不包含 `backend/data`、`main/backend/data`、`.env`、`.venv`、`node_modules`、数据库或日志；发现时视为构建失败。
- 发布清单与 `SHA256SUMS` 用于验证产物完整性；不要把清单文件的哈希当作模型输出真实性证明。
- 本票没有切换真实数据源、删除真实数据或自动迁移；真实数据操作必须先由维护者指定来源并完成验证备份。
- 回退时保留旧数据目录和已验证备份，按相同迁移头和入口检查后再启动；不得通过删除数据库或清空缓存来“修复”启动失败。

## 10. 相关文档

- 验收记录：`docs/acceptance/release-clean-package.md`
- 生产入口：`docs/operations/release-production-entry.md`
- 包说明：`main/README.md`、`main/backend/README.md`
- 第三方声明：`main/THIRD_PARTY_NOTICES.md`
- 备份与维护：`docs/operations/maintenance-backup.md`
