# 正常澄清被误报“模型响应无法解析”修复

日期：2026-11-10（Asia/Shanghai）。

## 现场与复现

本地后台 18:46:12 和 18:46:39 的两个 S03 需求助手调用被记为 CONTRACT_INVALID，界面呈现 LLM_RESPONSE_INVALID。两份返回均为完整 JSON，status=needs_input，包含非空结构化方向问题，顶层 issues=[]；Pydantic 字段验证通过，失败来自旧的“needs_input 必须至少一个 issue”语义校验。用户正常选方向不应被当成模型故障。

`python -m pytest -q backend/tests/test_clarification_contract_compat.py --tb=short` 修复前复现两项失败，HTTP 503 和用户提供的错误文案完全一致；不是仅验证独立 JSON 解析函数。

另一处现场数据是尺寸 update 重复系统默认且 evidence_ids=[]。这不是用户修改，不能伪造来源；若照旧处理会在修复第一个错误后触发 REQUIREMENT_SOURCE_INVALID。

## 修复边界

- requirements_assistant 的正常 needs_input 由非空、有效的 structured clarifications 证明，不再额外强制 issues；旧角色的错误协议不变，无问题的 needs_input 仍拒绝。
- 仅忽略“无证据、set size、值等于实际配置且与当前结果一致”的冗余声明，不标成用户已回答。未知来源的商品、文案及其他修改继续拒绝；尺寸冲突仍经过原处理链。
- 模型指令同步说明正常澄清可返回 issues=[]，系统默认不放入用户修改 updates。
- 原始契约拒绝原因保存在内部 role call 错误记录，不只保留笼统文本。
- 手动继续 S03 时可复用此前被旧契约拒绝的有效澄清，但必须匹配当前 run、revision、全部用户消息、参考资产和配置尺寸，并且是需求助手的澄清结果。复用仍走正常需求来源和问题处理链；不自动调用模型、不直接生图、不伪造用户回答。原失败审计保留，另记 saved_clarification_reused。
- 提示词展示、编辑及点击确认后生成的规则保持有效。

## 验证

回归覆盖现场 shape、默认尺寸冗余、刷新保留问题、手动恢复且模型调用次数不增加、缺失问题仍拒绝、无来源商品修改仍拒绝。测试使用可控适配器，不触发付费调用。

## 最终结果

- 新增 6 项回归全部通过。根目录 `npm run check` 全部通过：30 个前端文件共 235 项、后端 Ruff、186 项后端测试（243.54 秒）。日志见 [check.log](evidence/clarification-contract-repair-20261110/check.log)。
- 两份现场原始返回已用修复后的契约重新验证，均为 needs_input、一个有效结构化问题；没有重新调用模型。
- 已重新构建并更新本地后台。实际运行容器再次核实两份失败记录满足原消息／run／revision／参考／尺寸匹配条件，可通过“继续流程”复用原澄清。后台及前端 API 代理健康检查均正常。
- 保留原失败记录，不代替用户选择选项或确认生图；本轮未触发供应商调用，未创建图片任务。
