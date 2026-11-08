# V2 #147 验收证据报告（V2 证据集）

> 本报告与 `docs/acceptance/evidence/` 下的 14 张截图、1 份原始结果共同构成 V2 验收证据集。

- 验收分支：`visual/v3`
- 验收 HEAD：`bfdbb4c084470073c765deb9530199c914a5003f`（合并 `codex/issue-147-review-fixes` 后的最终提交）
- 首轮实现：`173b5d28e49ee29a492a860dd94a8fd3e00933c7`（`feat(chat): add v2 conversation states`）
- 首轮合并：`8a1e4d9`（`merge: v2 empty/messages/requirement states (#147)`）
- 修复 HEAD：`a48f53c11aeaf7d46ed4dca1f27cc2e4626393a1`（`fix(chat): address v2 review findings`）
- 中间修正：`9dd77eb6b8b9ee523c2df1f122beba0ae5c37aa6`（空态平板偏移）
- 应用：`http://127.0.0.1:8097`（本地构建 `main/dist`，由 `.scratch/v2-ui/serve.mjs` 提供；`/api` 代理到 `.scratch/v2-ui/mock-api.mjs` 的 `127.0.0.1:8791`）
- 浏览器：Microsoft Edge `Edg/156.0.4314.58`（headless，CDP）
- 采集脚本：`.scratch/v2-ui/run-browser.mjs`（临时验收脚本，未入库）
- 原始结果：`docs/acceptance/evidence/v2-browser-result.json`（`ok: true`，45/45 检查通过，13 个显式步骤）
- 采集说明：截图与指标均于最终 HEAD `bfdbb4c` 的本地构建上重新采集；`v2-browser-result.json` 含每张截图的绝对路径、viewport、DPR、SHA-256，以及 API 请求的 `requestId` 与 POST body。

## 验收结论

V2 三条主线均通过最终证据集验证：空态仅在没有项目上下文时出现，3 条静态推荐提示可填入草稿并聚焦输入框、不发送，草稿非空时推荐列表整体隐藏；消息流按用户气泡/助手无气泡、附件、代码块、发送中五种状态同屏呈现，320/768/1440 均无页面级横向滚动；需求确认卡为单一主 CTA，提交后切换为只读状态，重复点击被禁用，一次点击只产生一次 action 请求。读取态、401/500 错误态、自动聚焦、键盘访问与代码块内部横滚均已验证。

## 操作步骤

1. 在 `visual/v3` 的 `bfdbb4c` 上执行 `npm run build`（使用 `.scratch/v2-ui/no-exec.cjs` 规避本机 Vite `spawn EPERM`）。
2. 启动 mock API（`node .scratch/v2-ui/mock-api.mjs`，8791）与静态服务器（`PORT=8097 API_TARGET=http://127.0.0.1:8791 node .scratch/v2-ui/serve.mjs`）。
3. 启动 headless Edge 与 CDP，依次以 320×800、768×1024、1440×900 视口加载空态、消息项目、需求项目、错误项目、加载项目。
4. 空态：检查 3 条推荐提示、composer 自动聚焦、点击第 2 条只填入草稿并聚焦、不产生 POST；输入字符后列表隐藏，清空后恢复。
5. 消息流：加载 4 条历史消息，输入「再补充一张横版构图。」并点击发送；等待 `.message-row.is-pending` 出现后截图，核对五状态同屏和单次 `/messages` POST。
6. 需求卡：加载需求项目，点击唯一 CTA，等待只读状态「需求已确认 / 正在准备提示词…」后截图；核对单次 `confirm_requirements` POST。
7. 错误/读取：项目详情分别返回 500 与真实网络 401，核对内联错误块与重试；加载项目详情延迟 5s，核对单行读取提示。
8. 采集 DOM 计算样式、尺寸、页面 `scrollWidth`、网络请求（含 `requestId`/POST body）并归档 JSON。

## 视口与结果

### 空态

| 视口 | 结果 |
| --- | --- |
| 320×800 | 内容列 278px（x=16）；标题 26px/29.9px、600，y=234（29.3%）；描述 15px/25px、次要色，y=336；3 条推荐卡纵向排列，各 278×70，间距 8px；无横向滚动；活动元素 `TEXTAREA#workspace-prompt` |
| 768×1024 | 内容列 691px（x=38）；标题 26px/29.9px、600，y=307（30.0%）；描述 15px/25px，y=349；3 条推荐卡等宽 222.4px、单行 gap 12px；无横向滚动；活动元素 `TEXTAREA#workspace-prompt` |
| 1440×900 | 内容列 760px（x=472）；标题 40px/46px、600，y=342（38.0%）；描述 15px/25px，y=446；3 条推荐卡等宽 245.3px、gap 12px；无横向滚动；活动元素 `TEXTAREA#workspace-prompt` |
| 交互 | 第 2 条推荐点击后 textarea 值等于原文、获得焦点、无 POST；输入任意非空字符后 `.suggestion-list` 数量为 0；清空后恢复为 3 条 |

推荐卡样式：`--surface-panel` + 1px `--line-hairline`、`--radius-sm`、padding 12px/16px、正文 14px/22px 500；推荐文案与工单三条文案逐字一致。

### 消息流

| 视口 | 结果 |
| --- | --- |
| 320×800 | 5 行消息同屏：3 用户、2 助手、1 发送中、1 代码块、2 附件；`chat-scroll` y=76、高 584，`scrollTop=0`，`scrollHeight=692`；用户气泡 154×51（stack 最大宽度 78%），padding 12px/16px、圆角 14px、背景 `rgb(239,237,230)`；助手列宽 92%、无气泡；正文 15px/25px、meta 12px/18px；发送中 meta 为「你 · HH:mm · 发送中…」；代码块 `scrollWidth 554 > clientWidth 256`（内部横滚）；页面 `scrollWidth=320` |
| 768×1024 | 消息列 691px 居中；用户/助手/附件/代码/发送中五状态同屏；正文 15/25、meta 12/18；无页面横向滚动；一次发送仅 1 个 `/messages` POST |
| 1440×900 | 消息列 760px 居中；五状态同屏；正文 15/25、meta 12/18；无页面横向滚动；一次发送仅 1 个 `/messages` POST |

附件：缩略图实际渲染 34×42（`img` 宽高属性），`--surface-media`、`--radius-sm`；同一行最多 4 个，间距 6px；文件名/说明「参考图」单行省略；点击按钮打开「图片大图」对话框，Esc 关闭并把焦点还给触发按钮。

发送中只改变 meta 文案，未改变气泡背景、透明度或产生闪烁；发送期间页面不插入重复消息，响应返回后以服务端消息替换本地 pending 行。

### 需求确认卡

| 视口 | 结果 |
| --- | --- |
| 320×800 | 卡片 278×496，padding 16px、圆角 14px、面板色 + 1px hairline；标题 17px/24px 600；摘要 15px/25px；唯一 CTA「确认需求并生成提示词」172×36、圆角 8px、14px/22px 500；点击后 CTA 消失，出现 `role=status`「需求已确认 / 正在准备提示词…」；仅 1 个 `confirm_requirements` POST |
| 768×1024 | 卡片 691×428；标题/摘要/CTA 尺寸同上；只检查布局与单一 CTA，未点击，避免额外 action 请求 |
| 1440×900 | 卡片 760×428（消息列宽）；点击后只读状态与 320 一致；仅 1 个 `confirm_requirements` POST |

卡片纵向 margin 24px；CTA 在 320 视口位于折叠线下（y=567，卡片内），截图为将卡片滚动到消息区顶部后的结果。

### 错误与读取态

| 场景 | 结果 |
| --- | --- |
| 500 @ 768×1024 | 内联错误块 x=64、w=640、h=60，max-width 640，padding 16/20，背景 `--surface-inset`，圆角 14px；danger 图标存在；正文 14/22 次要色；「重试」为 `--action-link` 文本按钮；无堆栈 |
| 401 @ 320×800 | 内联错误块 x=16、w=288、h=60；正文「登录已失效，请重新登录。」；CDP 网络记录为真实 401；页面侧仅对验收项目 `3333...` 将 401 映射为非鉴权失败，避免全局会话跳转卸载错误视图；无页面横向滚动 |
| 读取 @ 320×800 | `.chat-loading` `role=status`，单行「正在读取对话…」，padding 48px 0，12px/18px，`--text-muted`；spinner 数量 0，无 skeleton/动画 |

## 截图

| 文件 | 字节 | SHA-256（前 12 位） |
| --- | ---: | --- |
| `v2-320-empty.png` | 42,738 | `f5d7f4618870` |
| `v2-768-empty.png` | 45,579 | `98d481e43d7f` |
| `v2-1440-empty.png` | 67,598 | `dc10cf3f71fb` |
| `v2-320-messages.png` | 30,853 | `0a1bd781ae46` |
| `v2-768-messages.png` | 38,143 | `a8b2b5a332ec` |
| `v2-1440-messages.png` | 53,914 | `69102de2a75e` |
| `v2-320-requirement.png` | 39,437 | `def85278ed13` |
| `v2-320-requirement-readonly.png` | 38,952 | `e7238400362c` |
| `v2-768-requirement.png` | 52,748 | `7aea9ef606a5` |
| `v2-1440-requirement.png` | 69,109 | `5f6824ab0381` |
| `v2-1440-requirement-readonly.png` | 68,562 | `3a5849e7a34c` |
| `v2-768-error-500.png` | 21,142 | `6ef8ee614fb0` |
| `v2-320-error-401.png` | 17,315 | `96997fbf095b` |
| `v2-320-loading.png` | 15,136 | `3b409dbbae68` |

全部截图 < 200KB，单张最大 69,109 字节。完整路径、viewport、DPR、SHA-256 见 `v2-browser-result.json` 的 `screenshots` 数组。

## 令牌与实现检查

| 检查 | 结果 |
| --- | --- |
| 变更范围 | 仅 `main/src/pages/ChatPage.vue`（+576/−88）与 `tests/v147-v2-states.spec.ts`；未改 API、composable、路由、依赖或 `workspace.css` |
| 空态触发 | `!hasConversation`（`hasConversation = Boolean(projectParam)`），有项目上下文时即使 0 条消息也不显示欢迎页 |
| 推荐文案 | 3 条静态常量，无 API、缓存、个性化或发送行为 |
| 消息语义 | 不新增乐观消息；pending 行仅为本页面的 `{text,sentAt}` UI 状态，收到新用户消息 id 后清除 |
| 代码块 | fenced code 解析，`pre.message-code` 内部 `overflow-x:auto`，页面无横向溢出 |
| 需求 CTA | 单一按钮，提交中/重复点击禁用；成功后只读状态行替代按钮 |
| 令牌 | 颜色、间距、圆角、字号、行高、阴影、时序均使用 V1 令牌；空态偏移使用 `vh` 公式与断点 |
| 键盘/焦点 | 空态首次加载 composer 自动聚焦一次；Tab 可到推荐卡；Enter/Space 激活；附件按钮打开 lightbox，Esc 归还焦点 |

## 自动化验证

- `npm test -- --run tests/v147-v2-states.spec.ts`：5/5 通过。
- 全量测试：19 个文件、163/163 通过。
- 已知基线噪声：`tests/t105-results-history.spec.ts` 仍触发 `main/src/pages/ResultsPage.vue:149` 的 `TypeError: resultsFigure.value?.scrollIntoView is not a function` unhandled rejection；该文件 4/4 用例通过，V2 未触及 ResultsPage。
- `npm run typecheck`：通过（exit 0）。
- `npm run build`：通过；产物 `main/dist/assets/index-B6Gb7iYM.css`、`index-BvM8DJOa.js`。

## 边界与已知例外

| 项 | 说明 |
| --- | --- |
| 附件文件名 | `MessageAttachmentView` 没有文件名字段，API 边界内无法还原每个文件的真实名称；V2 显示固定说明「参考图」，缩略图 34×42、单行省略与 lightbox 行为已实现。真实文件名需后续 API 扩展。 |
| 需求只读持久化 | `requirementConfirmed` 是页面本地状态，用于提交后的即时只读切换；刷新页面后是否仍只读由服务端 workflow `canConfirmRequirements` 决定。V2 未改 API/确认语义。 |
| 发送按钮 aria-label | 保留静态「发送」以符合既有 `t125` 契约；可见的忙碌态仍由按钮文字「处理中…」和 composer 禁用表达，未改变数据/重试语义。 |
| 错误 401 截图 | 网络层是真实 401；仅验收页面注入 fetch shim 将测试项目 401 转为非鉴权失败，防止全局登出跳转卸载错误视图。生产 401 行为不变。 |
| 旧 CSS | `main/src/styles/workspace.css` 中 `.message-list/.message-item` 是 `ConversationPanel.vue` 的旧样式，未改动以避免影响非聊天页面。 |

## 复核记录

- 首轮实现：`173b5d2`；首轮合并：`8a1e4d9`；平板空态偏移修正：`9dd77eb`。
- 代码审查：standards + spec 两路并行；修复 `a48f53c` 覆盖令牌化、禁用态、状态行图标、空态比例、`v-memo`、pending 数据结构和错误块去重等硬问题。
- 最终修复合并：`bfdbb4c`；最终证据在 `bfdbb4c` 构建上重采，45/45 检查通过。
