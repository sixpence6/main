# Issue #127 T15 验收记录：完整新对话体验的账号生命周期隔离复验

## 1. 范围与结论

- 工单：#127（V3 纯对话 T15，父票 #112）。
- 目标：新界面的列表、文字、提示词、附件、图片、恢复引用和滚动定位在切号、重登、跨标签失效及晚到响应下不串上下文；确认既有身份、会话和私有状态清理逻辑在完整组合下仍成立，并修复复验中发现的晚到响应缺口。
- 验收基线：集成分支 `codex/issue-112-integration` 的 `449ed22c86b31e585718bf3182f4a2e48c47668e`（T14 合并与验收文档提交后）。本票从该提交新建 `codex/issue-112-t127` worktree。
- 本票变更：`main/src/components/AppSidebar.vue`、`main/src/composables/useChatFlow.ts`、`main/src/composables/useWorkflow.ts`，以及新增 `tests/t127-lifecycle-isolation.spec.ts`。未修改后端生产代码、数据库迁移、SRS 参数或次数/槽位口径。
- 结论：A→B→A、同账号新登录、显式退出、跨标签失效和浏览器返回均先完成身份核验再恢复业务；列表、搜索、草稿、待发送附件、提示词/任务恢复引用、旧图片和旧错误不跨账号生命周期；403 与供应商 503 不退出登录，只有 401 清空账号和私有存储；跨标签消息只携带失效信号，不含账号、项目、请求体、Cookie 或 CSRF。真实浏览器视觉、手机软键盘和真实供应商证据留 #129/#128。

## 2. 实现面

| 面 | 实现 |
|---|---|
| 账号生命周期监听 | `AppSidebar.vue` 监听 `sessionRevision`：清理内联重命名状态与重命名错误；仅在 `currentAccount` 存在时重新读取当前账号的会话列表，避免退出后匿名请求 |
| 列表与搜索隔离 | 既有 `useWorkspace.loadProjects` 捕获起始 `sessionRevision` 和查询词，晚到响应在账号或查询变化后丢弃；`clearPrivateWorkspace` 在会话变化时清空列表、游标、查询、搜索/重命名错误和项目详情 |
| 草稿与恢复引用 | `clearPrivateWorkspace` 清空每项目草稿；`useChatFlow` 的 `sessionRevision` 监听清理待发送附件、待确认需求 request_id、待确认提示词 command_id、待执行任务动作、任务确认勾选、页面错误、自动推进状态和复制状态，并清除复制定时器 |
| 晚到建项目 | `begin` / `beginWithAttachment` 在创建项目响应返回后核对 `sessionRevision`；若期间已切号，不调用 `adoptCreatedProject`，不写入旧项目、不刷新详情、不显示旧错误 |
| 晚到发送 | `send` / `beginWithAttachment` 在暂存和发送响应前后核对 `sessionRevision`；切号后不应用旧消息、不触发详情/流程刷新、不自动推进 |
| 晚到动作 | `confirmRequirements` / `confirmPrompt` 在动作响应后核对 `sessionRevision`；失败时保留安全错误，成功才清理 request_id/command_id 并刷新；`useWorkflow.runAction` 在会话、动作序列或项目已变化时返回 `false`，不再把过期动作当作成功 |
| 流程轮询 | 既有 `useWorkflow.load` 在写入视图前核对 `sessionRevision`、加载序列和项目 id；晚到的流程/轮询响应不写旧视图，切号后项目参数清空并触发空视图 |
| 私有图片与 URL | 项目详情、消息、图片历史和流程视图在会话变化时清空；图片 URL 仍为受保护的 `GET /api/assets/{id}`，由既有 owner、保留期和 `can_access_asset` 门禁校验，不用本地缓存兜底 |
| 跨标签失效 | 既有 `crossTab` 只发送 `{type:'session-invalidated', nonce}`；页面收到后进入“正在重新验证登录状态…”，重新 `getSession`，再按新身份重建列表与业务视图，不复用旧页面数据 |
| 浏览器返回 / BFCache | 既有 `installSessionLifecycle` 在 `popstate`、`pageshow.persisted` 时调用 `revalidateForLifecycleChange`；核验期间隐藏旧业务，核验完成后按路由和账号重新读取 |
| 显式退出 | 既有 `logout` 在 `finally` 中清空账号、CSRF、会话标记和私有浏览器存储；侧栏跳转登录页，晚到业务响应由上述会话守卫丢弃 |
| 401 / 403 / 503 | 既有 API 客户端只在 401 调用未授权处理器并退出登录；403 权限拒绝和供应商 503 保持当前账号与会话，仅显示安全错误；本票测试固定这一区别 |
| 安全边界 | 无新增后端入口；列表、详情、流程仍走既有 owner 过滤和对象关联；跨标签信号不含凭据；旧请求由服务端身份/所有者/CSRF 门禁拒绝。后端既有隔离、所有权、后台任务和会话绑定测试作为补充证据 |

## 3. 测试面

新增 `tests/t127-lifecycle-isolation.spec.ts`，7 个测试函数/7 个用例。测试挂载真实 App、路由和 Element Plus，只替换 fetch、时钟、页面可见性和存储，不 mock 前端业务模块。

- T15-1 `A→B→A 与同账号新登录都清空列表、搜索、草稿和旧恢复引用`：切到 B、再回 A、再同账号新会话，均清空搜索、草稿和旧 request/pending 存储；列表只显示当前账号项目。
- T15-2 `切换账号后迟到的建项目响应不能把旧项目带回新身份`：创建项目响应延迟 180ms，期间切到 B；旧项目不出现、路径保持根入口、零工作流动作、零旧详情读取。
- T15-3 `跨标签失效信号只含随机信号，重新核验后隐藏旧图片并加载新账号列表`：旧账号项目与图片历史在失效后消失，新账号列表出现；失效消息只含 `nonce`/`type`，不包含邮箱、项目 id、csrf 或 session id。
- T15-4 `浏览器返回先核验身份，旧项目和私有图片不会在新页面残留`：`popstate` 触发重新核验，显示“正在重新验证登录状态…”，随后只显示欢迎态，旧项目和旧图不残留。
- T15-5 `显式退出清除账号、草稿、列表和旧恢复引用并回到登录页`：退出后账号为空、会话未建立、私有存储清空、旧项目不可见，唯一写请求为 logout。
- T15-6 `403 和供应商 503 保持会话，只有 401 才清空账号与私有存储`：403/503 保留账号和存储；401 才清空账号、会话和私有存储。
- T15-7 `切换账号后迟到的发送失败不会留下旧错误或自动重试`：发送返回 409 延迟 160ms，期间切到 B；旧错误提示不出现、不重试、不增加额外发送或工作流动作。

相关既有前端证据继续通过：

- `tests/t124-attachment.spec.ts` 的登录生命周期用例覆盖“切号清空待发送图片，晚到上传不写入新上下文”。
- `tests/t126-history.spec.ts` 覆盖多轮图片历史和当前账号下的图片隔离；本票 T15-3 进一步验证切号后图片历史消失。
- 全套 `tests/` 32 个 spec 文件、69 个用例全部通过，确认生命周期守卫没有破坏既有聊天、附件、任务状态和历史能力。

后端既有证据（本票未改后端生产代码，14 个用例全部通过）：

- `tests/test_t59_isolation.py`：owner 持久化与分页前过滤、子对象跨账号边界、伪造 owner/角色/stale principal、无 owner 旧项目不外发、匿名业务请求拒绝。
- `tests/test_t60_ownership_closure.py`：参考图上传记录操作者并阻止其他账号、外部 artifact id 拒绝且忽略自报角色、CSRF 和旧写入门禁仍生效。
- `tests/test_t62_background_ownership.py`：退出不改变任务 owner 或停止已授权工作、禁用停止下一次远端调用且不发布、任务创建持久化服务端授权而非客户端字段、发布前复核 owner、禁用 owner 不能发起模型调用。
- `tests/test_t71_session_binding.py`：登录暴露稳定的会话生命周期 id。

RED 证据：实现前运行最终 7 个用例（`D:/Desktop/issue-112-context/t127-red2.json`），3 个用例失败，分别暴露：切号后侧栏不重新加载新账号列表；迟到的建项目响应把旧项目带回新身份；迟到的发送 409 留下旧错误。其余 4 个生命周期场景当时已通过，作为既有清理逻辑的回归基线。补齐上述三个生产缺口后 7/7 通过。

## 4. 命令与结果

工作目录：`C:/Users/28425/.codex/worktrees/issue-112-t127/新建文件夹`。

```powershell
D:/nodejs/node.exe node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.json
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run tests/t127-lifecycle-isolation.spec.ts --reporter=json --outputFile=D:/Desktop/issue-112-context/t127-green2.json
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run --reporter=json --outputFile=D:/Desktop/issue-112-context/t127-full1.json
D:/nodejs/node.exe node_modules/vite/bin/vite.js build --config main/vite.config.ts
```

后端既有安全测试：

```powershell
cd C:/Users/28425/.codex/worktrees/issue-112-t127/新建文件夹/backend
C:/Users/28425/.codex/worktrees/issue-112-integration/新建文件夹/backend/.venv/Scripts/python.exe -m pytest tests/test_t59_isolation.py tests/test_t60_ownership_closure.py tests/test_t62_background_ownership.py tests/test_t71_session_binding.py -q -p no:cacheprovider --basetemp=D:/Desktop/issue-112-context/t127-backend1
```

结果：

- 类型检查：通过。
- T15 专项：`7 passed`（JSON `D:/Desktop/issue-112-context/t127-green2.json`）。
- 当前前端全套：32 个 spec 文件，`69 passed / 69`，全部通过；JSON `D:/Desktop/issue-112-context/t127-full1.json`。
- 生产构建：通过（1654 modules；仅有既有大 chunk 警告；`main/dist/` 被 gitignore）。
- 后端既有隔离/所有权/后台/会话测试：`14 passed in 27.90s`（basetemp `D:/Desktop/issue-112-context/t127-backend1`）。
- 静态检查：`git diff --check` 无输出；未在 `main/` 下新增测试、截图或临时产物。

## 5. #127 AC 逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| 1 A→B→A、同账号新登录、显式退出、跨标签失效、浏览器返回均验证；接入以来的身份/会话守卫在完整组合下成立 | 通过 | T15-1（A→B→A、同账号新会话）、T15-3（跨标签）、T15-4（浏览器返回）、T15-5（显式退出）；T15-2/7 覆盖晚到建项目和发送 |
| 2 成功/失败/finally/轮询/搜索/上传/私有图片 URL/定位回调都匹配完整上下文；仅中止请求不能替代安全匹配 | 通过 | T15-1 搜索与列表；T15-2 建项目晚到；T15-3 私有图片和历史；T15-7 失败回调；T124 既有上传晚到用例；`useWorkflow.load` 与 `runAction` 的 session/sequence/project 三重守卫覆盖轮询与动作回调；401/403/503 见 T15-6 |
| 3 清空草稿、列表、查询缓存、选择、临时 URL、旧恢复引用；跨标签消息仅失效信号，不含凭据/请求体 | 通过 | T15-1 草稿/搜索/request+pending 存储；T15-3 旧图片/搜索/草稿/存储清理与消息字段断言；T15-5 退出清理；T15-4 路由返回 |
| 4 401/403/供应商失败语义区分；恢复无提权；旧 payload 服务端拒绝且不额外调用；修复缺口后复验 | 通过 | T15-6 固定 401 清空、403/503 保持；`runAction` 过期返回 false 防止旧动作成功；后端 `test_t59` 伪造 owner/角色/stale principal、`test_t60` 外部 artifact/CSRF、`test_t62` 后台 owner/禁用全部通过 |
| 5 仅隔离环境验证；不操作真实用户数据/凭据/运行配置；保护用户未提交修改；测试/文档/截图/临时产物不进应用本体 | 通过 | 只在专用 worktree 修改；原用户树仅只读核对；测试在 `tests/`、验收文档在 `docs/acceptance/`；无后端迁移或运行配置变更；构建产物 gitignore |
| 6 所有新增入口从接入起遵守身份/所有者/对象关联/内容门禁/CSRF/来源/审计 | 通过（无新增后端入口） | 前端只读既有 owner 过滤接口；未新增后端写路由；后端既有 `test_t59/test_t60/test_t62` 验证身份、owner、对象关联、CSRF 和审计边界 |
| 7 不改 SRS 参数或次数口径；不新增免费预览、自动等待超时、隐式排队或自动图片重试；缺失证据保持未知 | 通过 | 无 SRS/迁移/后端生产变更；未新增任何预览、超时、队列或自动重试；真实浏览器/手机/供应商证据未声称通过，留 #129/#128 |

## 6. 旧展示与测试替换关系

#122 已退役旧 V2 展示 spec。本票把账号生命周期隔离责任落到 `t127-lifecycle-isolation.spec.ts`，并复用 `t124-attachment.spec.ts` 的上传晚到用例和 `t126-history.spec.ts` 的图片历史用例。未恢复旧工作台、旧项目抽屉或旧结果页交互；结果页仍为只读定位。

## 7. 残余未知与边界

- 未做真实浏览器 BFCache、真实跨标签 BroadcastChannel、真实手机软键盘或真实供应商网络时延验收；#129/#128 负责，当前结论只覆盖 jsdom 组件集成和受控 HTTP 边界。
- 测试环境不安装真实路由身份守卫；测试通过 `ensureSession` 和会话状态直接驱动生命周期，真实入口守卫由既有 `t55-auth` 等安全测试与后端测试补充。
- 轮询只验证了代码级 session/sequence/project 守卫和全套回归，没有用真实 2.5 秒轮询计时器单独构造端到端网络竞争；该边界与 #125 的任务状态轮询共用实现。
- 跨标签信号测试断言只含 `nonce`/`type`，但不验证浏览器原生 BroadcastChannel 的跨进程投递；#129 真实浏览器验收负责。
- 403/503 显示的安全错误由既有 API 客户端消息清洗逻辑提供；未新增服务端错误码。
- 既有旧 V2 全链路用例的 `FINAL_CONFIRMATION_REQUIRED` 基线问题不在本票范围。

## 8. 合并复核（Lead）

- 待功能提交、合并和合并后验收完成后填写。
