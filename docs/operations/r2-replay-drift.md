# R2 原意图重放与需求漂移运维说明

- Issue: #66
- 分支: ticket/66
- 代码提交: 3ca3e839cc52d8b9ddc8423b11bed5b40c3e8018
- 适用版本: v3 R2 #65 之后

## 1. 这份说明解决什么问题

本票不增加新的部署组件、后台队列或配置项。它改变的是生成确认后的行为：

- 同一个浏览器确认动作会复用同一个 request_id，刷新、导航或重新登录不会生成第二个任务。
- 服务端把 request_id 与卡片摘要、revision、参考用途、尺寸绑定；同标识换参数会被拒绝，而不是悄悄复用旧授权。
- 生成提示词如果出现需求卡中没有的卖点、文字、价格或弱化要求，会被阻断并要求用户修改需求、重新确认。
- 被阻断的意图只释放占用，不退还已经受理的一次用量；不会自动重试，也不会调用图片服务。

## 2. 配置与部署

本票没有新增配置。沿用 #65：

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| generation_daily_limit | 20 | 每个账号滚动 24 小时受理上限 |
| generation_personal_slots | 1 | 每账号活跃生成占用 |
| generation_instance_slots | 2 | 全实例活跃生成占用 |
| generation_project_slots | 1 | 单项目活跃生成占用 |
| visual_image_size | 3840x2160 | 默认单图尺寸 |

部署要求：

1. 先执行既有迁移到 0008；本票不新增迁移。
2. 不需要清空 generation_intents、generation_usage、generation_slots。
3. 不需要重启后台任务表；已存在任务按原所有权继续推进。
4. 不要直接编辑数据库中的意图状态、用量或占用。处理异常时先读取流程视图和审计事件，再按本说明确认是否需要用户重新确认。

## 3. 前端行为

确认按钮的 request_id 保存在浏览器 localStorage，键形如：

~~~
v3-generation-request:<project_id>:<run_id>:<requirement_revision>:<brief_digest>
~~~

- 同一项目、同一需求版本、同一最终卡片摘要，刷新或重新登录后复用原 id。
- 意图进入 delivered、failed 或 abandoned 后，前端清除该键；下一次确认使用新 id。
- 如果浏览器禁用 localStorage，组件回退到内存 id，不会因为存储失败阻止确认；此时跨标签页不保证复用同一 id，需按第 4 节处理。
- 只读查询、刷新页面、历史导航不调用模型；不要为了“刷新状态”重复点击生成。

## 4. 常见错误与处理

### 4.1 409 INTENT_REPLAY_CONFLICT

含义：同一 request_id 已绑定另一组参数（卡片摘要、revision、尺寸、参考用途等）。

处理：

1. 刷新页面，确认当前需求卡和尺寸是否已变化。
2. 如确实要改尺寸或参考用途，先“修改需求”，重新确认；不要手工改 localStorage 里的 id 试图绕过。
3. 如只是丢响应或双击，刷新后应回到原意图；不要新建第二个任务。
4. 仍冲突时，检查浏览器是否保留了旧卡片摘要的键；可清除该项目的 v3-generation-request 键后重新确认，但必须先确认没有活跃意图，否则会得到 429 占用错误。

### 4.2 409 INTENT_STATE_INVALID

含义：该 request_id 对应的意图已结束、失效或 abandoned。

处理：刷新流程视图；如果当前需求仍有效，重新确认生成。不要修改数据库中的旧意图。

### 4.3 409 PROMPT_REQUIRES_RECONFIRMATION

含义：提示词引入了未确认的卖点、文字、价格或弱化了硬要求；本次未提交生图。

处理：

1. 查看面板提示和 flow issue，不要期待自动重试。
2. 点击“修改需求”，在 S04 补充或纠正需求，重新核对最终卡后再确认。
3. 原意图已 abandoned、占用已释放、原一次用量保留；不要把它当作免费重试。
4. 若反复出现，检查需求卡是否缺少用户实际想要的卖点或文字；这是产品需求与提示词一致性保护，不是模型故障。

### 4.4 409 CARD_DIGEST_MISMATCH／REQUIREMENT_REVISION_CONFLICT／REQUIREMENTS_CHANGED

含义：客户端回显的卡片或需求版本与服务端当前状态不一致，或卡片尺寸／参考用途不匹配。

处理：刷新页面，读取当前最终卡和 revision 后重新确认；不要手工拼接旧 payload。

### 4.5 429 GENERATION_LIMIT_REACHED／GENERATION_SLOT_BUSY

含义：滚动窗口额度用尽，或账号／实例／项目活跃占用已满。

处理：等待窗口过期或等待现有任务进入可证明的本地终态；本系统没有隐藏队列，也不会自动排队。不要重复点击；额度管理由 #67 提供。

## 5. 只读核对与审计

只读核对流程：

~~~
GET /api/projects/{project_id}/workflow
~~~

该请求只读取数据库投影，不调用模型。可核对：

- view.usage 的今日已受理、剩余、个人／实例／项目占用；
- view.intent 的 request_id 前缀、状态、卡片摘要和需求版本；
- view.issue.code 是否为 PROMPT_REQUIRES_RECONFIRMATION。

审计事件：

| 事件 | 含义 |
| --- | --- |
| generation_intent_accepted | 新意图受理、计数和占用写入 |
| generation_intent_abandoned | 提示词漂移导致意图 abandon、占用释放、用量保留 |
| requirements_confirmed | 确认与意图提交 |
| requirements_confirmation_invalidated | 旧确认失效 |

排查时优先看这些事件和 GenerationIntent 的 status，不要直接改表。数据库中的用量行是受理账本，不是可随意清零的缓存。

## 6. 验证与回归

定向测试：

~~~
cd D:/Desktop/新建文件夹/.worktrees/t66/backend
PYTHONPATH=D:/Desktop/新建文件夹/.worktrees/t66/main/backend D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings tests/test_t66_replay_drift.py
~~~

预期：6 passed。

HTTP smoke（需临时目录、fake adapter）：

~~~
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t66-smoke/smoke.py
~~~

预期：ok=true，checks=88。smoke 覆盖跨会话重放、同标识变尺寸、篡改摘要／revision、占用拒绝、漂移后修改需求回到 S04。

前端验证：

~~~
npm test -- --reporter=dot
npm run typecheck
npm run build
~~~

预期：35 个文件、210 个测试通过，vue-tsc exit 0，Vite 构建成功。

## 7. 回滚与故障边界

- 本票没有数据库 schema 变更，回滚代码即可回到 #65 行为；不要删除 generation_intents、generation_usage、generation_slots 表或其中的历史记录。
- 回滚后旧 request_id 的冲突保护消失，但已有意图、用量和占用仍应保留。若需要恢复旧版本，按发布流程回退代码并重新跑 #66 回归。
- 不要因为前端显示冲突就删除数据库行、伪造批准或手工修改 owner；这些操作会破坏审计和归属。
- 真实供应商的远端幂等、计费与未知恢复不在本票证明范围内；遇到远端未知按 #68 的保守策略处理，不自动重发。
- 未授权真实数据根不得用于 smoke 或排障；所有验证使用临时 SQLite 和临时文件目录。
