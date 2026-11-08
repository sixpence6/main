# T18 无消费者旧呈现清理与交付证据收尾（Issue #130）

- 仓库：`sixpence6/main`
- 工作树：`D:\Desktop\新建文件夹\.worktrees\v130`
- 分支：`codex/issue-130-t18`
- 代码基线：`visual/v3@fe8f9f0`（`docs(acceptance): record T19 visual acceptance evidence (#143)`）
- 审计时 HEAD：`fe8f9f0`（工作树干净；随后仅做本报告所述 dead helper 删除与回归测试）
- 交付提交：本报告与清理所在提交（`git log -1`）
- 父 issue：#112（未修改、未关闭）
- 验收日期：2026-11-09
- 记录文件：`docs/acceptance/issue-130-t18.md`

## 结论

T18 的消费者审计未发现仍需删除的**有运行时/路由/测试消费者**旧呈现。V7（#152）已删除 8 个死组件、旧令牌别名和 `useWorkspace` 的旧抽屉状态；当前 `main/src` 的 36 个 `.ts`/`.vue` 源模块中，除类型声明 `env.d.ts` 外均从构建入口 `main/src/main.ts` 可达。新界面只有一个聊天主体、一个 `#workspace-prompt` 文本输入和一个文件输入，没有旧工作台隐藏副本、第二输入框、阶段导航或独立成果切换。

T18 额外清理了一个已证明零消费者的内部遗留 helper：`useWorkspace.currentProjectParam()`。它只读取 `window.location.search` 的旧 `project` 参数，删除前在 `main/src` 和既有 `tests/` 均无引用；实际旧 query 兼容由 `routeProjectId()`（`params.projectId` 或 `query.project`）承担。该删除不改变任何用户可见行为或路由契约，且因函数未进入运行时路径，生产构建哈希保持不变。

因此本票的应用代码改动**仅限删除该 dead helper**，并新增一个静态回归测试；没有重构、没有新增界面、没有修改 API/后端/依赖。按 #130 的删除条件逐项证明后，其余候选均无“替代行为已成立且当前无反例”的旧呈现可再删。保留项、内部残留及其原因见下方审计表与“有意保留”。

现有验证结果：

| 项目 | 结果 |
| --- | --- |
| 消费者审计 | 静态引用、动态加载、路由注册、构建入口、测试引用均无待删旧呈现；唯一零消费者 helper 已删除 |
| 专项回归 | `t130-t18-cleanup` + `t152-v7-cleanup` + `t126-history` + `t105-results-history` + `t122-pure-chat`：5 文件 / 30 测试通过 |
| 全量前端测试 | 25 文件 / 211 测试通过 |
| `npm run typecheck` | 通过（exit 0，无输出） |
| `npm run build` | 通过；94 modules，产物与 T19 验收构建哈希一致（dead helper 被 tree-shake，bundle 不变） |
| T19 复用 | 8/8 验收项、96/96 步骤，受控 mock + Edge headless，不是真实供应商/真实设备证明 |

## 验收对象与证据边界

### 代码基线

`fe8f9f0` 相对 T19 验收代码基线 `5fc0dd5` 仅增加验收文档、基线 PNG 和证据 JSON，没有应用代码改动：

```text
docs/acceptance/baselines/t19/*.png
docs/acceptance/baselines/t19/README.md
docs/acceptance/evidence/t19-browser-result.json
docs/acceptance/issue-143-t19.md
```

因此 T19 的浏览器结果可以作为当前代码基线的界面证据复用；本票又用当前 HEAD 重新构建，产物哈希与 T19 完全一致（见“构建”）。这不把 T19 的受控 mock 环境扩大解释为真实图片供应商或真实手机设备。

### 当前入口

`main/src/main.ts` → `App.vue` → `router/index.ts` → `WorkspaceLayout.vue`；`/`、`/projects/:projectId/chat` 使用 `ChatPage.vue`，`/projects/:projectId/results` 使用只读 `ResultsPage.vue`。登录、账号安全和账号管理路由各自使用 `LoginPage.vue`、`SetPasswordPage.vue`、`AccountSecurityPage.vue`、`AdminAccountsPage.vue`。

构建入口只包含 `main/index.html` 与 `main/src/main.ts`；`main/vite.config.ts` 以 `main` 为根，产物输出 `main/dist`。`main/dist` 当前只有 `index.html`、`index-*.css`、`index-*.js` 三个文件，没有测试、文档、截图或临时产物。

## Issue #130 验收对照

| # | 验收条件 | 本票结论 | 证据与边界 |
| --- | --- | --- | --- |
| 1 | 以引用／路由／运行证据判断旧面板、旧必经阶段导航和状态计算的消费者；只有替代成立后才删除，不清用户历史或无关残留 | **通过（静态+现有运行测试）** | 下方消费者审计；8 个 V7 死组件无文件/引用；旧四阶段页面无源文件/无路由；`t130` 2/2、`t152` 11/11；全量 211 测试 |
| 2 | 保留生成、审查、安全、恢复及旧 URL 只读兼容；新界面无旧工作台隐藏副本或第二输入框 | **通过（界面/静态）** | `ChatPage` 单输入框/单文件输入；`t122` 空态、`t126` 第二输入框断言；`ResultsPage` 只读；`/describe` 等 4 条重定向；T19 c2/c4/c5 |
| 3 | 清理后复验受影响接口、工作台交互、构建、访问、恢复和零重发，更新当前证据 | **部分通过（前端/构建/T19）** | 本票未启动真实后端 SQLite/供应商，不把受控 mock 当前端接口证明；前端 211 测试、build、T19 c4/c5/c7/c8 为当前证据；真实后端和真实设备保持未知 |
| 4 | 只把有当前证据的项目标完成，保留失败／未知和真实环境缺口；测试、文档、截图、临时产物不进应用/发布包 | **通过** | 本报告明确区分通过/未知；`main/dist` 仅 3 个构建文件；`main/` 内未新增测试/文档/截图 |
| 5 | 不自动修改或关闭父 issue，不以票关闭代替 v3 发布验收 | **通过** | 未操作 #112；本票不声明 v3 发布验收 |
| 6 | 只在隔离环境验证，不操作真实用户数据、凭据或运行配置，保护未提交修改 | **通过** | 使用当前工作树、临时 mock 数据和既有测试夹具；未读取或写入真实账号数据；工作树基线干净，新增内容仅为报告、回归测试和 dead helper 删除 |
| 7 | 所有新增入口遵守身份、所有者、对象关联、内容门禁、CSRF/来源与审计约束 | **不适用（无新增入口）** | 本票不改后端、API、路由入口或鉴权；仅新增仓库根 `docs/acceptance/` 文档 |
| 8 | 不改变 SRS 参数/次数口径，不新增免费预览、自动等待超时、隐式排队或自动图片重试 | **通过（无行为改动）** | 不修改业务代码、测试夹具语义或配置；仅审计与记录 |

> 结论栏的“通过”只覆盖本票实际执行的前端静态/组件/构建与 T19 界面证据。真实后端接口、真实供应商、真实手机软键盘/中文输入法和完整发布环境不在本票完成声明内。

## 消费者审计

### 方法

1. **静态引用**：`git grep` 搜索旧组件名、旧页面名、旧工作台标签、旧选择器和旧 URL 状态字段。
2. **动态加载**：搜索 `defineAsyncComponent`、动态 `import(`；唯一命中为 `main/src/api/types.ts:248` 的类型级 `import('./workflow').WorkflowView`，不产生额外运行时模块。
3. **路由注册**：读取 `main/src/router/index.ts`，确认只有当前聊天、只读结果、登录/账号路由和旧路径重定向。
4. **构建入口**：从 `main/src/main.ts` 做模块可达性遍历（脚本仅读取 `main/src`，输出 36 个 `.ts`/`.vue` 源模块；35 个可达，唯一不可达为 `env.d.ts` 类型声明；3 个 CSS 分别由 `main.ts`（`tokens.css`、`base.css`）和 `WorkspaceLayout.vue`（`workspace.css`）副作用导入）。
5. **测试引用**：运行 `t130`、`t152`、`t126`、`t105`、`t122` 及全量套件，确认旧路径、旧组件缺失、单输入框和只读结果行为。

### 审计表

| 候选 | 静态/动态/路由/构建/测试证据 | 处理 |
| --- | --- | --- |
| `DescribePage.vue`、`ConfirmPage.vue`、`GeneratePage.vue`、`StageNav`、`PageTemplate` 等旧四阶段入口 | `git ls-files main/src` 无这些文件；`main/src` 无引用；路由只注册 `ChatPage`/`ResultsPage`；构建入口可达性无相关模块；旧 `r3-gate.md` 是历史记录而非当前源文件证据 | 已在 R3/V7 删除，T18 不再改动 |
| `ConversationPanel`、`ComposerPanel`、`BriefCard`、`HistoryPreview`、`ModelStatusDrawer`、`ProjectDrawer`、`ReferenceUploader`、`workflow/WorkflowPanel` | 8 个组件文件均不存在；`git grep` 在 `main/src` 零引用；`tests/t152-v7-cleanup.spec.ts` 的 “dead component chain is absent” 通过 | 保持删除；V7 已交付 |
| 旧工作台隐藏副本、阶段卡、任务/工作流面板文案 | `main/src/pages/ChatPage.vue` 无 `需求工作台`、`工作流面板`、`阶段导航`、`旧工作台`、`独立成果`、`第二个输入`、`第二输入框` 等标签；`WorkspaceLayout.vue` 只有侧栏/顶栏/聊天主体；`v-show` 计数为 0 | 无候选可删；新界面无隐藏副本 |
| 第二输入框/重复 composer | `ChatPage.vue` 仅 1 个 `<textarea>`（`#workspace-prompt`）、1 个 `type="file"` 输入、1 个 `footer.composer-area`；`ComposerPanel` 已不存在；`t122` 空态只显示“一个输入框”；`t126` 历史提示词测试断言无第二输入框 | 保持单输入框结构 |
| 旧 `workspace.css` 选择器/规则 | `main/src/styles/workspace.css` 355 行；`workspace-shell`、`stage`、`workflow`、`result-panel`、`conversation-panel`、`brief-card`、`history-preview`、`model-drawer`、`project-drawer`、`composer-panel` 均 0 匹配；V7 已从 2794 行裁剪到 417 行，T19 前审查又缩减到 355 行 | 无旧段落可删；保留当前布局/侧栏/响应式规则 |
| `/describe`、`/confirm`、`/generate`、`/results` 旧外部路径 | `router/index.ts` 四条均 `redirect` 到命名路由 `describe`，保留 `query`/`hash`；无 `component`、无 action 调用；`tests/t126-history.spec.ts` 逐条 mount，断言标题、当前路径 `/`、`fetch.writes.length === 0`；`tests/t55-auth.spec.ts` 覆盖未登录 `/generate?project=...` 被守卫；`tests/v2-t20-retention.spec.ts` 覆盖 `/results?project=...` 落到 describe | 保留为无副作用重定向；不删除 |
| `ResultsPage.vue` / `project-results` 只读结果 | 当前 `ResultsPage.vue` 313 行，只显示已发布且可下载版本；`selectVersion` 只 `router.push`；`backToModify` 只返回聊天；无写请求、无模型调用、无自动跳转；`ChatPage.vue` 结果卡 `RouterLink` 使用 `project-results`；`t105` 4/4、`t126` 结果链接测试通过 | 保留；它是只读历史定位兼容，不是旧创作主流程结果页 |
| `useWorkspace.currentProjectParam()` | 原 `main/src/composables/useWorkspace.ts:145` 曾读取 `window.location.search` 的 `project`；删除前在 `main/src` 和既有 `tests/` 零引用；实际旧 query 兼容由 `routeProjectId()`（`params.projectId` 或 `query.project`）承担；`tests/t130-t18-cleanup.spec.ts` 断言 helper 与 `window.location.search` 不再出现、`routeProjectId`/`query.project` 保留 | **已删除**：零消费者旧 URL 状态 helper；替代路径已存在；删除后 bundle 哈希不变，T19 证据仍精确对应 |
| `useWorkspace.healthStatus`、`sidebarExpanded`、`toggleSidebar` | 无模板/测试外部消费者；`healthStatus` 仅由 `checkHealth` 写入，`sidebarExpanded` 仅由断点 watch 和 `toggleSidebar` 写入；当前布局不使用展开/收起状态 | **有意保留**：属于 store 内部表面和健康探测，不是旧 UI；删除会改变公共返回对象与健康请求行为，超出“零风险呈现清理” |
| `AppHeader`、`AuthFrame`、`PasswordField`、账号/登录页 | `AppHeader`/`AuthFrame`/`PasswordField` 被 `AccountSecurityPage`、`AdminAccountsPage`、`LoginPage`、`SetPasswordPage` 使用；这些路由仍在 `router/index.ts` | 保留；不属于旧工作台呈现 |
| `useWorkflow`、`workflow/projection.ts` | `ChatPage.vue`、`ResultsPage.vue` 与 `useChatFlow` 使用真实工作流投影、动作和结果状态；`projection.ts` 被 `useWorkflow`/测试使用 | 保留复用业务能力，不能随旧组件删除 |
| `AppSidebar`、`SideDrawer`、`CurrentStatusCapsule`、`StatusAnnouncer`、`UnsavedChangesDialog` | 当前 `WorkspaceLayout.vue` 模板直接使用；`t147`、`t151`、`v1` 等测试覆盖；侧栏/抽屉/状态胶囊是当前 V3 外壳 | 保留 |
| 旧截图、测试、临时产物 | `main/dist` 只有 3 个构建文件；`main/` 内无新增验收产物；T19 截图/JSON 位于 `.scratch/t19-ui/evidence/`，基线位于 `docs/acceptance/baselines/t19/` | 不进入应用本体或发布包 |
| 构建入口孤儿模块 | 36 个 `.ts`/`.vue` 源模块，35 个从 `main.ts` 可达；唯一不可达 `main/src/env.d.ts` 是类型声明，无运行时 import；3 个 CSS 由 `main.ts`/`WorkspaceLayout.vue` 副作用导入 | 无孤儿组件/页面可删 |

### 动态加载与路由补充

- `defineAsyncComponent`：0 处。
- 动态 `import(`：仅 `main/src/api/types.ts:248` 的类型引用，不改变运行时加载图。
- 当前路由：`/login`、`/set-password`（public）、`/account/security`、`/admin/accounts`（requiresAdmin）、`/` 下的 `describe`、`project-chat`、`project-results`，以及 4 条旧路径重定向和 catch-all。
- 没有 `StageNav`/`PageTemplate`/旧四页组件注册；没有旧路径 `component` 直载；没有自动进入结果页的发布跳转。

## 旧链接与只读兼容

### 旧路径

```text
/describe  -> { name: 'describe', query, hash }
/confirm   -> { name: 'describe', query, hash }
/generate  -> { name: 'describe', query, hash }
/results   -> { name: 'describe', query, hash }
/:pathMatch(.*)* -> { name: 'describe' }
```

- 重定向不调用 workflow action、不创建项目、不提交生成、不重放确认/恢复。
- `tests/t126-history.spec.ts` 的“旧阶段链接只定位到对话入口，不触发准备或生成”逐条覆盖四条路径，并断言 `fetch.writes` 为空。
- 带 `?project=` 的旧链接仍由 `useWorkspace` 的 `routeProjectId()` 读取 query，加载对应本人项目；账号/归属校验由既有 API 和路由守卫承担，T19 c5 S8.3 覆盖账号 B 打开账号 A 旧项目时显示 not-found 且不泄漏内容。
- 未登录访问 `/generate?project=abc` 由 `t55-auth` 覆盖，落到登录流程。

### 只读结果

- `/projects/:projectId/results?version=<runId>` 仍由 `ResultsPage.vue` 提供；`ChatPage` 的“在结果页只读查看”链接保留该真实版本定位（`t126` 断言 href）。
- 结果页只展示已发布、可下载版本；请求版本不可用时显示安全状态，不用当前图兜底；`selectVersion` 只改变 `route.query.version`；`backToModify` 只回到聊天；下载使用真实资源 URL。
- 无自动成果跳转；发布后仍追加在原对话，`t126`/T19 c5 覆盖刷新不重复追加。

## 全流程、恢复与零重发

T19 的当前界面证据（`docs/acceptance/evidence/t19-browser-result.json`，`visual/v3@5fc0dd5`，当前构建哈希一致）与本票专项测试共同覆盖：

| 场景 | 证据 | 结果 |
| --- | --- | --- |
| 空态、单输入框、建议只填充不发送 | T19 c1 9/9；`t122` 空态测试 | 通过 |
| 需求卡→提示词检查→待确认→复制→确认→执行→结果 | T19 c2 26/26；`t122` 主流程测试 | 通过（受控 mock 界面） |
| 失败恢复、unknown 只核实/恢复、原地更新、不自动重试 | T19 c4 17/17；`t149` 执行/恢复测试；`t125` 任务状态测试 | 通过（界面/组件） |
| 刷新不重复追加、旧链接、账号切换 | T19 c5 8/8；`t126`、`t127` | 通过（界面/组件） |
| 320/768/1440 响应式 | T19 c7 13/13；`t128` | 通过（CDP 视口） |
| 200% 缩放 | T19 c8 8/8 | 通过（受控视口） |
| 发送/确认零重发 | `t122`、`t125`、`t126` 的 `fetch.writes`/POST 计数断言；T19 c2 单次 `confirm_prompt` 记录、c5 S8.1 刷新无重复 POST | 通过（前端/受控 mock） |

T19 关键值：

- 8/8 验收项，96/96 步骤，失败 0，已知缺陷 0；25 张截图，9 张规范基线共 519,542 B。
- c5 S8.1：刷新前后消息数 1/1，POST 列表为空。
- c5 S8.3：账号 B 打开账号 A 旧项目显示“项目不存在或已被删除，请从项目列表重新选择。”，`leaked: false`。
- c5 S8.6：无 session 访问落到 `/login`。
- c4 S7.2：失败态只出现服务端允许的“安全恢复生成结果”，没有 cancel/retry/regenerate。

本票不把上述受控 mock 结果标成真实供应商、真实后端 SQLite 或真实手机输入法证据；这些仍是未知项。

## 测试、类型检查与构建

### 专项

```bash
NODE_OPTIONS="--require D:/Desktop/新建文件夹/.scratch/v2-ui/no-exec.cjs" \
npx vitest run \
  tests/t130-t18-cleanup.spec.ts \
  tests/t152-v7-cleanup.spec.ts \
  tests/t126-history.spec.ts \
  tests/t105-results-history.spec.ts \
  tests/t122-pure-chat.spec.ts \
  --pool=threads --reporter=dot
```

结果：**5 files passed，30 tests passed**，耗时约 4.56 s。

其中：

- `t130-t18-cleanup`：2/2，断言旧 `currentProjectParam`/`window.location.search` 不再出现，且 `routeProjectId`/`query.project` 兼容保留。
- `t152-v7-cleanup`：11/11，覆盖 8 个死组件缺失、旧令牌别名删除、旧 drawer 状态删除、token/焦点/字号/断点门禁。
- `t126-history`：7 个测试通过，包含旧四路径零写入、结果只读链接、历史提示词无第二输入框。
- `t105-results-history`：4 个测试通过，覆盖只读结果版本、不可用状态和历史保留。
- `t122-pure-chat`：6 个测试通过，覆盖空态单输入、主流程、零提前请求。

### 全量

```bash
NODE_OPTIONS="--require D:/Desktop/新建文件夹/.scratch/v2-ui/no-exec.cjs" \
npm test -- --pool=threads --reporter=dot
```

最终结果：**25 files passed，211 tests passed**，耗时约 8.45 s。

测试说明：加入 `t130` 前，第一次并行全量运行（24 files / 209 tests）出现 1 个 `tests/t125-task-status.spec.ts:266` 时序失败（160 ms mock 动作在负载下先完成，发送按钮已恢复可用），隔离重跑该文件 **11/11** 通过，随后全量重跑 **209/209** 通过；加入 `t130` 后最终全量为 **211/211**。没有为 t125 修改应用代码或测试；记录为并行时序 flake 的残余风险，不把它当作产品功能失败，也不把第一次失败隐藏掉。

### 类型检查

```bash
npm run typecheck
```

结果：exit 0，无输出。

### 构建

```bash
NODE_OPTIONS="--require D:/Desktop/新建文件夹/.scratch/v2-ui/no-exec.cjs" npm run build
```

结果：exit 0，94 modules，409 ms。

| 产物 | 大小 | gzip |
| --- | ---: | ---: |
| `main/dist/index.html` | 0.45 kB | 0.31 kB |
| `main/dist/assets/index-DjNoueTg.css` | 85.57 kB | 12.67 kB |
| `main/dist/assets/index-BavlKGdi.js` | 259.09 kB | 87.26 kB |

两个资产哈希与 `docs/acceptance/issue-143-t19.md` 记录的 `index-DjNoueTg.css`、`index-BavlKGdi.js` 完全一致；而 `5fc0dd5..fe8f9f0` 只有验收文档/基线改动。T18 删除零消费者 `currentProjectParam()` 后重建仍是这两个哈希（该函数被 Vite tree-shake），因此当前工作树 bundle 与 T19 构建精确一致，T19 界面证据在本票中复用是可行的；但本票未重新运行 T19 浏览器 runner，仍以 T19 原始报告为界面证据，且其环境是受控 mock + Edge headless。

## 有意保留

以下保留均为有消费者、业务复用、旧链接兼容或证据稳定性所需，不作为“未清理”缺陷：

1. **只读 `ResultsPage.vue` 与 `project-results` 路由**：旧成果路径的只读历史定位兼容；无自动跳转、无生成动作。
2. **四条旧路径重定向**：外部书签兼容；只跳转到聊天入口，保留 query/hash，不重放动作。
3. **`ChatPage.vue` 的单输入框/单文件输入**：新 V3 唯一入口，不是第二输入框。
4. **`WorkspaceLayout.vue` 的 legacy 隔离横幅**：`currentProject?.legacy_unreviewed` 的 v2 内容门禁，属于保留的安全/迁移兼容，不是旧工作台。
5. **`AppHeader`、`AuthFrame`、`PasswordField`、账号/登录页**：登录、账号安全和管理的现有真实入口。
6. **`useWorkflow`、`projection.ts`、`useChatFlow`**：真实生成、审查、恢复和结果投影的业务能力；不能随旧组件删除。
7. **`useWorkspace` 的 `healthStatus`、`sidebarExpanded`/`toggleSidebar`**：无可见消费者的内部表面；删除不改善用户行为，且会改变公共 store 形状与健康探测。T18 未把它们误当成旧呈现删除；`currentProjectParam()` 已作为明确的旧 URL 状态 helper 单独删除。
8. **V7 既有视觉例外**：`ChatPage.vue` 的 `.result-prompt` 10px、`.chat-error` 12px，以及 `AuthFrame`/账号页既有媒体查询；属于已记录历史例外，T18 不扩大视觉改动。
9. **历史文档、基线、旧迁移/恢复兼容**：不清用户历史、不清旧数据库升级历史、不删除测试；这些不是应用呈现残留。

## 未知与真实环境缺口

- **真实后端/真实 SQLite**：本票未启动真实临时 SQLite 或后端独立进程；`npm test` 与 T19 使用组件 harness/受控 mock。因此不将后端接口、迁移、真实并发/重启行为标为本票通过；相关结论沿用 #112 已关闭切片，但本报告不重新认证。
- **真实供应商**：T19 mock 不是真实图片供应商；没有真实模型调用、真实计费/限额和真实远端未知状态证据。
- **真实手机与输入法**：T19 为 Edge headless CDP 视口，未覆盖真实手机软键盘、中文 IME 和物理设备安全区；仅覆盖 320/768/1440 CSS 像素和 200% 缩放。
- **发布性能**：T19 明确不是 release 性能测试；本票 build 只证明构建成功与体积，不证明生产容量。
- **测试时序 flake**：首次并行全量运行的 `t125` 单测时序失败未在代码层修复，隔离与全量重跑通过；若 CI 负载更高仍可能复现，需后续单独稳定该测试。
- **残留内部状态表面**：`healthStatus`、`sidebarExpanded`/`toggleSidebar` 等仍是 store 内部无外部消费者的表面；本票按“不是旧呈现、删除会改变公共形状/健康探测”保留，未宣称“源码零死代码”。
- **T19 与本次源码改动**：T18 删除了零消费者的 `currentProjectParam()`，但构建产物哈希与 T19 完全一致（tree-shaken），故 T19 的界面证据仍精确对应当前 bundle；本票没有重新运行 T19 浏览器 runner，无法提供“清理后新一次浏览器运行”的独立时间戳。

## 边界确认

- 应用代码仅删除 `main/src/composables/useWorkspace.ts` 中的零消费者 `currentProjectParam()`；未修改后端、API 契约、依赖、数据库迁移、鉴权或工作流参数。
- 未修改或关闭父 issue #112；未推送分支。
- 未在 `main/` 内新增测试、文档、截图或临时产物；报告位于仓库根 `docs/acceptance/`，回归测试位于仓库根 `tests/`。
- 未操作真实用户数据、凭据或运行配置；审计只读取工作树和临时 mock fixture。
- 新增文件为 `tests/t130-t18-cleanup.spec.ts` 与本报告；T19 的 25 张截图和 JSON 仍在 `.scratch/t19-ui/evidence/`，规范基线在 `docs/acceptance/baselines/t19/`，不属于应用构建输入。

## 复现命令摘要

```bash
cd "D:/Desktop/新建文件夹/.worktrees/v130"

# 静态审计
git grep -nE 'ConversationPanel|ComposerPanel|BriefCard|HistoryPreview|ModelStatusDrawer|ProjectDrawer|ReferenceUploader|WorkflowPanel' -- main/src
git grep -nE 'ConfirmPage|GeneratePage|DescribePage|StageNav|PageTemplate|ResultRetrievalPanel|GenerationRiskDialog|workspace-shell' -- main/src
git grep -nE 'defineAsyncComponent|import\(' -- main/src
git grep -nE '需求工作台|工作流面板|阶段导航|旧工作台|独立成果|第二个输入|第二输入框' -- main/src

# 专项
NODE_OPTIONS="--require D:/Desktop/新建文件夹/.scratch/v2-ui/no-exec.cjs" \
npx vitest run tests/t130-t18-cleanup.spec.ts tests/t152-v7-cleanup.spec.ts \
  tests/t126-history.spec.ts tests/t105-results-history.spec.ts \
  tests/t122-pure-chat.spec.ts --pool=threads --reporter=dot

# 全量
NODE_OPTIONS="--require D:/Desktop/新建文件夹/.scratch/v2-ui/no-exec.cjs" \
npm test -- --pool=threads --reporter=dot

# 类型/构建
npm run typecheck
NODE_OPTIONS="--require D:/Desktop/新建文件夹/.scratch/v2-ui/no-exec.cjs" npm run build
```

审计过程原始输出保存在 `.scratch/t18-audit/`（工作树外的临时审计目录，不进入应用或提交）。
