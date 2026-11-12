# R3 U3 当前交付、历史预览与反馈新版本运维说明

- Issue: #75
- 父 Issue: #52
- 分支: ticket/75
- 代码提交: 1b6f59a52b66d08052ac4a130a6faf131a76b62f
- 基线: integration/v3 cdbb3b4
- 更新日期: 2026-11-01
- 状态: 可用（R3 U3 阶段；完整发布边界见 #77／#85）

## 1. 目的与边界

本说明供本地单实例部署的维护者使用，说明 U3 当前交付、历史预览和反馈新版本在浏览器中的行为。它不替代 R1 的账号与会话操作，也不替代 R2 的受理、计数、槽位和远端未知处置规则。

适用边界：

- 本地或受控单实例部署，账号由管理员创建，没有公开注册、SMTP、MFA 或团队共享。
- 一次生成只产生一张图片，默认尺寸为 3840×2160；批量、多尺寸和精确局部编辑不在本期。
- U3 只在当前项目版本上存在合法已发布交付时成立。旧成功图片、磁盘文件或供应商响应本身不等于当前成功。
- 历史版本只读；选择历史不会修改需求、确认或触发模型。反馈会重新确认需求并创建新版本，不承诺局部像素不变。
- 结案不声称远端已取消；本页没有远端取消能力。
- 不读取或写入 backend/data、main/backend/data 的真实数据。任何真实数据操作都需要维护者明确选择来源并完成已核验的备份。

## 2. 用户操作路径

1. 登录后打开项目。项目默认进入 describe 工作台，不会自动跳到旧的确认、生成或结果页面；U3 交付状态在同一工作台显示。
2. 当当前项目版本存在合法已发布交付时，页面显示“当前交付”、当前版本 filename、当前第 N 版、尺寸和“下载当前交付”。点击下载使用服务端资产链接，不拼静态目录路径。
3. 需要查看旧版本时，在“历史版本”区点击“第 N 版”标签。切换只改变本地预览；历史图下方始终标注“历史预览 · 不代表当前需求状态”，页面不会修改当前需求或确认。
4. 基于当前交付调整时，在“基于当前交付继续调整”区填写说明，点击“基于反馈生成新版本”。页面明确当前交付是第 N 版，提交后回到 U1 的需求整理状态，需要重新确认新需求并另行生成。
5. 旧交付、旧图片和旧版本快照不会被新版本覆盖；新版本完成后仍按当前版本重新判断 U3。
6. 如果当前交付显示“当前交付不可用”，不要尝试历史静态路径、直接文件 URL 或重复点击下载；先按第 4 节检查内容门禁和资产状态。
7. 页面导航、刷新、重新登录和历史选择都不应额外调用模型；反馈提交只创建新版本流程，不调用旧 /generations 接口。

## 3. 页面状态和按钮

### 3.1 当前交付

| 服务端状态 | 页面行为 |
| --- | --- |
| completed，delivery.status=published，当前 run 的版本 can_download=true 且 asset_id／filename 与交付一致 | 显示“当前交付：filename”、当前第 N 版、尺寸、交付图片和“下载当前交付” |
| completed，delivery.status=published，但当前版本 can_download=false、asset_id 为空或版本不匹配 | 显示“当前交付不可用：当前版本图片已撤销、过期、文件缺失或未通过内容门禁，不能预览或下载。历史记录仍按真实状态保留。”不显示图片和下载 |
| delivery.status=approved | 显示“交付待发布”，不提供下载；需要人工发布 |
| delivery.status=revoked | 显示当前内容不可访问或批准已失效，不提供下载 |
| 没有 delivery 或工作流未覆盖当前项目 revision | 不显示当前交付区块；继续显示流程状态 |

当前交付下载和预览使用同一个合法 asset_id；不会因为历史记录里存在旧文件而显示旧图。

### 3.2 历史版本

- 只显示 status=published 且 can_download=true 的版本；被撤销、拒绝、过期或缺少内容门禁审批的版本不可见或不可下载。
- 多于一个版本时显示“切换历史版本”标签，按需求 revision 倒序；当前选择使用 aria-pressed。
- 选中历史版本只改变本地预览；显示“历史快照 · 已审查”、版本需求摘要、已批准提示词、尺寸快照、历史预览图和下载入口。
- 历史预览不会改变当前交付、当前需求、确认按钮或项目 revision，也不会调用模型。
- 旧版本若已不可访问，服务端版本字段会是 can_download=false／asset_id 为空；页面不应通过直链绕过，历史记录仍按真实状态保留。

### 3.3 反馈新版本

反馈区只在当前合法交付存在且服务端返回 request_new_version 时出现。

| 项目 | 行为 |
| --- | --- |
| 文本 | 必填，用于说明本次调整要求 |
| 基准 | 始终基于当前交付；即使用户正在查看历史预览，反馈也不会改成基于历史版本 |
| 提交 | 只提交 request_new_version，带 expected_revision；不会提交旧 /generations 接口 |
| 服务端结果 | 项目 revision 加一，重置需求确认和目标版本，创建新的 S03 流程，保留旧交付和旧版本快照 |
| 页面结果 | 回到 U1 整理需求，不显示局部编辑或像素级保留承诺 |

页面文案固定说明“反馈会重新确认需求并生成一个新版本，不承诺局部像素不变或只修改指定区域。每次生成仍仅一张图并使用固定尺寸。”不要把它改写成局部修改能力。

### 3.4 键盘和窄屏

- 历史标签、反馈文本框和反馈按钮都可键盘聚焦；标签按钮有可见焦点和 aria-pressed。
- 320／768 宽度下工作台为单列；1440 宽度为两列。历史标签和反馈按钮应在视口内，不产生主操作横向溢出。
- 页面外壳如果在 768px 出现 document 级横向宽度，应先确认是否为继承的全局外壳，不要用 overflow:hidden 隐藏 U3 内容。

## 4. 内容门禁与版本事实

- 当前版本判定使用服务端 workflow 视图：status=completed、项目 revision 不落后、delivery.status=published，并且存在与当前 run_id、asset_id、filename 一致且 can_download=true 的 WorkflowVersion。
- 生成图的资产访问还要求有效图片 Approval、asset_id／asset_digest 匹配、交付清单 Approval 和版本 revision；任一条件失败时不得显示图片或下载。
- 历史版本列表和版本快照由服务端构建；前端只做只读投影，不从本地缓存、静态目录或供应商响应推断版本。
- 反馈新版本只递增项目 revision 并创建新 S03 流程，不删除、不覆盖旧交付、旧资产或旧版本记录；旧版本是否仍可下载由它的内容门禁状态决定。

## 5. 验收和复现命令

以下命令只用于本地复现，工作目录为 D:/Desktop/新建文件夹/.worktrees/t75，且使用临时 SQLite 与 fake adapter；不要对真实数据根执行。

前端定向和全量：

~~~
npm test -- --run tests/t75-u3-delivery.spec.ts --reporter=dot
npm test -- --run --reporter=dot
npm run typecheck
npm run build
~~~

预期：定向 1 个文件／6 个测试通过；全量 43 个文件／248 个测试通过；vue-tsc exit 0；Vite exit 0，仅有 chunk-size 警告。

后端定向和全量（工作目录 backend，uv 环境）：

~~~
uv run python -m pytest -q tests/test_t75_delivery_history.py
uv run python -m pytest -q tests/test_t75_image_size_gate.py
~~~

预期：交付历史测试 1 passed；尺寸门禁测试 2 passed。全量按非 v2／v2 分开运行，合计 589 passed，无失败、跳过或 unknown。

真实浏览器、真实 HTTP、临时 SQLite、完整 App：

~~~
node D:/Desktop/新建文件夹/.scratch/t75-browser/run_browser.mjs
~~~

预期：ok true，30 项检查通过；当前交付图片／下载、历史双版本、本地切换、内容门禁拒绝与恢复、反馈新版本和 1440／768／320 布局均通过；visual_submit=1、visual_poll=1，历史选择和反馈不增加模型调用。结果写入 .scratch/t75-browser/browser-result.json。

## 6. 排障

| 现象 | 检查 | 处置 |
| --- | --- | --- |
| 交付已发布但没有图片或下载 | 当前版本 can_download 是否 true；asset_id／filename 是否与 delivery 一致；资产是否 rejected／expired／missing | 查看 workflow 版本和资产审批；不要用静态路径或旧 URL 绕过内容门禁 |
| 显示“当前交付不可用” | delivery 是否 published 但当前版本不可下载；图片 Approval 或交付清单 Approval 是否有效 | 按 R1／R2 内容门禁修复审批或资产状态；历史记录保留，不要删除审计 |
| 历史标签缺失 | 版本是否 status=published 且 can_download=true；是否被撤销、拒绝、过期或缺少审批 | 只从服务端真实版本列表排查；不要在前端伪造版本 |
| 选择历史后当前交付变化 | 当前交付是否仍指向当前 run 的 asset_id；是否误改了 workflow 或项目 revision | 历史选择必须只读；恢复正常后刷新；检查是否有旧页面脚本或外部写请求 |
| 反馈区不出现 | 是否存在合法 currentVersion；available_actions 是否有 request_new_version；delivery 是否已发布／批准 | 先修复当前交付内容门禁；不要直接调用旧生成接口 |
| 反馈后没有回到 U1 | POST 响应错误码、项目 revision、brief_status、S03 运行是否创建 | 按服务端错误处理，不要重复提交；检查是否被权限或内容门禁阻断 |
| 反馈后旧版本消失 | 旧 Delivery／Asset／WorkflowVersion 是否被删除或覆盖 | 这是严重回归；停止操作并按 #54 备份流程保留现场，恢复旧版本记录后再继续 |
| 历史或反馈触发了模型 | 检查网络请求、stats（烟测环境）和后端日志 | 正常页面不应自动调用；定位旧脚本或外部重试，不要删除证据 |
| 768 宽度出现横向滚动 | 区分 U3 workbench 宽度和全局外壳 document 宽度 | U3 工作台在 768 为 649px、控件在视口内；全局外壳问题记入 Release 门禁 |
| 下载文件与预览不一致 | 检查当前版本 asset_id／filename 与 delivery 是否一致；是否拿到旧缓存或旧版本 | 刷新读取服务端状态；不要用历史 asset_id 替代当前交付 |

## 7. 回滚和数据边界

- 本票没有数据库迁移，迁移头仍为 0010；回滚代码提交 1b6f59a 的前端与 config_status 改动，不涉及表结构。
- config_status 的尺寸判定修复是默认 3840×2160 生成所必需；回滚前端但保留后端修复时，旧前端仍可能只显示旧交付；回滚后端但保留新前端时，默认纵向尺寸会再次被误报不可用，因此建议整体回滚并重新验证配置状态。
- 回滚不授权删除或重建真实数据。不得删除交付、资产、版本、审批、审计、意图、次数或槽位记录来“修复”显示；真实未知必须按 #68／#69 的核实、找回、结案和新任务规则处理。
- 生产回滚前先按 #54 备份流程完成服务停止、来源确认和 manifest 核验；本说明不授权直接操作真实数据。
- 本票没有读取或写入 backend/data 或 main/backend/data；浏览器和测试只使用 .scratch 下的临时目录、临时 SQLite 和临时 Edge 用户目录。

## 8. 关联文档和证据

- 验收记录：docs/acceptance/r3-u3-delivery.md。
- R2 生成周期：docs/acceptance/r2-generation-cycle.md。
- R2 恢复和重启：docs/acceptance/r2-recovery-restart.md。
- R2 本地结案和新任务：docs/acceptance/r2-local-closure.md。
- R2 门禁：docs/acceptance/r2-gate.md。
- R3 U1 工作台：docs/acceptance/r3-u1-workbench.md。
- R3 U2 原位授权：docs/acceptance/r3-u2-authorization.md。
- 浏览器证据：.scratch/t75-browser/browser-result.json、server.log；测试日志：.scratch/t75-evidence/。
