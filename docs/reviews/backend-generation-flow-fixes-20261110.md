# 后端生图流程修复验证（2026-11-10）

承接 [后端生图流程审查](backend-generation-flow-20261110.md)。当前工作区已包含四项问题的修复，本轮核对实现、重新运行原始审查探针，并补齐尺寸协商导致文案要求丢失的边界情况。未覆盖或回退其他工作区修改。

## 四项审查问题的当前实现

| 原问题 | 修复后的行为 | 实现入口 |
|---|---|---|
| 首次附件没有把识图结果交给需求助手 | 附件进入工作流时清除识图前的需求缓存，识图完成后由 S03 消费实际参考报告，避免重复进入需求整理 | `main/backend/app/services/messages.py::_send_attachment_action`、`main/backend/app/workflow/stages/s02.py::analyze` |
| 生成完成后，聊天修改没有进入下一轮 | 在完成状态把需求修改/提示词修改接入 `request_new_version`，保存工作流消息并建立新 run，重新整理并交付 | `main/backend/app/services/messages.py::_plan_message_action`、`main/backend/app/workflow/stages/s10.py::_request_new_version` |
| 更换参考图复用旧视觉方案，报 RUN_SUPERSEDED | 完成后的附件修改建立新 run；协作模式重新执行视觉策划，不走只检查旧产物 approved 状态的复用捷径 | `main/backend/app/workflow/consolidated.py::plan_visual`、`main/backend/app/services/messages.py::_send_attachment_action` |
| 跨轮补充商品时，尚缺的文案被丢弃 | 保留尚未解决的文案问题，直到收到文案原文、明确取消或合法的清空更新，不只依赖本轮关键词 | `main/backend/app/workflow/collaboration.py::accept_requirements` |

## 本轮补齐的边界修复

原有修复在普通补充路径上通过，但尺寸不受支持时仍会以单个尺寸问题覆盖全部澄清问题。因此，用户接受尺寸并补充商品后，缺失文案会再次被跳过。

本轮先增加失败回归：请求 16:9 商品主图及促销文案，接受当前可用的 1024x1024 并补充商品名称，此时仍应等待文案。修改前实际已经生成并完成，违反预期。

`accept_requirements` 现将尺寸问题与其他尚未解决的必要问题一起保存，并去除原有重复尺寸问题。上述场景在用户真正提供“新品上市”后才生成，且只提交一次。

## 额外回归覆盖

新增测试均在 `backend/tests/test_collaborative_revisions.py`，已被仓库检查脚本收集。

- 完成后的自然语言修改实际进入新提示词（蓝色背景），不是只检查生成次数。
- 同一个修改请求携带相同 request_id 重发，不额外调用模型或图片生成。
- 新旧版本分别拥有已完成 run 和已发布 delivery，两张历史图片仍可读取。
- 用户在需求澄清期间上传参考图，需求助手消费识图报告，视觉策划、提示词编制及图片提交各执行一次。
- 尺寸协商后仍保存未提供的文案，补齐原文后完整交付一次。

## 验证

```powershell
.\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/audit_generation_flow_20261110.py backend/tests/test_collaborative_revisions.py --tb=short --show-capture=no
```

结果：**23 passed**，包括原始审查 10 项及正式回归 13 项。

新增测试和本轮修改的后端模块 Ruff 检查通过。

仓库根目录 `npm run check` 全部通过：依赖图检查、前端类型检查、29 个前端测试文件共 **228 个测试**、后端 Ruff，以及 **148 个后端测试**全部通过。

所有行为验证使用隔离 SQLite、实际 ASGI 路由及可控模型/图片供应商替身；没有调用真实付费模型或操作真实业务数据库，真实供应商的可用性和语义输出质量不属于本轮验证结论。

