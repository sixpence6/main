# Release T31 保留期清理与跨发布物秘密隔离验收记录

- Issue: #83
- 父 Issue: #52
- 分支: ticket/83
- 基线: integration/v3 4306959
- 代码提交: d21e80ebe7458c76336f68532bf0003e4a9315d2
- 验收日期: 2026-11-01
- 状态: 通过（Release T31 阶段条件；正式发布由 #85 Release-GATE 决定）

## 1. 结论与边界

本票在既有保留期清理上补齐安全审计 90 天窗口，并把“候选文件已按保留期删除、数据库行仍需保留”的事实纳入备份兼容校验；同时新增只读秘密扫描工具，对响应、前端构建、业务数据库、日志、文件名、模型请求、发布包和备份做合成秘密检查。清理只删除登记在 `generated/` 下、已过 24 小时且无发布/引用/审批/恢复依赖的候选图片文件，不删除已发布图片、未认领 legacy 资源或审批/确认/恢复证据；数据库行保留并标记 `review_note=retention_expired`，不声称物理安全擦除。

1. 终态未发布候选图片保留 24 小时；普通诊断事件保留 30 天；安全审计事件保留 90 天。`RetentionPolicy.audit_ttl=AUDIT_TTL=timedelta(days=90)`，`RetentionReport` 新增 `scanned_audits` 与 `deleted_audit_ids`。
2. 候选清理继续复用既有保护规则：已发布/已批准资源、未认领 legacy 资源、非 generated 类型、Delivery/Generation/Project 引用、重复路径、LegacyItem、受保护或非终态 GenerationJob、活跃 Approval 的 checks_json 均阻止删除；只有满足终态、无依赖、路径安全、摘要与尺寸核验的文件才会被 unlink。
3. 诊断清理不再跳过 `retention_*` 事件；普通诊断满 30 天后可删除，恢复/确认/风险/引用变更等受保护事件继续保留，避免清理自身制造无界诊断。
4. 新增 `_clean_audits`：只删除超过 90 天的 AuditEvent；逐行在 write_transaction 中删除，某行失败时记录 `RetentionFailure(object_kind="audit", stage="audit", error_code="AUDIT_DELETE_FAILED")` 与 `retention_cleanup_failed`，保留该行并继续，不扩大删除范围。
5. 删除候选文件成功后，在独立 write_transaction 中把 Asset.review_note 标记为 `retention_expired`；数据库行与历史仍可查询，客户端不会把缺失文件误判为普通服务错误。
6. 备份兼容：`_snapshot_database` 在 assets 表存在 review_note 时读取该列，旧测试 schema 没有该列时回退 NULL；`_copy_asset_files` 只对 `kind=generated` 且 review_note 恰为 `retention_expired` 的缺失文件写入 `missing` 条目和 reason，其他缺失仍抛 `SOURCE_FILE_MISSING`。
7. `verify_backup` 对 manifest.missing 只接受 reason=retention_expired 且 kind=generated 的条目；其他 missing 抛 `BACKUP_INCOMPLETE`，present/missing 冲突仍抛 `MANIFEST_MISSING_CONFLICT`。因此保留期删除后的备份可验证，未知悬挂引用仍被阻断。
8. 清理与发布/备份共用 MaintenanceCoordinator：清理持有 `EXPIRY_DELETION` 租约，文件发布使用 `FILE_PUBLICATION`，备份使用完整 maintenance 窗口；竞争时等待或跳过，不产生半快照或悬挂引用。
9. 新增只读 `tools/secret_scan.py`：不打印命中值；扫描根目录、单个文件和 zip，识别 private-key、OpenAI `sk-`、AWS access key、GitHub token，并支持人工 marker；退出码 1 表示命中。没有新增 HTTP 路由或迁移，head 仍为 0010。
10. 用完整 App、真实临时 SQLite、fake OpenAI-compatible provider 完成了真实 HTTP 受理到发布/下载、重启清理、备份/独立校验和秘密扫描；日志未出现商品正文或 messages 数组，数据库/备份未出现模型或会话密钥。未触碰 `backend/data` 或 `main/backend/data`。本票不替代 #84 的真实数据声明或 #85 最终发布关口；#51 保持打开。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t83 |
| 分支 | ticket/83 |
| 基线 | integration/v3 4306959 |
| 操作系统 | Windows；Python 3.12.13；Node v26.12.1；npm 11.20.0 |
| 前端 | Vite 8.3.5；Vitest 5.0.5；Vue 3.5 |
| 后端测试解释器 | D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe |
| 数据库 | 临时 SQLite；alembic 0010；26 张表，integrity ok |
| 模型 | 本地 fake OpenAI-compatible provider；无真实供应商调用 |
| 数据根 | 仅 .scratch/t83-retention 临时目录；未触碰 backend/data、main/backend/data |

## 3. 实现摘要

### 3.1 保留期与审计清理

- `main/backend/app/services/retention.py` 新增 `AUDIT_TTL=timedelta(days=90)`、`RETENTION_EXPIRED_NOTE="retention_expired"`；`RetentionPolicy.audit_ttl` 可注入，默认 90 天；`RetentionReport.scanned_audits`、`deleted_audit_ids`。
- `_clean_audits` 先只读扫描 AuditEvent，再逐行事务删除；失败保留行并写失败报告与诊断事件。
- `_clean_diagnostics` 移除 `retention_` 前缀豁免，仍保留 `PROTECTED_DIAGNOSTIC_EVENTS`；候选文件删除成功后在独立事务中写 `Asset.review_note=retention_expired`。
- 没有新增删除接口、网页导出接口或物理擦除接口。

### 3.2 备份兼容与完整性

- `main/backend/app/maintenance/backup.py` 定义 `RETENTION_EXPIRED_NOTE`，快照查询按 assets 表实际列选择 `review_note` 或 NULL，向后兼容 0002 测试 schema。
- `_copy_asset_files` 只接受 generated + retention_expired 的缺失条目；未知缺失继续 `SOURCE_FILE_MISSING`。
- `main/backend/app/maintenance/manifest.py` 逐条校验 missing reason/kind，非法条目 `BACKUP_INCOMPLETE`；数据库 assets 交叉检查同样要求 reason=retention_expired。

### 3.3 秘密扫描工具

- `tools/secret_scan.py` 默认模式：private-key、`sk-`、AKIA、GitHub token；跳过 .git/.venv/node_modules/__pycache__/.pytest_cache/.ruff_cache/.scratch。
- `--marker` 可注入合成秘密；输出只给 path/kind/marker index，不给值；命中 exit 1，干净 exit 0。

### 3.4 测试

- 新增 `backend/tests/test_t83_retention_secrets.py` 7 个测试：90/30 天边界、备份与清理租约竞争、秘密扫描与 zip、响应/数据库/文件名/备份无秘密、审计删除失败保留行、CLI 不回显、保留期缺失文件可备份但未知缺失被拒绝。
- 复用 `test_t54_maintenance_backup.py` 与 `test_t81_backup_schedule.py`，三文件共 36 个测试通过。

## 4. 命令与结果

### 4.1 针对性后端测试

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest tests/test_t54_maintenance_backup.py tests/test_t81_backup_schedule.py tests/test_t83_retention_secrets.py -q
~~~

结果：36 passed in 10.90s，exit 0。日志 `.scratch/t83-evidence/backend-targeted.log`。

### 4.2 Ruff

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m ruff check main/backend/app/services/retention.py main/backend/app/maintenance/backup.py main/backend/app/maintenance/manifest.py tools/secret_scan.py backend/tests/test_t83_retention_secrets.py
~~~

结果：All checks passed!，exit 0。

### 4.3 全量前端回归与构建

~~~powershell
cd D:/Desktop/新建文件夹/.worktrees/t83
D:/nodejs/node.exe D:/nodejs/node_modules/npm/bin/npm-cli.js test -- --run --reporter=dot
D:/nodejs/node.exe D:/nodejs/node_modules/npm/bin/npm-cli.js run typecheck
D:/nodejs/node.exe D:/nodejs/node_modules/npm/bin/npm-cli.js run build
~~~

结果：38 个测试文件 / 173 个测试通过，27.57s，exit 0；typecheck exit 0；Vite 8.3.5，1,664 modules，871ms，main/dist/index.html 0.47 kB、CSS index-B2Fixx5N.css 430.37 kB、JS index-CFEBsUU2.js 1,159.11 kB，仅有 chunk 体积警告。日志 `.scratch/t83-evidence/frontend.log`、`typecheck.log`、`build.log`。

### 4.4 全量后端回归

~~~powershell
cd D:/Desktop/新建文件夹/.worktrees/t83/backend
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest [non-v2 文件清单] -q
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest [test_v2_*.py 文件清单] -q
~~~

结果：非 v2 三批 170 + 137 + 82 = 389 passed；v2 19 文件 235 passed；合计 624 passed，无 failed/skipped/unknown。只有 `backend/tests/support/auth.py:42` 的既有 cookie DeprecationWarning。日志 `.scratch/t83-evidence/backend-nonv2-1.log`、`backend-nonv2-2.log`、`backend-nonv2-3.log`、`backend-v2.log`。

### 4.5 Release 针对性回归

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest tests/test_t78_production_entry.py tests/test_t79_release_package.py tests/test_t80_https_lan.py tests/test_t81_backup_schedule.py tests/test_t82_restore.py tests/test_t83_retention_secrets.py -q
~~~

结果：35 passed in 19.27s，exit 0。日志 `.scratch/t83-evidence/release-targeted.log`。

### 4.6 完整 App 真实 HTTP 冒烟

~~~powershell
D:/nodejs/node.exe D:/Desktop/新建文件夹/.scratch/t83-retention/run_retention.mjs
~~~

结果：exit 0，`result.json` 的 ok=true，appPort 10349 / fakePort 10348。临时 SQLite 已迁移至 0010，fake provider 仅本地；账号 retention-user@example.com，项目 29e8a571-bee3-4766-a9b8-ea344997421c，request a263f2c4-4304-4942-8c49-566a12325a6f，阶段 S10 completed，交付 asset 5f05f31d-4b1a-455d-92ab-e9a8d8c67434，文件 delivery-5f05f31d-r1.png，下载 29,373 bytes、image/png，usage 20/1/19；fake stats chat=6、images=1，role 顺序为 generation_engineer, output_reviewer, quality_compliance, output_reviewer, delivery_manager, output_reviewer。

重启清理核对：候选文件已删除但 Generation 行存在；legacy 文件存在；旧诊断删除、受保护诊断保留、29 天诊断保留；120 天审计删除、89 天审计保留；已发布文件存在，项目 GET 200，下载字节数 29,373。

备份：backup `20261101T082619Z-2abc1c37`，manifest_sha256 `851172da1f71b73dfeddc2b9b9a5beb1cdacf0b107d5d06fe82a8168b37820e3`，file_count 2、missing_files 1（仅 retention_expired）、total_bytes 58,746、verified true；独立 verify_backup 通过 manifest_digest/file_sizes/file_digests/sqlite_integrity/foreign_keys/schema_version/asset_cross_check，asset_count 3、alembic 0010。

### 4.7 秘密扫描与日志边界

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe tools/secret_scan.py --root D:/Desktop/新建文件夹/.scratch/t83-retention/backups --root D:/Desktop/新建文件夹/.worktrees/t83/main/dist --root D:/Desktop/新建文件夹/.scratch/t83-retention/scan-logs --marker T83-CLI-SECRET-92d4c7b1 --json
~~~

结果：exit 0，hits=[]，备份/前端构建/日志均无合成秘密。数据目录扫描 exit 1，唯一命中为隔离夹具中的 `.env`（标记 marker，属受保护配置文件，不进入备份）；backupHasSecrets=false。对 `.scratch/t83-retention/data` 做无 marker 通用扫描 exit 0，说明数据库中的 SHA256 等摘要未误报为明文秘密。发布包与 zip（t79 最终产物）扫描 exit 0，hits=[]。

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe tools/secret_scan.py --root D:/Desktop/新建文件夹/.worktrees/t79/.scratch/t79-release2-final/ecommerce-image-workspace-0.1.0 --zip D:/Desktop/新建文件夹/.worktrees/t79/.scratch/t79-release2-final/ecommerce-image-workspace-0.1.0.zip --json
~~~

结果：exit 0，hits=[]；日志 server-a/server-b/fake 与 smoke/check JSON 不含商品正文 `磨砂玻璃咖啡杯` 或 messages 数组。

## 5. V3-AC 覆盖

| AC | 本票证据 |
| --- | --- |
| V3-AC-18 | 保留期/备份不绕过内容门；发布文件保留、未认领 legacy 保留、已发布资源可下载 |
| V3-AC-20 | 唯一生产入口冒烟；无新增路由/迁移；发布包无测试和秘密 |
| V3-AC-21 | 响应/前端构建/DB/日志/文件名/模型请求/发布包/备份秘密扫描；摘要不误报 |
| V3-AC-34 | 90 天安全审计清理；删除失败保留行并记录脱敏失败；无审计导出接口 |
| V3-AC-35 | 24h 候选、30d 诊断、90d 审计；时间推进+重启验证；发布/审批/恢复依赖保留 |
| V3-AC-36 | 清理与发布/备份租约竞争；未知缺失仍阻断备份；无物理擦除声明 |

阶段条件均已验证；最终 40 项 V3-AC 汇总由 #85 Release-GATE 负责。

## 6. 证据文件

- `.scratch/t83-retention/result.json`、`backupPath`、`backup-verify`、`server-a.log`、`server-b.log`、`fake.log`。
- `.scratch/t83-evidence/backend-targeted.log`、`backend-nonv2-1..3.log`、`backend-v2.log`、`release-targeted.log`、`frontend.log`、`typecheck.log`、`build.log`、`retention-smoke.log`。
- 代码：`main/backend/app/services/retention.py`、`main/backend/app/maintenance/backup.py`、`main/backend/app/maintenance/manifest.py`、`tools/secret_scan.py`、`backend/tests/test_t83_retention_secrets.py`。

## 7. 残余风险

1. 冒烟使用本地 fake OpenAI-compatible provider 和临时 SQLite；未验证真实厂商、真实企业网络或真实数据根。
2. 未实现物理安全擦除；本票只保证业务文件删除和账本行保留。
3. 日志检查覆盖本次冒烟日志；生产日志轮转/外部采集不在本票范围。
4. 备份兼容仅为 retention_expired 的 generated 缺失；其他历史未知缺失仍按完整性失败处理。
5. 当前发布包由 #79 产物扫描；#83 的代码变更未重建最终 ZIP，最终打包由后续发布关口执行。
6. `.env` 是隔离冒烟夹具中的受保护配置；真实部署仍须限制目录权限并禁止进入发布包/备份。
7. #51 保持打开，正式发布由 #85 决定。
