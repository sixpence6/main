# Release T32 真实数据核源、认领与可回退切换演练验收记录

- Issue: #84
- 父 Issue: #52
- 分支: ticket/84
- 代码基线: integration/v3 c21424c337535e4c01302e2a36f5c773a0e39cba（本票无生产代码改动）
- 验收日期: 2026-11-01
- 状态: 通过（Release T32 演练条件；真实生产数据仍未迁移，最终发布由 #85 Release-GATE 决定）

## 1. 结论与边界

本票对真实数据候选根做只读核源，并在隔离字节副本上完成 0001 -> 0010 迁移、历史项目认领、认领前备份、HTTP 权限/内容检查、受控恢复回退和启动保留期检查。结论如下：

1. 真实根 D:/Desktop/新建文件夹/backend/data 是当前唯一非空候选源；main/backend/data 为空。核源结果：alembic 0001、12 个项目、14 个资源（13 generated + 1 reference）、19 个 generation、49 条消息、无 accounts、无 owner_account_id；14 个文件共 30,599,431 字节，content_digest 为 2b56fa60ed855defc6669c779646a11762c9ee349490b7fc11134a5e8210aee9。
2. 没有对真实根执行迁移、认领、恢复、删除或写入；所有写操作只发生在 D:/Desktop/新建文件夹/.scratch/t84-rehearsal 下的字节副本。演练结束后重新核源，真实根 digest 与文件清单仍未变化。
3. 11 个有历史证据的项目被本机维护 CLI 认领；1 个空项目“未命名项目”（52e396ec-7012-4ad1-a8f9-94028383ff48）没有 LegacyItem 证据，保持 owner NULL，不猜测归属。
4. 认领前备份已独立验证；重复认领返回 idempotent=true 且不新增 claim；认领只改变 11 个项目的 owner_account_id，其它业务表逐行一致，claim 前后 snapshot digest 相同。
5. 认领后历史内容仍不可当作已审查内容使用：项目列表返回 11 个 legacy_unreviewed 项目，资源下载 409 LEGACY_UNREVIEWED，静态 /generated 路径 404。
6. 受控恢复到认领前备份成功：会话被撤销，当前密码可重新登录，claim/owner 回滚，恢复闸门 open 到 2026-11-02T08:42:47.112Z；新受理返回 409 RESTORE_GAP_BLOCKED。
7. 启动保留期检查在迁移副本上运行后，14 个文件与 14 条资源记录逐个 SHA256 不变；legacy_unreviewed 与 quarantined 历史内容没有被删除。
8. 本票验证的是字节副本，不是生产切换本身；不能把本记录当作“真实历史数据已经迁移完成”。真实根正式迁移必须由维护者明确指定源、范围和窗口后，再按同一流程执行。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t84 |
| 分支 | ticket/84 |
| 基线 | integration/v3 c21424c |
| 操作系统 | Windows；Python 3.12.13；Node v26.12.1；npm 11.20.0 |
| 后端测试解释器 | D:/Desktop/新建文件夹/.worktrees/t75/backend/.venv/Scripts/python.exe |
| 数据库 | 真实数据字节副本；SQLite；迁移前 0001，迁移后 0010；26 张表 |
| 前端 | Vite 8.3.5 构建产物；无真实供应商调用 |
| 真实数据根 | D:/Desktop/新建文件夹/backend/data（只读核源）；main/backend/data 为空 |
| 演练副本 | D:/Desktop/新建文件夹/.scratch/t84-rehearsal/{base-original,live,retention-copy} |
| 凭证 | 仅演练副本内临时管理员 rehearsal-admin@example.com；未读取 .env、模型密钥或 TLS 私钥 |

## 3. 执行步骤与命令

### 3.1 只读核源

~~~bash
python -m app.maintenance.cli inspect-sources \
  --candidate-root D:/Desktop/新建文件夹/backend/data \
  --candidate-root D:/Desktop/新建文件夹/main/backend/data \
  --json
~~~

结果保存到 .scratch/t84-evidence/inspect-sources-real.json 和 inspect-sources-final.json；两次 content_digest 相同。

### 3.2 字节副本与迁移

使用只读递归复制生成 base-original 和 live；排除 app.db-shm 与 tmp。随后只对副本执行：

~~~bash
python -m alembic -c D:/Desktop/新建文件夹/.worktrees/t84/backend/alembic.ini upgrade head
~~~

APP_DATA_DIR 指向 live；迁移 0001 -> 0002 -> 0003 -> 0004 -> 0005 -> 0006 -> 0007 -> 0008 -> 0009 -> 0010 全部成功。

### 3.3 历史项目检查与认领

~~~bash
python -m app.maintenance.cli inspect-legacy \
  --candidate-root D:/Desktop/新建文件夹/.scratch/t84-rehearsal/live \
  --confirm-source-digest a45ad68a34bfe13d3672755bd34d16f8eaf1dce26cdd05b53127ff93bed8e64e --json
~~~

确认 11 个 ownerless 项目有 55 条 LegacyItem 证据后，使用明确备份、明确管理员和 CLAIM_LEGACY 确认执行：

~~~bash
python -m app.maintenance.cli claim-legacy \
  --candidate-root D:/Desktop/新建文件夹/.scratch/t84-rehearsal/live \
  --backup D:/Desktop/新建文件夹/.scratch/t84-rehearsal/backups-preclaim/backup-20261101T083940Z-03c7e80c \
  --admin-email rehearsal-admin@example.com \
  --confirm-claim CLAIM_LEGACY --service-stopped \
  --confirm-source-digest c1b8e9445b3473ee725eaabed7bf30043ed7ce8158f6a912c78b366bbcfa1498 --json
~~~

重复同一备份、同一管理员、同一项目集再次执行（候选根 digest 8afa5a8a4d35d233e9a9349f242a80601aa67a47e38eec6acf809ed9f84cc556），返回 idempotent=true。

### 3.4 认领前备份与受控恢复

~~~bash
python -m app.maintenance.cli backup --source .../live --output .../backups-preclaim \
  --service-stopped --confirm-source-digest c1b8e9445b3473ee725eaabed7bf30043ed7ce8158f6a912c78b366bbcfa1498 --json
python -m app.maintenance.cli restore --source .../live \
  --backup .../backups-preclaim/backup-20261101T083940Z-03c7e80c \
  --output .../backups-restore --confirm-restore RESTORE --service-stopped --json
~~~

恢复前先由 backup 生成 verified 快照；restore 再生成 safety backup 后原子切换。

### 3.5 完整 App 与 HTTP 检查

使用 python -m app.production --host 127.0.0.1 --port 8814 启动完整 App，APP_DATA_DIR 指向副本，WEB_DIST_DIR 指向本工作树 main/dist，APP_PUBLIC_URL、ALLOWED_ORIGINS、ALLOWED_HOSTS、ALLOW_LOOPBACK_HTTP、AUTH_COOKIE_SECURE 按临时 HTTP 环境设置。HTTP 脚本覆盖登录、项目列表、项目详情、workflow、资源下载和静态路径；所有 unsafe 请求带 Origin 与 X-CSRF-Token。

### 3.6 启动保留期检查

对另一份迁移副本 retention-copy 启动完整 App，等待 /api/health 成功后停止；启动生命周期执行 retention。启动前后分别对 references/generated/published 全量文件做 SHA256，并读取 assets 的 kind、review_status、relative_path 对比。

## 4. 结果

### 4.1 真实源清单

| 指标 | 值 |
| --- | --- |
| 候选根 | backend/data 非空；main/backend/data 为空 |
| alembic_version | 0001 |
| 项目 | 12 |
| 资源 | 14（13 generated，1 reference） |
| generation | 19 |
| 消息 | 49 |
| 文件数 | 14 |
| 总字节 | 30,599,431 |
| content_digest | 2b56fa60ed855defc6669c779646a11762c9ee349490b7fc11134a5e8210aee9 |
| 数据库 SHA256 | 51295294785c36af3fd7934ad9e45aa19430b9d6609f2c3622a8ab3c14ae8ad4 |

项目明细见 .scratch/t84-evidence/project-inventory.json。12 个项目中 11 个有 LegacyItem 证据；空项目 52e396ec-7012-4ad1-a8f9-94028383ff48（未命名项目，0 资源、0 消息、0 generation）没有证据，保持未认领。

### 4.2 副本迁移

- 副本 digest 与真实源一致：2b56fa60ed855defc6669c779646a11762c9ee349490b7fc11134a5e8210aee9，14 文件 30,599,431 字节。
- 迁移日志见 .scratch/t84-evidence/migrate.log：0001 -> 0010 全部 OK，最后 head=0010；迁移后 inspect-legacy source_digest 为 a45ad68a34bfe13d3672755bd34d16f8eaf1dce26cdd05b53127ff93bed8e64e。
- 创建演练管理员后，备份前 source_digest 为 c1b8e9445b3473ee725eaabed7bf30043ed7ce8158f6a912c78b366bbcfa1498；认领后重复检查 digest 为 8afa5a8a4d35d233e9a9349f242a80601aa67a47e38eec6acf809ed9f84cc556。
- 迁移后数据仍为 12 项目、14 资源、19 generation、49 消息；没有导入测试数据。

### 4.3 认领与幂等

| 项目 | 值 |
| --- | --- |
| 认领项目 | 11 |
| LegacyItem | 55 |
| claim_id | 85c633fb-dc81-4c86-91fe-541fbe84e3d2 |
| 首次 idempotent | false |
| 重复 idempotent | true |
| snapshot digest | f4d636fa06e874d7a091c6dc6630d8adc022a80d967695980acd6a3267b80df3 |
| 备份 manifest | 00954ab6db8660670a94b1da523c1b76d860337bbaa7c0afdcb5e83905ea3a71 |

compare_claim.py 对比结果：26 张表；非项目表逐行一致（accounts 1、sessions 1、assets 14、generations 19、messages 49、legacy_items 55，approvals/deliveries/jobs/artifacts 0）；projects 12 行排除 owner_account_id 后一致；owner 仅 0 -> admin 的 11 行发生变化；legacy_claims 1 行；audit 只新增 1 条 legacy_projects_claimed。证据：claim-preservation.json。

### 4.4 认领后的权限与内容门禁

- 旧会话仍有效时 GET /api/projects 返回 11 个项目，全部标记 legacy_unreviewed，不是 12 个，也没有空项目。
- GET /api/projects/64a44549-a929-4a26-8775-37744908b196 返回 200，项目有 9 条消息、3 个 generation。
- GET workflow 返回 stage=null、status=none，10 条 legacy items 全部 status=legacy_unreviewed。
- GET /api/assets/0f9a89fd-8d5b-472e-9a74-ac61f4ddc86c 和 ?download=true 均为 409，错误码 LEGACY_UNREVIEWED。
- GET /generated/0f9a89fd-8d5b-472e-9a74-ac61f4ddc86c.png 为 404，静态目录不绕过审查。
- 证据：.scratch/t84-rehearsal/check-check.json、check-content.json。

### 4.5 受控恢复与回退

- 恢复目标：backup-20261101T083940Z-03c7e80c，manifest 00954ab...，verified=true。
- safety backup：20261101T084247Z-1a935b51。
- sessions_revoked=1；links/approvals/deliveries/assets_guarded 均为 0；usage_floor_accounts=0；slot floors 为空。
- restore-gate.json：status=open、schema=maintenance-restore-gate/v1、gap_expires_at=2026-11-02T08:42:47.112Z。
- 旧会话请求 /api/auth/session 和 /api/projects 均为 401 AUTH_REQUIRED；同一当前密码重新登录成功。
- 恢复后（gate probe 前）项目回归 12 个、owner 0、legacy_claims 0；重复 claim 与 owner 变更全部消失，历史文件仍在。
- 故意在恢复后创建 gate-user 与项目 7dc9ae7b-5d89-443f-8c2d-21d993fcedf1，确认请求返回 409 RESTORE_GAP_BLOCKED；该临时项目只存在于演练副本，导致最终统计为 13 项目/1 owner，不改变回滚结论。
- 证据：.scratch/t84-evidence/restore-rollback.json、post-restore-summary.json、.scratch/t84-rehearsal/gate-probe.json。

### 4.6 保留期与文件保护

迁移副本 retention-copy 启动前后：

| 指标 | 启动前 | 启动后 |
| --- | --- | --- |
| 文件数 | 14 | 14 |
| 资源数 | 14 | 14 |
| 文件清单与 SHA256 | 14 条 | 完全一致 |
| 资源记录 | 14 条 | 完全一致 |
| review_status | legacy_unreviewed、quarantined | 不变 |

启动保留期没有删除任何真实历史文件。证据：retention-before.json、retention-after.json、retention-migrate.log。

### 4.7 真实根未被修改

演练结束后再次执行 inspect-sources：content_digest 仍为 2b56fa60ed855defc6669c779646a11762c9ee349490b7fc11134a5e8210aee9，文件 14 个、30,599,431 字节，app.db SHA256 51295294785c...，wal 0。证据：inspect-sources-final.json。

## 5. V3 验收条件对照

| AC | 本票证据 |
| --- | --- |
| V3-AC-12 | 真实字节副本完成 0001 -> 0010 迁移、认领前验证备份、安全备份、受控恢复和回退；真实根 digest 前后不变 |
| V3-AC-18 | 11 个 legacy_unreviewed 项目可见但资源下载 409、静态路径 404；恢复后未把历史内容变成已审查或已交付 |
| V3-AC-32 | 恢复撤销既有会话；旧会话 401；当前密码重新登录成功；未调用模型或供应商 |
| V3-AC-33 | restore-gate.json open，恢复后新受理 409 RESTORE_GAP_BLOCKED；回退后 claim/owner 消失、历史文件保留 |
| V3-AC-40 | 记录了代码版本、环境、全部命令/结果、隔离副本边界、前后 digest 和证据文件；全量回归 624 passed、前端 38 文件/173 tests |

## 6. 证据

- 代码基线：c21424c337535e4c01302e2a36f5c773a0e39cba
- 后端：backend-nonv2-1/2/3.log（170 + 137 + 82 = 389 passed）+ backend-v2.log（235 passed）= 624 passed
- 前端：frontend.log（38 files / 173 tests）；typecheck.log；build.log
- 源清单：inspect-sources-real.json、inspect-sources-final.json、project-inventory.json
- 副本：copy-digests.json、migrate.log、retention-migrate.log
- 认领：inspect-legacy.json、backup-preclaim.json、claim-1.json、claim-2-idempotent.json、claim-preservation.json
- 恢复：restore-rollback.json、post-restore-summary.json、gate-probe.json
- HTTP：check-check.json、check-content.json、server-http-*.log
- 保留期：retention-before.json、retention-after.json
- 证据根：D:/Desktop/新建文件夹/.scratch/t84-evidence 和 .scratch/t84-rehearsal

## 7. 剩余风险与后续

1. 本票是真实数据字节副本演练，不是生产数据迁移；真实根仍为 0001，生产切换必须由维护者明确授权后另行执行。
2. 演练使用临时 HTTP 和临时管理员，不代表真实域名、真实证书链、真实浏览器或真实局域网第二设备；这些由 #85 最终发布关口覆盖。
3. 认领只处理有 LegacyItem 证据的 11 个项目；空项目保持 owner NULL，是否清理或补证据需要维护者决定。
4. 本票没有调用真实模型供应商；历史资源并未被重新生成或重新审查。
5. #85 仍需汇总 40 条 V3-AC 证据；#51 在最终发布前保持打开。
