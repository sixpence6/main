# C0 维护备份与隔离恢复证据（#54）

- **Issue**：GitHub #54《V3 T02 [C0]：一致备份、维护协调与隔离恢复校验》
- **父规格**：GitHub #52
- **阶段**：C0
- **分支**：`ticket/54`
- **代码提交**：`f2bf2a9`（实现提交；本文档为后续文档提交）
- **基线**：`integration/v3` / `fdcf8d9`
- **记录日期**：2026-10-31（Asia/Shanghai）
- **状态**：**C0 合成数据实现证据已记录；不表示 v3 完成，也不表示真实数据已备份或迁移。**

## 1. 结论与边界

本票交付 `main/backend/app/maintenance/`：单来源选择、维护写入协调器、SQLite Online Backup 快照、数据库引用文件的允许列表复制、摘要/完整性清单、隔离目录校验、成功后 `latest.json` 指针，以及本地 CLI `inspect-sources` / `backup` / `verify` / `restore-verify`。

本记录只使用 `.scratch/spec-52/t54/evidence/` 下的临时 SQLite 与临时目录。没有读取、写入、迁移、认领、备份或恢复真实 `backend/data` 或 `main/backend/data`。没有发起任何外部模型请求。没有关闭 #54。

## 2. 交付与接口

| 项 | 位置 / 行为 |
|---|---|
| 维护协调器 | `main/backend/app/maintenance/coordinator.py`；业务写入、文件发布、过期删除三类通道；排空在途操作、拒绝新操作、可配置超时 |
| 应用写入边界 | `main/backend/app/db.py` 的 `write_transaction` 接入 `BUSINESS_WRITE` 守卫；`main/backend/app/services/assets.py` 文件发布使用 `file_publication_guard` |
| HTTP 维护门 | 应用在线时对 `/api/` 下的 POST/PUT/PATCH/DELETE 返回 503 `MAINTENANCE_ACTIVE`；没有新增匿名维护端点 |
| 单来源选择 | `sources.py`：只读检查候选；多个非空候选必须传 `--confirm-source-digest`，否则拒绝 |
| 一致备份 | `backup.py`：维护窗口内使用 `sqlite3.Connection.backup`；快照完成后把目标库设为 DELETE journal，移除目标侧 WAL/SHM |
| 文件与清单 | 只复制 `assets` 表引用的 `references/`、`generated/`、`published/` 文件；`manifest.json` + `manifest.sha256`，schema `maintenance-backup/v1` |
| 独立校验 | 暂存目录先在输出目录下的隔离临时目录中完整 `verify_backup`，通过后才原子改名发布；`latest.json` 只在成功后写 |
| 恢复演练 | `restore_verify` 要求空输出目录和 `--confirm-restore REHEARSE`，返回 `production_switched: false` |
| 秘密排除 | `.env`、`.env.*`、模型/TLS 私钥及常见密钥后缀；备份树中不出现这些文件 |

## 3. 来源选择证据

使用两个临时来源，验证"绝不自动合并"：

| 候选 | 内容摘要 | 结果 |
|---|---|---|
| `evidence/source` | `022b119dfa82619a1bc0c49645cef587c4386582d216f5eae14397941355dbfc` | 被选中；3 个允许文件，84 字节；`excluded_files=2`（`.env` 与 `references/private.key`） |
| `evidence/second-source` | `d8bed512fa73087631c0953c98f171c1abd1146b5a2bfd8c85bf1c5fab48f8a8` | 非空但未被选中 |

原始证据：`.scratch/spec-52/t54/evidence/inspect-conflict.json`。同一来源备份前后再次检查，内容摘要均为 `022b119d...`，且没有新增 `app.db-wal` / `app.db-shm`，见 `inspect-before.json`、`inspect-after.json`。

## 4. 一致性备份、清单与恢复证据

### 4.1 单来源备份与独立校验

来源为临时 `evidence/source`，输出为 `evidence/backups`。命令：

```powershell
cd D:\Desktop\新建文件夹\.worktrees\t54\main\backend
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m app.maintenance.cli backup ^
  --source D:\Desktop\新建文件夹\.scratch\spec-52\t54\evidence\source ^
  --output D:\Desktop\新建文件夹\.scratch\spec-52\t54\evidence\backups ^
  --candidate-root D:\Desktop\新建文件夹\.scratch\spec-52\t54\evidence\source ^
  --service-stopped --timeout 10 --json
```

结果：

| 项 | 值 |
|---|---|
| `backup_id` | `20261031T054126Z-3c78eadb` |
| `manifest_sha256` | `46e57ddf57b7aaf031759029965b2a9f5017482b123e88057cba996162987fcd` |
| `source_digest` | `022b119dfa82619a1bc0c49645cef587c4386582d216f5eae14397941355dbfc` |
| `file_count` / `total_bytes` | 3 / 84 |
| `verified` / `missing_files` | `true` / 0 |

备份树只有：`data/app.db`、`data/generated/gen.bin`、`data/published/pub.bin`、`data/references/ref.bin`、`manifest.json`、`manifest.sha256`。没有 `.env` 或私钥文件。

`verify` 结果：`ok: true`，`alembic_version=0002`，`asset_count=3`，checks 包含 `manifest_digest`、`file_sizes`、`file_digests`、`sqlite_integrity`、`foreign_keys`、`schema_version`、`asset_cross_check`。原始输出：`backup.json`、`verify.json`。

### 4.2 WAL 已提交状态的恢复验证

保留一个打开的 SQLite 写入连接，在 WAL 中提交 `payload = ('wal-proof','committed-in-wal')`，此时 `app.db-wal` 为 41,232 字节，然后运行同一 CLI 备份（`--service-stopped` 仅表示离线维护前提；该脚本是临时 SQLite 试验，不是在线应用跨进程协调）：

| 项 | 值 |
|---|---|
| `backup_id` | `20261031T054134Z-212685c6` |
| `manifest_sha256` | `afd137ca22da0883839ceb9078d48c67df9974f904f41f0c2ba8e99f1937518a` |
| 恢复后 `payload` | `[('wal-proof', 'committed-in-wal')]` |
| 恢复后 `asset_count` / `alembic_version` | 1 / `0002` |
| 恢复后 `journal_mode` | `delete`（快照自包含） |

原始输出：`wal-witness.log`、`restore-verify-wal.json`，查询脚本 `query_restore.py`。这证明已提交 WAL 状态不是仅停留在源侧 `-wal` 文件，而是进入了恢复后的数据库。

### 4.3 冲突来源拒绝

命令含两个 `--candidate-root` 且不带摘要：

```powershell
python -m app.maintenance.cli backup --source <source> --output <out> ^
  --candidate-root <source> --candidate-root <second-source> --service-stopped --json
```

结果：exit 1，`error.code=SOURCE_CHOICE_REQUIRED`，列出两个非空候选；错误信息要求维护者明确选择并传内容摘要。错误摘要：`backup-conflict-refused.json`。

传错误摘要：exit 1，`SOURCE_DIGEST_MISMATCH`。传正确摘要 `022b119d...`：备份成功，`verified: true`，证明授权是按摘要锁定单来源，而不是自动合并。原始输出：`backup-confirmed.json`。

### 4.4 损坏快照拒绝

复制一份已验证备份并篡改 `data/references/ref.bin` 后执行 `verify`：

```text
exit 1
{"ok": false, "error": {"code": "FILE_SIZE_MISMATCH", "message": "backup file size does not match the manifest: references/ref.bin"}}
```

原始输出：`verify-corrupted.json`。清单和文件哈希不匹配时不会把该快照当作可用备份。

### 4.5 失败保留上一份可用备份

1. 在 `evidence/failure-out` 成功创建 `backup-20261031T054146Z-38853553`，写 `latest.json`；
2. 删除来源中数据库引用的 `references/ref.bin`，再次备份；
3. 第二次备份 exit 1，`error.code=SOURCE_FILE_MISSING`，详情列出缺失的 `asset-1`；
4. `latest.json` 与第一次完全相同；输出目录只剩原 `backup-*` 和 `latest.json`，没有半发布暂存目录。

原始输出：`failure-initial-backup.json`、`backup-failure-missing-file.json`、`latest-before-failure.json`、`latest-after-failure.json`。

## 5. 测试与静态检查

| 检查 | 命令 | 结果 |
|---|---|---|
| 定向测试 | `cd backend`；`D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe tests/test_t54_maintenance_backup.py -q --disable-warnings` | **19 passed in 2.51s**，exit 0 |
| 完整后端套件 | `cd backend`；`D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings` | **445 passed in 358.35s (0:05:58)**，exit 0；原始日志 `evidence/full-backend-pytest.log` |
| Ruff | `python -m ruff check main/backend/app/maintenance main/backend/app/db.py main/backend/app/main.py main/backend/app/services/assets.py backend/tests/test_t54_maintenance_backup.py` | All checks passed |

定向测试覆盖：在途写者排空、维护期间新写者拒绝、文件发布租约排空/拒绝、超时释放门、超时无半发布备份、多来源拒绝与摘要授权、单来源 WAL 快照、恢复后 WAL 数据、清单/哈希/完整性、损坏快照拒绝、上一份 latest 保留、秘密/.env 排除、数据库引用缺失文件拒绝、FastAPI 503 与 `write_transaction` 守卫、CLI 全流程。

## 6. 验收编号边界

| 编号 | 本票已证明 | 仍未完成（不计通过） |
|---|---|---|
| **V3-AC-21** | 合成来源中的 `.env` 和 `references/private.key` 被排除；备份树无秘密文件 | 跨响应/前端包/日志/文件名的完整秘密扫描与真实密钥环境 |
| **V3-AC-33** | 单来源选择、只读候选报告、冲突拒绝、摘要授权、临时恢复 | 真实 `backend/data` 与 `main/backend/data` 的选择、认领、幂等迁移与真实回退点 |
| **V3-AC-36** | WAL 已提交状态的一致快照、清单/哈希/交叉校验、失败保留上一份可用备份、无 Web 全库导出 | 至少每 24 小时周期调度、启动补跑、最近 7 份轮换（#81），不同磁盘部署证据 |
| **V3-AC-37** | 损坏快照拒绝；隔离恢复演练成功且不切换生产 | 生产恢复切换、会话/一次性凭据撤销、审批到期/ledger gap、恢复后的完整关联检查（#82） |

## 7. 明确限制与后续票

1. **CLI 是离线维护工具**：`backup` 要求 `--service-stopped`；没有跨进程在线锁，也没有声称能与正在运行的应用进程做在线一致备份。在线写入协调器是应用进程内的可复用门，测试覆盖其排空/拒绝/超时行为；本票不注册匿名维护 HTTP 端点。
2. **没有周期调度**：不自动每日运行、不启动补跑、不自动轮换 7 份快照；属于 #81。
3. **没有生产恢复开关**：`restore-verify` 只把备份复制到空隔离目录并校验，`production_switched` 恒为 `false`；生产恢复、会话撤销、审批到期与 ledger gap 属于 #82。
4. **同盘备份不防整盘损坏**：运维文档要求受限输出目录并优先使用另一块磁盘。
5. **真实数据未动**：本记录所有来源、输出、恢复目录均在 `.scratch/spec-52/t54/` 下；真实候选仍待维护者选择。

## 8. 证据索引

原始证据位于 `.scratch/spec-52/t54/evidence/`（被忽略，不随提交分发）：

- `inspect-before.json`、`inspect-after.json`、`inspect-conflict.json`
- `backup.json`、`verify.json`、`restore-verify.json`
- `wal-witness.log`、`restore-verify-wal.json`、`query_restore.py`
- `backup-conflict-refused.json`、`backup-confirmed.json`
- `verify-corrupted.json`
- `failure-initial-backup.json`、`backup-failure-missing-file.json`、`latest-before-failure.json`、`latest-after-failure.json`
- `full-backend-pytest.log`

复现时必须使用临时数据根与临时 SQLite，不得把 `--source` 或 `--output` 指向 `backend/data`、`main/backend/data` 或任何真实数据。
