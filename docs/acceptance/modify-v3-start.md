# modify-v3-start — 需求阶段放宽与最终提示词确认（实施记录）

状态：**部分实施；仅完成需求阶段放宽、澄清引导和最终提示词确认停点，未完成 modify_v3 的完整界面重建，也未做真实浏览器／真实模型验收。**

- 分支：integration/v3；记录基线：`311b215687e8a87fa97d7c643eca5969645bb2d9`
- 依据：`version/v3/modify_v3.md` 第 3 节；`docs/architecture/workflow-gates-v3.md`；`docs/architecture/frontend-design-v3.md`；`docs/architecture/prompt-confirmation-v3.md`
- 测试：隔离临时 SQLite、脚本化模型／视觉适配器；前端 Vitest 组件测试。网络全部 stub。

## 1. 本次已实现

1. **S03 需求审查放宽**（`main/backend/app/workflow/stages/s03.py`）
   - 只有 6 项 blocking 规则参与放行：`G03_SUBJECT_PURPOSE`、`G03_SOURCE_BASIS`、`G03_TEXTS`、`G03_CONSTRAINTS`、`G03_SIZE`、`G03_UNRESOLVED`。
   - `G03_STRUCTURE` 登记为 advisory；advisory 失败只留下建议，不改变 S03 的有效裁决。
   - 需求顾问返回 `needs_input` 时不再把澄清问题送独立审查；直接回到 S03 `needs_input`，在对话中展示问题。
   - reviewer 的 blocking 结果为 `unknown` 时，不再变成死墙，而是回到 `needs_input` 并给出可操作的补充提示。
   - 修订预算耗尽、非法 reviewer 声明等仍保持安全失败；硬约束、安全、权限、身份和独立审查未放宽。

2. **逐步引导的对话入口**（`main/src/components/ConversationPanel.vue`、`main/src/composables/useWorkspace.ts`）
   - 仅当 `stage === 'S03' && status === 'needs_input'` 时，把 `issue.message` 作为助手消息展示；用户直接在下方的原输入框回答。
   - `WorkflowPanel` 对同一澄清消息去重，避免一题两处显示。
   - 需求问题只询问阻塞缺项；风格、背景、颜色、灯光等作为可假设项，不作为拦截条件。

3. **先看完整提示词，再显式确认生成**（`main/backend/app/workflow/stages/s08.py`、`main/src/components/workflow/WorkflowPanel.vue`）
   - S07 通过后停在已批准提示词等待状态；`generation_cycle.py` 不再自动调用 S08 提交图片。
   - 对话中展示 `第 N 次 · 完整执行提示词`，默认展开；有负向内容时同时展示。
   - 按钮文案为“确认使用以上提示词生成”；点击载荷绑定 `prompt_artifact_id`、`prompt_digest`、`requirement_revision` 和稳定的 `request_id`。
   - 服务端缺少 `request_id` 返回 `PROMPT_CONFIRMATION_REQUIRED`；提示词产物、摘要或需求 revision 不一致返回 `PROMPT_CONFIRMATION_STALE`。同意图重放由 `_job_by_intent` 返回原任务，不重复提交。

## 2. 已验证

- 前端全量：`46/46` 个测试文件、`219/219` 个测试通过；`npm run typecheck` 退出码 0。
- 后端相关：
  - `tests/test_v2_t05_requirements.py` + `tests/test_v3_p1_t03_review_catalogue.py`：41 passed。
  - `tests/test_v2_t05_requirements.py` 单独：27 passed（含 advisory `G03_STRUCTURE` 不阻断、reviewer unknown 引导回澄清）。
  - `tests/test_v2_t11_generation.py`：12 passed（含精确提示词确认和过期载荷拒绝）。
  - `tests/test_t65_generation_cycle.py` + `tests/test_t69_local_closure.py`：14 passed（含 S07 后不自动提交、最终确认后进入本地闭环）。
  - `tests/test_v3_p1_t02_adjudication.py` + `tests/test_v3_p1_t03_review_catalogue.py`：34 passed。

## 3. 未完成／残余

- `modify_v3.md` 的完整界面重建（侧栏、统一消息流、附件、真实浏览器、手机与输入法、视觉对照）未在本记录范围内完成。
- 当前图像适配器仍只消费单个 `prompt` 字符串；`prompt_plan.negative_prompt` 的实际合并／发送与“展示即实际请求”的逐字段冻结尚未完成。现有生成测试仍断言 `request.prompt == positive_prompt`。
- 全量后端中备份／恢复测试仍受工作区 ACL 限制，`.verify-*` 目录清理报 `PermissionError WinError 5`；这不是产品断言失败，相关非备份用例已分组通过。
- 本记录不是发布验收，也不替代真实模型、真实浏览器和发布门禁证据。
