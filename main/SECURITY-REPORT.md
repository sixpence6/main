# 迁移发布检查报告

日期：2026-11-11（Asia/Shanghai）。目标仓库：sixpence6/main（保持 PRIVATE）。

## 发布范围

- 应用版本 0.1.0；当前工作区生产源码快照，包含未提交的前后端修订和新增模块。
- 前端源码及 dist 构建产物、后端源码、空配置模板、依赖锁、Docker/Compose、数据库迁移 0001–0017、部署说明。
- ZIP 共 207 个文件；迁移包是全新部署包，backend/data 仅保留空 .gitkeep。
- ZIP SHA256：`c222e19d834f110e8911858dbc82ea4a4cfb828c1edff058bf77ba969681bc15`。
- RELEASE-MANIFEST.json 的 source_commit 是开发基线；source_dirty=true / source_snapshot 说明纳入工作区修订。逐文件 SHA256 固定实际发布内容。

## 密钥和隐私检查

- 未包含任何真实 .env 或环境覆盖文件；backend/.env.example 中 API 密钥字段为空。
- 对两份本机 .env 的 3 个不同真实密钥值进行内存精确比对，ZIP 中命中 0；扫描输出与本报告均不记录密钥值。
- Gitleaks v8.30.1 扫描最终发布目录：0 条泄露。
- 未包含数据库、账号/会话、聊天记录、用户上传图、生成图、运行日志、备份、TLS 私钥、本机凭据或 Git 元数据。
- 未发现本机用户目录路径；ZIP CRC、文件清单与逐文件 SHA256 核验通过。
- GitHub 发布使用仅含审核后迁移文件的独立根提交，不携带本机 Git 历史。未扫描/改写现有远端历史；本报告结论针对本次新增发布内容。
- 扩展 .gitignore / .dockerignore 与打包拒绝规则，排除环境覆盖、私钥、证书目录、备份和运行数据。

## 依赖与行为验证

- npm audit（npm 官方 advisory 接口，仅生产依赖）：0 个已知漏洞。
- pip-audit（从 uv.lock 导出的 31 个锁定运行依赖）：0 个已知漏洞。
- npm run check：依赖图、typecheck、前端 30 个文件 / 235 项测试、后端 lint、236 项目标测试全部通过。
- 额外安全/发布测试：111 项通过，覆盖身份、账号链接、会话、停用、权限隔离、默认拒绝、生产入口、打包、HTTPS/LAN 与敏感信息保留。
- 前端生产构建通过；从独立发布目录构建前后端 Docker 镜像通过。
- Docker 全新临时数据目录启动通过，空 SQLite 数据库升级至 0017；7 项 HTTP 检查通过，包含页面、健康检查、配置状态、匿名保护、Host 与 Origin 拒绝。
- Compose 构建上下文均为发布包自身，默认端口仅绑定 127.0.0.1。

## 修复与复验

- 打包工具改为选择白名单内已跟踪及未被忽略的新生产文件，避免漏掉新增 workflow 模块及 0017 迁移。
- 从根 workspace 锁生成独立锁，转换 Docker/Compose 构建路径，目标设备不再依赖开发仓库父目录。
- 初轮三个失败均已定位为过期测试：缺少两个附件路由、旧 npm 锁布局断言、备份测试未限制临时数据源。更新测试并完整复验后 111 项全部通过；未更改生产权限或来源核验规则。

## 使用边界

本次核验未发现密钥或用户运行数据进入发布物。依赖审计反映检查时 advisory 数据；未执行镜像操作系统漏洞扫描或全面渗透测试。目标设备仍须自行填写模型配置、创建新管理员；未使用真实供应商密钥执行付费模型调用。已有用户数据如需迁移，应经受控本地备份单独传输，不上传 GitHub。

部署步骤见包内 README.md。Windows 首次运行先执行 `Copy-Item backend/.env.example backend/.env`，配置后运行 `docker compose up -d --build`，再执行 `docker compose exec backend python -m app.auth.cli init-admin`。

## README 部署说明修订

本修订发布为 `v0.1.0-migration.20261111.1`。补齐 Windows / Linux 首次部署、配置示例、管理员初始化、访问验证、HTTPS、原生运行和常见故障排查，修正备份目录层级与本机命令路径。

与上一版 ZIP 比较，文件集合不变，只有 README.md、RELEASE-MANIFEST.json、SHA256SUMS 内容变化；前后端代码、依赖锁、镜像构建配置与前端产物逐字节相同。此前镜像构建和运行核验适用于这些未修改文件，本轮未重新执行付费模型调用或扩展部署安全验收。

修订 ZIP 已重新验证所有文件哈希和 ZIP CRC，检查 README 与工作区源文件一致；真实密钥匹配为 0，Gitleaks 扫描为 0 条泄露，11 项打包回归测试通过。原始 Release 保留供追溯，新下载应选择此修订版。
