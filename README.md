# 电商生图智能体

前端、后端与必要部署文件位于本仓库的 [`main/`](main/) 目录。该目录是独立应用根目录，包含源码、前端构建产物、依赖锁、数据库迁移与 Docker 配置。

## 下载迁移包

推荐从 [GitHub Releases](../../releases/latest) 下载 `ecommerce-image-workspace-0.1.0.zip`，解压后阅读包内的 `README.md`。当前部署说明修订版为 `v0.1.0-migration.20261111.1`。Release 同时提供单独的 README、安全检查报告、发布清单及 SHA256 校验文件。

也可以克隆本仓库后进入应用目录：

```text
git clone https://github.com/sixpence6/main.git
cd main/main
```

私有仓库需要使用有权限的 GitHub 账号。后续命令均在包含 `docker-compose.yml` 的应用目录执行；使用 ZIP 时进入解压目录，使用仓库时进入上述 `main/main`。

## 快速部署（Docker）

安装并启动 Docker Engine / Docker Desktop 与 Compose v2。Windows 使用 Linux 容器模式。首次构建需要网络。

Windows PowerShell 创建配置：

```powershell
Copy-Item backend/.env.example backend/.env
notepad backend/.env
```

Linux 创建配置：

```bash
cp backend/.env.example backend/.env
nano backend/.env
```

在 `backend/.env` 中取消对应注释或补充本机访问配置：

```dotenv
APP_PUBLIC_URL=http://127.0.0.1:8090
ALLOWED_ORIGINS=["http://127.0.0.1:8090"]
ALLOWED_HOSTS=["127.0.0.1","localhost","[::1]"]
ALLOW_LOOPBACK_HTTP=true
AUTH_COOKIE_SECURE=false
```

模型可暂时留空以验证页面和登录；实际对话与图片生成需要填写 `LLM_*` 和 `VISUAL_*` 的供应商地址、模型名与密钥，具体示例见 [完整部署说明](main/README.md)。

启动并创建管理员：

```text
docker compose up -d --build
docker compose ps
docker compose exec backend python -m app.auth.cli init-admin
```

按终端提示设置管理员邮箱与密码，然后在当前设备浏览器打开 `http://127.0.0.1:8090`。已有管理员时跳过初始化。

## 完整说明与安全范围

- [完整部署 README](main/README.md)：Windows / Linux、模型配置、管理员初始化、访问验证、HTTPS 局域网访问、原生运行、备份迁移及故障排查。
- [安全检查报告](main/SECURITY-REPORT.md)：扫描、依赖与测试范围。
- [发布文件清单](main/RELEASE-MANIFEST.json)和[包内 SHA256 校验](main/SHA256SUMS)。

本仓库中的应用文件与已审核迁移包一致，不包含真实 `.env`、密钥、数据库、账号会话、聊天记录、用户图片、日志或备份。新设备从空数据开始，需自行配置凭据并创建管理员；历史数据通过受控方式单独迁移。

默认 Docker 端口只绑定 `127.0.0.1`。局域网访问请按完整 README 配置 HTTPS；不要直接将本机 HTTP 例外用于局域网。修改 `.env` 后执行 `docker compose up -d --force-recreate backend frontend` 应用配置。
