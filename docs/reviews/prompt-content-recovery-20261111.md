# 提示词正文丢失修复：双轴审查

## 范围

- 实施基线：`fc7f6868d6e1499aa436e55b8dc2104a245b409e`。
- 最终审查基线：`6cc844213f2fe0e0c22cda8cd92b8dd739309f6a`。同期对话意图修复完整保留，不计入本次提示词差异。
- 审查 HEAD：`20c1ab10b4956d8ca50142532ccff325fb2ba201`。
- 比较命令：`git diff 6cc844213f2fe0e0c22cda8cd92b8dd739309f6a...20c1ab10b4956d8ca50142532ccff325fb2ba201`。
- 需求依据：`docs/architecture/prompt-content-recovery-2026-11-11.md` 八项验收要求及用户本轮实施、审查、合并授权。

四个隔离实施分支：

| 分工 | 分支 | 实施提交 |
| --- | --- | --- |
| Markdown 解析及正文判定 | `codex/prompt-markdown-parser` | `7312acb` |
| 后端保护及原文恢复 | `codex/prompt-confirmation-backend` | `f7df7c5`、`f4ad779` |
| 前端修正及明确确认 | `codex/prompt-confirmation-frontend` | `c31f633` |
| 独立回归测试 | `codex/prompt-confirmation-regression` | `976fe4a` |

## Standards

初审发现一个 P2：前端无条件排除 Markdown 标题行，导致后端合法的 `# 正向提示词：猫` 无法确认；历史原文若仅由行内正文标题组成，恢复入口也被隐藏。

修复提交 `04d6b7e`：识别明确提示词标签中的行内正文，统一结构性空内容的判定；补充 21 个前端纯函数用例及一个真实确认卡恢复用例。两项原始 probe 复核通过，未发现新问题。

最终结论：**PASS，未解决发现 0 项**。不是仅靠字数阈值，也没有恢复历史业务审查门禁。

## Spec

逐条复核八项验收要求，包括不丢正文、歧义保留原文、结构空内容拒绝、已持久化坏快照保护、恢复依赖绑定、明确确认、登录／项目生命周期、快照与图片请求一致。

独立运行 14 个解析 probe 通过。最终新基线下逐文件比对已审补丁，确认同期意图修复没有被计作本次 scope creep。

最终结论：**PASS，遗漏／部分实现 0，范围外改动 0，实现错误 0**。

## 集成验证

- 原始 2878 字棋盘 Markdown 只读回放：正向 2051 字、反向 788 字，无降级警告，保留 32 格／闭环要求及路径、数量错误的反向内容。
- 专项后端：`test_prompt_content.py`、`test_prompt_confirmation_recovery.py`、`test_markdown_handoff.py`、`test_editable_prompt_confirmation.py`，**92 passed**。
- 确认前零图片提交；坏快照直接 POST 以 `PROMPT_EMPTY` 拒绝；编辑创建新快照、旧快照失效，不增加模型调用。
- 真实数据库边界：已完成且绑定图片的历史结果允许只读；相同旧结果标记但处于 running/submitting 时仍拒绝空正文，没有再次调用图片服务。
- 前端定向：解析判定和提示词确认卡 **39 passed**。
- 最终执行 `npm run check`：依赖图、typecheck 通过；前端 **31 文件、265 passed**；后端 Ruff 通过；后端 **345 passed、3 failed**。**完整检查并非全绿。**
- 3 项失败均在不含本次提示词修复的 `6cc8442` 基线独立复现（基线定向命令得到 3 failed、4 passed）：
  - `test_conversation_alignment.py::test_answers_accumulate_and_continue_original_request[送礼用，背景别太暗]`
  - `test_conversation_alignment.py::test_conversation_kind_with_a_complete_brief_remains_discussion`
  - `test_chat_intent_routing.py::test_chat_preserves_lifecycle_even_when_model_returns_wrong_ready_brief[clarification]`
- 这些测试和对应意图路由生产代码未为本任务修改；不得将已知基线失败归为本次修复通过或擅自削弱断言。最终合并依据是两轴审查通过、本次专项通过及无新增已知检查失败，不是全库全绿声明。
- 本地详细检查日志位于仓库外部运行产物区 `.scratch/prompt-recovery-runtime/npm-check-latest.log`；基线复现日志为同目录 `baseline-three.log`。临时测试目录、缓存和运行日志不进入提交。

仅提交源代码、文档和测试；未部署容器，未修改真实会话、快照、授权或生图数据。实际历史任务不会被追溯改写。

## 清理事故与恢复边界（合并后）

合并提交 `908f7f4` 已进入原分支 `visual/v3`。四个实施分支均已合并并用 `git branch -d` 删除，四个 managed worktree 已由工具归档。

**归档之后发现严重异常：原工作区 `main/` 中 202 个跟踪文件缺失，根 `node_modules/` 被清空，`main/backend/data` 与 `main/backups` 不再可见。** 未发出针对这些源目录的删除指令；时间上发生在 managed worktree 归档之后，怀疑与临时工作区依赖 junction / npm workspace 链接的递归清理有关，但没有足够证据确认工具内部根因。

发现后立即停止进一步清理。仅对 Git 报告缺失的 `main/` 跟踪路径从合并后的 HEAD 恢复，没有重置索引或覆盖其他修改；根依赖通过锁文件执行 `npm ci --ignore-scripts` 恢复。丢失的容器配置从现有容器环境恢复到未跟踪的 `.env`，没有输出或提交凭据。

**源代码可恢复，不等于运行数据可恢复。** 尝试从正在运行的后端文件描述符复制 SQLite 数据，但底层挂载文件已不可读取，未取得有效数据库。当前不能确认 `main/backend/data` 原始数据库、图片以及 `main/backups` 的恢复，必须视为潜在实际数据损失。没有以根 `backend/data` 的旧开发库或历史备份覆盖原路径，也没有重启／重建现有业务容器来掩盖问题。

集成审查 worktree 因工具将其视为本 chat primary checkout，归档被拒绝；为避免进一步风险，保留此干净 detached checkout，不再绕过保护强删。四个用户要求的实施分支已清理。

此前“未修改真实数据”的验证范围仅适用于实施与测试阶段，**不适用于这次清理事故**。后续需要用户选择可信备份或独立恢复途径，不能将本轮交付描述为完全成功或无损清理。
