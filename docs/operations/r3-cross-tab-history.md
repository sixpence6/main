# R3 跨标签、历史返回与账号绑定恢复运维说明

- Issue: #72
- 父 Issue: #52
- 分支: ticket/72
- 代码提交: 5a8eb02c8234e0db2cf7d247779e63104c43928d
- 验收日期: 2026-11-01
- 状态: 通过

## 1. 运行时行为

1. 跨标签失效只通过 BroadcastChannel 的 v3-session-lifecycle 通道传播，消息只有 type=session-invalidated 和随机 nonce。BroadcastChannel 不可用时使用 localStorage 的 v3-session-lifecycle-signal 键；两者都不携带账号、项目、请求标识、Cookie、CSRF、密码或业务正文。
2. 收到跨标签信号、浏览器 pageshow(persisted)、popstate 或路由守卫需要重新核验时，会重新请求 GET /api/auth/session。核验期间页面显示“正在重新验证登录状态…”，旧工作台隐藏。
3. 登录生命周期键为 account.id + session_id。退出、401、切号或同一账号的新登录生命周期都会递增 sessionRevision；工作台清理项目、消息、生成结果、草稿、参考图、列表和浏览器私有存储，并中止未完成请求。
4. 恢复键为 ecommerce-generation-pending:<accountId>:<projectId>，只保存 projectId、requestId、confirmedRevision、acknowledgeUnknownPresent、acknowledgeUnknownValue、generationId。已登录时不会读取旧的 ecommerce-generation-pending:<projectId> 或基键 ecommerce-generation-pending；重新登录只通过 GET /api/generations/{id} 查询原记录，不提交生成。
5. 会话失效重定向到登录页时，带 project 查询的地址会收敛为 “/”；未登录首次访问的普通深链接仍保留 redirect，便于正常登录后继续。该行为由 main/src/auth/session.ts 的 safeAuthRedirect 和 redirectToLogin 实现。
6. 本票不新增后端路由、数据库表、迁移或模型调用。部署后 alembic head 仍应为 0010；不需要运行迁移。

## 2. 日常验证

### 2.1 前端测试

在工作树根执行：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

期望结果：

- Vitest 全量 40 个文件、227 个测试通过；日志写入 .scratch/t72-frontend-final/test.log。
- vue-tsc --noEmit exit 0；日志 .scratch/t72-frontend-final/typecheck.log。
- Vite 构建成功，dist/index.html 与 dist/assets 生成；日志 .scratch/t72-frontend-final/build.log。构建只应有 chunk-size 和既有 fireEvent.change 警告。

只验证本票专题：

~~~
npx vitest run tests/t55-auth.spec.ts tests/t72-cross-tab-history.spec.ts --reporter=dot
~~~

期望 2 个文件、10 个测试通过；t72 单独为 8 个测试。t55 的“未登录访问工作台时跳转登录页并保留原地址”必须继续通过，不能被 safeAuthRedirect 改坏。

### 2.2 真实浏览器补证

构建完成后执行（工作树根或任意目录均可，脚本内部使用绝对路径）：

~~~
node D:/Desktop/新建文件夹/.scratch/t72-browser/run_browser.mjs
~~~

期望：

~~~
{"ok":true,"checks":17,"base_url":"http://127.0.0.1:<临时端口>","nav_type":"back_forward","signals":4}
~~~

完整结果在 D:/Desktop/新建文件夹/.scratch/t72-browser/browser-result.json。必须确认：

- cookie.value 为 <redacted>，证据文件不保存 __Host-session 原始值。
- cookie 属性为 HttpOnly、Secure、SameSite=Strict、Path=/。
- login、project、session 三组响应包含 cache-control: no-store、pragma: no-cache、x-content-type-options: nosniff、referrer-policy: no-referrer、x-frame-options: DENY。
- nav_type=back_forward，session_checks_before_back 小于 session_checks_after_back。
- signals 共 4 条且每条只有 nonce/type。
- stats.llm_roles 为空，stats.llm_started 为空，visual_submit=0，visual_poll=0。

脚本使用真实 uvicorn、真实 HTTP、临时 SQLite、完整 App、工作树 dist，以及 fake LLM／Visual adapter。不要把它指向 backend/data、main/backend/data 或任何真实用户数据；脚本会在临时目录创建 data/app.db 和 Edge profile。

## 3. 运行条件与故障排查

### 3.1 浏览器

- 使用 C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe，headless + --remote-debugging-port + 临时 --user-data-dir。
- 端口必须使用空闲端口；脚本会自行选择并启动 uvicorn。若端口被占用，先终止旧 smoke／uvicorn 进程或改用新运行目录。
- Edge 对 127.0.0.1 上的 HttpOnly + Secure Cookie 正常接受；用 httpx 做种子登录时，httpx 不会自动回发 Secure Cookie，seed 脚本需要手动读取 Set-Cookie 并把 Cookie 头放回后续请求。这只影响测试种子，不影响浏览器行为。
- 如果浏览器一直停在登录页，检查 APP_PUBLIC_URL、PORT、allowed_hosts 和临时数据库是否已迁移；错误密码或未初始化管理员应先用 setup.py 初始化临时管理员。

### 3.2 构建与静态资源

浏览器脚本服务的是 D:/Desktop/新建文件夹/.worktrees/t72/dist，不是 main/dist。修改前端后必须先 npm run build，否则浏览器会加载旧构建，跨标签或重定向修复不会体现在补证中。

### 3.3 登录返回地址

- 401 会话失效且当前地址含 project 查询时，登录地址应是 /login?redirect=/，不应出现旧 project id。
- 未登录首次访问 /generate?project=... 仍应保留 redirect=/generate?project=...；这是路由守卫的深链接行为，不应被 safeAuthRedirect 的会话失效路径误改。
- 若发现旧 project 出现在登录页，检查 redirectToLogin 是否走 safeAuthRedirect，以及当前路由是否仍由 WorkspaceLayout 清理函数同步替换。

### 3.4 存储与恢复

- 退出或切号后，旧账号键 ecommerce-generation-pending:<oldAccountId>:<projectId> 不应被新账号读取；旧的无账号键也不应自动认领。
- 恢复流程只允许 GET /api/generations/{id} 并校验 request_id；若恢复键损坏、request_id 不匹配或 GET 404，显示“仍未查到…”而不是提交新生成。
- 存储读失败不阻止页面加载；写入失败时用户会看到“无法写入会话存储；本次提交仍保留在当前页面，刷新或关闭页面后将无法恢复。”不要把它当作提交失败。

## 4. 安全边界

1. 跨标签消息只能作为失效提示，不能用于传递身份或业务数据。不要向 v3-session-lifecycle 通道增加账号、项目、请求 id、Cookie、CSRF 或正文。
2. 恢复键不能写密码、会话令牌、CSRF、模型密钥、卡片正文、提示词或参考图字节。只允许本票定义的六个字段和 accountId。
3. 会话 Cookie 由浏览器 HttpOnly 管理；前端不得读取或写入 __Host-session。CSRF 仍由现有 API client 处理，不得因跨标签逻辑关闭。
4. 任何新的恢复入口都必须复用 R1 ownership 与 R2 意图／计数规则；本票只做前端上下文核验，不改变后端授权、额度、槽位或远端任务状态。
5. 不得把真实用户数据复制到 .scratch 或浏览器验收目录；验收只使用临时 SQLite 和临时账号。

## 5. 回滚

本票无迁移、无后端接口变更。若前端需要回滚，回退代码提交 5a8eb02c8234e0db2cf7d247779e63104c43928d 并重新构建即可；浏览器私有存储中的账号绑定键由前端清理函数处理，旧的无账号键设计仍可读取（未登录路径），不会阻塞回滚。回滚后应重新运行前端全量测试和至少一次真实浏览器补证。
