# R2 原任务核实、结果找回与重启恢复验收记录

- Issue: #68
- 父 Issue: #52
- 分支: ticket/68
- 代码提交: 6befb7357078b52c6ba1f9fd4613dba7192b692b
- 验收日期: 2026-11-01
- 状态: 通过（R2 阶段条件；本地跟踪结案与新任务风险确认见 #69，R2 门禁见 #70）

## 1. 结论与边界

本票补齐 R2 的原任务核实、结果找回与重启恢复语义：

1. 受理后的提示词失败保留真实状态：S07 进入 failed，原生成意图仍为 accepted／executing，用量保持一次，个人／实例／项目槽位保持占用，图片服务没有被调用。提示词失败不是“明确终态”，不能凭此释放占用。
2. 明确终态失败与远端未知被分开处理。供应商明确拒绝或明确失败时，生成意图转为 failed，用量仍为一次，三个槽位一致释放，流程进入 S08 blocked GENERATION_FAILED；远端未知时，意图、一次用量和占用全部保留，流程进入 S08 blocked GENERATION_UNKNOWN。
3. 结果文件暂时不可取回时，流程进入 S08 blocked RESULT_RETRIEVAL_PENDING，保留意图、一次用量和占用；用户可对原 locator 再取回，不创建新意图、不增加次数，也不重新提交图片任务。
4. 新增 verify_generation 与 recover_generation 两个显式用户动作。核实只读取持久化事实，不调用模型、图片服务或计数；找回只恢复原 GenerationJob：本地可证明未提交的进程中断任务走原 job 的单次提交路径，已提交或已有远端标识的任务只 poll／fetch 原任务，不创建第二次提交。
5. 进程终止和重启不由启动流程自动补发。独立子进程在“已保存任务、尚未调度提交”与“已提交远端、尚未记录结果”两个临界点被终止并重启，重启修复只写本地事实，不调用 LLM、不 poll、不 submit；用户显式找回后仍复用原意图与原次数。
6. 前端在原任务 blocked 时展示“核实原任务”“找回原结果”安全动作，说明任务与一次计数已保留、系统不会自动重发。普通 GET／刷新只读取工作流投影，不触发模型调用；恢复登录或恢复账号也不会自动续发。
7. 本票没有新增迁移。数据库仍为 0001–0009，0008 和 0009 在两个迁移树中字节一致；生成意图、用量、槽位和任务表的既有记录无需重算。

本票只对 #68 负责的阶段条件给结论。smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite 和完整 App，但 LLM／Visual 为 fake adapter；真实供应商的远端幂等、限流、计费与结果保留策略不属于本票证据。本地跟踪结案、明确风险确认后的新任务见 #69，独立连接／故障注入／完整 R2 门禁见 #70。本票不把未知状态解释成远端未受理，也不删除任何有效回归测试。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t68 |
| 分支 | ticket/68 |
| 基线 | integration/v3 a24239c |
| 代码提交 | 6befb7357078b52c6ba1f9fd4613dba7192b692b |
| 后端 Python | 3.12.13（backend/.venv） |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 数据库 | 临时 SQLite，alembic 0009 |
| 模型适配 | 测试 LLMStub／VisualStub；smoke 为 fake adapter |
| 数据边界 | 未读取或写入 backend/data 与 main/backend/data |

## 3. 实现摘要

### 3.1 显式恢复动作

修改 main/backend/app/workflow/stages/s08.py、dispatch.py 和 templates.py：

- S08 的 ACTIONS 扩展为 submit_generation、verify_generation、recover_generation。
- verify_generation 只在 S08 blocked 且 issue 属于 PROCESS_INTERRUPTED、GENERATION_UNKNOWN、RESULT_RETRIEVAL_PENDING 或 GENERATION_FAILED 时可用；它只做状态核实，不调用任何适配器，不新增意图、用量或槽位。
- recover_generation 只在 PROCESS_INTERRUPTED、GENERATION_UNKNOWN、RESULT_RETRIEVAL_PENDING 时可用；GENERATION_FAILED 只允许核实，不提供重新生成入口。
- dispatch 增加“核实原任务”和“找回原结果”两个动作视图；前端直接使用该视图，不需要新增路由。

### 3.2 明确终态、未知与计数

修改 main/backend/app/acceptance.py 并新增 main/backend/app/generation_recovery.py：

- terminate_intent 在单个 BEGIN IMMEDIATE 事务中把 accepted／executing 意图改为 failed，释放该意图的三个槽位，并写入 generation_intent_failed 审计（result=failure，reason_code 截断到 64 字符）。用量行不删除，因此一次受理仍计入滚动窗口。
- S08 的 _settle_terminal 对 RESULT_FETCH_FAILED 特殊处理为 RESULT_RETRIEVAL_PENDING，保留意图、用量和槽位；对 ACCOUNT_DISABLED／ACCOUNT_INACTIVE 不擅自释放；其他明确终态失败调用 terminate_intent 后再进入 GENERATION_FAILED。
- 提示词失败发生在 S07，不进入 _settle_terminal；S07 保持 failed，意图和占用不动，图片服务未调用。
- recover_remote 对远端 pending 返回 pending，对 unknown 写 generation/job unknown 后进入 GENERATION_UNKNOWN，对明确 failed 写终态失败；所有分支都不增加 generation_intent 或 generation_usage 行。
- 恢复、核实、找回均不新增 request_id，也不创建新的 Generation／GenerationJob；成功找回后的质量审查与交付仍走既有 S09／S10 人工批准发布链。

### 3.3 本地未提交任务与远端任务

- resume_interrupted 只接受 job failed + PROCESS_INTERRUPTED，且 generation.submit_attempted 为空、没有 remote_task_id、没有 result_locator_json。它把原 generation 重置为 queued／waiting_slot，把原 job 重置为 submit_pending，并返回原 prompt artifact 摘要和 confirmed snapshot 摘要。
- 恢复后由既有 _schedule 重新调度同一个 GenerationJob，仍走 execute_generation 的单次 submit 路径；不会新建第二个 request，也不会再写一行用量。
- recover_remote 对已有 remote_task_id 的任务只调用一次 adapter.poll；ProviderError 的 result_available 会保存 locator；明确 not_submitted／rejected 转终态失败；pending 不写新事实；unknown 写 unknown；只有 locator 且没有远端标识时使用原 locator fetch_result。
- 结果字节落盘后重新校验 sha256、review_status 置为 quarantined，仍必须经过 S09 内容审查和 S10 人工批准发布；文件到达不等于发布。

### 3.4 崩溃、重启与启动修复

启动修复仍是 services/recovery.py 的本地事务：不调用 LLM、不调用图片适配器、不自动补发。它把仍处于 queued／running 的旧任务按可证明性分为 PROCESS_INTERRUPTED、VISUAL_OUTCOME_UNKNOWN 或其他本地终态；未知不会被当成远端未受理。

新增测试用真实独立子进程在两类临界点终止：

- 任务已创建、调度器尚未提交时：子进程读取数据库确认 job 存在后 os._exit(9)；重启修复产生 PROCESS_INTERRUPTED，用户显式找回后只提交一次原任务。
- 已提交远端、尚未记录结果时：子进程把 job 改为 submitted／remote_task_id，把 generation 改为 running／waiting_remote 后 os._exit(9)；重启修复产生 GENERATION_UNKNOWN，不自动 poll／submit；用户显式找回只 poll 原 remote_task_id，不产生第二次提交。

### 3.5 前端

修改 main/src/components/workflow/WorkflowPanel.vue、useWorkflow.ts 和 workflow API 类型：

- S08 blocked 视图显示“核实原任务”“找回原结果”；点击时调用对应 workflow action，不调用 /generations。
- 页面显示“任务与一次计数已保留”“系统不会自动重发”“一次受理计数保留，生成槽位已释放”等真实状态说明。
- GET／刷新／导航只加载 workflow 投影，不触发模型；恢复登录只恢复会话，不自动续发。

### 3.6 兼容性与边界

- v2 无 request_id 的确认路径不进入 v3 意图语义；既有 v2 未知、重启、提交重放和交付回归保持通过。
- 新增动作仅对存在 intent 的 v3 任务开放；旧 v2 任务没有 intent 时不会误走 v3 恢复分支。
- 不改动 0001–0009 已应用迁移；本票没有新表、新列或数据回填。
- 未触碰 backend/data 与 main/backend/data；测试与 smoke 全部使用临时目录和临时 SQLite。

## 4. 执行命令与结果

### 4.1 定向恢复测试

命令（cwd 为工作树 backend，PYTHONPATH 指向工作树 main/backend）：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t68-test7-final tests/test_t68_recovery.py
~~~

结果：7 passed in 9.56s。日志 D:/Desktop/新建文件夹/.scratch/t68-test7.log。覆盖提示词失败、unknown 核实只读、找回原 remote_task_id、结果文件暂时不可取回、进程中断恢复、明确终态失败释放槽位、独立进程提交前崩溃与提交后崩溃。

### 4.2 相关 R2 回归

命令范围：test_t65_generation_cycle.py、test_t66_replay_drift.py、test_t67_generation_quota.py、test_t68_recovery.py。

结果：23 passed in 28.43s；日志 D:/Desktop/新建文件夹/.scratch/t68-related-final.log。

### 4.3 全量后端回归

| 范围 | 命令要点 | 结果 |
| --- | --- | --- |
| 非 v2 全部测试 | 34 个 tests/test_*.py，排除 test_v2_*；cwd 工作树 backend，PYTHONPATH 工作树 main/backend | 330 passed, 2 warnings, 350.56s |
| 继承 v2 全部测试 | 19 个 tests/test_v2_*.py | 235 passed, 309.37s |
| 合计 |  | 565 passed，无失败、跳过或 unknown |

非 v2 证据目录：D:/Desktop/新建文件夹/.scratch/t68-full-nonv2-final3；v2 日志：D:/Desktop/新建文件夹/.scratch/t68-full-v2-final.log。

### 4.4 前端测试、类型检查与构建

命令（cwd 工作树根）：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：37 个文件、214 个测试通过，22.15s；日志 .scratch/t68-frontend-final/test.log。
- vue-tsc --noEmit：exit 0；日志 .scratch/t68-frontend-final/typecheck.log。
- Vite 8.3.5：1662 modules，738ms；dist/index.html 0.47 kB，dist/assets/index-Dkab_Qvv.css 412.64 kB，dist/assets/index-Bgq-2Bd2.js 1,186.43 kB；仅有 chunk-size 警告；日志 .scratch/t68-frontend-final/build.log。
- 新增 tests/t68-workflow-recovery.spec.ts 覆盖 blocked 状态的安全恢复动作、无 /generations 提交和“任务与一次计数已保留”提示。

### 4.5 真实 HTTP、临时 SQLite、完整 App smoke

命令：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t68-smoke/smoke68.py
~~~

结果：ok=true，checks=44，base_url=http://127.0.0.1:9865，project_id=56c18ff2-9a17-4ffa-a8e3-267542699d43。结果文件 .scratch/t68-smoke/result68.json，服务日志 .scratch/t68-smoke/server.log。

smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite、完整 App；LLM／Visual 为 fake adapter，并在核实与找回之间重启 uvicorn。覆盖：

- 确认后任务返回 unknown：S08 blocked GENERATION_UNKNOWN，动作包含核实与找回，只有一次 submit、没有 poll、一次用量。
- 核实动作不调用 provider，也不改变用量或槽位。
- 终止并重启 uvicorn 后同一数据库仍为 blocked GENERATION_UNKNOWN；启动过程不自动 submit／poll，一次意图和一次用量保持不变。
- 切换 fake adapter 为 success 并释放 gate 后，找回只 poll 原 remote_task_id，没有第二次 submit；进入 S10 needs_input。
- 人工批准和发布后流程 completed，意图 delivered，槽位释放，返回精确 PNG 字节；数据库中只有一个 delivered 意图、一行用量、零活跃槽和一个 published delivery。

### 4.6 迁移与数据库

- 本票没有新增迁移；两个迁移树仍为 0001–0009。
- 0008_r2_generation_acceptance.py：7416 字节，SHA256 1daf3c11e773cd8a0aaaab807a3ac2b2c2beae2c2a53cf054832e7faff762282。
- 0009_generation_quota_limits.py：1974 字节，SHA256 0abd0449a25a90c5a855df02d8b5593652909200f11d9c423882dd1b573523ea。
- 两个迁移树中的 0008、0009 内容一致；smoke 数据库 alembic_version=0009，26 张业务表，PRAGMA integrity_check=ok。

### 4.7 静态检查

- ruff check（acceptance.py、generation_recovery.py、s08.py、test_t68_recovery.py、test_t66_replay_drift.py）：All checks passed。
- git diff --check：exit 0；没有空白错误，只有工作树 CRLF 提示。
- compileall app：exit 0。

## 5. 验收覆盖表

| 验收项 | 要求摘要 | 证据与结论 |
| --- | --- | --- |
| #68 条件 1 | 提示词失败、明确终态失败、远端未知保留真实状态和一次计数；仅可证终态释放槽位 | 7 个定向测试分别断言 S07 failed、GENERATION_FAILED、GENERATION_UNKNOWN、RESULT_RETRIEVAL_PENDING；明确终态释放三个槽位，未知和取回失败保留 |
| #68 条件 2 | 核实、恢复原任务和找回不新增意图／次数；未知不因超时自动释放或伪称未受理 | verify 只读；recover 复用原 job／intent／usage；未知分支不写已受理伪事实，不自动释放；测试与 smoke 均验证 intent=1、usage=1 |
| #68 条件 3 | 独立进程在提交／调度、模型提交及本地记录临界终止并重启；UI 安全恢复；恢复账号／会话不自动调用 | test_t68_recovery.py 两个独立子进程测试覆盖提交前与提交后临界；前端 spec 覆盖恢复动作；GET／刷新不调用模型；smoke 重启 uvicorn 后不自动 submit／poll |
| #68 条件 4 | 已确认 HTTP／真实临时 SQLite、完整 App 及必要真实环境边界；提交版本、环境、命令与结果 | 44 项 smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite、完整 App 和 fake adapter；版本、命令与结果见第 2、4 节 |
| V3-AC-19 | 退出／过期与禁用不同；原任务归属不变；禁用阻止下一模型调用和发布；已发远端不伪称取消；恢复不自动再调用 | R1／#62 已覆盖禁用和后台归属；本票保证恢复不改 owner／intent，unknown 不伪称取消，重启不自动调用 |
| V3-AC-27 | 导航／刷新／历史／重新登录不额外调用模型；原任务合法执行不误判 | workflow GET 与 verify 测试断言无 LLM／Visual 调用；前端 spec 断言无 /generations；原任务恢复走原 job，不新建意图 |
| V3-AC-28 | 确认失败、提示词失败、远端未知、审核不可用、文件丢失保留真实状态；无自动重生、假发布或未审图预览 | 本票覆盖提示词失败、未知、结果取回失败和终态失败；S09／S10 仍要求审查和人工批准；quarantined 资产不预览、不发布 |
| V3-AC-31 | 提交前拒绝不计数；受理后失败／未知计一次；核实／恢复／找回／重放不另计；新受理任务另计；受限不自动排队 | 本票 7 个测试和 #65／#67 回归证明：失败／未知保持一次，核实／恢复／找回不新增；#69 承接风险确认新任务，系统无队列 |
| V3-AC-32 | 受理后崩溃、重启或恢复不清零、不穿透槽位、不重发生图；未知不自动解释为远端未受理 | 两个独立进程测试和重启 smoke 均验证 intent=1、usage=1、槽位不动、无自动 submit／poll；恢复只 poll 原任务 |

## 6. 证据文件

- 后端测试：backend/tests/test_t68_recovery.py；兼容性调整：backend/tests/test_t66_replay_drift.py。
- 前端测试：tests/t68-workflow-recovery.spec.ts。
- 生产代码：main/backend/app/generation_recovery.py、main/backend/app/acceptance.py、main/backend/app/workflow/stages/s08.py、main/backend/app/workflow/dispatch.py、main/backend/app/workflow/templates.py。
- 定向日志：.scratch/t68-test7.log；相关回归：.scratch/t68-related-final.log。
- 全量后端：.scratch/t68-full-nonv2-final3（非 v2）、.scratch/t68-full-v2-final.log（v2）。
- 前端：.scratch/t68-frontend-final/test.log、typecheck.log、build.log。
- smoke：.scratch/t68-smoke/result68.json、server.log、smoke68.py、serve.py、setup.py、seed_s06.py、data/app.db。
- 迁移：main/backend/migrations/versions/0008_r2_generation_acceptance.py、0009_generation_quota_limits.py 及 backend 对应文件。

## 7. 残余风险

1. 真实模型供应商的远端任务保留、poll 语义、限流、计费和供应商侧幂等没有在本票验证；smoke 使用 fake LLM／Visual。真实未知仍必须人工核实，不能把本票结果外推为供应商保证。
2. 结果找回是显式用户动作，不是后台轮询或自动重试；用户不点击时任务保持 blocked，不会自动恢复。
3. 远端任务过期后 provider 可能返回失败；系统会把可证明失败转为 GENERATION_FAILED，但不会猜测远端是否仍可找回。
4. 本地跟踪结案、审计后的风险确认新任务、独立连接竞争和完整 R2 故障注入由 #69、#70 继续验收；本票不把未知当结案。
5. 进程终止测试使用独立 Python 子进程模拟临界退出，smoke 使用真实 uvicorn 重启；两者都不是生产级 chaos 平台，生产部署仍应保留进程监控和数据库备份。
6. 恢复动作没有新增迁移；回滚代码不会删除已有的 intent／usage／slot／job 行，未知和 blocked 行必须保留，不能靠删表“修复”。
