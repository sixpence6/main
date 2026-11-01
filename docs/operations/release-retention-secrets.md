# Release T31 保留期清理与跨发布物秘密隔离运维说明

- Issue: #83；父 Issue: #52；分支: ticket/83；基线: integration/v3 4306959
- 适用：本地或受控单机部署。正式发布由 #85 Release-GATE 决定；本文件不替代备份、恢复或 HTTPS 运维文档。

## 1. 目的与边界

运行期和停机后启动的保留期清理按类型删除到期内容：终态未发布候选图片 24 小时、普通诊断 30 天、安全审计 90 天。清理不删除已发布图片、未认领 legacy 资源、审批/确认/风险/恢复证据，也不删除数据库中的资产/生成行；删除候选文件后把资产标记为 `retention_expired`，不声称物理安全擦除。

秘密隔离的目标是让模型密钥、会话令牌、一次性链接、TLS 私钥不通过响应、前端 bundle、业务数据库、日志、文件名、模型请求、发布包或备份泄露。必要哈希/SHA256 摘要属于可公开校验值，不是明文秘密。

## 2. 保留期矩阵

| 对象 | 保留期 | 处理 | 保护条件 |
| --- | --- | --- | --- |
| 终态未发布候选图片 | 24 小时 | 删除 generated 文件；Asset.review_note=retention_expired；保留 Generation/Asset 行 | 已发布/已批准、Delivery/Generation/Project 引用、LegacyItem、活跃审批、受保护/非终态 Job 等 |
| 普通诊断事件 | 30 天 | 删除 DiagnosticEvent | `PROTECTED_DIAGNOSTIC_EVENTS` 中的恢复/确认/风险/引用变更事件 |
| 安全审计事件 | 90 天 | 删除 AuditEvent；失败保留并记录 | 无产品删除/编辑接口；审计失败须可追踪 |
| 已发布图片 | 不按本策略删除 | 保留文件与账本 | 发布/交付/审批证据 |
| 未认领 legacy 资源 | 不按本策略删除 | 保留文件与账本 | 待 #84 受控认领 |

## 3. 清理与竞争

1. 启动时 main.py 在 lifespan 中执行一次 `run_retention_cleanup`，之后每 `RETENTION_INTERVAL_SECONDS=900` 秒执行；清理窗口使用 `MaintenanceCoordinator` 的 `EXPIRY_DELETION` 租约。
2. 文件发布使用 `FILE_PUBLICATION` 租约，备份使用完整 maintenance 窗口；同一时刻只允许一个维护/发布阶段，超时会跳过本轮而不是制造半快照。
3. 清理先扫描终态 Generation；`_project_dependencies`/引用检查不通过则跳过。路径必须是 data 根下 `generated/` 的相对路径，拒绝绝对路径、`..`、symlink/Windows reparse、未知文件；大小与摘要不符则记录失败并保留。
4. 候选文件成功删除后，独立事务把 Asset.review_note 设为 `retention_expired`；如果该事务失败，报告失败，文件已删除但行仍保留，后续人工核对。
5. 诊断/审计删除逐行事务；单行失败记录 `RetentionFailure` 和 `retention_cleanup_failed` 诊断，不删除其他有效行，也不把失败伪装成成功。
6. 时间推进、重启和失败不会产生悬挂引用：清理由数据库引用检查保护；备份只接受显式 `retention_expired` 的 generated 缺失。

## 4. 与备份/恢复的关系

- 备份 `manifest.missing` 中，只有 `kind=generated` 且 `reason=retention_expired` 的条目允许缺失；未知缺失仍使备份失败（`SOURCE_FILE_MISSING` / `BACKUP_INCOMPLETE`）。
- 备份的 SQLite 快照读取 `assets.review_note`（若存在）；旧 schema 没有该列时使用 NULL，兼容历史测试/旧库。
- 备份仍校验数据库摘要、alembic 版本、表行数、文件 SHA256 和 assets 交叉引用；不要为绕过失败手工修改 manifest。
- 恢复演练/切换见 `release-restore-recovery.md`；本票不改变恢复 gate 和账本 floor。

## 5. 秘密扫描

只读工具 `tools/secret_scan.py` 不打印命中值：

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe tools/secret_scan.py --root ./main/dist --root ./backend/data --root ./logs --json
~~~

- `--marker VALUE` 注入合成秘密，用于发布前自检；命中输出只有 path/kind/marker 序号。
- `--zip FILE` 同时扫描 zip 内文件。
- 默认跳过 .git/.venv/node_modules/__pycache__/.pytest_cache/.ruff_cache/.scratch，避免开发缓存误报。
- 退出码 0=干净，1=命中；脚本不会读取或输出秘密明文。

## 6. 常用命令

~~~powershell
cd D:/Desktop/新建文件夹/.worktrees/t83
D:/nodejs/node.exe D:/nodejs/node_modules/npm/bin/npm-cli.js test -- --run --reporter=dot
D:/nodejs/node.exe D:/nodejs/node_modules/npm/bin/npm-cli.js run typecheck
D:/nodejs/node.exe D:/nodejs/node_modules/npm/bin/npm-cli.js run build
~~~

后端完整回归（工作树 backend）：

~~~powershell
cd D:/Desktop/新建文件夹/.worktrees/t83/backend
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest tests/test_t54_maintenance_backup.py tests/test_t81_backup_schedule.py tests/test_t83_retention_secrets.py -q
~~~

完整 App 冒烟：

~~~powershell
D:/nodejs/node.exe D:/Desktop/新建文件夹/.scratch/t83-retention/run_retention.mjs
~~~

## 7. 故障处理

| 现象 | 检查 | 处理 |
| --- | --- | --- |
| 候选文件未删除 | Generation 状态、审批/Delivery 引用、路径是否 generated、摘要/尺寸 | 确认依赖已解除；不要手工删除数据库行或绕过引用检查 |
| 文件已删除但界面仍显示 | Asset.review_note 是否为 retention_expired、项目详情/历史接口 | 保留账本行；按 U3 内容门显示真实不可用，不伪造成功 |
| 备份报 SOURCE_FILE_MISSING | 缺失文件是否 generated、review_note 是否 retention_expired | 只有保留期清理的明确缺失可接受；其他缺失先修复源文件再备份 |
| 备份报 BACKUP_INCOMPLETE/MANIFEST_MISSING_CONFLICT | manifest.missing reason/kind、重复路径、present 是否冲突 | 不修改 manifest 掩盖；重新生成备份或人工核对 |
| 清理未运行 | 服务是否启动、coordinator 是否被备份/发布占用、日志 retention_cleanup_failed | 等待下一周期或手工重启；检查租约竞争，不要强制删除 |
| 秘密扫描命中 | path/kind/marker 定位（不输出值） | 从构建/日志/配置移除；轮换真实密钥；不要把命中值贴到票据 |

## 8. 安全、数据与回滚

- 数据根必须由显式 `APP_DATA_DIR` 指定；不得把 `backend/data` 或 `main/backend/data` 作为测试/清理对象。
- 备份输出必须在 `APP_DATA_DIR` 与 `WEB_DIST_DIR` 之外，限制本机权限；`.env`、模型密钥、TLS 私钥不得进入备份或发布包。
- 清理只 unlink 已登记文件并保留账本行；没有物理擦除承诺。需要回滚时使用最近一次已验证备份和 `release-restore-recovery.md` 的停机流程，不要直接覆盖数据库。
- 本票没有新增 HTTP 清理/导出路由；不要通过网页触发清理。
- 正式发布前仍须完成 #84 的真实数据源声明和 #85 的发布关口；#51 保持打开。

## 9. 相关文档

- `docs/acceptance/release-retention-secrets.md`（本票验收记录）
- `docs/operations/release-backup-rotation.md`
- `docs/operations/release-restore-recovery.md`
- `docs/architecture/hld_v3.md`、`docs/architecture/v3-cleanup.md`
