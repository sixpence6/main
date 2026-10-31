# R2 本地跟踪结案与风险确认新任务验收记录

- Issue: #69
- 父 Issue: #52
- 分支: ticket/69
- 代码提交: 92f4c9d1150acb299adc5a1aa89e83b0ea4ca83a
- 验收日期: 2026-11-01
- 状态: 通过（R2 阶段条件；独立连接／故障注入／完整 R2 门禁见 #70）

## 1. 结论与边界

本票补齐 R2 的两个安全出口：本地跟踪结案，以及明确风险确认后的不同新任务。

1. 本地跟踪结案只对 GENERATION_UNKNOWN 或 RESULT_RETRIEVAL_PENDING 的 v3 任务开放。close_generation_tracking 要求 acknowledge_local_close=true，在单个 BEGIN IMMEDIATE 事务中把原 GenerationIntent 从 accepted／executing 改为 closed，释放该意图的个人／实例／项目三个槽位，并写入 generation_tracking_closed 审计。用量账本完全不变，因此原受理仍计一次；审计 before_json 记录 active_slots=3，after_json 记录 status=closed、active_slots=0，detail_json 明确 remote_cancelled=false。
2. 本地结案不取消远端任务，也不声称取消。界面和审计都明确“原受理计数已保留；本系统未取消远端任务，远端任务可能仍在执行或计费，也不会自动续发”。系统没有新增任何远端取消调用。
3. 新任务入口 start_new_generation 只在 LOCAL_TRACKING_CLOSED 状态下开放，要求 acknowledge_risk=true。它不重用原意图，而是以新的确定性 request_id 重新校验当前项目 revision、最终需求卡摘要、参考资产及用途、图片尺寸和本人账号，再经过 accept_in_session 重新检查滚动次数、个人／实例／项目槽位，成功后写入新的 intent、新的 usage 和三个新槽位，并重新走 S07 编制、独立审查、S08 提交和后续质量／交付链。
4. 新任务不是自动重试。原任务保持 unknown／closed 历史，新任务使用 attempt=2 和 previous_generation_id 关联；风险确认不能绕过槽位或额度。重复 start_new_generation 返回 409，不会重复受理；查询原任务、刷新或导航只读工作流投影，不调用模型或图片服务。
5. 旧 v2 的 acknowledge_unknown 路径不能被 v3 用来绕过本地结案。对已有 v3 intent 的 unknown 任务直接提交 submit_generation + acknowledge_unknown 会返回 409 LOCAL_CLOSURE_REQUIRED，不新增意图、用量或槽位，也不增加 provider 调用。
6. 权限、禁用和竞争均按真实账号验证：其他账号访问本项目返回 404 PROJECT_NOT_FOUND；所有者被禁用后关闭或新任务返回 401，原 intent／usage／占用保持不变；恢复账号后必须重新登录并显式操作，不会自动续发。并发两个会话同时结案得到 200／409，只有一个 closed 意图、一个用量和一条审计；并发两个会话同时确认风险新任务得到 200／409，只新增一个意图、一次用量和三个槽位。
7. 持久化明确区分原任务与新任务：结案后原 intent 状态为 closed、原 usage 保留、所有原槽位 released；新任务产生第二个 intent 和第二个 usage，新任务槽位独立占用，旧任务历史不被覆盖。图片仍必须通过 S09 质量审查和 S10 人工批准／发布才成为可交付结果，文件到达不等于发布。
8. 前端在 S08 blocked 时展示“结束本地跟踪”和“确认风险并生成新任务”，分别要求显式勾选风险说明；关闭后只显示新任务入口。管理端安全事件显示“结束本地跟踪”和“未取消远端任务”，不展示项目内容。
9. 本票新增迁移 0010，只扩展 generation_intents 的 status CHECK 约束以允许 closed，不改表结构、不重算旧数据、不改动 0001–0009。两个迁移树中的 0010 文件字节一致。

本票只对 #69 负责的 V3-AC-19、26、28、30、31、32、34 阶段条件给结论。smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite 和完整 App，但 LLM／Visual 为 fake adapter；真实供应商的远端幂等、取消、计费和结果保留策略不属于本票证据。独立连接、写点故障注入和完整 R2 门禁见 #70。未触碰 backend/data 或 main/backend/data。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t69 |
| 分支 | ticket/69 |
| 基线 | integration/v3 d240740 |
| 代码提交 | 92f4c9d1150acb299adc5a1aa89e83b0ea4ca83a |
| 后端 Python | 3.12.13（backend/.venv） |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 数据库 | 临时 SQLite，alembic 0010 |
| 模型适配 | 测试 LLMStub／VisualStub；smoke 为 fake adapter |
| 数据边界 | 未读取或写入 backend/data 与 main/backend/data |

## 3. 实现摘要

### 3.1 本地跟踪结案

修改 main/backend/app/acceptance.py，新增 close_intent_tracking：

- 只接受 accepted／executing 的 GenerationIntent；其他状态返回 409 INTENT_STATE_INVALID。
- 校验 operator_account_id 与 intent.owner_account_id 一致；不一致返回 409 OWNERSHIP_MISMATCH。
- 在单个 write_transaction（BEGIN IMMEDIATE）中统计当前活跃槽位，写入 before_json={"status","active_slots"}，设置 status=closed，调用 release_slots_in_session，写入 after_json={"status":"closed","active_slots":0}。
- 写审计 generation_tracking_closed，result=success，actor_kind=account，target_kind=generation_intent，reason_code=local_tracking_closed，detail_json 含 intent_id、reason、remote_cancelled=false。审计失败与状态变更同事务回滚。
- 不删除 GenerationUsage，不修改 Generation／GenerationJob，不调用模型或 provider。

### 3.2 风险确认的新任务

修改 main/backend/app/workflow/stages/s08.py：

- ACTIONS 增加 close_generation_tracking 和 start_new_generation；blocked GENERATION_UNKNOWN／RESULT_RETRIEVAL_PENDING 提供核实、找回、结案；LOCAL_TRACKING_CLOSED 只提供 start_new_generation；GENERATION_FAILED 只提供核实。
- close_generation_tracking 要求 acknowledge_local_close=true，且运行状态必须是上述不确定 blocked；成功后把 S08 置为 blocked LOCAL_TRACKING_CLOSED。
- start_new_generation 要求 acknowledge_risk=true，且运行状态必须是 LOCAL_TRACKING_CLOSED。它读取最新 job／generation／closed intent，允许客户端省略 previous_generation_id（由服务端取当前 job 的 generation_id），拒绝不匹配的值。
- 新 request_id 使用 uuid5(NAMESPACE_URL, v3-generation-risk:{previous_generation_id}:{prompt_artifact_id}:{final_artifact_digest})，与旧意图、旧 request_id 不同；服务端重新校验 previous Generation 属于本项目且状态 unknown、previous intent 状态 closed。
- 重跑 _require_confirmed 和 _load_prompt，要求当前 revision、卡片摘要、参考资产／用途、尺寸仍匹配；再用 accept_in_session 在短事务中检查额度与三类槽位。成功后创建新 GenerationIntent、一个 GenerationUsage、三个 GenerationSlot，并创建 attempt=2 的 GenerationJob／Generation。
- 新任务继续走 generation_cycle 的正常编制与提交；失败、未知、审查、交付仍按既有 R2 语义处理，不存在隐藏队列或自动重排。

### 3.3 兼容与既有语义

- main/backend/app/workflow/stages/s07.py 的 _require_intent 只在 run blocked LOCAL_TRACKING_CLOSED 时允许 closed；正常 S07 不允许。
- 有 v3 intent 的 unknown 任务在 _submit 中先受到 LOCAL_CLOSURE_REQUIRED 保护；无 intent 的 v2 旧路径保持原样，避免破坏继承 v2 回归。
- main/backend/app/workflow/dispatch.py 增加“结束本地跟踪”“确认风险并生成新任务”动作视图及确认要求；templates.py 增加 LOCAL_TRACKING_CLOSED 与 LOCAL_CLOSURE_REQUIRED 文案。
- main/backend/app/auth/security_events.py 允许 generation_tracking_closed 事件，安全投影允许 intent_id、reason、remote_cancelled，状态快照允许 active_slots。

### 3.4 前端

- main/src/components/workflow/WorkflowPanel.vue：close_generation_tracking 发送 {acknowledge_local_close:true}，start_new_generation 发送 {acknowledge_risk:true}；确认文案分别说明“原计数保留、不代表取消远端任务”和“新任务重新计数、占用槽位、旧任务可能仍在执行或计费”。
- main/src/api/workflow.ts 的 WorkflowIntent.status 增加 closed；blocked 视图继续展示 issue 与可用动作。
- main/src/pages/AdminAccountsPage.vue：安全事件标签“结束本地跟踪”，状态标签“本地跟踪已结束”，事件详情显示“未取消远端任务”。
- GET workflow、刷新、导航、重新登录只读；没有新增生成路由，也不主动调用 /generations。

### 3.5 迁移与兼容

- 新增 main/backend/migrations/versions/0010_r2_local_tracking_closure.py 与 backend/migrations/versions/0010_r2_local_tracking_closure.py，revision=0010，down_revision=0009。
- upgrade 使用 batch_alter_table 重建 generation_intents，替换 status CHECK 为 accepted／executing／delivered／failed／abandoned／closed；downgrade 先把 closed 映射为 abandoned，再恢复旧约束。
- 两个迁移文件字节一致（1086 bytes，SHA256 5b109a79380a729d8afc58bc66af5455bfc927dfefec9f4c8794629f8892d481）。

## 4. 执行命令与结果

### 4.1 定向测试

命令（cwd 工作树 backend，PYTHONPATH 工作树 main/backend）：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t69-tests8-final tests/test_t69_local_closure.py
~~~

结果：定向 t68+t69 共 15 passed in 22.61s（其中 t69 8 个测试）；日志 D:/Desktop/新建文件夹/.scratch/t69-target-final.log。覆盖：结案保留一次用量、释放三槽、审计前后值；同请求重复结案 409；风险新任务新增意图／用量／槽位且只提交一次；重复新任务 409；日额度耗尽 429 且无副作用；旧 acknowledge_unknown 被 409 LOCAL_CLOSURE_REQUIRED 阻断；非 owner 404；禁用 401 且恢复后显式操作；并发结案与新任务各只有一个成功。

### 4.2 全量后端回归

| 范围 | 命令要点 | 结果 |
| --- | --- | --- |
| 非 v2 全部测试 | 35 个 tests/test_*.py，排除 test_v2_*；cwd 工作树 backend，PYTHONPATH 工作树 main/backend | 338 passed, 2 warnings, 375.49s |
| 继承 v2 全部测试 | 19 个 tests/test_v2_*.py | 235 passed, 319.86s |
| 合计 |  | 573 passed，无失败、跳过或 unknown |

非 v2 日志：D:/Desktop/新建文件夹/.scratch/t69-full-nonv2-final3.log；v2 日志：D:/Desktop/新建文件夹/.scratch/t69-full-v2-final.log。

### 4.3 前端测试、类型检查与构建

命令（cwd 工作树根）：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：38 个文件、215 个测试通过，23.04s；日志 .scratch/t69-frontend-final/test.log。
- vue-tsc --noEmit：exit 0；日志 .scratch/t69-frontend-final/typecheck.log。
- Vite 8.3.5：1662 modules，740ms；dist/index.html 0.47 kB，dist/assets/index-BStgIkyN.css 412.64 kB，dist/assets/index-DEINol8H.js 1,186.99 kB；仅有 chunk-size 警告；日志 .scratch/t69-frontend-final/build.log。
- 新增 tests/t69-local-closure.spec.ts 覆盖结案和新任务确认文案、payload、无 /generations 调用；旧 v2 确认标签与 payload 由既有测试保持。

### 4.4 迁移 0010

全新链路：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m alembic -c D:/Desktop/新建文件夹/.worktrees/t69/backend/alembic.ini upgrade head
~~~

结果：0001→0010 全部成功，alembic_version=0010，26 张表，generation_intents 的 status CHECK 包含 closed，PRAGMA integrity_check=ok；证据 .scratch/t69-migration/fresh-data、fresh.log、verify-fresh.log。

链式保留：先升级到 0009，插入 project p-0010（title=Preserved project，revision=4），再升级 0010。结果：0010 成功，26 张表，p-0010 记录仍为 (p-0010, Preserved project, 4)，integrity_check=ok；证据 .scratch/t69-migration/chain-data、chain-0009.log、insert.py、chain-0010.log、verify-0010.log。

### 4.5 真实 HTTP、临时 SQLite、完整 App smoke

命令：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t69-smoke/smoke69.py
~~~

结果：ok=true，checks=47，base_url=http://127.0.0.1:5469，project_id=7fc8b422-7cff-4826-a4f7-ab2d6a4c0716。结果文件 .scratch/t69-smoke/result69.json，服务日志 .scratch/t69-smoke/server.log。

smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite 和完整 App，LLM／Visual 为 fake adapter。覆盖：

- unknown 任务阻断 S08，动作包含核实、找回、结束本地跟踪；原任务只有一次图片 submit、一次用量。
- 结案后 issue=LOCAL_TRACKING_CLOSED、只显示“确认风险并生成新任务”，intent=closed、usage 仍为 1、三槽全部释放；重复结案 409，且没有 provider 调用。
- 数据库恰有一条 generation_tracking_closed 审计，before active_slots=3、after active_slots=0、remote_cancelled=false。
- 风险新任务在新 request_id 下创建第二个 intent／usage／三槽并再提交一次；S10 needs_input 后人工 approve／publish，新 intent=delivered、槽位归零、返回精确 PNG 字节。
- 数据库最终为 closed + delivered 两个 intent、两行 usage、零活跃槽位、两个 job 历史。

## 5. 验收覆盖表

| AC | 本票阶段条件 | 证据 |
| --- | --- | --- |
| V3-AC-19 | 退出／过期与禁用不同，原任务归属不变，未知不伪称取消或自动续发 | 结案审计 remote_cancelled=false；禁用后 401 且 intent／usage／槽位不变；恢复后不自动调用，仅显式新任务 |
| V3-AC-26 | 双击／丢响应／跨标签：一个意图、一次计数、应用侧至多一次提交；变参冲突 | 重复结案 409；重复新任务 409；并发结案 200/409；并发新任务 200/409；原请求只一个 intent／usage；新任务只一次提交 |
| V3-AC-28 | 各类失败与未知保持真实状态，无自动重生、假发布、未审图预览 | 未知任务保持 unknown／closed 历史；不自动重发；新任务必须显式确认并重新走质量审查与人工发布；未审图不展示 |
| V3-AC-30 | 个人／实例／项目及窗口竞争原子有效，管理员无豁免，降额不取消或清零 | 结案与 start_new_generation 都在短事务中检查三槽；日额度=1 时新任务 429 且无新意图／用量／槽位；原 accepted 计数不因结案清除 |
| V3-AC-31 | 拒绝不计、受理后失败／未知计一次，核实／恢复／找回／重放不另计，不排队 | 结案保留 usage=1；新任务新计一次；旧 unknown ack 被阻断；额度拒绝不写行；没有队列或自动重提 |
| V3-AC-32 | 崩溃、重启与恢复不清零、不穿透槽位、不自动重发 | 结案只释放已证实本地占用并保留账本；新任务重新完整检查；#68 的独立进程崩溃／重启证据继续通过 |
| V3-AC-34 | 必要审计、前后值、正确视图及秘密隔离，审计故障回滚敏感变更 | generation_tracking_closed 同事务写 before／after／detail；本人只看到自己的用量与安全事件；管理端不含项目内容；沿用 R1 审计失败回滚模式 |

## 6. 证据文件

- 代码提交：92f4c9d1150acb299adc5a1aa89e83b0ea4ca83a。
- 定向日志：.scratch/t69-target-final.log（15 passed, 22.61s，含 t68 7 + t69 8）。
- 非 v2 全量：.scratch/t69-full-nonv2-final3.log（338 passed, 375.49s）。
- v2 全量：.scratch/t69-full-v2-final.log（235 passed, 319.86s）。
- 前端：.scratch/t69-frontend-final/test.log、typecheck.log、build.log。
- 迁移：.scratch/t69-migration/fresh-data、fresh.log、verify-fresh.log；chain-data、chain-0009.log、insert.py、chain-0010.log、verify-0010.log。
- smoke：.scratch/t69-smoke/result69.json、server.log、stats.json。
- 测试文件：backend/tests/test_t69_local_closure.py（8 tests）、backend/tests/test_t68_recovery.py（动作集合更新）、tests/t69-local-closure.spec.ts。

## 7. 残余风险

- 本地结案不是远端取消。若供应商侧任务仍在执行或计费，系统不会也不能替维护者取消；运维必须按供应商控制台和合同流程处理。
- 新任务仍是应用侧的一次新受理，不是对原远端任务的幂等重放。真实供应商的远端幂等窗口、限流、计费和结果保留策略未在本票 fake adapter 环境中验证。
- #70 仍需用独立连接、写点故障注入、进程退出、双击／丢响应／跨 tab 和完整 R2 回归验证事务边界；本票的并发测试是应用进程内两个 HTTP 会话，不等同于跨进程数据库竞争。
- 本票没有费用封顶、余额或计费承诺；额度只限制受理次数与活跃槽位。
- 结案确认来自前端显式勾选，服务端校验 acknowledge_local_close；若未来增加其他客户端，必须复用同一服务端动作，不得直接改 intent 状态。
