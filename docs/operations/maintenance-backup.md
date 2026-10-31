# 维护备份与隔离校验操作说明

- **适用票号**：GitHub #54（V3 T02 [C0]，父规格 #52）
- **模块**：`main/backend/app/maintenance/`
- **备份清单格式**：`maintenance-backup/v1`
- **状态**：本地维护员 CLI 与可复用写入协调器；**没有** Web 全库导出、周期调度或生产恢复开关。

## 1. 适用范围与硬边界

本工具只备份维护者**明确指定的一份数据源**。它不会扫描后自动合并、认领或挑一份"看起来更全"的目录。若有多个非空候选，必须先 `inspect-sources` 查看候选及内容摘要，再用 `--source` 指定唯一来源；当其他候选非空时，还必须传入所选来源的 `--confirm-source-digest`。

备份内容只允许：

- 数据根下的 SQLite 主库 `app.db`（即使存在已提交 WAL，也通过 SQLite Online Backup API 生成自包含快照）；
- 数据库 `assets.relative_path` 引用的 `references/`、`generated/`、`published/` 下不可变文件。

不会复制 `.env`、`.env.*`、模型密钥、TLS 私钥、`*.pem`、`*.key`、`*.p12`、`*.pfx`、`*.jks`、`*.keystore`、`*.kdbx` 或其他任意文件。数据库引用到缺失文件时备份失败，不发布不完整备份。

输出目录不得位于所选来源内，也不得覆盖仓库真实数据根（`backend/data`、`main/backend/data`）。建议将 `--output` 放在另一块磁盘的受限目录；同盘备份不能防止整盘损坏。

## 2. 环境

- Windows（本票证据在 Windows 10 上采集），Python 3.12。
- 开发仓库使用 `backend/.venv`；部署包使用其随包虚拟环境。以下以开发仓库为例：
  ```powershell
  cd <repo>\main\backend
  ..\..\backend\.venv\Scripts\python.exe -m app.maintenance.cli --help
  ```
- CLI 只做本地文件与 SQLite 操作，不访问网络、不调用模型。
- **CLI 备份要求应用进程已停止**。`backup` 必须显式带 `--service-stopped`；否则返回 `SERVICE_MUST_BE_STOPPED`。这是离线维护流程，不是跨进程在线备份锁。应用在线时的写入协调由 `app.maintenance.coordinator` 在进程内完成（测试覆盖），CLI 不复用该进程内锁。

## 3. 维护步骤

### 3.1 只读检查候选来源

```powershell
..\..\backend\.venv\Scripts\python.exe -m app.maintenance.cli inspect-sources ^
  --candidate-root <data-root-a> ^
  --candidate-root <data-root-b> ^
  --json
```

输出 schema 为 `maintenance-sources/v1`，每个候选包含 `root`、`app_db_present`、`content_digest`、`file_count`、`excluded_files`、`unsafe_files` 与文件 SHA-256 列表。该命令只读：不打开 SQLite、不创建文件、不修改候选。

- 只有一个非空候选：可以直接进入备份。
- 多个非空候选：记录所选候选的 `content_digest`，在备份命令中传入 `--confirm-source-digest`。未传时返回 `SOURCE_CHOICE_REQUIRED`；摘要不匹配返回 `SOURCE_DIGEST_MISMATCH`。绝不自动合并。

### 3.2 停止服务并创建备份

先停止应用进程，确认没有进程继续写数据根，然后：

```powershell
..\..\backend\.venv\Scripts\python.exe -m app.maintenance.cli backup ^
  --source <data-root> ^
  --output <backup-root> ^
  --service-stopped ^
  --timeout 30 ^
  --candidate-root <data-root> ^
  --json
```

如果维护者已经通过检查确认存在多个非空候选，追加：

```powershell
  --confirm-source-digest <所选候选的 content_digest>
```

成功输出至少包含 `ok: true`、`verified: true`、`backup_id`、`backup_dir`、`manifest_sha256`、`source_digest`、`file_count`、`total_bytes`、`missing_files: 0`。备份目录结构：

```text
<backup-root>/
  backup-<UTC时间>-<随机后缀>/
    data/app.db
    data/references/...
    data/generated/...
    data/published/...
    manifest.json
    manifest.sha256
  latest.json
```

`manifest.json` 记录数据库摘要与 integrity 结果、schema 版本、每个文件的大小与 SHA-256、缺失文件列表、来源内容摘要及边界声明；`manifest.sha256` 固定清单本身。只有暂存目录在隔离目录中独立校验通过后，才会原子改名为 `backup-*`，再写 `latest.json`。失败时旧 `latest.json` 和旧可用备份保持不变，暂存目录被清理。

### 3.3 独立校验备份

```powershell
..\..\backend\.venv\Scripts\python.exe -m app.maintenance.cli verify ^
  --backup <backup-root>\backup-<...> ^
  --json
```

成功结果包含 `ok: true`、`checks`（清单摘要、文件大小/摘要、SQLite integrity、外键、schema 版本、资产交叉检查）、`database_sha256`、`manifest_sha256`、`asset_count`。任何缺失、哈希不符、SQLite 损坏、资产行与文件不一致都会以非零退出并返回 `ok: false`。

### 3.4 隔离恢复演练

恢复演练只写入一个空的隔离目录，绝不切换生产数据根，也不修改备份本身：

```powershell
..\..\backend\.venv\Scripts\python.exe -m app.maintenance.cli restore-verify ^
  --backup <backup-root>\backup-<...> ^
  --output <空目录> ^
  --confirm-restore REHEARSE ^
  --json
```

成功结果必须满足 `verified: true`、`production_switched: false`。未写 `REHEARSE` 时返回 `RESTORE_CONFIRMATION_REQUIRED`；输出目录非空时返回 `RESTORE_OUTPUT_NOT_EMPTY`。

## 4. 证据预期与失败处理

一次维护备份至少保留以下证据：

1. `inspect-sources` 的 JSON：候选、所选来源摘要、被排除文件计数；
2. `backup` 的 JSON：`backup_id`、`manifest_sha256`、`source_digest`、`verified: true`；
3. `verify` 的 JSON：完整 checks、SQLite integrity、资产交叉检查；
4. `restore-verify` 的 JSON：隔离目录、`verified: true`、`production_switched: false`；
5. 备份目录文件清单，确认只有 `data/app.db`、数据库引用的允许目录文件、两个清单文件，没有 `.env`、私钥或任意未引用文件。

失败时：

- 命令返回非零并打印 `{"ok": false, "error": {"code": ..., "message": ...}}`；
- 不覆盖来源数据根；
- 不覆盖上一份可用备份或旧 `latest.json`；
- 不留下已发布的半成品备份；检查输出目录不应出现新的 `backup-*`，暂存目录应已清理。

## 5. 明确不在本票范围

- **#81**：每日/每 24 小时周期调度、启动补跑、保留最近 7 份已验证快照的轮换。本票只提供手动 CLI 与 `latest.json` 指针，不自动删除旧备份。
- **#82**：生产恢复切换、恢复时会话/一次性凭据撤销、审批到期与 ledger gap 处理。本票的 `restore-verify` 只做隔离演练，永远不切换生产。
- 真实 `backend/data`、`main/backend/data` 的选择、认领与迁移；本票没有读取、写入或改动这两个真实数据候选。
- Web 端整库下载或匿名维护端点。本模块不注册任何路由。
