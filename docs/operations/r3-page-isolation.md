# R3 切号与同账号重登录页面隔离运维说明

- Issue: #71
- 父 Issue: #52
- 分支: ticket/71
- 代码提交: bb9aab322a0fe5c007751bf1d0cf2c357ebe8a09
- 验收日期: 2026-11-01
- 状态: 已交付（R3 阶段）

## 1. 目标与边界

本说明面向维护者和本地运维，解释工作台在以下场景中的行为：

- 同一浏览器退出登录；
- 同一账号在另一个标签或设备重新登录；
- A 账号退出后 B 账号登录，再切回 A；
- 会话 Cookie 过期或被服务端撤销；
- 业务请求返回 401、403 或供应商 503；
- 页面刷新、历史返回、导航和迟到响应。

应用不把浏览器缓存当作权限来源。服务端仍以 HttpOnly 会话 Cookie、CSRF Token、账号状态和项目 owner 校验为唯一授权依据；前端清理只负责不显示旧页面、不重交旧请求、不泄漏旧恢复元数据。

## 2. 登录生命周期模型

1. 登录成功或 GET /api/auth/session 成功时，后端返回 account、session_id、csrf_token、expires_at。
2. session_id 是 AuthSession 记录 id，不是 session token；它用于区分同一账号的不同登录会话。原始 token 只保存在 HttpOnly Cookie，前端不可读。
3. 前端维护 activeSessionKey = account.id + ":" + session_id。account.id 或 session_id 任一变化都会递增 sessionRevision。
4. 同一账号重新登录会得到新的 session_id，因此即使 account.id 相同，也会被视为新生命周期并清空旧页面。
5. 退出、401、切换账号、同账号新会话都会触发：
   - 清空 currentAccount、currentSessionId、CSRF；
   - 中止工作台请求并递增 detailToken、projectContextToken、generationToken；
   - 清空当前项目、消息、生成结果、需求草稿、参考图状态、项目列表和抽屉；
   - 清除浏览器私有恢复键。
6. 已有会话失效时，WorkspaceLayout 隐藏业务并显示“正在重新验证登录状态…”。未认证用户由路由守卫送到 /login；引导阶段的 401 只清状态，不重复跳转。

## 3. 浏览器存储清理范围

clearPrivateBrowserState 清理：

| 存储 | 键 | 内容 |
| --- | --- | --- |
| localStorage | v3-generation-request | 旧版稳定请求标识 |
| localStorage | v3-generation-request:* | 按项目／run／revision／卡片摘要／会话隔离的稳定请求标识 |
| sessionStorage | ecommerce-generation-pending | 旧版未确认生成凭据 |
| sessionStorage | ecommerce-generation-pending:* | 按项目的未确认生成凭据 |

不清理：

- HttpOnly __Host-session／__Host-csrf Cookie：由服务端撤销或过期，JavaScript 不可读写；
- 浏览器缓存、Service Worker、第三方扩展数据；
- 用户自己复制到应用外的内容。

这些键不包含密码、原始会话 token 或 CSRF token；它们只保存稳定 request_id、项目 id、revision 和确认标记。私有存储清理失败不会阻塞退出，也不应被当作授权成功。

## 4. 操作步骤

### 4.1 正常退出

1. 点击右上角退出。
2. 前端调用 POST /api/auth/logout（带 CSRF 和 Origin）。
3. 服务端撤销当前会话并清 Cookie。
4. 前端 clearSession：清空账号、CSRF、业务页面、草稿、参考图和恢复键。
5. 路由进入 /login。

退出只结束本端会话，不影响同账号其他活动会话；其他客户端仍可继续读取自己的项目。

### 4.2 同账号在另一标签重新登录

1. 新标签登录成功后得到新的 session_id。
2. 新标签的 sessionRevision 从该标签当前值递增；旧标签的 sessionRevision 不会变化，因为它是独立 JavaScript 上下文。
3. 新标签从空工作台开始，不复用旧标签的草稿或未发送确认。
4. 旧标签的页面仍显示旧标签自己的会话；服务端按该标签 Cookie 鉴权。若服务端撤销旧标签会话，旧标签下一次请求返回 401，随后隐藏业务并回登录页。
5. 应用不通过 localStorage 广播登录事件；跨标签同步属于 #72 的补证范围，当前不声称已覆盖所有浏览器多标签时序。

### 4.3 A → B → A

1. A 退出或 401 后，业务页面隐藏、草稿和恢复键清空。
2. B 登录后使用自己的空工作台，项目列表与详情均按 B 的 Cookie 查询。
3. A 再登录（新 session_id）时页面仍是空工作台，不会恢复 A 之前的项目、草稿或生成凭据。
4. 服务端对 A、B 的项目访问仍按 owner 校验，跨账号项目返回 404 PROJECT_NOT_FOUND。

### 4.4 会话失效

1. 任一受保护请求返回 401：requestJson 清除 CSRF，调用 unauthorizedHandler。
2. 若已有活动会话，dropSession 清理私有状态并跳转 /login；若处于首次引导，只有守卫决定跳转，避免重复跳转。
3. 已发出的迟到响应即使忽略 AbortSignal，也会因 sessionRevision 不一致被丢弃。
4. 不要通过手动删除 localStorage 来“恢复”页面；正确做法是重新登录并由服务端签发新会话。

### 4.5 401、403、503 的分流

| 状态 | 含义 | 前端行为 |
| --- | --- | --- |
| 401 | 未认证、会话过期或已撤销 | 清账号、清 CSRF、清私有状态、隐藏业务、回登录 |
| 403 | 已认证但无权限／来源不合法 | 保留账号与页面，显示错误；不自动重试 |
| 503 | 服务或供应商暂不可用 | 保留账号与页面，显示错误；不自动重试模型 |
| 网络错误 | 请求未完成 | 保留账号与页面，显示错误；由用户显式重试 |

## 5. 验证与排障

### 5.1 本地验证命令

后端定向（工作树 backend，PYTHONPATH 工作树 main/backend）：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings tests/test_t71_session_binding.py tests/test_t55_auth_foundation.py tests/test_t57_sessions.py
~~~

前端（工作树根）：

~~~
npm test -- --reporter=dot tests/t71-session-isolation.spec.ts
npm run typecheck
npm run build
~~~

### 5.2 常见现象

- 登录后立即回到登录页：检查 Cookie 的 Secure／SameSite／Host 设置，确认使用允许的 Host，且没有在 HTTP 下使用 Secure Cookie；检查反向代理是否改写 Host。
- 401 后仍看到旧项目：确认当前构建包含 sessionRevision 清理；检查是否绕过了 WorkspaceLayout 或直接调用了未封装的 API。
- 403 后被送回登录页：这是错误；403 不应触发 unauthorizedHandler，应检查 requestJson 的状态分支和代理返回码是否被改写。
- 同账号重新登录后出现旧草稿：检查 session_id 是否由后端返回、前端是否使用 activeSessionKey，以及是否存在旧的 sessionStorage 键。
- 刷新后重复生成提交：本票的稳定 request_id 在同一登录生命周期内复用，但切换会话会删除旧键。若仍重复，检查确认动作是否携带 request_id，并查看 GenerationIntent 唯一约束。

### 5.3 证据

- 代码提交 bb9aab322a0fe5c007751bf1d0cf2c357ebe8a09。
- 前端专题：.scratch/t71-t71spec2.log（4 passed）。
- 前端全量：.scratch/t71-frontend-test-final.log（39 files/219 tests）。
- 后端全量：.scratch/t71-full-nonv2.log（351 passed）、.scratch/t71-full-v2.log（235 passed）。
- smoke：.scratch/t71-smoke/result.json（48 checks）。
- 迁移：.scratch/t71-migration（0010 fresh／chain，26 张表，integrity ok）。

## 6. 回滚与数据边界

- 本票没有新增数据库迁移，回滚代码即可回到基线行为；已有 0010 迁移和业务数据不受影响。
- 不得为了验证清理行为删除 backend/data 或 main/backend/data；验证一律使用临时 SQLite 和临时数据目录。
- 前端构建产物应输出到工作树 dist 或部署目录，不要覆盖 main/dist，除非发布流程明确要求并已备份。
- 若需要禁用本票的页面隔离，只能回滚代码并重新构建；不要通过保留旧 localStorage 键或绕过 sessionRevision 来“修复”显示问题。
- 当前不提供管理员强制清除某个浏览器 localStorage 的服务端接口；浏览器本地状态由用户退出／重新登录或浏览器清站点数据解决。
