# V1 #146 验收证据报告（V1 证据集）

> 本报告与 `docs/acceptance/evidence/` 下的 5 张截图、1 份原始结果共同构成 V1 验收证据集。

- 验收分支：`codex/issue-146-integration`
- 验收 HEAD：`baea5cca6b30eda642b445550c1abceaf6f8e2bd`（父提交 `d42ef88` + `73abb46`）
- 修复/归档分支：`codex/issue-146-review-fixes`（基于上述验收 HEAD）
- 修复 HEAD：本分支最新提交（提交信息 `fix(v3): address V1 shell review findings (#146)`）
- 应用：`http://127.0.0.1:8090`（Docker `ecommerce-image-workspace:local`，前端由 `main/docker/Dockerfile` 构建）
- 浏览器：Microsoft Edge `Edg/156.0.4314.58`（headless，CDP）
- 采集脚本：`.scratch/v1-browser-check.mjs`（采集用临时脚本，未入库）
- 原始结果：`docs/acceptance/evidence/v1-browser-result.json`（`ok: true`，16/16 检查通过）

## 操作步骤

1. 用 `main/docker/Dockerfile` 重建前端镜像，并用 `main/docker-compose.yml` + `.scratch/v3-local-compose.yml` 重建 frontend 容器。
2. 在 backend 容器内用 `app.auth.cli recover-admin` 重置本地 admin 密码（仅本地验收环境）。
3. 启动 headless Edge，登录 `admin@example.com`，进入工作台。
4. 依次以 1440×900、768×1024、320×800 视口采集 DOM 尺寸/计算样式；在 1440 下按 Tab 聚焦侧栏“新对话”按钮采集焦点环；截图归档到 `docs/acceptance/evidence/`。

## 视口与结果

### 1440×900

| 检查 | 结果 |
| --- | --- |
| 侧栏宽度 | 264px（x=0, w=264, h=900），背景 `rgb(23,25,29)` |
| 顶栏高度 | 48px（x=264, y=0, w=1176, h=48），背景 `rgb(23,25,29)`，下边框 `rgb(44,47,53)`，右内边距 16px |
| 顶栏数量 | 1；旧 `.mobile-bar` 数量 0 |
| 壳体 | `.pure-shell` 1440×900，padding 0，背景 `rgb(246,244,239)` |
| 主面板 | 透明背景 `rgba(0,0,0,0)`，圆角 0 |
| 内容列 | `.composer-shell` 宽 760px（x=472） |
| 横向溢出 | 无（doc/body/inner 均 1440） |
| 侧栏焦点 | Tab 后 `.new-chat-button`：outline 2px `rgb(207,230,242)`，offset 2px，`:focus-visible` 命中 |
| 状态胶囊 | 顶栏右侧，24px 高（x=1310, y=12, w=114, h=24） |

### 768×1024

| 检查 | 结果 |
| --- | --- |
| 顶栏数量/高度 | 1 个 `.workspace-topbar`，48px；`.mobile-bar` 0 个（未出现 52+68 双顶栏） |
| 侧栏 | 不常驻（sidebarCount=0），抽屉默认关闭（drawerCount=0） |
| 壳体/主面板 | 768×1024，padding 0；主面板透明、圆角 0 |
| 横向溢出 | 无（doc/body/inner 均 768） |
| 状态胶囊 | 顶栏右侧，24px 高（x=638, y=12, w=114, h=24） |

### 320×800

| 检查 | 结果 |
| --- | --- |
| 顶栏高度 | 44px（w=320, h=44） |
| 顶栏胶囊 | 不存在 |
| 行内胶囊 | 存在，`position: static`，24px 高，x=16, y=52（顶栏底 44 之后） |
| 流内顺序 | 顶栏 0–44 → 胶囊 52–76 → `.chat-scroll` 从 76 开始；胶囊不是 absolute |
| 横向溢出 | 无（doc/body/inner 均 320） |

## 截图

- `docs/acceptance/evidence/v1-1440-shell.png` — 1440 壳体/侧栏/顶栏/内容列
- `docs/acceptance/evidence/v1-1440-focus-sidebar.png` — 1440 侧栏键盘焦点环
- `docs/acceptance/evidence/v1-768-single-topbar.png` — 768 单顶栏
- `docs/acceptance/evidence/v1-768-drawer-closed.png` — 768 抽屉关闭基线（为 V6 留档；当前状态与单顶栏截图一致）
- `docs/acceptance/evidence/v1-320-capsule-inflow.png` — 320 顶栏 44 + 行内胶囊

## 令牌与全局检查（`main/src`，排除 `tokens.css`）

| 检查 | 结果 |
| --- | --- |
| `var(--color-*)` 引用 | 0 |
| 6 位十六进制颜色 | 0 |
| `color: rgba(` | 0 |
| `outline: 0/none` | 0 |
| 8/9/10/11px 字号 | 0 |
| 未定义的 `var(--x)` | 0（由 V1 测试脚本遍历校验） |

## 自动化验证

- `npm test -- --run tests/v1-tokens-shell.spec.ts`：79/79 通过。
- 全量测试：156 通过 / 2 失败（共 158）；失败项 `t125-task-status`（基线同样失败的 160ms busy 时序抖动）与 `t128-responsive-keyboard`（基线既有失败）；`t105-results-history` 仍有既有的 `scrollIntoView` unhandled 错误，但该文件 4/4 用例通过。
- `npm run typecheck`：通过（exit 0）。
- `vite build --config main/vite.config.ts`：通过（91 modules）。

## §11 缺陷覆盖

- #1 ResultsPage 未定义令牌：已替换为 `--space-5` / `--text-caption` / `--text-section`。
- #3 `.workspace-shell` 死代码块：已删除。
- #4 768–1023 双顶栏（壳层部分）：旧 `.mobile-bar` 移除，统一单个 `.workspace-topbar`；JS 断点阈值留给 V6。
- #5 状态胶囊渐变/多重阴影/呼吸动画：已重写为 24px 图标+文字胶囊，`role="status" aria-live="polite"`，仅 120ms 颜色/透明度过渡。
- #2 抽屉 `!important` 与 #4 JS 阈值：按工单边界保留给 V6。
