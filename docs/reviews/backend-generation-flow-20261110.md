# 后端生图流程审查（2026-11-10）

> 后续修复：原报告的四项问题已通过原始接口探针复测。本轮还补齐了尺寸协商后的文案状态保留，详见 [修复验证记录](backend-generation-flow-fixes-20261110.md)。下文保留审查时的历史发现和证据。

结论：当前 `collaborative` 默认链路能够完成单轮生成，但尚不能认为连续聊天、附件识图交接和多轮需求累积可靠。10 个隔离行为探针中，4 个失败、6 个通过；失败对应下面四个可复现问题。

审查对象是当前工作区（含审查前已有的未提交修改），HEAD 为 `1a200a5407fafb72b6b95da80bf024caf987eeba`。关键源码 SHA-256 见 [baseline.json](evidence/backend-generation-flow-20261110/baseline.json)。审查期间业务文件存在外部更新；本报告列出的四项均在最后一轮探针中再次复现。早期发现的两项取消文案问题已在外部更新后通过复测，不列为当前缺陷。

本轮只新增审查报告及显式执行的探针，不修改业务实现。所有探针使用临时 SQLite、ASGI 接口和可控模型/图片供应商替身，无真实供应商调用、计费或真实业务数据库操作。因而可以验证程序的流程行为，不能据此保证真实模型语义理解或真实供应商可用性。

## 可复现问题

### 1. [P1] 首次图文消息的需求结果在识图前缓存，识图后没有重新整合

- 入口：创建空项目，暂存商品图，再发送“按这张商品图帮我做一张电商主图，参考商品外观，不需要文字”。
- 实际：角色调用顺序为 `requirements_assistant → reference_analyst`；需求助手从未收到识图报告。即使识图报告已经明确给出商品主体，系统仍问“请补充具体商品主体”，停在 S03。
- 原因：`services/messages.py:859` 先分类本条消息，`workflow/consolidated.py:242` 缓存整个需求输出；附件绑定及识图在后。S03 的 `run_requirements` 在 `consolidated.py:47` 无条件复用缓存，仅替换消息证据 ID，没有检查参考图及报告是否变化。
- 建议：附件用途判断与完整需求抽取分开，或为缓存绑定参考图/报告摘要；识图后必须让需求助手实际消费报告。不能靠模型在没看到图时猜中商品。
- 探针：`test_first_image_request_passes_analysis_to_requirements_before_asking`。

### 2. [P1] 已完成生成后的自然语言修改被当成普通交流

- 入口：第一张图完成后，在同一对话发送“把背景改成蓝色，重新生成一张”。模型正确分类为 `requirements_change`。
- 实际：HTTP 200，但图片提交次数仍为 1，没有新一轮生成。
- 原因：`services/messages.py:391` 的需求修改分支只处理 S01—S07；S10/completed 落入 `CONVERSATION_ACK`。该消息随后以 `conversation` 存储，而需求读取只选 `workflow` 消息，这次修改也不会进入后续需求上下文。
- 建议：为完成状态明确接入新版本创建及需求重新整理，保存这次修改为新一轮有效输入，保持旧结果历史独立。
- 探针：`test_completed_chat_can_generate_requested_revision`。

### 3. [P1] 更换参考图时复用旧视觉产物，触发过期依赖而停住

- 入口：第一张图完成后，在同一对话上传新参考图并要求重新生成。
- 实际：HTTP 409 `RUN_SUPERSEDED`，工作流停在 `S05/active`，没有完成新的交付。
- 原因：`workflow/consolidated.py:262` 的缓存捷径只看旧创意、视觉产物是否 `approved`，没有比较它们依赖的需求草稿和参考报告。新的需求/参考报告形成后，捷径直接进入最终需求合成；`stages/s05.py:941` 检测到旧视觉依赖与当前依赖不一致，抛出过期错误。当前路径也没有把该异常转成一个可继续的明确终态。
- 建议：只有依赖摘要完全一致才复用视觉产物；需求/参考图改变时重新策划，或建立全新 run。异常必须落入明确的失败/恢复状态。
- 探针：`test_replacing_image_can_generate_exactly_one_new_version`。

### 4. [P1] 用户只补充商品主体时，尚缺的指定文案被自动跳过

- 入口：用户请求“给我的商品做张主图，要加一句促销文案”；助手询问商品主体和文案原文。下一轮用户仅回答“米色托特包”。模型仍返回 `needs_input`，明确继续询问文案。
- 实际：后端丢弃文案问题，继续视觉策划并提交一次图片生成。
- 原因：`workflow/collaboration.py:108` 以最新一条消息是否含“文字/文案/标语/名称”为条件保留文案问题，没有保留前文已经建立但尚未解决的“必须提供准确文案”状态。
- 建议：把文案需求及其未解决状态持久化；只有用户提供原文或明确取消后才解除阻塞。问题是否必要不能只根据本轮关键词判断。
- 探针：`test_missing_copy_stays_pending_when_subject_is_answered`。

## 已验证可用的路径

1. 文本需求首次生成可完成自动交付（在后续修改探针的前置步骤中验证）。
2. 参考图在需求整理前已完成识图时，整条链可以完成：`reference_analyst → requirements_assistant → visual_planner → generation_engineer`，图片提交带有实际参考图字节，只有一次图片提交。
3. 需求助手、视觉策划和生成工程师分别发生一次受控超时后，显式 `continue_workflow` 均能完成交付，每次仅一次图片提交。
4. 用户明确取消旧文案后，最新需求中的文字能够清空；等待文案原文时说“不要文案了，保持无字”，也不会再把取消指令当成画面文字。这两项在审查早期失败，工作区外部更新后已通过复测。

## 验证及证据

探针刻意命名为 `audit_*.py`，不会加入默认 `test_*.py` 收集；需显式执行，以便保留当前缺陷的复现证据而不改变普通测试集。

在仓库根目录执行：

```powershell
.\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/audit_generation_flow_20261110.py --tb=short --show-capture=no
```

结果：**4 failed, 6 passed**。原始输出见 [probes.log](evidence/backend-generation-flow-20261110/probes.log)，机器可读结果见 [probes.xml](evidence/backend-generation-flow-20261110/probes.xml)。新增探针的 Ruff 检查通过。

审查早期单独执行规定的后端针对性集合，结果为 **125 passed, 2 failed**（之后工作区发生外部更新；这不是最后一轮结果）：

- `test_v2_t04_reference_analysis.py::test_reference_report_contract_negatives_never_create_artifact[extra-SERVICE_UNAVAILABLE]`：实际错误码 `LLM_RESPONSE_INVALID`，测试仍期望 `SERVICE_UNAVAILABLE`。
- `test_v3_p1_t06_gate.py::test_three_personas_cover_every_active_model_role`：当前角色集多出 `reference`，旧“三人设”断言失败。

扩展运行角色链、旧全链路验收和恢复集合时为 **52 passed, 13 failed**。其中失败涉及旧最终授权/恢复夹具和旧交付契约；本轮没有把这些失败直接计为十三个业务缺陷，也没有以这些旧用例替代当前 `collaborative` 链路的接口探针。

`npm run check` 最后一轮：依赖图、类型检查、29 个前端测试文件共 **226 个测试**、后端 lint 均通过；后端测试 **131 passed, 1 failed**。失败为 `tests/test_gate_free_workflow.py::test_reference_role_joins_and_delivery_has_no_review_calls`，参考图用例返回 `REQUIREMENT_DRIFT`。这项失败需要继续区分供应商替身的参考用途交接与实现约束，未额外计入上面的四个已定位缺陷。审查早期的错误码/人设测试失败已不出现在这一轮结果中。

## 修复顺序

先修首次附件的识图上下文交接、完成后的消息路由、视觉缓存依赖校验；再将文案的未解决状态跨轮持久化，替换只依据本轮关键词的筛选。随后让四个失败探针转绿，再对真实供应商执行一轮图文输入、改图、多轮补充及失败恢复验收。



