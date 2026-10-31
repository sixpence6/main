# R1 参考上传与工作流归属闭环操作说明

- **适用版本**：v3 R1，ticket #60（代码提交 8436d80 及之后的文档提交）
- **适用对象**：部署／运维人员、管理员、普通用户
- **前置阶段**：C0 备份与维护基线（#54）、R1 首管理员与登录（#55）、账号创建与一次性链接（#56）、本人改密与多端会话（#57）、账号停用／恢复与安全事件（#58）、项目归属隔离（#59）已合并
- **状态**：本说明只覆盖参考图上传／删除与工作流动作的归属闭环、实际操作者审计和外部模型数据说明；不覆盖历史／恢复双门禁、后台任务归属、旧数据认领或 R1 全阶段验收

## 1. 范围与硬边界

本票上线后，服务端在参考图和工作流入口执行：

1. 上传、删除参考图前先校验当前账号是项目所有者，再解析 multipart 和写文件；他人、未知和未归属项目统一 404。
2. 工作流动作装载项目前校验有效主体和本人项目，提交时在事务内再次校验；项目锁与 expected_revision 校验保持有效。
3. 参考图存储名由服务端 asset_id 生成，不使用上传文件名；资源字节读取再次校验项目所有者。
4. 实际操作者写入 audit_events，记录账号、项目、动作、结果、原因和必要详情；不改变 v2 的模型职责、审批角色和产物状态机。
5. 前端自报的 approved_by、owner_account_id、is_admin、artifact_id、card_digest 等只作为断言，不能授权；伪造字段不产生审批。
6. 首次业务输入区域显示外部模型数据说明，但不承诺任何供应商零保留。

明确不允许：

- 通过修改数据库把项目临时“共享”给其他账号；共享／协作不在 v3 范围。
- 给管理员增加读取普通用户项目、参考图或资源字节的旁路。
- 把 reference_uploaded、reference_removed、workflow_action 的 detail 扩展成请求正文、模型原始输出、密码、Cookie、一次性链接或完整异常文本。
- 在没有备份与回滚预案时直接在生产数据根执行验收演练或文件清理。

## 2. 前置条件

| 项 | 要求 |
|---|---|
| 服务版本 | 已包含 #55–#60 的构建 |
| 数据库 | SQLite；迁移版本为 0005；本票无新增迁移 |
| 迁移树 | main/backend 与 backend 两处迁移文件一致；不得修改已应用的 0003／0004／0005 |
| 网络边界 | 仅可信内网／局域网；不得直接暴露公网 |
| 账号 | 至少一个可用管理员；普通用户通过管理员创建并完成一次性链接设置 |
| 外部模型 | 如配置 LLM／视觉适配器，必须先确认供应商数据处理条款；应用不替代供应商合规评审 |
| 数据 | 禁止在真实业务数据上做验收演练；演练使用临时 DATA_DIR |

## 3. 操作步骤

### 3.1 升级前备份

1. 按 docs/operations/maintenance-backup.md 停止业务写入或进入维护窗口。
2. 使用 C0 维护 CLI 生成一致性备份，确认 SQLite、引用文件与清单校验通过。
3. 记录备份目录和校验摘要；不要在未验证备份的情况下继续。

### 3.2 部署代码

本票不新增数据库迁移：

1. 部署包含代码提交 8436d80 的构建。
2. 在 main/backend 目录执行 alembic current，确认仍为 0005。
3. 不需要执行 alembic upgrade；若显示低于 0005，先按 #59 的迁移说明补齐，不要跳版。

### 3.3 启动与健康检查

1. 启动正式入口 uvicorn app.main:app，确认 /api/health 返回 200。
2. 使用管理员登录，打开 /login、工作区和描述需求页。
3. 确认描述需求页显示「外部模型数据说明」，且没有第三方脚本、遥测或远程资源请求。

### 3.4 验证参考图归属

使用两个普通账号 A、B 和临时数据根：

1. A 创建项目并上传一张 PNG／JPEG 参考图，记录项目 ID 和资源 ID。
2. B 直接上传到 A 的项目、读取 A 的资源字节、删除 A 的参考图。预期均为统一 404，不出现 403 或 409 的存在性泄露。
3. 管理员重复上述访问。预期同样为 404，不产生代看成功。
4. 检查项目目录：文件应位于 references/{asset_id}.{ext}，不得按原始上传文件名落盘，不得在数据根外创建文件。
5. 用 B 的身份在请求体或表单中伪造 owner_account_id、is_admin、approved_by 等字段，预期 422 或不产生授权效果。

生产验证使用临时账号与临时项目；不要把真实用户图片或项目内容写入工单、日志或聊天记录。

### 3.5 验证工作流动作

1. A 在本人项目中执行 confirm_requirements、submit_request 等合法动作，确认原有阶段流转不变。
2. B 对 A 的项目执行相同动作，预期 404；管理员执行同样 404。
3. 使用 A 的合法项目但提交 B 项目的 artifact_id、artifact_digest 或 previous_generation_id，预期 409 或 404，不产生审批／生成任务。
4. 在请求体加入 approved_by、owner_account_id、is_admin 等伪造字段，预期不改变审批角色或项目归属。
5. 在已有 workflow run 的项目上调用旧 v1 写入口，预期 409 LEGACY_FLOW_DISABLED。

### 3.6 检查实际操作者审计

以只读方式查询 audit_events：

    SELECT occurred_at, event_type, result, actor_account_id, target_id, reason_code
    FROM audit_events
    WHERE event_type IN ('reference_uploaded', 'reference_removed', 'workflow_action')
    ORDER BY occurred_at DESC, id DESC
    LIMIT 20;

预期：

- reference_uploaded 的 actor_account_id 是实际上传账号，target_id 是项目 ID；detail 只包含 mime_type、width、height、bytes。
- reference_removed 的 detail 只包含 had_reference。
- workflow_action 的 actor_account_id 是实际动作账号，detail 只包含 action_id、expected_revision，必要时包含 run_id、stage；失败时 reason_code 为稳定错误码。
- 事件中没有密码、Cookie、会话令牌、一次性链接、请求正文或模型原始错误。

注意：本票的这三类操作者事件写入 audit_events，但不在 #58 的最小安全事件 allowlist 中，管理员安全事件页面不会显示它们；需要审计时使用受控只读查询。

### 3.7 外部模型数据边界检查

1. 检查构建产物包含「外部模型数据说明」和「不承诺供应商零保留」。
2. 检查模型调用请求只包含业务必要字段，不含会话 Cookie、CSRF、密码、恢复令牌或一次性链接。
3. 如果真实供应商适配器已配置，按供应商合同确认数据保留和训练政策；应用侧说明不构成供应商零保留承诺。
4. 用户输入和模型输出均按数据处理，不把其中的文本当 HTML、SQL、路径或命令执行。

## 4. 失败处理

| 现象 | 处理 |
|---|---|
| 匿名访问业务接口返回 401 AUTH_REQUIRED | 正常默认拒绝；先登录。 |
| 访问他人／未知／未归属项目返回 404 PROJECT_NOT_FOUND | 正常隔离；不要通过管理员或数据库绕过。 |
| 读取他人资源返回 404 ASSET_NOT_FOUND | 正常隔离；不要返回 403 以暴露存在性。 |
| 上传缺少 CSRF 返回 403 CSRF_INVALID | 刷新 CSRF 后重试；不要关闭 CSRF 校验。 |
| 上传 Origin 不匹配返回 403 ORIGIN_INVALID | 检查 ALLOWED_ORIGINS 与受信任入口；不要放行任意来源。 |
| 上传图片返回 422 | 检查格式、解码和体积限制；不要绕过图片校验直接写文件。 |
| 旧入口写入返回 409 LEGACY_FLOW_DISABLED | 使用当前工作流；不要重新打开未迁移写入口。 |
| 工作流动作返回 503 CAPABILITY_NOT_READY | 未配置模型能力；这是预期边界，不是归属失败。 |
| 动作成功但审计行缺失 | 检查应用日志中的 failed to record operator event；不要盲目重放动作；按项目状态和请求 ID 核对后再处理。 |
| 动作失败但没有审计行 | 只有进入分发器的动作才记录；分发前错误（未知动作、修订冲突）不产生 workflow_action 事件。 |

## 5. 回滚

本票没有数据库迁移，回滚只需回退应用代码到 #59 的构建：

1. 停止服务或进入维护窗口。
2. 部署上一版构建；数据库保持 0005，不需要降级。
3. 保留已写入的 audit_events；不要删除审计记录来“恢复干净”。
4. 参考图文件仍位于 references 目录，按现有保留策略处理；不要手工移动到数据根外。
5. 启动后执行健康检查和一次本人项目上传／工作流动作。

如果回滚涉及数据库降级或清理引用文件，必须先完成一致性备份并演练恢复，按 #54 的维护流程执行。

## 6. 证据与复核

- 验收记录：docs/acceptance/r1-ownership-closure.md
- 目标测试：backend/tests/test_t60_ownership_closure.py（3 passed in 2.59s）
- 全量后端：521 passed, 3 warnings in 467.21s，日志 .scratch/t60-backend-full.log
- 前端：33 个测试文件、206 个测试通过；vue-tsc exit 0；Vite build 714ms
- 真实 HTTP：.scratch/t60-ownership-smoke/result.json 与 uvicorn.log
- 代码提交：8436d80af1b7ab6335a5becaaf4c867927c9bc5a

本票不覆盖 #61 历史／恢复双门禁、#62 后台任务归属与禁用截断、#63 旧数据认领、#64 R1-GATE；这些必须在对应票中另行验收。
