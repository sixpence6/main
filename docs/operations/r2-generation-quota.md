# R2 本人用量与管理员额度运维说明

- Issue: #67
- 分支: ticket/67
- 代码提交: e709aa63037a03a4ce25ea657de0243d13598657
- 适用版本: v3 R2 #66 之后

## 1. 这份说明解决什么问题

本票把生成受理的次数和并发占用变成可解释、可管理的运行边界：

- 每个账号默认滚动 24 小时最多受理 20 次生成，个人同时 1 个活跃任务，全实例同时 2 个，单项目同时 1 个。
- 管理员可以调整实例默认值，也可以给单个普通账号设置每日和个人槽位覆盖；普通用户只能查看自己的用量。
- 限额检查在模型调用之前完成；超限直接返回 429，没有隐藏队列、自动排队或自动重提。
- 刷新、重启、禁用账号不会清零用量；降额不会取消已经受理的意图，也不会删除历史用量。
- 每次管理变更与审计写入在同一事务中，审计失败时变更回滚。

## 2. 配置与部署

### 2.1 环境默认值

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| generation_daily_limit | 20 | 每账号滚动 24 小时受理上限 |
| generation_personal_slots | 1 | 每账号活跃生成占用上限 |
| generation_instance_slots | 2 | 全实例活跃生成占用上限 |
| generation_project_slots | 1 | 单项目活跃生成占用上限（固定，不开放账号覆盖） |
| visual_image_size | 3840x2160 | 默认单图尺寸，与额度无关 |

这些环境变量是进程启动时的默认值，修改后需要重启服务。运行中的调整优先使用管理员接口。

### 2.2 有效值解析

对某个账号：

1. 若 generation_quota_limits 有 scope=account、scope_id=账号 id 的行，先取该行的 daily_limit 和 personal_slots。
2. 空字段回退到 scope=instance 的实例行。
3. 实例行也为空时回退到环境默认值。
4. instance_slots 只从实例行或环境默认值解析；账号行不允许设置。
5. project_slots 始终取环境默认值，当前为 1。

没有额度行的账号行为与 #65 完全一致，不需要初始化数据。

### 2.3 部署步骤

1. 先升级数据库到 0009：

~~~
cd D:/Desktop/新建文件夹/.worktrees/t67/backend
PYTHONPATH=D:/Desktop/新建文件夹/.worktrees/t67/main/backend D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m alembic -c D:/Desktop/新建文件夹/.worktrees/t67/backend/alembic.ini upgrade head
~~~

2. 确认 alembic_version 为 0009，且 generation_quota_limits 表存在。
3. 重启后端和前端静态资源；前端需要包含本票的管理页构建产物。
4. 不需要清空 generation_intents、generation_usage 或 generation_slots。历史用量继续参与滚动窗口计算。
5. 不要直接编辑数据库中的额度、用量或槽位行。额度调整使用管理接口或管理页；用量是受理账本，不是缓存。

## 3. 前端行为

### 3.1 管理员

进入账号管理页：

- “实例生成额度”面板可设置每日受理次数、个人活跃槽位、实例并发槽位。空白表示继承环境默认值。
- 点击任一非管理员账号的“调整额度”，可设置该账号的每日和个人槽位覆盖；对话框显示当前生效值、已用／剩余、个人／实例活跃数和窗口起止。
- 账号额度对话框只显示聚合用量，不显示项目 id、项目内容或生成结果。
- 管理员账号没有“调整额度”按钮；管理员也不能作为账号额度目标。管理员自己的创作任务同样受实例额度限制，没有豁免。

### 3.2 普通用户

流程面板显示自己的今日已受理、剩余、个人／实例／项目 active/limit，以及窗口起止。普通用户不能调用管理接口，也不能修改额度。

前端不承诺费用封顶，不显示余额或金额；额度只表示受理次数和并发占用。

## 4. 常见错误与处理

### 4.1 429 GENERATION_LIMIT_REACHED

含义：当前账号滚动 24 小时内的受理次数已达到有效每日额度。

处理：

1. 查看流程面板的已用／剩余和窗口起止，等待窗口滑出旧记录。
2. 不要重复点击生成；重复点击不会创建新意图，也不会绕过额度。
3. 管理员可在确认业务需要后通过账号额度对话框提高每日额度；提高后新的确认才可使用新增额度。
4. 不要为了“重置”删除 generation_usage 行；这会破坏审计和后续窗口计算，且不是支持的操作。

### 4.2 429 GENERATION_SLOT_BUSY

含义：个人、实例或项目活跃槽位已满。

处理：

1. 查看是个人还是实例槽位满；项目槽位为 1。
2. 等待现有任务进入可证明的本地终态，或在 #68／#69 支持的核实、恢复、结案流程中处理。
3. 不要新建第二个项目或第二个浏览器标签试图绕过；同一实例的槽位检查是原子的。
4. 系统不会自动排队或自动重提；受限请求不会在后台偷偷等待。

### 4.3 403 ADMIN_REQUIRED

含义：普通用户直接调用了管理额度接口。

处理：这是预期的权限拒绝。普通用户只能读取自己的流程视图；需要调整额度时联系管理员。

### 4.4 422 校验失败

含义：请求带有多余字段，或传入 0、负数、非整数。管理接口只接受正整数或 null（null 表示删除覆盖），且额外字段被禁止。

处理：按接口字段重新提交；不要尝试通过伪造 owner、role、effective 字段绕过。

### 4.5 404 ACCOUNT_NOT_FOUND

含义：账号额度目标不存在，或目标是管理员账号。

处理：管理员账号不作为账号级额度目标；确认账号 id 后重试。管理员创作使用实例默认限制。

### 4.6 500 审计写入失败

含义：额度变更与审计在同一事务中，审计写入抛错时变更不会提交。

处理：检查数据库和审计依赖，修复后重试。不要手工写入额度行来“补”一次失败变更；重试同一管理操作即可。

### 4.7 降额后已有任务被拒绝

含义：降额只影响后续受理，不取消已受理意图；已有任务继续按原意图执行，但新的确认会按新额度判断。

处理：不要删除意图或用量行。若已有任务占用槽位，等待其本地终态或按 #68／#69 的恢复流程处理。

## 5. 只读核对与审计

本人用量：

~~~
GET /api/projects/{project_id}/workflow
~~~

读取 view.usage：daily_limit、used、remaining、window_started_at、window_expires_at、personal_active／limit、instance_active／limit、project_active／limit。该请求只读数据库投影，不调用模型。

管理员核对：

~~~
GET /api/admin/generation-limits
GET /api/admin/accounts/{account_id}/generation-limits
~~~

第一个接口返回实例覆盖、有效值和默认值；第二个返回账号覆盖、有效值和账号级聚合用量，不含项目内容。

审计事件 generation_limits_updated：

| 字段 | 含义 |
| --- | --- |
| detail.scope | instance 或 account |
| detail.before | 变更前覆盖值和有效值 |
| detail.after | 变更后覆盖值和有效值 |
| actor | 执行调整的管理员 |
| target | generation_limits 或目标账号 |

排查顺序：先读管理接口确认当前 policy／effective，再读审计事件的 before／after，最后核对 generation_usage 的窗口记录。不要直接改表。

## 6. 验证与回归

定向测试：

~~~
cd D:/Desktop/新建文件夹/.worktrees/t67/backend
PYTHONPATH=D:/Desktop/新建文件夹/.worktrees/t67/main/backend D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings tests/test_t67_generation_quota.py
~~~

预期：5 passed。

HTTP smoke：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t67-smoke/smoke.py
~~~

预期：ok=true，checks=80。smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite、完整 App 和 fake LLM／Visual，覆盖默认值、管理变更、普通用户 403、重放、槽位、降额、旧入口关闭、发布字节和审计前后值。

前端验证：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

预期：36 个文件、213 个测试通过，vue-tsc exit 0，Vite 构建成功。

## 7. 回滚与故障边界

- 本票新增 generation_quota_limits 表；回滚代码前先确认是否需要保留管理员设置。降级 0009 会删除覆盖表，但不会删除 generation_intents、generation_usage 或 generation_slots。
- 不要删除用量行来“重置额度”。用量是滚动窗口账本；删除会破坏审计，并可能让额度被意外放宽。
- 回滚后环境默认值仍然生效；已有意图和槽位按 #66 的行为继续处理。
- 本票不证明真实供应商计费、远端限流或供应商侧幂等；遇到远端未知按 #68 的保守策略处理，不自动重发。
- 本票不提供费用封顶、余额、账单、自动扩容、隐藏队列或自动重提。
- 未授权真实数据根不得用于 smoke 或排障；所有验证使用临时 SQLite 和临时文件目录。
