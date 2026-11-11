# 对话意图路由验收记录（2026-11-11）

状态：本次修复已于 2026-11-12 合入本地 visual/v3，两路 code-review 和最终 npm run check 均通过。前半部分保留各分支当时的红灯与待办历史；最终结果见文末。不代表 v3 整体发布或运行数据恢复。

## 范围与基线

- 基线：`fc7f6868d6e1499aa436e55b8dc2104a245b409e`。
- 测试分支：`codex/chat-intent-tests`。
- 工作树：`C:/Users/28425/.codex/worktrees/chat-intent-tests/新建文件夹`。
- 本分支只修改 `backend/tests/**`、`tests/**` 及本次两份文档，未修改应用、package 配置或 `scripts/check.mjs`。
- [目标契约](../architecture/chat-intent-routing-2026-11-11.md) 与测试新增后仍基于上述应用代码验证。未合并其他 worker 分支。已只读核对前端实现提交 `8b12a893a2006a8c8e59784f27b77b585742e78f` 的 `turn_kind` 契约。

## 测试构成

后端新增 24 个收集用例：6 个非制作意图、4 个真实请求分类、5 个生命周期场景（每个依次检查问候/感谢/能力询问/讨论）、4 个首消息制作及最终确认、2 个审美回答、2 个缺商品回答、1 个显式暂缓。

生命周期测试在问候时可遇到错误的 `image_request/ready` 模型响应；新会话旧 organize 适配器也故意返回凭空填好的商品和 prompt。S03 用先提问、随后错误 ready 的脚本验证程序保护。测试断言生产角色调用、provider 次数、真实数据库和 workflow 投影，非仅检查脚本声明的分类。

前端新增“首条闲聊后在同一项目继续制作”用例，覆盖首条响应 `workflow=null`；更新两份现有测试中的首消息 mock/断言为 `/messages`，请求正文为 `content/expected_revision/request_id`，制作响应助手 `turn_kind=workflow`，动作序列移除 `submit_request`。两文件共 14 个用例；全前端现为 236 个。

## 实际执行与结果

Windows PowerShell。每条命令显式指定下述 cwd；Python 仍使用原虚拟环境：

```powershell
# cwd: C:/Users/28425/.codex/worktrees/chat-intent-tests/新建文件夹/backend
$env:TEMP = 'D:/Desktop/新建文件夹/backend/.pytest_tmp_chat_intent'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe' -m pytest -p no:cacheprovider -q tests/test_chat_intent_routing.py --tb=short
```

结果：**14 failed, 10 passed in 26.68s**。无收集或夹具错误。预期失败包括：问候/感谢/能力询问误判 requested；五个生命周期中的闲聊不满足 conversation/状态保护；四个首条真实请求未通过完整准备与确认契约；绑定 `A，但别太暗` 返回 `409 QUESTION_EXPIRED`；显式暂缓的首消息仍返回 workflow 而非 conversation。生成中前置条件等待 provider 已开始，并在 finally 释放受控 gate。

```powershell
# cwd: C:/Users/28425/.codex/worktrees/chat-intent-tests/新建文件夹
& 'D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe' -m ruff check --no-cache backend/tests/test_chat_intent_routing.py --ignore E501
npm run typecheck
npm run check
```

结果：新增后端测试 lint **通过**，typecheck **通过**。`npm run check` 的依赖图和 typecheck 通过；前端 **233 passed, 3 failed（30 files，2 failed/28 passed）**，在前端阶段退出，后端 lint/targeted suite 未由该次总检查执行。3 个失败均在本次调整的首条消息路径：T122 首次制作、T122 首条闲聊、T152 首次等待提示。原应用仍调用 `submit_request`，与目标断言冲突。直接执行后端回归的结果如上。

执行环境备注：C 盘工作树内 Python 临时文件写入返回 PermissionError，故 TEMP/TMP 指向 D 盘独立临时目录；这不改变应用导入位置。`backend/tests/conftest.py` 从本工作树的 `main/backend` 导入。初次 `npm run check` 因工作树缺 `.venv` 退出；建立指向原环境的忽略项 junction 后执行，才得到上述前端阶段结果。共享 node_modules junction 使用父任务提供的环境。未改配置绕过检查。

## 集成待验证项（由父任务补录）

本分支的红灯只证明回归断言已建立。合并审阅后的后端、前端、测试与父任务 `scripts/check.mjs` 修改后，需要记录：

1. 集成提交及实际运行目录。
2. `npm run check` 最终退出码与各阶段结果，确认 targeted suite 包含 `tests/test_chat_intent_routing.py`。
3. 新增后端 24 个用例和前端 236 个用例的实际通过/失败情况；若计数改变，说明新增或调整原因。
4. 人工确认自然回复、待答问题仍可回答、闲聊后同一提示词仍可确认、完成后真实新请求仍可准备，以及 provider 只在最终确认后提交。

接口交接：`/messages` 保留现有请求形状；闲聊助手必须 `turn_kind=conversation`，制作准备助手必须 `workflow`。绑定的混合选项正文应支持 `A，但别太暗`。等待最终确认的闲聊必须保留原 snapshot 和 command_context 的可用性。未作真实外部模型或图片服务调用；脚本适配器验收不能替代真实服务验收。

## 评审补充回归（尚未集成）

`c4d8a4a` 在首轮 24 个用例后新增 3 个无选项文案问题回归：两种模型 kind 下“不用”应保存 `texts=[]` 并继续准备；问候保留问题。原应用定向结果为 **3 failed, 24 deselected in 8.00s**：两种 kind 都把“不用”保存为图片文字 `['不用']`，问候仍返回 workflow。这是原基线证据，不代表后端 worker 当前代码的状态。

随后针对 spec 评审的三项发现，测试文件增加到 **37 个收集用例**。新增意见分类与两个制作对照，并用独立 API 用例直接检查：

- 新会话“我觉得冷白背景比较适合钢笔主图”即使遇到错误 ready/完整 brief，也只对话。
- 等待提示词时“我喜欢这个背景”保留精确 snapshot、intent 和 usage。
- 生成中“我很喜欢这个文案”不追加 pending_feedback；夹具在需要模型分类时返回 conversation，不能以名词命中代替语义判断。
- “你可以帮我做一张钢笔主图，极简冷白背景，无文字”和“制作钢笔主图，极简冷白背景，文案为为什么选择我们”均可准备；文案脚本响应按实际请求设置，保存的 texts 必须保留原文。

原生命周期场景的 CHAT 列表也增加上述三种意见/赞赏，集成时会在各状态依次检查。以下只运行新增的 7 个定向用例，未重跑整个后端文件、前端或 npm check：

```powershell
# cwd: C:/Users/28425/.codex/worktrees/chat-intent-tests/新建文件夹/backend
# 使用上文相同 TEMP/TMP/PYTHONDONTWRITEBYTECODE 设置
& 'D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe' -m pytest -p no:cacheprovider -q tests/test_chat_intent_routing.py -k 'product_opinion or active_copy_praise or polite-request or question-word-copy' --tb=short
```

结果：**6 failed, 1 passed, 30 deselected in 11.89s**，无夹具错误。通过项是礼貌请求的底层意图判断；失败项为含“为什么”的文案被暂停、两个首条请求未准备、两条意见返回 workflow，以及生成中赞赏被写入 pending_feedback。新增测试 lint 通过。未合并应用修复，尚无本次补充用例的集成绿灯证据；父任务应以 37 个用例作为当前后端回归文件的集成计数。

## 当前字段回答的上下文修正（2026-11-12，尚未集成）

评审确认：原生命周期测试把“我觉得冷白背景比较适合钢笔主图”在所有阶段都当作闲聊，但 `first()` 在 S03 正在询问 `style_keywords`，提供深浅背景选项且允许自由输入；这句话在该上下文是有效的具体字段回答。现仅在 lifecycle 的 clarification 阶段跳过这一句，其余四阶段仍保留；模糊的“我喜欢这个背景”继续在全部五阶段检查 conversation 和问题/状态不变。conversation 断言增加正文诊断，便于定位失败输入。

新增两个正向 API 用例分别发送“我喜欢冷白背景”“我觉得冷白背景比较适合钢笔主图”，检查背景偏好保存、需求版本前进、问题清除、提示词准备，以及确认前零图片提交、确认后一次提交。仅要求保存内容包含具体背景偏好，不强制把原句归一化为某个固定列表项。文件现共 **39 个收集用例**。

```powershell
# cwd: C:/Users/28425/.codex/worktrees/chat-intent-tests/新建文件夹/backend
# 使用上文相同 TEMP/TMP/PYTHONDONTWRITEBYTECODE 设置
& 'D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe' -m pytest -p no:cacheprovider -q tests/test_chat_intent_routing.py -k concrete_preference --tb=short
```

本分支原应用上的定向结果：**2 passed, 37 deselected in 5.75s**。新增测试 lint 与 diff 空白检查通过。本次未运行 lifecycle 或全量检查；这两个正向用例通过不代表其余闲聊、模糊指代或集成验收已经通过。父任务负责最终组合后的全量证据。

## 2026-11-12 最终集成验收

- 实际目录：`D:/Desktop/新建文件夹`。最终代码快照：`2c7020de5d12b403a7d9c77f7047c4c9f29dced1`，已快进合入本地 `visual/v3`。
- 在该快照执行根目录 `npm run check`，退出码 **0**：依赖图、typecheck、前端 **31 文件 / 265 用例通过**、后端 Ruff 通过、后端 targeted suite **355 passed in 369.96s**；检查脚本明确包含 `tests/test_chat_intent_routing.py`。本地日志：`.git/chat-intent-check-final-integrated.log`。
- 本次聊天意图测试由初始 24 项扩展为最终 **44 项**：补充具体审美回答、模糊指代、普通感谢与感谢后真实答案、模型误报 ready/完整 brief、五个生命周期的状态保护等。worker 对最终文件另运行聊天 44 项和对齐 26 项，均通过。
- 前端 265 项与此前 236 项的差异包含同期另一任务的提示词相关测试；最终后端 355 项也包含其提示词内容与恢复测试，不能把全部新增计为本任务独立编写。
- 两路 code-review 最终均 PASS，Standards 0 项、Spec 0 项；初审问题及最后的感谢语 P2 均修复。审查范围、分支 tip 与历史失败见 [审查记录](../reviews/chat-intent-routing-20261111.md)。
- 自动化验证覆盖首条闲聊后同项目继续制作、原问题仍可回答、闲聊保留提示词快照、实际答案继续准备以及确认后才提交 provider。模型与图片适配器使用脚本，未调用真实外部服务；未将这些证据声明为人工浏览器或真实模型验收。

## 本地合并与清理

- 三个实现分支的最终 tip 均为最终代码快照祖先；合并后使用 `git branch -d` 删除 `codex/chat-intent-backend`、`codex/chat-intent-frontend`、`codex/chat-intent-tests`，未强制删除。
- 先验证并仅移除四个依赖 junction，再检查三个工作区无任何 reparse point。后端和前端工作区通过应用工具归档，确认两个目录消失且归档附件存在。
- 测试工作区归档被工具拒绝：`The task's primary checkout cannot be removed.` 因此保留干净的 detached 工作区 `C:/Users/28425/.codex/worktrees/chat-intent-tests/新建文件夹`；实现分支已删除，未绕过应用保护移除目录。
- 本任务临时测试缓存移到忽略的 `.scratch/chat-intent-test-caches/` 保留。清理后复核主项目关键源文件、node_modules、Python、根 backend/.env 与 backend/data 仍存在，关键文件大小与清理前记录一致；不把这一局部检查扩大为全部数据完整性证明。
- 同期任务的 `main/backend/data` 与 `main/backups` 原运行数据仍未确认恢复。根 `backend/data` 不是替代库，本次没有覆盖运行数据。详见 [清理事故记录](../reviews/prompt-content-recovery-20261111.md)。
- 本轮仅本地提交，未推送远程。