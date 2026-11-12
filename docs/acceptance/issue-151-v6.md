# V6 输入区与移动抽屉（#151）验收记录

- 验收分支：`visual/v3`
- 验收 HEAD：`539f696`（`merge: v6 composer and mobile drawer (#151)`）
- 实现提交：`9e52e58`（`feat(chat): add V6 composer and mobile drawer (#151)`）
- 合并提交：`539f696a877762ae8be2a16e7b498ae991332fe2`
- 基线：`5245362`（V5 验收记录提交）
- 应用地址：`http://127.0.0.1:8101`
- Mock API：`http://127.0.0.1:8796`（`.scratch/v6-ui/mock-api.mjs`）
- 截图与原始结果：`docs/acceptance/evidence/v6-*.png`、`docs/acceptance/evidence/v6-browser-result.json`
- 浏览器：Microsoft Edge `Edg/156.0.4314.58`，headless CDP；Windows 10，DPR 1，OS 缩放 100%
- 视口：320×800、768×1024、1440×900
- 字体：页面实际匹配 `"Noto Sans SC"`

## 结论

V6 输入区与移动抽屉通过验收：760px 内容列、面板/发丝线/14px 圆角/焦点环、36×36 发送键、8 行内部滚动、IME Enter 不发送、320 发送键可见、附件 34×42 预览与错误提示、264px 单层抽屉、焦点进入/陷阱/回焦、Esc/遮罩/关闭、背景 `inert` 与滚动锁均符合 #151 与 `screen-specs-v3.md` §7–§8。浏览器自动检查 **77/77** 通过，16 张截图均小于 200KB，三个视口均无页面级横向滚动。

## 操作步骤

1. 在 `visual/v3` 的 `539f696` 上执行生产构建（使用 `.scratch/v2-ui/no-exec.cjs` 规避本机 Vite `spawn EPERM`）。
2. 启动 V6 mock（8796）与静态服务（8101，代理 `/api` 到 8796）。
3. 用 Edge headless CDP 依次设置 320×800、768×1024、1440×900 视口。
4. 加载项目 `99999999-aaaa-4aaa-8aaa-999999999999` 的对话页，核对输入区 idle 几何、发送键禁用、页面横向滚动与 topbar 数量。
5. 聚焦文本域，核对整体焦点环（2px ring + 2px offset）与焦点位置。
6. 输入文案并发送，核对 busy 态发送键「处理中…」、仅一行原因 caption、无 toast、输入仍可编辑。
7. 选择参考图，核对上传失败 `role=alert`、34×42 预览、单行文件名和 24px 移除按钮。
8. 在 320 输入 10 行，核对文本域封顶 212px（8×24）且内部滚动，截图 `v6-320-composer-eight-lines.png`。
9. 模拟 IME composition Enter 不发送、普通 Enter 发送，记录消息计数。
10. 在 320、768 打开移动抽屉，核对宽度、scrim、关闭按钮、`role=dialog`/`aria-modal`、焦点进入、背景 `inert`、滚动锁和 Esc 回焦。
11. 在 1024 核对常驻侧栏、无抽屉入口、仅一个 topbar。
12. 汇总 DOM 计算样式、几何、请求统计并写入 `v6-browser-result.json`。

## 输入区结果

| 视口 | 输入区宽度 | 面板 | 内边距 | 圆角 | 发送键 | 页面横向滚动 |
| --- | ---: | --- | --- | ---: | ---: | --- |
| 320×800 | 288px，x16 | `--surface-panel` + 1px `--line-hairline` + `--shadow-composer` | 12px 16px | 14px | 36×36 | 无（scrollWidth 320） |
| 768×1024 | 720px，x24 | 同上 | 12px 16px | 14px | 36×36 | 无（scrollWidth 768） |
| 1440×900 | 760px，x472 | 同上 | 12px 16px | 14px | 36×36 | 无（scrollWidth 1440） |

- 文本域：`font-size: 15px`、`line-height: 24px`、`min-height: 44px`、`max-height: 212px`、`overflow-y: auto`；三个视口 idle 均无页面级横向滚动。
- 焦点环：`outline: 2px solid rgb(30, 90, 115)`、`outline-offset: 2px`，边框变为 `--line-control`（`rgb(143, 140, 132)`）。
- 发送键默认禁用：36×36，背景 `--surface-inset`（`rgb(239, 237, 230)`），前景 `--text-muted`，`opacity: 1`，不使用整体透明度。
- busy 态：发送键 `aria-busy="true"`、`title`/`data-label` 均为「处理中…」，输入仍可编辑；`composer-hint` 仅一行「正在生成，完成后可发送」，无错误块、无 toast。
- 8 行封顶：320 文本域 `height: 212px`，`scrollHeight 260 > clientHeight 212`，输入区整体 238px 高，发送键仍可见。
- IME：composition Enter 前后消息数均为 3；compositionend 后普通 Enter 使消息数变为 4，即组合输入 Enter 不发送、普通 Enter 发送。
- 附件错误：`role="alert"` 文本「参考图上传失败，请重试。」；预览 34×42，移除按钮 24×24，文件名 `fixture.png` 单行显示。
- 三个视口均只有 1 个 `.workspace-topbar`。

## 移动抽屉结果

| 视口 | 面板 | 关闭按钮 | scrim | 圆角 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 320×800 | 264×800，x56 | 44×44，x260/y16 | `rgba(12, 14, 17, 0.4)` | 右侧 19px | `role=dialog`、`aria-modal=true`、背景 `inert`、`body overflow:hidden` |
| 768×1024 | 264×1024，x504 | 44×44，x708/y16 | `rgba(12, 14, 17, 0.4)` | 右侧 19px | 同上 |

- 打开后焦点落在关闭按钮；Tab 仅在抽屉内循环；Esc、遮罩、关闭按钮三条路径均关闭（专项测试覆盖），关闭后焦点返回「打开侧栏」触发器。
- 关闭后背景 `inert` 移除、`body` 滚动恢复，焦点回到触发器。
- 1024×900：侧栏常驻，无「打开侧栏」按钮、无抽屉层，仅一个 topbar。
- 抽屉宽度使用 `min(264px, calc(100vw - 32px))`；`main/src/styles/workspace.css` 中抽屉规则无 `!important`（现存 `!important` 仅为 `.sr-only` 工具类，887–894 行）。

## 截图

| 文件 | 字节 | SHA-256（前 16 位） |
| --- | ---: | --- |
| v6-320-composer-idle.png | 12,876 | 1e47b8a75b3252a1 |
| v6-320-composer-focus.png | 14,026 | 04cd970b8de9a3f7 |
| v6-320-composer-blocked.png | 17,230 | d3f934046eae51b5 |
| v6-320-composer-attachment-error.png | 18,916 | 230943b1d6f721e0 |
| v6-768-composer-idle.png | 15,541 | b475a7870b58d565 |
| v6-768-composer-focus.png | 16,723 | ab1fdc71861ac381 |
| v6-768-composer-blocked.png | 20,218 | b374b97b714c470e |
| v6-768-composer-attachment-error.png | 22,118 | 47c094528cd1b960 |
| v6-1440-composer-idle.png | 30,669 | 7a8884718453d6f2 |
| v6-1440-composer-focus.png | 31,745 | 9525469cb0db0169 |
| v6-1440-composer-blocked.png | 35,331 | a3ed3ea9c2f96a45 |
| v6-1440-composer-attachment-error.png | 37,343 | a43ee5501c1d3870 |
| v6-320-composer-eight-lines.png | 14,032 | 146ec2c9a1cd3c27 |
| v6-320-drawer-open.png | 30,150 | ec469027ae3e52d6 |
| v6-768-drawer-open.png | 53,084 | c1d17b7c8f3cf397 |
| v6-1024-no-drawer.png | 58,874 | f3ae41c413c9ba11 |

## 自动化验证

- V6 专项：`tests/t151-v6-composer-drawer.spec.ts` 6/6 通过。
- 相关回归：`t122`、`t124`、`t125`、`t128`、`v1-tokens-shell` 共 17/17 通过。
- 全量：23 个文件 / 195 个测试通过。
- 已知基线：`tests/t105-results-history.spec.ts` 的 `TypeError: resultsFigure.value?.scrollIntoView is not a function`（`main/src/pages/ResultsPage.vue:149`）为存量未处理 rejection，不影响验收结论。
- `npm run typecheck` 通过；`npm run build` 通过（`index-h_2e6Kof.css`、`index-K5SOEaYo.js`）。

## 边界确认

- 未修改上传、发送、重试逻辑或 blocked-reason 来源。
- 未新增附件类型；附件预览、文件名、移除按钮均为既有数据。
- 未修改 V2–V5 的消息流、需求卡、提示词卡、执行状态、结果卡与灯箱。
- 抽屉改动集中在 `AccessibleDrawer.vue`（背景 inert/过渡）、`SideDrawer.vue`（264px）、`workspace.css`（抽屉段）与 ChatPage 输入区。
