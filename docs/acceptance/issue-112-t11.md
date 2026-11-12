# Issue #123 T11 验收记录：侧栏真实会话列表、搜索与重命名

## 1. 范围与结论

- 工单：#123（V3 纯对话 T11，父票 #112）。
- 目标：在 #122 的纯对话外壳中，把真实项目会话直接放进侧栏，不经过旧项目抽屉；支持服务端标题搜索、加载更多、切换会话、刷新恢复、新建对话和仅修改标题的元数据重命名；保留草稿离开提醒、账号失效清理和安全边界。
- 验收基线：集成分支 `codex/issue-112-integration` 的 `baf26e3`（T10 合并与验收文档提交后）。本票从该提交新建 `codex/issue-112-t123` worktree。
- 本票变更：`main/src/components/AppSidebar.vue`、`tests/support/workspace.ts` 的默认列表路由，以及新增 `tests/t123-sidebar-conversations.spec.ts`。没有修改后端生产代码、数据库迁移、SRS 参数或次数/槽位口径；列表、搜索和重命名继续调用既有真实项目 API。
- 结论：本票覆盖的侧栏会话列表、服务端搜索、分页去重、重命名元数据、草稿切换和账号失效晚响应场景通过前端测试、类型检查、生产构建和既有后端搜索/重命名回归；真实浏览器视觉与手机抽屉留 #129/#128。

## 2. 实现面

| 面 | 实现 |
|---|---|
| 直接列表 | `AppSidebar.vue` 挂载时调用既有 `loadProjects()`；`ul aria-label="对话列表"` 直接展示服务端 `ProjectSummary`，不打开 `ProjectDrawer`，不提供项目删除、本地业务库或跨设备同步 |
| 搜索 | 搜索表单 `role="search"`，输入框 aria-label `按对话标题搜索`；提交时调用 `setProjectQuery()`，由既有 API 先按当前账号标题过滤再分页；清空按钮恢复首页列表，不写业务数据 |
| 分页 | 使用 `nextCursor` 和既有 `loadMoreProjects()`；追加页按 id 去重，旧查询/旧账号结果由 store 的查询与 `sessionRevision` 守卫丢弃 |
| 切换与刷新 | 点击列表项调用 `selectProject()`，保留既有未发送草稿确认；当前项 `aria-current="page"`；直接打开 `/projects/:id/chat` 时从真实项目详情恢复 |
| 重命名 | 行内输入 aria-label `对话标题`，保存调用既有 `renameProject()`，仅发 `PATCH /api/projects/{id}` 的 `{title}`；不触发工作流动作、不改变 revision、不使提示词快照失效 |
| 状态/错误 | 加载、空列表、无匹配、首次加载失败、加载更多失败均有安全文案和重试；重命名失败保留原表单与原标题 |
| 生命周期 | 沿用 workspace 的 `sessionRevision` 守卫；账号失效或重新登录后，晚到的列表、搜索和重命名响应不再写入新会话状态 |
| 视觉/布局 | 保留 #122 的 288px 侧栏、圆角、颜色 token；会话区独立滚动，底部账号与用量、用量、数据使用说明和退出登录保持可见 |

## 3. 测试面

新增 `tests/t123-sidebar-conversations.spec.ts`，6 个测试函数/6 个用例，挂载真实 App、路由和 Element Plus；只替换 fetch、时钟、可见性和存储，不 mock 前端业务模块：

- T11-1 侧栏直接展示服务端会话；标题搜索请求在分页前发出，覆盖后页项目；无匹配显示 `没有标题匹配“不存在”的对话`；清空恢复列表；全程无业务写请求。
- T11-2 `加载更多` 按游标请求并去重；旧的延迟搜索响应不能覆盖当前搜索。
- T11-3 重命名只发送 `{title}`，同时更新当前标题和侧栏；不重新加载详情、不提交工作流动作、不携带 revision。
- T11-4 重命名失败显示安全错误 `标题保存失败，请重试`，保留原名和打开的表单。
- T11-5 直接链接恢复真实会话并标出当前项；有未发送草稿时切换先弹出 `放弃未保存的修改？`，确认后切换项目并清空输入框，不执行工作流动作。
- T11-6 账号生命周期递增后，晚到的重命名响应不能写入侧栏；清空后的空态为 `暂无对话。新建对话后会显示在这里。`。

## 4. 命令与结果

工作目录：`C:/Users/28425/.codex/worktrees/issue-112-t123/新建文件夹`。

```powershell
D:/nodejs/node.exe node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.json
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run tests/t123-sidebar-conversations.spec.ts --reporter=dot
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run --reporter=json --outputFile=D:/Desktop/issue-112-context/t123-full1.json
D:/nodejs/node.exe node_modules/vite/bin/vite.js build --config main/vite.config.ts
```

后端回归（既有真实 HTTP + 临时 SQLite 测试，无本票生产代码变更）：

```powershell
cd C:/Users/28425/.codex/worktrees/issue-112-t123/新建文件夹/backend
C:/Users/28425/.codex/worktrees/issue-112-integration/新建文件夹/backend/.venv/Scripts/python.exe -m pytest tests/test_v3_p4_t14_project_search.py tests/test_issue_121_t09.py -q -p no:cacheprovider --basetemp=D:/Desktop/issue-112-context/t123-backend1
```

结果：

- 类型检查：通过。
- T11 专项：`6 passed`（5.92s）。
- 当前前端全套：12 个 spec 文件，`41 passed / 41`，24 个 Vitest 套件，全部通过；JSON `D:/Desktop/issue-112-context/t123-full1.json`。
- 生产构建：通过（1654 modules；仅有既有大 chunk 警告）。
- 后端回归：`22 passed in 79.07s`（`test_v3_p4_t14_project_search.py` 7 个搜索/分页/重命名/状态用例 + `test_issue_121_t09.py` 15 个工作流用例）。
- 静态检查：`git diff --check` 无输出；未在 `main/` 下新增测试、截图或临时产物。

## 5. #123 AC 逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| 1 复用服务端项目 API；列表直接可达，不在项目抽屉后；搜索覆盖本人全部标题且先于分页 | 通过 | T11-1 断言直接 `对话列表`、首次请求 `q=null/limit=20`、搜索请求携带标题且命中未加载页；后端 `test_v3_p4_t14_project_search.py` 的 unloaded-page 搜索覆盖 |
| 2 新建/切换/刷新恢复真实会话；重命名只改元数据，不改需求 revision、不使有效快照失效、不触发模型 | 通过 | T11-3/T11-5 断言行内保存与直接链接恢复；T11-3 断言无详情重载、无工作流 POST；后端 rename metadata-only 用例及 #121 的 prompt_preview/ledger 不变量 |
| 3 本人过滤、跨页搜索、快速切换、晚搜索请求；草稿离开提醒和账号失效清理接入当前生命周期 | 通过 | T11-2 晚搜索不覆盖；T11-5 草稿对话框；T11-6 `sessionRevision` 递增后晚重命名不写入；后端搜索按 owner 过滤、cursor 绑定 query/owner |
| 4 不删除项目、不建本地业务库、不做跨设备同步 | 通过 | 侧栏无删除入口；无本地持久化业务数据；只调用既有 REST API |
| 5 仅隔离环境；保护用户未提交改动；测试/文档/截图/临时产物不入应用本体 | 通过 | 只在当前 worktree 修改；原用户树未操作；测试在 `tests/`，文档在 `docs/acceptance/`；构建产物 `main/dist/` 被 gitignore |
| 6 新入口遵守身份/所有者/对象关联/内容门禁/CSRF 来源/审计 | 通过（沿用既有接口） | 未新增后端写接口；列表/详情只读，重命名走既有 PATCH 的会话、owner、CSRF 与审计路径；前端不绕过 store 的 `expected_revision` 和 `sessionRevision` 守卫 |
| 7 不改 SRS 参数或次数口径；不新增免费预览、自动超时、隐式排队、自动图片重试 | 通过 | 无后端/迁移/SRS 变更；列表、搜索、重命名均不触发工作流或生成；无定时器重试或队列逻辑 |

## 6. 旧展示测试替换关系

#122 已按 R3 外壳替换退役旧 V2 展示 spec；本票把其“项目列表/搜索/重命名”责任落到 T11-1—T11-6，并保留既有认证/安全/历史相关 spec。未恢复旧 `需求工作台`、旧 `ProjectDrawer` 或隐藏旧面板；#124—#130 继续分别覆盖附件、状态恢复、历史、账号生命周期、响应式和真实浏览器验收。

## 7. 残余未知与边界

- 未做真实浏览器、同尺寸视觉对照、真实手机抽屉/软键盘或真实供应商验收；#129/#128 负责。
- 搜索由显式 Enter/表单提交触发，没有输入防抖；这是当前轻量交互的明确实现，不宣称实时搜索。
- 侧栏一次显示服务端分页的前 20 项，更多数据通过游标加载；不缓存跨设备或离线标题。
- 重命名只校验前端长度并依赖后端 1—120 字符校验；失败文案来自既有安全错误映射。
- 前端测试使用受控 fetch 与固定响应，不证明真实网络时延、并发和错误文案；后端搜索/重命名由既有 HTTP + SQLite 测试证明。

## 8. 合并复核（Lead）

- 功能提交：`67d9b162acb796586b03fae199a8f3d88062ebd7 feat(chat): add real conversation list search and rename (#123)`（4 个文件，+636/−6；侧栏列表/搜索/重命名、测试和验收文档，无后端生产代码或迁移变更）。
- 集成合并：`845dc08c1a85af35aa87a51ebe6c2acee19d062d merge: T11 sidebar conversation list search and rename (#123)`，以 `--no-ff` 合入 `codex/issue-112-integration`（第一父提交 `baf26e3`）。
- 合并后前端验收：12 个 spec 文件，`41 passed / 41`（JSON `D:/Desktop/issue-112-context/t123-postmerge.json`）；`vue-tsc --noEmit -p tsconfig.json` 通过；`vite build --config main/vite.config.ts` 通过（1654 modules，既有大 chunk 警告）。
- 合并后后端回归：`test_v3_p4_t14_project_search.py` + `test_issue_121_t09.py` 共 `22 passed in 73.23s`（basetemp `D:/Desktop/issue-112-context/t123-postmerge-backend`）；本票未改后端生产代码。
- 测试文件一致性：`git diff 67d9b16..845dc08 -- tests/t123-sidebar-conversations.spec.ts docs/acceptance/issue-112-t11.md` 无输出；合并后测试文件 SHA-256 为 `a9d812202b642a33acf551e9c82add1af379d27131938db6919f0b1e8e451a78`，与功能提交一致。
- 合并后工作树：`git status --short` 为空；`git diff --check` 无输出。
- 结论：#123 的侧栏真实会话列表、搜索、分页、切换、草稿提醒和元数据重命名通过合并后前端验收、类型检查、生产构建及既有后端搜索/重命名回归；真实浏览器视觉与手机抽屉留 #129/#128。
