# R2 竞争、回滚与不重发验收门禁记录

- Issue: #70
- 父 Issue: #52
- 分支: ticket/70
- 代码提交: 2d61d64beeb7a6106adc8ea8def990ebd3aaf64e
- 验收日期: 2026-11-01
- 状态: 通过（R2 阶段门禁条件；R3 与 Release 条件不在本票范围）

## 1. 结论与边界

本票为 R2 建立独立验收门禁，验证生成受理在竞争、故障、重放、未知、重启和结案条件下的一致性：

1. 受理事务的意图、用量、三类槽位和审计任一点写入失败时整体回滚；确认接口的审计写入失败也不会留下需求确认或生成授权。
2. 个人、实例、项目槽位使用独立 SQLAlchemy 引擎和独立连接竞争，不依赖进程内 ProjectLocks；三个维度都只有固定数量的成功者，其余请求得到 429 GENERATION_SLOT_BUSY。
3. 丢响应后的并发重放只产生一个意图、一次计数和一次应用侧提交；同请求标识变参冲突由 #66 的绑定摘要检查阻断，本票复核其不产生旁路。
4. 24 小时滚动窗口严格为 UTC 毫秒区间 (now - 24h, now]，恰好落在下界的记录不计入；跨窗口边界不会清零或重复计数。
5. 编制失败、远端未知、结果暂时无法找回、独立进程在提交前后崩溃、服务重启、本地跟踪结案和风险确认新任务，都保持真实状态和准确的计数／占用／实际调用数。
6. R2 全量回归、内容质量审批和原子发布回归全部通过，没有失败、跳过或 unknown；独立 HTTP smoke 覆盖真实 uvicorn、临时 SQLite 和完整 App，并在未知任务核实前后重启服务。
7. 旧独立确认和生成提交入口在已有 v3 WorkflowRun 后返回 409 LEGACY_FLOW_DISABLED，没有保留旁路。

边界说明：

- 测试和 smoke 使用 LLMStub／VisualStub 或 fake LLM／visual adapter，不代表真实模型供应商的质量、延迟、计费或远端行为；真实供应商调用不在本票范围。
- 数据库为临时 SQLite；独立连接竞争证明的是当前 SQLite + BEGIN IMMEDIATE 部署形态下的一致性，不替代 PostgreSQL 或其他数据库的容量／锁行为验证。
- 测试只读写临时目录，未读取或写入 backend/data 与 main/backend/data，也没有使用未授权真实数据。
- 本票只关闭 #70 的 R2 阶段条件；R3 工作台、Release 扫描和 #52 的最终整体关闭仍待后续票据。父 Issue #52 保持打开，#51 不作为替代关闭。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t70 |
| 分支 | ticket/70 |
| 基线 | integration/v3 0ff7ecc |
| 代码提交 | 2d61d64beeb7a6106adc8ea8def990ebd3aaf64e |
| 后端 Python | 3.12.13（D:/Desktop/新建文件夹/backend/.venv） |
| 测试框架 | pytest，pytest-asyncio |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 数据库 | 临时 SQLite，alembic 0010 |
| 模型适配 | 测试用 LLMStub／VisualStub；smoke 为 fake adapter |
| 数据边界 | 未读取或写入 backend/data 与 main/backend/data |

## 3. 实现摘要

本票没有修改生产代码，只新增后端门禁测试 backend/tests/test_t70_r2_gate.py。测试直接用应用真实的 accept_in_session、write_transaction、HTTP 路由和 SQLAlchemy 引擎，不替换业务实现。

### 3.1 受理写点故障注入

test_each_acceptance_write_fault_rolls_back_intent_usage_and_slots 在 accept_in_session 的同一事务上使用 SQLAlchemy after_flush 事件，分别在以下位置抛出异常：

- 意图插入后；
- 用量和三类槽位插入后；
- 审计插入后；
- 以及通过 monkeypatch 让 write_audit 直接抛错。

每个场景都断言 generation_intents、generation_usage、generation_slots 为空，且不存在 generation_intent_accepted 审计。事务回滚由 write_transaction 的 BEGIN IMMEDIATE + rollback 路径完成，不依赖后续清理。

### 3.2 确认接口审计故障

test_confirmation_audit_failure_leaves_no_confirmation_or_authorization 通过真实 HTTP 调用 S06 confirm_requirements，并让审计写入抛错。断言：

- 没有 requirements_card approval；
- 没有 GenerationIntent、GenerationUsage、GenerationSlot；
- 项目仍为 brief_status=review；
- WorkflowRun 仍在 S06 active；
- LLM 和 visual 均没有调用。

### 3.3 独立连接槽位竞争

test_independent_connections_guard_personal_instance_and_project_slots 对 personal、instance、project 三个维度分别参数化。每个场景创建两个独立 SQLAlchemy 引擎和独立连接，预热连接后用各自的 BEGIN IMMEDIATE 并发调用 accept_in_session，不使用 ProjectLocks。

断言每个维度恰好一个成功、一个 GENERATION_SLOT_BUSY；最终一个意图、一行用量、三个活跃槽。该测试直接证明数据库连接级竞争不会突破个人 1、实例 2、项目 1 的限制。

### 3.4 丢响应并发重放

test_concurrent_lost_response_replay_creates_one_intent_and_one_count 使用两个独立连接，同时以相同项目、相同 request_id 调用 accept_in_session。断言一个返回新受理、一个返回 replay，二者 intent id 相同；最终一个意图、一行用量、三个活跃槽，且没有 visual submit。

### 3.5 滚动窗口下界

test_exact_rolling_window_boundary_is_utc_millisecond_exclusive 在 T0 受理，释放槽位后分别在 T0+24h-1ms 和 T0+24h 查询用量：

- T0+24h-1ms 仍计入 used=1；
- 恰好 T0+24h 时 used=0；
- 在精确下界再次受理成功，最终两个意图、两行用量、六个槽位（第一组三个已释放）。

### 3.6 编制失败、未知、结案和新任务

- test_prompt_failure_keeps_one_count_and_slot_without_visual_call：generation_engineer 返回 503 SERVICE_UNAVAILABLE，S07 failed；一个意图、一次用量、三个槽位仍占用；一次 LLM 调用、零 visual；连续三次 GET workflow 不增加模型调用。
- test_unknown_closure_and_new_task_have_exact_counts_and_calls：visual submit 返回 unknown，S08 blocked GENERATION_UNKNOWN；verify 只读不增加调用；close 结束本地跟踪，意图 closed、用量保持 1、槽位释放、仅一条 generation_tracking_closed 审计；start_new_generation 明确风险后创建第二个意图、第二行用量、新三个槽位，第二次 visual submit，复用已批准提示词因此不增加 generation_engineer／output_reviewer 调用。
- test_legacy_confirmation_and_generation_entries_are_closed：已有 WorkflowRun 的项目调用旧 POST /confirm 和旧 POST /generations 均 409 LEGACY_FLOW_DISABLED，没有新增意图／用量／槽位。

### 3.7 安全失败与默认尺寸

- test_ownership_and_database_failures_are_safe_and_recoverable：错误 operator 调用 accept_in_session 得到 OWNERSHIP_MISMATCH 且无任何行；独立连接持有 BEGIN IMMEDIATE 时应用侧写入在 5 秒 busy timeout 后抛 OperationalError，仍无任何行；释放锁后重试成功，得到一个意图、一行用量、三个槽位。
- test_default_visual_size_is_the_single_v3_dimension：Settings.visual_image_size 默认值为 3840x2160，固定单图尺寸。

## 4. 执行命令与结果

### 4.1 门禁测试

命令（cwd 为工作树 backend，PYTHONPATH 指向工作树 main/backend）：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t70-test4 tests/test_t70_r2_gate.py
~~~

结果：12 passed in 16.83s。

### 4.2 R2 与内容审批回归

命令范围：test_t65_generation_cycle.py、test_t66_replay_drift.py、test_t67_generation_quota.py、test_t68_recovery.py、test_t69_local_closure.py、test_t70_r2_gate.py、test_v2_t12_quality.py、test_v2_t13_delivery.py。

结果：70 passed in 88.46s；basetemp D:/Desktop/新建文件夹/.scratch/t70-r2-related。覆盖 R2 受理、重放、额度、恢复、结案、门禁，以及 v2 内容质量审批和原子发布。

### 4.3 全量后端回归

| 范围 | 命令要点 | 结果 |
| --- | --- | --- |
| 非 v2 全部测试 | 36 个 tests/test_*.py，排除 test_v2_*；cwd 工作树 backend，PYTHONPATH 工作树 main/backend | 350 passed, 2 warnings, 395.85s |
| 继承 v2 全部测试 | 19 个 test_v2_*.py | 235 passed, 323.77s |
| 合计 |  | 585 passed，无失败、跳过或 unknown |

日志：.scratch/t70-full-nonv2.log、.scratch/t70-full-v2.log。

### 4.4 前端

命令（cwd 为工作树根）：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- 38 个测试文件、215 个测试通过，27.68s；
- vue-tsc --noEmit exit 0；
- Vite 8.3.5，1662 modules，742ms；dist/index.html 0.47 kB，dist/assets/index-BStgIkyN.css 412.64 kB，dist/assets/index-DEINol8H.js 1,186.99 kB。仅有 chunk size 警告和既有 fireEvent.change 警告。

日志：.scratch/t70-frontend/test.log、typecheck.log、build.log。

### 4.5 迁移

- 全新升级：alembic upgrade head，0001→0010，exit 0；alembic_version=0010，26 张表，PRAGMA integrity_check=ok，generation_intents 包含 closed 状态约束。
- 链式保留：先升级到 0009，插入 projects 行 p-0010（Preserved project，revision 4），再升级到 0010；exit 0，0010，26 张表，p-0010 保留，closed 状态约束存在，integrity_check=ok。
- 证据目录：.scratch/t70-migration；日志 fresh.log、chain-0009.log、insert.log、chain-0010.log、verify.log。

### 4.6 真实 HTTP smoke

两个 smoke 均使用真实 uvicorn、临时 SQLite、完整 App 和 fake LLM／visual adapter，监听 127.0.0.1 临时端口。

| 场景 | 命令 | 结果 |
| --- | --- | --- |
| 未知任务核实、重启、找回 | cwd .scratch/t70-smoke/restart；python scenario.py | ok=true，44 checks，base_url http://127.0.0.1:13795，project 5f61bda4-44ec-46e1-ae42-53fd73a6e720 |
| 未知任务结案、风险新任务、发布 | cwd .scratch/t70-smoke/closure；python scenario.py | ok=true，47 checks，base_url http://127.0.0.1:13943，project a3728c4e-1c6b-46c6-b54d-19998639e17c |

重启 smoke 验证：unknown 后只有 verify／recover／close；verify 不调用 provider 或模型；服务重启后同一意图、同一用量、无自动 submit／poll；显式 recover 只 poll 原 remote_task_id；最终发布精确 PNG 字节。结案 smoke 验证：结案前后槽位审计、remote_cancelled=false、无额外 provider 调用；风险确认新任务产生新意图／用量／槽位并发布精确 PNG。结果文件：.scratch/t70-smoke/restart/result68.json、.scratch/t70-smoke/closure/result69.json。

## 5. 验收覆盖表

| AC | 本票条件 | 证据 |
| --- | --- | --- |
| V3-AC-19 | 退出／过期与禁用不同，原任务归属不变，未知不伪称取消或自动续发 | 未知任务 gate 测试；t68 独立进程测试；restart smoke 中重启后同意图、无自动调用，close 审计 remote_cancelled=false |
| V3-AC-24 | 一次操作绑定真实主体／快照，确认后才编制审查，批准前无生图或虚构提示词 | 确认审计故障测试；t65 全链路；confirm 后无 visual，S07 提示词批准后才执行 |
| V3-AC-25 | revision／摘要／参考／尺寸篡改及内容漂移阻断并返回重新确认 | t66 篡改与漂移测试；t70 同请求变参不旁路；提示词漂移回 U1 |
| V3-AC-26 | 双击／丢响应／跨标签：一个意图、一次计数、应用侧至多一次提交；变参冲突 | 独立连接并发重放测试；t66 同标识变参测试；t65 稳定 request_id 测试 |
| V3-AC-27 | 导航／刷新／历史／重新登录不额外调用模型，原任务合法执行不误判 | 提示词失败测试连续 GET 不增加调用；t66 查询测试；前端刷新稳定标识 spec；restart smoke 重启不自动调用 |
| V3-AC-28 | 各类失败与未知保持真实状态，无自动重生、假发布或未审图预览 | 提示词失败、unknown、结果找回测试；t68 独立进程崩溃；t69 结案；全链路只在质量审批后发布 |
| V3-AC-29 | 单图及 3840×2160、历史尺寸快照不变、反馈新版本不覆盖 | Settings 默认尺寸测试；t65 单图链路（测试配置使用 1024×1024 时仍固定单图）；v2 t12/t13 内容审批与原子发布回归 |
| V3-AC-30 | 个人／实例／项目及窗口竞争原子有效，管理员无豁免，降额不取消或清零 | personal／instance／project 独立连接竞争测试；窗口精确下界测试；t67 降额与管理员无豁免测试 |
| V3-AC-31 | 拒绝不计、受理后失败／未知计一次，核实／恢复／找回／重放不另计，不排队 | 槽位拒绝测试；提示词失败和 unknown 测试；close/new task 精确计数；gate 重放测试；无队列路径 |
| V3-AC-32 | 崩溃、重启与恢复不清零、不穿透槽位、不自动重发 | t68 两个独立子进程崩溃测试；restart smoke 服务 kill/restart；gate 数据库锁失败后重试成功 |
| V3-AC-34 | 必要审计、前后值、正确视图及秘密隔离，审计故障回滚敏感变更 | 确认审计故障测试；结案审计 before/after active_slots；t67 generation_limits_updated 审计；t64 秘密扫描 |
| V3-AC-40 | 鉴权／库／审计／归属故障按职责安全失败，另一操作者可复现维护步骤 | 错误 operator 无行；数据库 BEGIN IMMEDIATE 锁失败无行后重试成功；确认审计故障无授权；R1 门禁与维护文档 |

## 6. 证据文件

- 门禁测试：backend/tests/test_t70_r2_gate.py（12 个测试）
- 门禁运行：.scratch/t70-test4
- R2／内容回归：.scratch/t70-r2-related（70 passed，88.46s）
- 全量非 v2：.scratch/t70-full-nonv2.log（350 passed，395.85s）
- 全量 v2：.scratch/t70-full-v2.log（235 passed，323.77s）
- 前端：.scratch/t70-frontend/test.log、typecheck.log、build.log（38 files／215 tests，typecheck 0，build 742ms）
- 迁移：.scratch/t70-migration/{fresh-data,chain-data,fresh.log,chain-0009.log,insert.log,chain-0010.log,verify.log}
- 重启 smoke：.scratch/t70-smoke/restart（44 checks，result68.json，server.log）
- 结案 smoke：.scratch/t70-smoke/closure（47 checks，result69.json，server.log）
- 代码提交：2d61d64beeb7a6106adc8ea8def990ebd3aaf64e

## 7. 残余风险

1. Gate 和 smoke 使用 fake LLM／visual adapter，无法证明真实供应商的远端任务、超时、计费或重试行为；这些必须由真实环境边界测试另行验证，不能把本票当作真实供应商验收。
2. 并发竞争只在临时 SQLite + BEGIN IMMEDIATE 上验证；换用其他数据库或连接池参数时需要重新验证锁和 busy timeout 行为。
3. 本票只验证 R2 阶段门禁。R3 工作台投影、Release 全量安全扫描和 #52 最终关闭不在范围内；#51 仍保持打开。
4. 前端回归证明现有确认、用量、恢复、结案控件没有旁路提交，但不替代真实浏览器多标签、网络丢包和人工可用性验收。
5. 未触摸 backend/data 与 main/backend/data；没有对真实历史数据执行认领或迁移。真实数据操作仍需维护者明确来源选择、有效备份和单独授权。
