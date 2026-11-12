# 电商生图智能体

本仓库使用 npm workspace：仓库根目录保存 workspace 清单、锁文件及开发验证入口，`main/` 保存前端源码和后端应用源码。**源码 checkout 中的 `main/` 不是可单独复制部署的完整应用目录。**

## 源码 workspace

克隆后停留在仓库根目录（包含根 `package.json`、`package-lock.json`、`main/` 与 `backend/` 的目录）：

```text
git clone https://github.com/sixpence6/main.git
cd main
npm ci
npm run dev
```

根脚本将前端开发与构建转发到 `main` workspace：

```text
npm run build
npm run check
```

前端产物写入 `main/dist/`。`npm run check` 检查前端依赖图、类型、前端测试、后端 lint 和定向后端测试；`npm run check:full` 执行完整后端测试。后端验证需要仓库根 `backend/` 的开发依赖环境；后端应用源码位于 `main/backend/app/`。不要在 `main/` 中另建 npm 锁文件或另装一套 workspace 依赖。

源码部署还需要根 workspace 的锁文件、后端运行配置与迁移入口；仅复制 `main/` 无法保留这些依赖关系。源码 Docker 与原生启动的目录约定见 [部署说明](main/README.md)。本轮集成结果见 [2026-11-12 验收记录](docs/acceptance/review-integration-2026-11-12.md)，其中未验证项保持开放。

## Standalone ZIP

Standalone ZIP 是发布工具生成、带独立依赖锁和部署配置的产物。它采用扁平目录：解压根目录直接包含 `backend/`、`docker-compose.yml`、`package.json` 等文件；它与源码仓库根、源码 `main/` 的目录布局不同。

下载时选择维护者明确指定的 tag / release，并核对该次附件的 README、`RELEASE-MANIFEST.json` 与 SHA256。仓库保留的历史 tags / releases 及 `v0.1.0-migration.20261111.1` 迁移说明用于追溯，不能证明当前 checkout 已经生成或验证了对应 ZIP；不要仅凭 `/releases/latest` 或文件名判断源码与产物一致。

在指定 ZIP 解压根目录中，按包内 README 配置 `backend/.env`，再执行：

```text
docker compose up -d --build
docker compose ps
docker compose exec backend python -m app.auth.cli init-admin
```

上述是 standalone ZIP 的操作目录约定；本轮 ZIP 的构建、摘要、隔离安装及启动结果仍须在验收记录中补证。已有管理员时跳过初始化。私有仓库和发布附件需要相应 GitHub 访问权限。

## 配置与运行边界

配置模板、模型供应商设置、本机 HTTP、受控 HTTPS 局域网、原生启动、备份恢复及故障排查见 [部署说明](main/README.md)。配置必须按实际布局放置：源码原生入口使用仓库根 `backend/.env`，源码 Docker Compose 使用 `main/backend/.env`，standalone ZIP 使用解压根 `backend/.env`。

填写后的 `.env`、密钥、数据库、会话、用户图片、日志、备份与 TLS 私钥不得随源码或发布包分发。新设备需配置凭据并初始化管理员；历史数据通过受控备份另行迁移。发布物是否满足这些条件须按本次实际产物检查，旧安全报告不替代当前扫描。

默认本机访问源为 `http://127.0.0.1:8090`；HTTP 例外仅限当前机器回环访问。局域网开放须配置可信 HTTPS、精确 Host / Origin 并完成相应验收。修改 `.env` 后重新创建容器以应用配置。
