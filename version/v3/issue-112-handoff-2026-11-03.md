# Issue #112 实施交接：T01 已合入，T02 未提交待续

记录时间：2026-11-03 10:45（Asia/Shanghai）。本文是中断检查点，不是规格完成、阶段验收或发布声明。

## 1. 最新用户意图与停止边界

原任务：按 `implement-spec` 实现 [#112](https://github.com/sixpence6/main/issues/112) 及其子工单。用户随后主动中断，最新要求仅为“生成交接文档放到 version/v3 下”。因此本文件按用户指定位置保存，不使用 handoff 技能默认的系统临时目录。

**当前已停止实施和扩审。恢复代码、测试、提交、合并及工单推进须等待用户明确要求继续。** 不因本文存在自动继续后台工作。T02 的所有未提交及未跟踪文件均须保留，不能 reset、clean 或 archive。

## 2. 工作区、分支与交付状态

| 位置 | 当前事实与保护要求 |
|---|---|
| `C:\Users\28425\.codex\worktrees\issue-112-integration\新建文件夹` | 当前集成 worktree；分支 `codex/issue-112-integration`；HEAD `76cd5f446bd8d7d3e60714c0fb4b301538558ab9`。写本文之前工作树干净；本次只新增交接文档，不自动提交。 |
| `C:\Users\28425\.codex\worktrees\issue-112-t02\新建文件夹` | T02 实现 worktree；分支 `codex/issue-112-t02`；HEAD 同上，但有 **20 个 tracked 修改、6 个 untracked 文件，均未暂存**。没有 T02 提交、合并或验收结论。 |
| `D:\Desktop\新建文件夹` | 原用户工作区，分支 `integration/v3`，业务基线 `311b215687e8a87fa97d7c643eca5969645bb2d9`。已有未提交架构修改、旧 `vesion/` 删除、新 `version/` 及其他残留；本轮未清理、还原、暂存或切换它。只读取依赖，不把此树当 T02 源码运行。 |
| `D:\Desktop\issue-112-context` | 外部上下文、工单缓存、代码地图及运行日志。下文给出必要指针；这些文件不是提交内容，不保证跨机器可用。 |

T01 专属 worktree `C:\Users\28425\.codex\worktrees\issue-112-t01\新建文件夹` 已由主代理归档，`list_artifacts` 确认其为 `archived_worktree`；不要再引用它运行测试。集成及 T02 worktree 仍为 active。本轮未 push、未创建 PR；仓库工单规则允许凭验收评论关闭，不要求 PR。

### 已提交内容

不要重复实施或 cherry-pick 下列内容：

- `1847af2`：携带已确认的本轮必要规范；`1a9cc59`、`371028d`：基线、保护范围及运行器证据。
- `a5ef6588fe94e498fe691d599b88ca19fa93a5ec`：T01 实现；`4de60ef`：T01 同步集成 tip。
- **`76cd5f446bd8d7d3e60714c0fb4b301538558ab9`：merger 子代理将 T01 合入集成。** 具体实现范围以提交与 [T01 验收记录](../../docs/acceptance/issue-112-t01.md) 为准。

## 3. 下一会话先读什么

无需重新访谈需求，也不要从历史 issue 的关闭状态推断当前能力。

1. [仓库指令](../../AGENTS.md)、[工单约定](../../docs/agents/issue-tracker.md)、[领域文档入口](../../docs/agents/domain.md)。若 glossary/ADR 不存在则按仓库规则继续，不另行补造。
2. [父规格 #112](https://github.com/sixpence6/main/issues/112)、[当前工单 #114](https://github.com/sixpence6/main/issues/114)。完整子工单与原生依赖以 GitHub 当前状态为准。
3. [HLD v3](../../docs/architecture/hld_v3.md)、[门禁与工作流](../../docs/architecture/workflow-gates-v3.md)、[提示词确认契约](../../docs/architecture/prompt-confirmation-v3.md)、[前端设计](../../docs/architecture/frontend-design-v3.md)、[SRS](SRS_v3.md)。候选方案只作 rationale，不能作已交付证据。
4. [本轮基线](../../docs/acceptance/issue-112-baseline.md)、[T01 证据](../../docs/acceptance/issue-112-t01.md)。[早期启动记录](modify-v3-start.md) 属历史背景。
5. **T02 精确中断检查点**：`D:\Desktop\issue-112-context\t02-worker-handoff.md`；**未完成的定向审查**：`D:\Desktop\issue-112-context\t02-security-review.md`。

其他按需指针：外部目录下 `spec-112.md`、`ticket-113.md` 至 `ticket-130.md`、`task-graph.json`、`backend-map.md`、`frontend-map.md`、`orchestration.md`。地图基于原始代码树，使用时映射到当前 worktree；`active-state.json` 是较早编排快照，不能覆盖本文最新停止状态。

## 4. 工单与阶段状态

- **#113 / T01：CLOSED。** 已通过证据评论交付，合并后主公开验收及迁移 **28 passed / 65.39s**。详见 [关闭评论](https://github.com/sixpence6/main/issues/113#issuecomment-6593323941) 与仓库验收记录。
- **#114 / T02：OPEN，已认领但未完成。** 草稿仅在 T02 worktree，不在集成分支；不能关闭本票或对外称已贯通最终确认。
- **#115—#130：本会话未实施。** T02 通过后才会解锁 #115／#116／#117，后续严格查询实际依赖图，不把工单当串行 checklist 或擅自跳票。
- **#121 / T09 是 R2→R3 明确进入门槛。** PC3-01—09、MV3-05—09 后端及受影响 R1/R2 未在同一当前基线上通过前，不开始 #122 的界面替换或制作假确认按钮。本轮未做任何 R3 实现。
- 不修改、重开、关闭或扩写既有 #86／#111；不自动关闭父 #112。真实手机、供应商、发布环境缺证不得用模拟或截图代签。

## 5. T02 草稿：范围、实际测试与当前失败

草稿的 API、模型、文件清单与红绿过程已写入外部 worker 检查点，不在本文复制。接续前先查看 T02 的 `git status`、tracked diff **及 6 个 untracked 文件**：普通 `git diff` 不包含新 `final_confirmation.py`、0013 迁移及新测试。

核心方向为既有工作流 `confirm_prompt` 动作、当前登录命令上下文、原子最终授权和唯一图片尝试、事务外消费冻结请求、沿用原意图与用量；这些仍是**未验收草稿**。不得仅删除 T01 默认拒绝守卫，回到旧可变字段拼装执行。

### 最新实际结果

日志根：`C:\Users\28425\.codex\worktrees\issue-112-t02\新建文件夹\backend\.scratch\t02`。没有本轮 handoff 后新增测试；以下不是 post-commit 结果。

| 证据 | 实际结果与限制 |
|---|---|
| `t02-final.log` | **60 passed、1 failed / 168.37s**；目标汇总不是全绿。 |
| `restart-recheck.log` | 同一失败单例复跑仍 **1 failed / 5.03s**。 |
| `r1-entry.log` | **131 passed、1 skipped / 185.35s**；2 条既有 httpx cookie 弃用警告；skip 是缺少前端 dist 的制品测试，不是通过。 |
| `event-lifecycle.log` | T62 五项与升级为真实最终点击的 T65 正向一项：**6 passed / 12.99s**。原先基于 sleep 轮询的两个失败已改成受控 adapter 入口事件，保留退出／禁用断言。 |
| `migration-lifecycle.log` | **9 passed / 17.31s**。 |
| `security-green.log` | T01 两项 P2 的草稿修复公开 red→green：**7 passed / 13.05s**，后来包含在目标汇总通过项中。 |

唯一目标失败：

```text
tests/test_issue_113_t01.py::test_two_independent_restarts_keep_waiting_without_calls
```

子进程重启后 `prompt_preview=None`，在比较 `.items()` 时失败。**当前有依据但未证实的解释**：新精确配置指纹包含 `timeout_seconds`；准备时测试配置为 5，子进程环境遗漏 `VISUAL_TIMEOUT_SECONDS`，使用默认值造成依赖失配。worker 的一次字符串替换未命中，停止时该字段仍未加入。恢复后先核对再定向验证，不能削弱配置指纹、跳过断言或把推断写成已修复。重登后的 `command_context` 本来应变化，冻结内容／摘要应保持。

此外：最后一个新增 E501 表达式已拆行，但**未最终重跑 lint**；dispatch 的 36 条 E501 属基线既有，不借本票全库格式化。停止时 `git diff --check` 为 0。`docs/acceptance/issue-112-t02.md` 尚不存在；最终审查、验收记录、提交、同步集成 tip 与 merger 验证均未完成。

## 6. 安全审查的正确解释

T01 合入后的独立静态审查报告位于 `D:\Desktop\issue-112-context\t01-security-review.md`：

- F1：旧 job 的提示词兼容读取仅凭未撤销批准，未完整验证原摘要／角色／版本。
- F2：旧服务指纹归一化时丢 URL query，与 adapter 实际消费配置不完全相同。

两项均交给 T02 worker 修复，公开测试已在**未提交草稿**通过；不能称集成 HEAD 已修复。F2 保留旧历史指纹，新冻结契约采用不公开配置内容的精确指纹；更强绑定与当前重启测试失败可能相关。

T02 审查按用户中断停止，尚未证实可报告 P1/P2，**不等于完整审查无发现**。重放前重验顺序、generations→adapter→发布链、事务 helper 与竞争调度仍需补审；具体已读范围和未知项见该报告，不重新猜测漏洞。

## 7. 可用运行环境与保护事项

有效 runner：

```powershell
$py = 'C:\Users\28425\.codex\worktrees\issue-112-integration\新建文件夹\backend\.venv\Scripts\python.exe'
Set-Location 'C:\Users\28425\.codex\worktrees\issue-112-t02\新建文件夹\backend'
# 仅在用户授权恢复后，先重现当前唯一失败；临时目录保持隔离。
& $py -m pytest -q tests/test_issue_113_t01.py::test_two_independent_restarts_keep_waiting_without_calls --basetemp 'D:\Desktop\issue-112-context\resume-t02-restart'
```

- 此 venv 从本机合法 Python 创建，`.pth` 仅引用原树已安装依赖，不指向集成业务代码；worker 已核验父／子进程实际导入 T02 自己的应用源码。
- **原用户树的 venv Python 不可复用**：实测临时文件权限失败。系统 Python + 临时 `PYTHONPATH` 也不适合独立进程测试，因为子进程设施会覆盖该变量。不要改原树依赖或系统权限来绕过。
- 测试从目标 worktree 的 `backend` 执行，以真实临时 SQLite 与受控 adapter 为主要 seam；不能用私有 helper mock 代替公开 HTTP 业务证明。
- Ruff 使用 `--no-cache`；旧全量基线有 218 条历史错误。前端依赖可复用集成根的 node_modules junction；没有运行真实供应商或生产数据迁移。
- 既有 8000／8090 服务归属未核实，不能连接它们作验收或终止。需要运行服务时另建隔离数据根和新 loopback 端口；不读真实凭据、业务库或改运行配置。
- 文档／测试／日志／脚手架放应用本体之外。迁移在 `backend` 管理，现有包内副本需保持同步；不为清理用户无关文件扩大范围。

旧基线结果只查 [基线记录](../../docs/acceptance/issue-112-baseline.md)，不重复跑来替代新契约验收。

## 8. 若用户要求继续：最短接续路线

1. 核对上述两个 worktree HEAD、T02 dirty 状态和 GitHub #114；保留所有草稿，先读 worker 与审查检查点。
2. 围绕重启预览缺失作诊断；验证超时配置失配假设，修 fixture 或实际缺陷后先跑该单例，再跑 worker 检查点记录的同一目标汇总。
3. 完成基础安全定向审查及最终 lint、`diff --check`、迁移副本一致性；保留所有失败／skip／未测边界，生成 T02 验收记录。
4. T02 worker 提交后将集成 tip merge 入自身分支；由 **merger 子代理** 合入集成并重跑主验收。只有实际验收成立后才按仓库规则评论／关闭 #114、查询下一 frontier。
5. 后续继续用户已授权的 `implement-spec` 机制：一票一 branch/worktree、TDD、稀疏上下文指针、可并行 frontier 独立写集；最终双轴 code-review、修复及收尾。未完成票不得伪结案，最终真实设备／环境门槛不得跳过。

## 9. Suggested skills / 建议技能

- **`implement-spec`**：用户授权恢复整项实施时调用；路径 `C:\Users\28425\.codex\skills\implement-spec\SKILL.md`。本次显式调用该技能已授权子代理机制；新会话仍先确认用户当前意图。
- **`tdd`**：所有实现／修复纵切使用；路径 `C:\Users\28425\.codex\skills\tdd\SKILL.md`，一并读 tests.md、mocking.md。测试 seam 已确认，不再需求访谈。
- **`diagnosing-bugs`**：处理当前重启预览失败，先证据后假设；路径 `C:\Users\28425\.codex\skills\diagnosing-bugs\SKILL.md`。
- **`code-review`**：最终集成按固定起点 `311b215687e8a87fa97d7c643eca5969645bb2d9` 分 Standards／Spec 两轴审查；路径 `C:\Users\28425\.codex\skills\code-review\SKILL.md`。目前只做过局部定向审查，未完成该最终流程。
- **`handoff`**：需要再次中断转交时更新检查点；路径 `C:\Users\28425\.codex\skills\handoff\SKILL.md`。

有 Skill 工具则调用；没有该工具时直接读取相应 SKILL.md 并执行，不虚构技能已调用或测试已通过。

## 10. 子代理与进程检查点

实现者 `Avicenna`：`01a19ed5-16c9-7220-ad6b-6d7611de3ee8`；审查者 `James`：`01a19f41-e727-7ef3-b2f7-67a02a4fc1d2`。两者已确认停止并返回外部检查点，不再让它们继续实施。代理 ID 可能仅在原会话有效；文件和工作树才是新会话的可靠交接依据。

主代理及 worker 在停止时分别扫描本任务 Python 进程，未发现本任务 pytest／uvicorn 残留；没有终止不相关服务。后续启动／清理前仍重新核对进程归属。本次仅完成交接文档，**没有完成 #112，也没有提交 T02。**