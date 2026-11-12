# #113 / T01：批准实际请求后持久等待

日期：2026-11-03（America/Toronto）。结论仅覆盖 **T01 隔离后端停点**，不代表 T02 最终执行、R2 整体、R3 或发布通过；不关闭 GitHub issue。

## 1. 基线、保护范围与前置

- 原始业务基线 `311b215687e8a87fa97d7c643eca5969645bb2d9`；从本地 `codex/issue-112-integration` 的 `1847af2d12d46b82685f0fbdc00c8b256a81e655` 经 `create_worktree` 建立专属 worktree，验证 `git merge-base --is-ancestor codex/issue-112-integration HEAD` 返回 0 后建立 `codex/issue-112-t01`。
- 专属根：`C:\Users\28425\.codex\worktrees\issue-112-t01\新建文件夹`。所有业务修改、测试和提交仅在此树；无原用户树重置、真实数据升级、凭据读取或真实供应商调用。未改前端。
- 已读取 AGENTS、HLD/WG/确认契约、父规格 #112、工单 #113、backend-map，以及 TDD skill、tests.md、mocking.md。沿用用户确认的 seam：已登录 HTTP/工作流、真实临时 SQLite、受控外部 adapter、独立进程重启；不模拟内部阶段来替代公开行为证明。
- **先复核、再实现**：在 `1847af2` 运行 T55—T64，**97 passed / 114.47s**。旧前端结果与后端全量基线由主代理提供，属于外部基线证据，本票未复跑前端，也不将该结果记作新停点通过。
- 后续复核了历史 xfail：`SCOPE_AND_RESPONSIBILITY` 不在当前目录。仅将 T87 的这条 fixture 改为当前已登记建议 `G03_CONFIRMATION`，删除“fixed by #90” xfail，实际复跑 #90 的公开 S04/S05 建议不误拦和未知规则拒绝：**11 passed / 15.31s**。未放宽未知规则或必审检查。

## 2. 最小实现与实际 interface

- 继续复用 `GenerationIntent / GenerationUsage / GenerationSlot` 的原子准备受理；不在预览或恢复新增次数，不另设免费预览额度、等待超时、队列或图片自动重试。
- S07 编制候选后，在独立审查**之前**生成 `effective_request`。现有图片 adapter 只有单一 `prompt` 输入，因此实际负向内容按 `combined-text-v1` 合并进最终文本；不把不会发送的独立负向字段冒充实际请求。
- 请求冻结完整已确认需求及执行需求快照、账号/项目/run/意图、实际正向/负向和合成文本、单参考资产 ID/内容 SHA-256/用途/审查依赖、单图尺寸/数量/response_format，以及无密钥的 adapter/model/service 指纹。明确无参考也以空列表参与摘要。
- 审查通过后，在同一 `BEGIN IMMEDIATE` 中重验所有者、有效账号、需求/候选/配置/参考绑定，保存独立审查、批准、`prepared_prompts` 不可变请求与摘要、工作流 `S07/needs_input`、必要 `prompt.prepared` 审计；任一写点失败则全部回滚，候选不对外可见。
- **只读出口仍是现有 GET `/api/projects/{id}/workflow`**，新增 `prompt_preview={id,status,request_digest,request,created_at,can_confirm}`。`status=awaiting_confirmation`，**本票 `can_confirm=false`**。未批准、撤销、需求变化、规则/绑定失效、参考拒绝/错版/内容变化时不给预览，prompt artifact 正文也不可绕过该门禁泄漏。
- 准备后台 driver 止于冻结等待。旧提交/重试/风险新任务/图片质检后的重新生成入口、任务创建、后台执行 claim 和可证未提交的恢复均受默认拒绝守卫约束；**T01 没有最终授权签发器，不能创建新的图片尝试**。已有提交/远端未知不被补造授权或重发，历史本地读取与原远端找回逻辑保留。
- 准备同 ID 重放不再把等待态重置为 active；S07 编制及审查的明确手动恢复均在原计数与候选预算下回到等待。读取和进程启动不自动恢复调用。
- 迁移 `0012` 及 main 中打包副本完全一致，仅新增表，不回填旧授权；SQLite 显式开启 DDL 事务，覆盖结构及 Alembic version 写入失败回滚。非空准备事实拒绝降级，避免擦除停点边界。只在临时 SQLite 测试。

## 3. TDD 与公开验收证据

首条用例完整经过 HTTP：创建项目 → 明确请求 → 需求整理/独立审查 → 创意 → 视觉 → 最终需求审查 → 明确准备 → GET。

- **RED（旧 `1847af2`）**：`test_preparation_waits_without_any_image_attempt` 在 `visual.submit_calls == []` 失败，实际记录一次 `GenerationRequest`；不是因缺字段或不存在的测试接口失败。
- **GREEN**：同一公开路径现已持久等待，`generation_job=null`、项目 `generations=[]`、受控图片 adapter 的 submit/poll/fetch 均为零。
- 后续按单纵切补测并转绿：准备重放会重置等待、S07 审查恢复直接进旧 S08、S07 编制中断恢复未回等待、失效参考/批准仍可读、SQLite version 写入失败遗留新结构。没有批量改写旧成功流程测试。

| T01 条款 | 当前证据 |
|---|---|
| PC3-01 安全读取 | 全文实际请求/负向/输出；单参考真实上传及摘要/用途；暂停外部 reviewer 时候选不可见；需求修改、批准撤销/规则/绑定失效、参考拒绝/审查失效/文件变化隐藏正文 |
| PC3-02 未最终确认零图片 | 新准备完整独立审查后等待；无 GenerationJob、Generation 列表为空、零 adapter 调用；一次 usage，三类 active 占用均为 1 |
| GET 不授权/不调用 | 多次 GET 内容稳定；SQLite 存储边界 trigger 禁止批准、准备、任务、用量、槽位的 INSERT/UPDATE，读取及旧动作仍满足安全拒绝，adapter 请求数不变 |
| 重启不穿透 | 原 ASGI app 退出后启动两个独立 uvicorn 进程，各自登录并多次真实 HTTP GET；同一快照/计数/占用，无 job；外部 adapter 任何调用均写探针并失败，最终探针日志为空 |
| PC3-07 最小未授权路径 | 旧 workflow 提交/重试/恢复/继续及未知最终确认动作、旧 HTTP generations 提交拒绝；真实旧未提交历史 fixture 经公开 recover 返回 FINAL_CONFIRMATION_REQUIRED，原尝试事实不变 |
| 原子保存 | SQLite trigger 分别故障于 prepared_prompts、approval、review、audit、waiting 状态；全部无公开预览/批准/图片任务；已有一次用量和占用保留，日志无候选正文 |
| R1 接入即安全 | 未登录 401、跨所有者 404、恶意 Origin/缺 CSRF 403；退出不改准备归属；后台冻结前禁用重验，恢复账号不自动继续或暴露未批准候选 |
| 临时升级 | 全新升级；0011 历史项目/批准逐字保留且新增事实为空；版本写入故障全回滚；空表可回退重升，非空降级拒绝且原公开预览仍可读 |

新增测试：`backend/tests/test_issue_113_t01.py`、`backend/tests/test_issue_113_migration.py`。外部 adapter/进程脚手架均在 `backend/tests/support/`，不进入 main。

## 4. 可重复命令与结果

在本 worktree 的 `backend` cwd 执行。使用主代理建立的合法隔离 runner，**不是原用户树 venv Python，也不是系统 Python 临时 PYTHONPATH 方案**。`backend/pyproject.toml` 的 pythonpath/conftest 指向本 worktree 的 `main/backend`；独立子进程继承此 runner 的依赖环境。

```powershell
$py = 'C:\Users\28425\.codex\worktrees\issue-112-integration\新建文件夹\backend\.venv\Scripts\python.exe'
Set-Location 'C:\Users\28425\.codex\worktrees\issue-112-t01\新建文件夹\backend'
$env:TEMP = Join-Path $PWD '.scratch\t01'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

& $py -m pytest -q tests/test_issue_113_t01.py tests/test_issue_113_migration.py `
  tests/test_t70_r2_gate.py::test_each_acceptance_write_fault_rolls_back_intent_usage_and_slots `
  tests/test_t70_r2_gate.py::test_confirmation_audit_failure_leaves_no_confirmation_or_authorization `
  tests/test_t70_r2_gate.py::test_independent_connections_guard_personal_instance_and_project_slots `
  tests/test_t70_r2_gate.py::test_concurrent_lost_response_replay_creates_one_intent_and_one_count
```

结果：**34 passed / 75.76s**；其中 T01 新增公开/迁移测试 28 项，继承受理事务/独立连接测试 6 项只作补充，不替代 HTTP 主证据。日志：`backend/.scratch/t01/t01-final.log`。

```powershell
& $py -m pytest -q tests/test_t55_auth_foundation.py tests/test_t56_account_links.py `
  tests/test_t57_sessions.py tests/test_t58_account_status.py tests/test_t59_isolation.py `
  tests/test_t60_ownership_closure.py tests/test_t61_history_recovery_gate.py `
  tests/test_t62_background_ownership.py tests/test_t63_legacy_claim.py tests/test_t64_r1_gate.py `
  tests/test_v3_p1_t02_adjudication.py tests/test_v3_p1_t03_review_catalogue.py `
  tests/test_v3_p1_t04_production_reviews.py tests/test_v3_p1_t12_legacy_compat.py `
  tests/test_t87_gate_regressions.py `
  -k 'not test_logout_does_not_change_persisted_task_owner_or_stop_authorized_work and not test_disable_stops_next_remote_call_and_keeps_remote_fact_without_publishing and not test_job_creation_persists_server_authorization_not_client_fields'

& $py -m pytest -q tests/test_t01_boot.py tests/test_t01_process.py `
  tests/test_t78_production_entry.py tests/test_t79_release_package.py
```

- 相关 R1/目录/内容门禁：**167 passed、3 deselected / 177.09s**，仅 2 个既有 httpx cookies 弃用警告。日志 `r1-regression-final.log`。
- **不能把 deselected 当通过**：T62 的这三条旧成功路径必须无最终点击生图，与 #112 取代的契约冲突。初次改后全 T55—T64 实跑为 94 passed / 3 failed；保留原测试不降断言、不跳过标注。T01 新增对应准备期的退出/禁用/归属公开行为；已提交图片的正向完整矩阵由 T02 接通真实最终授权后升级，本票不宣称已完成。
- 迁移/运行/打包入口：**21 passed、1 skipped / 24.84s**；skip 为前端 dist 缺失时制品构建，未为了补证运行前端 build。版本断言仅更新为真实 head 0012，不改旧业务成功路径。日志 `migration-entry-final.log`。
- Ruff 使用 `--no-cache`；除继承的 prompts.py 长行外，本票涉及 Python 文件检查通过。prompts.py 既有 55 条 E501 保留，新增加的说明分行，不顺手格式化整库。`git diff --check` 通过。

日志均在本树 `backend/.scratch/t01/`（忽略、不打包）。本文保存命令、判据与结果，不依赖临时数据库长期存在作为完成依据。

## 5. 残余未知与后继边界

1. **最终确认尚未实现**：无可执行最终确认按钮/授权/请求。T02 必须将唯一最终授权/尝试与冻结请求关联，逐字段验证实际 submit；不能删除默认拒绝后回用旧可变字段组装路径。
2. PC3-03—06、PC3-07 已提交/未知任务的完整矩阵、PC3-08/09 的完整编辑/放弃/历史语义不在本票宣称完成。等待不自动释放、不重计；明确放弃及实质修改的后继结案流程另票实施。
3. 未运行全库旧自动成功流程来宣称全绿；除必需的迁移 head 与过时 xfail 外，原成功路径测试不改。R1 的有效当前门禁已复验，不据历史 xfail 或父 issue 状态签门槛。
4. 真实供应商、多设备/HTTPS、浏览器/视觉/手机、生产数据升级、备份恢复发布演练均未取得本票证据。只使用临时数据库、测试密钥和 `.invalid` 受控 adapter；不允许因此进入 R3 或生产发布。
5. 最终实现提交和集成 tip 的合并提交见交接报告；集成分支只 merge 到本分支，未回写原用户树或擅自关闭 issue。
