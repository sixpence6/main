# Issue #118 T06 验收记录：原意图文本修复与明确放弃结案

## 1. 范围

- 工单：#118（V3 纯对话 T06，父票 #112）。
- 目标：需求未变的文本问题在原意图内修复并重审重显；明确放弃只在可证未执行时结案释放，不退款、不伪称远端取消；实质修改需求时用窄操作合法结算旧未提交任务；不引入自动重试、自动到期、免费预览、隐式排队或 SRS 口径变化。
- 本票只涉及 R2 后端行为纵向切片，无前端改动、无新迁移。T09 门槛通过前不实施 R3 界面替换，也未制作前端假确认按钮。

## 2. 实现

- `main/backend/app/acceptance.py`
  - 新增 `settle_unsubmitted_intent`：只有 `GenerationIntent.status == "accepted"` 且不存在任何 `GenerationJob` 行时才结算；结算为 `abandoned`、释放该意图的 live slots、保留 usage 行不动，并写审计。审计固定记录 `image_attempt_exists=False`、`remote_cancelled=False`、原因和命令关联；有 job 时返回 False，由调用方保守拒绝。
  - 不删除 `generation_usage`，不改变窗口计数；不调用任何远端取消接口。
- `main/backend/app/workflow/stages/s06.py`
  - `revise_requirements` 在启动需求修订前，对最新 intent 为 `accepted` 且无 job 的旧任务调用上述窄结算；有 job 时返回 409 `INTENT_ATTEMPT_EXISTS`，保留原计数与占用。供后续消息流程复用同一事实判定。
- `main/backend/app/workflow/stages/s07.py`
  - `ACTIONS` 增加 `abandon_generation`；S07 `needs_input` 暴露 `revise_prompt` / `abandon_generation`，active 暴露 `compile_prompt` / `revise_prompt`。
  - `_AbandonPromptCommand` 严格校验 `snapshot_id`、64 位十六进制 `snapshot_digest`、`command_id`，拒绝额外字段；命令与显示快照、项目/流程归属、request digest、intent 所有者绑定。
  - `_abandon_generation`：以 `command_id` 作为审计 `correlation_id` 做幂等；同命令同参数重放返回原结果，同命令异快照/digest 返回 `COMMAND_ID_CONFLICT`；快照已失效、非本流程或 digest 不符分别返回明确 409；存在任一 `GenerationJob` 时返回 `INTENT_ATTEMPT_EXISTS`，不结案、不释放。
  - 成功后把该 intent 的所有 awaiting 快照置 `invalidated`，run 置 S07 / blocked / `PROMPT_ABANDONED`，审计 detail 绑定 intent、snapshot、request digest、稳定 request_id、project revision 与 command_id，且明确 `remote_cancelled=False`。
  - `revise_prompt` 在 `needs_input` 时先检查持久候选预算，预算耗尽则保留旧候选并返回 409 `REVISION_BUDGET_EXHAUSTED`；随后失效旧 awaiting 快照、将 run 恢复 active，再走完整 `_compile`。新候选重新经过独立 output reviewer 审查，通过后由 `approve_and_wait` 重新冻结、展示并进入最终确认；旧快照的最终确认上下文不能继承。
- `main/backend/app/workflow/dispatch.py`
  - 注册 `abandon_generation` 的动作元数据；S07 的 `revise_requirements` 在 needs_input、`PROMPT_REQUIRES_RECONFIRMATION` 和 `PROMPT_ABANDONED` 三种等待/阻断状态均可见。
- `main/backend/app/workflow/state.py`
  - `build_workflow_view` 在可最终确认时不再把动作覆盖成仅 `confirm_prompt`，保留并列的提示词修复、放弃和需求修改入口。
  - `add_approval` 的复用条件增加 `existing.artifact_id == artifact.id`：即使新候选 digest 与旧候选相同，也不能把旧文本授权继承给重新审查的新 artifact revision。
- `main/backend/app/workflow/templates.py`
  - 新增 `PROMPT_ABANDONED` 文案：提交前明确放弃、未发起图片调用、槽位释放、计数保留、不自动重试、不声称远端取消。

## 3. 测试与证据

新增 `backend/tests/test_issue_118_t06.py`，共 7 个测试函数、10 个用例（其中“等待/尝试”用例按 GenerationJob 四态参数化），使用真实临时 SQLite、公开 HTTP/工作流接口、受控 LLM/Visual 适配器，部分用例使用独立 app 生命周期验证重启持久性：

1. 正常文本修复保留同一 intent、同一 request_id、同一需求版本、一次 usage 和 3 个 live slots；预算从 1 增至 2，旧候选 invalidated、新候选 awaiting_confirmation；旧快照最终确认返回 409 `PROMPT_STALE`；新候选经第二次独立 S07 output reviewer 审查后最终确认，提交一次图片并保持 usage=1。
2. 预算跨独立 app 重启保持；等待 0.05s 不自动到期、不改变候选；耗尽时返回 409 且最后候选仍有效。
3. 明确放弃绑定 snapshot/digest/revision/request_id/command_id；审计 correlation_id 等于 command_id；同命令重放幂等、同命令异 digest 冲突、新命令用已失效快照被拒；usage 保留、slots 归零、无 job、无图片调用，且不声称远端取消。
4. 等待不自动到期；分别插入 `submit_pending`、`submitted`、`remote_pending` 或 `unknown` 且 `submit_attempted=1` 的 job（参数化四态）后，放弃与需求修改均返回 `INTENT_ATTEMPT_EXISTS`，intent 仍 accepted、slots 仍占用、候选不变。
5. `revise_requirements` 只在旧 intent 无 job 时结算：需求 revision 增加、旧 intent abandoned、usage 仍一行、slots 归零，审计记录 `reason=requirements_revision` 与 `remote_cancelled=False`。
6. S12 reviewer 失败后不自动重试；显式 `continue_workflow` 用 review checkpoint 恢复同一 intent、同一预算与候选；随后文本修复保留 intent/request_id/usage，预算再增一次，reviewer 调用累计三次。
7. 新入口 HTTP 边界：匿名 401、他人项目 404、错误 Origin/缺失 CSRF 403、非法 digest 422；所有拒绝后候选、intent、usage、slots 与审计均不变。

实际运行（cwd = T06 worktree `backend`，隔离 basetemp）：

```powershell
& $py -m pytest tests/test_issue_118_t06.py -q -p no:cacheprovider --basetemp D:\Desktop\issue-112-context\t06-final1
```

结果：`10 passed in 35.39s`。

TDD 红绿证据：在 556826f 基线上临时移除本票 6 个实现文件后运行同套测试，得到 `10 failed in 27.54s`（`backend/.scratch/t06/red1.log`）；实现后的实现者日志 `backend/.scratch/t06/green2.log` 为 `10 passed in 40.81s`，lead 最终复跑为 `10 passed in 35.39s`（basetemp `t06-final1`）。

相邻/主验收回归（同一 T06 worktree，隔离 basetemp `D:\Desktop\issue-112-context\t06-regression2`）：

```powershell
& $py -m pytest tests/test_issue_117_t05.py tests/test_issue_115_t03.py tests/test_issue_116_t04.py tests/test_issue_114_t02.py tests/test_issue_114_migration.py tests/test_issue_113_t01.py tests/test_issue_113_migration.py -q -p no:cacheprovider --basetemp D:\Desktop\issue-112-context\t06-regression2
```

结果：`119 passed in 408.98s`（T05 14 + T03 28 + T04 22 + T02 24 + T01 25 + 迁移 6）。

静态检查：本票修改的 `acceptance.py`、`s06.py`、`s07.py`、`state.py` 与新增测试均通过 `ruff check --no-cache`；`dispatch.py` 与 `templates.py` 仅报告文件既有的 E501（单行动作字典及既有 `IMAGE_NOT_APPROVED` 文案），本票未新增检查错误。`git diff --check` 无输出，退出码 0。

## 4. 逐条验收判定

| 票面要求 | 判定 | 证据 |
|---|---|---|
| 正常及 S12 文本修复保留原意图、一次用量与持久候选预算，需求不变不新建需求版本；技术故障不自动重试 | 通过 | 测试 1、2、6：同 intent/request_id/requirement_revision/usage；预算跨重启；S12 失败后 sleep 不自动重试 |
| 修复后的实际请求重新独立审查、冻结、展示和最终确认，旧文本授权不能继承；刷新/重启不重置预算 | 通过 | 测试 1：第二次 S07 output reviewer、旧快照 409、新快照最终确认；测试 2 独立 app 重开预算不变；`add_approval` artifact_id 绑定修复 |
| 明确放弃命令绑定原任务、快照、revision 和稳定请求 ID；仅无图片尝试且无在途未收敛外部调用时失效结案释放 | 通过 | 测试 3、4：审计字段绑定、同命令幂等、有 job 拒绝、slots 归零、usage 保留 |
| 普通暂缓不等于放弃，等待无自动到期；原计数保留，已提交或未知保守占用且不伪称远端取消 | 通过 | 测试 2、3、4：等待不改变状态；unknown job 保守拒绝；审计固定 `remote_cancelled=False` |
| 实质修改需要新意图时，提供按可证事实合法结算旧未提交任务的窄操作，供后续消息流程复用 | 通过 | 测试 5；`settle_unsubmitted_intent` 为 `revise_requirements` 复用，且无 job 才结算 |
| 本票仅在隔离环境验证；不操作真实用户数据、凭据或运行配置，保护用户现有未提交修改；测试文档不进入应用本体 | 通过 | 全部测试使用临时 SQLite 与受控适配器；测试位于 `backend/tests`，文档位于 `docs/acceptance`；未触碰原用户树 |
| 所有新增入口遵守身份、所有者、对象关联、内容门禁、CSRF/来源与审计约束 | 通过 | 测试 7 覆盖匿名/他人/Origin/CSRF/非法参数；动作归属、快照 digest、严格 payload 校验与审计由实现与测试共同证明 |
| 不改变 SRS 参数或次数口径，不新增免费预览、自动等待超时、隐式排队或自动图片重试 | 通过 | usage 只增不删、无新预算参数、无定时器、无自动调用；测试 2/6 等待不自动重试 |

## 5. 残留风险

- 放弃/结算前的 `GenerationJob` 检查与后续写事务之间仍有窄并发窗口；实现采用“任一 job 即保守拒绝”，若未来引入新的外部调用记录，必须同步扩展可证事实集合。
- `revise_requirements` 先结算旧未提交 intent，随后才启动需求修订；若后续需求修订在写消息后失败，旧 intent 保持已结算且 usage 保留，这是“已合法结算”的保守结果，需在后续消息流程中按该事实继续处理。
- 未覆盖真实浏览器、手机和前端展示；R3 后按 #129 补真实浏览器与设备证据。
- S12 用例验证 review checkpoint 重放与预算持久，不重复证明真实进程崩溃边界；崩溃恢复边界由 T05 主验收覆盖。

## 6. 合并复核（Lead）

- 合并：`c9fb79b` 将 `codex/issue-112-t06`（`5990ccc`）以 `--no-ff` 合并入 `codex/issue-112-integration`。
- 合并后主验收集（119 条既有 + T06 10 条）：`129 passed in 435.67s`，basetemp `D:\Desktop\issue-112-context\t06-main-accept1`。
- 静态检查：本票修改文件通过 ruff（dispatch/templates 仅既有 E501），`git diff --check` 退出 0。
- 结论：合并结果与 T06 分支验收一致，接受 #118 并关闭。
