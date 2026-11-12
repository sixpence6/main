# Issue #122 T10 验收记录：纯对话外壳与首次文生图闭环

## 1. 范围与结论

- 工单：#122（V3 纯对话 T10，父票 #112）。
- 目标：把 R3 前端从旧“需求工作台”改为单侧栏、单消息流、单固定输入区的纯对话外壳；首次明确发送才创建项目，并沿用真实工作流接口完成 S01→S06 需求收敛、需求确认、提示词全文/复制、最终确认、生成与内联图片；不自动跳转独立成果页。
- 验收基线：集成分支 `codex/issue-112-integration` 的 `a231fb0`（T09 合并后）。本票从该提交新建 `codex/issue-112-t122` worktree。
- 本票变更：前端外壳、聊天流程 composable、API 类型、设计 token 与前端测试；没有修改后端生产代码、迁移、SRS 参数、次数/槽位口径或供应商适配。旧工作台展示测试中不再适用于 R3 的 36 个 spec 随外壳替换退役，见第 4 节；其产品语义由 #123—#129 的新票重写覆盖。
- 结论：本票覆盖的 R3 外壳与首次文生图闭环通过当前前端测试、类型检查、生产构建和既有后端 #121 gate 证据；真实浏览器视觉、真实供应商、真实手机和完整账号组合仍是未知，留待 #129。

## 2. 实现面

| 面 | 实现 |
|---|---|
| 外壳 | `WorkspaceLayout.vue` 改为浅灰紫页面 + 圆角白色侧栏 + 圆角白色主面板；桌面侧栏 288px、顶栏高度 68px、会话宽度 880px、外壳圆角 28px；移动端同一侧栏进入抽屉，不新增第二套工作台 |
| 侧栏 | `AppSidebar.vue` 保留品牌“电商生图工作台”、新对话、账号与用量、用量、数据使用说明、退出登录；真实项目列表/搜索/重命名属 #123，本票不提前做 |
| 页面 | `ChatPage.vue` 仅一个消息流、一个标题栏和底部固定输入区；新会话只显示欢迎语和输入框，不显示功能卡、阶段卡、旧 `WorkflowPanel` 或独立成果入口 |
| 流程 | `useChatFlow.ts`：首次发送创建项目并调用 `submit_request`，随后只自动推进服务器明确提供的 `compose_brief`、`plan_creative`、`plan_visual`、`finalize_requirements`；停在需求确认，不自动确认。需求确认后调用 `confirm_requirements`；提示词预览可确认时才显示可用按钮；最终确认携带 `snapshot_id`、`snapshot_digest`、`command_id`、`command_context`；执行完成后自动推进 `prepare_delivery`、`approve_delivery`、`publish_delivery`，并在原消息流内联渲染已发布资产 |
| 数据契约 | `api/types.ts`、`api/workflow.ts`、`api/client.ts` 补齐 `turn_kind`、`request_id`、`MessageAttachmentView`、`PromptPreviewView` 与 `sendMessage.request_id`；没有新增后端接口 |
| 防陈旧覆盖 | `useWorkflow.adopt()` 在 POST 返回后取消在途 GET 并递增加载序列；`useWorkspace.adoptCreatedProject()` 清理旧详情/消息/生成并同步 URL，避免新项目被旧请求覆盖 |
| 视觉 token | `styles/tokens.css` 更新页面 `#EEF0F8`、面板 `#FFFFFF`、次级 `#F5F6FA`、主色 `#5846E8`、正文 `#1F2438`、次级文字 `#626D88`、边框 `#E5E8F3` 及外壳/侧栏/顶栏/会话宽度变量 |

## 3. 测试面

新增 `tests/t122-pure-chat.spec.ts`，5 个测试函数/5 个用例，挂载真实 App、路由和 Element Plus；只替换 fetch、时钟、可见性和存储，不 mock 前端业务模块：

- T10-1 新对话只显示欢迎语、一个输入框和禁用发送；无项目时零 POST，不渲染旧“需求工作台”或“提示词确认”区域；侧栏显示品牌、账号与用量、数据使用说明。
- T10-2 首次发送只创建一次项目，自动推进到需求确认；断言 `submit_request` 的 `expected_revision`/text 与自动动作顺序；确认前不请求 `confirm_requirements`/图片。
- T10-3 确认需求后显示完整实际提示词、输出参数和额度说明，可复制；点击最终确认前不提交 `confirm_prompt`；点击后携带真实快照字段并在同一 `project-chat` 路由内联显示图片，不跳转结果页。
- T10-4 服务端 `can_confirm=false` 时显示“提示词检查中…”，按钮禁用且不提交最终确认。
- T10-5 打开已有对话时即使服务端提供了 `compose_brief` 动作，也不自动执行业务动作；自动推进只由本会话的发送/确认触发。

测试使用固定 UUID 断言格式、真实路由守卫之外的公开页面语义和请求捕获；不直接调用组件内部方法。后端真实链路（文本+参考图、S01→S10、最终确认零提交、发布资产读取）已由 #121 的 `backend/tests/test_issue_121_t09.py` 15 个用例覆盖；本票不重复伪造后端通过。

## 4. 旧展示测试退役与替换

R3 明确要求新界面不是旧工作台的隐藏/缩小版，旧测试中依赖 `需求工作台`、`.workflow-panel`、`AppHeader`、`ProjectDrawer`、旧任务卡、旧“准备交付”面板和自动跳结果页的断言无法在不恢复旧界面的前提下继续通过。为避免把旧展示重新塞回产品，本票退役以下 36 个旧 spec（共 162 个失败用例及其中的旧断言）：

- 项目/外壳/抽屉：`t01-workspace`、`t02-project-read`、`t02-project-read-supplement`、`t03-first-project`、`t07-navigation-conflict`、`t101-shared-shell`、`t106-project-search`、`t01-legacy-isolation`。
- 输入/附件/卡片：`t05-reference-upload`、`t103-single-chat-cards`、`t104-single-composer-edit`、`t107-draft-attachments`、`t102-publish-jump`。
- 状态/恢复/闭环：`t60-dataflow-notice`、`t65-workflow-usage`、`t66-workflow-replay`、`t68-workflow-recovery`、`t69-local-closure`。
- 账号/响应式/旧工作台：`t71-session-isolation`、`t72-cross-tab-history`、`t22-responsive-brand`、`t73-u1-workbench`、`t74-u2-authorization`、`t75-u3-delivery`、`t77-r3-gate`、`code-review-regressions` 及 `v2-t02/t03/t04/t05/t06/t09/t13/t37/t41/t49-*`。

替换归属：项目列表/搜索/重命名 → #123；图片附件 → #124；轻量状态与恢复 → #125；历史与旧链接只读 → #126；账号生命周期 → #127；响应式/键盘/手机 → #128；真实浏览器与视觉证据 → #129；无消费者旧展示最终清理 → #130。本票新增的 T10-1—5 只覆盖 #122 的当前验收面，不把旧 162 个断言宣称为通过。

## 5. 命令与结果

工作目录：`C:/Users/28425/.codex/worktrees/issue-112-t122/新建文件夹`。

```powershell
D:/nodejs/node.exe node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.json
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run tests/t122-pure-chat.spec.ts --reporter=verbose
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run
D:/nodejs/node.exe node_modules/vite/bin/vite.js build --config main/vite.config.ts
```

结果：

- 类型检查：通过。
- T10 专项：`5 passed`（5.14s）。
- 当前前端全套：`35 passed / 35`，11 个 spec 文件，约 12.6s；旧 V2 展示 spec 退役后无失败。
- 生产构建：通过（1654 modules transformed；仅有 Vite 既有的大 chunk 警告）。
- 静态检查：`git diff --check` 无输出；未在 `main/` 外新增测试、截图或临时产物。

## 6. #122 AC 逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| 1 浅灰紫背景、圆角侧栏/主面板、对话标题、唯一消息流、固定输入；不缩小/隐藏/移动旧流程面板 | 通过 | T10-1 只发现欢迎语、唯一输入框和侧栏入口；旧 `需求工作台`/`提示词确认` 区域不存在；`WorkspaceLayout`/tokens 提供目标色值与几何；真实视觉对照留 #129 |
| 2 新对话只有欢迎语和输入；首次明确发送才创建项目；必要澄清/需求确认经对话和轻量动作转真实后端 | 通过 | T10-1 无项目零 POST；T10-2 首次发送恰好创建一次项目并捕获 `submit_request`→`compose_brief`→`plan_creative`→`plan_visual`→`finalize_requirements`；需求确认是轻量按钮，不进入旧工作台 |
| 3 首次准备前说明一次受理额度；批准提示词全文展开/可复制，确认按钮紧随其后；点击前零图片执行 | 通过 | T10-2/T10-3 断言“本次生成会占用 1 次额度；今日已受理 1 / 20 次。”；T10-3 断言完整 positive prompt、输出、复制和最终确认前无 `confirm_prompt`；后端零提交由 #121 G1/G2 证明 |
| 4 执行后批准图片追加到同一对话，无自动结果跳转；消息/确认符合账号、登录生命周期、项目和请求序列 | 通过（本票 UI + 既有安全套件） | T10-3 点击最终确认后仍在 `project-chat`，内联 `img` 为已发布资产；`t55-auth`、`t57-security`、`t58-accounts-security`、`t76-auth-experience` 当前全绿；真实登录切换/旧链接组合留 #127/#129 |
| 5 保留必要账号/用量/数据使用轻量入口，不重做登录/管理员 | 通过 | T10-1 断言账号与用量、数据使用说明；侧栏无管理员/模型选择/本地数据库入口；登录页与管理员页未改 |
| 6 仅隔离环境；保护未提交用户改动；测试/文档/截图/临时产物不入应用本体 | 通过 | 只改当前 worktree；原用户树未操作；测试在 `tests/`，文档在 `docs/acceptance/`；未写入 `main/` 下测试或临时文件 |
| 7 新入口遵守身份/所有者/对象关联/内容门禁/CSRF 来源/审计 | 通过（沿用既有接口） | 没有新增后端写接口；首次发送、消息和确认都走已由 #113—#121 验证的鉴权、项目归属、内容门禁和审计 API；前端不绕过 `expected_revision`/快照摘要/命令上下文 |
| 8 不改 SRS 参数或次数口径；不新增免费预览、自动等待超时、隐式排队、自动图片重试 | 通过 | 本票无后端/迁移/SRS 变更；自动推进仅处理服务器 `available_actions` 中明确动作，停在需求确认和最终确认；无定时重试或图片重发逻辑 |

## 7. 残余未知与边界

- 未做真实浏览器、同尺寸视觉对照、真实手机软键盘或真实供应商验收；#129 负责。
- 侧栏真实项目列表/搜索/重命名、图片附件、任务状态恢复、历史只读和账号生命周期迁移分别留 #123—#127；本票只提供 R3 外壳和首次闭环。
- 前端测试使用受控 fetch 与固定响应，不证明真实后端时延、并发和错误文案；后端链路以 #121 gate 为准。
- 旧 162 个展示断言按第 4 节退役，不以“当前全套 34 passed”宣称旧 UI 行为仍存在；后续票需按替换归属补回新 UI 证据。
- 生产构建存在既有大 chunk 警告，不是本票引入的功能失败；未修改构建配置来掩盖。

## 8. 合并复核（Lead）

- 功能提交：`5f04a36270aae729cd8b218e1a374c54e9229f5b feat(chat): add pure-chat shell and first text-to-image closure (#122)`（49 个文件，+1477/−9265；前端外壳/流程/API 类型/token/测试与验收文档，退役 36 个旧展示 spec，无后端生产代码或迁移变更）。
- 集成合并：`887f55d22017ca05ffc96d2b13830811c98ad585 merge: T10 pure-chat shell and first text-to-image closure (#122)`，以 `--no-ff` 合入 `codex/issue-112-integration`（第一父提交 `a231fb0`）。
- 合并后前端验收：11 个 spec 文件，`35 passed / 35`（约 14.3s；JSON `D:/Desktop/issue-112-context/t122-postmerge.json`）；`vue-tsc --noEmit -p tsconfig.json` 通过；`vite build --config main/vite.config.ts` 通过（1654 modules，既有大 chunk 警告）。
- 合并后后端主验收：同一 11 个后端验收文件按相邻两组执行，`105 passed in 375.11s`（batch A：T01/T02 迁移与 T03—T04）+ `53 passed in 218.28s`（batch B：T05—T09），合计 `158 passed`，无失败；单次 11 文件运行超过 runner 600s 上限，因此拆分为同一文件集合的无重叠批次（basetemp `t122-mainA` / `t122-mainB`）。
- 测试文件一致性：`git diff 5f04a36..887f55d -- tests/t122-pure-chat.spec.ts docs/acceptance/issue-112-t10.md` 无输出；合并后测试文件 SHA-256 为 `13ba7dee5b99ba728f56347436454b2edbc838d7233b7141ec19685a260d37eb`，与功能提交一致。
- 合并后工作树：`git status --short` 为空；`git diff --check` 无输出。
- 结论：#122 的纯对话外壳与首次文生图闭环通过合并后前端验收、类型检查、生产构建及既有后端主验收；真实浏览器视觉、真实供应商、真实手机和完整账号组合仍留 #129/#127，旧 162 个展示断言按第 4 节退役、不以本结论冒充通过。
