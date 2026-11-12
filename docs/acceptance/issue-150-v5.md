# V5 结果卡与居中灯箱（#150）验收记录

- 验收分支：`visual/v3`
- 验收 HEAD：`b261329`（`fix(chat): render v5 result media at 3:4 (#150)`）
- 实现提交：`267cbe3`（`feat(chat): add V5 result cards and centered lightbox (#150)`）
- 合并提交：`f3e6935`（`merge: v5 result cards and lightbox (#150)`）
- 基线：`cb39004`（V4 验收记录提交）
- 应用地址：`http://127.0.0.1:8100`
- Mock API：`http://127.0.0.1:8795`（`.scratch/v5-ui/mock-api.mjs`）
- 截图与原始结果：`docs/acceptance/evidence/v5-*.png`、`docs/acceptance/evidence/v5-browser-result.json`
- 浏览器：Microsoft Edge `Edg/156.0.4314.58`，headless CDP；Windows 10，DPR 1，OS 缩放 100%
- 视口：320×800、768×1024、1440×900
- 字体：页面实际匹配 `"Noto Sans SC"`

## 结论

V5 结果卡与居中灯箱通过验收：单版本/多版本网格、当前/历史/最新徽标、不可用图、禁用下载、居中灯箱、焦点陷阱、三种关闭路径、背景 `inert` 与滚动锁均符合 #150 与 `screen-specs-v3.md` §6。浏览器自动检查 **60/60** 通过，8 张截图均小于 200KB，无页面级横向滚动。

## 操作步骤

1. 在 `visual/v3` 的 `b261329` 上执行生产构建（使用 `.scratch/v2-ui/no-exec.cjs` 规避本机 Vite `spawn EPERM`）。
2. 启动 V5 mock（8795）与静态服务（8100，代理 `/api` 到 8795）。
3. 用 Edge headless CDP 依次设置 320×800、768×1024、1440×900 视口。
4. 加载多版本项目 `77777777-bbbb-4bbb-8bbb-777777777777`，核对三张结果卡、网格列数、徽标、不可用图与禁用下载，截图 `v5-{width}-results.png`。
5. 加载单版本项目 `66666666-aaaa-4aaa-8aaa-666666666666`，核对单卡占一个网格项，截图 `v5-{width}-single.png`。
6. 加载空历史项目 `88888888-cccc-4ccc-8ccc-888888888888`，确认无结果区。
7. 在 320 与 1440 打开第一张结果图灯箱，核对 `role=dialog`/`aria-modal`、焦点、scrim、尺寸、背景 `inert` 与滚动锁，截图 `v5-{width}-lightbox.png`。
8. 分别用 Esc、遮罩、关闭按钮关闭，确认焦点返回触发控制。
9. 记录 DOM 计算样式、网格/卡片/图片/灯箱尺寸、页面 `scrollWidth` 与 mock 请求统计并归档 JSON。

## 视口与布局结果

| 视口 | 结果区宽度 | 网格 | 卡片宽度 | 图片尺寸（3:4） | 页面横向滚动 |
| --- | --- | --- | --- | --- | --- |
| 320×800 | 278px | 1 列 | 278px | 252×336 | 无 |
| 768×1024 | 681px | 2 列，gap 16px | 332.6px | 307×409 | 无 |
| 1440×900 | 760px | 2 列，gap 16px | 372px | 346×461 | 无 |

- 多版本项目每个视口渲染 3 张卡：历史版本（无图，显示「该版本图片不可用」）、当前版本（有图，下载禁用）、最新已发布（有图，下载可用）。
- 卡片：`padding: 12px`，`--surface-panel` + 1px `--line-hairline` + `--radius-md`；图片 `object-fit: cover`、实际渲染严格 3:4；徽标分别为「历史版本 / 当前版本 / 最新已发布」。
- 禁用下载为原生 `button[disabled]` + `aria-disabled="true"`，颜色使用 `--surface-inset`/`--text-muted`，不依赖整体透明度；可用下载为链接，44×44 命中高度。
- 单版本项目只占一个网格项，不拉伸为整行。

## 灯箱结果

| 视口 | 面板 | 图片 | 关闭按钮 | 内边距 | scrim |
| --- | --- | --- | --- | --- | --- |
| 320×800 | 288×379，x16/y211 | 272×363 | 44×44，x252/y219 | 8px | `rgba(12, 14, 17, 0.4)` |
| 1440×900 | 605×798，x418/y51 | 581×774（86vh 上限） | 44×44，x970/y59 | 12px | `rgba(12, 14, 17, 0.4)` |

- `role="dialog"`、`aria-modal="true"`；打开后焦点落在关闭按钮，Tab 仅在灯箱内循环。
- Esc、点击遮罩、点击关闭按钮三条路径均关闭，关闭后焦点返回结果卡触发链接。
- 背景应用 `inert`，`document.body` 设置 `overflow: hidden`；打开期间背景不可点击、不可滚动。
- 首帧/关闭动画 180ms；`prefers-reduced-motion: reduce` 时动画为 0。

## 截图

| 文件 | 字节 | SHA-256（前 16 位） |
| --- | ---: | --- |
| v5-320-results.png | 28,157 | 20880993f22271f7 |
| v5-320-single.png | 28,007 | dabdd49887b06803 |
| v5-320-lightbox.png | 30,367 | ddef48f24a1a3e2b |
| v5-768-results.png | 53,691 | d237ca3acba196ae |
| v5-768-single.png | 41,790 | 3eba27a009fcbc20 |
| v5-1440-results.png | 58,447 | 355e2e96f7b6d0f2 |
| v5-1440-single.png | 50,491 | 24096857024b5349 |
| v5-1440-lightbox.png | 43,616 | c740094627cae0f4 |

## 自动化验证

- V5 专项：`tests/t150-v5-result-lightbox.spec.ts` 13/13 通过。
- 附件回归：`tests/t124-attachment.spec.ts` 7/7 通过。
- 全量：22 个文件 / 189 个测试通过。
- 已知基线：`tests/t105-results-history.spec.ts` 的 `TypeError: resultsFigure.value?.scrollIntoView is not a function`（`main/src/pages/ResultsPage.vue:149`）为存量未处理 rejection，不影响退出码之外的验收结论。
- `npm run typecheck` 通过；`npm run build` 通过（94 modules）。

## 边界确认

- 未修改图片 URL、发布、历史数据或版本切换请求。
- 未重做 `ResultsPage.vue` 布局。
- 未实现 V6 输入区/移动抽屉。
- 灯箱组件为 `main/src/components/ImageLightbox.vue`，仅由 ChatPage 结果卡与附件缩略图复用。
