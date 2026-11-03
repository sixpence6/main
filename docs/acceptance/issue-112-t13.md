# Issue #125 T13 验收记录：轻量任务状态、失败与真实恢复动作

## 1. 范围与结论

- 工单：#125（V3 纯对话 T13，父票 #112）。
- 目标：在同一对话中以一条轻量状态跟随真实任务；只显示服务端给出的安全原因和实际可用动作；忙时草稿可编辑但不能提交或排队；恢复动作清楚区分只读核实、可能调用模型和计数动作。
- 验收基线：集成分支 `codex/issue-112-integration` 的 `2b5d46e`（T12 合并与验收文档提交后）。本票从该提交新建 `codex/issue-112-t125` worktree。
- 本票变更：`main/src/composables/useChatFlow.ts`、`main/src/pages/ChatPage.vue`，以及新增 `tests/t125-task-status.spec.ts`。未修改后端生产代码、数据库迁移、SRS 参数或次数/槽位口径；任务状态、安全原因和可用动作继续使用 #99/#112 已交付的 workflow 公共接口。
- 结论：轻量状态、服务端安全原因、只读核实与可能调用模型的区分、服务端动作白名单、明确确认计数动作、忙时草稿可编辑但不排队、文字修复回到唯一输入框、账号禁用失败提示均通过前端集成测试、类型检查、生产构建和既有后端 T13 安全投影测试；真实浏览器/手机/真实供应商留 #129/#128。

## 2. 实现面

| 面 | 实现 |
|---|---|
| 单条轻量状态 | `ChatPage.vue` 只渲染一个 `section[aria-label="任务状态"]`；显示 `task_state.reason.message` 或简短状态文案。不渲染 `progress.stage_label`、阶段卡、思考过程或百分比；进行中仅保留轻量打字点装饰 |
| 服务端安全原因 | `taskReason` 直接取 `workflow.view.task_state.reason`；类别标签来自固定映射，正文只使用服务端 `reason.message`，不读取 `issue.message` 或前端猜测错误文本 |
| 原因分类 | 覆盖需补充、内容需要修复、需要重新确认、硬约束不满足、内容未通过检查、缺少必要证据、处理结果异常、技术故障、结果未知、授权已失效；每种状态显示对应安全标签和正文 |
| 动作白名单 | `taskActions` 只过滤 `available_actions` 中的恢复/修复动作；前端不补造动作、不调用未返回的动作、不自动执行恢复动作 |
| 效果区分 | 每个动作显示 `只读核实`、`可能调用模型` 或 `更新状态`；只读动作显示“不新增受理计数”，计数动作显示会占用一次受理计数和生成槽位 |
| 明确确认 | `retry_generation`、`retry_rejected_generation`、`revise_prompt_and_retry`、`start_new_generation`、`close_generation_tracking` 等需要确认的动作显示固定确认文案；未勾选时按钮和发送均禁用，确认后才发送对应 `acknowledge_*` payload |
| 文字修复 | 需要文本的动作只设置 `pendingTaskAction`，提示“正在修改”，不立即调用接口；用户在唯一底部输入框输入后点击发送，才以顶层 `text` 调用动作；没有第二输入表单 |
| 忙时行为 | 任务执行时 textarea 仍可编辑，但发送按钮禁用；不创建本地队列、不自动重排、不并发提交。恢复动作完成后只刷新详情，不触发 `autoAdvance` |
| 失败与生命周期 | 动作失败显示服务端安全文案或固定兜底文案；账号 `sessionRevision` 或项目切换清除待处理动作、确认状态和错误；`useWorkflow` 已在会话/项目/序列不匹配时丢弃晚到响应 |
| 计数口径 | 不新增生成、不自动重试、不自动排队、不新增免费预览或等待超时；只有服务端实际动作和用户明确确认才可能新增计数 |
| 权限与审计 | 未新增后端入口；继续调用既有 `POST /api/projects/{id}/workflow/actions/{action}`，复用身份、owner、对象关联、内容门禁、CSRF 来源和审计约束 |

## 3. 测试面

新增 `tests/t125-task-status.spec.ts`，7 个测试函数/7 个用例。测试挂载真实 App、路由和 Element Plus，只替换 fetch、时钟、可见性和存储，不 mock 前端业务模块。

- T13-1 进行中只显示一条当前状态，不展示阶段卡、内部阶段或虚构百分比；即使服务端 `stage_label` 为内部阶段名，也不会渲染。
- T13-2 等待生成时仍只显示一条状态，不出现阶段卡或自动提交；60ms 内无任何写请求。
- T13-3 任务进行中草稿仍可编辑，但发送保持禁用且不会排队提交；动作请求延迟期间草稿输入不会产生第二个动作请求。
- T13-4 安全原因来自服务端，并分别覆盖需补充、硬约束不满足、编制修复、未知和技术故障；原始 `issue.message` 哨兵不会显示。
- T13-5 只显示服务端实际提供的恢复动作，只读核实与可能调用模型清晰区分；未勾选确认时重试按钮禁用，勾选后只发送一次带 `acknowledge_new_attempt` 的请求。
- T13-6 文字修复动作回到唯一输入框；点击动作本身不产生请求，输入并发送后只发送顶层 `text`，仍只有一个 textbox，没有第二输入表单。
- T13-7 账号禁用时只显示安全失败，不自动重试，不显示“图片已经生成，可以保存使用了”等成功文案，原任务原因保持可见。

后端既有证据：`backend/tests/test_v3_p1_t13_safe_projection.py` 共 18 个用例通过，覆盖任务进度/绑定/约束/动作 effect、未审核 payload 剥离、未批准 artifact 不投影、安全原因不读取 `issue_message`、历史资源不冒充当前成功、跨账号/匿名/禁用账号读取拒绝、普通与恢复路径共享同一安全原因，以及 `ReasonCode` 分类映射。

RED 证据：实现前运行 `tests/t125-task-status.spec.ts` 的 6 个初始用例，6 个全部失败，首个失败为找不到 `region` 名称 `任务状态`，且 textarea 处于 disabled；随后实现轻量状态区和动作 UI，7 个用例通过。新增的等待生成用例进一步验证“单条状态、不自动提交”。

## 4. 命令与结果

工作目录：`C:/Users/28425/.codex/worktrees/issue-112-t125/新建文件夹`。

```powershell
D:/nodejs/node.exe node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.json
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run tests/t125-task-status.spec.ts --reporter=verbose
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run --reporter=json --outputFile=D:/Desktop/issue-112-context/t125-full3.json
D:/nodejs/node.exe node_modules/vite/bin/vite.js build --config main/vite.config.ts
```

后端既有安全投影测试：

```powershell
cd C:/Users/28425/.codex/worktrees/issue-112-t125/新建文件夹/backend
C:/Users/28425/.codex/worktrees/issue-112-integration/新建文件夹/backend/.venv/Scripts/python.exe -m pytest tests/test_v3_p1_t13_safe_projection.py -q -p no:cacheprovider --basetemp=D:/Desktop/issue-112-context/t125-backend1
```

结果：

- 类型检查：通过。
- T13 专项：`7 passed`（最新 4.25s）。
- 当前前端全套：28 个 spec 文件，`55 passed / 55`，全部通过；JSON `D:/Desktop/issue-112-context/t125-full3.json`。
- 生产构建：通过（1654 modules；仅有既有大 chunk 警告）。
- 后端安全投影：`18 passed in 10.04s`（basetemp `D:/Desktop/issue-112-context/t125-backend1`）。
- 静态检查：`git diff --check` 无输出；未在 `main/` 下新增测试、截图或临时产物。

## 5. #125 AC 逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| 1 任务进行中仅更新当前状态，不刷阶段卡、思考过程或虚构百分比；草稿可编辑但忙时不提交、不排队 | 通过 | T13-1/T13-2 断言只有一条 `任务状态` 区域、无内部阶段/百分比；T13-3 断言 textarea 在动作延迟期间仍启用、发送禁用、无第二次动作请求；实现没有本地队列 |
| 2 安全原因来自服务端，区分需补充、硬约束、编制修复、未知和技术故障，不把未发布图片称成功 | 通过 | T13-4 覆盖五类原因并以哨兵证明不读取 `issue.message`；T13-7 断言失败时不出现成功文案；后端 18 个用例覆盖 typed `task_state.reason` 和 fallback 文案 |
| 3 只读本地核实与可能调用服务的恢复明确区分；只消费服务端实际可用操作，不能补最终授权或自动重生成 | 通过 | T13-5 断言只读动作标签/计数说明、模型动作标签、未知动作不渲染、确认前禁用、确认后只发送一个动作请求；前端动作来自 `available_actions` 过滤；T13-6 文字修复仍走真实动作；没有自动生成或自动确认路径 |
| 4 覆盖等待/执行/拒绝/未知/恢复及账号禁用晚到路径，计数与受控调用次数和界面说明一致 | 通过 | T13-2 等待、T13-1 执行、T13-4 五类失败/未知、T13-5 恢复动作、T13-7 账号禁用；测试统计动作 POST 次数为 0 或 1，并检查确认/计数文案；后端覆盖拒绝、未知、禁用读取和 reason 映射 |
| 5 只在隔离环境验证；不操作真实用户数据、凭据或运行配置；保护用户未提交修改；测试/文档/截图/临时产物不进应用本体 | 通过 | 只在专用 worktree 修改；原用户树未操作；测试位于 `tests/`，验收文档位于 `docs/acceptance/`；构建产物被 gitignore；未新增后端迁移或运行配置 |
| 6 新入口遵守身份/所有者/对象关联/内容门禁/CSRF 来源与审计；后继安全复验不延期基本安全 | 通过（复用既有接口） | 未新增后端写路由；动作继续走既有 workflow 路由和会话/owner/对象/CSRF/审计路径；后端 T13 用例检查跨账号、匿名和禁用账号读取 |
| 7 不改 SRS 参数或次数口径；不新增免费预览、自动等待超时、隐式排队或自动图片重试；缺失证据保持未知 | 通过 | 无后端/SRS/迁移变更；前端无自动重试、无队列、无免费预览或超时；动作只有用户点击并明确确认后才调用；真实浏览器/手机/供应商证据未声称通过，留 #129/#128 |

## 6. 旧展示测试替换关系

#122 已退役旧 V2 展示 spec，本票把“轻量状态、失败原因、恢复动作”责任落到 T13-1—T13-7。旧 `WorkflowPanel` 的阶段卡、四步进度、旧 action textarea 不再恢复；本票只保留其仍然有效的安全语义（安全原因、只读核实说明、计数确认、恢复动作白名单），并改为唯一对话内的单条状态区。旧 `t68-workflow-recovery`、`t74-u2-authorization` 等已退役的行为由本票和后端 `test_v3_p1_t13_safe_projection.py` 替代；不重新引入旧的独立工作台或第二输入框。

## 7. 残余未知与边界

- 未做真实浏览器视觉、真实手机软键盘、真实供应商生成或真实网络时延验收；#129/#128 负责，当前结论只覆盖 jsdom 组件集成与后端 HTTP/SQLite。
- 前端测试使用受控 fetch 验证动作请求、禁用状态和计数文案，不实际执行模型调用；后端 T13 用真实 HTTP、临时 SQLite 受控适配器覆盖安全投影和动作效果。
- 任务状态只来自服务端 workflow 视图；前端不缓存旧状态、不推测进度，也不会在恢复动作后自动推进流程。
- 文本修复使用唯一 composer；多行输入、富文本和“同时修改多个动作”未在本票扩展。
- 需要确认的动作使用显式 checkbox；未勾选时不会提交，也不会自动勾选或替用户确认授权。
- 真实账号禁用晚到路径在 T13-7 用受控 403 响应验证；真实跨标签页/登录生命周期完整组合仍留 #127。

## 8. 合并复核（Lead）

- 功能提交：待提交。
- 集成合并：待合并。
- 合并后前端验收：待运行。
- 合并后后端安全投影：待运行。
- 测试文件一致性：待核对。
- 合并后工作树：待核对。
- 结论：待合并复核。
