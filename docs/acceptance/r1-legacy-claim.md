# R1 核源后有回退点的幂等旧数据认领验收记录

- **Issue**：#63 V3 T11 [R1]：核源后有回退点的幂等旧数据认领
- **父 Issue**：#52 V3 规格实施
- **分支**：ticket/63（基于 integration/v3 080f8e8）
- **代码提交**：82c8de3977c95bb95cdc24d9846e9286d335fecd
- **验收日期**：2026-10-31
- **状态**：本票实现、目标测试、相关回归、全量后端覆盖、前端回归与构建、迁移检查和真实 HTTP 冒烟均通过；合并由 integration/v3 执行

## 1. 结论与边界

本票实现了一个只在本机维护 CLI 中执行的旧数据认领流程。认领前必须先选择唯一真实数据源、完成并验证一致性备份，并由当前实例的首管明确确认；冲突来源不会自动合并。认领只把指定旧项目的 owner 设置为该首管，不自动批准 legacy_unreviewed 内容，不生成审批、操作者或生成授权，也不改变原始业务快照。未认领项目继续保持无 owner，普通 API 不可见、不可读取；新账号工作区为空。重复执行同一备份、同一管理员、同一项目集时返回原认领记录和同一认领 ID，不重复写审计或重复改 owner。认领事务、审计和快照校验在同一事务中提交，审计故障会整体回滚。

本票只承担 ticket #63 的阶段条件，不代表整个 R1 已完成。明确未覆盖：

| 未覆盖项 | 归属 |
|---|---|
| R1 全阶段真实路由全清单、跨专题矩阵、全制品秘密标记扫描 | #64 T12 R1-GATE |
| 交付、审批、历史、恢复的完整 R2／R3 跨账号与跨版本矩阵 | #65–#70（R2）、#71–#77（R3） |
| 真实维护者来源选择、真实旧数据认领授权和真实业务数据操作 | 必须由维护者另行明确确认；本票只用临时数据演练，不能由包内空目录或测试夹具推断 |
| 保留期清理、已发布／参考依赖／审批证据的到期规则 | #83 及后续 Release 阶段；本票只验证认领不改变旧 reviewed 状态 |
| 真实供应商生成和真实模型闭环 | R2／R3 与 Release-GATE |

真实 HTTP 冒烟使用临时 SQLite、完整 App、测试 v1 种子和本机 CLI；没有触碰真实数据根，也没有连接真实模型供应商。冒烟中的旧项目来自测试种子，不代表维护者已经选择或授权任何真实旧数据。该边界在第五节和第七节如实标注。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t63 |
| 分支／基座 | ticket/63 / integration/v3 080f8e8 |
| 代码提交 | 82c8de3977c95bb95cdc24d9846e9286d335fecd |
| 操作系统 | Windows（本地开发环境） |
| Python | 3.12.13（backend/.venv） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 数据库 | SQLite 临时数据目录（aiosqlite，WAL）；迁移版本 0007 |
| Web | 真实 uvicorn + 构建后的 dist；不使用真实业务数据 |
| 模型 | 测试用 LLM／Visual 假适配器；本票不调用真实模型 |

## 3. 实现摘要

### 3.1 来源、备份和回退验证

- main/backend/app/maintenance/sources.py 的 select_source 负责枚举候选数据根、计算内容摘要并拒绝冲突来源自动合并；存在其他非空候选时必须提供 --confirm-source-digest，摘要不一致直接失败。
- main/backend/app/maintenance/claim.py 的 _verify_backup_for_source 要求备份目录存在且不在源数据根内，调用 load_manifest 与 verify_backup 校验清单、文件大小／摘要、SQLite 完整性、外键、schema 版本和 asset 数量；缺文件、来源根不一致或 schema 不一致都拒绝认领。
- 认领前要求选中的源数据库 schema 为当前 alembic head，备份中的 schema 必须与源数据库一致；不存在有效的、可验证的备份时不执行认领。

### 3.2 认领记录与幂等键

- 新迁移 0007 同时放在 main/backend/migrations/versions/0007_r1_legacy_claim.py 和 backend/migrations/versions/0007_r1_legacy_claim.py，创建 legacy_claims 表。字段包括 id、idempotency_key、admin_account_id、source_root、source_digest、backup_id、backup_manifest_sha256、project_ids_json、project_count、snapshot_before_digest、snapshot_after_digest、created_at、status；admin_account_id 外键指向 accounts.id 且为 RESTRICT，另有 ix_legacy_claims_admin_created 索引。
- 幂等键由 backup_manifest_sha256、admin_account_id 和排序后的 project_ids 计算，不包含当前 source_digest。这样同一个已核验备份和同一项目集的重复命令可以命中原认领记录，而不会被 owner 更新后的新摘要误判为新请求。
- source_digest、backup_id、manifest 摘要、项目集和认领时间都持久化到 legacy_claims，作为后续回退和复核证据。

### 3.3 事务性认领与快照保护

- claim_legacy 在写事务前重新读取管理员账号，确认其存在、is_admin、can_login，并且是 instance_bootstrap 记录中的首管；只有首管可以认领。
- 被选中项目必须存在、owner_account_id 为空、至少有一个 LegacyItem 证据；任何缺失、已认领或非旧项目都拒绝。
- _snapshot_digest 动态枚举 projects 及 messages、assets、generations、workflow_runs、role_calls、artifacts、artifact_reviews、approvals、generation_jobs、deliveries、legacy_items、diagnostic_events，排除 projects.owner_account_id 后按 rowid 顺序哈希。认领事务前后都计算并比较该摘要，确保除 owner 外没有业务记录、审批、操作者或快照被篡改。
- 同一事务插入 LegacyClaim、设置 Project.owner_account_id、flush 后再次比较快照，并写入 legacy_projects_claimed 审计事件；审计失败会回滚 claim 行和所有 owner 更新。
- 重复认领时校验原认领的 source_root、backup_manifest_sha256、项目 owner 和 snapshot_after_digest；任一变化返回 CLAIM_STATE_CHANGED，不会覆盖原记录。

### 3.4 旧内容状态不变

认领只建立所有者，不修改 Asset.review_status、LegacyItem.status、Approval、GenerationJob 或任何历史操作者字段。legacy_unreviewed 的生成资产在认领后仍然不能通过字节门禁；参考图等已允许的原始字节按现有 #61 门禁读取。这样“有账号／有 owner”不等于“内容已批准”。

### 3.5 CLI 与审计视图

- main/backend/app/maintenance/cli.py 新增 inspect-legacy 与 claim-legacy 子命令；两者都不暴露 HTTP 路由。
- claim-legacy 必须提供 --source、--backup、--admin-email、至少一个 --project、--confirm-claim CLAIM_LEGACY、--service-stopped；如存在候选冲突还必须提供 --confirm-source-digest。CLI 不提供 --password，不接受默认密码。
- legacy_projects_claimed 已加入 main/backend/app/auth/security_events.py 的事件类型，project_count 加入安全 detail 白名单；管理员安全事件视图可以看到认领事件，但不会得到密码、令牌、完整路径正文或 provider 错误。
- 普通用户不能调用维护 CLI；普通 API 仍然按 #55–#62 的认证、归属和字节门禁执行。

### 3.6 测试覆盖

backend/tests/test_t63_legacy_claim.py 共 6 个测试，使用真实 ASGI App、临时 SQLite、AuthClient、真实首管和已验证备份：

1. 认领必须显式确认、服务已停止且存在有效备份；错误条件不改变 owner、不写 claim。
2. 冲突来源必须确认摘要；首次认领成功后重复命令幂等，claim 行和审计仍为 1。
3. 认领后只有被认领项目出现在管理员列表；未认领项目 404；参考字节可读且精确，生成字节 409 且不返回内容；新账号列表为空；旧审批／生成任务不被伪造；管理员安全事件显示 project_count。
4. 审计写入失败回滚 claim 行和 owner 更新。
5. 备份后源内容变化被 SOURCE_CHANGED_SINCE_BACKUP 拒绝。
6. inspect-legacy 只列出无 owner 且至少有一个 LegacyItem 的项目，并返回与源一致的摘要。

## 4. 执行命令与结果

### 4.1 目标测试

工作目录：D:/Desktop/新建文件夹/.worktrees/t63/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t63-temp TMP=D:/Desktop/新建文件夹/.scratch/t63-temp     D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t63-target-basetemp       tests/test_t63_legacy_claim.py

结果：6 passed in 5.48s，exit 0。

### 4.2 相关回归

相关工作目录：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings       tests/test_t63_legacy_claim.py       tests/test_v2_t01_legacy.py       tests/test_v2_t49_legacy_review.py       tests/test_t58_account_status.py       tests/test_t15_contract.py

结果：38 passed in 37.11s，exit 0。

### 4.3 全量后端覆盖

工作目录：D:/Desktop/新建文件夹/.worktrees/t63/backend

单次全量命令超过运行工具 600 秒上限，在约 80% 处被截断且没有最终摘要；为覆盖全部 48 个测试文件，按文件名排序分两组执行：

    files=$(ls tests/test_*.py | head -n 24)
    TEMP=D:/Desktop/新建文件夹/.scratch/t63-pytest TMP=D:/Desktop/新建文件夹/.scratch/t63-pytest       D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t63-seg1-basetemp $files

    files=$(ls tests/test_*.py | tail -n 24)
    TEMP=D:/Desktop/新建文件夹/.scratch/t63-pytest TMP=D:/Desktop/新建文件夹/.scratch/t63-pytest       D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings       --basetemp=D:/Desktop/新建文件夹/.scratch/t63-seg2-basetemp $files

结果：第一组 278 passed, 2 warnings in 269.83s；第二组 258 passed, 1 warning in 345.58s；合计 536 passed，覆盖全部 48 个测试文件。日志为 .scratch/t63-backend-seg1.log 和 .scratch/t63-backend-seg2.log；warning 仅为测试临时目录 cwd 提示。该拆分是运行时限造成的执行方式，不是跳过用例，也没有删除回归。

### 4.4 前端回归、类型检查与构建

工作目录：D:/Desktop/新建文件夹/.worktrees/t63

    D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --reporter=dot
    D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p D:/Desktop/新建文件夹/.worktrees/t63/tsconfig.json
    D:/Desktop/新建文件夹/node_modules/.bin/vite.cmd build

结果：33 个测试文件、206 个测试通过，26.56s；vue-tsc exit 0；Vite 构建成功 812ms，输出 dist/index.html（0.47 kB）、dist/assets/index-CoQKJWll.css（412.64 kB）、dist/assets/index-B9cC436c.js（1,175.91 kB）。只有既存 chunk > 500 kB 警告。本票未修改前端源码，前端回归用于确认现有页面和类型未受后端改动影响。

### 4.5 静态检查

对 main/backend/app/maintenance/claim.py、main/backend/app/maintenance/cli.py、main/backend/app/auth/security_events.py 和 backend/tests/test_t63_legacy_claim.py 执行 Ruff，结果 All checks passed。

### 4.6 迁移证据

脚本：.scratch/t63-migration/migration_check.py
结果：.scratch/t63-migration/result.json

| 检查项 | 结果 |
|---|---|
| 全新建库 alembic upgrade head | fresh_version 0007；legacy_claims 表存在 |
| legacy_claims 列 | id、idempotency_key、admin_account_id、source_root、source_digest、backup_id、backup_manifest_sha256、project_ids_json、project_count、snapshot_before_digest、snapshot_after_digest、created_at、status |
| 索引 | ix_legacy_claims_admin_created 及两个唯一索引存在 |
| 外键 | admin_account_id -> accounts.id，RESTRICT |
| 0006 -> 0007 保留检查 | 既有 account、project 保留，project 标题和 owner 保留；legacy_claims 存在且初始 count 0 |
| 两份迁移文件 | migration_copies_identical true |

### 4.7 真实 HTTP + 临时 SQLite 冒烟

脚本：.scratch/t63-run-smoke.py、.scratch/t63-smoke-seed.py
结果：.scratch/t63-claim-smoke/result.json
服务日志：.scratch/t63-claim-smoke/uvicorn.log

环境：完整 App，uvicorn 127.0.0.1:18775，DATA_DIR=.scratch/t63-claim-smoke/data，WEB_DIST_DIR=.worktrees/t63/dist，APP_PUBLIC_URL=http://127.0.0.1:18775，ALLOWED_HOSTS=127.0.0.1/localhost，ALLOW_LOOPBACK_HTTP=true，AUTH_COOKIE_SECURE=false。临时库从 0001 升级到 0007，包含两个测试 v1 旧项目，首管为 admin@example.com，密码 only in memory，不写入日志。

| 场景 | 结果 |
|---|---|
| inspect-legacy 列出未认领旧项目 | 2 个项目；返回 source_digest |
| 首次 claim-legacy | idempotent=false；claim 行 1；审计 legacy_projects_claimed 1；被选项目 owner 设置 |
| 重复 claim-legacy（同一备份、管理员、项目集） | idempotent=true；claim_id 与首次相同；claim 行和审计仍为 1 |
| 未认领项目 | owner=null；管理员列表不出现；GET 项目 404 |
| 被认领项目详情 | 200；参考图字节 200 且 exact=true |
| 旧生成资产字节 | 409；响应不含原始字节；review_status 仍为 legacy_unreviewed |
| 旧审批／生成任务 | Approval 0；GenerationJob 0；LegacyItem 状态全部 legacy_unreviewed |
| 管理员安全事件 | 200；legacy_projects_claimed 1 条；detail 只有 project_count=1 |
| 新账号工作区 | 通过管理员创建并设置后登录；GET /api/projects 返回 [] |
| 匿名访问 | GET /api/projects 401 |
| 服务日志出现密码 | false |

冒烟真值说明：旧项目来自测试 v1 种子，不连接真实供应商；该冒烟验证应用侧 CLI、真实 HTTP、临时 SQLite、旧内容隔离和日志边界，不声称已经对任何真实旧数据执行认领。

## 5. 验收覆盖表

| V3-AC | 本票条件 | 本票证据 | 结论 |
|---|---|---|---|
| V3-AC-18 | 自有待审／撤销／过期图、原始 locator、静态路径和历史 URL 均不能绕过 G08；给资产分配账号不改变内容批准 | 认领测试中未审生成资产仍为 legacy_unreviewed，字节 409 且不返回内容；参考图字节按现有门禁可读；#61 的路由和字节矩阵回归通过 | 本票只覆盖“认领不改变内容批准”及与旧资产的隔离；完整路由／locator 矩阵由 #61 与 #64 R1-GATE 复核 |
| V3-AC-33 | 单来源和冲突多来源迁移均可验证；备份前不认领，未认领不开放；重复执行不丢数据，原批准／操作者不被伪造，新账号无旧项目 | 6 个目标测试覆盖单一来源、冲突来源、缺有效备份、摘要变化、幂等重复、审计回滚、未认领 404、新账号空列表；真实 HTTP 冒烟重复验证 | 本票阶段条件已覆盖；真实来源授权留给维护者，真实数据演练不在本票 |
| V3-AC-34 | 必要事件和变更前后值可追踪，本人／管理员视图正确且无秘密；审计写入故障不静默完成敏感管理变更 | legacy_projects_claimed 审计写入与 owner 更新同事务；审计失败回滚测试；管理员事件视图只显示 project_count；安全事件不返回密码／令牌 | 认领事件和审计回滚已覆盖；全事件矩阵和全制品秘密扫描由 #64 R1-GATE 复核 |
| V3-AC-35 | 时间推进与停机后启动执行到期规则，不误删已发布、参考依赖、审批证据或未认领旧资产；普通日志不夹带完整业务正文 | 本票验证未认领旧资产 owner 为空、生成资产仍隔离、认领不修改 Approval／GenerationJob／LegacyItem；临时数据不执行保留期清理 | 仅覆盖“未认领旧资产不被误当新失败图／不误删”的边界；保留期到期规则由后续 Release 票和 #64 复核 |

## 6. 证据文件

- 代码：main/backend/app/maintenance/claim.py、main/backend/app/maintenance/cli.py、main/backend/app/auth/security_events.py
- 迁移：main/backend/migrations/versions/0007_r1_legacy_claim.py、backend/migrations/versions/0007_r1_legacy_claim.py
- 测试：backend/tests/test_t63_legacy_claim.py
- 全量日志：.scratch/t63-backend-seg1.log、.scratch/t63-backend-seg2.log
- 迁移结果：.scratch/t63-migration/result.json
- 冒烟脚本：.scratch/t63-run-smoke.py、.scratch/t63-smoke-seed.py
- 冒烟结果：.scratch/t63-claim-smoke/result.json；服务日志 .scratch/t63-claim-smoke/uvicorn.log
- 相关文档：docs/operations/r1-legacy-claim.md

## 7. 残余风险

- 真实数据认领必须由维护者明确选择来源并确认授权；本票的临时测试种子不能推断真实数据根或真实项目集合。
- 全量后端因工具 600 秒上限拆成两组，共 536 passed；两组按文件名覆盖全部 48 个测试文件，但不构成单进程一次性运行的摘要。
- 认领只建立 owner，不自动审阅 legacy_unreviewed 内容；用户仍需按当前工作流重新确认需求并取得新的生成授权。
- 真实 HTTP 冒烟使用测试 v1 种子，不验证真实历史数据规模、真实审批证据或真实供应商结果。
- #64 R1-GATE 仍需完成真实路由全清单、跨账号矩阵、秘密标记扫描、审计故障和迁移回退演练；本票不据此声称 R1 已完成。
