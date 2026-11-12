# V7 清理旧令牌、死组件与旧样式（#152）验收记录

- 验收分支：`visual/v3`
- 验收 HEAD：`d84923c`（`merge: v7 cleanup and token convergence (#152)`）
- 实现提交：`9953cbd`（`refactor(v7): remove legacy token aliases and dead workspace chain (#152)`）
- 令牌收敛修复：`bff53cf`（`fix(v7): tokenize remaining pill radii (#152)`）
- 基线：`4203046`（V6 验收记录提交）
- 应用地址：`http://127.0.0.1:8102`（empty/messages）、`http://127.0.0.1:8104`（result）
- Mock API：`http://127.0.0.1:8791`（V2 fixtures）、`http://127.0.0.1:8795`（V5 result fixtures）
- 截图与原始结果：`docs/acceptance/evidence/v7-{320,768,1440}-{empty,messages,result}.png`、`v7-browser-result.json`、`v7-result-browser-result.json`
- 浏览器：Microsoft Edge `Edg/156.0.4314.58`，headless CDP；Windows 10，DPR 1，OS 缩放 100%
- 视口：320×800、768×1024、1440×900
- 字体：页面实际匹配 `"Noto Sans SC"`

## 结论

V7 完成旧令牌别名、死组件、`useWorkspace` 死状态与 `workspace.css` 旧段落的删除，V1–V6 的视觉与行为保持不变。V6 基线对比中 empty 与 result 状态 9 张中 6 张逐像素一致，messages 状态 3 张仅时钟 `HH:mm` 字形区域存在动态差异；符合 #152 的“仅抗锯齿/动态内容差异”例外。V7 专项 9/9、全量 24 文件 203 测试通过，typecheck/build 通过。

## 操作步骤

1. 在 `visual/v3` 的 `d84923c` 上执行生产构建（使用 `.scratch/v2-ui/no-exec.cjs` 规避本机 Vite `spawn EPERM`）。
2. 用 V6 基线 `4203046` 的构建在三个视口捕获 empty/messages/result，保存到 `.scratch/v7-baseline/`。
3. 启动 V7 构建的静态服务 8102 + V2 mock 8791，捕获 empty/messages，并执行 V2 45 项状态检查。
4. 启动 V7 构建的静态服务 8104 + V5 mock 8795，捕获 result，并执行 V5 60 项结果检查。
5. 用 Pillow 逐像素比较 V7 截图与 V6 基线，统计差异像素数与包围盒；对 messages 仅允许 `HH:mm` 区域差异。
6. 运行组件/别名/色值/焦点/字号 grep 门禁与 `t152-v7-cleanup` 单测。
7. 在 `d84923c` 运行全量测试、`npm run typecheck`、`npm run build`。
8. 将截图、哈希、视口、字体与环境写入 `v7-browser-result.json` 与 `v7-result-browser-result.json`。

## 逐像素对比

| 状态 | 视口 | 差异像素 | 差异包围盒 | 结论 |
| --- | --- | ---: | --- | --- |
| empty | 320×800 | 0 | — | 逐像素一致 |
| empty | 768×1024 | 0 | — | 逐像素一致 |
| empty | 1440×900 | 0 | — | 逐像素一致 |
| messages | 320×800 | 93 | 224,573–238,582（15×9） | 仅 `HH:mm` 时钟字形 |
| messages | 768×1024 | 93 | 659,540–674,549（15×9） | 仅 `HH:mm` 时钟字形 |
| messages | 1440×900 | 93 | 1162,540–1176,549（15×9） | 仅 `HH:mm` 时钟字形 |
| result | 320×800 | 0 | — | 逐像素一致 |
| result | 768×1024 | 0 | — | 逐像素一致 |
| result | 1440×900 | 0 | — | 逐像素一致 |

## 截图

| 文件 | 字节 | SHA-256（前 16 位） |
| --- | ---: | --- |
| v7-320-empty.png | 41,179 | 6af2e1bef16c9c83 |
| v7-768-empty.png | 42,831 | c4f057b67be105ec |
| v7-1440-empty.png | 64,528 | 2d18b9ad37034c32 |
| v7-320-messages.png | 29,243 | 757097d2d4c11fd7 |
| v7-768-messages.png | 34,817 | bd27392c2d5bd144 |
| v7-1440-messages.png | 50,281 | 94b353d91c4d21fe |
| v7-320-result.png | 26,793 | 89c67388146deb19 |
| v7-768-result.png | 52,104 | ba1f3d4df64a1f0e |
| v7-1440-result.png | 56,690 | 4f2eaed92205e1ab |

## 自动化验证

- V7 专项：`tests/t152-v7-cleanup.spec.ts` 9/9 通过。
- 全量：24 个文件 / 203 个测试通过（`--pool=threads --maxWorkers=4`）。
- `npm run typecheck` 通过。
- `npm run build` 通过：94 modules，`index-Dckf2OqR.css` 84.17 kB，`index-Besp5zSj.js` 258.23 kB。
- 已知基线：`tests/t105-results-history.spec.ts` 的 `TypeError: resultsFigure.value?.scrollIntoView is not a function`（`main/src/pages/ResultsPage.vue:149`）为存量未处理 rejection，不影响 V7 验收结论。

## CI 门禁

以下命令在 `d84923c` 上运行：

```bash
git grep -nE 'ConversationPanel|ComposerPanel|BriefCard|HistoryPreview|ModelStatusDrawer|ProjectDrawer|ReferenceUploader|WorkflowPanel' -- main/src
# （空，exit 1）

git grep -nE -- '--(font-family|text-input|text-input-line|text-product|text-product-line|text-small|text-heading|color-[a-z-]+|radius-control|radius-panel|radius-card|radius-image|shadow-panel|conversation-width)' -- main/src
# （空，exit 1）

git grep -nE '#[0-9a-fA-F]{6}\b' -- main/src ':!main/src/styles/tokens.css'
# （空，exit 1）

git grep -nE 'outline\s*:\s*(0|none)\b' -- main/src
# （空，exit 1）

git grep -nE 'font-size\s*:\s*(1[01]|[0-9])(\.[0-9]+)?px' -- main/src
# （空，exit 1）
```

`t152-v7-cleanup.spec.ts` 同时验证：每个 `var(--x)` 都有定义；`--color-*` 不再出现在组件样式；8 个死组件文件均已删除；`useWorkspace` 不再包含 `projectDrawerOpen`/`openProjectDrawer`/`closeProjectDrawer`。

### 保留的既有例外

V7 的边界是“只删不改”，以下值与 V1–V6 既有渲染绑定，改动会改变真实几何，因此未在 V7 中强行替换；记录为待后续视觉票处理：

- `main/src/pages/ChatPage.vue:1861` `.result-prompt` 的 `border-radius: 10px`（结果卡折叠提示词块）。
- `main/src/pages/ChatPage.vue:1891` `.chat-error` 的 `border-radius: 12px`（错误块）。
- `main/src/components/AuthFrame.vue:278` 的 `@media (max-width: 900px)`。
- `main/src/pages/AccountSecurityPage.vue:635`、`AdminAccountsPage.vue:854` 的 `@media (max-width: 640px)`；`AdminAccountsPage.vue:952` 的 `@media (max-width: 720px)`。

V7 直接影响的 `workspace.css` 已从 2794 行缩减到 417 行，V1–V6 使用的规则保留；旧 `workspace.css` 中无引用的组件段落已删除。

## 边界确认

- 未修改后端、路由、鉴权、上传/发送/重试逻辑。
- 未改动 V1–V6 的视觉、交互或数据语义；empty/messages/result 的像素对比见上表。
- 删除组件前已用 `git grep` 确认零引用：`ConversationPanel`、`ComposerPanel`、`BriefCard`、`HistoryPreview`、`ModelStatusDrawer`、`ProjectDrawer`、`ReferenceUploader`、`workflow/WorkflowPanel`。
- `AppHeader.vue` 保留（账户页 `AccountSecurityPage.vue` 使用）。
- 旧令牌别名迁移点：`--conversation-width`→`--conversation-max`，`--shadow-panel`→`--shadow-composer`，`--text-input`→`--text-reading`，`--text-product`→`--text-page`，`--font-family`→`--font-sans`。
