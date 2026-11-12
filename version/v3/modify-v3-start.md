# modify_v3 实施启动记录：基线与确认契约

日期：2026-11-03（America/Toronto）。状态：**启动批次已执行；本批完成步骤 1 的隔离基线与步骤 2 的契约／权威规范对齐，不代表步骤 3—6 或 MV3 验收已完成。**

依据：[modify_v3](../../version/v3/modify_v3.md) 第 5 节。该文开头“本次只交付文档”记录的是上一轮交付边界；本轮用户已明确要求依据该文开始实施。仍遵守 R1 → R2 → R3，不把历史 issue 关闭或审计记录当当前证明。

## 1. 代码基线与保护范围

- 源码 HEAD：`311b215687e8a87fa97d7c643eca5969645bb2d9`（`fix(workflow): keep unmapped issue text out of safe view`）。
- 工作目录：`D:/Desktop/新建文件夹`。用户已存在的 `vesion/` 删除、未跟踪 `version/`、浏览器临时目录、旧 pytest 副本、DAG 图片和 ZIP 全部保留；不清理、不恢复、不提交这些用户变更。
- 不读取或写入真实数据库正文；不更改 `backend/data`、`main/backend/data`、`.env`、备份、运行配置或凭据，不执行迁移／认领／数据根切换。真实来源未另行确认，不在本批猜测来源或制作所谓已验证迁移备份。
- 当前采用用户已提供的 `version/v3/SRS_v3.md` 作为规范位置；仅修正本批权威文档指向它的路径，不代办整个 `vesion` → `version` Git 重命名。
- 测试、日志、文档修改前副本及静态检查脚本全部位于 `main/` 外。本批没有修改应用前后端、依赖、迁移或测试断言；没有改变第 12 节产品参数。
- 开始时的 HEAD、完整状态与差异统计分别保存在 `.scratch/modify-v3-20261103-start/baseline-head.txt`、`baseline-status.txt`、`baseline-diff-stat.txt`；规范原文备份在同目录 `before-docs/`。原始状态较大，包含历史测试残留，只作本地保护证据，不进入发布包。

## 2. 当前实现复核

以下为本次源码观察；不是对新契约的验收结果。

| 已复核事实 | 当前入口／符号 | 后续动作 |
|---|---|---|
| 需求确认在同一事务受理意图／用量／占用，并调度后续周期 | `main/backend/app/workflow/stages/s06.py::_confirm_atomic`、`_after_acceptance` | 保留准备前的原子受理，但不再把它当图片最终授权 |
| 后台周期 S07 编制／审查后立即调用 S08 `submit_generation` | `main/backend/app/generation_cycle.py::_run_cycle` | 在两者间建立持久化等待，后台不能自动穿透 |
| S08 当前可用动作允许提交，参数并不要求“用户看过的提示词快照”；创建图片记录才推进原意图执行 | `main/backend/app/workflow/stages/s08.py::available`、`_submit`、`_create_job` | 增加最终快照校验、持久授权与唯一执行尝试，不仅删除一次自动调用 |
| 工作流会公开 `approved` 产物正文，但没有独立待确认快照／最终授权投影 | `main/backend/app/workflow/state.py::build_workflow_view`、`schemas_workflow.py::WorkflowView` | 保留候选隔离；批准、失效、执行事实须与安全预览一致校验 |
| `prompt_plan` 有正向／负向字段；图片请求只有一个 prompt，S08 当前保存正向文本 | `workflow/contracts.py`、`adapters/base.py::GenerationRequest`、`stages/s08.py::_create_job`、`services/generations.py` | 统一编制并冻结实际请求；在准备阶段解决负向内容的实际传递，不能只在 UI 展示后宣称已执行 |
| 上传接口只接收一个文件和 revision，项目只有一个当前参考字段 | `routers/assets.py::upload_reference`、`models.py::Project` | 不伪装多附件；补消息／稳定资产版本关联，保持既有单参考图产品边界 |
| 消息入口传 content 与 revision，无显式消息附件列表 | `routers/messages.py::send_message_endpoint`、`schemas.py::SendMessageRequest` | 图文共同发送及历史消息图片须有真实后端关系，不用当前项目缩略图解释旧消息 |
| 标题查询与重命名接口当前已存在 | `routers/projects.py::list_projects_endpoint`、`rename_project_endpoint` | 复用并回归本人过滤、分页与元数据语义；不重复实现旧文档所谓缺失能力 |
| 聊天挂载工作流与预览，存在独立成果页及发布自动跳转 | `main/src/pages/ChatPage.vue`、`ResultsPage.vue`、`components/workflow/WorkflowPanel.vue` | 后端证据成立后重建唯一时间线，图片内联；旧 URL 只读定位 |
| 原启动修复不主动调用模型；它修复的是旧角色／执行记录 | `main/backend/app/services/recovery.py::repair_leftover_state` | 补新等待快照的独立进程重启证明，不以既有“重启零调用”测试推导新停点成立 |

## 3. 本次执行的基线验证

所有命令从仓库根执行。后端使用现有 `backend/.venv/Scripts/python.exe`、真实临时 SQLite 与受控 adapter；显式 `--basetemp` 位于本批 `.scratch/`，`APP_DATA_DIR` 也指向隔离根。前端为现有 Vitest/jsdom 测试，不冒充真实浏览器；运行仍有既有 `fireEvent.change` 用法警告，本批未修改测试来消除或掩盖它们。

| 验证 | 实际结果 | 日志 |
|---|---|---|
| R1 gate＋历史恢复 gate：`test_t64_r1_gate.py`、`test_t61_history_recovery_gate.py` | **10 passed，17.05s，exit 0** | `.scratch/modify-v3-20261103-start/r1-gate.log` |
| R2 gate／生成周期／重放漂移／额度／恢复／结案：`test_t70_r2_gate.py`、`test_t65_generation_cycle.py`、`test_t66_replay_drift.py`、`test_t67_generation_quota.py`、`test_t68_recovery.py`、`test_t69_local_closure.py` | **46 passed，77.92s，exit 0** | `.scratch/modify-v3-20261103-start/r2-gate.log` |
| `npm run typecheck` | **exit 0** | `.scratch/modify-v3-20261103-start/typecheck.log` |
| `npm test -- --reporter=dot` | **46 files／218 tests passed，35.04s，exit 0** | `.scratch/modify-v3-20261103-start/frontend-test.log` |

可复现后端命令（分别使用新的空临时目录，不指向真实数据）：

```powershell
$env:APP_DATA_DIR = "$PWD/.scratch/<new-run>/isolated-data"
& './backend/.venv/Scripts/python.exe' -m pytest -c backend/pyproject.toml `
  backend/tests/test_t64_r1_gate.py backend/tests/test_t61_history_recovery_gate.py `
  --basetemp "$PWD/.scratch/<new-run>/r1-temp" -q
& './backend/.venv/Scripts/python.exe' -m pytest -c backend/pyproject.toml `
  backend/tests/test_t70_r2_gate.py backend/tests/test_t65_generation_cycle.py `
  backend/tests/test_t66_replay_drift.py backend/tests/test_t67_generation_quota.py `
  backend/tests/test_t68_recovery.py backend/tests/test_t69_local_closure.py `
  --basetemp "$PWD/.scratch/<new-run>/r2-temp" -q
```

这些是**旧源码行为的当前基线**，不是完整 R1/R2 重发布验收，更不是“真实提示词后最终确认”已经通过。新实现后仍须复验受影响门槛，不能原样转记通过。

## 4. 已落下的契约与规范

| 文档 | 本批变化 |
|---|---|
| [确认契约](../architecture/prompt-confirmation-v3.md) | 明确准备预算、原意图一次计数与三类占用、批准后持久化等待、实际请求冻结、最终授权原子性、失效／重放／恢复／旧入口与 PC3-01—09 后端证明 |
| [HLD v3](../architecture/hld_v3.md) | R2 从自动提交改为准备与最终授权两个事实；R3 改为唯一聊天和内联交付，保留串行门槛 |
| [门禁与工作流](../architecture/workflow-gates-v3.md) | 正常与 S12 同一最终确认守卫；修编制不补授权，发布不跳页，独立审查不减少 |
| [前端设计](../architecture/frontend-design-v3.md) | 重建目标外壳、直接会话列表、唯一输入、默认全文提示词、轻量状态、内联历史图片与只读旧 URL；改写冲突 FE3-AC，保留编号 |
| [SRS v3](../../version/v3/SRS_v3.md) | 同步 UX／GEN／LIM 与对应 AC，明确覆盖旧布局和自动生图顺序；第 12 节参数原样保留 |

计数设计保持“准备受理时计一次已受理逻辑尝试”；最终确认不再计。等待保留原占用；可证无图片执行／无在途未收敛调用的明确放弃才可结案释放，计数不退款；未知任务沿用原保守策略。**本批未引入新的免费预览、等待超时、次数口径或额度参数。** 若实现需要这些差异，先另行确认。

## 5. 顺序与尚未通过项

| modify_v3 步骤 | 本批状态 | 证据／下一门槛 |
|---|---|---|
| 1 固定基线 | 本批保护范围、当前源码与相关旧门禁已核对 | 上述日志；真实数据操作排除，未来迁移仍先核源备份 |
| 2 对齐确认契约 | 技术契约与受影响权威规范已同步 | 本批文档；静态一致性不等于业务行为已实现 |
| 3 证明后端停点 | **未实施／未通过** | 先新增能复现自动穿透的 interface 回归，再补持久化等待／最终确认；PC3-01—09 与受影响 R1/R2 复验 |
| 4 重建前端 | **尚未进入** | 步骤 3 的 MV3-05—09 后端证据成立后才接 R3 |
| 5 集成与视觉验收 | **未执行** | 本批无参考页同尺寸截图、真实浏览器、真实供应商或手机软键盘证据 |
| 6 收尾与清理 | **未执行** | 新行为成立且证明旧呈现无消费者后才删；不清用户残留 |

MV3-01—14 本批均保持待验收，不能因规范已同步或旧测试通过而标完成。下一可执行切片是确认契约 PC3-01／02／07：通过真实工作流准备、读取与重启重现“未最终确认仍自动提交”，再修后端停点；不提前制作前端假按钮。

## 6. 静态检查边界

已执行静态检查：66 个本地文档链接均存在；SRS 第 12 节参数表原样保留；V3-AC 46 条、FE3-AC 15 条、WG3-AC 12 条的编号顺序无丢失；没有应用／测试／配置／依赖差异；原有工作区状态条目全部保留；`git diff --check` 返回 0。结果保存在 `.scratch/modify-v3-20261103-start/docs-check.json`。这些只验证文档和保护范围，不是 MV3 行为证据。

证据目录在 `.scratch/`，不随发布包分发；此记录保留命令、基线、摘要与残余未知，不能依赖临时文件永久存在来宣称产品验收完成。
