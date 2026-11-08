# V4 #149 验收证据报告（执行、失败与恢复）

> 本报告与 `docs/acceptance/evidence/` 下的 15 张截图、1 份原始结果共同构成 V4 验收证据集。

- 验收分支：`visual/v3`
- 验收 HEAD：`06d854c`（合并 `codex/issue-149-v4` 后的合并提交）
- 实现提交：`0d30e50`（`feat(chat): add v4 execution states (#149)`）
- 基线：`fb382e7`（V3 验收报告合并后的 `visual/v3`）
- 应用：`http://127.0.0.1:8099`（本地构建 `main/dist`，由 `.scratch/v2-ui/serve.mjs` 提供；`/api` 代理到 `.scratch/v4-ui/mock-api.mjs` 的 `127.0.0.1:8794`）
- 浏览器：Microsoft Edge `Edg/156.0.4314.58`（headless，CDP）
- 采集脚本：`.scratch/v4-ui/run-browser.mjs`（临时验收脚本，未入库）
- 原始结果：`docs/acceptance/evidence/v4-browser-result.json`（`ok: true`，66/66 检查通过，10 个显式步骤）

## 验收结论

V4 的五类任务状态在同一消息流位置通过验证：执行中只显示一条静态状态行，没有气泡、打字点、百分比或内部阶段；待确认动作使用 20×20 复选框，Space 可切换，未勾选时发送不会产生请求；失败状态在原位以图标 + 单行原因呈现，只有服务端返回 `recover_generation` 时才出现恢复动作，失败后无自动重试；结果未知显示「结果未知，正在核实」，只保留服务端给出的核实/获取动作，不出现「已取消」；恢复成功在原位更新为「已恢复 / 这一轮已完成。」，不新增第二条状态消息，且每次恢复只产生一个 action POST。三档视口均无页面级横向滚动。

## 操作步骤

1. 在 `visual/v3` 的 `06d854c` 上执行 `npm run build`（使用 `.scratch/v2-ui/no-exec.cjs` 规避本机 Vite `spawn EPERM`）。
2. 启动 mock API（`node .scratch/v4-ui/mock-api.mjs`，8794，`ACTION_DELAY_MS=1200`）与静态服务器（`PORT=8099 API_TARGET=http://127.0.0.1:8794 node .scratch/v2-ui/serve.mjs`）。
3. 启动 headless Edge 与 CDP，依次以 320×800、768×1024、1440×900 视口加载五个项目。
4. 执行中：核对单条静态状态行、inset 卡片、图标与文字；确认无气泡/打字点/百分比/内部阶段。
5. 待确认：勾选 20×20 复选框并截图；确认仅勾选不产生 POST，未勾选时发送按钮禁用。
6. 失败：核对原位失败原因、无动作（硬失败 fixture）；等待 900ms 确认无自动重试 POST。
7. 结果未知：核对「结果未知，正在核实」与「核实原任务 / 获取原结果」两个服务端动作，确认无「已取消」。
8. 恢复：记录点击前任务区 top；点击「恢复原任务」；等待并截图「已恢复 / 这一轮已完成。」；确认任务区仍在原位、0 按钮、1 个 action POST。
9. 采集 DOM 计算样式、尺寸、页面 `scrollWidth`、网络请求（含 `requestId`）与 mock action 统计。
10. 归档 `v4-browser-result.json` 与 15 张截图。

## 视口与结果

### 执行中 / 待确认 / 失败 / 结果未知 / 已恢复

| 视口 | 执行中 | 待确认 | 失败 | 结果未知 | 已恢复 |
| --- | --- | --- | --- | --- | --- |
| 320×800 | 卡片 288×46，x=16，y=297；0 按钮；`is-executing` | 卡片 288×299；复选框 20×20；1 个动作按钮；勾选后仍未发送 | 卡片 288×94；0 按钮；硬失败原因单行 | 卡片 278×404；2 个只读动作；无「已取消」 | 卡片 288×72；0 按钮；top 297 不变；1 个恢复 POST |
| 768×1024 | 卡片 691×46，x=38，y=254 | 卡片 691×245；复选框 20×20 | 卡片 691×72 | 卡片 691×328；2 个只读动作 | 卡片 691×72；top 254 不变 |
| 1440×900 | 卡片 760×46，x=472，y=254 | 卡片 760×245 | 卡片 760×72 | 卡片 760×328 | 卡片 760×72；top 254 不变 |

卡片样式：`--surface-inset` 背景、`--radius-md`（14px）、padding `--space-3` / `--space-4`（12/16）、正文 14/22、`--text-secondary`。执行中与未知状态使用 `--state-info`，失败使用 `--state-danger`，恢复使用 `--state-success`。动作按钮最小高度 36px、圆角 pill；复选框 20×20、1px `--line-control` 边框、`--radius-sm`、选中为 `--action-primary` 底 + 16px 勾。所有状态卡片无 `.message-bubble` 与 `.typing-dots`。

## 截图

| 文件 | 字节 | SHA-256（前 12 位） |
| --- | ---: | --- |
| `v4-320-executing.png` | 23,894 | `43d44d942c4b` |
| `v4-768-executing.png` | 27,789 | `9b00540097ca` |
| `v4-1440-executing.png` | 42,556 | `54a2788057a3` |
| `v4-320-confirm.png` | 42,234 | `67f246b1e5ea` |
| `v4-768-confirm.png` | 47,041 | `2fed527ad1a4` |
| `v4-1440-confirm.png` | 62,996 | `a8c1325f8cf9` |
| `v4-320-failed.png` | 27,686 | `4b392fcf1a8f` |
| `v4-768-failed.png` | 31,777 | `0f0a3ab65820` |
| `v4-1440-failed.png` | 46,873 | `a7626d6bd1e5` |
| `v4-320-unknown.png` | 46,969 | `3bfe1275edfa` |
| `v4-768-unknown.png` | 52,143 | `8e1b7f9a3c2c` |
| `v4-1440-unknown.png` | 68,305 | `f747721110ef` |
| `v4-320-recovered.png` | 24,803 | `9198418a240c` |
| `v4-768-recovered.png` | 28,640 | `d16d58f35e19` |
| `v4-1440-recovered.png` | 43,540 | `8ea88ee2c692` |

全部截图 < 200KB，单张最大 68,305 字节，总计约 662KB。完整路径、viewport、DPR、SHA-256 见 `v4-browser-result.json` 的 `screenshots` 数组。

## 令牌与实现检查

| 检查 | 结果 |
| --- | --- |
| 变更范围 | 仅 `main/src/pages/ChatPage.vue` 与新增 `tests/t149-v4-execution-states.spec.ts`；未改 API、后端、类型、`useChatFlow.ts`、依赖或 V5/V6 |
| 状态来源 | UI 从既有 `taskStatusText`、`taskReason`、`taskActions`、`isWorkingStatus` 派生；未新增 API 字段或状态机 |
| 执行中 | 一条静态状态行；无 `.message-bubble`、`.typing-dots`、百分比、内部阶段或循环动画 |
| 确认 | 20×20 复选框；Space 切换；`sendDisabled` 复用既有 `pendingTaskConfirmationMissing`；未勾选发送不产生请求 |
| 失败 | `taskReason.severity === 'failed'` 原位替换；原因来自服务端 `message`；无服务端动作时不渲染按钮；无自动重试 |
| 结果未知 | `remote_unknown` 显示「结果未知，正在核实」；动作只来自 `available_actions` 的 `verify_generation` / `retrieve_original_result`；不声称「已取消」 |
| 已恢复 | 恢复 action 返回 `completed` 后在同一 region 显示「已恢复 / 这一轮已完成。」；不新增消息；恢复后 0 按钮 |
| 不可用图 | 使用既有 `resultUnavailableText` 语义；inset + danger 图标 + 单行文本；未改文案或替换图片逻辑 |
| 动效 | `prefers-reduced-motion` 下 task card/icon/button/checkbox `animation:none; transition:none`；无 typing keyframes |
| 请求 | 三档恢复各 1 个 `recover_generation` POST；确认、失败、未知状态均无 POST；mock 统计 `actions=3` |

## 自动化验证

- `npm test -- --run tests/t149-v4-execution-states.spec.ts`：6/6 通过。
- `npm test -- --run tests/t125-task-status.spec.ts`：11/11 通过。
- 全量测试：21 个文件、176/176 通过。
- 已知基线噪声：`tests/t105-results-history.spec.ts` 仍触发 `main/src/pages/ResultsPage.vue:149` 的 `TypeError: resultsFigure.value?.scrollIntoView is not a function` unhandled rejection；该文件用例通过，V4 未触及 ResultsPage。
- `npm run typecheck`：通过（exit 0）。
- `npm run build`：通过；产物 `main/dist/assets/index-D1vSa5Td.css`、`index-CGTCKn8g.js`。

## 边界与已知例外

| 项 | 说明 |
| --- | --- |
| 失败 fixture | mock 使用受控 `run-v4` 与 `V4_HARD_FAILURE` / `V4_REMOTE_UNKNOWN` / `V4_TECHNICAL_FAILURE` 原因；未新增生产错误映射。 |
| 恢复语义 | 仅调用服务端返回的 `recover_generation`；没有自动重试、轮询或重新生成逻辑。 |
| 待确认截图 | `confirm` 场景为「授权已失效 + 待确认修改提示词」fixture；截图为勾选后状态，未点击发送，网络统计为 0 POST。 |
| 旧错误来源 | `taskActionButton:disabled { opacity:.5 }` 是既有任务动作样式，V4 未改其语义；状态卡本身无透明度。 |
| 临时脚本 | `.scratch/v4-ui/` 仅为本机验收工具，未纳入仓库；生产代码不依赖它。 |

## 复核记录

- 实现：`0d30e50`；合并：`06d854c`（parents `fb382e7` + `afc9f41`）。
- 浏览器证据：66/66 检查通过，15 张截图，3 个恢复 action（320/768/1440 各一次）。
- 代码审查：待全量 ticket 完成后按 `code-review` 流程复核；本报告先记录 V4 验收事实。
