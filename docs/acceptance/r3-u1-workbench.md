# R3 U1 连续需求工作台验收记录

- Issue: #73
- 父 Issue: #52
- 分支: ticket/73
- 代码提交: f8779b6effc4457c421f8bc16f8c268c518ec123
- 验收日期: 2026-11-01
- 状态: 通过（R3 的 U1 阶段条件；U2、U3 与 R3 门禁见 #74、#75、#77）

## 1. 结论与边界

本票把“描述／完善当前需求”从顺序页面改成同一页面内的连续工作台：桌面为沟通与需求卡双列，1023 CSS 像素及以下为单列；当前问题、下一步、唯一确认动作、已审查内容与历史图片都在需求侧就近呈现。

1. 工作台左侧是既有 ConversationPanel，包含商品描述输入与参考图上传；右侧是 WorkflowPanel 与 HistoryPreview。DOM 顺序就是阅读顺序，键盘 Tab 先到描述与参考图，再到需求卡和生成状态。
2. 未发送文字仍只存在页面内存。输入、窗口缩放和页面导航不会写 localStorage／sessionStorage，不会创建 GenerationIntent，也不会调用 LLM 或 Visual。
3. 需求侧新增“当前问题与下一步”状态块。它按项目、流程视图与确认状态给出当前动作：无项目时提示描述商品与用途；无流程时提示先提交；审查未通过时提示等待独立审查；待确认时指向唯一确认按钮；已受理时提示处理中；阻塞时指向服务端实际可用动作。
4. 候选参考报告和候选 final_brief 的 payload 不提前展示。只有 status=approved 且带 payload 的最终需求卡才显示完整字段；未批准时只显示“需求卡内容正在审查，通过后会在此完整展示，不能提前确认。”
5. 确认状态与原因集中到 main/src/workflow/projection.ts。none、reviewing、pending、confirmed、stale 的判断保持既有服务端事实：批准摘要、项目 revision、需求 revision 任一不一致即不能继续使用原确认；stale 时只提示重新确认，不显示重复确认入口。
6. HistoryPreview 只列 status=succeeded、result_asset.availability=available 的生成记录，按版本号倒序最多六张。标题为“历史图片预览”，每张图带“第 N 次生成 · 历史预览，不代表当前需求状态”，并明确“查看历史不会修改当前需求，也不会调用模型”。版本列表中的旧图也增加“历史预览 · 不代表当前需求状态”。
7. 首次业务输入前的模型数据流说明保持在工作台之前：文字、需求卡和参考图可能发给已配置的外部模型；不得输入密码、会话凭证、恢复链接或秘密；不承诺供应商零保留。
8. 既有路由守卫、pageshow／popstate 重新核验、sessionRevision 迟到响应丢弃、草稿页面内存和参考图服务端 revision 行为未改动。旧四页路由在本票中保留，避免在 U2／U3 完成前破坏已验收的导航与持久化行为；U1 只新增连续工作台入口，不声称四页已经合并。
9. 本票没有后端、数据库或迁移变更。迁移头仍为 0010；没有新增 HTTP 路由、模型调用、后台任务或跨账号数据出口。

本票对 #73 承接的 V3-AC-23、25、38、39 只给出 U1 阶段条件结论。三状态原位衔接的 U2／U3 部分由 #74／#75 完成，整体再由 #77 门禁复核；不能把本票结论提前当作跨阶段通过。未读取或写入 backend/data 或 main/backend/data。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t73 |
| 分支 | ticket/73 |
| 基线 | integration/v3 670bdc1 |
| 代码提交 | f8779b6effc4457c421f8bc16f8c268c518ec123 |
| 后端 Python | 3.12.13（工作树 backend/.venv） |
| Node.js | v26.12.1 |
| 前端测试 | Vitest 5.0.5 + jsdom |
| 前端构建 | Vite 8.3.5 |
| 数据库 | 临时 SQLite，alembic 0010（本票无迁移变更） |
| 模型适配 | 测试 LLMStub／VisualStub；浏览器 smoke 为 fake adapter |
| 浏览器 | Microsoft Edge headless，真实 CDP，真实 uvicorn、完整 App、构建产物 |
| 数据边界 | 未读取或写入 backend/data 与 main/backend/data |

## 3. 实现摘要

### 3.1 需求状态投影

新增 main/src/workflow/projection.ts：

- latestFinalBrief 取 revision_no 最大的 final_brief。
- confirmationStateOf 复用原 WorkflowPanel 规则：没有批准最终卡是 reviewing；有当前摘要、项目 revision 和需求 revision 一致的未撤销 requirements_card 批准是 confirmed；存在其他 requirements_card 批准是 stale；批准且待确认是 pending。
- userStateOf 提供 U1／U2／U3 的连续状态判断：当前 revision 的已发布交付是 U3；已有 accepted／executing 意图或待确认／已确认需求是 U2；其余是 U1。
- nextStepForWorkflow 输出当前问题标题、说明与可选 actionId，不自行创建动作，只引用服务端 available_actions 中真实存在的动作。

### 3.2 工作台布局

修改 main/src/pages/DescribePage.vue：

- 模型数据流提示之后新增 section.u1-workbench，aria-label 为“需求工作台”。
- 桌面 grid-template-columns 为 minmax(0, 1.15fr) 和 minmax(0, 0.85fr)，左沟通、右需求，gap 使用既有间距变量。
- 1023px 及以下切换为单列；两个 .u1-column 依次堆叠。
- 列内组件取消原来的最大宽度和自动外边距，避免嵌套面板缩窄或横向溢出。

### 3.3 当前问题与下一步

修改 main/src/components/workflow/WorkflowPanel.vue：

- 使用 projection.ts 的 latestFinalBrief、confirmationStateOf 和 nextStepForWorkflow，删除组件内重复的确认状态实现。
- 顶部新增 role=status 的“当前问题与下一步”区域；存在 actionId 时显示“下一步操作：动作名”，动作名由 actionLabel 生成，和实际按钮一致。
- 阻塞时不再重复 issue.message；问题消息仍只由原有流程提示显示一次，避免同一错误在页面上出现两遍。
- 最终需求卡仍只在 approved 且有 payload 时显示字段；确认按钮仍只在 pending 时出现，确保单一确认动作。

### 3.4 历史预览

新增 main/src/components/HistoryPreview.vue 并扩展版本列表：

- 只接受 succeeded 生成和 availability=available 的结果资产；missing、failed、无结果资产均不显示。
- 版本号倒序，最多六张，图片使用既有受保护 assetUrl。
- 历史区域和版本列表都有“历史预览 · 不代表当前需求状态”的可见说明。
- 组件只读取 projectGenerations，不发请求、不改变当前需求、不调用模型。

### 3.5 样式

修改 main/src/styles/workspace.css：

- .workflow-next-step 使用左侧主色边线和面板背景，形成就近的状态提示，不靠弹窗或新页面。
- .workflow-version-preview 固定最大宽度 180px，窄屏最大 100%。
- .history-preview 为独立面板；图片列表 auto-fill、最小 120px，图片 4:3 object-fit cover。

## 4. 执行命令与结果

### 4.1 定向测试

工作树根：

~~~
npm test -- --run tests/t73-u1-workbench.spec.ts
~~~

结果：1 个文件、7 个测试通过，4.10s；日志 .scratch/t73-evidence/t73-spec.log。覆盖：

- 双列 DOM 和左右列归属；
- 候选报告／候选最终卡不可见，未批准时没有确认按钮；
- 已批准且待确认时只有一个确认按钮，下一步文字与实际动作一致；
- 阻塞时当前问题只出现一次，下一步指向“核实原任务”；
- 历史只显示 succeeded 且 available 的版本，顺序与标签正确；
- 未发送文字留在输入框和页面内存，localStorage／sessionStorage 不含草稿，参考图替换／移除使用当前项目修订；
- 320px 仍保留两个列容器、页面内输入和零写请求；
- userStateOf 覆盖 U1、U2、U3。

### 4.2 前端全量回归与构建

~~~
npm test -- --run --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：41 个文件、234 个测试通过，22.51s；日志 .scratch/t73-evidence/t73-frontend-full.log。仅有既有 fireEvent.change／fireEvent.input 警告。
- vue-tsc --noEmit：exit 0；日志 .scratch/t73-evidence/t73-typecheck.log。
- Vite 8.3.5：1667 modules，684ms；dist/index.html 0.47 kB，dist/assets/index-DlEnCzc3.css 414.54 kB，dist/assets/index-BT4LJWy4.js 1,195.20 kB；仅 chunk-size 警告；日志 .scratch/t73-evidence/t73-build.log。

### 4.3 全量后端回归

| 范围 | 测试数 | 结果 |
| --- | --- | --- |
| 非 v2 全部测试（排除 test_v2_*） | 351 | 351 passed，2 warnings，381.10s |
| 继承 v2 全部测试 | 235 | 235 passed，297.10s |
| 合计 | 586 | 586 passed，无失败、跳过或 unknown |

工作目录为工作树 backend，解释器为工作树 backend/.venv，PYTHONPATH 指向工作树 main/backend。唯一警告是既有测试支持文件 backend/tests/support/auth.py:42 的 Cookie 弃用提示。本票无后端代码变更；此回归用于证明没有删除有效测试或破坏 R1／R2／v2 行为。

### 4.4 迁移边界

本票无迁移文件变更。基线到代码提交只改 6 个前端／测试文件：

~~~
git diff --stat 670bdc1..f8779b6
~~~

迁移头仍是 0010；数据库结构、alembic 版本和两份迁移树均未修改。

### 4.5 真实浏览器、真实 HTTP、临时 SQLite、完整 App smoke

命令：

~~~
node D:/Desktop/新建文件夹/.scratch/t73-browser/run_browser.mjs
~~~

结果：status 0，ok=true，14 个检查全部通过；结果文件 .scratch/t73-browser/browser-result.json，服务日志 .scratch/t73-browser/server.log，服务统计 .scratch/t73-browser/stats.json。

smoke 使用真实 uvicorn、真实 HTTP、临时 SQLite、完整 App、工作树构建产物，LLM／Visual 为 fake adapter；登录后进入自己的空项目工作台，不接触真实数据目录。关键测量：

| 视口 | 计算布局 | 列位置 | 横向边界 | 主操作 |
| --- | --- | --- | --- | --- |
| 1440 | grid，两条轨道 616.391px／455.594px | 左 289–905，右 905–1360，同顶 363 | workbench 1072/1072，main 1201/1201，document 1440/1440 | 选择参考图、发送、刷新、开始整理需求均在视口内 |
| 768 | grid，单轨道 649px | 第一列 381–1062，第二列 1062–1438 | workbench 649/649，main 689/689 | 四个主操作在 768 内；document 的 1020 来自工作台之外的既有外壳，不是 U1 横向溢出 |
| 320 | grid，单轨道 273px | 第一列 599–1389，第二列 1389–1915 | workbench 273/273，main 305/305，document 320/320 | 选择参考图 59–155、发送 33–87、刷新 236–272、开始整理需求 46–259，均未越界 |

交互检查：

- 描述输入框 aria-label 为“描述商品与图片需求”，点击后 activeElement 是该 TEXTAREA；焦点可见性由既有输入边框承担，脚本读取的 outlineStyle 为 none。
- 用 compositionstart、insertCompositionText、Enter keyCode 229、compositionend 模拟中文输入“陶瓷马克杯，暖色生活场景”，组合期间回车未提交；POST 数量前后都是 1（仅登录）。
- 未发送草稿没有进入 localStorage，sessionStorage 也没有草稿；导航、缩放和输入前后的 stats 为 llm_roles=[]、llm_started=[]、visual_submit=0、visual_poll=0、tasks=0、cycles=0。
- 1440 空项目没有历史结果资产，因此不出现 HistoryPreview；历史过滤与标签由定向测试覆盖。

## 5. 验收覆盖表

| AC | 本票阶段条件 | 证据 |
| --- | --- | --- |
| V3-AC-23 | U1 在单页内连续呈现沟通、参考输入与当前需求卡；当前问题与下一动作清晰；未批准报告不显示；只有一个确认入口 | 双列／单列 DOM 与浏览器布局；projection 的 reviewing／pending／stale 判断；候选 payload 隐藏测试；唯一确认按钮测试。U2／U3 原位衔接与四页合并留给 #74／#75／#77 |
| V3-AC-25 | 需求变化、摘要或 revision 不一致时不显示可继续确认；需要改需求时回到 U1 并重新确认 | confirmationStateOf 仅在批准摘要、项目 revision、需求 revision 一致时 confirmed；stale 只提示重新确认；既有 R2 服务端篡改与漂移门禁回归 586 项继续通过；本票未新增绕过入口 |
| V3-AC-38 | 320／768／1440 CSS 宽度；真实浏览器；标签、焦点、键盘、中文输入、状态可用；表单和主操作不横向溢出 | 真实 Edge CDP 14 项；三档计算布局与按钮边界；TEXTAREA 焦点；中文组合输入不提交；定向测试 320px；全量 41 文件／234 测试 |
| V3-AC-39 | 首次业务输入前说明外部模型数据流；无密码／会话／恢复秘密；保持 v2 持久化；不声称零保留 | 既有 model-data-notice 保留在工作台前；浏览器 localStorage 为空、sessionStorage 无草稿；未发送文字仅页内存；本票没有新增模型调用；全量 v2 235 项回归继续通过 |

## 6. 证据文件

- 代码提交：f8779b6effc4457c421f8bc16f8c268c518ec123。
- 定向测试：.scratch/t73-evidence/t73-spec.log（1 file/7 tests）。
- 前端全量：.scratch/t73-evidence/t73-frontend-full.log（41 files/234 tests）。
- 类型检查：.scratch/t73-evidence/t73-typecheck.log（exit 0）。
- 构建：.scratch/t73-evidence/t73-build.log（Vite 8.3.5）。
- 浏览器 smoke：.scratch/t73-browser/browser-result.json（14 checks）、server.log、stats.json、run_browser.mjs。
- 测试文件：tests/t73-u1-workbench.spec.ts（7 tests）。
- 生产文件：main/src/workflow/projection.ts、main/src/components/HistoryPreview.vue、main/src/components/workflow/WorkflowPanel.vue、main/src/pages/DescribePage.vue、main/src/styles/workspace.css。

## 7. 残余风险

- 旧四页路由仍存在，U2、U3 还没有在原位完成替换。本票只证明 U1 连续工作台可用，不能把“三状态原位衔接”或“没有四个必经页面”整体记为已通过；这些必须由 #74、#75 和 #77 继续补证。
- 浏览器 smoke 使用 fake LLM／Visual 和空项目，不以真实供应商费用、远端幂等或结果保留作为证据；本票路径没有新增模型调用，所以这部分不是本票门槛。
- 768px 下 documentElement.scrollWidth 为 1020，定位到 U1 工作台之外的外壳；本票确认 workbench 与 main 没有横向溢出、主操作在视口内。若后续调整全局外壳，应重新跑三档浏览器检查。
- 历史预览只证明筛选与标签，不证明受保护资产在真实生成链中的字节权限；该边界由 R1 历史／字节门禁和 R2 交付回归继续覆盖。
- 本票没有改变服务端确认、配额、归属或恢复逻辑；UI 的下一步只引用 available_actions，不自行生成动作。服务端门禁仍是唯一授权依据。
