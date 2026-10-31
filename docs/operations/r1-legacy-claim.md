# R1 核源后有回退点的幂等旧数据认领操作说明

- **适用版本**：v3 R1，ticket #63（代码提交 82c8de3977c95bb95cdc24d9846e9286d335fecd 及之后的文档提交）
- **适用对象**：维护者、部署／运维人员、初始管理员
- **前置阶段**：C0 备份与维护基线（#54）、R1 认证与账号（#55–#58）、项目归属隔离（#59）、参考图与工作流闭环（#60）、历史与字节门禁（#61）、后台任务归属（#62）已合并
- **状态**：本说明只覆盖离线旧项目认领；不覆盖真实数据源选择授权、保留期清理、恢复切换或 R1 全阶段验收

## 1. 范围与硬边界

本票提供两个只在本机维护 CLI 中执行的命令：

1. inspect-legacy：只读列出无 owner 且包含 LegacyItem 证据的旧项目，并报告所选来源的内容摘要。
2. claim-legacy：在服务停止、来源唯一、备份已验证、首管明确确认后，把指定旧项目的 owner 设置为首管，并在同一事务写入 legacy_projects_claimed 审计。

明确不允许：

- 网络接口不提供认领路由；不要通过 HTTP、网页表单或管理员 API 尝试认领。
- 不先选择真实来源就认领；存在多个非空候选时必须由维护者提供明确的 --source 和 --confirm-source-digest，不能自动合并。
- 没有有效、已验证、与来源一致的备份时不得认领。
- 不能根据包内空目录、示例配置或测试夹具推断真实数据根。
- 认领只建立 owner；不得借此自动批准 legacy_unreviewed 内容、补造历史操作者、批准记录或生成授权。
- 不得直接改数据库 owner、claim 表或删除未认领旧资产来绕过流程。
- 不在真实业务数据上演练；演练必须使用临时 DATA_DIR 或已批准的隔离副本。

## 2. 前置条件

| 项 | 要求 |
|---|---|
| 服务版本 | 已包含 #55–#63 的构建 |
| 数据库 | SQLite；迁移目标为 0007；本票新增 0007 |
| 迁移树 | main/backend 与 backend 两处迁移文件内容一致；不要修改已应用的 0003–0006 |
| 配置 | APP_DATA_DIR 指向真实来源根；WEB_DIST_DIR 与数据根分离；APP_PUBLIC_URL、ALLOWED_HOSTS、ALLOWED_ORIGINS 已按部署配置 |
| 账号 | 至少一个可登录的首管；认领账号必须是 instance_bootstrap 记录中的首管 |
| 备份 | 已按 docs/operations/maintenance-backup.md 完成一致性备份并通过 verify；备份目录必须在来源根之外 |
| 数据 | 明确记录源目录、数据库和文件集合；不得用空目录或未确认的挂载路径代替 |
| 运行状态 | 认领前停止应用进程；CLI 必须带 --service-stopped |

## 3. 操作步骤

### 3.1 先备份并验证

1. 停止业务写入或进入维护窗口，停止应用进程。
2. 按 docs/operations/maintenance-backup.md 对选定的真实来源执行备份，确认 SQLite、WAL、引用文件、生成文件、发布文件和清单全部通过验证。
3. 记录 backup_id、manifest_sha256、来源根和内容摘要；没有通过验证的备份不得继续。

### 3.2 部署与迁移

1. 部署包含代码提交 82c8de3977c95bb95cdc24d9846e9286d335fecd 的构建。
2. 在 main/backend 目录执行 alembic current，确认目标为 0007；新库执行 alembic upgrade head，已有 0006 的库执行 alembic upgrade 0007。
3. 确认两份迁移文件一致，且 legacy_claims 表、索引和外键存在；不要手工建表或改列。
4. 启动正式入口 uvicorn app.main:app，确认 /api/health 返回 200。

### 3.3 识别唯一来源

1. 执行 inspect-sources 查看候选数据根、数据库和内容摘要。
2. 如只有一个非空来源，记录其路径和 content_digest；如存在多个非空且来源不一致，停止并交由维护者明确选择，不要合并。
3. 维护者确认来源后，后续命令显式带 --source 和 --confirm-source-digest；摘要不一致时 CLI 会拒绝。

示例（路径和摘要为占位符，不得照抄到真实环境）：

    python -m app.maintenance.cli inspect-sources --json
    python -m app.maintenance.cli inspect-sources       --candidate-root SOURCE_DIR       --json

### 3.4 只读检查可认领项目

在应用仍停止时执行：

    python -m app.maintenance.cli inspect-legacy       --source SOURCE_DIR       --confirm-source-digest SOURCE_DIGEST       --json

预期输出 ok=true、source、source_digest 和 projects 数组。每个项目包含 id、title、legacy_items、created_at；只列出 owner_account_id 为空且至少有一个 LegacyItem 的旧项目。把要认领的项目 ID 逐项记录下来，不要一次认领未核对的项目。

### 3.5 明确确认并认领

使用首管邮箱和逐项 --project 执行：

    python -m app.maintenance.cli claim-legacy       --source SOURCE_DIR       --backup BACKUP_DIR       --admin-email ADMIN_EMAIL       --project PROJECT_ID_1       --project PROJECT_ID_2       --confirm-source-digest SOURCE_DIGEST       --confirm-claim CLAIM_LEGACY       --service-stopped       --json

成功输出包含 ok=true、claim_id、idempotent=false、source、source_digest、backup_id、backup_manifest_sha256、admin_account_id、project_ids、snapshot_digest、created_at。首次认领的 idempotent 应为 false；同一备份、同一管理员、同一项目集再次执行应返回 idempotent=true 和相同的 claim_id，claim 行与审计行仍只有一条。

如果出现 SOURCE_CHOICE_REQUIRED、SOURCE_DIGEST_MISMATCH、BACKUP_SOURCE_MISMATCH、BACKUP_INCOMPLETE、SOURCE_CHANGED_SINCE_BACKUP、PROJECT_ALREADY_CLAIMED 或 CLAIM_STATE_CHANGED，应停止并重新核对来源、备份和项目集，不要反复重试或手工改库。

### 3.6 启动后验证

1. 启动完整 App，确认 /api/health 200。
2. 首管登录后 GET /api/projects，只应看到已认领项目；未认领旧项目和不存在项目都应返回 404，不能通过分页、详情、消息、工作流或资源 ID 探测存在性。
3. 读取已认领项目的参考图，应返回真实字节；旧生成资产若仍是 legacy_unreviewed，应返回 409 且不返回内容。
4. 查询 GET /api/admin/security-events，按 legacy_projects_claimed 过滤，应只有本次认领的事件，detail 只含 project_count 等安全字段；不得出现密码、会话令牌、恢复链接、模型密钥或完整业务正文。
5. 新账号登录后 GET /api/projects 应为空；不要用新账号查看旧项目。

### 3.7 回退与恢复

1. 认领前必须先保留通过验证的备份；如认领结果不符合预期，停止服务，按 docs/operations/maintenance-backup.md 的恢复流程在隔离目录先演练恢复。
2. 不要在运行库上手工删除 legacy_claims、清空 owner 或改 Approval／LegacyItem 状态来“撤销”认领；这会被视为篡改审计和业务快照。
3. 如必须降级 0007，先完成一致性备份，在隔离环境演练 alembic downgrade 0006 和恢复；确认不会丢失仍需要的认领、审计和业务记录后再切换生产。
4. 生产恢复切换属于 #82 的控制范围；本票只提供认领前备份和回退依据。

## 4. 失败处理

| 错误码或现象 | 含义／处理 |
|---|---|
| CLAIM_CONFIRMATION_REQUIRED | 缺少 --confirm-claim CLAIM_LEGACY；补上明确确认，不能脚本自动生成。 |
| SERVICE_MUST_BE_STOPPED | 没有 --service-stopped；先停止应用并确认没有写入，再重试。 |
| SOURCE_CHOICE_REQUIRED | 有多个非空来源；由维护者选定 --source 并记录选择，禁止自动合并。 |
| SOURCE_DIGEST_MISMATCH | 提供的 --confirm-source-digest 与当前来源不一致；重新检查挂载和目录，不要忽略。 |
| SOURCE_DIGEST_MISSING | 来源没有可计算内容摘要；检查目录和数据库是否存在及可读。 |
| BACKUP_NOT_FOUND | 备份目录不存在；回到 3.1 生成并验证备份。 |
| BACKUP_OVERLAPS_SOURCE | 备份位于来源根内；把备份放到来源根之外。 |
| BACKUP_INCOMPLETE | 备份缺文件或清单不完整；不得认领，重新备份。 |
| BACKUP_SOURCE_MISMATCH | 备份来自别的来源根；选择正确备份。 |
| BACKUP_SCHEMA_MISMATCH | 备份和来源 schema 不一致；先完成迁移或在正确版本上重新备份。 |
| SCHEMA_NOT_CURRENT | 来源不是当前 alembic head；先按迁移流程升级并验证。 |
| SOURCE_CHANGED_SINCE_BACKUP | 备份后来源内容变化；停止，重新备份并重新选择。 |
| ADMIN_NOT_FOUND／ADMIN_NOT_ELIGIBLE／ADMIN_NOT_BOOTSTRAP | 认领账号不存在、非管理员、不可登录或不是首管；使用正确的本机首管。 |
| PROJECT_NOT_FOUND／PROJECT_NOT_LEGACY | 项目不存在或没有 LegacyItem 证据；重新执行 inspect-legacy。 |
| PROJECT_ALREADY_CLAIMED | 项目已有 owner；不要重复或覆盖，核对既有认领记录。 |
| CLAIM_SNAPSHOT_CHANGED／CLAIM_STATE_CHANGED | 项目内容或 owner 在认领前后变化；停止并检查是否有写入或并行认领。 |
| CLAIM_FAILED | 事务失败并已回滚；查看服务日志中的异常类型，保留现场后重新核对，不要手工补写。 |
| 认领后未认领项目可见 | 错误；检查是否绕过 API 或直接改库，立即停止并按事故处理。 |
| 旧生成资产返回 200 | 错误；检查 review_status、LegacyItem、Approval 和字节门禁，不要用页面隐藏代替后端保护。 |

## 5. 证据与复核

- 验收记录：docs/acceptance/r1-legacy-claim.md
- 目标测试：backend/tests/test_t63_legacy_claim.py（6 passed in 5.48s）
- 相关回归：38 passed in 37.11s
- 全量后端覆盖：278 passed + 258 passed = 536 passed，48 个测试文件，日志 .scratch/t63-backend-seg1.log 与 .scratch/t63-backend-seg2.log
- 迁移证据：.scratch/t63-migration/result.json（fresh_version 0007；0006 -> 0007 保留检查通过；两份迁移一致）
- 前端：33 个测试文件、206 个测试通过；vue-tsc exit 0；Vite build 812ms
- 真实 HTTP：.scratch/t63-claim-smoke/result.json；服务日志 .scratch/t63-claim-smoke/uvicorn.log
- 冒烟脚本：.scratch/t63-run-smoke.py、.scratch/t63-smoke-seed.py
- 代码提交：82c8de3977c95bb95cdc24d9846e9286d335fecd

真值说明：冒烟使用临时 SQLite、测试 v1 种子和本机 CLI；不连接真实供应商，也不代表维护者已选择或授权任何真实旧数据。正式环境必须由维护者明确确认来源、备份和项目集后执行。
