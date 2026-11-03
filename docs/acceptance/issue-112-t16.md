# Issue #128 T16 验收记录：键盘、滚动、窄屏抽屉与真实手机输入

## 1. 范围与结论

- 工单：#128（V3 纯对话 T16，父票 #112）。
- 目标：完整新对话在键盘、中文输入法、独立滚动、三种宽度和真实手机软键盘下可操作；只做当前行为切片的组合验证、必要修复和受控清理，不恢复旧工作台或旧结果页交互。
- 验收基线：集成分支 `codex/issue-112-integration` 的 `147e0f51ffee6aae2e91244f2d6536ce5daf88e2`（T15 合并与验收文档提交后）。本票从该提交新建 `codex/issue-112-t128` worktree。
- 本票变更：`main/src/pages/ChatPage.vue`、`main/src/components/AccessibleDrawer.vue`、`main/src/components/SideDrawer.vue`、`main/src/layouts/WorkspaceLayout.vue`，以及新增 `tests/t128-responsive-keyboard.spec.ts`。未修改后端生产代码、数据库迁移、SRS 参数或次数/槽位口径。
- 结论：Enter、Ctrl/Meta+Enter 发送，Shift+Enter 换行，中文 composition 选字不会误发送；上滚阅读后新内容不强拉到底，出现“有新内容，回到底部”提示；窄屏使用与宽屏同一个侧栏组件的单层抽屉，具备 `aria-expanded`/`aria-controls`、背景滚动锁、焦点进入和关闭返回；图片放大使用可关闭的对话框并返回原图链接；附件、复制、最终确认等关键操作有可见标签和键盘焦点。真实手机软键盘、真实浏览器像素级溢出和真实设备交互证据尚未取得，保持未知，留 #129。

## 2. 实现面

| 面 | 实现 |
|---|---|
| 键盘发送 | `ChatPage.onComposerKeydown` 统一处理 Enter：普通 Enter、Ctrl+Enter、Meta+Enter 发送；Shift+Enter 不发送；`composing` 与 `event.isComposing` 时忽略，保证中文输入法选字不误发；Alt+Enter 不处理。 |
| 输入框自适应 | `composerInput` 在输入和草稿变化后执行 `resizeComposer`：先重置高度，再按 `scrollHeight` 增长，上限 180px；空草稿保持 44px 最小高度。 |
| 独立滚动 | `.chat-scroll` 独立滚动；`onScroll` 计算距底部距离，距底部 48px 内视为跟随最新内容。上滚后不再自动拉底，改为显示“有新内容，回到底部”按钮；点击后回到最新位置并清除提示。 |
| 固定输入不遮正文 | 输入区位于 `.chat-scroll` 之外的固定 footer；`.pure-chat`、`.pure-shell`、`.composer-area` 使用 `min-width:0`、`max-width:100%`、`min-height:0` 和 `overflow` 约束；输入区底部加 `safe-area-inset-bottom`。 |
| 窄屏抽屉 | `WorkspaceLayout` 的“打开侧栏”按钮增加 `aria-expanded` 和 `aria-controls="workspace-side-drawer"`；`AccessibleDrawer` 增加可选 `panelId` 并绑定到对话框 `id`；`SideDrawer` 传入 `workspace-side-drawer`，仍复用同一个 `AppSidebar` 单层抽屉，不新增第二套侧栏。 |
| 抽屉焦点/滚动 | 复用 `AccessibleDrawer` 既有行为：打开时保存原焦点并把 `body` 滚动锁住，关闭时恢复原 overflow 并把焦点返回触发按钮；Escape 关闭、Tab 焦点陷阱保持。 |
| 图片放大 | 图片历史中的原图链接保留既有 `href`、`target="_blank"`、`rel="noopener"` 和 `aria-label="查看大图：…"`；新增可见“查看大图”标签。点击时聚焦原链接并打开 `AccessibleDrawer`（标题“图片大图”、关闭按钮“关闭大图”），显示受保护资产 URL；关闭/Escape 后焦点回到原图链接。 |
| 可见标签与焦点 | 隐藏文件选择输入增加 `tabindex="-1"`，避免不可见控件进入 Tab 顺序；附件、复制、最终确认和发送按钮保留可见文本/aria-label；最终确认按钮在忙碌或预览不可确认时保持禁用，不产生隐式动作。 |
| 响应式与安全区 | `.pure-shell` 增加 `100vh/100dvh`、`max-width:100vw` 和底部安全区 padding；聊天面板、消息、输入区增加最小宽度与溢出约束；窄屏输入区增加底部安全区 padding。 |
| 后端与安全边界 | 无新增后端入口；键盘、滚动、抽屉和放大仅消费既有 workflow、detail 和受保护 `GET /api/assets/{id}` 数据；图片 URL 仍由 owner、保留期和 `can_access_asset` 门禁保护。 |

## 3. 测试面

新增 `tests/t128-responsive-keyboard.spec.ts`，5 个测试函数/5 个用例。测试挂载真实 App、路由和 Element Plus，只替换 fetch、时钟、页面可见性和存储；不 mock 前端业务模块或内部阶段。

- T16-1 `Enter、Ctrl 或 Meta+Enter 发送，Shift+Enter 换行，中文 composition 不发送`：发送计数和草稿文本逐次核对，覆盖普通 Enter、Shift+Enter、Ctrl+Enter、composition 期间 Enter 和 Meta+Enter。
- T16-2 `上滚阅读后新内容不强拉到底，出现新内容提示，点击后才回到底部`：需求确认触发新提示词内容时，先模拟上滚，断言 scrollTop 不被改写并出现提示；点击提示后才回到底部。
- T16-3 `320 和 768 使用同一侧栏单层抽屉，锁背景滚动、关闭后焦点回到触发按钮；1440 常驻侧栏`：两种窄宽度均验证同一“工作区菜单”对话框、`aria-controls`/`aria-expanded`、`body` overflow 锁、关闭按钮焦点和焦点返回；1440 验证侧栏常驻且无“打开侧栏”。
- T16-4 `图片放大有可见标签和键盘焦点，关闭后回到原图链接`：图片历史当前版本原图链接可聚焦，点击打开“图片大图”对话框并显示正确资产 URL；Escape 关闭后焦点返回原图链接，重复打开/关闭仍正确。
- T16-5 `唯一输入框和附件、复制、最终确认等关键操作均有可见标签且可聚焦`：只存在一个输入框；附件、复制、最终确认按钮可见且可聚焦；发送按钮在输入有效后可通过 Tab 到达。

RED 证据：实现前专项运行 5 个用例全部失败（`5 failed / 5`）。首个失败为打开侧栏按钮缺少 `aria-controls="workspace-side-drawer"`；其余分别暴露 Ctrl/Meta+Enter 不发送、上滚后无新内容提示、图片放大无可见标签、发送按钮在空草稿时不可聚焦。补齐上述缺口后 5/5 通过。

相关既有前端证据继续通过：

- `tests/t126-history.spec.ts` 的图片历史、当前/历史版本提示词隔离、下载链接和旧结果页只读定位；本票保留原图链接的既有 role/name/href，并新增放大对话框。
- `tests/t127-lifecycle-isolation.spec.ts` 的账号生命周期清理与晚到响应隔离。
- 全套 `tests/` 34 个 spec 文件、74 个用例全部通过，确认键盘和布局改动没有破坏聊天、附件、任务状态、历史与账号隔离能力。

后端既有回归（本票未改后端生产代码，33 个用例全部通过）：

- `tests/test_issue_121_t09.py`：同一消息入口、需求/提示词确认、最终执行、交付和受控适配器公开流程。
- `tests/test_v3_p1_t13_safe_projection.py`：任务状态进度、绑定、约束、动作 effect/read_only/requires_model_call、安全原因和权限边界。

## 4. 命令与结果

工作目录：`C:/Users/28425/.codex/worktrees/issue-112-t128/新建文件夹`。

```powershell
D:/nodejs/node.exe node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.json
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run tests/t128-responsive-keyboard.spec.ts --reporter=verbose
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run --reporter=json --outputFile=D:/Desktop/issue-112-context/t128-full2.json
D:/nodejs/node.exe node_modules/vite/bin/vite.js build --config main/vite.config.ts
```

后端回归：

```powershell
cd C:/Users/28425/.codex/worktrees/issue-112-t128/新建文件夹/backend
C:/Users/28425/.codex/worktrees/issue-112-integration/新建文件夹/backend/.venv/Scripts/python.exe -m pytest tests/test_issue_121_t09.py tests/test_v3_p1_t13_safe_projection.py -q -p no:cacheprovider --basetemp=D:/Desktop/issue-112-context/t128-backend1
```

结果：

- 类型检查：通过。
- T16 专项：`5 passed`（实现前为 5 failed）。
- 当前前端全套：34 个 spec 文件，`74 passed / 74`，全部通过；JSON `D:/Desktop/issue-112-context/t128-full2.json`（同结果的首次全量在 `t128-full1.json`）。
- 生产构建：通过（1654 modules；仅有既有大 chunk 警告；`main/dist/` 被 gitignore）。
- 后端公开流程与安全投影回归：`33 passed in 60.26s`（basetemp `D:/Desktop/issue-112-context/t128-backend1`）。
- 静态检查：`git diff --check` 无输出；未在 `main/` 下新增测试、截图或临时产物。

## 5. #128 AC 逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| 1 Enter 发送、Shift+Enter 换行且 composition 选字不发送；上滚时新消息不强拉底，固定输入不遮正文 | 通过（组件/DOM 层） | T16-1、T16-2；输入区和滚动区 CSS 约束见第 2 节。真实浏览器像素级遮挡仍由 #129 补充 |
| 2 320/768/1440 CSS 像素无横向溢出；窄屏是同一侧栏单层抽屉，焦点、背景滚动、关闭返回正确 | 通过（DOM/组件层）；真实像素证据未知 | T16-3 覆盖三种视口语义、同一 `AppSidebar` 单层抽屉、`aria-expanded/controls`、背景锁和焦点返回；jsdom 不能证明真实浏览器无横向溢出，留 #129 |
| 3 附件操作、复制、最终确认、恢复动作、图片放大与关闭有可见标签和键盘焦点，关闭回正确触发处 | 通过 | T16-4 放大/关闭/焦点返回；T16-5 附件、复制、最终确认和发送按钮可见可聚焦；T125 恢复动作仍由服务端 available_actions 驱动 |
| 4 真实手机软键盘下发送与输入可达，留实际交互证据；桌面模拟或缩放截图不能代签，缺设备保持未知 | 未知（未声明通过） | 无真实手机设备交互证据；本票仅完成桌面组件和 DOM 层验证，#129 后补真实设备/浏览器证据 |
| 5 仅隔离环境验证；不操作真实用户数据/凭据/运行配置；保护未提交修改；测试/文档/截图/临时产物不进应用本体 | 通过 | 仅在专用 worktree 修改；原用户树只读核对；测试在 `tests/`、文档在 `docs/acceptance/`；无运行配置或数据库变更；构建产物 gitignore |
| 6 所有新增入口从接入起遵守身份/所有者/对象关联/内容门禁/CSRF/来源/审计 | 通过（无新增后端入口） | 仅复用既有 owner 过滤、受保护资产和 workflow 接口；未新增写路由；后端回归通过 |
| 7 不改 SRS 参数或次数口径；不新增免费预览、自动等待超时、隐式排队或自动图片重试；按当前证据声明通过，缺失证据保持未知 | 通过 | 无后端/SRS/迁移/次数逻辑变更；未新增预览、超时、队列或自动重试；真实设备证据明确保持未知 |

## 6. 旧展示与测试替换关系

#122 已退役旧 V2 工作台展示 spec。本票不恢复旧工作台、旧项目抽屉、旧结果页自动跳转或第二输入表单；图片历史仍由 `t126-history.spec.ts` 的只读结果页定位和本票的聊天内放大覆盖。窄屏抽屉复用 `AppSidebar`，不新造独立导航组件。

## 7. 残余未知与边界

- 未取得真实手机软键盘、真实浏览器、真实触摸或真实屏幕阅读器证据；jsdom 只能验证 DOM、事件和焦点顺序，不能证明像素级遮挡、真实滚动惯性或软键盘安全区行为。#129 负责真实浏览器/视觉/设备验收，缺失证据保持未知。
- T16-3 的 320/768/1440 断言是视口变化下的组件结构、可访问属性和焦点行为，不是 CSS 像素截图；第 5 节已明确真实像素证据未知。
- T16-4 验证放大对话框的焦点进入/返回和受保护资产 URL，不验证真实图片解码、缩放质量或大图下载时延。
- 本票未改后端；公开流程和安全投影回归只证明既有 API 未受前端布局改动影响，不替代 #129 的完整集成验收。
- 既有 V2 测试的 `FINAL_CONFIRMATION_REQUIRED` 基线问题和本票无关。

## 8. 合并复核（Lead）

- 功能提交：`3c625d3f8fb52316858fca287aa753659f09668c feat(chat): support keyboard scrolling drawer and image zoom (#128)`（6 个文件，+646/−12；键盘发送与输入法保护、独立滚动和新内容提示、窄屏抽屉可访问属性、图片放大对话框、5 个测试和验收文档；无后端生产代码或迁移变更）。
- 集成合并：`d739fb6eca1cae686c126bef20f67e29e7b02e46 merge: T16 keyboard scrolling drawer and real phone input (#128)`，以 `--no-ff` 合入 `codex/issue-112-integration`（第一父提交 `147e0f5`，第二父提交 `3c625d3`）。
- 合并后前端验收：34 个 spec 文件，`74 passed / 74`（JSON `D:/Desktop/issue-112-context/t128-postmerge.json`）；`vue-tsc --noEmit -p tsconfig.json` 通过；`vite build --config main/vite.config.ts` 通过（1654 modules，既有大 chunk 警告）。
- 合并后后端公开流程与安全投影回归：33 个用例 `33 passed in 55.94s`（basetemp `D:/Desktop/issue-112-context/t128-postmerge-backend`）；本票未改后端生产代码。
- 测试文件一致性：`git diff 3c625d3 d739fb6 -- tests/t128-responsive-keyboard.spec.ts docs/acceptance/issue-112-t16.md` 无输出；提交内测试文件 SHA-256 为 `181beda7a09929e610cb1ef1d81b93884d72901e15866b271856a6b793b3e842`，功能提交与合并提交一致。
- 合并后工作树：`git status --short` 为空；`git diff --check` 无输出。
- 结论：#128 的 Enter/Shift+Enter/composition、独立滚动和新内容提示、320/768/1440 抽屉与常驻侧栏语义、图片放大与焦点返回、关键操作可见标签与键盘焦点，通过合并后前端全套、类型检查、生产构建和既有后端公开流程/安全投影回归；真实手机软键盘和真实浏览器像素证据未取得，保持未知并留 #129。
