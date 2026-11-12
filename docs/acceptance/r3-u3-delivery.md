# R3 U3 当前交付、历史预览与反馈新版本验收记录

- Issue: #75
- 父 Issue: #52
- 分支: ticket/75
- 基线: integration/v3 cdbb3b4
- 代码提交: 1b6f59a52b66d08052ac4a130a6faf131a76b62f
- 验收日期: 2026-11-01
- 状态: 通过（R3 U3 阶段条件；U1／U2 见 #73／#74，R3 门禁见 #77）

## 1. 结论与边界

本票把 U3 当前交付、历史预览和反馈新版本接到同一个工作台：只有当前项目版本上、当前发布记录指向且通过内容门禁的已发布版本才算当前交付；用户可以在原位查看、下载当前交付，切换历史预览，或基于当前交付反馈一个新版本。历史预览只读，反馈会回到 U1 重新确认并生成新版本，旧版本不会被覆盖。

1. U3 不是一个新路由或新页面，而是 describe 工作台在同一位置呈现的第三个用户状态。当前交付合法时显示当前版本、尺寸、图片和“下载当前交付”；当前交付被撤销、过期、文件缺失或内容门禁拒绝时只显示真实阻断状态，不展示图片或下载入口，历史记录仍按真实状态保留。旧四页仍保留到 #77，显式旧路由行为不变。
2. 当前版本由服务端事实共同决定，不能只看是否有图片文件或历史成功记录。currentPublishedVersion 要求工作流 status=completed、项目 revision 不落后于视图、delivery.status=published，并且 versions 中存在 run_id 与当前运行一致、status=published、can_download=true、asset_id 与 delivery.asset_id 一致且有 filename 的版本；任一条件不满足都不能进入 U3 的合法交付状态。
3. 内容门禁仍在服务端。历史版本列表只列出 status=published 且 can_download=true 的版本；can_access_asset 对生成图继续检查图片 Approval、asset_id／asset_digest、交付清单 Approval 和版本 revision。拒绝、撤销、过期或缺失时，前端得到的是不可下载的版本或空 asset_id，不会凭旧 URL、静态路径或供应商成功响应当作当前交付。
4. 历史选择只在浏览器本地改变预览，不写业务数据、不修改需求 revision、不改变确认状态、不调用模型。历史版本显式标注“历史预览”和“不代表当前需求状态”；当前交付本身始终按当前版本显示。页面没有把历史预览当成当前结果，也没有覆盖旧图片或旧快照。
5. 反馈入口只在当前合法交付存在且服务端返回 request_new_version 时出现。反馈文本是必填的；提交后服务端把项目 revision 加一、重置确认和目标需求版本、创建新的 S03 工作流，并保留旧交付、旧图片和旧版本快照，因此页面回到 U1 重新整理需求，而不是在原图上承诺局部像素修改。
6. 反馈会明确写明：基于当前第 N 版，重新确认需求并生成一个新版本，不承诺局部像素不变或只修改指定区域；每次生成仍仅一张图并使用固定 3840×2160 尺寸。页面不提供“局部编辑”“只改指定区域”等能力承诺。
7. 本票修复了 3840×2160 默认尺寸的配置判定缺陷。此前 config_status 只接受方形尺寸，导致默认纵向尺寸在真实 App 中被误报为不可用；现在改为接受两个正整数像素边，拒绝 0、空格和 Unicode × 等非法写法，并继续由适配器能力交集决定最终可用尺寸。这是 #75 验收默认尺寸所必需的生产修复。
8. 没有新增 HTTP 路由、数据库迁移或后台任务；迁移头仍为 0010。前端只改工作台投影、当前交付／历史／反馈区块和样式，后端只改配置状态判定；权限、会话、审计、内容门禁仍由既有 R1／R2 代码执行。
9. 本票的验收边界是临时 SQLite、完整 App、真实 HTTP 和 headless Edge，加上 fake LLM／Visual adapter；不访问外部模型，不读取或写入 backend/data 或 main/backend/data。真实浏览器只证明页面状态、请求数量、键盘操作和三视口布局，不证明供应商图像质量或真实计费。
10. 本票只对 #75 承接的 V3-AC-18、23、27、28、29、38 给出 U3 阶段条件结论。三状态最终收敛、旧路由收敛和 Release 门禁仍由 #77、#85 复核；不能把本票结论提前当作跨阶段全部通过。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t75 |
| 分支 | ticket/75 |
| 基线 | integration/v3 cdbb3b4 |
| 代码提交 | 1b6f59a52b66d08052ac4a130a6faf131a76b62f |
| Node.js | v26.12.1 |
| 前端测试 | Vitest 5.0.5 |
| 前端构建 | Vite 8.3.5 |
| 后端 | Python 3.12.13（backend/.venv，uv） |
| 数据库 | 临时 SQLite，alembic 0010，26 张表 |
| 真实浏览器 | Microsoft Edge，C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe，headless + CDP |
| 模型适配 | LLMStub／VisualStub（fake adapter，不访问外部模型） |
| 数据边界 | 不读取或写入 backend/data、main/backend/data |

## 3. 实现摘要

### 3.1 当前交付与 U3 投影

修改 main/src/workflow/projection.ts：

- 新增 currentPublishedVersion(project, view)：只在 view.status=completed、项目 revision 不落后、delivery.status=published 时，查找与当前 run_id、delivery.asset_id 和 filename 完全一致、status=published 且 can_download=true 的 WorkflowVersion；找不到时返回 null。
- 新增 currentDeliveryBlocked(project, view)：当工作流已完成、项目 revision 已在视图内且 delivery 为 published，但 currentPublishedVersion 为空时返回 true；用于区分“有历史交付但当前交付不可用”和“还没有交付”。
- userStateOf 现在只在合法当前版本存在时返回 u3；当前交付被阻断时回到 u1；意图 accepted／executing 或确认 pending／confirmed 时返回 u2；其余为 u1。这样 U3 不依赖独立页面，也不会把旧成功图或磁盘残留当作当前交付。
- nextStepForWorkflow 增加“当前交付已发布”和“当前交付不可用”两个真实分支；前者可以提供 request_new_version 的下一步，后者明确说明撤销、过期、缺失或内容门禁原因，且不提供伪造的下载动作。

### 3.2 当前交付与阻断状态

修改 main/src/components/workflow/WorkflowPanel.vue：

- 当前交付区块只在 delivery 存在且工作流覆盖当前项目 revision 时显示。合法分支显示当前版本 filename、当前第 N 版、尺寸、交付图片和“下载当前交付”；下载地址使用版本 asset_id 和 download=true，不拼静态目录路径。
- 阻断分支显示“交付：filename”和“当前交付不可用：当前版本图片已撤销、过期、文件缺失或未通过内容门禁，不能预览或下载。历史记录仍按真实状态保留。”，不渲染图片和下载链接；已撤销或无发布时也分别显示对应真实提示。
- imageStatus 把合法发布显示为“已发布”，被阻断显示为“交付不可用”，否则显示“历史交付”或真实 generation job 状态；不会把旧 success 当成当前状态。

### 3.3 历史版本选择

- publishedVersions 只取 status=published 且 can_download=true 的 WorkflowVersion，按 requirement_revision 降序排列；被撤销、被拒绝、过期或缺少审批的版本不会进入可下载列表。
- 多于一个版本时显示“切换历史版本”标签按钮，每个按钮标明第 N 版，当前选择使用 aria-pressed；默认优先当前合法版本，否则选择最早可见的已发布版本。
- 选择历史版本只更新本地 selectedHistoryRunId，不发起 POST，不修改项目或 workflow 数据；历史图片使用“第 N 版历史预览”作为 alt，并显示“历史预览 · 不代表当前需求状态”和“这不会修改当前需求、确认或调用模型”的说明。
- 历史版本仍显示版本需求摘要、已批准提示词、尺寸快照和下载入口；旧图片、旧文件名和旧尺寸不会被当前生成覆盖。

### 3.4 反馈新版本

- feedbackAction 只在 currentVersion 存在且服务端 available_actions 返回 request_new_version 时出现；它不进入普通动作列表，而是在“基于当前交付继续调整”区块中作为唯一主操作。
- feedbackContext 明确写出当前交付第 N 版、filename 和尺寸，并说明反馈会重新确认需求、生成一个新版本，不承诺局部像素不变或只修改指定区域，每次仍仅一张图和固定尺寸。
- 反馈文本必填；提交沿用服务端 request_new_version 动作，带 expected_revision 和文本内容。成功后服务端递增项目 revision、重置 brief_status、创建新 S03 运行并撤销旧的 requirements_card／generation_prompt 审批；旧交付、旧图片和旧版本快照保持不可变。页面刷新后回到 U1 整理需求，不跳转到旧确认页。
- 选择历史版本不会改变反馈的当前交付基准：反馈始终明确基于 currentVersion，而不是用户正在查看的历史预览；历史预览只影响只读显示。

### 3.5 服务端内容门禁和版本快照

- 现有 workflow access 逻辑继续对生成图要求有效图片 Approval、asset_id／asset_digest 匹配、交付清单 Approval 和对应版本 revision；因此当前资产被置为 rejected 时，workflow 仍可显示 delivery 事实，但 versions 的 can_download=false、asset_id=null、filename=null，直接访问资产返回 409 ASSET_REJECTED。
- 新增 backend/tests/test_t75_delivery_history.py 覆盖：当前唯一合法版本存在且可下载；预览和下载字节一致；重复 GET 不增加模型／视觉调用；资产 rejected 后当前交付不可下载且历史快照仍在；恢复 approved 后重新可下载；request_new_version 后项目 revision 加一、创建新的 S03 运行、旧版本快照和旧下载字节完全不变、没有额外模型调用。
- request_new_version 不提供局部编辑或像素级保留承诺；服务端只创建新版本流程，旧发布交付不删除、不覆盖。

### 3.6 3840×2160 尺寸判定修复

修改 main/backend/app/services/config_status.py：

- _SQUARE_SIZE 改为 _SIZE_PAIR，_is_square_size 改为 _is_valid_size；合法条件是 width 和 height 都是大于 0 的整数像素，不再要求两边相等。
- 配置问题码从 IMAGE_SIZE_NOT_SQUARE 改为 IMAGE_SIZE_NOT_USABLE，消息为“当前尺寸不是合法像素尺寸，不能作为生图输出规格”。默认 VISUAL_IMAGE_SIZE=3840x2160 因而是合法输出尺寸，只有 0 边、空格、Unicode × 等非法形式被阻断。
- 新增 backend/tests/test_t75_image_size_gate.py 两个测试锁定 3840×2160 可用和非法写法不可用；既有 openai images adapter／capabilities 测试继续通过。

### 3.7 前端测试覆盖

新增 tests/t75-u3-delivery.spec.ts，6 个测试：

1. 只有当前版本的合法发布交付进入 U3，并给出图片和“下载当前交付”入口。
2. 已发布但当前版本 can_download=false 时不展示图片或下载，只显示真实阻断说明。
3. 切换历史版本只改变本地预览，不修改需求、确认或调用模型。
4. 反馈明确基于当前版本并创建新版本，成功后回到 U1 整理需求。
5. 320 像素窄屏保留历史切换和反馈操作，键盘可达且不额外写请求。
6. projection 只认定与当前版本、当前交付一致的已发布版本。

同时调整 tests/t73-u1-workbench.spec.ts、tests/v2-t13-delivery.spec.ts、tests/v2-t44-version-history.spec.ts 的夹具：旧页面测试需要显式构造与当前流程版本一致的合法版本，或明确停留在旧路由；没有删除既有有效断言。

### 3.8 后端与浏览器证据范围

- 后端全量 589 passed，其中新增 test_t75_delivery_history.py 1 个测试、test_t75_image_size_gate.py 2 个测试。
- 浏览器烟测用临时 SQLite、完整 App、真实 HTTP 和 headless Edge；fake LLMStub／VisualStub 只用于可控地完成 S07–S10，不访问外部模型。烟测覆盖当前交付图片／下载、历史双版本切换、内容门禁拒绝与恢复、反馈新版本和 1440／768／320 布局。
- 真实 HTTP 统计只有一次生成链：visual_submit=1、visual_poll=1；历史选择和反馈不触发模型调用，反馈后旧版本仍保留。

## 4. 执行命令与结果

### 4.1 前端定向测试

工作目录：D:/Desktop/新建文件夹/.worktrees/t75。

~~~
npm test -- --run tests/t75-u3-delivery.spec.ts --reporter=dot
~~~

结果：1 个测试文件、6 个测试通过，3.20s，exit 0。

### 4.2 前端全量、类型检查和构建

~~~
npm test -- --run --reporter=dot
npm run typecheck
npm run build
~~~

结果：

- Vitest：43 个文件、248 个测试通过，27.04s，exit 0；日志 .scratch/t75-evidence/t75-frontend-full.log。仅有既有 fireEvent.change／fireEvent.input 提示，无失败。
- vue-tsc --noEmit：exit 0；日志 .scratch/t75-evidence/t75-typecheck.log。
- Vite 8.3.5：1667 modules，790ms；dist/index.html 0.47 kB，dist/assets/index-CktPRk0b.css 417.80 kB，dist/assets/index-D7wG20_9.js 1,205.80 kB；只有 chunk-size 警告，exit 0；日志 .scratch/t75-evidence/t75-build.log。

### 4.3 后端定向和全量回归

工作目录：D:/Desktop/新建文件夹/.worktrees/t75/backend，Python 3.12.13，uv 虚拟环境。

~~~
uv run python -m pytest -q tests/test_t75_delivery_history.py
uv run python -m pytest -q tests/test_t75_image_size_gate.py
uv run python -m pytest -q tests/test_openai_images_adapter.py tests/test_v2_t02_capabilities.py
~~~

结果：test_t75_delivery_history.py 1 passed，1.79s；test_t75_image_size_gate.py 2 passed，0.05s；既有配置与适配器定向 18 passed，6.04s。

全量按非 v2 与 v2 分开运行：

| 范围 | 结果 | 日志 |
| --- | --- | --- |
| 非 v2 全部测试（39 个 test_*.py，排除 test_v2_*） | 354 passed, 2 warnings, 401.41s | .scratch/t75-evidence/t75-backend-nonv2.log |
| 继承 v2 全部测试（19 个 test_v2_*.py） | 235 passed, 315.06s | .scratch/t75-evidence/t75-backend-v2.log |
| 合计 | 589 passed，无失败、跳过或 unknown |  |

仅有的 warning 是 backend/tests/support/auth.py:42 的 DeprecationWarning（设置 per-request cookies），不是本票引入的失败。

### 4.4 迁移与临时数据库边界

本票没有迁移，迁移头仍为 0010，0001–0010 未改。浏览器验收使用临时数据库 .scratch/t75-browser/data/app.db，实测 alembic_version=0010，26 张表，PRAGMA integrity_check=ok。没有打开或修改 backend/data、main/backend/data。

### 4.5 真实浏览器、真实 HTTP、临时 SQLite、完整 App

先构建最终前端，再执行：

~~~
node D:/Desktop/新建文件夹/.scratch/t75-browser/run_browser.mjs
~~~

最终结果：{"ok":true,"checks":30,"base_url":"http://127.0.0.1:9937"}；完整结果 .scratch/t75-browser/browser-result.json，服务日志 .scratch/t75-browser/server.log，运行日志 .scratch/t75-evidence/t75-browser.log。

30 项检查覆盖：

1. 最终需求卡原位只有一个确认入口，路径仍为 /?project=...，不跳旧确认页。
2. 未勾选时按钮禁用，usage 显示真实受理次数和个人／实例／项目占用。
3. 空格键勾选确认框后按钮启用，键盘焦点可达主操作。
4. 显式确认前 LLM started／roles 为空、Visual submit／poll 为 0，没有提前调用模型。
5. 一次确认只发一个 POST /workflow/actions/confirm_requirements，没有 POST /generations，路由和 project 查询保持不变。
6. 执行进度四步、无虚构百分比；后台完成后准备交付仍等待人工批准和发布。
7. 已批准执行提示词默认折叠，只有用户展开后才显示；真实生成链 visual_submit=1、visual_poll=1，模型角色按 S07–S10 各调用一次。
8. 当前交付显示合法图片、当前版本 filename、尺寸 3840×2160 和“下载当前交付”，下载 href 带 download=true。
9. 种子历史版本使历史区出现两个已发布版本标签；切换“第 1 版”只改变本地预览，历史说明明确“不会修改当前需求、确认或调用模型”，当前交付图片 src 不变。
10. 历史选择期间没有 mutating POST，stats 不变。
11. 把当前资产置为 rejected 后，当前交付显示“当前交付不可用”，不显示图片和下载；历史版本仍显示；恢复 approved 后重新可下载。
12. 内容门禁检查没有 mutating POST。
13. 反馈文本必填；提交只发一个 request_new_version POST，没有 POST /generations；路径仍为 /?project=...，旧版本仍在，反馈区消失，页面回到 U1 的“整理需求”。
14. 反馈前后 stats 相同，证明页面动作没有新增模型调用。
15. 1440px：两列并排，workbench 1072px，列宽 616.39px／455.59px，document 1440/1440，无横向溢出。
16. 768px：单列，workbench 649px，workbench scrollWidth=clientWidth=649，所有可见控件都在 768px 内；document 宽度 1020px 来自继承的全局外壳，不是 U3 工作台溢出（见残余风险）。
17. 320px：单列，workbench 273px，document 320/320，历史标签和反馈按钮在视口内。
18. 临时数据库 alembic 0010、26 表、integrity_check=ok。
19. 真实 HTTP 服务只使用临时目录，未访问真实数据根。
20. 反馈新版本创建新的 S03 工作流，旧交付和旧版本快照保持不变。
21. 历史预览不改变确认按钮、最终需求卡或当前需求摘要。
22. 当前交付下载和预览使用同一合法 asset_id，没有静态路径访问。
23. 交付发布后仍需人工批准，未自动发布。
24. 当前版本阻断时没有伪造下载或未审图预览。
25. 旧历史图片带有“历史预览 · 不代表当前需求状态”标签。
26. 视图刷新后状态来自服务端，不依赖本地缓存。
27. 页面没有把历史请求或轮询变成模型调用。
28. 320／768／1440 的主操作没有横向溢出。
29. 反馈后旧版本仍可下载，未被新版本覆盖。
30. 真实浏览器脚本以 headless Edge、临时用户目录运行，结束时关闭服务和临时资源。

浏览器自动化使用 C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe，headless + remote-debugging-port + 临时用户目录；后端为真实 uvicorn、临时 SQLite、完整 App，LLM／Visual 为 fake adapter，不访问外部模型或真实数据。

## 5. 验收覆盖表

| AC | 本票 U3 阶段条件 | 证据 |
| --- | --- | --- |
| V3-AC-18 | 当前交付被撤销、拒绝、过期或缺失时不能靠 locator／静态历史路径绕过内容门禁；历史预览仍按真实状态保留 | t75-u3-delivery 测试 2；test_t75_delivery_history rejected／restore 断言；浏览器检查 11／12／24；#61 历史恢复门禁全量回归 |
| V3-AC-23 | 当前交付、历史预览和反馈新版本三状态在原位衔接；旧图明确标注历史，不覆盖旧版本；没有重复确认或强制四页路径 | t75-u3-delivery 测试 1／3／4；浏览器检查 6／8／9／10／13／14；#73／#74 全量回归；旧路由保留到 #77 |
| V3-AC-27 | 切换历史、刷新、导航和重新登录不额外调用模型；原任务合法执行不被误判 | t75-u3-delivery 测试 3；浏览器检查 9／10／14；后端 test_t75_delivery_history 重复 GET 无额外调用 |
| V3-AC-28 | 当前发布不可用、失败或未知时保持真实状态，不自动重生、不假发布、不展示未审图预览 | t75-u3-delivery 测试 2；浏览器检查 11／12／24；WorkflowPanel 阻断分支；#68 恢复回归 |
| V3-AC-29 | 单图、固定 3840×2160、历史尺寸快照不变；反馈创建新版本不覆盖旧版本 | test_t75_image_size_gate 2 个测试；test_t75_delivery_history 版本快照断言；浏览器检查 8／9／18／29；config_status 修复 |
| V3-AC-38 | 320／768／1440 三视口可用；历史切换、反馈、键盘焦点和主操作无横向溢出 | 浏览器检查 15／16／17／28；t75-u3-delivery 测试 5；组件键盘测试；实际设备边界见残余风险 |

## 6. 证据文件

- 代码提交：1b6f59a52b66d08052ac4a130a6faf131a76b62f。
- 前端定向／全量／类型／构建：.scratch/t75-evidence/t75-spec.log、t75-frontend-full.log、t75-typecheck.log、t75-build.log。
- 后端定向／全量：.scratch/t75-evidence/t75-delivery-history.log、t75-image-size-gate.log、t75-backend-nonv2.log、t75-backend-v2.log。
- 浏览器：.scratch/t75-browser/run_browser.mjs、serve.py、setup.py、seed75.py、browser-result.json、server.log、data/app.db；运行日志 .scratch/t75-evidence/t75-browser.log。
- 迁移检查：临时库 alembic 0010、26 表、integrity_check=ok。
- 构建产物：D:/Desktop/新建文件夹/.worktrees/t75/dist（Vite 生成，不在 Git 提交中）。

## 7. 残余风险

1. 真实浏览器只在本机 Edge headless 上执行 320／768／1440 计算布局和键盘操作，不是物理手机、其他浏览器或辅助技术；Release #85 若要求指定真实设备，必须重新补证。
2. 768px 时 U3 workbench 本身为 649px 且所有控件都在视口内，但继承的全局页面外壳使 document.scrollWidth=1020px。该外壳不属于 U3，本票没有用 overflow:hidden 掩盖内容；Release 门禁需要决定是否把全局外壳纳入发布门槛。
3. LLM／Visual 是 fake adapter，证明的是应用的内容门禁、版本快照、请求数量和交互边界，不是供应商真实生成质量、延迟、计费或保留策略。
4. 浏览器历史版本是测试夹具通过临时种子创建的合成版本；其 requirement_revision 与当前版本相同，因此两个标签都显示“第 1 版”。这证明历史按钮和本地切换行为，不代表生产数据一定出现相同 revision 组合；生产版本由服务端 request_new_version 重新编号。
5. 内容门禁的拒绝／恢复使用临时数据库直接改资产状态，不是真实供应商撤回；真实远端撤销、过期和文件缺失仍以 R1／R2 的服务端门禁和资产检查为准。
6. 本票没有新增远端取消能力；反馈新版本不会删除旧交付，也不会声称旧任务在远端已取消。真实未知仍按 #68／#69 的核实、找回、结案和新任务规则处理。
7. 未读取或写入 backend/data、main/backend/data；临时数据库、临时用户目录和 fake adapter 均在 .scratch 下。
