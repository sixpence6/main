# T19 界面验收报告（Issue #143）

## 结论

T19 在当前集成分支 `visual/v3` 的 `5fc0dd5` 上通过。

| 项目 | 结果 |
| --- | --- |
| 验收标准 | **8/8** |
| 验收步骤 | **96/96** |
| 失败步骤 | 0 |
| 已知缺陷 | 0 |
| Runner 结论 | `ok: true` |
| 规范基线 | 9 张 PNG，519,542 B（每张 ≤200 KB，总计 <2 MB） |
| 验收环境 | Edge `Edg/156.0.4314.58`，Windows 10.0.19045，headless CDP |
| 验收对象 | `main/dist`，`visual/v3@5fc0dd5` |
| 受控 API | `.scratch/t19-ui/mock-api.mjs`，应用 8105 / mock 8106 |

本报告是界面层验收，不把受控替身、CDP 视口或模拟输入法当作真实浏览器设备、真实手机软键盘或真实图片服务的证明；相应未知项在“边界与例外”中列出。

## 验收对象与运行

- 分支：`visual/v3`
- 验收 HEAD：`5fc0dd5c3d6571a41f1d908382b1d318cc245fd4`（`merge: apply V3-V7 review fixes`）
- 前置实现：V1 `7bc94c5`、V2 `5306a72`、V3 `589689a`、V4 `06d854c`、V5 `f3e6935`、V6 `539f696`、V7 `d84923c`
- 最终审查修复：`3b8b7c4`（V3–V7 review findings + T19 读取重试状态缺陷）
- T19 前一项门禁修复：`5bb618b`（`.new-content-button` 令牌化）
- 构建：`NODE_OPTIONS="--require D:/Desktop/新建文件夹/.scratch/v2-ui/no-exec.cjs" npm run build`
- 构建产物：`main/dist/assets/index-DjNoueTg.css`、`main/dist/assets/index-BavlKGdi.js`
- 运行：`APP_PORT=8105 MOCK_PORT=8106 node .scratch/t19-ui/run-browser.mjs`
- 验收运行：`run-20261108-235556`
- 基线生成运行：`run-20261108-235258`（同一构建，`--update-baseline`）

## 证据与方法

- Harness：`.scratch/t19-ui/run-browser.mjs`，零依赖 Node + Edge CDP；不引入 Playwright/Cypress。
- Mock：`.scratch/t19-ui/mock-api.mjs`，固定账号 A/B、项目 P1/P3/P4/P5/P6/PB、合成资产；所有请求带 `request_id`/`record_id`，可追踪到 `.scratch/t19-ui/evidence/run-20261108-235556/mock-stats.json`。
- 环境：Edge headless；1440x900@1、768x1024@1、320x800@1、720x450@2（200% 缩放）、1024x900@1、1023x900@1；`prefers-reduced-motion: reduce` 模拟；时区 UTC；字体栈 `system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif`。
- 产物：25 张截图、`report.json`、`mock-stats.json`、`browser.log` 位于 `.scratch/t19-ui/evidence/run-20261108-235556/`；最终报告副本为 `docs/acceptance/evidence/t19-browser-result.json`。
- 规范基线：`docs/acceptance/baselines/t19/` 下 9 张 PNG 与 `README.md`。

复现：

```bash
NODE_OPTIONS="--require D:/Desktop/新建文件夹/.scratch/v2-ui/no-exec.cjs" npm run build
APP_PORT=8105 MOCK_PORT=8106 node .scratch/t19-ui/run-browser.mjs
```

首次生成基线时追加 `--update-baseline`。应用端口 8090 在验收环境被既有 Docker 转发进程占用，因此使用 8105/8106；runner 支持环境变量覆盖。

## 八项验收

| # | 检查 | 结果 | 证据 |
| --- | --- | --- | --- |
| c1 | 空状态：三条建议、点击填充不发、三档视口 | 9/9 | S1.1–S1.8；`1440-empty.png`、`320-empty.png` |
| c2 | 主流程：需求卡→提示词检查/待确认/复制/确认→执行→结果 | 26/26 | S2.1–S5.4；`1440-requirement-pending.png`、`1440-pending-confirmation.png`、`1440-result.png` |
| c3 | 大图：居中、Esc/遮罩/关闭、焦点陷阱与归还、`aria-modal`、body 锁定 | 10/10 | S6.1–S6.10；`1440-result-lightbox.png`、`320-lightbox.png` |
| c4 | 失败恢复：受控失败、仅服务端动作、unknown 仅核实/恢复、原地恢复 | 17/17 | S7.1–S7.17；`1440-failure.png`、`1440-recovering.png`、`1440-recovered.png`、`1440-unknown.png` |
| c5 | 刷新、旧链接、账号切换 | 8/8 | S8.1–S8.8；`1440-refresh.png`、`1440-old-link-404.png`、`login-401.png` |
| c6 | 减少动态：抽屉/大图无位移，时长 ≤120 ms | 5/5 | S10.1–S10.5；`reduced-motion-drawer.png`、`reduced-motion-lightbox.png` |
| c7 | 响应式 320/768/1440：无横向滚动、单顶栏、抽屉 264、侧栏 | 13/13 | S9.1–S9.13；`768-topbar.png`、`768-drawer.png`、`320-composer.png` |
| c8 | 200% 缩放：不裁切不重叠、可见焦点环 | 8/8 | S11.1–S11.8；`1440-zoom200-focus.png` |

关键断言值：

- 空状态 1440：内容列 760 px、左右 offset 0、3 列建议；首屏 composer 自动聚焦；点击建议只填草稿并聚焦，不发请求。
- 提示词：检查中 pill `检查中`，CTA 禁用、复制可用、opacity 1、版本 `v3.0`；待确认 CTA `确认使用以上提示词生成`；复制结果与卡片正文完全一致；确认只产生 1 个 `confirm_prompt` POST；结果只有 1 张当前卡片。
- 大图：`role=dialog`、`aria-modal=true`、焦点进入关闭按钮、Tab/Shift+Tab 不逃逸、Esc/遮罩/关闭均关闭并归还焦点、背景 `inert`、body overflow hidden；320 关闭按钮 44×44。
- 失败：失败块单行且只显示服务端允许的恢复动作；恢复后原位变为执行中再完成；unknown 只显示核实/恢复；unavailable 没有图片和重生成入口；读取错误重试后错误清除。
- 刷新/旧链接/账号：刷新不重复追加消息；账号 B 打开账号 A 旧链接得到 `PROJECT_NOT_FOUND` 且无内容泄漏；无 session 跳转 `/login`。
- 响应式：320/768/1440 均无页面横向滚动；768 单顶栏、抽屉 264；1024 侧栏常驻；1023 使用抽屉。

## MV3-01—14 映射

本表只把当前 T19 能验证的界面行为映射到 MV3；后端契约、幂等和并发结论来自已关闭的 `docs/acceptance/issue-112-t09.md`，不把本次浏览器截图当作后端证明。

| MV3 | T19 / 其他证据 | 状态 |
| --- | --- | --- |
| MV3-01 | c1 空态、c7 响应式；V1 `issue-146-v1.md` | 通过（界面） |
| MV3-02 | c5 S8.5 切号后项目列表隔离；搜索/重命名/持久化继承 T02/T09 | 通过（界面+继承后端） |
| MV3-03 | c2 S2.1–S2.6 用户消息→需求卡；V3 提示词卡 | 通过（界面） |
| MV3-04 | S1.8 附件、S2.5 上传/消息请求；V6 附件交互 | 通过（界面） |
| MV3-05 | S3.1–S4.9 检查中/待确认/复制/确认；后端 PC3-01 | 通过（界面+继承后端） |
| MV3-06 | S4.7 一次 confirm；后端 PC3-02 零图片尝试 | 通过（界面+继承后端） |
| MV3-07 | S3.5 契约版本、S4.4 剪贴板逐字相等；后端 PC3-03 | 通过（界面+继承后端） |
| MV3-08 | S8.3 旧链接 404、S8.5 账号隔离；后端 PC3-04 | 通过（界面+继承后端） |
| MV3-09 | S4.7 单请求；后端 PC3-05/06 覆盖双击/并发/故障 | 通过（继承后端） |
| MV3-10 | S8.1 刷新、S5.1 结果卡；V5 `issue-150-v5.md`、后端 PC3-09 | 通过（界面+继承后端） |
| MV3-11 | c4 S7.1–S7.17；V4 `issue-149-v4.md` | 通过（界面） |
| MV3-12 | c5 S8.5–S8.7；V6 `issue-151-v6.md` | 通过（界面） |
| MV3-13 | c7 13/13、c8 8/8；V6 IME 事件日志；无真实手机软键盘 | 通过（界面，软键盘未知） |
| MV3-14 | S8.3/S8.4 旧链接 404；222 请求无模型调用；后端 PC3-09 | 通过（界面+继承后端） |

## CI 与缺陷门禁

| 门禁 | 结果 |
| --- | --- |
| 6 位 hex 只在 `tokens.css` | 0 处越界 |
| `var(--color-` 只在 tokens | 0 处使用 |
| `outline: 0/none` | 0 处 |
| `font-size` <12px | 0 处 |
| 未定义 CSS 变量 | `tests/t152-v7-cleanup.spec.ts` 11/11 |
| 旧组件引用 | 0 处（V7 t152 测试） |
| `.workspace-shell` 残留 | 0 处 |
| 抽屉段 `!important` | 仅 `.sr-only` 工具类（`workspace.css:324-331`），不在抽屉段 |
| 断点 | 仅 767/1023 语义断点；c7 13/13 |
| 胶囊渐变/呼吸 | CSS 无渐变、无 animation；reduced-motion 下过渡关闭 |

`workspace.css` 最终 355 行；V7 后 13px 非等宽文本与 10/12px 半径已在 T19 前审查修复中移除，残留仅为已记录的既有例外，详见 `issue-152-v7.md`。

## 规范基线

| 文件 | 视口 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `1440-empty.png` | 1440x900@1 | 73,022 | `ca4dc0275695598decdd9e866a231fae0ddf0c40dd26d47fc55955c8f37eb38c` |
| `1440-requirement-pending.png` | 1440x900@1 | 78,947 | `86ed5662581689620791b4a1a224454f21bbe294721ef4bd7bfec5b4a49398bb` |
| `1440-pending-confirmation.png` | 1440x900@1 | 67,930 | `71a154eb80f4c406886ac6da972bda2622d8a7753e470a7293cced12c5dec3c2` |
| `1440-result-lightbox.png` | 1440x900@1 | 93,089 | `a604312236caa582452c3a2258eebadef1831d5381a3950bd82f4cad76c27a82` |
| `768-drawer.png` | 768x1024@1 | 54,320 | `2fc77b8056ee23d3590286792e2bb9450494d06f69219c833558ab8fda64ca16` |
| `768-topbar.png` | 768x1024@1 | 43,033 | `c0140f5ff6c5b90c72c1b0b9bfb67ad571e58e139a0f7295bb42f595fd448092` |
| `320-empty.png` | 320x800@1 | 37,971 | `97c5f7f189846a87cd5a6ecc8248b3203cf3af267da4549d24624e5c89306392` |
| `320-lightbox.png` | 320x800@1 | 37,530 | `14e42e7c4c9d238b51a556d4fd8b38ca41915af565b16ec713f6c7c626f4950c` |
| `320-composer.png` | 320x800@1 | 33,700 | `0bdee9c8bba18df2fa8d260793ec0528ab356d20bc1c6a17604047cfc5c1cbb2` |

总计 **519,542 B**。

基线生成与验证之间：4 张图存在状态胶囊动画像素差（最大 42 px），`768-drawer` 像素一致但 PNG 字节不同；所有功能断言通过。详见 `docs/acceptance/baselines/t19/README.md`。

## 请求与记录追踪

`.scratch/t19-ui/evidence/run-20261108-235556/mock-stats.json` 包含 58 条状态性请求的 `request_id`/`record_id`；浏览器网络总数为 222。关键路径：

| 动作 | request_id | record_id | 结果 |
| --- | --- | --- | --- |
| 附件上传 | `req-000007` | `rec-000001` | 200 |
| 首条消息发送 | `req-000009` | `rec-000003` | 200 |
| 确认需求 | `req-000012` | `rec-000006` | 200 |
| 确认提示词 | `req-000022` | `rec-000011` | 200 |
| 恢复生成 | `req-000050` | `rec-000026` | 200 |
| 核实结果 | `req-000061` | `rec-000033` | 200 |
| 旧链接读取 | `req-000100` | `rec-000052` | 404 `PROJECT_NOT_FOUND` |

## 边界与例外

1. **受控替身，不是真实图片服务。** 所有 API 由 `.scratch/t19-ui/mock-api.mjs` 提供；没有真实模型调用、真实账户或真实资产。MV3-05—09 的后端契约、幂等、并发和故障注入结论继承 `docs/acceptance/issue-112-t09.md`，本次 T19 只补充界面路径。
2. **抽屉宽度。** 按 ticket #151 的 264px 验收；`plan.md` 中 272px 是旧探索值，不适用于本 ticket。
3. **失败原因文案。** V4 最终 UI 使用单行状态标签 `技术故障`，完整服务端原因保留在 `/workflow` 响应中，由 S7.1 同一步断言；没有把服务端长文逐字渲染到失败块。
4. **软键盘。** 320/768 视口和 IME composition 事件为 CDP 模拟，未在真实手机上验证软键盘弹起、安全区和输入法候选框。
5. **受控错误。** 4 条 console 4xx/5xx 均来自 fixture：受控失败 500、旧链接/缺失资产 404、无 session 401；不是产品缺陷。
6. **基线动画像素。** 见上节说明；基线文件不承诺逐字节等于验证截图。
7. **报告 `git.dirty`。** 最终运行期间只有未跟踪的 `docs/acceptance/baselines/` 与 `t19-browser-result.json`，没有应用源码修改。

## 结论

在受控 API、Edge headless、指定视口和 200% 缩放条件下，T19 的 8 项界面验收全部通过；V3–V7 的界面主流程、失败恢复、结果大图、响应式布局、焦点与减少动态均达到 ticket 的验收口径。真实服务、真实手机软键盘和发布级性能不在本次证据范围内。
