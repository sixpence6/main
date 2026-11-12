# V3 #148 验收证据报告（提示词确认卡）

> 本报告与 `docs/acceptance/evidence/` 下的 15 张截图、1 份原始结果共同构成 V3 验收证据集。

- 验收分支：`visual/v3`
- 验收 HEAD：`589689a66e347e1ec2c68785bee77ee1b3d96550`（合并 `codex/issue-148-v3` 后的合并提交）
- 实现提交：`33960abd7fd9a669401c4304af9669346fbdda1b`（`feat(chat): add V3 prompt confirmation card (#148)`）
- 基线：`5306a72`（V2 合并后的 `visual/v3`）
- 应用：`http://127.0.0.1:8098`（本地构建 `main/dist`，由 `.scratch/v2-ui/serve.mjs` 提供；`/api` 代理到 `.scratch/v3-ui/mock-api.mjs` 的 `127.0.0.1:8793`）
- 浏览器：Microsoft Edge `Edg/156.0.4314.58`（headless，CDP）
- 采集脚本：`.scratch/v3-ui/run-browser.mjs`（临时验收脚本，未入库）
- 原始结果：`docs/acceptance/evidence/v3-browser-result.json`（`ok: true`，66/66 检查通过，8 个显式步骤）
- 采集说明：截图与指标均于验收 HEAD `589689a` 的构建上重新采集；`v3-browser-result.json` 含每张截图的绝对路径、viewport、DPR、SHA-256，以及确认请求的完整 POST body（含 `command_id`）。

## 验收结论

V3 提示词确认卡五个状态均通过最终证据集验证：待确认展示标题、版本、状态胶囊、引用正文、负面块、事实与双动作；检查中 CTA 禁用但复制仍可用；复制后按钮变为「已复制」，CDP 读取的剪贴板内容与卡片正文逐字一致，2000ms 后复原；确认后动作区收起、正文保持只读并追加执行状态行，一次点击只产生一次 `confirm_prompt` action；超长正文在 480px 高的容器内滚动，CTA 在三档视口均保持可见。320/768/1440 均无页面级横向滚动，卡片宽度与消息列一致。

## 操作步骤

1. 在 `visual/v3` 的 `589689a` 上执行 `npm run build`（使用 `.scratch/v2-ui/no-exec.cjs` 规避本机 Vite `spawn EPERM`）。
2. 启动 mock API（`node .scratch/v3-ui/mock-api.mjs`，8793，`CONFIRM_DELAY_MS=8000`）与静态服务器（`PORT=8098 API_TARGET=http://127.0.0.1:8793 node .scratch/v2-ui/serve.mjs`）。
3. 启动 headless Edge 与 CDP，依次以 320×800、768×1024、1440×900 视口加载待确认、检查中、超长三个项目。
4. 待确认：核对标题/版本/胶囊/引用/负面块/事实/CTA；复制并读取剪贴板；点击 CTA 一次，3 秒内截图确认态。
5. 检查中：核对 CTA 禁用（背景 `--surface-inset`、文字 `--text-muted`、opacity 1）与复制按钮可用。
6. 已复制：点击复制，确认按钮文案为「已复制」，2000ms 内截图；测试额外验证 1999ms 仍保持、+1ms 复原。
7. 超长：滚动 `.prompt-card-body`，确认 `scrollHeight > clientHeight = 480`，CTA 仍可见，页面不产生横向滚动。
8. 采集 DOM 计算样式、尺寸、页面 `scrollWidth`、网络请求（含 `requestId`/POST body）并归档 JSON。

## 视口与结果

### 待确认 / 检查中 / 已复制 / 已确认

| 视口 | 待确认 | 检查中 | 已复制 | 已确认 |
| --- | --- | --- | --- | --- |
| 320×800 | 卡片 278×483，x=16；标题 17/24 600；引用 15/25，padding 14/16；事实 2 列；CTA 244×36、圆角 8、y=608；2 个按钮 | 卡片 278×513；CTA 244×36 禁用，opacity 1；复制仍可用 | 复制按钮文案「已复制」，剪贴板 38 字符；卡片尺寸同待确认 | 卡片 278×421；动作区 0 按钮；`role=status`「已确认，正在执行…」；1 个 `confirm_prompt` POST |
| 768×1024 | 卡片 691×322，x=38；事实 4 列；CTA 186×36，右对齐 y=589 | 卡片 691×352；CTA 129×36 禁用；2 个按钮 | 剪贴板 38 字符；卡片尺寸同待确认 | 卡片 691×308；0 按钮；状态行同上；1 个 POST |
| 1440×900 | 卡片 760×322，x=472；事实 4 列；CTA 186×36，右对齐 y=589 | 卡片 760×352；CTA 129×36 禁用 | 剪贴板 38 字符 | 卡片 760×308；0 按钮；状态行同上；1 个 POST |

卡片样式：`--surface-panel` + 1px `--line-hairline`、`--radius-md`（14px）、padding 16px；版本 `v1.1` 为 13/20 `--font-mono` `--text-muted`；状态胶囊 12/18 600，warning 三重表达；引用块 `--surface-inset`、左 2px `--line-hover`、`0 8 8 0` 圆角；负面块 `--surface-inset` + danger 图标 + 12/18「避免」标签。

### 超长正文

| 视口 | 结果 |
| --- | --- |
| 320×800 | 卡片 278×552；`.prompt-card-body` `scrollHeight=1286`、`clientHeight=480`、`max-height=480px`、`overflow-y:auto`；CTA 234×36、y=677，仍在卡片内可见；页面 `scrollWidth=320` |
| 768×1024 | 卡片 681×552（消息列受聊天滚动条影响为 681px）；body `scrollHeight=725 > clientHeight=480`；CTA 186×36、y=807 可见；无页面横向滚动 |
| 1440×900 | 卡片 760×552；body `scrollHeight=700 > clientHeight=480`；CTA 186×36、y=683 可见；无页面横向滚动 |

## 截图

| 文件 | 字节 | SHA-256（前 12 位） |
| --- | ---: | --- |
| `v3-320-pending.png` | 36,080 | `e3d0328bd4ab` |
| `v3-768-pending.png` | 48,923 | `06cc3441f2b5` |
| `v3-1440-pending.png` | 64,779 | `0277ba0c5b19` |
| `v3-320-checking.png` | 36,457 | `58e96d0ff99c` |
| `v3-768-checking.png` | 49,986 | `fd4b9faf4518` |
| `v3-1440-checking.png` | 65,952 | `83fd885cb1ae` |
| `v3-320-copied.png` | 35,689 | `963ccf2c9db1` |
| `v3-768-copied.png` | 48,302 | `bc1f622030ea` |
| `v3-1440-copied.png` | 64,359 | `6da7c520951c` |
| `v3-320-confirmed.png` | 35,339 | `5f4b2704b90c` |
| `v3-768-confirmed.png` | 46,980 | `0f195da284dd` |
| `v3-1440-confirmed.png` | 62,948 | `5a1971b32a92` |
| `v3-320-overlong.png` | 33,509 | `6151c98375fc` |
| `v3-768-overlong.png` | 57,844 | `ee3b47a89417` |
| `v3-1440-overlong.png` | 69,128 | `87823b6679e8` |

全部截图 < 200KB，单张最大 69,128 字节，总计 764KB。完整路径、viewport、DPR、SHA-256 见 `v3-browser-result.json` 的 `screenshots` 数组。

## 令牌与实现检查

| 检查 | 结果 |
| --- | --- |
| 变更范围 | 仅 `main/src/pages/ChatPage.vue`、`main/src/composables/useChatFlow.ts`（复制计时 2500→2000ms）与 `tests/t148-v3-prompt-card.spec.ts`；未改 API、后端、路由、依赖、`workspace.css` 或 V4–V6 |
| 版本号 | 从 `promptPreview.request.contract_version` 提取 `prompt-request-1.1.0` → `v1.1`，缺省 `v1`；未改 prompt 生成逻辑 |
| 状态矩阵 | `can_confirm=true` → 待确认；`false` → 检查中；`copied` → 已复制；提交后 → 已确认；正文 >480px → 内部滚动 |
| 复制 | 剪贴板内容与 `positivePrompt` 完全一致；2000ms 后复原；无 toast |
| 确认 | `onConfirmPrompt()` 防连点，提交后立即禁用；成功保留 3 秒快照供截图，随后随 `prompt_preview=null` 收起；一次点击一个 action |
| 只读 | 已确认后动作区移除，正文不再可编辑，追加 `role=status` 执行状态行 |
| 超长 | `max-height:480px; overflow-y:auto`；动作区 `position:sticky; bottom:0`；三档视口 CTA 均可见 |
| 响应式 | 320 事实 2 列、CTA 全宽；≥768 事实 4 列、CTA 行内右对齐；无页面横向滚动 |
| 令牌 | 颜色、间距、圆角、字号、行高、阴影均使用 V1 令牌；版本 13px 由 `calc(var(--text-caption) + 1px)` 表达（令牌集无 13px 字号，沿用既有元数据写法） |

## 自动化验证

- `npm test -- --run tests/t148-v3-prompt-card.spec.ts`：7/7 通过。
- `npm test -- --run tests/t122-pure-chat.spec.ts`：6/6 通过。
- 全量测试：20 个文件、170/170 通过。
- 已知基线噪声：`tests/t105-results-history.spec.ts` 仍触发 `main/src/pages/ResultsPage.vue:149` 的 `TypeError: resultsFigure.value?.scrollIntoView is not a function` unhandled rejection；该文件 4/4 用例通过，V3 未触及 ResultsPage。
- `npm run typecheck`：通过（exit 0）。
- `npm run build`：通过；产物 `main/dist/assets/index-BJz8L-V8.css`、`index-BaEbC6z3.js`。

## 边界与已知例外

| 项 | 说明 |
| --- | --- |
| 状态来源 | 卡片状态全部来自既有 `promptPreview`/`busy`/`copied` 与页面本地提交快照；未新增 API 字段或 prompt 版本逻辑。 |
| 确认后收起 | 后端 `safe_preview()` 只返回 `awaiting_confirmation` 行；确认后 `prompt_preview` 变 null，卡片在 3 秒快照后自然收起。该行为是既有数据语义，V3 未改。 |
| 版本 13px | `tokens.css` 无 13px 字号令牌，版本号使用 `calc(var(--text-caption) + 1px)`；视觉结果为 13/20，符合规范值。 |
| 截图统计 | 本轮 mock 为全新进程，`detailGets=15`、`confirm_prompt` action 3 次（每个点击视口一次），无重复请求。 |
| 临时脚本 | `.scratch/v3-ui/` 仅为本机验收工具，未纳入仓库；生产代码不依赖它。 |

## 复核记录

- 实现：`33960ab`；合并：`589689a`（parents `5306a72` + `33960ab`）。
- 浏览器证据：66/66 检查通过，15 张截图，3 个确认 action（320/768/1440 各一次）。
- 代码审查：待全量 ticket 完成后按 `code-review` 流程复核；本报告先记录 V3 验收事实。
