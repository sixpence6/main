# Issue #112 T04（#116）并发重放与写点故障验收

## 范围与环境

- 票：#116「V3 纯对话 T04：并发重放与写点故障下的单次账本」。
- 工作树：`C:\Users\28425\.codex\worktrees\issue-112-t04\新建文件夹`；分支 `codex/issue-112-t04`；基线 `da459a4`。
- 测试主切面：已登录用户 → HTTP／工作流公开接口；真实临时 SQLite；受控 `LLMStub`／`VisualStub`。
- 唯一 runner：`C:\Users\28425\.codex\worktrees\issue-112-integration\新建文件夹\backend\.venv\Scripts\python.exe`，cwd `...\issue-112-t04\新建文件夹\backend`；`TEMP/TMP=...\backend\.scratch\t04\temp`；`--basetemp D:\Desktop\issue-112-context\t04-pytest`。

## 改动

- 新增 `main/backend/app/confirmation_conflicts.py`（47 行）：
  - `is_unique_violation`（:14）只识别真实唯一约束冲突；注入的存储故障继续按 500 暴露。
  - `resolve_final_integrity_conflict`（:20）先按 `project_id + command_id` 重查，再按 `snapshot_id` 重查；分别返回 `FINAL_COMMAND_CONFLICT` / `FINAL_ALREADY_AUTHORIZED`。
- `main/backend/app/final_confirmation.py`：
  - 原 `confirm_prompt` 改为 `_confirm_prompt_once`（:196），保留原单事务与重放分支。
  - 新增薄包装 `confirm_prompt`（:415-433）：仅捕获唯一约束型 `IntegrityError`，映射为 409；其他 `IntegrityError` 原样抛出。
  - `_schedule` 仍在 `if created:` 分支内（:400-411），仅首次创建调度。
- 新增 `backend/tests/test_issue_116_t04.py`（622 行，22 个用例）：覆盖准备/最终重放、同 ID 异参、同快照换 ID、独立连接、五类最终写点与三类准备写点故障、槽位/日额度拒绝、滚动窗口、三并发和映射器。

## RED → GREEN

基线（未接入 `confirmation_conflicts`，18 个用例）：

```powershell
& $py -m pytest tests/test_issue_116_t04.py -q -p no:cacheprovider --basetemp D:\Desktop\issue-112-context\t04-pytest
```

实际结果：`13 passed, 5 failed`。失败为准备受理相关断言（工作流状态仍为 `active`、账户级用量口径），随后修正测试口径。

实现后：

```powershell
& $py -m pytest tests/test_issue_116_t04.py -q -p no:cacheprovider --basetemp D:\Desktop\issue-112-context\t04-pytest
```

实际结果：`18 passed in 70.23s`。

补充同命令并发、三并发、日额度与映射器用例后：`22 passed in 96.97s`。

合并 `codex/issue-112-integration`（fast-forward 至 `1495af5`）后复跑：`22 passed in 92.99s`。

相邻回归：`tests/test_issue_114_t02.py` → `24 passed in 95.20s`。

Ruff：

```powershell
& $py -m ruff check --no-cache ../main/backend/app/confirmation_conflicts.py ../main/backend/app/final_confirmation.py tests/test_issue_116_t04.py
```

实际结果：`All checks passed!`。

## 逐条验收

- **通过**：准备双击（同请求 ID 同参）只产生 1 个 `GenerationIntent`、1 条 `generation_usage`、3 个活跃槽位；同 ID 异参返回 409，不新增账本行。
- **通过**：最终确认同命令重放返回原 `generation_job`；同快照换命令 ID 返回 409 `FINAL_ALREADY_AUTHORIZED`；同命令换 revision 返回 409 `FINAL_COMMAND_CONFLICT`。
- **通过**：两个独立 app（不同 engine/session）并发提交不同命令 ID，仅 1 个 `final_authorizations`、1 个 `generations`、1 个 `generation_jobs`、1 条 `generation_usage`；`visual.submit_calls == 1`。
- **通过**：独立 `sqlite3.connect` 直接核验 `generation_intents=1`、`generation_usage=1`、三槽位 `personal/instance/project` 各 1、`generation_jobs=1` 且其 `generation_id` 唯一。
- **通过**：最终五类写点（`final_authorizations`、`generations`、`generation_jobs`、`audit_events`、`prepared_prompts`）故障均 500 全回滚，`visual.submit_calls == []`，重试后仅一次提交、用量仍为 1。
- **通过**：准备三类写点（`generation_slots`、`generation_usage`、`audit_events`）故障全回滚，零 intent/usage/slot，未触发模型调用；修复后重试成功且只受理一次。
- **通过**：个人槽位忙时第二项目返回 429 `GENERATION_SLOT_BUSY`，不产生 intent、不排队、不调用适配器；`_drain` 后仍无任务。日额度为 1 时第二项目返回 429 `GENERATION_LIMIT_REACHED`。
- **通过**：滚动窗口按 24h 边界计数；个人/实例/项目三槽位分别约束，释放个人槽位后允许下一受理，实例槽位仍保留双占位事实。
- **通过**：同意图重复确认不重新计数（`usage.used == 1`），不新增 generation/job/authorization。
- **通过（实现级）**：`resolve_final_integrity_conflict` 直接用例证明同命令异摘要 → `FINAL_COMMAND_CONFLICT`，同快照异命令 → `FINAL_ALREADY_AUTHORIZED`。

## 残余未知

- SQLite `BEGIN IMMEDIATE` 使真实「两事务同时插入」窗口极窄；HTTP 并发用例实际走的是串行化后的重放/409 分支。`IntegrityError` 映射通过直接调用映射器与代码路径验证，未在真实并发插入中稳定复现。
- 本票仅 R2 后端行为；未做真实浏览器、真实设备、进程重启或真实供应商调用。
- 未证明准备阶段「等待超过任意时间仍不自动释放」的时钟推进场景；当前无自动释放参数，重复读取未改变计数/槽位。
