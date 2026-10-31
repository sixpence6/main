# R3 U1 连续需求工作台运维说明

- Issue: #73
- 父 Issue: #52
- 分支: ticket/73
- 代码提交: f8779b6effc4457c421f8bc16f8c268c518ec123
- 验收日期: 2026-11-01
- 状态: 已交付（R3 U1 阶段）

## 1. 目标与边界

本说明面向维护者和本地运维，解释 U1 连续需求工作台的页面结构、使用方式、响应式行为和排障方法。

U1 是“描述／完善当前需求”的状态：用户在同一个工作台里输入商品与用途、添加参考图、查看当前需求卡和下一步。需求卡通过独立审查后才出现确认按钮；确认、生成和正式发布仍是服务端流程，不因前端布局改变而放宽。

本票只交付 U1。旧 /describe、/confirm、/generate、/results 页面在本票中继续保留，U2、U3 的原位衔接、导航收敛和 R3 总门禁分别由 #74、#75、#77 处理。不要把本说明当作四页已经合并的发布说明。

## 2. 页面结构

### 2.1 工作台

工作台 section 的 aria-label 是“需求工作台”，内部有两个列容器：

1. u1-conversation：ConversationPanel，包含商品描述 textarea、发送按钮、参考图选择／替换／移除。
2. u1-requirement：WorkflowPanel（阶段、当前问题与下一步、当前需求卡、确认动作、生成状态、历史版本）和 HistoryPreview。

桌面宽度为 1023px 以上时两列并排，左列略宽；1023px 及以下单列，沟通在上、需求在下。DOM 顺序就是键盘阅读顺序。

### 2.2 当前问题与下一步

WorkflowPanel 顶部的 role=status 区域显示三类信息：

- 当前问题标题；
- 一句可执行说明；
- 如果服务端返回了可用动作，再显示“下一步操作：动作名”。

动作名来自服务端 available_actions，不从前端猜测。没有项目、没有流程、审查中、待确认、处理中、阻塞、已发布和 stale 各有独立文案。阻塞时原始 issue.message 只保留在流程提示中一次，不在状态块重复。

### 2.3 需求卡

只有 status=approved 且 payload 存在的 final_brief 会显示完整字段。candidate、reviewing、无 payload 的最终卡只显示“需求卡内容正在审查，通过后会在此完整展示，不能提前确认。”未批准内容不会出现在 DOM 可读文本里。

确认按钮只在 pending 状态出现，文案在有 usage 时是“确认以上需求并生成图片”，没有 usage 的 v2 兼容场景保持“确认需求”。同一需求卡不会渲染第二个确认入口。

### 2.4 历史预览

HistoryPreview 只列：

- generation.status=succeeded；
- result_asset 存在且 availability=available。

按 version_no 倒序，最多六张。标题是“历史图片预览”，每张图有“第 N 次生成 · 历史预览，不代表当前需求状态”。版本列表中的旧图也有“历史预览 · 不代表当前需求状态”。查看历史不发请求、不改变当前 revision、不调用模型。

## 3. 使用步骤

1. 登录后进入自己的项目工作台。首次输入前先阅读模型数据流说明：文字、需求卡和参考图可能发给已配置的外部模型；不要填写密码、会话凭证、恢复链接或秘密；应用不承诺供应商零保留。
2. 在左列输入商品、用途、风格和文字要求。发送前文字只在当前页面内存；刷新或离开页面会丢失，这是有意行为，不写入浏览器存储。
3. 需要参考图时使用“选择参考图”。上传／替换／移除使用服务端返回的项目 revision；成功后才更新当前参考图，失败保留旧图并显示错误。
4. 发送后右列显示阶段与“当前问题与下一步”。如果正在整理或独立审查，等待；如果审查未通过，不能确认；如果出现阻塞，按服务端提供的动作处理，不自行重试模型。
5. 需求卡通过审查后核对字段，勾选必要确认并点击唯一的确认按钮。确认后显示受理、生成、审查、恢复或交付状态；重复点击不会再次计数。
6. 旧生成成功后可在“历史图片预览”查看旧版本。历史图只作参考，不代表当前需求状态；要基于新需求生成时，从当前需求卡重新确认。
7. 窄屏下两列自动堆叠。键盘用户从描述输入开始，Tab 到参考图按钮、发送、需求动作；中文输入法组合期间按回车不会误提交。

## 4. 本地验证

### 4.1 前端定向

工作树根：

~~~
npm test -- --run tests/t73-u1-workbench.spec.ts
~~~

期望：1 个文件、7 个测试通过，约 4s。日志 .scratch/t73-evidence/t73-spec.log。

### 4.2 前端全量与构建

~~~
npm test -- --run --reporter=dot
npm run typecheck
npm run build
~~~

验收记录中的结果：41 个文件、234 个测试通过，22.51s；vue-tsc exit 0；Vite 1667 modules，684ms，只有 chunk-size 警告。

### 4.3 浏览器检查

真实 uvicorn、临时 SQLite、完整 App、构建产物、fake LLM／Visual：

~~~
node D:/Desktop/新建文件夹/.scratch/t73-browser/run_browser.mjs
~~~

期望：status 0，ok=true，14 checks。结果在 .scratch/t73-browser/browser-result.json，服务日志在 server.log。

三档期望：

- 1440：两列并排，左列约 616px、右列约 456px；workbench 1072px 宽，无横向溢出。
- 768：单列，649px 宽；两列上下相接；四个主操作都在视口内。
- 320：单列，273px 宽；document 320/320；主操作不出屏。

### 4.4 后端回归

本票没有后端变更，但必须保留既有回归。工作树 backend，PYTHONPATH 工作树 main/backend：

~~~
python -m pytest -q --disable-warnings tests
~~~

全量按非 v2 与 v2 分开运行，避免超过命令时限：非 v2 351 passed，v2 235 passed，合计 586 passed。不要为了缩短时间删除 v2 测试或跳过认证、归属、生成恢复测试。

## 5. 常见现象与排障

### 5.1 桌面仍是单列

检查视口 CSS 宽度是否大于 1023px。浏览器缩放、设备模拟器的 mobile 标志和滚动条会影响 CSS 宽度；在 headless Edge 中验证断点时应使用 mobile:false，否则 device metrics 可能给出 1020px 的布局宽度。也检查构建是否包含 DescribePage 的 u1-workbench 样式。

### 5.2 需求卡显示“正在审查”

这是候选或未批准状态。不要通过修改前端状态或直接调用确认接口绕过；确认按钮只在服务端返回的 pending 状态出现。查看 WorkflowPanel 的 issue 与 available_actions，按独立审查流程重试或修订需求。

### 5.3 确认按钮出现两个

正常情况下只有一个。检查是否同时挂载了 BriefCard 的旧确认入口和 WorkflowPanel；U1 工作台只挂 WorkflowPanel。旧 ConfirmPage 仍可能在旧路由中使用，属于 U2 前保留路径，不是 U1 工作台的重复入口。

### 5.4 历史区域不出现

历史只显示 succeeded 且 result_asset availability=available 的生成。failed、missing、revoked、未完成或没有 result_asset 的记录不会显示。检查项目详情里的 generations 和资产可用性；不要为了让旧图出现而放宽筛选或直接拼资产 URL。

### 5.5 未发送文字丢失

这是设计边界：未发送文字只保存在组件内存，刷新、关闭标签、切换账号或清理会话都会丢失；它不写 localStorage／sessionStorage，也不跨账号。不要通过把草稿写入 localStorage 来“改进”持久化，除非另行设计账号隔离和退出清理。

### 5.6 下一步动作和按钮不一致

下一步动作的 actionId 必须存在于服务端 available_actions；动作名由同一个 actionLabel 生成。若不显示，检查服务端是否返回了动作，或流程视图是否处于 loading／error；不要在前端硬编码动作名。

### 5.7 320px 出现横向滚动

先区分 U1 工作台和全局外壳。验收要求 workbench／main 不溢出、主操作不出屏；若 documentElement.scrollWidth 大于视口但 workbench 与 main 的 scrollWidth=clientWidth，检查外壳或全局 header 的固定宽度。不要用 overflow:hidden 掩盖真实的内容溢出。

## 6. 回滚与数据边界

- 本票无数据库迁移。回滚代码并重新构建即可回到基线页面；0010 迁移和业务数据不受影响。
- 不得为了验证布局或历史预览删除 backend/data 或 main/backend/data；一律使用临时 SQLite、临时数据目录和工作树构建产物。
- 前端构建输出到工作树 dist 或发布目录；不要覆盖 main/dist，除非发布流程明确要求并已备份。
- 历史预览使用既有受保护资产 URL；不要新增静态目录暴露或把 references、generated、published 直接映射为静态文件服务。
- 回滚后旧四页仍按既有路由工作；U1 的双列展示、下一步状态块和历史筛选会一并消失，需重新构建前端。
