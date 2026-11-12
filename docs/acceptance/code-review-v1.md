# 前端 v1 code review

- 范围：`git diff 6bdf84a...HEAD`（integration/frontend-v1，T01–T12 记录）
- 日期：2026-10-29
- 结论：**无阻断性发现**。首轮发现的 1 个过时用户文案与 8 个规格/健壮性问题均已修复并增加回归测试；其余为架构/重复代码判断项，记录为后续重构候选。

## Standards

**硬性违规（AGENTS.md）：无。** `main/` 只放项目本体；测试、文档、配置均位于根目录其他文件夹。

判断项（非阻断）：

- `useWorkspace.ts` 为约 2,700 行 god composable；接口、URL、sessionStorage、轮询、图片校验和所有工作流集中在一处，存在 Divergent Change / Shotgun Surgery。
- `queryProjectStatus` 与 `queryGeneration`、多个错误消息映射器有重复结构；可提取统一查询/错误映射层。
- `GenerationRiskDialog`、`UnsavedChangesDialog`、`AccessibleDrawer`、`ResultPanel` 重复 focus trap / body scroll lock / focus restore；建议提取公共 composable。
- `ModelStatusDrawer` 的 LLM/VISUAL 卡片与 `BriefCard` 的快照字段有重复标记/逻辑；`issueText` 与 API 层 sanitizer 规则不一致。
- `ConversationPanel` 同时承担加载、对话、需求编辑、生成摘要、上传与 composer，prop drilling 较重。
- `workspace.css` 为 2,100 余行全局样式表，存在原始色值与按 ticket 追加的注释；Element Plus 全局注册，健康检查现已实际调用。

## Spec 审查与修复

首轮抽查 T01–T11 的验收路径均有完整工作台测试；进一步对照父 issue 后发现并修复以下 8 项：

1. **打开项目默认视图**：原实现总是进入“描述需求”。现按规格选择：整理中、生成中、最近失败/未知或提交待核实时进入对话；否则有成功版本进入结果，无成功版本进入对话。
2. **生成阶段解锁条件**：原实现只依据 `brief_status === confirmed`。现“已有可生成需求或已有生成记录”均可解锁；点击“生成图片”会把焦点定位到生成概览。
3. **参考图解码校验**：原实现只读 512 KiB 并检查文件签名/尺寸头，CRC 损坏的 PNG 或截断 JPEG 可通过。现校验完整 PNG 块边界/CRC/IHDR/IDAT/IEND 与 JPEG 帧/扫描/EOI，并在浏览器支持时用 `createImageBitmap` 实际解码；失败不发任何写请求。
4. **2xx 非法 JSON 回执**：原实现把解析错误抛为原始 `SyntaxError`，导致生成/参考图写请求被误判为可重试。现转为 `ApiError(status, INVALID_RESPONSE)`，并纳入“回执不明”安全分支，保留恢复凭据。
5. **恢复凭据多项目覆盖**：原使用单一全局 sessionStorage 键。现按项目使用 `ecommerce-generation-pending:<projectId>`，同时兼容读取旧全局键，避免第二个项目覆盖第一个项目的待核实提交。
6. **参考图 MIME 回退**：原在模型只声明 WebP 等格式时静默回退到 PNG/JPEG。现只接受模型声明且与本地支持的交集；交集为空时明确说明“当前模型未声明支持 PNG 或 JPEG 参考图。”并禁用选择。
7. **消息 409 恢复**：原只在 composer 显示错误。现保留原输入，显示“需求已在别处更新，这条消息未发送。”，提供“加载最新需求”和只读本地副本，不自动重发。
8. **健康检查展示**：原 `getHealth` 无调用方。现启动时 GET `/api/health`，模型状态抽屉显示“服务健康检查：正常/不可用/尚未完成”。

另有 1 个过时文案修复：`ResultPlaceholder.vue` 的“结果图片查看将在后续版本接入”改为“请选择项目查看结果。”。

## 验证

- `npm run typecheck` 通过
- `npm test -- --run`：13 个文件、158 个测试通过（含 `tests/code-review-regressions.spec.ts` 的 10 个回归测试）
- `npm run build` 通过（1617 modules；>500 kB chunk 警告保留）
- 回归测试覆盖：默认成功视图、失败默认对话、生成记录解锁与焦点、CRC 损坏 PNG、截断 JPEG、仅 WebP 能力、消息 409 恢复、2xx 非法 JSON 回执、多项目凭据共存、健康检查。

## 剩余未核验项

真实 OpenAPI 字段差异、真实图片编码/扩展名、真实持久化重启、真实供应商错误码、Windows Chrome+Edge 行为仍受外部前提阻塞，详见 `docs/acceptance/t12-real-api-browser-acceptance.md`。未核验前不得宣称真实闭环完成。
