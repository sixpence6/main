# R3 U2 原位授权、执行状态与安全恢复验收记录

- Issue: #74
- 父 Issue: #52
- 分支: ticket/74
- 基线: integration/v3 7409e98
- 代码提交: f8f074a88a76a9b4f2711e857c5c8ce9ed9c3d28
- 验收日期: 2026-11-01
- 状态: 通过（R3 U2 阶段条件；U1／U3 见 #73／#75，R3 门禁见 #77）

## 1. 结论与边界

本票把 U2 原位授权、执行状态和安全恢复接到 U1 工作台：用户在最终需求卡上勾选确认并点击一次生成，页面留在原位，同时显示真实的编制提示词、生成图片、检查图片、准备交付四步状态；失败、未知和恢复入口只使用服务端事实，不自动重发，也不虚构进度。

1. 不再强制四页路径。项目打开后默认停留在 describe 工作台；旧的 /confirm、/generate、/results 只在显式旧路由时恢复，供 #77 收敛前的继承回归使用。U2 的确认、执行和交付处理都在同一页面完成，不跳额外确认页。
2. 一次操作只提交一个 R2 动作。确认按钮只在 confirmationState=pending 时出现，确认框必须勾选；点击只 POST /api/projects/{id}/workflow/actions/confirm_requirements，不调用旧 /generations 接口，不新增前端授权路径。服务端仍在同一个事务里复核项目归属、revision、卡片摘要、参考图和尺寸，并写入唯一意图、次数和槽位。
3. 请求绑定真实主体与快照。确认请求携带 artifact_id、artifact_digest、requirement_revision 和稳定的 request_id；request_id 由当前项目、流程、需求摘要和会话共同确定，重复点击和响应丢失后的再次点击复用同一请求标识，变参由服务端返回冲突，不在浏览器重新拼一套授权。
4. 执行状态来自服务端。四步为编制提示词（S07）、生成图片（S08）、检查图片（S09）、准备交付（S10）；状态只有已完成、进行中、等待、等待处理、已暂停、结果未确认，全部由 stage、status、generation_job、issue 和 delivery 推导，没有计时器百分比，也没有前端推测的完成。
5. 后台轮询只读。useWorkflow 只在 S07–S10 活动、意图 accepted／executing、尚无交付且没有 approve_delivery／publish_delivery 动作时，每 2500ms 调一次 GET /api/projects/{id}/workflow；页面隐藏、离线、动作处理中、阻塞、失败、交付就绪或组件卸载都会停止。轮询不会 POST，也不会调用模型。
6. 只有实际产生且已批准的执行提示词可折叠展示。prompt_plan 从通用已批准产物列表中排除，单独取最高 revision 的 approved 且有 payload 的记录；详情默认折叠，摘要写明第 N 次，正文说明它只用于本次生成任务。候选、未批准、无 payload 或确认前的提示词不会提前显示。
7. 需求漂移回到 U1。S07 发现确认摘要、文案或限制不一致时返回 PROMPT_REQUIRES_RECONFIRMATION；前端只显示当前问题和修改需求／重新确认指引，不展示候选提示词，也不允许继续原确认。
8. 失败和未知保持真实。阻塞未知任务只显示安全恢复说明和服务端可用动作；核实只读、找回只读原结果、恢复不补发、结案不声称远端取消、新任务另行计数。执行进度对 unknown 显示“结果未确认”，不把未知当未受理，不显示未审图预览，也不伪造发布。
9. 参考图与导航继承真实边界。加载已有参考图不自动调用分析；只有新上传改变 reference_asset.id 且流程仍为 none 时，才执行一次服务端 analyze_reference 动作；手动刷新只 GET 流程状态。导航、刷新、历史返回和重新登录不触发模型调用。
10. 本票没有新增 HTTP 路由、数据库迁移或后台任务。迁移头仍为 0010，0001–0010 未改；只改了前端工作台、轮询、默认阶段、确认动作元数据和对应测试。没有读取或写入 backend/data 或 main/backend/data。

本票只对 #74 承接的 V3-AC-23、24、25、26、27、28、38 给出 U2 阶段条件结论；三状态完整衔接、四页最终收敛和 Release 门禁仍由 #75、#77、#85 复核，不能把本票结论提前当作跨阶段全部通过。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t74 |
| 分支 | ticket/74 |
| 基线 | integration/v3 7409e98 |
| 代码提交 | f8f074a88a76a9b4f2711e857c5c8ce9ed9c3d28 |
| Node.js | v26.12.1 |
| 前端测试 | Vitest 5.0.5 |
| 前端构建 | Vite 8.3.5 |
| 后端 | Python 3.12.13（backend/.venv，uv） |
| 数据库 | 临时 SQLite，alembic 0010，26 张表 |
| 真实浏览器 | Microsoft Edge，C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe，headless + CDP |
| 模型适配 | LLMStub／VisualStub（fake adapter，不访问外部模型） |
| 数据边界 | 不读取或写入 backend/data、main/backend/data |

## 3. 实现摘要

### 3.1 原位授权和默认阶段

修改 main/src/composables/useWorkspace.ts：

- chooseDefaultStage 在项目加载后默认回到 describe，不再按 brief_status、latest generation 或 uncertain 自动跳到 /confirm、/generate、/results。打开项目不会强制经过旧四页。
- 仍允许显式旧路由恢复：路由名对应的 preferredStage 在未锁定时生效，满足 #77 之前的旧页面回归；resolveProject 先从 route.name 解析 preferredStage，避免 stage 重置把显式 /confirm、/generate、/results 同步改写成 /。
- 确认按钮仍由 WorkflowPanel 的 confirmationState 控制，只有 pending 才渲染；确认框和按钮的最新技术说明见下。

修改 main/backend/app/workflow/dispatch.py：

- confirm_requirements 的 requires_confirmation 从 False 改为 True，前端在真实 UI 中因此要求先勾选“确认需求卡”。这是服务端动作元数据与前端测试夹具之间的真实不一致，由浏览器验收发现；没有放宽任何服务端校验。

WorkflowPanel 的确认提交仍走 useWorkflow.runAction；点击一次只发一个 confirm_requirements 动作。请求 payload 继续复用 #65/#66 的既有实现：artifact_id、artifact_digest、requirement_revision 和稳定 request_id 都由当前视图和本地请求标识生成；不调用 generations API，也不跳转路由。

### 3.2 四步执行进度

修改 main/src/components/workflow/WorkflowPanel.vue：

- EXECUTION_STEPS 固定四步：S07 编制提示词、S08 生成图片、S09 检查图片、S10 准备交付。
- EXECUTION_STATE_LABELS：done 已完成、current 进行中、waiting 等待、blocked 等待处理、failed 已暂停、unknown 结果未确认。
- executionStepState 依据当前 stage 与服务端 status 推导；failed 显示已暂停，blocked／needs_input 显示等待处理，generation_job=unknown 或 GENERATION_UNKNOWN／RESULT_RETRIEVAL_PENDING 显示结果未确认，未知不会被描述成失败或未受理。
- executionStepDetail 对当前步骤给出真实动作说明；S08 显示服务端 generation_job 的图片状态；S09 明确候选图审查通过前不展示预览；S10 明确发布仍需人工确认。
- executionVisible 只在已有流程且存在 delivery、generation intent 或当前处于 S07–S10 时显示；没有流程时不显示空进度。
- 模板使用 section.workflow-execution、role=region、aria-labelledby 和 ol.execution-step；当前步骤 aria-current=step，状态标签只来自上述枚举，没有百分比或模拟动画。

### 3.3 只读后台轮询

修改 main/src/composables/useWorkflow.ts：

- POLL_INTERVAL_MS=2500；canObserveBackground 要求 run_id、status=active、stage 属于 S07–S10、没有 delivery、没有 approve_delivery／publish_delivery、intent 为空或 accepted／executing。
- armPoll 和 poll 检查 disposed、actionBusy、document.visibilityState、navigator.onLine；页面隐藏、离线、动作处理中或超出观察条件时不排下一次轮询。
- load(silent=true) 用于轮询：保留当前 view，不清空 loading／error，已有 view 时轮询错误不覆盖页面；成功或失败后都会重新评估并安排下一次轮询。
- 项目切换、runAction 结束、visibilitychange、online／offline 和 unmount 都清理定时器；unmount 取消请求并移除监听。轮询只调用 GET getWorkflow，没有 POST。

### 3.4 已批准执行提示词

修改 WorkflowPanel 的 approvedArtifacts 与新增 executionPrompt：

- approvedArtifacts 排除 prompt_plan，避免它混入通用产物列表或提前展开。
- executionPrompt 只接受 kind=prompt_plan、status=approved、payload 非空，并取 revision_no 最高的一条。
- 模板 details.workflow-execution-prompt 只在 executionPrompt 存在时显示，摘要为“查看已批准的执行提示词（第 N 次）”，默认不展开；正文只列 payloadLines，并说明只展示实际产生且通过独立审查、用于本次生成任务的提示词。
- 候选、未批准或 payload 缺失的 prompt_plan 不会出现在可读文本中；需求漂移时服务端阻断，前端不展示候选内容。

### 3.5 安全恢复与动作说明

WorkflowPanel 新增恢复动作元数据：

- RECOVERY_ACTION_IDS 包含 verify_generation、recover_generation、retrieve_original_result、recover_submission、resume_after_restart、continue_workflow、close_generation_tracking、start_new_generation，恢复类动作统一排在普通动作之后。
- actionHelp 按动作给出准确说明：核实只读、不新增计数；找回只读原结果、不提交新生成；恢复不补发已提交请求；继续使用原检查点；结案只结束本地跟踪并释放已证实占用，不声称远端已取消；start_new_generation 明确是新任务、会重新计数并占用槽位，旧任务仍可能在远端执行或计费。
- 安全恢复提示只在存在核实／找回／继续／结案类动作时显示，明确这些操作不会自动提交新的生成请求，也不会自动新增受理次数；只出现 start_new_generation 时不显示这句，避免把新任务误说成安全恢复。
- actionDisabledReason 覆盖加载／处理中、缺少文字说明、未勾选确认三类原因，并写入按钮 title；不可用动作不会静默。
- 所有恢复动作都必须由用户点击触发，组件不会在挂载、轮询、刷新或会话恢复时自动调用任何一个。

### 3.6 参考图与导航回归

WorkflowPanel 保留新上传参考图的一次显式分析行为：

- 监听 currentProject.reference_asset.id；只有 id 从空变为新值（新上传或替换）时才 refresh 流程；若服务端返回 status=none 且存在 analyze_reference 动作，才执行一次该动作。
- 打开已有参考图、刷新页面或手动点击刷新不会自动调用 analyze_reference；手动刷新只 GET 流程状态。
- 这保持了 R2 的“上传后独立分析”行为，同时满足 U2 的导航不触发模型要求。

### 3.7 前端测试覆盖

新增 tests/t74-u2-authorization.spec.ts，8 个测试：

1. 核对最终需求卡后原地确认一次，不跳转旧确认页也不调用旧生成接口。
2. 执行进度按服务端状态展示四步，不显示虚构百分比，已批准提示词默认折叠。
3. 未批准的提示词不展示；需求漂移只引导修改需求并重新确认。
4. 阻塞未知任务只提供安全恢复说明和动作，等待期间不自动发请求。
5. 后台执行只按节奏读取状态，遇到阻塞立即停止轮询且从不自动写请求。
6. 确认动作可用键盘聚焦、勾选并提交，状态提示始终可读。
7. 加载已有参考图不会自动调用分析，手动刷新也仅读取状态。
8. 320 像素窄屏保留执行进度和折叠提示词，不产生额外写请求。

同时调整了 code-review-regressions、t02、t04、t06、t07、t08、t69、t72、v2-t20 等旧测试：旧页面仍通过显式 /confirm、/generate、/results 路由进入，删除的是“打开项目自动跳四页”的假设，没有删除任何有效断言。t69 的流程 GET 次数从 2 调整为 1，是因为默认自动路由不再额外触发一次旧页面读取；业务流程、结案和计数断言保留。

### 3.8 服务端回归断言

在 backend/tests/test_t65_generation_cycle.py 的完整确认流程测试中增加断言：GET /api/projects/{id}/workflow 返回的 confirm_requirements.requires_confirmation 必须为 True，再执行确认 POST。该断言锁定了前端真实需要的显式勾选，防止 dispatch 元数据再次漂移。

## 4. 执行命令与结果

### 4.1 前端定向测试

工作目录：D:/Desktop/新建文件夹/.worktrees/t74。

~~~
npm test -- --run tests/t74-u2-authorization.spec.ts --reporter=dot
~~~

结果：1 个测试文件、8 个测试通过，4.02s，exit 0；日志 .scratch/t74-evidence/t74-spec.log。

### 4.2 前端全量、类型检查和构建

~~~
npm test -- --run --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：42 个文件、242 个测试通过，23.20s，exit 0；日志 .scratch/t74-evidence/t74-frontend-full.log。仅有既有 fireEvent.change／fireEvent.input 提示和 Vue Router 警告，无失败。
- vue-tsc --noEmit：exit 0；日志 .scratch/t74-evidence/t74-typecheck.log。
- Vite 8.3.5：1667 modules，995ms；dist/index.html 0.47 kB，dist/assets/index-AlNW3Oh6.css 416.52 kB，dist/assets/index-DD_0UVhm.js 1,201.27 kB；只有 chunk-size 警告，exit 0；日志 .scratch/t74-evidence/t74-build.log。

### 4.3 后端全量回归

工作目录：D:/Desktop/新建文件夹/.worktrees/t74/backend，Python 3.12.13，uv 虚拟环境。全量按非 v2 与 v2 分开运行，避免超过单条命令时限：

| 范围 | 结果 | 日志 |
| --- | --- | --- |
| 非 v2 全部测试（37 个 test_*.py，排除 test_v2_*） | 351 passed, 2 warnings, 372.92s | .scratch/t74-evidence/t74-backend-nonv2.log |
| 继承 v2 全部测试（19 个 test_v2_*.py） | 235 passed, 298.01s | .scratch/t74-evidence/t74-backend-v2.log |
| 合计 | 586 passed，无失败、跳过或 unknown |  |

后端定向回归（dispatch 元数据与生成相关）：

~~~
python -m pytest -q tests/test_t65_generation_cycle.py tests/test_t66_replay_drift.py tests/test_t67_generation_quota.py tests/test_t68_recovery.py tests/test_t69_local_closure.py tests/test_t70_r2_gate.py tests/test_v2_t09_confirmation.py
~~~

结果：48 passed，62.97s，exit 0。仅有既有 SQLAlchemy／httpx 警告，无失败。

### 4.4 迁移与临时数据库边界

本票无迁移。浏览器验收使用临时数据库 .scratch/t74-browser/data/app.db，实测 alembic_version=0010，26 张表，PRAGMA integrity_check=ok。没有打开或修改 backend/data、main/backend/data。

### 4.5 真实浏览器、真实 HTTP、临时 SQLite、完整 App

先构建最终前端，再执行：

~~~
node D:/Desktop/新建文件夹/.scratch/t74-browser/run_browser.mjs
~~~

最终结果：{"ok":true,"checks":17,"base_url":"http://127.0.0.1:12576"}，完整结果 .scratch/t74-browser/browser-result.json，服务日志 .scratch/t74-browser/server.log，运行日志 .scratch/t74-evidence/t74-browser-smoke.log。

17 项检查覆盖：

1. 最终需求卡在原位只出现一个确认入口，路径仍为 /?project=...，不跳旧确认页。
2. 未勾选时按钮禁用，title 为“请先勾选确认。”，页面显示真实 usage（今日已受理 0/20，进行中个人／实例／项目）和“核对并确认当前需求卡”的下一步。
3. 空格键勾选确认框后按钮启用。
4. Tab 可把焦点移到主操作。
5. 在显式确认前 LLM started／roles 均为空，Visual submit／poll 均为 0，证明未提前调用模型。
6. 一次确认只发一个 POST /workflow/actions/confirm_requirements，没有 POST /generations，路由和 project 查询保持不变；usage 变为受理 1/20，意图 accepted。
7. 执行进度显示四步且没有百分比。
8. 确认后后台周期开始但被 fake LLM gate 挡住，尚无完成的模型响应或图片提交。
9. 已批准执行提示词存在但默认折叠。
10. 点击摘要后才展开，正文含 positive_prompt，未批准内容不可见。
11. 生成链推进到提示词、图片、检查三步均已完成。
12. 准备交付显示“等待处理”，页面没有“100%”“自动发布”“已发布”或伪造完成，仍显示“批准交付”人工入口。
13. 真实 HTTP 流中 Visual submit=1、Visual poll=1，LLM 角色依次为 generation_engineer、output_reviewer、quality_compliance、output_reviewer、delivery_manager、output_reviewer；无残留后台任务或周期。
14. 确认后的轮询与导航没有新增任何 mutating 请求（POST 仍只有登录和确认两条）。
15. 1440px：两列并排，workbench 1072px，列宽 616.39px／455.59px，document 1440/1440，无横向溢出。
16. 768px：单列，两列上下相接，workbench 649px，workbench scrollWidth=clientWidth=649，五个可见控件都在 768px 内；document 宽度 1020px 来自继承的全局外壳，不是 U2 工作台溢出（见残余风险）。
17. 320px：单列，workbench 273px，document 320/320，执行进度、提示词摘要和“批准交付”按钮都在视口内。

浏览器自动化使用 C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe，headless + remote-debugging-port + 临时用户目录；后端为真实 uvicorn、临时 SQLite、完整 App，LLM／Visual 为 fake adapter，不访问外部模型或真实数据。

## 5. 验收覆盖表

| AC | 本票 U2 阶段条件 | 证据 |
| --- | --- | --- |
| V3-AC-23 | U2 在最终卡上原位进入执行，不因确认或生成跳到旧四页；只有一个确认入口；旧图仍按历史预览标注（U1 已交付，U3 由 #75 完成） | t74 测试 1／2／6；浏览器检查 1／6；选择默认阶段测试与旧路由适配；#73 历史预览测试仍全量通过 |
| V3-AC-24 | 一次操作绑定真实项目、最终卡摘要、需求 revision 和稳定 request_id；确认后才编制审查，批准前无生图或虚构提示词 | t74 测试 1／3；浏览器检查 5／6／8／9／10；后端 #65/#66 原子确认、重放和漂移测试 48 passed；586 全量通过 |
| V3-AC-25 | revision／摘要／参考／尺寸或文案漂移时阻断，回到 U1 修改并重新确认，不展示候选提示词 | t74 测试 3；WorkflowPanel executionPrompt 只接受 approved+payload；#66 漂移测试与 #70 门禁回归通过 |
| V3-AC-26 | 一次确认只产生一个 R2 动作；双击、响应丢失和跨标签复用稳定 request_id，变参由服务端冲突；应用侧不再拼旧生成步骤 | t74 测试 1／5；浏览器检查 6／14；#65/#66/#70 回归 48 passed；#72 跨标签测试全量通过 |
| V3-AC-27 | 打开项目、刷新、历史返回、重新登录、参考图加载和后台轮询不额外调用模型；原任务合法执行不被误判 | t74 测试 5／7；浏览器检查 5／14；#72 全量测试；真实 stats 只有一次生成链且无后台残留任务 |
| V3-AC-28 | 失败／未知保持真实状态，无自动重生、假发布或未审图预览；未知显示结果未确认并保留受理事实 | t74 测试 4／5；浏览器检查 12；执行步骤 unknown 状态、无自动请求断言；R2 #68/#69/#70 回归通过 |
| V3-AC-38 | 320／768／1440 视口；U2 主操作、折叠提示词和进度可用；键盘可聚焦、勾选、提交；控件不横向溢出 | 浏览器检查 3／4／15／16／17；t74 测试 6／8；#73 中文输入与 U1 布局测试继续全量通过；实际设备边界见残余风险 |

## 6. 证据文件

- 代码提交：f8f074a88a76a9b4f2711e857c5c8ce9ed9c3d28。
- 前端定向／全量／类型／构建：.scratch/t74-evidence/t74-spec.log、t74-frontend-full.log、t74-typecheck.log、t74-build.log。
- 后端回归：.scratch/t74-evidence/t74-backend-nonv2.log、t74-backend-v2.log。
- 浏览器：.scratch/t74-browser/run_browser.mjs、serve.py、setup.py、seed74.py、browser-result.json、server.log、stats.json、data/app.db；运行日志 .scratch/t74-evidence/t74-browser-smoke.log。
- 迁移检查：.scratch/t74-evidence/check_t74_db.py；临时库 alembic 0010、26 表、integrity_check=ok。
- 构建产物：D:/Desktop/新建文件夹/.worktrees/t74/dist（Vite 生成，不在 Git 提交中）。

## 7. 残余风险

1. 真实浏览器补证只使用本机 Edge headless，不是物理手机或其他浏览器；#74 的 AC-38 阶段结论是 Edge 的 320／768／1440 计算布局加组件键盘测试。Release #85 若要求指定真实设备或辅助技术，必须在 Release 阶段重新补证。
2. 768px 时 workbench 为 649px 且控件都在 768px 内，但继承的全局页面外壳使 document.scrollWidth=1020px。该外壳不属于 U2，本票没有用 overflow:hidden 掩盖 U2 内容；Release 门禁需要决定是否把全局外壳横向宽度纳入发布门槛。
3. LLM／Visual 是 fake adapter，证明的是应用的单命令、状态流转、轮询和恢复边界，不是供应商真实生成质量、延迟或保留策略。真实供应商行为不在本票声明内。
4. 阻塞、未知和失败路径主要用组件夹具与后端回归证明；真实浏览器烟测使用成功链加 fake gate，没有对真实远端做失败注入。R2 #68/#69/#70 的独立进程和故障注入仍是这些路径的主要证据。
5. 本票没有改变 R2 的意图、次数、槽位和恢复语义，也没有新增远端取消能力。页面上的“结案”只结束本地跟踪，不声称远端已取消；这一事实仍需在后续发布说明中保持。
6. 未读取或写入 backend/data、main/backend/data；临时数据库、临时用户目录和 fake adapter 均在 .scratch 下。