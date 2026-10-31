# R2 原意图重放与需求漂移安全反馈验收记录

- Issue: #66
- 父 Issue: #52
- 分支: ticket/66
- 代码提交: 3ca3e839cc52d8b9ddc8423b11bed5b40c3e8018
- 验收日期: 2026-11-01
- 状态: 通过（R2 阶段条件；未知恢复、重启与 R2 门禁见 #68、#70）

## 1. 结论与边界

本票补齐了 #65 生成闭环中“确认之后的原意图重放”和“需求漂移”两个缺口：

1. 确认请求现在携带稳定 request_id。相同项目、相同 request_id、相同卡片绑定与参数会返回原生成意图，不再新建授权、计数或占用；同一 request_id 改尺寸、参考用途或卡片摘要会返回冲突，绝不静默复用旧授权。
2. 确认入口在读取已有批准之前先验证客户端回显的卡片摘要、artifact_id、requirement_revision、尺寸、参考用途与参考图。篡改字段不会进入审批或意图写入。
3. S07 对提示词覆盖值执行精确等值校验，并增加确定性的落地检查：未在已确认需求卡中出现的新卖点、价格、引号文字、大写宣传词或新限制会阻断编制。发现漂移时不创建 prompt_plan artifact、不创建 generation_prompt 批准、不调用图片服务。
4. 漂移时原意图被标记 abandoned，个人／实例／项目占用在同一事务中释放，已受理的一次用量保留；流程进入 S07 blocked，issue_code 为 PROMPT_REQUIRES_RECONFIRMATION，并向用户展示“修改需求”入口。修改需求后回到 S04，重新核对并确认才可再次生成。
5. 前端 request_id 使用 localStorage 稳定键，刷新、导航和重新登录后仍复用同一标识；409 冲突与漂移提示在面板中可见。GET／刷新只读取流程视图，不触发模型调用。
6. 旧 v2 无 request_id 的确认路径保持兼容；本票不新增 HTTP 路由，不修改数据库 schema，alembic head 仍为 0008。

本票只对 #66 负责的阶段条件给结论。真实模型供应商、真实生产数据和 R2 完整门禁不在本票范围内；远端未知、重启恢复、额度管理界面和 R2-GATE 分别由 #68、#67、#70 承接。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t66 |
| 分支 | ticket/66 |
| 基线 | integration/v3 5b3719b |
| 代码提交 | 3ca3e839cc52d8b9ddc8423b11bed5b40c3e8018 |
| 后端 Python | 3.12.13（backend/.venv） |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 数据库 | 临时 SQLite，alembic 0008 |
| 模型适配 | 测试用 LLMStub／VisualStub；smoke 为 fake adapter |
| 数据边界 | 未读取或写入 backend/data 与 main/backend/data |

## 3. 实现摘要

### 3.1 S06：精确请求绑定与原意图重放

修改 main/backend/app/workflow/stages/s06.py：

- _client_binding 现在同时校验可选的 artifact_digest／card_digest、artifact_id、requirement_revision、size、reference_usage、reference_asset_id，并在读取 latest requirements_card 批准之前执行。客户端回显只作为断言，不作为授权来源。
- _confirm_atomic 重新计算 intent_request_digest，并用 intent_for_request(project_id, request_id) 精确查找原意图。摘要一致且状态为 accepted／executing／delivered 时返回原意图；摘要不一致返回 409 INTENT_REPLAY_CONFLICT；已结束或失效状态返回 409 INTENT_STATE_INVALID。
- 删除原先按项目＋运行取“最近一个意图”的复用逻辑。该逻辑会让同一卡片上的变参请求静默拿到旧授权，是本票修复的核心缺陷。
- 同一事务内仍重验 owner、账号可登录、项目 revision、运行 requirement_revision、卡片批准状态与参考图归属；事务提交前不进行任何模型网络调用。

### 3.2 S07：覆盖值精确校验与提示词落地检查

修改 main/backend/app/workflow/stages/s07.py，新增 main/backend/app/prompt_guard.py：

- requirement_coverage 的每个覆盖值必须与硬要求原文完全相等，并且该值必须出现在对应 positive_prompt／negative_prompt 中。以前只要求“值是非空子串”，缩短或弱化要求可蒙混通过；现在会返回 PROMPT_INVALID，并在存在 v3 意图时转为重新确认。
- prompt_drift_errors 按中文分号、换行和句号切分提示词，逐句要求在已确认卡片片段与安全连接词之外没有残留内容；同时检查风险卖点词、数字价格、引号文字和大写宣传词。它是确定性补充，不替代独立 output_reviewer 的语义审查。
- 漂移处理调用 acceptance.abandon_intent：一个写事务内把意图置为 abandoned、释放全部活跃占用、写入 generation_intent_abandoned 审计，用量账本不变。随后 S07 置为 blocked，issue_code／template_id 为 PROMPT_REQUIRES_RECONFIRMATION，记录诊断并返回 409。
- 漂移路径不会创建 prompt_plan artifact，不会创建 generation_prompt 批准，不会进入 S08，也不会向图片服务提交。收到提示后只能修改需求并重新确认，不能复用旧授权。
- dispatch._views_for 在 S07 active 或 blocked 且 issue 为 PROMPT_REQUIRES_RECONFIRMATION 时暴露 revise_requirements；既有路由的 revise_after_confirmation 允许该阶段动作，修改需求后回到 S04。

### 3.3 前端：稳定标识、冲突提示与只读查询

修改 main/src/composables/useWorkflow.ts 与 main/src/components/workflow/WorkflowPanel.vue：

- useWorkflow 增加 errorCode，并在 409 后重新加载视图时保留服务端错误码，避免错误提示被刷新覆盖。
- WorkflowPanel 对 INTENT_REPLAY_CONFLICT、PROMPT_REQUIRES_RECONFIRMATION、REQUIREMENT_REVISION_CONFLICT、REQUIREMENTS_CHANGED 显示对应安全提示；漂移提示明确“本次未提交生图”，并引导修改需求后重新确认。
- 确认 request_id 的 localStorage 键由 project_id、run_id、requirement_revision、final brief digest 组成；刷新、导航、重新登录同一项目时复用，不生成新的意图。意图进入 delivered／failed／abandoned 后清除该键。
- 浏览器禁用存储时回退到组件内存标识，不阻塞确认；正常浏览器路径使用持久键。
- 流程 GET 只读取数据库投影，不调用任何模型；已授权后台执行由独立任务推进，与页面刷新无关。

### 3.4 兼容性与数据边界

- v2 无 request_id 的确认仍走原非原子兼容路径；有 request_id 且项目有 owner 时才启用 v3 意图与额度语义。S07 的漂移守卫只在存在 v3 意图时执行，因此 v2 既有审查测试保持原行为。
- 本票没有迁移文件变更。两个迁移树中的 0008 文件均为 7416 字节，SHA256 均为 1daf3c11e773cd8a0aaaab807a3ac2b2c2beae2c2a53cf054832e7faff762282。
- 未触碰 backend/data 和 main/backend/data；所有测试与 smoke 使用临时目录和临时 SQLite。

## 4. 执行命令与结果

### 4.1 定向后端测试

命令（cwd 为工作树 backend，PYTHONPATH 指向工作树 main/backend）：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t66-target-final tests/test_t66_replay_drift.py
~~~

结果：6 passed in 8.33s。日志 .scratch/t66-logs/targeted.log。

覆盖：跨客户端重放、篡改绑定、同标识变尺寸、跨账号标识、提示词新增卖点、弱化覆盖值。

### 4.2 全量后端回归

| 范围 | 命令要点 | 结果 |
| --- | --- | --- |
| 非 v2 全部测试 | 31 个 tests/test_*.py，排除 test_v2_*，cwd 工作树 backend，PYTHONPATH 工作树 main/backend | 318 passed, 2 warnings, 427.84s |
| 继承 v2 全部测试 | 19 个 tests/test_v2_*.py | 235 passed, 411.60s |
| 合计 |  | 553 passed，无失败、跳过或 unknown |

日志：.scratch/t66-logs/full-nonv2.log、.scratch/t66-logs/full-v2.log。

### 4.3 前端测试、类型检查与构建

命令（cwd 工作树根）：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：35 个文件、210 个测试通过，28.14s；日志 .scratch/t66-logs/frontend-test.log。
- vue-tsc --noEmit：exit 0；日志 .scratch/t66-logs/frontend-typecheck.log。
- Vite 8.3.5：1662 modules，1.48s；dist/index.html 0.47 kB，dist/assets/index-CoQKJWll.css 412.64 kB，dist/assets/index-Dq3_pPgX.js 1,178.56 kB；仅有 chunk-size 警告；日志 .scratch/t66-logs/frontend-build.log。
- 新增 tests/t66-workflow-replay.spec.ts 的 3 个用例覆盖稳定标识、冲突提示、漂移修改需求入口；每个用例均断言没有 POST /generations。

### 4.4 真实 HTTP、临时 SQLite、完整 App smoke

命令：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t66-smoke/smoke.py
~~~

结果：ok=true，checks=88，base_url=http://127.0.0.1:5943，project_id=70e62a29-76fd-4e24-980a-feae83cdcc97。结果文件 .scratch/t66-smoke/result.json。

smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite 和完整 App；LLM／Visual 为 fake adapter。覆盖：

- 两个独立会话重放同一 request_id：同一个意图、一次用量、无额外模型调用、应用侧至多一次图片提交；
- GET 刷新不增加模型调用；
- 同标识改尺寸返回 409；篡改卡摘要返回 CARD_DIGEST_MISMATCH；篡改 revision 返回 REQUIREMENT_REVISION_CONFLICT；
- 占用中新建不同 request_id 返回 429 GENERATION_SLOT_BUSY；
- 漂移项目确认后 S07 blocked PROMPT_REQUIRES_RECONFIRMATION，意图 abandoned、用量保留、占用释放、不新增图片提交，修改需求动作返回 200 并进入 S04。

### 4.5 静态检查与迁移边界

- ruff check main/backend/app/acceptance.py main/backend/app/prompt_guard.py main/backend/app/workflow/stages/s06.py main/backend/app/workflow/stages/s07.py backend/tests/test_t66_replay_drift.py：All checks passed，exit 0；日志 .scratch/t66-logs/ruff.log。
- git diff --check：clean，exit 0；日志 .scratch/t66-logs/diff-check.log。
- alembic heads：0008 (head)；日志 .scratch/t66-logs/alembic-heads.log。git status 对两个 migrations/versions 目录无变更。
- dispatch.py、templates.py 的既有 E501 等基线不在本票新增行中；本票修改的 dispatch 条件已避免新增 SIM102。

## 5. 验收覆盖表

| AC | 要求摘要 | 本票证据 | 结论 |
| --- | --- | --- | --- |
| V3-AC-24 | 一次明确主操作绑定真实账号与需求快照；确认后才编制／审查提示词；批准前无生图；不展示虚构的预先最终提示词 | 确认事务写 approval＋intent＋usage＋slots；S07 在 S08 前编译和独立审查；单测断言确认时无 visual 调用；前端只展示流程结果，不展示未生成的最终提示词 | 阶段条件通过 |
| V3-AC-25 | revision／卡片摘要／参考归属／尺寸篡改或门禁未满足时不受理；需求变化回 U1 重新确认 | test_tampered_bindings_are_rejected_without_intent_or_approval、test_same_request_with_changed_size_does_not_reuse_intent、test_cross_account_request_identifier_cannot_reuse_project；漂移项目 revise_requirements 回 S04 | 阶段条件通过 |
| V3-AC-26 | 双击／丢响应／跨标签重放同请求：一个意图、一次计数、应用侧至多一次提交；同标识变参拒绝 | 跨客户端顺序重放断言一个 intent、一次 usage、3 个占用、一次 visual submit；同标识变尺寸 409；smoke 两独立会话复验 | 阶段条件通过；真正并发竞争由 #70 门禁复核 |
| V3-AC-27 | 导航／刷新／历史／重新登录不额外调用模型，原任务合法执行不误判 | GET 前后 role_calls 不增加；跨客户端重新登录不触发模型；刷新复用 localStorage request_id；后台任务独立推进，页面刷新不改变所有权 | 阶段条件通过 |
| V3-AC-28 | 确认失败、提示词失败、远端未知、审核不可用、文件丢失分别保留真实状态；无自动重生、假发布、未审图预览或偷偷重复提交 | 提示词漂移／覆盖弱化：意图 abandoned、S07 blocked、无 prompt artifact、无 visual、无自动重试；确认失败与额度拒绝无意图；远端未知、文件丢失与重启细节由 #68 承接，不在本票宣称已完成 | 阶段条件通过（未知／重启细节 #68） |

## 6. 证据文件

| 路径 | 说明 |
| --- | --- |
| backend/tests/test_t66_replay_drift.py | 6 个后端定向测试 |
| tests/t66-workflow-replay.spec.ts | 3 个前端行为测试 |
| main/backend/app/prompt_guard.py | 提示词落地检查 |
| main/backend/app/acceptance.py | abandon_intent 与意图生命周期 |
| .scratch/t66-logs/targeted.log | 定向测试输出 |
| .scratch/t66-logs/full-nonv2.log | 非 v2 全量后端 |
| .scratch/t66-logs/full-v2.log | 继承 v2 全量后端 |
| .scratch/t66-logs/frontend-test.log | 前端 Vitest |
| .scratch/t66-logs/frontend-typecheck.log | vue-tsc |
| .scratch/t66-logs/frontend-build.log | Vite 构建 |
| .scratch/t66-logs/ruff.log | ruff |
| .scratch/t66-logs/diff-check.log | git diff --check |
| .scratch/t66-logs/alembic-heads.log | alembic head |
| .scratch/t66-smoke/result.json | 88 项 HTTP smoke |
| docs/operations/r2-replay-drift.md | 运维与故障处理说明 |

## 7. 残余风险与后续

1. prompt_guard 是确定性的词面与结构检查，不能替代语义审查。独立 output_reviewer 仍是必要的第二道门；两者同时存在，宁可误拦也不放行未确认卖点。
2. 本票测试使用 fake LLM／Visual，不代表真实供应商的响应质量、延迟、计费或远端幂等。真实环境边界在 Release 阶段验证。
3. 应用层项目锁会把同一项目的并发确认串行化；本票验证顺序跨客户端重放和两会话 smoke，独立连接并发、写点故障和窗口边界留给 #70 R2-GATE。
4. 远端未知、进程重启、结果找回、原任务核实与本地跟踪结案分别留给 #68、#69；本票不自动重发、不把未知当作未受理。
5. 本票不新增额度管理界面，管理员额度调整闭环由 #67 完成；当前配置默认值沿用 #65：每日 20、个人 1、实例 2、项目 1。
6. 未触碰真实数据根；真实来源与真实认领授权仍必须由维护者明确确认并完成已验证备份。
