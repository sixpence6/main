# R1 本人改密、多端会话与本机首管恢复验收记录（ticket #57）

- **Issue**：GitHub #57《V3 T05 [R1]：本人改密、多端会话与本机首管恢复》
- **父规格**：GitHub #52；R1 阶段同时遵循 issue #51 的认证、会话与敏感变更约束
- **分支**：`ticket/57`（基于 `integration/v3` `0f4f818`）
- **代码版本**：`9ef2255` `feat(auth): add personal session controls and local admin recovery (#57)`；本记录为同一分支的文档提交
- **记录日期**：2026-10-31（Asia/Shanghai）
- **状态**：**本票范围已实现并通过下方列出的定向、全量、前端构建与真实 HTTP 证据；不表示完整 R1 或 v3 验收通过。**

## 1. 结论与边界

本票交付账号已登录后的自助改密、多端会话列表与撤销，以及仅限服务器本机终端执行的首管理员遗失恢复。实现包含：

- `POST /api/auth/password/change`：验证当前密码、执行密码策略、原子更新密码哈希、递增会话纪元、撤销该账号全部会话与未使用链接、写审计；并发改密只有一个成功，其余返回 `409 PASSWORD_CHANGED`。
- `GET /api/auth/sessions`：只返回调用者自己的活动会话，字段仅含不可变会话 ID、是否当前、创建／最近活动／空闲到期／绝对到期时间与客户端信息；不返回 token、CSRF、摘要或来源引用。
- `DELETE /api/auth/sessions/{session_id}`、`POST /api/auth/sessions/revoke-others`：本人在线撤销指定会话或其他会话；跨账号／不存在／已失效 ID 统一 `404 SESSION_NOT_FOUND`；撤销当前会话同时清除 Cookie。
- 普通退出仍只撤销当前会话。空闲 30 分钟／绝对 12 小时由服务端验证，轮询请求不续期；下一请求立即按撤销或超时状态拒绝。该语义沿用 #55 的中间件和会话实现，本票新增本人撤销路径并复验。
- 本机维护命令 `python -m app.auth.cli recover-admin [email]`：要求真实本地 TTY，交互读取并二次确认新密码，不接受 `--password`；只允许管理员目标，原子重置密码、清除禁用状态、递增会话纪元、撤销全部会话及未使用链接、写 `local_maintainer` 审计。
- 不提供任何 HTTP 首管恢复路由；管理功能不能通过网络接口替代本机维护。
- 前端 `/account/security`：显示当前账号、改密表单、会话列表、当前会话标记及撤销操作；成功后回到登录页。

明确不属于本票、也未在本次宣称完成的内容：

| 未完成项 | 归属 |
|---|---|
| 管理员禁用／恢复普通账号及恢复后凭证语义 | #58 |
| 项目／会话／需求等业务对象的所有者隔离 | #59 |
| 完整账号管理界面、品牌化登录、移动端视觉验收 | #76 |
| 跨标签页／浏览器历史返回的会话切换 | #72 |
| 邮件发送、自助注册、管理员代登、公开互联网入口 | 明确不做 |

本记录没有读取、迁移、备份、移动或删除 `backend/data` 与 `main/backend/data`。全部临时数据库、构建产物和服务日志位于未跟踪的 `.scratch/t57-*` 临时根或工作树内未跟踪 `dist`。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 分支 | `ticket/57` |
| 基线 | `0f4f818`（`integration/v3`，已合并 #56） |
| 代码提交 | `9ef2255` |
| 操作系统 | Windows 10（10.0.19045，64 位） |
| Python | 3.12.13（`backend/.venv/Scripts/python.exe`） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 前端构建 | Vite 8.3.5 |
| 临时数据根 | `.scratch/t57-session-smoke/data`、`.scratch/t57-final-basetemp` |

## 3. 实现摘要

### 3.1 本人改密

- 先校验新密码策略（15–128 个 Unicode code point、允许空格、弱口令表 fail-closed），再验证当前密码；当前密码错误返回 `400 CURRENT_PASSWORD_INVALID`，不泄露其他信息。
- 验证与哈希在写事务外执行，避免长事务占用 SQLite 写锁；写事务内重新加载账号与会话，复核账号可登录、会话未撤销、纪元匹配、绝对／空闲未超时，并确认密码哈希仍是验证时的值。
- 成功后更新 `password_hash`、`password_set_at`、`password_policy_version`，递增 `session_epoch`，批量撤销该账号所有会话和所有未使用账号链接；审计 `password_changed` 与敏感变更位于同一事务，审计写入失败整体回滚。
- 响应 `204` 并清除浏览器 Cookie；旧密码和所有旧会话立即失效。

### 3.2 多端会话

- 会话投影由服务端按 `account_epoch`、绝对到期、空闲到期和撤销状态计算，只返回活动会话。
- 客户端信息来自登录时的受限 User-Agent 记录，不返回 token／CSRF／摘要／来源引用。
- 撤销指定会话与撤销其他会话均限定在调用者账号内；跨账号 ID 与随机不存在 ID 返回同一 `404 SESSION_NOT_FOUND`。
- 撤销当前会话后清 Cookie；撤销其他会话保留当前会话；其他账号的会话不受影响。
- 普通 `POST /api/auth/logout` 只撤销当前会话，不扩展为全端退出。

### 3.3 首管理员本机恢复

- 新增 `recover-admin` 子命令，与 `init-admin` 一样要求 stdin 与 stdout 都是真实 TTY；非 TTY 退出码 2，提示“本机维护命令只能在本机交互终端中运行。”。
- 未提供邮箱时要求数据库恰好存在一个管理员；提供邮箱时只接受该管理员。普通账号、未知邮箱、无管理员或多管理员且未指定目标均拒绝，退出码 1。
- 校验并哈希新密码后，在同一写事务内更新管理员密码／策略、清除 `is_disabled` 与禁用元数据、递增 `session_epoch`、撤销全部会话与未使用链接，并写 `admin_recovered` 审计（`actor_kind=local_maintainer`）。
- 恢复完成后必须使用新密码正常登录；不会自动创建会话，也不会自动重启服务。
- 不存在 `/api/.../recover-admin` 路由；OpenAPI 中无恢复路径。网络接口不能借维护功能提权。

### 3.4 前端

- `/account/security` 为登录后页面，页头提供“账号安全”入口；展示当前账号邮箱。
- 改密表单要求当前密码、新密码、确认密码，前端执行 15–128 长度与一致性校验；提交走统一 CSRF store；成功后清除本地会话并跳转登录。
- 会话列表显示客户端信息、当前标记、最近活动和绝对到期；只有“撤销”操作，不展示任何凭证；撤销当前会话后清本地会话并跳转登录。
- 页面错误按 400／404／409／422／429／503 给出稳定提示，不渲染服务端内部信息。

## 4. 执行命令与结果

以下命令均在 `D:/Desktop/新建文件夹/.worktrees/t57` 内执行；后端工作目录为 `backend`。

### 4.1 定向后端测试

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings tests/test_t57_sessions.py tests/test_t15_contract.py
```

结果：**17 passed，9.94s，exit 0**。包含 12 个 #57 会话／恢复测试与更新后的 OpenAPI 契约测试。

### 4.2 后端全量回归

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\pytest.exe -q --disable-warnings --basetemp=D:\Desktop\新建文件夹\.scratch\t57-final-basetemp
```

环境变量：`TEMP=TMP=D:\Desktop\新建文件夹\.scratch\t57-pytest`。结果（exit 0）：

```text
504 passed, 2 warnings in 480.03s (0:08:00)
```

其中包含既有 492 个测试与本次新增 12 个测试。摘要日志保存在 `.scratch/t57-backend-full.log`。

### 4.3 前端测试、类型检查与生产构建

```powershell
cmd.exe /c "D:\Desktop\新建文件夹\node_modules\.bin\vitest.cmd run --reporter=dot"
cmd.exe /c "D:\Desktop\新建文件夹\node_modules\.bin\vue-tsc.cmd --noEmit -p D:\Desktop\新建文件夹\.worktrees\t57\tsconfig.json"
cmd.exe /c "D:\Desktop\新建文件夹\node_modules\.bin\vite.cmd build"
```

结果：

| 检查 | 结果 |
|---|---|
| Vitest | **31 个测试文件、202 个测试通过，16.41s，exit 0**；仅既有 `fireEvent.change`／`fireEvent.input` 告警 |
| vue-tsc | exit 0，无输出 |
| Vite build | 成功，687ms；输出 `dist/index.html`、`dist/assets/index-CAeF0yqy.css`、`dist/assets/index-CwodAPB3.js`；仅有大于 500 kB 的既有 chunk 提示 |

### 4.4 Ruff

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\ruff.exe check main/backend/app/auth backend/tests/test_t57_sessions.py backend/tests/test_t15_contract.py
```

结果：**All checks passed!，exit 0**。

### 4.5 真实 HTTP + 临时 SQLite 冒烟

脚本：`.scratch/t57-run-smoke.py`；恢复辅助：`.scratch/t57-recover-service.py`；结果：`.scratch/t57-session-smoke/result.json`。脚本在 `.scratch/t57-session-smoke/data` 建库，启动真实 uvicorn（127.0.0.1:18769，临时 SQLite，完整 App），使用显式回环 Host／Origin、`ALLOW_LOOPBACK_HTTP=true`、`AUTH_COOKIE_SECURE=false`，仅用于本机开发验证。

```powershell
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe D:\Desktop\新建文件夹\.scratch\t57-run-smoke.py
```

结果（exit 0，摘要）：

| 检查 | 结果 |
|---|---|
| 匿名访问业务 API／恢复路径 | 401／401 |
| 同账号三次真实登录 | 200／200／200 |
| 会话列表 | 200，三条，当前标记恰一条 |
| 会话字段 | 仅 `id,current,created_at,last_activity_at,idle_expires_at,absolute_expires_at,client_info`；无 token 字段 |
| 撤销指定其他会话 | 204；该会话随后访问业务 API 401，当前会话仍 200 |
| 撤销其他会话 | 204；第三个会话 401，当前会话仍 200 |
| 当前密码错误 | 400 `CURRENT_PASSWORD_INVALID`；原会话仍可用 |
| 正确改密 | 204；原会话随后 401；旧密码登录 401；新密码登录 200 |
| 本机恢复（真实服务运行中、同一临时库） | 辅助进程 exit 0 |
| 恢复后旧会话／旧密码 | 401／401 |
| 恢复后新密码登录 | 200 |
| OpenAPI 恢复路径 | 空数组；会话路径三条 |
| 非 TTY 执行恢复命令 | exit 2，提示“本机维护命令只能在本机交互终端中运行。” |
| 服务日志出现密码 | false |

完整输出在 `.scratch/t57-session-smoke/result.json`；服务日志在 `.scratch/t57-session-smoke/uvicorn.log`。冒烟初版曾因会话列表不返回原始 token、按任意其他会话选择目标导致误判；最终改为每个客户端发送不同 User-Agent 并按 `client_info` 选中目标后通过。OpenAPI 正确路径为 `/api/openapi.json`。

### 4.6 会话超时与轮询

超时边界由既有 #55 测试以冻结时钟覆盖：空闲 30 分钟、绝对 12 小时、轮询请求不续期、下一请求拒绝。本票新增的会话投影测试在同一冻结时钟下验证 `+1801s` 与 `+12h+1s` 不再出现在活动会话列表。真实 HTTP 冒烟验证撤销后的下一请求立即 401。

## 5. 验收覆盖表（仅限本票部分）

| 验收项 | 本票覆盖 | 证据 | 未覆盖／剩余 |
|---|---|---|---|
| **V3-AC-05** 密码设置／修改 | 已登录用户验证当前密码后改密；新密码策略；改密撤销全部会话与未使用链接；重置链接路径已在 #56 验证 | `test_password_change_verifies_current_and_revokes_all_sessions`、`test_password_change_rejects_policy_failure_without_touching_old_password`、真实 HTTP 改密段 | 首次设置／管理员代设链接属 #56；品牌化页面属 #76 |
| **V3-AC-10** 空闲／绝对时限与轮询 | 服务端会话验证沿用 #55 的空闲 30 分钟／绝对 12 小时、轮询不续期、下一请求拒绝；本票投影只显示活动会话 | #55 的 `test_idle_absolute_and_polling` 等；本票 `test_session_timeouts_are_reflected_by_active_session_projection` | 真实浏览器长时间等待属 #85 真实设备验收 |
| **V3-AC-11** 多端会话 | 同账号多会话并存；列表只返回安全字段；本人可撤销指定／其他会话；跨账号不可见且统一 404；普通退出只撤销当前 | `test_session_list_returns_only_own_active_sessions_without_secrets`、`test_revoke_one_session_is_immediate_and_unknown_cross_account_ids_are_redacted`、`test_revoke_current_clears_cookies_and_next_request_is_unauthorized`、`test_revoke_other_sessions_keeps_current_and_does_not_touch_other_accounts`、真实 HTTP | 跨标签页／历史返回属 #72；账号切换属 #71 |
| **V3-AC-12** 本机首管恢复 | 仅本机 TTY；无网络路由；只接受管理员；重置密码、清禁用、撤销全部会话与未使用链接、原子审计；完成后旧凭据失效 | `test_local_recover_admin_resets_password_revokes_sessions_and_links_and_audits`、`test_recover_admin_rejects_non_admin_target_and_audit_failure_rolls_back`、`test_recovery_has_no_network_route_and_cli_has_no_password_option`、真实 HTTP 恢复段 | 服务器控制台物理／远程受控访问流程属部署运维，不在代码票 |
| **V3-AC-13** CSRF／Origin | 改密与会话撤销走已认证 CSRF 与精确 Origin；GET 会话列表不改状态；私有响应 no-store／no-referrer | #55 中间件测试；本票真实 HTTP 所有改密／撤销均带 CSRF；前端统一 CSRF store | 真实浏览器跨站点伪造与多标签页属 #72／#76 |
| **V3-AC-34** 审计完整性 | 改密、撤销单会话、撤销其他会话、首管恢复均写审计；稳定账号／会话 ID；不记录密码或 token；敏感变更与审计同事务，失败回滚 | `test_password_change_audit_failure_rolls_back_hash_epoch_and_sessions`、`test_local_recover_admin_..._audits`、`test_recover_admin_..._audit_failure_rolls_back`、真实 HTTP 日志密码缺失 | 90 天保留清理与完整审计查询界面随发布票 |

本票不宣称上述 AC 已跨阶段全部通过；例如 #58 的禁用恢复、#59 的对象级隔离、#72 的跨标签页恢复仍需各自验收。

## 6. 证据文件

| 路径 | 内容 |
|---|---|
| `backend/tests/test_t57_sessions.py` | 12 个定向会话／恢复测试 |
| `tests/t57-security.spec.ts` | 4 个账号安全页前端测试 |
| `.scratch/t57-run-smoke.py`／`.scratch/t57-session-smoke/result.json` | 真实 uvicorn + 临时 SQLite 会话与恢复冒烟 |
| `.scratch/t57-recover-service.py` | 同库本机恢复辅助调用 |
| `.scratch/t57-backend-full.log` | 504 passed 全量后端摘要 |
| `docs/operations/r1-local-admin-recovery.md` | 首管遗失恢复操作说明 |
| `docs/operations/r1-local-admin-init.md` | 首管初始化操作说明（已交叉引用恢复流程） |

## 7. 残余风险与后续

1. **#58**：管理员禁用／恢复普通账号；当前会话撤销只覆盖本人操作与改密，不含管理员禁用触发。
2. **#59**：项目及子资源的所有者隔离；本票的会话认证不等于对象级授权。
3. **#71／#72**：账号切换、跨标签页／浏览器历史返回的会话恢复与缓存边界。
4. **#76**：品牌化登录、账号页与移动端视觉验收。
5. **发布阶段**：#78 统一生产入口、#80 局域网 HTTPS、#81–#84 备份／恢复／保留清理、#85 真实设备闭环。
6. **恢复操作**：必须在服务器本机受控终端执行；不得通过远程 shell 共享会话、命令历史或聊天工具传递管理员密码。

**本记录不声称完整 R1 或 v3 已验收。**
