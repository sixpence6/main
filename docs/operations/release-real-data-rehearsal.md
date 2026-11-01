# Release T32 真实数据核源、认领与可回退切换运行手册

本文说明如何在不触碰生产数据根的前提下，对真实历史数据做只读核源、字节副本迁移、历史项目认领、认领前备份、HTTP 权限检查和受控恢复回退。它覆盖 #84 的演练流程；正式生产切换必须由维护者单独授权，不能把演练结果当作生产迁移完成。

## 1. 目标与边界

- 先核源，再复制；任何写操作只能指向副本，不能直接指向 backend/data 或 main/backend/data。
- 真实根只有在维护者明确指定源、范围和维护窗口，并完成可验证备份后才允许迁移。
- 认领只处理有 LegacyItem 证据的 ownerless 项目；没有证据的项目保持 owner NULL，不猜测归属。
- 认领只设置 owner，不批准历史内容、不生成审批/交付、不伪造 operator。
- 恢复必须使用已验证备份，并要求服务停止和明确确认；恢复后会话撤销、缺口闸门打开。
- 测试、脚手架、证据和临时数据库只放在 .scratch 或仓库外测试目录，不放进 main。

## 2. 前置条件

- 维护者明确给出候选源根；若存在多个非空候选源，先由维护者裁决，不能自动选择。
- 服务已停止，所有后台任务已停止；备份/认领/恢复 CLI 都要求 --service-stopped。
- 目标磁盘有足够空间同时保存原副本、迁移副本、认领前备份、恢复 safety backup。
- 已确认代码版本和迁移 head（当前 0010）；旧 schema 不能直接切到生产。
- 已准备临时管理员账号；管理员密码只通过本地安全渠道设置，不写入文档或工单。
- 已确认不读取、不复制 .env、模型密钥、TLS 私钥。

## 3. 只读核源

先列出候选根：

~~~bash
python -m app.maintenance.cli inspect-sources \
  --candidate-root D:/Desktop/新建文件夹/backend/data \
  --candidate-root D:/Desktop/新建文件夹/main/backend/data \
  --json
~~~

检查每个候选的 content_digest、file_count、total_bytes、app_db_sha256、unsafe_files 和 non_empty_count。若多于一个非空候选，停止并请求维护者裁决；不要用目录名猜测生产源。

本票实际结果：backend/data 非空，12 项目、14 资源、19 generation、49 消息、14 文件、30,599,431 字节，digest 2b56fa60ed855defc6669c779646a11762c9ee349490b7fc11134a5e8210aee9；main/backend/data 为空。

## 4. 建立字节副本

在 .scratch 下建立 base-original 和 live 两份副本，排除 app.db-shm、app.db-wal 和 tmp；保留 app.db 与 references/generated/published。

~~~powershell
Copy-Item -Recurse -LiteralPath D:/Desktop/新建文件夹/backend/data \
  -Destination D:/Desktop/新建文件夹/.scratch/t84-rehearsal/base-original
Copy-Item -Recurse -LiteralPath D:/Desktop/新建文件夹/backend/data \
  -Destination D:/Desktop/新建文件夹/.scratch/t84-rehearsal/live
~~~

复制完成后再次对两份副本执行 inspect-sources，确认 digest 与真实根相同。后续所有迁移、认领、恢复、启动 App 都只设置 APP_DATA_DIR 指向 live 或 retention-copy。

## 5. 迁移副本

~~~bash
set APP_DATA_DIR=D:/Desktop/新建文件夹/.scratch/t84-rehearsal/live
python -m alembic -c D:/Desktop/新建文件夹/.worktrees/t84/backend/alembic.ini upgrade head
~~~

必须看到 0001 -> 0002 -> ... -> 0010 全部成功，最后 alembic_version=0010。迁移后核对项目、资源、generation、消息数量与迁移前一致。

## 6. 检查并认领历史项目

~~~bash
python -m app.maintenance.cli inspect-legacy \
  --candidate-root D:/Desktop/新建文件夹/.scratch/t84-rehearsal/live \
  --confirm-source-digest <迁移后digest> --json
~~~

检查 ownerless_projects、legacy_item_count、source_digest 和冲突。只有同时满足以下条件才继续：

1. 管理员是初始 bootstrap admin；
2. 每个待认领项目 owner_account_id 为空且有 LegacyItem 证据；
3. 备份已验证，manifest 摘要与备份目录匹配；
4. 维护者确认认领范围和项目集。

执行认领：

~~~bash
python -m app.maintenance.cli claim-legacy \
  --candidate-root D:/Desktop/新建文件夹/.scratch/t84-rehearsal/live \
  --backup D:/Desktop/新建文件夹/.scratch/t84-rehearsal/backups-preclaim/backup-<utc>-<hex> \
  --admin-email <bootstrap-admin-email> \
  --confirm-claim CLAIM_LEGACY --service-stopped \
  --confirm-source-digest <迁移后digest> --json
~~~

记录 claim_id、idempotent、snapshot_digest、project_count。用同一备份、同一管理员、同一项目集重复执行，必须返回 idempotent=true；换备份或换 owner 必须返回 conflict，不能重复写入。

## 7. 认领前验证备份

~~~bash
python -m app.maintenance.cli backup --source D:/Desktop/新建文件夹/.scratch/t84-rehearsal/live \
  --output D:/Desktop/新建文件夹/.scratch/t84-rehearsal/backups-preclaim \
  --service-stopped --confirm-source-digest <迁移后digest> --json
~~~

检查 verified=true、backup_id、manifest_sha256、file_count、total_bytes、missing_files=0。只有验证通过的备份才能作为恢复目标；把 backup_id 和 manifest 摘要写进演练记录。

## 8. HTTP 权限与内容检查

启动完整 App，APP_DATA_DIR 指向 live，WEB_DIST_DIR 指向 main/dist，APP_PUBLIC_URL/ALLOWED_ORIGINS/ALLOWED_HOSTS 指向临时地址，AUTH_COOKIE_SECURE=false 仅用于本机 HTTP 演练。

检查：

- 登录后项目列表只返回已认领项目，未认领项目不出现；
- 项目详情、workflow、历史资源按 owner 过滤；
- legacy_unreviewed 资源下载返回 409，静态 /generated、/references、/published 路径返回 404；
- 没有 owner 的项目返回 404，不能通过猜 ID 读取；
- 所有 unsafe 请求带 Origin 和 X-CSRF-Token；缺少时返回 403；
- 检查过程中不调用模型供应商，不提交新的生成。

本票结果：11 个已认领项目可见；资源 0f9a89fd-8d5b-472e-9a74-ac61f4ddc86c 的 inline/download 均 409 LEGACY_UNREVIEWED；静态路径 404。

## 9. 受控恢复回退

先停止完整 App 和所有后台任务，然后执行：

~~~bash
python -m app.maintenance.cli restore \
  --source D:/Desktop/新建文件夹/.scratch/t84-rehearsal/live \
  --backup D:/Desktop/新建文件夹/.scratch/t84-rehearsal/backups-preclaim/backup-<utc>-<hex> \
  --output D:/Desktop/新建文件夹/.scratch/t84-rehearsal/backups-restore \
  --confirm-restore RESTORE --service-stopped --json
~~~

检查 production_switched=true、verified=true、safety_backup_id、sessions_revoked、gap_expires_at。恢复后：

1. 旧会话请求 /api/auth/session 必须 401；
2. 当前密码重新登录必须成功；
3. claim 表必须为空，项目 owner 回到 NULL；
4. restore-gate.json 必须为 open；
5. 新受理必须 409 RESTORE_GAP_BLOCKED，不能新增 usage 或槽位；
6. 历史文件仍在，legacy_unreviewed 资源仍不可下载。

真实数据根如果必须执行恢复，额外需要维护者书面授权和 --allow-real-data-root；演练副本不需要该参数。

## 10. 启动保留期检查

再复制一份迁移副本 retention-copy，先记录 references/generated/published 全量文件 SHA256 和 assets 表；启动完整 App 等待 /api/health 成功后停止，让启动生命周期执行 retention；再次记录并比较。

要求：文件数、资源数、文件 SHA256、资源记录完全一致；legacy_unreviewed 和 quarantined 历史内容不能被删除；已发布资源和审批资源必须保留。若出现文件缺失或资源状态变化，停止演练并检查 retention 规则。

## 11. 演练结束核源

再次对真实根执行 inspect-sources，确认 content_digest、file_count、total_bytes、app.db SHA256 与开始前相同。若 digest 变化，停止发布并排查是否有进程或脚本写入了真实根；不要用副本结果掩盖差异。

本票结束核源：digest 仍为 2b56fa60ed855defc6669c779646a11762c9ee349490b7fc11134a5e8210aee9，14 文件、30,599,431 字节。

## 12. 故障处理

| 现象 | 处理 |
| --- | --- |
| 多个非空候选源 | 停止，请求维护者指定唯一源；不要自动选择。 |
| inspect-sources digest 变化 | 停止所有写操作，保留现场，排查服务/脚本；不要继续认领或恢复。 |
| 备份 verified=false / manifest 不匹配 | 不使用该备份；重新备份并验证。 |
| claim 返回 conflict | 说明备份、owner 或项目集与既有 claim 不一致；不要强制重试。 |
| 认领后项目仍 404 | 检查 owner_account_id、管理员会话和 owner 过滤；不要手工改库绕过。 |
| legacy 资源 409 LEGACY_UNREVIEWED | 这是预期门禁；历史内容需要独立审查，不得直接发布。 |
| restore 返回 BACKUP_SCHEMA_VERSION_MISMATCH | 备份 schema 与当前 head 不一致；先迁移到匹配版本或换备份。 |
| restore-gate 未生成 | 检查 restore 输出和 safety backup；不要启动服务对外提供新受理。 |
| 恢复后旧会话仍可用 | 停止服务，检查 sessions_revoked 和 session_epoch；不要继续对外服务。 |
| retention 删除历史文件 | 停止服务，保留现场，检查 review_status/retention_status 和 backup manifest；不要手工补文件冒充原文件。 |

## 13. 安全与数据边界

- 真实数据根只读核源；副本、备份和证据都放在 .scratch 或维护者指定目录。
- 不把 .env、密钥、TLS 私钥、密码、恢复链接写入文档、日志、聊天或工单。
- 不对真实根执行迁移、认领、恢复、retention 或删除，除非维护者明确授权并完成验证备份。
- 备份目录和恢复目录按敏感数据保护，仅服务账号与维护者读写。
- 所有真实操作都要记录代码版本、命令、输出、时间、操作者和回退点。

## 14. 相关文档

- docs/acceptance/release-real-data-rehearsal.md
- docs/operations/release-restore-recovery.md
- docs/operations/release-backup-rotation.md
- docs/operations/release-retention-secrets.md
- docs/operations/r1-legacy-claim.md
- docs/architecture/hld_v3.md
- #85 Release-GATE 与 #51 最终发布。
