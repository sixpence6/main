# #114 / T02：最终确认驱动冻结请求完成一次出图

日期：2026-11-03（Asia/Shanghai）。结论仅覆盖 **T02 隔离后端纵向切片**，不代表 R2 整体、R3 界面、真实供应商调用或发布通过；不自动关闭父 #112。

## 1. 基线、保护范围与前置

- 业务基线 `311b215687e8a87fa97d7c643eca5969645bb2d9`；本票从 T01 合入后的集成 tip `76cd5f446bd8d7d3e60714c0fb4b301538558ab9` 建立 `codex/issue-112-t02`，实现前验证集成分支为该分支祖先。
- 专属根：`C:\Users\28425\.codex\worktrees\issue-112-t02\新建文件夹`。所有业务修改、测试与日志只在此树；未切换或清理原用户树 `D:\Desktop\新建文件夹`，未读取真实凭据、真实业务库或调用真实供应商。
- 仅使用隔离临时 SQLite、`.invalid` 受控 adapter、测试密钥与独立进程重启；未运行前端 build，未实施任何 R3 界面改动（T09 门槛未到）。
- 承接用户已确认的接力检查点：`issue-112-handoff-2026-11-03.md`（`version/v3/`）与两份外部 worker/审查记录。

## 2. 最小实现与实际 interface

- **新增最终确认动作**：沿用既有工作流公开入口 `POST /api/projects/{id}/workflow/actions/confirm_prompt`，payload 为 `FinalCommand{snapshot_id, snapshot_digest, command_id, command_context}`（`extra=forbid`，摘要为 64 位十六进制）。`confirm_prompt` 被登记为 `s07` 的动作并在 `dispatch` 中标记为模型调用动作，路由不再按当前阶段动作表拒绝它。
- **只读出口**：`GET /api/projects/{id}/workflow` 的 `prompt_preview` 新增 `command_context`，并把 `can_confirm` 由常量 `false` 改为实际可用性布尔值。`command_context` 是 HMAC（key 为服务端 `csrf_digest`，材料含登录会话 id、账号 epoch、快照 id 与请求摘要），只暴露派生值，不暴露会话秘密。
- **新表与迁移**：`final_authorizations` 与迁移 `0013_final_authorizations`。授权对 `intent_id / snapshot_id / job_id / generation_id` 分别唯一，并对 `(project_id, command_id)` 唯一；`prepared_prompts.status` 增加 `authorized`。仓库内 `backend/migrations` 与 `main/backend/migrations` 的 0013 副本哈希一致（见第 4 节）。
- **同事务重验与写入**：`confirm_prompt` 在单个 `BEGIN IMMEDIATE` 内依次完成：所有者项目校验（含 `revalidate_principal`，覆盖禁用账号 / 会话 epoch / 撤销）、登录会话未过期、快照归属 `run` / 状态 / 摘要、`command_context` 常量时间比对、命令摘要冲突检查、`validated_request`（需求批准、提示词批准、规则版本、参考依赖、服务配置全部重算比对）、revision 冲突、原意图 `accepted` 与三类占用（personal / instance / project）精确匹配、同快照唯一授权判定；随后写入 `Generation`、`GenerationJob`、双向绑定、`FinalAuthorization`、快照 `authorized`、意图 `executing`、run `S08/active` 与 `prompt.final_authorized` 审计，全部 flush 后提交。任一步失败整体回滚，无授权、无尝试。
- **网络调用在事务外**：`_schedule` 只在事务提交且 `created=True` 时调用；重放分支只读取与校验，不写入、不重新调度、不新增用量。
- **执行只消费冻结请求**：`_execute_claimed` 通过 `execution_request` 调用 `require_authorized_attempt` 取回冻结请求，构造 `GenerationRequest` 时 `prompt` 取冻结文本、`brief` 取 `frozen_brief`、尺寸 / 数量 / `response_format` 取冻结输出、参考字节读取后校验 SHA-256 与冻结摘要一致；提交前再次比较 `execution_request` 与已取请求全等，否则 `PROMPT_STALE`。`openai_images` adapter 只使用 `GenerationRequest` 字段（含 `reference_bytes`），不读取项目可变字段。
- **旧入口保持默认拒绝**：`s08` 的提交 / 重试 / 风险新任务入口仍调用 `require_image_execution()`；`_run_job` 与恢复路径改用 `require_authorized_job` / `require_authorized_attempt` 的真授权绑定，历史任务无最终授权记录时不补造。

## 3. TDD 与公开验收证据

本轮续接首先复现已知失败单例（`test_two_independent_restarts_keep_waiting_without_calls`）：原因是子进程环境遗漏 `VISUAL_TIMEOUT_SECONDS`，而新的精确配置指纹包含 `timeout_seconds`，父进程配置为 5、子进程回落默认 300，导致预览按设计判为失效。修复为在测试 fixture 中显式传入父进程同一超时值，**未放宽指纹或跳过断言**；单例由 1 failed 转为 **1 passed / 8.58s**。

以下为本轮实际执行的证据（日志均在本树 `backend/.scratch/t02/`，被忽略、不打包）：

| 运行 | 结果 | 说明 |
|---|---|---|
| `t02-resume-final.log` | **61 passed / 169.00s** | T02 目标汇总：`test_issue_114_t02.py`、`test_issue_114_migration.py`、`test_issue_113_t01.py`、`test_issue_113_migration.py`、`test_t62_background_ownership.py`、T65 单轮完整流程 |
| `r1-entry-resume.log` | **131 passed、1 skipped / 203.18s** | R1 身份／对象／资源出口 + 入口与旧兼容回归；skip 是既有前端 dist 缺失制品测试，不是通过 |
| `security-green.log` | **7 passed / 13.05s** | T01 遗留 F1 六个参数用例 + F2 query 用例的公开 red→green |
| `final-green.log` | **1 passed / 2.97s** | 显式最终点击 → 冻结提交 → 质检 → 审查 → 发布 |
| `migration-lifecycle.log` | **9 passed / 17.31s** | 迁移与升级后 T62 |
| `event-lifecycle.log` | **6 passed / 12.99s** | T62 五项 + 升级为真实最终点击的 T65 正向 |

历史记录（保留不删）：`t02-basic.log` 为 26 passed / 2 failed，两条失败是旧 T62 以 sleep 轮询等待提交导致的观测超时，并非业务拒绝；后续改为在 adapter 提交入口等待真实事件（`SubmissionObservedVisual`）并保留退出／禁用／归属断言，不以放宽断言换取绿。

| T02 条款 | 当前证据 |
|---|---|
| 当前身份／登录上下文／revision／快照／稳定命令 ID | `FinalCommand` 严格校验；缺 `command_context`、过期或跨登录载荷、错账号、错项目、错 run 在创建尝试前拒绝；重放分支同样先做身份与会话校验 |
| 授权 + 唯一尝试 + 审计同事务，数据库约束保护唯一执行 | 单事务写 Generation/Job/绑定/授权/审计；四个唯一约束 + 命令域唯一；`test_final_transaction_fault_has_no_authorization_or_attempt_and_can_retry` 证明写点故障后无残留且可重试；`test_same_final_command_on_independent_http_apps_returns_one_attempt` 用独立 HTTP 应用竞争得到同一任务 |
| 生成记录与 adapter 请求只读冻结请求 | `test_reference_bytes_negative_prompt_and_output_match_reviewed_preview` 以请求捕获逐字段比对冻结 prompt、负向合并文本、参考字节与输出；`test_final_click_executes_only_the_frozen_request_and_publishes_same_task` 证明 adapter 实际提交的 prompt、尺寸与 brief 逐字段等同最终确认前的预览，且发布版本指向同一任务（可变字段扰动由代码路径与 `execution_request` 全等比较保证，本测试未直接注入扰动） |
| 准备与最终确认不同幂等域，同快照换命令 ID 不可重复执行 | `test_final_replays_same_command_but_new_command_cannot_repeat_or_recount`：同命令返回原任务、换命令 ID 同快照返回 `FINAL_ALREADY_AUTHORIZED`，用量仍为 1 |
| 既有质检、审查、原子发布；调用前与发布前复验 | 正向完整链路通过，结果可从原项目接口读取；`_publish` 重新执行 `ensure_job_active_in_session` 与 `execution_request` 后才写发布事实。账号禁用后的完整阻断矩阵属于 #115 |
| 失效矩阵与旧确认拒绝 | `test_final_basic_bindings_reject_stale_command_without_image_attempt` 覆盖需求、提示词批准、规则、参考与配置变化，均零图片尝试；F1/F2 另证历史提示词读取与配置 query 变化 |
| R1 接入即安全 | 未登录 401、恶意 Origin／缺 CSRF 403、跨所有者 404 在授权前判定；审计事件随同一事务提交 |
| 不改变次数与参数口径 | 最终确认不新增用量、不复受理；等待保留原槽位；无免费预览、自动超时、排队或自动重试 |

新增测试：`backend/tests/test_issue_114_t02.py`（10 项）、`backend/tests/test_issue_114_migration.py`（3 项）、`backend/tests/support/t02_prompt.py`；测试脚手架在 `backend/tests/support/`，不进入应用本体。

## 4. 可重复命令与结果

在本 worktree 的 `backend` cwd 执行，使用主代理建立的合法隔离 runner（不是原用户树 venv，也不是系统 Python 临时 PYTHONPATH）：

```powershell
$py = 'C:\Users\28425\.codex\worktrees\issue-112-integration\新建文件夹\backend\.venv\Scripts\python.exe'
Set-Location 'C:\Users\28425\.codex\worktrees\issue-112-t02\新建文件夹\backend'
$env:TEMP = Join-Path $PWD '.scratch\t02\temp'; $env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

& $py -u -m pytest -q tests/test_issue_114_t02.py tests/test_issue_114_migration.py `
  tests/test_issue_113_t01.py tests/test_issue_113_migration.py `
  tests/test_t62_background_ownership.py `
  tests/test_t65_generation_cycle.py::test_one_confirmation_creates_intent_usage_slots_and_full_cycle
```

结果 **61 passed / 169.00s**。

```powershell
& $py -u -m pytest -q tests/test_t55_auth_foundation.py tests/test_t56_account_links.py `
  tests/test_t57_sessions.py tests/test_t58_account_status.py tests/test_t59_isolation.py `
  tests/test_t60_ownership_closure.py tests/test_t61_history_recovery_gate.py `
  tests/test_t62_background_ownership.py tests/test_t63_legacy_claim.py tests/test_t64_r1_gate.py `
  tests/test_v3_p1_t04_production_reviews.py tests/test_t78_production_entry.py `
  tests/test_t79_release_package.py tests/test_v3_p1_t12_legacy_compat.py
```

结果 **131 passed、1 skipped / 203.18s**，仅 2 条既有 httpx cookies 弃用警告。

静态检查：`ruff check --no-cache` 对本票新增/修改的 25 个 Python 文件 **All checks passed**；`git diff --check` 返回 0；`0013` 迁移在 `backend/migrations` 与 `main/backend/migrations` 的 SHA-256 相同。`workflow/dispatch.py` 的 36 条 E501 是基线既有，本票不做全库格式化。

## 5. 续接的定向安全复审

接续中断的静态审查，复核了此前列为 UNKNOWN 的路径，取得以下证据（仅静态阅读，未跑测试）：

1. **入口顺序**：`confirm_prompt` 先 `require_owned_project`（含 `revalidate_principal`：禁用账号 / 会话 epoch / 撤销）与登录到期检查，再读取快照并比对命令上下文，之后才查询同命令记录；重放分支在 revision 与 `validated_request` 之后执行，未跳过当前安全校验。
2. **冻结请求到 adapter**：`_execute_claimed` 仅用 `execution_request` 返回的冻结对象构造 `GenerationRequest`；`openai_images.submit` 不读数据库或项目字段；提交前比较 `execution_request` 全等。
3. **事务语义**：`write_transaction` 为 `BEGIN IMMEDIATE` + 末尾单次 commit，异常整体 rollback；`write_audit` 只 `session.add`，随调用方事务；`_schedule` 在事务块之后。
4. **同快照唯一执行**：`existing` 按 `snapshot_id` 查询，非同一命令即 `FINAL_ALREADY_AUTHORIZED`；数据库唯一约束兜底；`_claim_generation` 只在 `queued` 且 `submit_attempted/remote_task_id/result_locator` 均为空时成功。
5. **调用前 / 发布前复验**：调用前有 `ensure_generation_active` 与 `require_authorized_attempt`；发布前 `_publish` 再执行 `ensure_job_active_in_session` 与 `execution_request`，并在写事务内二次校验。
6. **恢复路径**：`resume_interrupted` 需显式用户动作、要求 `require_authorized_job` 且原任务 `PROCESS_INTERRUPTED` 且无任何提交痕迹，不做启动时自动补调用。

未证实可报告的 P1/P2。**不等于完整审查无发现**：以下仍属未知——并发 `confirm_prompt` 争用下 `IntegrityError` 到 HTTP 状态的映射语义、`command_context` 作为只读字段的跨标签重放边界、旧无最终授权任务在 `_publish` 走旧路径时的完整内容门禁组合、以及真实供应商与多进程生产拓扑下的行为。

## 6. 残余未知与后继边界

1. 本票不覆盖 #115 的完整失效／重登矩阵、#116 的写点与并发竞争矩阵、#117 的崩溃恢复与历史升级矩阵；这些按任务图在 T02 通过后解锁。
2. 未调用真实供应商、未执行真实数据迁移、未做前端 / 浏览器 / 手机 / 视觉验收；R3 界面替换在 T09 门槛通过前不开始。
3. `docs/acceptance` 只记录本轮实际结果；历史测试通过、issue 关闭状态或父规格文本均不作为本轮完成证据。

## 7. 合并复核（集成分支，2026-11-03 11:17）

- 合并提交 `da459a4`（`git merge --no-ff codex/issue-112-t02`，父 `8a72851` 与 `39182e4`），无冲突，工作树干净。
- 在集成树复跑 T02 目标汇总：**61 passed / 165.77s**（`merge-t02-pytest-target`，日志 `backend/.scratch/merge/t02-target.log`）。
- 在集成树复跑 R1/入口回归：**132 passed / 220.57s**，0 失败（`merge-t02-r1-lead`，日志 `backend/.scratch/merge/r1-entry-lead.log`）。前一次合并复跑出现两条 `test_t79_release_package` 失败，定位为执行外壳 PATH 缺少 `git`（`tools/release_package.py:75` 的 `git ls-files` 抛 WinError 2），单独复跑 3 passed，属环境问题而非实现回归。
- 结论：T02 在集成基线上可重复通过，已在 #114 记录证据并关闭。