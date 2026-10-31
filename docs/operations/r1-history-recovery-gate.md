# R1 历史、找回与图片真实字节门禁操作说明

- **适用版本**：v3 R1，ticket #61（代码提交 4c17fb8 及之后的文档提交）
- **适用对象**：部署／运维人员、管理员、普通用户
- **前置阶段**：C0 备份与维护基线（#54）、R1 认证与账号（#55–#58）、项目归属隔离（#59）、参考图与工作流归属闭环（#60）已合并
- **状态**：本说明只覆盖历史、找回、参考图和生成结果真实字节的双重门禁，以及公开构建目录与私有数据目录分离；不覆盖后台任务归属、旧数据认领或 R1 全阶段验收

## 1. 范围与硬边界

本票上线后，服务端执行：

1. 项目详情、工作流、生成查询、找回、参考图与资源字节继续先校验有效主体和本人项目；未知、他人和未归属对象统一不可获得。
2. 项目详情只返回属于当前项目的参考图与结果资产元数据；即使数据库关联被改写成其他项目资产，也不把对方 ID、尺寸、locator 或字节暴露出来。
3. 参考图读取核对数据根内真实文件、sha256（新上传）和图片尺寸；摘要或尺寸不匹配返回 409，不返回原图。
4. 生成结果只有通过 v2 G08／交付门禁后才返回真实字节：review_status=approved、有效审批、匹配的 requirement／rules／prompt、GenerationJob 已发布、Delivery 为 published、清单审批与摘要一致。待审、仅批准、撤销、过期、隔离、缺失文件均不返回字节。
5. 公开构建目录必须位于私有数据目录之外；启动时拒绝重叠配置。
6. 静态资源只服务 WEB_DIST_DIR 内的构建文件；references、generated、published、data 不作为公开别名，供应商 locator 也不能当资源 URL。

明确不允许：

- 为了让页面显示而临时放宽 review_status、Delivery 状态、摘要或审批校验。
- 把私有数据目录加入 WEB_DIST_DIR，或把构建产物放进 APP_DATA_DIR。
- 通过管理员账号读取普通用户项目、图片或历史；管理员没有代看豁免。
- 删除、移动或改写 references／generated／published 文件来“修复”404／409；先核对资产和交付状态。
- 在真实业务数据上做本票验收演练；演练使用临时 DATA_DIR。

## 2. 前置条件

| 项 | 要求 |
|---|---|
| 服务版本 | 已包含 #55–#61 的构建 |
| 数据库 | SQLite；迁移版本为 0005；本票无新增迁移 |
| 迁移树 | main/backend 与 backend 两处迁移文件一致；不得修改已应用的 0003／0004／0005 |
| 配置 | APP_DATA_DIR 与 WEB_DIST_DIR 必须为不同目录，且互不包含 |
| 网络边界 | 仅可信内网／局域网；不得直接暴露公网 |
| 账号 | 至少一个可用管理员；普通用户通过管理员创建并完成一次性链接设置 |
| 备份 | 升级前已按 docs/operations/maintenance-backup.md 完成一致性备份并验证 |
| 数据 | 禁止在真实业务数据上做验收演练；演练使用临时 DATA_DIR |

## 3. 操作步骤

### 3.1 升级前备份

1. 按 docs/operations/maintenance-backup.md 停止业务写入或进入维护窗口。
2. 使用 C0 维护 CLI 生成一致性备份，确认 SQLite、引用文件、生成文件与清单校验通过。
3. 记录备份目录和校验摘要；不要在未验证备份的情况下继续。

### 3.2 部署代码

本票不新增数据库迁移：

1. 部署包含代码提交 4c17fb8 的构建。
2. 在 main/backend 目录执行 alembic current，确认仍为 0005。
3. 不需要执行 alembic upgrade；若显示低于 0005，先按 #59 的迁移说明补齐，不要跳版。

### 3.3 检查目录配置

在启动前确认：

    APP_DATA_DIR=D:/.../data
    WEB_DIST_DIR=D:/.../dist

要求两者不同，且任一方都不是另一方的父目录或子目录。若 WEB_DIST_DIR 位于 APP_DATA_DIR 内，应用会以 ValueError: WEB_DIST_DIR must be outside APP_DATA_DIR 拒绝启动。这是有意的安全边界，不要把构建产物重新放回数据目录来绕过。

### 3.4 启动与健康检查

1. 启动正式入口 uvicorn app.main:app，确认 /api/health 返回 200。
2. 使用管理员登录，打开 /login、工作区、描述需求页与结果页。
3. 检查登录页、工作区和静态资源加载正常；浏览器控制台不应出现引用私有路径的请求。

### 3.5 验证历史与图片字节

使用两个普通账号 A、B 和临时数据根：

1. A 创建项目并上传一张 PNG／JPEG 参考图，记录项目 ID 和资源 ID；读取本人参考图，确认字节与上传一致。
2. B 和管理员直接读取 A 的项目详情、工作流、参考图、资源字节，预期均 404。
3. 在临时数据根内篡改 A 的参考图文件一个字节，A 再读取预期 409 DIGEST_MISMATCH，响应不含原始字节；恢复文件后应再次 200 且字节一致。
4. 用 A 的项目 ID 拼出 /references、/generated、/published、/data/references、/static 下的猜测路径，预期均 404，不返回图片或目录列表。
5. 用 provider locator 形状的字符串作为 /api/assets/{id}，预期 404，不发起任意外部地址请求。
6. 生成结果：未审批、仅批准未发布、已撤销、已过期、隔离或缺失文件时读取资源，预期 409（缺失文件 404），响应不包含图片字节；发布后预览与 ?download=true 应返回同一真实字节，下载为 attachment。

### 3.6 验证参考图摘要兼容

1. 新上传参考图会写入 sha256；篡改文件后必须 409。
2. 旧参考图若没有 sha256，只按图片宽高核验；这是兼容路径，完整性弱于新上传。若要提升旧引用完整性，需单独迁移或重新上传，不能手工改库写入假摘要。
3. 参考图尺寸不一致同样按 DIGEST_MISMATCH 拒绝。

### 3.7 验证历史与跨项目关联

1. A 创建项目并上传参考图；B 创建另一个项目并上传参考图。
2. 在临时数据库把 A 项目的 reference_asset_id 指向 B 的资产，并让 A 的生成记录 result_asset_id 指向 B 的资产。
3. A 读取项目详情，预期 reference_asset 为 null、生成记录 result_asset 与 result_asset_id 为 null，响应不含 B 的资产 ID；A 读取 B 的资产仍 404。
4. 恢复临时数据库；不要在生产数据上执行此类篡改演练。

## 4. 失败处理

| 现象 | 处理 |
|---|---|
| 启动报 ValueError: WEB_DIST_DIR must be outside APP_DATA_DIR | 把前端构建产物移到数据目录之外，更新 WEB_DIST_DIR；不要改代码绕过。 |
| 匿名访问业务接口返回 401 AUTH_REQUIRED | 正常默认拒绝；先登录。 |
| 访问他人／未知／未归属项目返回 404 PROJECT_NOT_FOUND | 正常隔离；不要通过管理员或数据库绕过。 |
| 读取他人资源返回 404 ASSET_NOT_FOUND | 正常隔离；不要改成 403 暴露存在性。 |
| 读取参考图返回 409 DIGEST_MISMATCH | 文件字节与登记摘要不一致；按备份核对并恢复原文件，不要更新摘要来“修复”。 |
| 读取生成图返回 409 ASSET_UNAVAILABLE | 资产未通过审批／交付／发布或已撤销；检查 review_status、Approval、GenerationJob、Delivery 状态。 |
| 读取生成图返回 409 REQUIREMENT_CHANGED／RULES_MISMATCH／APPROVAL_MISSING | 审批与当前需求快照、规则版本或摘要不匹配；按当前工作流重新确认，不要手工插审批。 |
| 读取资产返回 404 ASSET_FILE_MISSING | 数据根内文件缺失；先核对备份与资产登记，不要用其他项目文件顶替。 |
| 旧静态路径返回 404 | 正常；公开构建只从 WEB_DIST_DIR 提供，私有数据没有静态别名。 |
| OpenAPI／页面正常但图片不显示 | 先按上述状态码判断内容门禁；前端不应以隐藏页面替代服务端保护。 |

## 5. 回滚

本票没有数据库迁移，回滚只需回退应用代码到 #60 的构建：

1. 停止服务或进入维护窗口。
2. 部署上一版构建；数据库保持 0005，不需要降级。
3. 保留 references、generated、published 文件与既有审计记录；不要删除文件或审计来“恢复干净”。
4. 启动后执行健康检查、本人参考图读取和一次已发布结果读取。
5. 若回滚同时涉及数据库降级或文件清理，必须先完成一致性备份并演练恢复，按 #54 的维护流程执行。

## 6. 证据与复核

- 验收记录：docs/acceptance/r1-history-recovery-gate.md
- 目标测试：backend/tests/test_t61_history_recovery_gate.py（4 passed in 3.48s）
- 相关回归：97 passed in 72.51s
- 全量后端：525 passed, 3 warnings in 541.40s，日志 .scratch/t61-backend-full.log
- 前端：33 个测试文件、206 个测试通过；vue-tsc exit 0；Vite build 655ms
- 真实 HTTP：.scratch/t61-history-smoke/result.json；服务日志 .scratch/t61-history-smoke/uvicorn.log
- 冒烟脚本：.scratch/t61-run-smoke.py
- 代码提交：4c17fb8d09cd35b1e064ff3aab682fdb65d847d9

