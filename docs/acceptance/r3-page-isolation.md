# R3 切号与同账号重登录的页面隔离验收记录

- Issue: #71
- 父 Issue: #52
- 分支: ticket/71
- 代码提交: bb9aab322a0fe5c007751bf1d0cf2c357ebe8a09
- 验收日期: 2026-11-01
- 状态: 通过（R3 阶段条件；跨标签历史／恢复、U1–U3 与 R3 门禁见 #72–#77）

## 1. 结论与边界

本票建立浏览器侧登录生命周期与账号上下文，保证切号、同账号重新登录、退出和会话失效后不残留旧业务页面或旧恢复元数据。

1. 后端登录与会话响应增加 session_id。它是服务端会话记录的稳定标识，不是会话令牌，也不可代替 Cookie；GET /api/auth/sessions 原本就向本人暴露该 id。前端用 account.id + session_id 组成 activeSessionKey，只有账号或登录生命周期变化时才递增 sessionRevision。同一账号重新登录即使 account.id 相同，新 session_id 也会触发一次新的生命周期。
2. 退出、401 会话失效、切换账号和同账号新会话都会通过 sessionRevision 同步通知工作台。clearPrivateWorkspace 会中止列表／项目详情／生成轮询请求，递增 detailToken、projectContextToken、generationToken，并清空当前项目、消息、生成结果、需求草稿、参考图状态、未发送文本、项目列表、导航确认、抽屉和选中结果。清理同步执行，旧请求即使已经越过 AbortSignal，也会因捕获的 sessionRevision 不一致而丢弃回调。
3. 浏览器私有恢复信息由 clearPrivateBrowserState 清除：localStorage 的 v3-generation-request 及前缀键、sessionStorage 的 ecommerce-generation-pending 及前缀键。它们只是稳定请求标识和未确认提交凭据，不含密码、会话令牌或 CSRF 秘密；HttpOnly 会话 Cookie 由服务端撤销／过期处理，前端不可读也不写入。
4. 未核验身份前不显示旧业务。WorkspaceLayout 在浏览器已有会话后又失去会话时隐藏工作台，显示“正在重新验证登录状态…”；若从未建立会话（例如不装路由守卫的测试挂载），保留空工作台壳用于首屏，但不会出现旧账号数据。生产入口由路由守卫先执行 ensureSession。
5. 迟到回调不是只靠 abort 证明隔离。useWorkspace 的列表、详情、消息和生成流程在请求开始时记录 sessionRevision，在成功和错误分支都检查 revision、请求序号和项目 id；useWorkflow 的 load 和 runAction 同样检查 sessionRevision、loadSequence／actionSequence 和 projectId。项目详情测试和流程视图测试分别延迟响应，在 clearSession 后确认不会回填。
6. 前端稳定请求标识按登录生命周期隔离：WorkflowPanel 的确认键在存在 session_id 时追加 session + session_id，旧键格式仍可读取以兼容历史数据；会话变化时 privateData 清理旧键。刷新同一会话仍复用同一个 request_id，不因刷新生成第二个意图。
7. 401／403／供应商故障分流：requestJson 仅在 401 时调用 unauthorizedHandler；403 PERMISSION_DENIED 和 503 SERVICE_UNAVAILABLE 只作为普通请求错误显示，不清除 currentAccount、不清除私有存储、不把用户送回登录页。测试中 403／503 后账号和会话保持，旧确认键保留；随后 401 才清除账号、隐藏业务并删除键。
8. 本票没有新增生成入口、模型调用或后台任务。导航、刷新、历史返回、重新登录只读取已有项目和流程投影，不调用 LLM／Visual，不改变原 GenerationIntent 的 owner、usage 或槽位；退出也不会把未知远端任务伪称为已取消或自动续发。服务端的原任务归属和恢复语义继续由 R1／R2 的 verify、recover、closure 路径负责。
9. 后端只改登录／会话响应字段，不改数据库、迁移或认证 Cookie 格式。迁移头仍为 0010，0001–0010 未变。浏览器侧验证使用真实 uvicorn、真实 HTTP、临时 SQLite、完整 App 和工作树构建产物，LLM／Visual 为 fake adapter。

本票只对 #71 负责的 V3-AC-10、11、12、13、14、19、27、40 阶段条件给出结论。跨标签历史／恢复和旧恢复元数据的更广场景见 #72；U1–U3 见 #73–#75；R3 门禁见 #77。未触碰 backend/data 或 main/backend/data。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t71 |
| 分支 | ticket/71 |
| 基线 | integration/v3 d638524 |
| 代码提交 | bb9aab322a0fe5c007751bf1d0cf2c357ebe8a09 |
| 后端 Python | 3.12.13（backend/.venv） |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 数据库 | 临时 SQLite，alembic 0010 |
| 模型适配 | 测试 LLMStub／VisualStub；smoke 为 fake adapter |
| 浏览器自动化 | Vitest + jsdom（组件／工作台集成测试） |
| 数据边界 | 未读取或写入 backend/data 与 main/backend/data |

## 3. 实现摘要

### 3.1 后端登录生命周期标识

修改 main/backend/app/auth/service.py 和 main/backend/app/auth/router.py：

- LoginResult 增加 session_id 字段，登录成功时写入新 AuthSession 记录的 id。
- LoginResponse、SessionResponse 增加 session_id，登录和 GET /api/auth/session 返回同一个值。
- session_id 是数据库会话记录标识，不是 Cookie 值、原始 token、CSRF token 或密码哈希；不写日志、不写可读存储。
- 没有新增认证路由，没有改变 __Host-session／__Host-csrf Cookie 属性、会话过期策略或 CSRF 校验。

### 3.2 前端会话上下文

修改 main/src/auth/session.ts：

- 新增 currentSessionId、sessionEstablished、sessionInvalidated、sessionRevision。
- activeSessionKey = account.id + ":" + (session_id ?? "session")；只有 key 变化才增加 sessionRevision。
- login／ensureSession 成功时设置账号、session_id、CSRF 和 established，清除 invalidated。
- dropSession 清空账号和 session_id、清除 CSRF，若此前有会话则标记 invalidated 并递增 revision，然后清除浏览器私有数据。
- 401 unauthorized handler 先记录是否已有活动会话，再 dropSession；只有已有会话才重定向登录。引导阶段的 401 只清状态，由路由守卫完成本轮导航，避免登录页反复 401 的导航环。
- 新增 main/src/auth/privateData.ts：clearPrivateBrowserState 仅清理本应用的稳定请求标识和旧生成凭据键，storage 读写异常不会阻塞退出。

### 3.3 工作台清理与迟到回调

修改 main/src/composables/useWorkspace.ts：

- 增加 clearPrivateWorkspace：重置导航、抽屉、创建未知态、发送状态、需求动作，中止列表／加载更多控制器，调用 clearProjectDetail，清空项目列表、游标、草稿、projectParam、stage，并调用 clearPrivateBrowserState。
- 以 flush:"sync" 监听 sessionRevision，保证在旧请求的微任务回调恢复前先递增所有 token。
- loadProject、loadProjects、appendPage 在请求开始时捕获 sessionRevision，在成功和异常分支检查 revision；详情仍检查 detailToken、projectContextToken、AbortSignal 和项目 id。
- clearProjectDetail 继续清理参考图 object URL、生成状态、选中结果和恢复凭据，不把旧图片或旧结果引用留给新账号。

修改 main/src/composables/useWorkflow.ts：

- load 和 runAction 捕获 sessionRevision；响应只在 revision、sequence、projectId 都一致时写入 view，否则丢弃。
- 409 冲突后的重新 load 也遵守同一 revision 检查，不把旧生命周期错误显示到新会话。

### 3.4 页面隔离和稳定请求标识

- main/src/layouts/WorkspaceLayout.vue：businessVisible = currentAccount !== null || (!sessionEstablished && !sessionInvalidated)；已有会话失效时隐藏业务，显示“正在重新验证登录状态…”。
- main/src/components/workflow/WorkflowPanel.vue：稳定 request_id 键在 session_id 存在时追加 session 和 session_id；无 session 的旧测试／历史键仍兼容。意图 delivered／failed／abandoned 时删除当前键。
- main/src/api/types.ts：AuthSessionResponse 增加可选 session_id；后端实际始终返回，旧 mock 用 expires_at 回退。

### 3.5 失败分流

- main/src/api/client.ts 的 401 handler 只由会话失效触发；403、503 和网络错误不调用 unauthorizedHandler，也不清理 currentAccount。
- 401 进入 clearSession：隐藏业务、清理私有存储、返回登录；403／503 保持当前页面和账号，显示操作错误，不自动重试模型或图片服务。

## 4. 执行命令与结果

### 4.1 定向测试

后端（cwd 工作树 backend，PYTHONPATH 工作树 main/backend）：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t71-be2 tests/test_t71_session_binding.py tests/test_t55_auth_foundation.py tests/test_t57_sessions.py
~~~

结果：44 passed, 2 warnings in 34.07s。其中 t71 的 1 个测试验证：登录响应 session_id 非空；GET /api/auth/session 返回同一 id；GET /api/auth/sessions 中当前项 id 一致；同一账号第二次登录得到不同 session_id、account.id 相同；旧客户端仍返回自己的旧 session_id。

前端定向（cwd 工作树根）：

~~~
npm test -- --reporter=dot tests/t71-session-isolation.spec.ts
npm run typecheck
~~~

结果：t71 专题 4 passed in 3.72s（日志 .scratch/t71-t71spec2.log）；vue-tsc exit 0（日志 .scratch/t71-typecheck3.log）。4 个测试分别覆盖：A→B→A 与同账号新会话清空项目／草稿／恢复键；迟到项目详情回调不回填；401 清除而 403／503 不清除；迟到 workflow 视图在会话变化后不写入。

### 4.2 前端全量回归与构建

命令（cwd 工作树根）：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：39 个文件、219 个测试通过，22.84s；日志 .scratch/t71-frontend-test-final.log。仅有既有 fireEvent.change 警告。
- vue-tsc --noEmit：exit 0；日志 .scratch/t71-typecheck3.log。
- Vite 8.3.5：1663 modules，1.36s；dist/index.html 0.47 kB，dist/assets/index-BStgIkyN.css 412.64 kB，dist/assets/index-DSOsZOI9.js 1,188.76 kB；仅有 chunk-size 警告；日志 .scratch/t71-frontend-build-final.log。

### 4.3 全量后端回归

| 范围 | 命令要点 | 结果 |
| --- | --- | --- |
| 非 v2 全部测试 | 37 个 tests/test_*.py，排除 test_v2_*；cwd 工作树 backend，PYTHONPATH 工作树 main/backend | 351 passed, 2 warnings, 429.78s |
| 继承 v2 全部测试 | 19 个 tests/test_v2_*.py | 235 passed, 353.96s |
| 合计 |  | 586 passed，无失败、跳过或 unknown |

非 v2 日志：.scratch/t71-full-nonv2.log；v2 日志：.scratch/t71-full-v2.log。ruff 对 auth/router.py、auth/service.py、test_t71_session_binding.py：All checks passed（.scratch/t71-ruff-final.log）。git diff --check 无空白错误。

### 4.4 迁移边界

本票无迁移变更。.scratch/t71-migration 记录：

- fresh：0001→0010 全部成功，alembic_version=0010，26 张表，integrity_check=ok，generation_intents status CHECK 包含 closed；日志 fresh.log、verify-fresh.log。
- chain：从 0009 升级到 0010 前插入 project p-0011（title=Preserved project，revision=4），升级后 0010 成功，p-0011 记录仍为 (p-0011, Preserved project, 4)，26 张表，integrity_check=ok；日志 chain-0009.log、insert.log、chain-0010.log、verify.log。
- 两个 0010_r2_local_tracking_closure.py 副本字节一致：1120 bytes，SHA256 5040de84a12d6f25622aca655f9034e1962e9c77d24faead307ad0c518491b5f。

### 4.5 真实 HTTP、临时 SQLite、完整 App smoke

命令：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t71-smoke/smoke71.py
~~~

结果：ok=true，checks=48，base_url=http://127.0.0.1:5813，project_id=52738704-66b7-48f4-9ff3-4865dccbba89。结果文件 .scratch/t71-smoke/result.json，服务日志 .scratch/t71-smoke/server.log。

smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite、完整 App 和工作树构建前端，LLM／Visual 为 fake adapter。覆盖：

- 账号 A、B 两次登录得到不同 session_id；GET /api/auth/session 在每个客户端稳定返回自己的 id。
- A 创建项目；B 和管理员读取该项目均 404 PROJECT_NOT_FOUND；A2（同账号新会话）仍可读自己的项目。
- A1 退出 204 后旧客户端 401；A2 不受影响；B 看不到 A 的项目。
- A3（A 再次登录）得到不同于 A2 的新 session_id，仍可读 A 的项目；A2 仍有效，证明退出只结束本端会话。
- 构建后的 / 和 /login 均返回 id=app 的 HTML；llm_started 为空，visual_submit=0，visual_poll=0；数据库至少两个活动会话。
- smoke 不读取或写入 backend/data、main/backend/data，只使用临时目录。

## 5. 验收覆盖表

| AC | 本票阶段条件 | 证据 |
| --- | --- | --- |
| V3-AC-10 | 正式 Cookie 与登录换发正确，前端及可读存储无登录秘密 | 登录／会话响应 session_id 是记录 id 而非 token；前端只存稳定请求键与旧生成凭据，不存 Cookie／密码／CSRF；t55 回归继续检查 localStorage／sessionStorage 无登录秘密；session.ts 无存储写入 |
| V3-AC-11 | 空闲／绝对临界与轮询不续期，过期不复活 | 401 会话失效后 dropSession 清空账号与业务，sessionInvalidated 阻止旧业务回显；不自动重新登录或续期；R1 的会话临界与轮询回归继续通过 |
| V3-AC-12 | 多端指定撤销、退出本端、改密／重置／禁用及他账号隔离 | smoke A1 退出后 401、A2 仍有效、B 隔离；A→B→A 与同账号新会话测试清空旧业务；R1 的撤销／改密／禁用回归通过 |
| V3-AC-13 | 所有变更动作的 CSRF、来源和返回地址，无后继新动作旁路 | 本票没有新增变更路由；所有既有变更仍走 R1 CSRF／Origin 校验；登录守卫不清除 CSRF 旁路；前端 401 后才清除 CSRF，403／503 保留 |
| V3-AC-14 | 切号、跨标签、返回、迟到回调及旧恢复元数据不串数据或重交 | 4 个前端测试：A→B→A、同账号新会话均清空项目／草稿／恢复键；迟到项目详情与迟到 workflow 回调丢弃；localStorage／sessionStorage 旧键删除；无 /generations 或 workflow action POST |
| V3-AC-19 | 退出／过期与禁用不同，原任务归属不变，未知不伪称取消或自动续发 | 前端退出只清客户端上下文，不修改服务端 intent／usage／槽位；smoke 无模型调用；R2 verify／recover／closure 回归继续通过；未知状态不被前端解释为取消 |
| V3-AC-27 | 导航／刷新／历史／重新登录不额外调用模型，原任务合法执行不误判 | smoke llm_started=[]、visual_submit=0、visual_poll=0；前端刷新只 GET session／project／workflow；同一会话稳定 request_id，切换会话才清理键；不自动重提 |
| V3-AC-40 | 鉴权／库／审计／归属故障按职责安全失败，另一操作者可复现维护步骤 | 401 清会话，403 PERMISSION_DENIED 与 503 SERVICE_UNAVAILABLE 保留会话并显示错误；后端 586 项回归含认证、归属、审计故障路径；smoke 使用临时 SQLite，可重复启动 |

## 6. 证据文件

- 代码提交：bb9aab322a0fe5c007751bf1d0cf2c357ebe8a09。
- 后端定向：.scratch/t71-be2（44 passed，含 t71 1 个）。
- 前端定向：.scratch/t71-t71spec2.log（4 passed, 3.72s）；类型检查：.scratch/t71-typecheck3.log。
- 前端全量：.scratch/t71-frontend-test-final.log（39 files/219 tests, 22.84s）；构建：.scratch/t71-frontend-build-final.log。
- 后端全量：.scratch/t71-full-nonv2.log（351 passed, 429.78s）、.scratch/t71-full-v2.log（235 passed, 353.96s）。
- 迁移：.scratch/t71-migration/fresh-data、fresh.log、verify-fresh.log；chain-data、chain-0009.log、insert.log、chain-0010.log、verify.log。
- smoke：.scratch/t71-smoke/result.json、server.log、run.log；构建前端副本 .scratch/t71-smoke/web。
- 测试文件：backend/tests/test_t71_session_binding.py（1 test）、tests/t71-session-isolation.spec.ts（4 tests）。

## 7. 残余风险

- 本票的“跨标签”证据集中在同一浏览器的多个会话／请求序列和 jsdom 集成测试；真正操作系统的多标签同时打开、BFCache 恢复和浏览器历史缓存强度仍应在 #72／#77 用真实浏览器补证。当前隔离依赖 sessionRevision、请求序号与显式清理，不把 abort 当唯一证明。
- session_id 是服务端会话记录标识，不是认证凭据；若未来把它用于其他用途，必须保持其不可作为 Cookie 替代品，并继续只通过 HttpOnly Cookie 认证。
- clearPrivateBrowserState 仅清理本应用已知键。第三方扩展、浏览器自身缓存或用户手工复制的内容不在应用控制范围；生产部署应继续使用 HTTPS、严格 Host／Origin 允许清单和私有响应头。
- 本票未改变服务端会话过期、撤销和审计实现；这些由 R1 回归覆盖。真实供应商故障与网络错误只验证不会清除登录态，不验证供应商重试策略。
- 本票使用 fake LLM／Visual adapter，不能证明真实模型调用的费用、远端幂等或结果保留；但本票路径没有新增模型调用。
