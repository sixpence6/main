# 前端 v1 code review

- 范围：`git diff 6bdf84a...HEAD`（integration/frontend-v1，T01–T12 记录）
- 日期：2026-10-29
- 结论：**无阻断性发现**；1 个过时用户文案已修复，其余为架构/重复代码判断项，记录为后续重构候选。

## Standards

**硬性违规（AGENTS.md）：无。** `main/` 只放项目本体；测试、文档、配置均位于根目录其他文件夹。

判断项（非阻断）：

- `useWorkspace.ts` 为约 2,500 行 god composable；接口、URL、sessionStorage、轮询、图片校验和所有工作流集中在一处，存在 Divergent Change / Shotgun Surgery。
- `queryProjectStatus` 与 `queryGeneration`、多个错误消息映射器有重复结构；可提取统一查询/错误映射层。
- `GenerationRiskDialog`、`UnsavedChangesDialog`、`AccessibleDrawer`、`ResultPanel` 重复 focus trap / body scroll lock / focus restore；建议提取公共 composable。
- `ModelStatusDrawer` 的 LLM/VISUAL 卡片与 `BriefCard` 的快照字段有重复标记/逻辑；`issueText` 与 API 层 sanitizer 规则不一致。
- `ConversationPanel` 同时承担加载、对话、需求编辑、生成摘要、上传与 composer，prop drilling 较重。
- `workspace.css` 为 2,086 行全局样式表，存在原始色值与按 ticket 追加的注释；Element Plus 全局注册、`getHealth` 暂无调用方。

## Spec

- T01–T11 的验收路径均有对应完整工作台测试；抽查未发现缺失的必做交互。
- T12 的“真实 OpenAPI/真实模型/Chrome+Edge 闭环”因外部前提缺失无法执行，已按规格记录为阻塞证据，未以受控测试替代。
- 修复：`ResultPlaceholder.vue` 中过时的“结果图片查看将在后续版本接入”改为“请选择项目查看结果。”，与已交付的结果视图一致。
- 剩余未核验项：真实 OpenAPI 字段差异、真实图片编码/扩展名、真实持久化重启与双浏览器行为；详见 `docs/acceptance/t12-real-api-browser-acceptance.md`。

## 修复后验证

- `npm run typecheck` 通过
- `npm test -- --run`：12 个文件、148 个测试通过
- `npm run build` 通过（1617 modules；>500 kB chunk 警告保留）
