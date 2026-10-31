# R3 U2 原位授权、执行状态与安全恢复运维说明

- Issue: #74
- 父 Issue: #52
- 分支: ticket/74
- 代码提交: f8f074a88a76a9b4f2711e857c5c8ce9ed9c3d28
- 基线: integration/v3 7409e98
- 更新日期: 2026-11-01
- 状态: 可用（R3 U2 阶段；完整发布边界见 #77／#85）

## 1. 目的与边界

本说明供本地单实例部署的维护者使用，说明 U2 工单在浏览器中的原位授权、执行状态、只读轮询和安全恢复行为。它不替代 R1 的账号与会话操作，也不替代 R2 的受理、计数、槽位和远端未知处置规则。

适用边界：

- 本地或受控单实例部署，账号由管理员创建，没有公开注册、SMTP、MFA 或团队共享。
- 一次确认只生成一张默认 3840×2160 的图片；批量、多尺寸和精确保留局部修改不在本期。
- 本页只展示服务端已产生的状态和已批准的执行提示词，不展示候选提示词，不提供虚构进度。
- 结案不声称远端已取消；页面没有远端取消能力。远端任务是否仍在执行或计费，必须按 R2 的未知事实处理。
- 不读取或写入 backend/data、main/backend/data 的真实数据。任何真实数据操作都需要维护者明确选择来源并完成已核验的备份。

## 2. 用户操作路径

1. 登录后打开项目。项目默认进入 U1／U2 的 describe 工作台，不会自动跳到旧的确认、生成或结果页面。
2. 在对话和参考输入区完善商品、用途和限制。未发送的文字只保存在当前页面内存，不写入 localStorage；切换账号或会话会清除。
3. 等待独立审查通过。只有状态为终稿且已批准的最终需求卡才显示完整字段；候选内容显示“需求卡内容正在审查，通过后会在此完整展示，不能提前确认”。
4. 核对最终需求卡，勾选“确认需求卡”，点击“确认以上需求并生成图片”。按钮只在待确认状态出现；没有勾选时按钮禁用，title 提示“请先勾选确认。”。
5. 页面停留在原位，显示编制提示词、生成图片、检查图片、准备交付四步。进度只显示真实状态，不显示百分比。
6. 需要查看执行提示词时，展开“查看已批准的执行提示词（第 N 次）”。未批准、候选或 payload 缺失的提示词不会出现。
7. 生成完成并准备交付后，仍需人工批准和发布；应用不会自动发布，也不会把等待状态说成完成。
8. 失败、阻塞或远端未知时，按第 4 节选择安全恢复动作，不要刷新页面强行重试，不要重复点击确认。

## 3. 页面状态和按钮

### 3.1 确认按钮

| 状态 | 页面行为 |
| --- | --- |
| confirmationState=none | 没有可确认终稿，只显示继续完善或等待审查 |
| reviewing | 显示“需求卡内容正在审查”，不显示需求和确认按钮 |
| pending | 显示最终需求卡字段、勾选框和生成按钮；这是唯一允许确认的状态 |
| confirmed | 显示生成流程处理中，不重复显示确认按钮 |
| stale | 显示“需要重新确认需求”，要求修改或重新整理后生成新卡，旧确认不能复用 |

确认按钮的禁用原因会显示在按钮 title 和页面说明中：

- 流程状态仍在加载或动作处理中。
- 服务端动作要求文字说明但用户尚未填写。
- 需要先勾选确认框。

### 3.2 四步执行进度

| 步骤 | 服务端阶段 | 页面含义 |
| --- | --- | --- |
| 编制提示词 | S07 | 根据已确认需求生成并独立审查执行提示词 |
| 生成图片 | S08 | 提交原任务并等待远端图片结果 |
| 检查图片 | S09 | 对候选图做质量与合规检查，通过前不展示预览 |
| 准备交付 | S10 | 准备交付记录；批准和发布仍由用户操作 |

步骤状态只有：

- 已完成：服务端已确认该阶段完成。
- 进行中：当前阶段正在执行或等待外部结果。
- 等待：该阶段尚未开始。
- 等待处理：当前流程被阻塞或需要用户处理。
- 已暂停：流程明确失败，保留真实失败状态。
- 结果未确认：远端结果未知或本地记录中断，不能当作成功或未受理。

页面没有百分比、没有模拟倒计时、没有把提交当成完成。执行进度只在存在流程或意图时显示；没有流程时不显示空进度。

### 3.3 后台读取节奏

- 前端仅在流程处于 S07–S10、运行状态为 active、没有交付、没有 approve_delivery／publish_delivery 动作，且意图为 accepted／executing 时轮询。
- 轮询间隔为 2500ms，只调用 GET /api/projects/{id}/workflow；不会 POST，也不会调用模型或图片适配器。
- 页面隐藏、浏览器离线、动作处理中、流程阻塞／失败／需要输入、交付就绪或组件卸载时停止轮询。
- 刷新页面或重新登录会重新读取当前事实，但不会自动补发确认、生成、恢复或分析请求。

## 4. 安全恢复动作

恢复动作只在服务端返回对应 available_actions 时出现；所有动作都必须由用户明确点击。动作按核实、找回、继续、结案、新任务排序，普通操作排在前面。

| 动作 ID | 页面文案 | 语义和边界 |
| --- | --- | --- |
| verify_generation | 核实原任务 | 只读远端状态，不创建任务、不提交图片、不新增受理次数 |
| recover_generation | 找回原结果 | 只轮询或取回原任务已有结果，不重新提交；远端未知时保留占用 |
| retrieve_original_result | 找回原结果 | 旧版结果找回路径，只针对可证明的失败结果，不新建任务 |
| recover_submission | 确认提交状态 | 核实提交是否到达远端，不自动补发 |
| resume_after_restart | 恢复并继续 | 恢复本地中断的原任务，沿用原事实和原计数 |
| continue_workflow | 继续流程 | 从原检查点继续，不新建任务 |
| close_generation_tracking | 结束跟踪 | 只结束本地跟踪并释放已证实占用；使用次数不变，不声称远端已取消 |
| start_new_generation | 开始新任务 | 明确新建任务，会重新核额、计一次并占用槽位；旧任务可能仍在远端执行或计费，需要风险确认 |

页面只在存在核实、找回、继续或结案类动作时显示安全恢复提示：“安全恢复：以下核实、找回、继续或结束跟踪操作不会自动提交新的生成请求，也不会自动新增受理次数。”如果唯一可见动作是 start_new_generation，则不显示这句，页面会明确说明新任务会重新计数。

### 4.1 常见处置

- 远端未知：先点“核实原任务”。核实只读；如果仍未知，保留一次计数和槽位，不要按超时推定为未受理，也不要重复确认。
- 提交后中断：点“恢复并继续”或“确认提交状态”，沿用原任务；系统不会自动补发。
- 结果可取回：点“找回原结果”，只取回原任务结果；若结果尚未就绪，页面显示结果未确认。
- 用户不愿继续等待：点“结束跟踪”。使用次数不变，只释放已证实占用；不要告诉用户远端已取消。
- 需要重新生成：只能通过“开始新任务”并确认风险。新任务另计一次，旧任务可能仍产生费用。

## 5. 验收和复现命令

以下命令只用于本地复现，工作目录为 D:/Desktop/新建文件夹/.worktrees/t74，且使用临时 SQLite 与 fake adapter；不要对真实数据根执行。

前端定向测试：

~~~
npm test -- --run tests/t74-u2-authorization.spec.ts --reporter=dot
~~~

预期：1 个文件、8 个测试通过，exit 0。

前端全量、类型和构建：

~~~
npm test -- --run --reporter=dot
npm run typecheck
npm run build
~~~

预期：42 个文件、242 个测试通过；vue-tsc exit 0；Vite exit 0，仅 chunk-size 警告。

后端回归（工作目录 backend，uv 环境）：

~~~
uv run python -m pytest -q tests/test_t65_generation_cycle.py tests/test_t66_replay_drift.py tests/test_t67_generation_quota.py tests/test_t68_recovery.py tests/test_t69_local_closure.py tests/test_t70_r2_gate.py tests/test_v2_t09_confirmation.py
~~~

预期：48 passed。全量按非 v2／v2 分开运行，合计 586 passed，无失败、跳过或 unknown。

真实浏览器、真实 HTTP、临时 SQLite、完整 App：

~~~
node D:/Desktop/新建文件夹/.scratch/t74-browser/run_browser.mjs
~~~

预期：ok true，17 项检查通过；确认前无模型调用，确认只发一个 confirm_requirements POST，生成链 visual_submit=1、visual_poll=1，1440 两列、768／320 单列可操作。结果写入 .scratch/t74-browser/browser-result.json。

## 6. 排障

| 现象 | 检查 | 处置 |
| --- | --- | --- |
| 看不到确认按钮 | 当前 confirmationState 是否为 pending；最终卡是否已独立批准；是否有 issue 或 stale | 继续完善或等待审查；stale 时修改／重新整理后重新确认；不要调用旧生成接口 |
| 确认按钮禁用 | title 的 actionDisabledReason；是否勾选“确认需求卡”；是否有必填文字 | 按提示补齐；不要绕过前端直接重复 POST |
| 点击确认后仍停在 U1 | 查看 POST 响应和 errorCode；是否 INTENT_REPLAY_CONFLICT／INTENT_STATE_INVALID／限额错误 | 按错误提示修改需求或查看用量；不要重复点击或刷新强行生成 |
| 进度停在“编制提示词” | 查看 workflow 的 stage／status／issue；服务端是否被 PROMPT_REQUIRES_RECONFIRMATION 或其他依赖阻断 | 后端日志有明确阻断原因时按提示处理；不要用前端计时判断失败 |
| 进度显示“结果未确认” | generation_job 是否 unknown；是否存在 RESULT_RETRIEVAL_PENDING 或 VISUAL_OUTCOME_UNKNOWN | 使用“核实原任务”或“找回原结果”；保留一次计数和槽位 |
| 提示词详情为空 | prompt_plan 是否 approved 且有 payload；是否仍为候选 | 候选不展示是预期行为；等待独立审查，不要提前确认 |
| 刷新后自动出现模型调用 | 检查浏览器网络和 /__smoke/stats（若为烟测环境） | 正常应用不应自动调用；检查是否有旧页面脚本或外部重试；不要删除审计 |
| 768 宽度出现横向滚动 | 区分 U2 workbench 宽度和全局外壳 document 宽度 | U2 工作台在 768 为 649px、控件在视口内；全局外壳问题记入 Release 门禁，不要用 overflow 隐藏内容 |
| 会话过期或切换账号 | 是否 401／AUTH_REQUIRED；当前 session_id 是否变化 | 按 R1 重新登录；页面会清除旧账号私有状态，不自动继续原任务 |

## 7. 回滚和数据边界

- 本票没有数据库迁移，迁移头仍为 0010；回滚只需回退代码提交 f8f074a 的前端与 dispatch 元数据改动，不涉及表结构。
- 如果只回退前端而保留 dispatch.requires_confirmation=True，旧页面仍能显示确认按钮；如果回退 dispatch 而保留新前端，确认框可能不再要求勾选，因此两者应一起回退。
- 本票没有写入 backend/data 或 main/backend/data；浏览器和测试只使用 .scratch 下的临时目录、临时 SQLite 和临时 Edge 用户目录。
- 不得删除审计、会话、意图、次数、槽位或恢复记录来“修复”状态；真实未知必须保留，按 #68／#69 的核实、找回、结案和新任务规则处理。
- 生产回滚前先按 #54 备份流程完成服务停止、来源确认和 manifest 核验；本说明不授权直接操作真实数据。

## 8. 关联文档和证据

- 验收记录：docs/acceptance/r3-u2-authorization.md。
- R2 生成周期：docs/acceptance/r2-generation-cycle.md。
- R2 恢复和重启：docs/acceptance/r2-recovery-restart.md。
- R2 本地结案和新任务：docs/acceptance/r2-local-closure.md。
- R2 门禁：docs/acceptance/r2-gate.md。
- R3 U1 工作台：docs/acceptance/r3-u1-workbench.md。
- 浏览器证据：.scratch/t74-browser/browser-result.json、server.log；测试日志：.scratch/t74-evidence/。