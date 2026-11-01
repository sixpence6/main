# Release T30 受控恢复、凭证撤销与账本缺口阻断验收记录

- Issue: #82
- 父 Issue: #52
- 分支: ticket/82
- 基线: integration/v3 fdb311d
- 代码提交: 0034ff2d1840eb0ba53ddce0fda99a1b769c8838
- 验收日期: 2026-11-01
- 状态: 通过（Release T30 阶段条件；正式发布由 #85 Release-GATE 决定）

## 1. 结论与边界

本票把 C0 的备份校验/恢复演练扩展为可审计的受控生产恢复：只允许本机维护 CLI，在服务停止且明确确认后，先验证备份并保存当前回滚点，再在隔离副本中检查 schema、所有者、审批、交付和文件引用，最后原子切换数据根。恢复后所有既有会话和未使用的一次性链接被撤销，审批/交付/资源不会被恢复成可用状态，旧密码和停用状态不能复活；账本缺口期间只允许同请求重放，不允许新受理，直到缺口窗口结束或本机维护者完成审计核对。

1. 恢复只通过 `python -m app.maintenance.cli restore` 本机 CLI 执行，要求 `--service-stopped` 和 `--confirm-restore RESTORE`；不提供网页整库导入/恢复路由。
2. 恢复前必须对备份执行 `verify_backup` 与 manifest 摘要校验，备份的 alembic_version 必须等于当前 head（0010）；不允许把旧 schema 直接切到生产。
3. 切换前先对当前源创建一份新的安全备份（safety backup），再复制目标备份到隔离 staging，二次校验后才切换；切换失败会把原目录恢复，原数据保持可用。
4. staging 校验覆盖 SQLite integrity_check、foreign_key_check、alembic_version、项目 owner_account_id、审批 artifact、交付 asset/job、job owner 与资源文件摘要；任何一项失败都不切换。
5. 恢复后的凭证处理：所有 sessions 写入 revoked_at/restore_revoked，accounts.session_epoch 递增，未使用 account_links 撤销但保留原有过期时间；已使用链接不改写。
6. 恢复不复活历史权限：当前已被撤销的审批、已撤销的交付和被拒绝的资源在恢复后仍保持撤销/拒绝；当前密码哈希与停用标志作为下限写回，旧备份中的旧密码和启用状态不能覆盖当前安全状态。
7. 受理账本保持保守：当前 24 小时已用次数和活动槽位若高于备份值，差额写入 usage_floors / slot_floors；缺口期间新意图返回 409 RESTORE_GAP_BLOCKED，同 request_id 的既有受理重放仍可返回原意图，不会新增计数或槽位。
8. 缺口闸门保存在数据根 `restore-gate.json`（maintenance-restore-gate/v1），含 backup_id、restored_at、gap_expires_at、撤销计数和 floors；到期后自动不再阻断，也可由本机维护者执行 `clear-restore-gap --confirm-clear CLEAR_RESTORE_GAP` 审计关闭。
9. 恢复和清缺口都会写入 audit_events：maintenance_restore_completed、maintenance_restore_gap_cleared，actor_kind=local_maintainer、target_kind=maintenance、source_ref=cli；安全事件投影已允许这两个事件类型和恢复相关安全摘要。
10. 用完整 App、真实临时 SQLite（alembic 0010）和 fake OpenAI-compatible provider 完成了备份、恢复、旧/新密码、交付撤销、资源门禁、缺口阻断、清缺口和继续受理的真实 HTTP 链路；未触碰 backend/data 或 main/backend/data，未调用真实模型供应商。本票不替代 #83 的保留期/密钥收口、#84 的真实数据迁移或 #85 最终发布关口；#51 保持打开。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t82 |
| 分支 | ticket/82 |
| 基线 | integration/v3 fdb311d |
| 操作系统 | Windows；Python 3.12.13；Node v26.12.1；npm 11.20.0 |
| 前端 | Vite 8.3.5；Vitest 5.0.5；Vue 3.5 |
| 后端测试解释器 | D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe |
| 数据库 | 临时 SQLite；alembic 0010；26 张表，integrity ok |
| 模型 | 本地 fake OpenAI-compatible provider；无真实供应商调用 |
| 数据根 | 仅 .scratch/t82-restore 临时目录；未触碰 backend/data、main/backend/data |

## 3. 实现摘要

### 3.1 恢复闸门与账本底线

- 新增 main/backend/app/maintenance/restore_state.py：读写 `restore-gate.json`，提供 gate_is_open、usage_floor_count、slot_floor_count、build_gate、clear_restore_gate。
- gate 字段包括 schema/status/backup_id/restored_at/gap_expires_at/cleared_at/cleared_by/clear_reason、sessions_revoked、links_revoked、approvals_revoked、deliveries_revoked、assets_guarded、usage_floors、slot_floors。
- gate_is_open 对损坏或缺失 expiry 采取 fail-closed；usage_floor_count 只在当前 24 小时窗口内计数，slot_floor_count 只在 gate open 时计数。
- acceptance.py 在既有同 request_id 重放之后、创建新 intent 之前检查 gate；新受理返回 409 RESTORE_GAP_BLOCKED。used 和 personal/instance/project 活动槽位均叠加 floor，usage_snapshot 同样显示 floor。

### 3.2 受控恢复切换

- 新增 main/backend/app/maintenance/restore.py：restore_switch 要求 RESTORE 确认与 service_stopped，拒绝 DEFAULT_DATA_DIR、MAIN_DIR/backend/data，除非显式 allow_real_data_root。
- 先 verify_backup + load_manifest，要求备份 alembic_version == head_revision() 0010；再 inspect_candidate 当前源并创建 safety backup。
- 目标备份复制到源目录同级的隔离 staging，二次 verify；`_validate_staged_database` 检查 integrity_check、foreign_key_check、alembic_version、项目 owner、审批 artifact、交付 asset/job 和 job owner。
- `_switch_source` 用两次 os.replace 将原源移到 .restore-old-<uuid> 隔离区，再把 staging/data 移到源位置；第二步失败时恢复原目录，成功后才删除隔离区。

### 3.3 凭证、审批与交付

- `_read_current_metrics` 只读读取当前 24 小时 generation_usage、活动 generation_slots、已撤销审批/交付、被拒绝资源、账号 password_hash/is_disabled，并合并已有 open gate 的 floors。
- `_apply_restore_purge` 撤销全部 sessions，递增 session_epoch，撤销未使用 account_links 并保留 expiry；不触碰已使用链接。
- 当前已撤销的 approvals 重新写入 revoked_at/restore_revocation_floor，当前 status=revoked 的 deliveries 重新置 revoked，当前 rejected 的 assets 重新置 rejected；当前 password_hash 与 is_disabled 覆盖备份旧值。
- `_build_usage_floors` 取 max(当前计数 - 备份计数, 0)，窗口从 now-24h 开始，过期时间取 max(当前窗口过期, now+1)，避免 floor 立即失效导致穿透；槽位 floors 按 personal/instance/project 分别计算。

### 3.4 CLI、审计与兼容

- cli.py 新增 restore 与 clear-restore-gap 子命令；原有 backup、backup-status、verify、restore-verify、inspect-sources、inspect-legacy、claim-legacy 不变。
- `_insert_audit` 写入 maintenance_restore_completed / maintenance_restore_gap_cleared；security_events.py 允许这两个事件类型及 safety_backup_id、gap_expires_at、cleared_at、approvals_revoked、deliveries_revoked、assets_guarded 安全摘要。
- 没有新增 HTTP 路由，没有新增 migration；数据库 head 仍为 0010。

### 3.5 测试

- 新增 backend/tests/test_t82_restore.py 5 个测试：完整 restore + 凭证撤销/floors、错误确认/损坏备份不改变源、clear 确认与幂等、gate helpers 不创建 gate、恢复事件在安全事件 allowlist。
- 复用了既有维护备份测试与完整业务链路；不删除既有回归。

## 4. 命令与结果

### 4.1 针对性后端测试

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m pytest tests/test_t82_restore.py -q
~~~

结果：5 passed in 3.20s，exit 0。日志 .scratch/t82-evidence/backend-targeted-final.log。

### 4.2 Ruff

~~~powershell
D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe -m ruff check main/backend/app/auth/security_events.py main/backend/app/maintenance/restore.py backend/tests/test_t82_restore.py
~~~

结果：All checks passed!，exit 0。

### 4.3 前端回归与构建

~~~powershell
npm test -- --run --reporter=dot
npm run typecheck
npm run build
~~~

结果：38 个测试文件、173 个测试通过（25.77s，exit 0）；vue-tsc 无输出（exit 0）；Vite 8.3.5 生产构建 1,664 modules、734ms，dist/index.html 0.47 kB、CSS 430.37 kB、JS 1,159.11 kB，仅 chunk 体积提示（exit 0）。日志 .scratch/t82-evidence/frontend-full.log、typecheck.log、build.log。

### 4.4 后端全量回归

非 v2 采用 44 个测试文件分三组运行：170 passed in 162.59s、137 passed with 2 warnings in 130.17s、75 passed in 76.65s，合计 382 passed；v2 为 19 个文件、235 passed in 306.76s。总计 617 passed，无 failed/skipped/unknown；仅有既有的 DeprecationWarning（backend/tests/support/auth.py:42）。日志 .scratch/t82-evidence/backend-nonv2-final-1.log、backend-nonv2-final-2.log、backend-nonv2-final-3.log、backend-v2-final.log。

### 4.5 真实临时环境 smoke

~~~powershell
python D:/Desktop/新建文件夹/.scratch/t82-restore/run_restore.py
~~~

结果：exit 0；完整 App 使用临时 SQLite（alembic 0010）和 fake provider，经 HTTP 完成管理员登录、用户创建/设置密码、项目创建、S06 确认、S07–S10 后台链路、批准/发布/下载，再执行 CLI 备份、数据变更、CLI 恢复、清缺口与继续受理。

- project d368bc28-13d6-4629-b7da-260f4824615e；第二项目 1b3c843c-7ae2-4e42-9eef-6fccb99e198f；account 1a19bdcb-f32a-4dd4-a512-2a35469f0314
- delivery asset 29809c36-819d-4639-9ce0-03ebbf80490c；filename delivery-29809c36-r1.png；29373 bytes；content-type image/png
- 备份 backup_id 20261101T065825Z-583dbde2，verified true，1 file，29373 bytes，manifest_sha256 06c973eaf40db461496541315e5bad54d923fe6d1c9e708ebbd60e6cf8e6d3dc
- 恢复 safety backup 20261101T065828Z-64ee5310；sessions_revoked 4、links_revoked 1、approvals_revoked 1、deliveries_revoked 1、assets_guarded 1、usage_floor_accounts 1；gap_expires_at 2026-11-02T06:58:28Z
- 恢复后旧密码 401；新密码 200；delivery status revoked；资源下载 409；第二项目确认 409 RESTORE_GAP_BLOCKED；pending setup link 无效；maintenance_restore_completed 审计 1 条；无活动会话
- clear-restore-gap 后 status cleared、already_cleared false、cleared_by local_maintainer；重启并登录新密码后同一项目确认 200、stage S07、usage used >= 2
- 账本 counts：accounts 3、projects 2、generations 1、generation_intents 2、generation_usage 2、generation_slots 6、approvals 6、deliveries 1、assets 1、sessions 6、account_links 2、audit_events 22

结果文件：.scratch/t82-restore/result.json、fake.log、server-a.log、server-b.log、server-c.log、server-d.log。

### 4.6 数据库与路由边界

- 临时 SQLite alembic 0010；26 张表；integrity ok；没有新增 HTTP 路由。
- 未触碰 backend/data、main/backend/data；没有读取或复制 .env、模型密钥、TLS 私钥。

## 5. V3 验收条件对照

| AC | 本票证据 |
| --- | --- |
| V3-AC-12 | 恢复前 service_stopped、verify_backup、safety backup、隔离 schema/owner/approval/delivery 校验、原子切换和失败回滚均有测试与真实 smoke |
| V3-AC-18 | 恢复不使已撤销审批/交付/拒绝资源复活；资源下载仍按 G08 审批/交付门禁 409 |
| V3-AC-32 | 恢复撤销全部会话与未使用链接，session_epoch 递增；旧密码不能登录，新密码可登录；不自动调用模型 |
| V3-AC-33 | restore-gate.json 与 usage/slot floors 保留缺口；新受理 409 RESTORE_GAP_BLOCKED；同 request 重放不新增；clear 后审计关闭 |
| V3-AC-35 | 恢复不触碰备份外秘密文件，不提供网页整库导出/导入；只允许本机 CLI |
| V3-AC-37 | maintenance_restore_completed / maintenance_restore_gap_cleared 审计事件与 safe detail 投影 |
| V3-AC-40 | 代码、环境、命令、结果与临时数据边界已记录；全量回归 617 passed；未删除有效回归 |

## 6. 证据

- 代码提交：0034ff2d1840eb0ba53ddce0fda99a1b769c8838
- 针对性测试：.scratch/t82-evidence/backend-targeted-final.log（5 passed）
- 后端全量：backend-nonv2-final-1/2/3.log + backend-v2-final.log（382 + 235 = 617 passed）
- 前端全量：frontend-full.log（38 files / 173 tests）；typecheck.log；build.log
- 真实 smoke：.scratch/t82-restore/result.json、fake.log、server-a..d.log
- 备份/恢复 ID、密码行为、交付状态、资源门禁和审计事件见 4.5。

## 7. 剩余风险与后续

1. 本票验证使用 fake OpenAI-compatible provider 和临时 SQLite，不代表真实供应商、真实磁盘故障或多节点并发恢复。
2. 恢复切换是单机 SQLite 数据根原子替换；未覆盖跨文件系统、电源中断和备份介质损坏；safety backup 只提供回滚点。
3. 恢复只处理数据库与允许资源目录，不迁移或判断 .env、TLS 私钥和模型密钥；这些必须由维护者另行配置。
4. 缺口窗口固定 86400 秒；到期后自动解除阻断，但不会自动补做人工核对；需要更严格窗口时由维护者用 CLI 管理。
5. 没有真实手机、真实局域网第二设备和生产证书链验证；这些属于 #85 最终发布关口。
6. #83 仍需完成保留期、秘密与发布包收口；#84 仍需维护者授权的真实数据迁移；#51 在最终发布前保持打开。
