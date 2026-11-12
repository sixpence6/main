# R3 跨标签、历史返回与账号绑定恢复验收记录

- Issue: #72
- 父 Issue: #52
- 分支: ticket/72
- 代码提交: 5a8eb02c8234e0db2cf7d247779e63104c43928d
- 验收日期: 2026-11-01
- 状态: 通过（R3 阶段条件；U1–U3 见 #73–#75，R3 门禁见 #77）

## 1. 结论与边界

本票建立浏览器侧的跨标签失效传播、历史返回身份核验和账号绑定恢复存储，使切号、跨标签、浏览器前进／后退、BFCache 返回及迟到回调都不串数据、不重复提交。

1. 跨标签只传播失效信号。BroadcastChannel 消息和 localStorage 回退键都只包含 type=session-invalidated 与随机 nonce，不携带账号、项目、请求标识、Cookie、CSRF、密码或业务正文。收到信号后本端重新查询 /api/auth/session，由服务端结果决定账号身份；信号本身不承担身份判断。
2. 登录生命周期由 account.id + session_id 组成。同一账号重新登录得到新的 session_id，会递增 sessionRevision；切号、退出、401 会话失效也会递增。工作台以 flush:"sync" 监听 sessionRevision，先中止列表、项目详情和生成轮询，再清理当前项目、消息、生成结果、需求草稿、参考图状态、列表、导航确认和私有浏览器存储。迟到回调必须同时匹配 sessionRevision、请求序号和项目 id，否则丢弃。
3. 历史返回先核验身份。popstate 和 pageshow(persisted) 会触发 ensureSession(true)；核验期间 sessionRevalidating 为真，WorkspaceLayout 隐藏旧业务并显示“正在重新验证登录状态…”。核验完成后才允许显示新身份的项目；旧账号项目、草稿、图片和恢复凭据不会回显。
4. 恢复存储按稳定账号和项目绑定。新键为 ecommerce-generation-pending:<accountId>:<projectId>，记录只含 projectId、requestId、confirmedRevision、acknowledgeUnknownPresent、acknowledgeUnknownValue 和 generationId，不含卡片正文、提示词、密码、Cookie 或 CSRF。已登录时只读取当前账号键，不自动认领旧的账号无关键或旧基键；写入账号键后清理旧键。
5. 重新登录只查询原意图。恢复流程先查已加载的 generations，再用 GET /api/generations/{id} 核实原生成记录，且 request_id 必须与恢复键一致；不调用 POST、不创建新意图、不增加次数、不提交图片。查不到时显示“仍未查到…”的真实状态，不伪造成功或自动重发。
6. 401 登录返回地址经过安全收敛。会话失效引起的登录跳转若当前路径带 project 查询，redirect 只保留 “/”，避免另一个账号登录后自动回到旧账号项目；未登录首次访问的普通深链接仍由路由守卫保留原地址（t55 回归覆盖），两类场景不混用。
7. 真实浏览器补证使用 Edge headless + CDP，不使用 jsdom 结论替代 Cookie、缓存或跨标签结论。真实浏览器确认 __Host-session 为 HttpOnly、Secure、SameSite=Strict，document.cookie 不含会话 Cookie，登录／项目／会话响应均带 no-store、Pragma no-cache、nosniff、no-referrer、X-Frame-Options DENY；历史返回导航类型为 back_forward，核验次数从 3 增至 8；跨标签信号 4 条均只有 nonce/type；切到 B 后看不到 A 项目；LLM／Visual 调用均为 0。
8. 本票只改前端。没有新增后端路由、模型调用、后台任务或数据库迁移；迁移头仍为 0010，0001–0010 未改。工作树构建产物由 Vite 生成，浏览器指向该构建产物，服务端使用真实 uvicorn、真实 HTTP、临时 SQLite 和完整 App，LLM／Visual 为 fake adapter。

本票只对 #72 负责的 V3-AC-14、15、27 阶段条件给出结论。U1–U3 的恢复与需求修改见 #73–#75，R3 门禁见 #77；未触碰 backend/data 或 main/backend/data，也未读取或写入任何真实用户数据。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t72 |
| 分支 | ticket/72 |
| 基线 | integration/v3 096261c |
| 代码提交 | 5a8eb02c8234e0db2cf7d247779e63104c43928d |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 后端 | Python 3.12.13（backend/.venv），本票无后端改动 |
| 数据库 | 临时 SQLite，alembic 0010 |
| 真实浏览器 | Microsoft Edge，C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe，headless + CDP |
| 模型适配 | LLMStub／VisualStub（fake adapter，不访问外部模型） |
| 数据边界 | 不读取或写入 backend/data、main/backend/data |

## 3. 实现摘要

### 3.1 跨标签失效信号

新增 main/src/auth/crossTab.ts：

- SessionInvalidationSignal 定义为 { type:'session-invalidated'; nonce:string }，只有这两个字段。
- CHANNEL_NAME = v3-session-lifecycle；SIGNAL_STORAGE_KEY = v3-session-lifecycle-signal；BroadcastChannel 不可用时用 localStorage 作为回退通道。
- broadcastSessionInvalidated 只在已安装监听器时发送；installCrossTabSessionListener 同时监听 BroadcastChannel 和 storage 事件，返回清理函数。
- 信号不含账号 id、邮箱、项目 id、request_id、Cookie、CSRF、密码或任何业务正文；测试断言消息键严格为 nonce/type。

### 3.2 登录生命周期与历史核验

修改 main/src/auth/session.ts：

- 新增 sessionRevalidating ref；installSessionLifecycle 注册 pageshow(persisted)、popstate 和跨标签监听，任一事件都执行 ensureSession(true)，核验期间 sessionRevalidating 为真，完成后复位。
- applySession 在 account.id + session_id 的 activeSessionKey 变化时递增 sessionRevision 并广播失效信号；dropSession 清理私有状态后广播；logout 结束本端会话并清理。
- ensureSession 复用进行中的检查 Promise，避免历史返回、跨标签和路由守卫同时发起多个 /api/auth/session 请求；失败时不缓存登录态。
- 新增 safeAuthRedirect：只接受单个前导斜杠，拒绝 // 和 /login；解析后若含 project 查询，返回 “/”，否则保留原路径。redirectToLogin 使用它；installAuthGuard 对未登录首次访问仍保留 to.fullPath，t55 的普通深链接行为不变。
- 401 handler 仍先记录是否存在活动会话，再 dropSession；只有已有会话才 redirectToLogin，避免引导阶段 401 与路由守卫互相竞争。

### 3.3 工作台隐藏与清理

修改 main/src/layouts/WorkspaceLayout.vue：

- businessVisible 在 sessionRevalidating 为真时强制为 false；核验期间显示“正在重新验证登录状态…”，不显示旧项目或旧按钮。

修改 main/src/composables/useWorkspace.ts：

- clearPrivateWorkspace 记录 hadProject = projectParam.value !== null 或 route.query.project 存在，然后清空 projectParam、stage、草稿、列表、项目详情、生成状态、对象 URL、导航和私有浏览器存储；若当前不是 describe 或存在旧 project 查询，router.replace({name:'describe'})，不把旧 project 留在地地址栏。
- 请求开始时捕获 sessionRevision；loadProject 检查 sessionAtStart、detailToken 和 AbortSignal，迟到响应不写入；clearProjectDetail 中止控制器并递增 token。sessionRevision 监听使用 flush:"sync"。
- 恢复键改为 ecommerce-generation-pending:<accountId>:<projectId>；normalizeGenerationCredential 校验 accountId，已登录时不接受缺少或不同的 accountId；读取已登录路径只读账号键，绝不自动读取旧的无账号 project 键或基键；保存后移除旧的无账号键。
- findKnownGeneration 先在已加载 generations 中按 request_id 查找；否则 GET /api/generations/{id}，只有 request_id 与恢复键一致才接受；恢复流程只设置 pending/retry 状态并显示“已恢复上次生成提交记录。”或“仍未查到…”，不发起生成 POST。
- 无 currentAccount 的旧测试路径继续使用 ecommerce-generation-pending:<projectId> 且不写 accountId，保持既有 t09 行为；登录后只用账号绑定键。

### 3.4 前端测试覆盖

新增 tests/t72-cross-tab-history.spec.ts（8 个测试）：

1. 跨标签只传播 nonce/type，收到信号后清理旧项目并切到 B；旧项目标题消失，currentAccount 变为 B，所有消息键只有 nonce/type，不含邮箱、project id、CSRF 或 session id；无生成 POST、无 workflow action POST。
2. pageshow(persisted) 先显示“正在重新验证登录状态…”，核验后显示空工作台，不显示旧项目，账号为 B，无生成 POST。
3. 旧的账号无关键 ecommerce-generation-pending:PROJECT_A 不被认领；带 ACCOUNT_A.id 的键显示“已恢复上次未确认的生成提交。”，只发一个项目 GET，无生成 POST。
4. 有账号时保存恢复键为 ecommerce-generation-pending:ACCOUNT_A.id:PROJECT_A；保存内容含 accountId，不含卡片正文或提示词；网络错误时仍无生成 POST。
5. 延迟 400ms 的项目详情在切到 B 后不填旧项目；等待后页面仍不显示旧账号项目，生成 POST 为 0。
6. 重新登录只 GET 原 generation（直接返回 GenerationView），request_id 匹配时显示“已恢复上次生成提交记录。”；只发一个 GET，无 POST；账号键被清除。
7. 存储读失败时不显示恢复提示，不提交，页面仍可加载项目。
8. 存储写失败时显示“无法写入会话存储；本次提交仍保留在当前页面，刷新或关闭页面后将无法恢复。”；存储为空，生成 POST 仍只有一次用户主动提交。

## 4. 执行命令与结果

### 4.1 前端定向测试

工作目录：D:/Desktop/新建文件夹/.worktrees/t72。

~~~
npx vitest run tests/t55-auth.spec.ts tests/t72-cross-tab-history.spec.ts --reporter=dot
~~~

结果：2 个测试文件、10 个测试通过。t72 专题单独为 8 个测试通过；t55 的未登录深链接保留原地址继续通过，证明安全重定向没有破坏首次访问。

### 4.2 前端全量回归与构建

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：40 个文件、227 个测试通过，23.54s；日志 .scratch/t72-frontend-final/test.log。
- vue-tsc --noEmit：exit 0；日志 .scratch/t72-frontend-final/typecheck.log。
- Vite 8.3.5：1664 modules，780ms；dist/index.html 0.47 kB，dist/assets/index-BStgIkyN.css 412.64 kB，dist/assets/index-CpzQuPAw.js 1,191.13 kB；只有 chunk-size 和既有 fireEvent.change 警告；日志 .scratch/t72-frontend-final/build.log。

### 4.3 后端全量回归

本票没有后端源码、路由或迁移改动，但为满足继承回归，仍执行了完整后端测试：

| 范围 | 结果 | 日志 |
| --- | --- | --- |
| 非 v2 全部测试（37 个 tests/test_*.py，排除 test_v2_*） | 351 passed, 2 warnings, 400.07s | .scratch/t72-full-nonv2.log |
| 继承 v2 全部测试（19 个 test_v2_*.py） | 235 passed, 323.53s | .scratch/t72-full-v2.log |
| 合计 | 586 passed，无失败、跳过或 unknown |  |

迁移边界：当前 alembic head=0010，26 张表，PRAGMA integrity_check=ok；本票未新增或修改迁移。临时数据库位于浏览器验收目录 .scratch/t72-browser/data/app.db。

### 4.4 真实浏览器、真实 HTTP、临时 SQLite、完整 App

先构建工作树前端，再执行：

~~~
node D:/Desktop/新建文件夹/.scratch/t72-browser/run_browser.mjs
~~~

结果：{"ok":true,"checks":17,"base_url":"http://127.0.0.1:2113","nav_type":"back_forward","signals":4}，完整结果 .scratch/t72-browser/browser-result.json，服务日志 .scratch/t72-browser/server.log。

17 项检查覆盖：

1. 真实表单登录成功，页面进入工作台。
2. __Host-session 在真实浏览器中设置，HttpOnly=true、Secure=true、SameSite=Strict、Path=/。
3. document.cookie 不包含 __Host-session；结果文件只记录布尔值，不保存 Cookie 值。
4. 登录响应带 no-store、Pragma no-cache、nosniff、no-referrer、X-Frame-Options DENY。
5. 项目响应带 no-store、Pragma no-cache、nosniff、no-referrer、X-Frame-Options DENY。
6. 会话响应带 no-store、Pragma no-cache、nosniff、no-referrer、X-Frame-Options DENY。
7. 浏览器前进／后退返回的导航类型为 back_forward；核验 /api/auth/session 次数从 3 增至 8。
8. 跨标签 logout 后第一标签不再显示旧项目。
9. 跨标签 logout 后 login 地址不再携带旧 project 查询（safeAuthRedirect 生效）。
10. 跨标签信号只含 type/nonce 两类键，共 4 条。
11. 第一标签重新核验后切换到 B，不显示 A 项目。
12. 切到 B 后无法看到 A 的项目标题或项目数据。
13. 全程 LLM 调用为 0、Visual submit 为 0、Visual poll 为 0。
14. 页面无模型任务或生成周期被意外创建。
15. 真实浏览器与临时 SQLite 的登录／项目读路径成功。
16. Cookie 值在结果文件中被明确替换为 <redacted>，证据不含会话秘密。
17. 浏览器验收脚本结束仍返回 ok=true，所有检查通过。

浏览器自动化使用 C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe，headless + remote-debugging-port + 临时用户目录；后端为真实 uvicorn、临时 SQLite、完整 App，LLM／Visual 为 fake adapter，不访问外部模型或真实数据。

## 5. 验收覆盖表

| AC | 本票阶段条件 | 证据 |
| --- | --- | --- |
| V3-AC-14 | 切号、跨标签、返回、迟到回调及旧恢复元数据均不串数据或重交 | t72 测试 1/2/5 覆盖跨标签、BFCache、迟到详情；测试 3/4/6 覆盖旧键不认领、账号绑定键和重新登录只查原 generation；浏览器检查 8/9/10/11/12 覆盖真实跨标签与登录返回；所有路径无生成 POST 或 workflow action POST |
| V3-AC-15 | 真实出口清单全覆盖，匿名／他人／不存在对象不泄漏 | 本票未新增 API；后端 586 个继承测试包含 R1 的 33/37 操作清单、跨账号 404、未发布字节 409 等门禁；t72 测试 5/6 与浏览器检查 11/12 证明 B 不显示 A 的项目；恢复只读取 GET /api/generations/{id}，不绕过 ownership；无新出口 |
| V3-AC-27 | 导航／刷新／历史／重新登录不额外调用模型，原任务合法执行不误判 | t72 测试 1/2/4/6 的生成 POST 计数为 0，恢复只 GET 原 generation；浏览器 stats 中 llm_roles、llm_started 为空，visual_submit=0、visual_poll=0；历史返回只触发 /api/auth/session 核验，不触发模型或生成任务 |

## 6. 证据文件

- 代码提交：5a8eb02c8234e0db2cf7d247779e63104c43928d。
- 前端定向与全量：.scratch/t72-frontend-final/{test.log,typecheck.log,build.log}；t72 专题文件 tests/t72-cross-tab-history.spec.ts。
- 真实浏览器：.scratch/t72-browser/run_browser.mjs、seed72.py、serve.py、setup.py、browser-result.json、server.log、stats.json、data/app.db。
- 后端回归：.scratch/t72-full-nonv2.log、.scratch/t72-full-v2.log。
- 迁移边界：.scratch/t72-browser/data/app.db 中 alembic 0010、26 张表、integrity_check=ok。
- 构建产物：D:/Desktop/新建文件夹/.worktrees/t72/dist（由 Vite 生成，不在 Git 提交中）。

## 7. 残余风险

1. 真实浏览器补证只使用本机 Edge headless；其他浏览器、移动端和辅助技术的 Cookie／BFCache 行为未在本票逐一枚举。V3-AC-14/15/27 的阶段结论以 Edge 真实行为加全量组件测试为证据。
2. BFCache 只在 pageshow(persisted)=true 时触发核验；普通前进／后退由 popstate 覆盖。浏览器若不触发这两个事件，仍由路由守卫和 401 handler 提供最终保护，但不会在显示旧页面前主动隐藏。
3. 存储读写失败使用测试替身模拟 SecurityError／QuotaExceededError；真实浏览器隐私模式和存储配额耗尽的表现可能因浏览器版本不同。
4. 本票没有改变 R2 的意图、计数、槽位和恢复语义；原任务的未知／恢复／结案仍由 #68/#69 及后续 #73–#75 处理。本票不声称远端任务被取消，也不自动重发。
5. 浏览器结果 JSON 已移除 Cookie 值，只保留属性与 <redacted>；项目 id 和账号邮箱用于验收复现，属于临时测试数据，不来自真实用户。未读取或写入 backend/data、main/backend/data。
