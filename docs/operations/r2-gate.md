# R2 竞争、回滚与不重发门禁操作说明

- Issue: #70
- 父 Issue: #52
- 分支: ticket/70
- 代码提交: 2d61d64beeb7a6106adc8ea8def990ebd3aaf64e
- 验收日期: 2026-11-01
- 状态: 通过（R2 阶段门禁）

## 1. 适用范围

本说明供维护者复现 #70 的 R2 门禁。门禁验证的是：

- 受理事务在意图、用量、槽位和审计写点失败时全部回滚；
- 个人、实例、项目槽位在独立数据库连接并发时只允许规定数量的成功者；
- 相同 request_id 的丢响应并发重放只产生一个意图、一次计数和一次应用侧提交；
- 24 小时窗口在精确 UTC 毫秒下界的行为；
- 提示词失败、远端未知、结果暂时无法找回、服务重启、本地跟踪结案和风险确认新任务的计数与调用数；
- 旧独立确认／生成入口在 v3 流程存在后关闭；
- 内容质量审批与原子发布回归。

本门禁不验证真实模型供应商、真实历史数据认领或 R3 工作台。真实数据操作必须由维护者选择来源、完成有效备份并另行授权。

## 2. 前置条件

| 项目 | 要求 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t70 |
| Python | D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe（3.12.13） |
| Node.js | v26.12.1 |
| 数据库 | 临时 SQLite；迁移 head 为 0010 |
| 模型 | LLMStub／VisualStub 或 fake adapter；不得连接真实供应商 |
| 数据 | 只使用 .scratch 下的临时目录；不得指向 backend/data 或 main/backend/data |

设置环境变量（PowerShell 示例）：

~~~
$env:PYTHONPATH = "D:/Desktop/新建文件夹/.worktrees/t70/main/backend"
Set-Location "D:/Desktop/新建文件夹/.worktrees/t70/backend"
~~~

测试使用的 APP_DATA_DIR、WEB_DIST_DIR 和数据库由 pytest fixture 或 smoke 脚本创建在临时目录；不要手工把 APP_DATA_DIR 指向真实数据根。

## 3. 门禁测试

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t70-test4 tests/test_t70_r2_gate.py
~~~

预期：12 passed。测试名和目的：

| 测试 | 目的 |
| --- | --- |
| test_each_acceptance_write_fault_rolls_back_intent_usage_and_slots | 在意图、用量／槽位、审计各写点注入异常，断言无残留 |
| test_confirmation_audit_failure_leaves_no_confirmation_or_authorization | 确认接口审计失败，断言无 approval／intent／usage／slot 且无模型调用 |
| test_independent_connections_guard_personal_instance_and_project_slots | 独立引擎竞争个人／实例／项目槽位，各一个成功、一个 429 |
| test_concurrent_lost_response_replay_creates_one_intent_and_one_count | 独立连接同 request_id 并发，一个受理一个 replay，一个意图一次用量 |
| test_exact_rolling_window_boundary_is_utc_millisecond_exclusive | T0+24h-1ms 计入、T0+24h 不计入，下界可重新受理 |
| test_prompt_failure_keeps_one_count_and_slot_without_visual_call | S07 失败保留一次计数和槽位，零 visual，GET 不增加模型调用 |
| test_unknown_closure_and_new_task_have_exact_counts_and_calls | 未知核实只读、结案保留计数、风险新任务新计数 |
| test_legacy_confirmation_and_generation_entries_are_closed | 旧确认／旧生成入口 409 LEGACY_FLOW_DISABLED |
| test_ownership_and_database_failures_are_safe_and_recoverable | 错误 operator、SQLite BEGIN IMMEDIATE 锁失败均无残留，释放后重试成功 |
| test_default_visual_size_is_the_single_v3_dimension | 默认生成尺寸为 3840x2160 |

注意：槽位测试不使用 ProjectLocks。不要为了让测试通过而放宽槽位限制、切换成内存锁或删除竞争用例；这些测试的目的就是证明数据库连接级一致性。

## 4. R2 与内容审批回归

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t70-r2-related tests/test_t65_generation_cycle.py tests/test_t66_replay_drift.py tests/test_t67_generation_quota.py tests/test_t68_recovery.py tests/test_t69_local_closure.py tests/test_t70_r2_gate.py tests/test_v2_t12_quality.py tests/test_v2_t13_delivery.py
~~~

预期：70 passed。该范围覆盖 R2 受理、重放／漂移、额度、恢复、结案、门禁，以及 v2 内容质量审批和原子发布。

## 5. 全量回归

非 v2 测试要逐个列出 tests/test_*.py，排除 test_v2_*；Windows 下 shell glob 不一定会展开。PowerShell 示例：

~~~
$files = Get-ChildItem tests/test_*.py | Where-Object { $_.Name -notlike 'test_v2_*' } | ForEach-Object { $_.FullName }
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t70-full-nonv2 $files
~~~

预期：350 passed, 2 warnings，约 396 秒。

~~~
$files = Get-ChildItem tests/test_v2_*.py | ForEach-Object { $_.FullName }
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t70-full-v2 $files
~~~

预期：235 passed，约 324 秒。合计 585 passed，不应出现 failed、skipped 或 unknown。

## 6. 前端

在工作树根执行：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

预期：38 files / 215 tests passed；vue-tsc exit 0；Vite build 成功。构建出现 chunk size 警告是已知的非阻断提示。

## 7. 迁移检查

迁移 head 是 0010。检查两个迁移树中的 0010 文件内容一致：

~~~
Get-FileHash main/backend/migrations/versions/0010_r2_local_tracking_closure.py
Get-FileHash backend/migrations/versions/0010_r2_local_tracking_closure.py
~~~

全新库：

~~~
$env:APP_DATA_DIR = "D:/Desktop/新建文件夹/.scratch/t70-migration-fresh/data"
$env:WEB_DIST_DIR = "D:/Desktop/新建文件夹/.scratch/t70-migration-fresh/web"
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m alembic -c D:/Desktop/新建文件夹/.worktrees/t70/backend/alembic.ini upgrade head
~~~

预期 alembic_version=0010，26 张表，PRAGMA integrity_check=ok。

链式保留：先升级到 0009，插入一行项目数据，再升级到 0010，确认该行仍在。参考 .scratch/t70-migration 的 fresh.log、chain-0009.log、insert.log、chain-0010.log、verify.log。

## 8. 真实 HTTP smoke

两个 smoke 都以 fake LLM／visual 运行真实 uvicorn、临时 SQLite 和完整 App；重启 smoke 会在 unknown 核实与 recover 之间停止并重启服务。

~~~
Set-Location D:/Desktop/新建文件夹/.scratch/t70-smoke/restart
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe scenario.py
~~~

预期 ok=true，44 checks，结果 result68.json。

~~~
Set-Location D:/Desktop/新建文件夹/.scratch/t70-smoke/closure
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe scenario.py
~~~

预期 ok=true，47 checks，结果 result69.json。smoke 必须使用临时 APP_DATA_DIR、临时 WEB_DIST_DIR 和 free port；若出现 ORIGIN_INVALID，检查 APP_PUBLIC_URL 是否与监听端口一致。

## 9. 失败处理

1. 槽位竞争失败：先确认测试是否使用独立引擎和 BEGIN IMMEDIATE；不要用 ProjectLocks 掩盖。若出现两成功，检查 accept_in_session 的额度查询和 generation_slots 写入是否在同一事务。
2. 审计故障回滚失败：检查 write_audit 是否在同一 session 中；确认 write_transaction 在异常时 rollback，而不是提前 commit。
3. 数据库锁测试超时：确认 SQLite busy_timeout=5000；该测试故意等待锁释放，耗时可接受。不要缩短到 0 或去掉该测试。
4. 未知／重启测试出现自动 submit／poll：检查 generation_cycle 是否在 startup 时被触发；startup repair 只允许本地状态修复，不得调用 provider 或模型。
5. 旧入口 409 失败：确认项目已有 WorkflowRun 且 ensure_legacy_entry_available 生效；不要通过删除 run 或改 owner 绕过。
6. 前端测试失败：先运行 npm run typecheck 区分类型错误和运行时错误；不要删除既有确认／用量／恢复 spec 来降低门槛。

## 10. 证据与约束

- 本票只新增 backend/tests/test_t70_r2_gate.py；没有生产代码或迁移变更。
- 证据必须包含代码提交、临时 SQLite、命令、退出码、通过数量和日志路径。
- 禁止把 APP_DATA_DIR 指向 backend/data 或 main/backend/data；禁止读取、写入或清理真实数据根。
- 不承诺真实供应商费用封顶；本门禁的计数是受理次数和槽位，不是金额账单。
- 本票不关闭 #51，也不宣称 #52 已整体完成；R3 和 Release 条件仍由后续票据验证。
