# Release T30 受控恢复、凭证撤销与账本缺口运行手册

本文说明如何在本机维护窗口内，从已验证的周期备份恢复完整数据根，处理恢复后的会话/链接撤销、审批与交付不复活，以及账本缺口闸门的核对与关闭。它只覆盖 #82；保留期/秘密收口、真实数据迁移和最终发布分别属于 #83、#84、#85。

## 1. 目标与边界

- 恢复只允许本机维护 CLI，不提供网页整库导入或恢复路由。
- 必须先停止服务、验证备份、保存当前安全备份，再在隔离副本中检查后原子切换。
- 恢复后所有既有会话和未使用的一次性链接失效；旧密码和停用状态不能复活。
- 已撤销的审批、交付和被拒绝的资源在恢复后仍不可用；恢复不会重新批准历史内容。
- 当前用量与活动槽位高于备份时，差额保留为闸门 floors；新生成受理被阻断，直到窗口结束或审计核对后清除。
- 不读取、复制或修改 .env、模型密钥、TLS 私钥；恢复不替代秘密配置。

## 2. 前置条件

- 服务已停止：CLI 要求 `--service-stopped`，并拒绝在运行中的服务上执行。
- 目标备份已完成 `verify`，manifest 摘要和 SQLite 完整性通过。
- 目标备份的 alembic_version 与当前代码 head 一致（当前 0010）；旧 schema 备份不能直接切到生产。
- 当前数据根是显式配置的 APP_DATA_DIR，且不是内建默认 real data root；真实数据根必须额外使用 `--allow-real-data-root` 并由维护者书面授权。
- 备份目录和输出目录位于数据根之外，且磁盘有足够空间保存 safety backup。

## 3. 恢复前检查

列出候选源和备份：

~~~bash
python -m app.maintenance.cli inspect-sources --json
python -m app.maintenance.cli verify --backup /path/to/backups/backup-<utc>-<hex>
~~~

确认备份包含完整账本与资源文件清单；不要在未验证的目录上执行恢复。

## 4. 执行恢复

~~~bash
python -m app.maintenance.cli restore \
  --source /path/to/backend/data \
  --backup /path/to/backups/backup-<utc>-<hex> \
  --output /path/to/backups \
  --confirm-restore RESTORE \
  --service-stopped \
  --json
~~~

真实数据根必须由维护者明确授权后才可加：

~~~bash
  --allow-real-data-root
~~~

执行顺序：

1. 校验目标备份与 manifest 摘要、schema 版本。
2. 为当前源创建 safety backup，并校验成功。
3. 复制目标备份到隔离 staging，二次校验。
4. 校验 SQLite、owner、审批 artifact、交付 asset/job 和资源摘要。
5. 读取当前凭证/审批/交付状态，计算 usage/slot floors，执行会话/链接撤销与安全下限写回。
6. 写入 `restore-gate.json` 和 maintenance_restore_completed 审计。
7. 两次原子 os.replace 切换数据根；失败时恢复原目录。

输出关键字段：backup_id、restored_dir、safety_backup_id、safety_backup_dir、manifest_sha256、file_count、total_bytes、sessions_revoked、links_revoked、approvals_revoked、deliveries_revoked、assets_guarded、usage_floor_accounts、gap_expires_at、verified、production_switched。

## 5. 恢复后的状态

- 所有恢复前会话被撤销；重新登录需要新密码或既有当前密码。
- 旧备份中的密码哈希不会覆盖当前密码；旧备份中的启用状态不会覆盖当前停用状态。
- 未使用的一次性链接被撤销，保留原过期时间；已使用链接不被改写。
- 已撤销审批、已撤销交付和被拒绝资源仍保持撤销/拒绝；下载和发布门禁继续生效。
- `restore-gate.json` 状态 open，`gap_expires_at` 默认恢复后 24 小时；缺口期间新受理返回 409 RESTORE_GAP_BLOCKED，同 request_id 重放仍返回原意图。
- usage floors 在当前 24 小时窗口内计入 used；slot floors 在 gate open 期间计入 personal/instance/project 活动槽位。

## 6. 核对与关闭缺口

先核对恢复后的账号、项目 owner、审批、交付、资源引用、用量和槽位；确认无缺口后执行：

~~~bash
python -m app.maintenance.cli clear-restore-gap \
  --source /path/to/backend/data \
  --confirm-clear CLEAR_RESTORE_GAP \
  --reason "已核对账号、项目、审批、交付与用量账本" \
  --service-stopped \
  --json
~~~

结果 status=cleared、cleared_by=local_maintainer，并写 maintenance_restore_gap_cleared 审计。重复执行返回 already_cleared=true。

清除后重新启动服务、登录并验证：旧密码失败、新密码成功；已撤销交付不可下载；资源门禁仍为 409；新受理可以创建 S07 且 usage 至少包含 floor 计数。

## 7. 故障处理

| 现象 | 处理 |
| --- | --- |
| RESTORE_CONFIRMATION_REQUIRED | 必须传 `--confirm-restore RESTORE`，不要用其它字符串。 |
| SERVICE_MUST_BE_STOPPED | 停止完整 App 和所有后台任务后重试；不要用网页操作替代。 |
| BACKUP_SCHEMA_VERSION_MISMATCH | 备份 schema 与当前 head 不一致；使用匹配版本的备份或先完成升级，不要直接切换。 |
| BACKUP_INTEGRITY_FAILED / MANIFEST 校验失败 | 不要恢复该备份；保留现场，检查磁盘/备份介质，使用上一份已验证快照。 |
| RESTORE_GAP_BLOCKED | 这是预期的账本缺口保护；核对账本后执行 clear-restore-gap，或等待 gap_expires_at。 |
| 恢复后旧密码仍可用 | 停止服务，检查是否误用了不带当前 password_hash 下限的旧版本；不要继续对外服务。 |
| 恢复后交付仍可下载 | 检查当前审批/交付撤销状态与 G08 门禁；不要手工改数据库绕过。 |
| 切换失败 | 查看 safety backup 和 `.restore-old-*` 隔离目录；原目录应在失败时恢复，确认服务仍指向原数据。 |

## 8. 安全与数据边界

- 恢复目录和备份目录按敏感数据保护，仅服务账号与维护者读写。
- 不要把 .env、模型密钥、TLS 私钥、缓存、测试文件或临时数据库放入备份/恢复目录。
- 不在网页、聊天或工单中传输备份内容、密码或恢复链接。
- 恢复审计事件只暴露安全摘要；完整 detail 仍保存在 audit_events，按维护权限访问。
- 任何真实数据根操作都必须先获得维护者明确授权并完成可验证备份；本手册不构成授权。

## 9. 相关文档

- docs/acceptance/release-restore-recovery.md
- docs/operations/release-backup-rotation.md
- docs/operations/release-production-entry.md
- docs/architecture/hld_v3.md
- #83 保留期与秘密收口、#84 真实数据迁移、#85 最终 Release-GATE。
