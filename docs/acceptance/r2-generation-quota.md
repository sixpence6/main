# R2 本人用量与管理员额度调整闭环验收记录

- Issue: #67
- 父 Issue: #52
- 分支: ticket/67
- 代码提交: e709aa63037a03a4ce25ea657de0243d13598657
- 验收日期: 2026-11-01
- 状态: 通过（R2 阶段条件；远端未知、重启恢复、本地结案与 R2 门禁见 #68、#69、#70）

## 1. 结论与边界

本票补齐 R2 的额度与用量闭环：

1. 默认限制保持 24 小时滚动窗口 20 次受理、每个账号 1 个个人活跃槽、全实例 2 个活跃槽、单项目 1 个活跃槽。前三项允许通过管理员配置覆盖，普通用户不能修改。单项目槽位按 SRS 固定为 1，不开放为可调项。
2. 服务端用量窗口为 UTC 毫秒区间 (now - 24h, now]，恰好落在下界的记录不计入；跨零点只按滑动窗口计算，不按自然日清零。刷新、重启、禁用账号都不会删除或清零已写入的用量。
3. 管理员可调整实例默认值和单个账号覆盖值；覆盖值优先于实例值，实例值优先于环境默认值。账号被提权为管理员后不获得额度豁免，管理员自己的创作任务同样占用量和槽位。
4. 降额只影响后续受理，不取消已受理意图、不删除已用量、不释放已有占用。受限请求在模型调用之前被拒绝，返回 429；系统没有隐藏队列，也不会自动排队或自动重提。
5. 管理变更和审计写入在同一个 BEGIN IMMEDIATE 事务中完成，审计失败时额度变更一并回滚，不会静默成功。事件 generation_limits_updated 记录范围、变更前后值和调整人。
6. 个人流程视图只展示当前账号自己的用量、窗口和占用；管理员额度接口只返回账号级聚合用量和限制，不返回项目 id、项目内容或生成结果。普通用户调用管理接口返回 403，伪造 owner／角色字段被 422 拒绝。
7. 前端管理页新增实例额度面板和账号额度对话框；普通用户继续在流程面板看到自己的今日已受理、剩余、窗口和个人／实例／项目占用。界面明确说明不承诺费用封顶、没有排队和自动重提。

本票只对 #67 负责的阶段条件给结论。真实模型供应商、真实生产数据和 R2 完整门禁不在本票范围内；远端未知与重启恢复见 #68，本地跟踪结案与新任务风险确认见 #69，独立连接／故障注入／全量 R2 门禁见 #70。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t67 |
| 分支 | ticket/67 |
| 基线 | integration/v3 c5f70be |
| 代码提交 | e709aa63037a03a4ce25ea657de0243d13598657 |
| 后端 Python | 3.12.13（backend/.venv） |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 数据库 | 临时 SQLite，alembic 0009 |
| 模型适配 | 测试用 LLMStub／VisualStub；smoke 为 fake adapter |
| 数据边界 | 未读取或写入 backend/data 与 main/backend/data |

## 3. 实现摘要

### 3.1 额度数据模型与迁移

新增 main/backend/app/generation_quota.py，并在两个迁移树中加入内容相同的 0009_generation_quota_limits.py：

- 表 generation_quota_limits 保存管理员配置：scope 只允许 instance 或 account，scope_id 为 instance 或账号 id；daily_limit、personal_slots、instance_slots 均可为空，空表示继承下一层。
- 约束包括 (scope, scope_id) 唯一，以及每个非空限制必须大于 0。账号行不允许写 instance_slots，实例槽位只由实例行或环境默认值决定。
- 有效值解析顺序为账号覆盖 > 实例覆盖 > Settings 默认值；project_slots 始终取 Settings.generation_project_slots。
- 迁移 0009 的 down_revision 为 0008，只新增额度表，不修改既有生成意图、用量和槽位表。两个迁移树中的 0009 文件字节一致。

### 3.2 受理与用量快照

修改 main/backend/app/acceptance.py：

- accept_in_session 通过 generation_quota.effective_limits 读取账号实际生效的每日、个人、实例和项目限制，再在原有短事务中检查滚动用量与三类活跃槽。
- 用量窗口继续使用 UTC 毫秒，条件为 window_start_ms > now_ms - 24h 且 <= now_ms；窗口下界严格排除。
- 同一意图的重放检查发生在额度检查之前，因此后续额度耗尽不会阻止原 request_id 返回原意图，也不会新增用量或调用模型。
- usage_snapshot 增加 window_started_at 和 window_expires_at，并返回 remaining、个人／实例／项目 active 与 limit，供本人流程视图和管理员账号视图共用。project_id 仍可选，但本人视图只查询当前项目归属。
- 拒绝路径在写意图、用量和槽位之前返回 429，不产生任何计数或占用，也不触发 S07／S08 或图片服务。

### 3.3 管理员额度接口与审计

修改 main/backend/app/auth/admin_router.py、security_events.py：

- GET /api/admin/generation-limits：读取实例额度覆盖、当前生效值和环境默认值。
- PUT /api/admin/generation-limits：更新实例的 daily_limit、personal_slots、instance_slots；字段可为正整数或 null（null 表示删除该覆盖并回到环境默认值），禁止额外字段。
- GET /api/admin/accounts/{account_id}/generation-limits：读取指定账号的覆盖、有效值，以及账号级聚合用量（used、remaining、窗口、个人／实例活跃数）。不返回项目 id 或项目内容。
- PUT /api/admin/accounts/{account_id}/generation-limits：更新该账号的 daily_limit、personal_slots；instance_slots 不在账号级开放。
- 管理员账号不能作为账号级额度目标，返回 404，与账号管理模块对管理员账号不可寻址的规则一致；管理员创建任务仍使用实例默认值，不获得豁免。
- 每个更新都在一个 write_transaction 中完成：重新确认管理员身份、读取目标、记录 before、写入或删除覆盖、记录 after、写 generation_limits_updated 审计。审计写入抛错时整个事务回滚。
- 审计 detail 包含 scope 以及 before／after 的覆盖值和有效值；security_events 的安全字段白名单只允许限制数值和范围，不把密码、会话或模型密钥写入事件。

### 3.4 前端

修改 main/src/pages/AdminAccountsPage.vue、api/client.ts、api/types.ts：

- 管理页增加“实例生成额度”面板，三项分别对应每日受理次数、个人活跃槽位、实例并发槽位；空白表示继承环境默认值。
- 每个非管理员账号增加“调整额度”对话框，可设置每日和个人槽位覆盖，显示当前生效值、已用／剩余、个人／实例活跃数、窗口起止，以及“不读取项目内容”“不承诺费用封顶”“无隐藏队列”的说明。
- 安全事件表识别 generation_limits_updated，显示“调整生成额度”、范围和变更前后摘要。
- 流程面板沿用 #65／#66 的本人用量展示：今日已受理、剩余、个人／实例／项目 active/limit；没有新增模型调用或自动刷新逻辑。

### 3.5 兼容性与边界

- 没有额度覆盖行时，行为与 #65 默认值完全一致；已有 generation_intents、generation_usage、generation_slots 数据不需要迁移或重算。
- 旧 v2 无 request_id 的确认路径不进入 v3 额度语义，既有 v2 回归保持通过。
- 额度是受理次数和槽位，不是金额或供应商成本上限；本票不引入计费、余额、自动扩容或队列。
- 未触碰 backend/data 与 main/backend/data；测试、迁移和 smoke 全部使用临时目录和临时 SQLite。

## 4. 执行命令与结果

### 4.1 定向后端测试

命令（cwd 为工作树 backend，PYTHONPATH 指向工作树 main/backend）：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t67-tests5 tests/test_t67_generation_quota.py
~~~

结果：5 passed in 7.95s。覆盖默认值、审计、普通用户 403、伪造字段 422、零值校验、管理员目标 404、审计故障回滚、窗口下界、跨零点、刷新／重启／禁用保持用量、降额不取消已受理、管理员无豁免和实例槽位竞争。

### 4.2 相关回归

命令范围：test_t61_history_recovery_gate.py、test_t64_r1_gate.py、test_t65_generation_cycle.py、test_t66_replay_drift.py。

结果：21 passed in 30.20s；日志 .scratch/t67-related1。新增 4 个受保护管理路由后，t61 的 PROTECTED_ROUTES 已加入对应路径，t64 门禁清单仍为 37 个操作，t15 契约测试预期操作数由 33 更新为 37。

### 4.3 全量后端回归

| 范围 | 命令要点 | 结果 |
| --- | --- | --- |
| 非 v2 全部测试 | 33 个 tests/test_*.py，排除 test_v2_*；cwd 工作树 backend，PYTHONPATH 工作树 main/backend | 323 passed, 2 warnings, 395.16s |
| 继承 v2 全部测试 | 19 个 tests/test_v2_*.py | 235 passed, 368.72s |
| 合计 |  | 558 passed，无失败、跳过或 unknown |

日志：.scratch/t67-full-nonv2-final.log、.scratch/t67-full-v2-final.log。

### 4.4 前端测试、类型检查与构建

命令（cwd 工作树根）：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：36 个文件、213 个测试通过，30.85s；日志 .scratch/t67-frontend/test.log。
- vue-tsc --noEmit：exit 0；日志 .scratch/t67-frontend/typecheck.log。
- Vite 8.3.5：1662 modules，931ms；dist/index.html 0.47 kB，dist/assets/index-DNUOfJr_.css 412.64 kB，dist/assets/index-gEyU1wgs.js 1,186.43 kB；仅有 chunk-size 警告；日志 .scratch/t67-frontend/build.log。
- 新增 tests/t67-admin-quota.spec.ts 的 3 个用例覆盖实例额度 PUT、账号额度对话框与用量、审计事件展示；t56／t58 既有账号规格补充了额度 GET mock。

### 4.5 真实 HTTP、临时 SQLite、完整 App smoke

命令：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t67-smoke/smoke.py
~~~

结果：ok=true，checks=80，base_url=http://127.0.0.1:10606，project_id=fd29fa56-bb78-4bf8-a5a4-f42855560749。结果文件 .scratch/t67-smoke/result.json，统计 .scratch/t67-smoke/stats.json，服务日志 .scratch/t67-smoke/server.log。

smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite 和完整 App；LLM／Visual 为 fake adapter。覆盖：

- 默认值为 20／1／2／1；管理员通过真实 HTTP 更新实例和账号额度；普通用户调用返回 403；伪造字段返回 422。
- 账号覆盖 daily_limit=3 后，本人流程视图显示生效额度和剩余；同一 request_id 重放仍返回原意图、used=1，不新增计数。
- 个人槽位满时第二个项目返回 429 GENERATION_SLOT_BUSY；将账号额度降到 1 后，已受理意图和 used 保留，下一次确认返回 429 GENERATION_LIMIT_REACHED，且没有新意图、用量、槽位或模型调用。
- 旧独立提交入口在存在工作流运行时返回 409 LEGACY_FLOW_DISABLED；完整链路仅一次图片提交，发布后返回精确 PNG 字节。
- 数据库最终为 1 个 delivered 意图、1 行用量、0 个活跃槽；额度行包含实例 20/1/2 与账号 1/1；审计 before=3、after=1；账号额度视图不含项目 id。

### 4.6 迁移

证据目录 .scratch/t67-migration：

- 全新库：0001 -> 0009，exit 0，alembic_version=0009，26 张业务表，含 generation_quota_limits，PRAGMA integrity_check=ok；verify-fresh.log。
- 链式升级：先到 0008，插入账号 acc-preserved，再升级 0009，exit 0；账号仍存在，新表及列存在，PRAGMA integrity_check=ok；chain-0008.log、insert.py、chain-0009.log、verify-chain.log。

### 4.7 静态检查

- ruff check（main/backend/app/generation_quota.py、acceptance.py、auth/admin_router.py、auth/security_events.py、backend/tests/test_t67_generation_quota.py）：All checks passed。
- git diff --check：exit 0，仅有工作树 CRLF 提示，没有空白错误。
- 两个迁移树中的 0009 文件均 60 行、内容一致；未修改已应用的 0001–0008。

## 5. 验收覆盖表

| 验收项 | 要求摘要 | 证据与结论 |
| --- | --- | --- |
| #67 条件 1 | 默认 24 小时 20 次、个人 1、实例 2、单项目 1；前三项正整数可配，普通用户不可调 | 默认值、管理 PUT、普通用户 403、422 与 t67 测试、smoke 均通过 |
| #67 条件 2 | UTC 毫秒窗口 (now-window, now]；下界退出；跨零点、刷新、重启、禁用不清零；降额不取消既有任务 | test_t67_generation_quota.py 的窗口边界、跨零点、刷新／重启／禁用和降额用例通过 |
| #67 条件 3 | 本人视图不泄漏他人项目；管理变更与审计原子；受限调用前拒绝，不排队、不重交、不承诺费用封顶 | 本人视图只读当前账号；账号额度视图无项目 id；审计故障回滚；429 发生在模型调用前；前端说明无费用封顶与队列 |
| #67 条件 4 | 已确认 HTTP／真实临时 SQLite、完整 App 及必要真实环境边界；提交版本、环境、命令与结果 | 80 项 smoke 使用真实 uvicorn、临时 SQLite、完整 App、fake adapter；版本、命令与结果见第 2、4 节 |
| V3-AC-17 | 普通用户伪造角色／owner 或直接调用管理能力失败；管理员读取他人创作失败，无通用超管读取分支 | 普通用户管理调用 403；extra 字段 422；管理员账号目标 404；额度接口只返回聚合值，不返回项目内容；R1 已覆盖管理员业务读取 404 |
| V3-AC-30 | 不同任务竞争个人／实例槽位及滚动边界；原子限制有效；管理员创作无豁免；降额不取消已有受理或清零记录 | 个人／实例槽位竞争用例、管理员创作受限用例、降额保留 used／intent 用例通过；事务内检查并写入 |
| V3-AC-31 | 提交前拒绝不计数；受理后失败／未知计数；核实、恢复、找回及幂等重发不另计；新任务另计；受限不自动排队 | 本票覆盖提交前拒绝、幂等重放、降额后拒绝和管理员受限；失败／未知计数与核实／恢复／找回的完整语义由 #68、#69、#70 继续验收 |
| V3-AC-34 | 必要事件和变更前后值可追踪；本人／管理员视图正确且无秘密；审计故障不静默完成敏感变更 | generation_limits_updated 包含 scope 与 before／after；安全字段白名单；审计抛错回滚测试通过；两份视图无秘密字段 |

## 6. 证据文件

- 后端测试：backend/tests/test_t67_generation_quota.py。
- 前端测试：tests/t67-admin-quota.spec.ts。
- 迁移：main/backend/migrations/versions/0009_generation_quota_limits.py、backend/migrations/versions/0009_generation_quota_limits.py。
- 定向与回归日志：.scratch/t67-tests5、.scratch/t67-related1、.scratch/t67-full-nonv2-final.log、.scratch/t67-full-v2-final.log。
- 前端日志：.scratch/t67-frontend/test.log、typecheck.log、build.log。
- 迁移证据：.scratch/t67-migration/verify-fresh.log、chain-0008.log、insert.py、chain-0009.log、verify-chain.log。
- smoke：.scratch/t67-smoke/result.json、stats.json、server.log、smoke.py、serve.py、setup.py、seed_s06.py。

## 7. 残余风险

1. 真实模型供应商的计费、远端限流和供应商侧幂等未在本票验证；本票只证明应用侧受理额度与槽位。smoke 使用 fake LLM／Visual。
2. 远端未知、进程重启、原任务核实／找回和本地跟踪结案仍由 #68、#69 承接；#70 负责独立连接、故障注入和完整 R2 门禁。本票不把未知状态当作可释放。
3. 环境默认值修改需要重启服务才能生效，且没有在线审计；管理员通过数据库覆盖的变更才有 generation_limits_updated 审计。生产变更应使用管理接口，不要直接改表。
4. 额度是受理次数与并发槽位，不是金额、token 或供应商费用上限；不提供余额、账单或自动扩容。
5. 单项目槽位按 SRS 固定为 1；本票不提供按账号调整项目槽位。实例槽位只支持全局配置，不支持按账号覆盖。
6. 管理界面只覆盖额度查看与调整；更细的运营报表、历史趋势和跨实例配额同步不在本票范围。
