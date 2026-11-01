# R3 T25 多视口验收与旧四页收缩关口验收记录

- Issue: #77
- 父 Issue: #52
- 分支: ticket/77
- 基线: integration/v3 3ed15f8
- 代码提交: 5ced2567d1201c002781ed0a05c21c7eb2cb5af1
- 验收日期: 2026-11-01
- 状态: 通过（R3-GATE 阶段条件；#51 仍由 Release 阶段关闭）

## 1. 结论与边界

本票把 R3 的旧四页必经路径收缩为单一工作台，并把旧外部地址改成无副作用跳转。替换后的工作台继续承载 U1 沟通与需求卡、U2 原位授权与安全恢复、U3 当前交付与历史预览；旧页面、阶段导航和不再被消费的状态代码已删除，没有把权限、内容门禁、恢复或审计降级为前端状态。

1. 工作台路由只保留 `/` 下的 describe 子路由；`/describe`、`/confirm`、`/generate`、`/results` 四个外部旧地址都重定向到同一个 describe 路由，并保留 `project` 查询参数与 hash。未知路径回到 describe。
2. 旧地址跳转只做路由替换，不发送确认、生成、恢复、发布或资产请求；即使 URL 带有 `action=start_new_generation` 和旧 `request_id`，重定向也不会重放动作。
3. 旧页面 ConfirmPage、GeneratePage、ResultsPage 已删除；StageNav、ResultRetrievalPanel、GenerationRiskDialog 也已删除；WorkspaceLayout 不再渲染阶段导航、旧状态查询提示或旧生成风险弹窗。
4. useWorkspace 已收敛为会话、项目、参考图、草稿和配置状态；旧的需求编辑、阶段跳转、生成提交、生成轮询、结果选择与生成凭据存储逻辑全部移除。BriefCard 现在是服务端快照的只读展示，不再提供本地编辑或确认按钮；未发送草稿仍只保存在页面内存。
5. U1/U2/U3 的原位行为保持：单次勾选确认后提交 R2 受理动作；执行进度只展示真实四步状态，不显示假百分比；已批准执行提示词只在折叠区出现；失败、未知、内容撤销和过期继续按真实状态阻断；历史版本切换只改变本地预览，不修改需求、不调用模型。
6. 权限、会话隔离、跨标签失效、BFCache 返回、迟到响应和 A→B→A 账号切换继续由既有 R1/R3 测试覆盖；本票没有因为删除旧页面而降低这些断言。
7. HistoryPreview 增加保留期与旧数据过滤：只展示 succeeded、非 expired、非 legacy_unreviewed 且 result_asset 可用的历史图。该修复防止过期图片或未审查旧资产通过历史预览被重新请求。
8. 320、768、1440 CSS 像素均保持同一工作台语义；标签、焦点、键盘、中文输入和主操作控件在真实浏览器中有检查。移动端和桌面端均不使用旧页面的强制跳转。
9. 测试迁移按“删除旧 UI 路径、保留并指向替代覆盖”的方式完成：删除 90 个只验证旧页面/旧存储/旧编辑路径的测试，新增 tests/t77-r3-gate.spec.ts 的 8 个关口测试；全量前端从 44 个文件／255 个测试变为 38 个文件／173 个测试，所有保留测试通过。
10. 本票验收边界为临时 SQLite、完整 App、真实 HTTP 与 headless Edge；使用假 LLM/视觉适配器，不访问真实数据根，不调用真实模型。实体手机、真实输入法和真实密码管理器仍需 Release 阶段人工复核。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t77 |
| 分支 | ticket/77 |
| 基线 | integration/v3 3ed15f8 |
| Node | v26.12.1 |
| npm | 11.20.0 |
| Vitest | 5.0.5 |
| Vite | 8.3.5 |
| Python | 3.12.13（复用既有工作树 .venv 依赖；生产代码来自 t77） |
| 数据库 | 临时 SQLite，alembic 0010，26 张表，integrity ok |
| 浏览器 | headless Microsoft Edge，CDP |
| 模型适配器 | LLMStub / VisualStub，无外部调用 |

## 3. 实现摘要

### 3.1 路由收缩

- main/src/router/index.ts 只保留 `/login`、`/set-password`、`/account/security`、`/admin/accounts` 和 WorkspaceLayout 下的 `{ path: '', name: 'describe', component: DescribePage }`。
- `/describe`、`/confirm`、`/generate`、`/results` 各自使用 redirect 函数：`{ name: 'describe', query: to.query, hash: to.hash }`。未知路径使用 catch-all 回到 describe。
- 重定向不创建动作、不读取生成凭据、不触发 `/generations` 或 `/assets` 请求；外部书签只获得新的工作台地址。

### 3.2 页面与状态清理

- 删除 main/src/pages/ConfirmPage.vue、GeneratePage.vue、ResultsPage.vue。
- 删除 main/src/components/StageNav.vue、ResultRetrievalPanel.vue、GenerationRiskDialog.vue。
- WorkspaceLayout 删除 StageNav、GenerationRiskDialog、旧状态查询提示和 `briefDirty` 传递；UnsavedChangesDialog 只提示未发送草稿。
- useWorkspace 从约 3180 行收缩到约 1500 行，只保留会话、项目列表/详情、参考图、草稿和配置状态；ConversationPanel 的发送禁用只依赖会话、整理中状态和当前生成状态。
- BriefCard 改为 75 行只读快照组件：显示需求字段、图片尺寸、可选提示词和参考资源状态，不提供编辑、保存、重新整理或确认动作。
- DescribePage 维持 U1 双列工作台：左侧 ConversationPanel，右侧 WorkflowPanel + HistoryPreview；1023px 以下单列。

### 3.3 历史预览安全过滤

- main/src/components/HistoryPreview.vue 只有同时满足以下条件才展示历史项：`generation.status === 'succeeded'`、`generation.retention_status !== 'expired'`、`generation.legacy_unreviewed !== true`、`generation.result_asset?.availability === 'available'`。
- 该过滤由测试迁移发现：旧历史预览曾可能请求被保留期清理或未审查的旧资产。修复后 t01-legacy-isolation 和 v2-t20-retention 都验证不会请求旧资产或显示过期结果。

### 3.4 测试迁移与替代覆盖

基线为 44 个测试文件／255 个测试；本票删除 7 个只针对已删除页面的文件（共 79 个测试），并在保留文件中删除 11 个只验证已删除 UI/存储路径的测试，新增 8 个关口测试，最终 38 个文件／173 个测试。

| 删除或改写的测试 | 原因 | 替代覆盖 |
| --- | --- | --- |
| tests/t04-brief-edit.spec.ts（17） | 旧 BriefCard 编辑/确认页已删除 | t73 U1 需求卡只读展示、t65/t66 确认漂移、t74 原位授权 |
| tests/t06-generation-first-result.spec.ts（15） | 旧 GeneratePage/ResultsPage 已删除 | t65–t68 生成周期、t74 执行状态、t75 当前交付 |
| tests/t08-version-browsing.spec.ts（9） | 旧 ResultsPage 版本浏览已删除 | t75 U3 历史标签、当前交付和法律版本测试 |
| tests/t09-uncertain-submit-recovery.spec.ts（15） | 旧页面恢复按钮/凭据已删除 | t68 核实与恢复、t69 本地结案、t74 安全恢复、t70 R2 门禁 |
| tests/t10-status-recovery.spec.ts（11） | 旧状态查询页已删除 | t68 重启恢复、t71/t72 会话与跨标签、t74 轮询 |
| tests/t11-terminal-recovery.spec.ts（10） | 旧终态恢复页已删除 | t68/t69 终态与计数、t70 门禁、t74 安全恢复 |
| tests/v2-t44-version-history.spec.ts（2） | 旧版本历史页已删除 | t75 U3 当前交付与历史预览、t77 旧地址跳转测试 |
| code-review-regressions.spec.ts 中 4 个旧结果/阶段测试 | 旧结果自动打开、阶段焦点和旧凭据不存在 | 保留 CRC/JPEG/WebP/消息冲突/模型抽屉；认证边界由 t55 覆盖 |
| t07-navigation-conflict.spec.ts 中 2 个旧 BriefCard 测试 | 旧本地编辑和旧版冲突已删除 | 保留草稿取消/保留、beforeunload 草稿和返回导航测试 |
| t72-cross-tab-history.spec.ts 中 5 个旧生成凭据测试 | 旧无账号键、账号键和恢复存储已删除 | 保留 3 个会话/跨标签/迟到响应测试；生成恢复由 t68/t69 覆盖 |

说明：删除的测试都不是当前 V3 契约的有效断言，而是已退场页面的实现细节；每个被删除的行为都有上表列出的替代测试。没有为了通过测试而放宽权限、内容门禁、计数或恢复断言。

### 3.5 新增关口测试

- tests/t77-r3-gate.spec.ts 共 8 个测试：`/describe`、`/confirm`、`/generate`、`/results` 四个旧地址分别重定向且无写入；带旧动作参数的生成地址不重放；320/768/1440 三个视口保留工作台双列 DOM、标签、焦点和无导航。
- 测试使用完整 App、真实 Router 和 FetchController，仅替换 HTTP、时钟、可见性和存储边界；不替换页面组件。

## 4. 命令与结果

### 4.1 专项关口测试

    cd D:/Desktop/新建文件夹/.worktrees/t77
    npm test -- --run tests/t77-r3-gate.spec.ts --reporter=verbose

结果：1 个文件／8 个测试通过，3.90s，退出码 0。日志 .scratch/t77-evidence/gate.log。

### 4.2 全量前端测试

    npm test -- --run --reporter=dot

结果：38 个文件／173 个测试全部通过，退出码 0。`--reporter=json` 复核为 173 passed、0 failed、success=true。日志 .scratch/t77-evidence/frontend-full3.log、frontend2.json。

### 4.3 类型检查与生产构建

    npm run typecheck
    npm run build

结果：typecheck 退出码 0；Vite 8.3.5，1664 个模块，820ms，退出码 0；dist/assets/index-B2Fixx5N.css 430.37 kB，dist/assets/index-CFEBsUU2.js 1,159.11 kB；只有 chunk 体积警告。日志 .scratch/t77-evidence/typecheck2.log、build2.log。

### 4.4 后端回归

    cd D:/Desktop/新建文件夹/.worktrees/t77/backend
    C:/Users/28425/.local/bin/uv.exe run python -m pytest [显式测试文件] -q

结果：非 v2 39 个文件／354 个测试通过，2 个警告，404.55s，退出码 0；v2 19 个文件／235 个测试通过，318.79s，退出码 0；合计 589 个测试通过，无失败、跳过或未知。唯一警告为 backend/tests/support/auth.py:42 的 per-request cookie 弃用警告。日志 .scratch/t77-evidence/backend-nonv2.log、backend-v2.log。

### 4.5 真实浏览器／HTTP／临时 SQLite／完整 App 验证

    D:/nodejs/node.exe D:/Desktop/新建文件夹/.scratch/t77-browser/run_browser.mjs

结果：退出码 0，ok=true，35 项检查通过。脚本启动临时 SQLite、执行 alembic 0010、以 uvicorn 启动完整 App，种子创建管理员与普通账号，并用 headless Edge CDP 操作。

检查覆盖：

1. `/describe`、`/confirm`、`/generate`、`/results` 四个旧地址都回到路径 `/`，保留项目查询参数，工作台存在，无旧阶段导航、无“查看结果”、无“结果已过保留期”，且没有新增写请求。
2. 带 `action=start_new_generation&request_id=legacy-request` 的旧生成地址保持惰性，没有写请求，也没有 `/api/generations` 或 `/api/assets` 读取。
3. 登录后打开工作台，S06 只显示一个原位确认动作；未勾选时按钮禁用并显示“请先勾选确认。”；今日受理、下一步和四步执行进度文案真实。
4. 勾选并按空格后只发生一次 `confirm_requirements` 写请求，路由保持在 `/`，没有 `/generations` 请求；执行进度四步无假百分比。
5. 背景编译、图片提交、审核和交付准备在显式释放后完成；已批准执行提示词只在折叠区出现，展开前不显示内容。
6. 发布交付后显示当前交付图片、“下载当前交付”、当前版本和 3840×2160 尺寸；种子历史版本出现两个标签；切换第 1 版只改变本地预览，当前交付 src 不变，无写请求且 stats 不变。
7. 内容门禁把当前资产标记 rejected 后页面显示“当前交付不可用”，不显示图片和下载，历史仍保留；恢复后重新可用。
8. 基于当前交付反馈会创建新版本并回到 U1；路由保持 `/`，只有一次 `request_new_version` 写请求，没有 `/generations`，模型调用计数不变。
9. 1440px 为双列两栏；768px 工作台单列；320px 单列且工作台、按钮、分页/标签均在视口内，文档宽度 320；全部主操作控件无横向溢出。

临时数据库检查：alembic 0010，26 张表，pragma integrity_check = ok；没有读取或写入 backend/data 与 main/backend/data。

## 5. 验收条件对照

| 验收项 | 本票证据 | 结论 |
| --- | --- | --- |
| V3-AC-14 | 全量前端 38 文件／173 测试、后端 589 测试、真实浏览器 35 项检查全部通过；R3 旧四页已被单一工作台替代，无失败或未知 | 通过（R3-GATE） |
| V3-AC-22 | 本票未新增外部脚本/图片/字体；品牌资源继续由 #76 的本地 AuthFrame/BrandMark 提供；工作台不引用参考站资源 | 阶段条件通过（沿用 #76） |
| V3-AC-23 | 单一工作台同时承载 U1/U2/U3；旧确认、生成、结果页删除；`/describe`/`/confirm`/`/generate`/`/results` 无副作用跳转；t73/t74/t75 + t77 覆盖 | 通过 |
| V3-AC-24 | t74 原位授权、t65/t66 R2 受理与重放、t70 R2 门禁；本票未改动后端受理契约 | 阶段条件通过 |
| V3-AC-25 | t73 当前需求卡只读展示、候选内容隐藏、漂移回 U1；t74 执行提示词只显示已批准内容 | 阶段条件通过 |
| V3-AC-26 | t74 四步执行状态与安全恢复、t68/t69 未知与本地结案、t75 当前交付；本票不重放确认 | 阶段条件通过 |
| V3-AC-27 | t74 轮询只 GET、t71/t72 会话/跨标签、浏览器第 4/6/8 项均验证无额外模型调用 | 阶段条件通过 |
| V3-AC-28 | t68/t69 失败与未知保持真实状态、t73 未批准内容隐藏、t74 安全恢复、t75 内容门禁与当前交付 | 阶段条件通过 |
| V3-AC-29 | t75 单图、3840×2160、历史尺寸快照、反馈新版本不覆盖旧版本；浏览器第 6/7 项 | 阶段条件通过 |
| V3-AC-38 | tests/t77-r3-gate.spec.ts 三视口 DOM/标签/焦点；真实浏览器 1440/768/320 工作台布局与控件边界；中文输入和键盘沿用 t73/t76 | 阶段条件通过 |
| V3-AC-39 | 模型数据流提示仍在 DescribePage 首个输入前；本票未新增秘密输入；认证页与浏览器本地存储由 t76 覆盖 | 阶段条件通过 |

## 6. 证据位置

- 关口测试：D:/Desktop/新建文件夹/.worktrees/t77/tests/t77-r3-gate.spec.ts
- 全量前端日志：D:/Desktop/新建文件夹/.scratch/t77-evidence/frontend-full3.log、frontend2.json
- 类型检查日志：D:/Desktop/新建文件夹/.scratch/t77-evidence/typecheck2.log
- 构建日志：D:/Desktop/新建文件夹/.scratch/t77-evidence/build2.log
- 后端日志：D:/Desktop/新建文件夹/.scratch/t77-evidence/backend-nonv2.log、backend-v2.log
- 浏览器脚本：D:/Desktop/新建文件夹/.scratch/t77-browser/run_browser.mjs
- 浏览器结果：D:/Desktop/新建文件夹/.scratch/t77-browser/browser-result.json（35 项检查，ok=true）
- 浏览器服务日志：D:/Desktop/新建文件夹/.scratch/t77-browser/server.log

## 7. 残余风险与未覆盖边界

1. 真实浏览器为 headless Edge，不是实体手机或桌面人工验收；CSS 像素、键盘和焦点可重复，但触控手感、系统输入法候选框和真实密码管理器扩展仍需 Release 阶段人工复核。
2. 768px 下工作台本身为单列且控件在视口内，但全局工作台壳层的文档宽度可达 1020px（既有侧栏/页头行为）；本票没有把该既有壳层问题计入“主操作横向溢出”，Release 视觉复核应继续观察。
3. 浏览器脚本使用种子账号、临时 SQLite、LLMStub/VisualStub 和 fake 视觉结果；没有验证真实供应商的生成质量、真实图片内容或供应商保留政策。
4. 测试删除按上表完成替代映射；外部消费者若直接导入已删除的页面或组件会失败，外部旧 URL 则由路由重定向兼容。
5. 本票没有新增后端路由、后台任务或数据库迁移；所有生成、恢复、内容门禁、审计和计数仍由 R1/R2 服务执行。
6. 未知路径 catch-all 只回到 describe，不主动保留任意未知 query；四个已知旧页面地址会保留 query/hash。
7. 本票不关闭 #51；Release 阶段仍需完成版本发布、全量验收和发布证据。
