# T13 真实 LLM 需求整理验收记录

- **Issue**：GitHub #27《接入真实 LLM 需求整理与结构化输出》
- **父规格**：GitHub #14；SRS AC-01（LLM）/AC-04、BE-AC-02/03
- **记录日期**：2026-10-30（Asia/Shanghai）
- **状态**：**通过（v1 文本基线）**。真实 LLM 已完成一次“描述→澄清→完整需求卡→确认”和一次“反馈→重新整理→确认”；可选多模态 organizer 是单列的额外扩展，不并入本结论。
- **证据性质**：真实 API 调用与受控适配器测试分开记录，详见 §2 与 §4；不以受控测试替代真实事实。

## 1. 环境与配置

| 项 | 值 | 说明 |
|---|---|---|
| 适配器 | `openai-chat` | 显式注册；见 `build_default_registry()` |
| Base URL | `https://api.ai-pixel.online/v1` | 与 T12 决策记录一致 |
| 模型 | `deepseek-v4.1-flash` | 真实调用使用 |
| 鉴权 | Bearer，服务端持有 | 不进入浏览器、日志、数据库或文件名 |
| 超时 | 60 秒 | `LLM_TIMEOUT_SECONDS=60` |
| 结构化输出请求 | `response_format=json_object` + 严格系统提示 | 仍由客户端严格解析，信任供应商响应不是设计前提 |
| 配置状态 | `configured=true`、`ready=true`、`verification=not_checked` | 真实调用成功不等于持久认证 |

## 2. 真实工作台路径证据

验收项目：`64a44549-a929-4a26-8775-37744908b196`。以下响应均来自 `.scratch/t15-acceptance/responses/` 的脱敏 JSON，不能从仓库直接复现供应商响应，但可按 T15 §复现命令重跑。

### 2.1 描述与澄清

| 步骤 | 请求/响应 | 结果 |
|---|---|---|
| 用户消息 1 | “我想做一张商品图” | 保存为 sequence 1，原文不变 |
| 助手回复 | “好的，请告诉我以下信息，我好帮你整理成需求卡：1）商品是什么，主要卖点或规格；2）图片用途，例如主图、详情页、朋友圈或广告投放；3）想要什么风格，例如简约白底、生活场景、圣诞氛围等；4）是否需要在图上加文字，例如价格、活动名、卖点文案；如有参考图也可以一并发来。” | `brief_status=needs_input`，sequence 2；集中追问而非一次只问一项 |
| 用户消息 2 | 商品、用途、风格、构图、无文字 | sequence 3；原文保留 |
| 助手回复 | “信息已完整，已按‘北欧风白色陶瓷马克杯、电商平台主图、极简北欧浅灰渐变背景、正方形居中’整理成需求卡，请确认。” | `brief_status=review`，sequence 4 |

### 2.2 完整需求卡

真实 review 卡片（revision 4）：

| 字段 | 值 |
|---|---|
| `product_description` | 北欧风白色陶瓷马克杯，容量350ml，哑光釉面，原木色手柄 |
| `purpose` | 电商平台主图 |
| `style` | 极简北欧风格，浅灰渐变背景，柔和自然光 |
| `composition` | 正方形构图，商品居中，杯身约占画面70%，正面微侧角度展示 |
| `text_content` | （空字符串） |
| `image_size` | 1024x1024（服务端固定，不由模型输出） |
| `prompt` | 完整中文电商主图提示词，覆盖主体、用途、风格、构图、文字约束；见 `responses/message-complete-message-1.json` / project detail |

**事实边界核对**：

- 商品描述逐字来自用户输入；未凭空增加价格、认证、品牌、材质升级等未知事实。
- `text_content` 保持空字符串，没有为了“完整”编造画面文字。
- 风格与构图字段可见地进入卡片和最终 prompt；没有把参考图（此时不存在）说成已使用。
- `prompt_revision=4`，与当前 revision 一致，满足确认前置条件。

### 2.3 确认

- 确认 revision 4 返回成功，`confirmed_revision=4`、`prompt_revision=4`、`brief_status=confirmed`。
- 重复确认同一 revision 返回 200，状态和 revision 不变（幂等）。
- LLM 阶段结束前 generation 数量为 **0**；确认本身不发起生图（`test_confirm_success_is_idempotent_and_makes_no_model_calls` 亦覆盖）。
- 配置状态仍返回 `verification=not_checked`，没有因为真实调用成功而改写为“已验证”。

### 2.4 反馈整理

在 v1 生成成功后，用户反馈（存储消息的原文还包含“请输出修改后的完整需求卡，五个字段都要有值；没有画面文字时 text_content 为空字符串。”，这是验收请求的一部分）：

> 保持商品和构图不变，把背景改成暖米色，并在杯身右侧加一道柔和高光，整体更温暖。

结果（revision 7）：

- 商品描述保持完全不变；
- 构图保留“正方形/居中/70%/正面微侧”，只增加“右侧柔和高光”；
- 风格从“浅灰渐变背景”改为“暖米色渐变背景，整体色调温暖”；
- `text_content` 仍为空；
- 生成新的 `prompt_revision=7`，不是复用旧 prompt；
- 反馈消息原文保留，assistant 消息带新的 brief snapshot。

首次真实反馈整理曾返回 **502 `LLM_CALL_FAILED`**，服务安全保留原草稿/消息，没有自动重发；验收脚本显式重试一次后得到 review，随后确认并生成 v2。这是“认证/额度/网络/超时/无效输出映射安全错误并保留持久状态”的真实旁证。

### 2.5 真实路径检查汇总

| 检查 | 结果 | 证据 |
|---|---|---|
| 真实完整回复映射为 `needs_input` | PASS | 首次回复 |
| 真实完整回复映射为 `review` | PASS | 第二次回复 |
| 用户原文保留 | PASS | sequence 1/3 |
| assistant 消息持久化并带 snapshot | PASS | sequence 2/4/7 |
| 五个字段齐全 | PASS | review revision 4/7 |
| prompt 非空且随卡片 revision | PASS | prompt_revision 4/7 |
| 服务端固定 1024x1024 | PASS | 卡片 image_size |
| LLM 阶段零 generation | PASS | t15-summary check 10 |
| 确认幂等 | PASS | 重复 confirm |
| 真实调用后 config 仍 `not_checked` | PASS | config-status-after-restart |
| 反馈保留未要求改变的约束 | PASS | 商品/构图字段对比 |

## 3. 结构化输出与失败处理

### 3.1 严格校验

- 只接受 `status` 为 `needs_input` 或 `review`；
- 非对象、缺 assistant 内容、字段非字符串、review 缺五字段、prompt 为空或超长均拒绝；
- `needs_input` 可只带部分字段，空值/缺省归一为空字符串；
- 所有文本和字段有长度限制；
- 未知 JSON 字段不写入业务字段；
- 无效结构不会被当成“部分成功卡片”，不会触发第二次“JSON 修复”模型调用；
- 上游返回无效时，用户原文和旧卡片保留，错误以安全业务错误呈现。

### 3.2 错误映射与测试

| 情况 | 映射 | 测试/真实证据 |
|---|---|---|
| 401/403 | `LLM_AUTH_FAILED` | `test_http_rejection_is_classified_without_leaking_credentials` |
| 400/413（含上下文超限类拒绝） | `LLM_REQUEST_REJECTED` | `test_status_codes_are_classified_as_safe_provider_errors[400/413]` |
| 429 | `LLM_RATE_LIMITED` | `... [429]` |
| 5xx | `LLM_UPSTREAM_ERROR` | `test_server_error_leaves_acceptance_uncertain` |
| 超时 | `LLM_TIMEOUT` | `test_timeout_leaves_acceptance_uncertain` |
| 连接错误 | `LLM_NETWORK_ERROR` | `test_network_error_leaves_acceptance_uncertain` |
| 无效 JSON / 结构 | `LLM_RESPONSE_INVALID` | `test_invalid_content_is_rejected...`、字段/prompt 测试 |

**上下文限制**：供应商没有公开上下文窗口，本次没有构造超限真实请求；受控测试只证明 400/413 会安全失败并保留状态。真实超限阈值仍是未知外部限制，不能用受控测试冒充“已测得真实上限”。

## 4. 参考图边界：v1 基线与可选扩展

T13 的基线是**文本 organizer**：请求只带已保存消息、当前 Brief 和 `has_reference_image` 布尔值，不读取参考图字节。

- 测试 `backend/tests/test_t09_reference_versions.py::test_text_only_organizer_keeps_reference_flag_without_image_bytes` 断言 organizer 收到存在性标志但不收到图片字节。
- T15 的 reference 阶段在实际 `.env` 中配置了 `LLM_VISION_MODEL=gpt-6.1-sol`，因此该次 organizer 走可选多模态扩展，参考图以 data URI 进入 LLM；不能把该次 reference exchange 的字段来源笼统归为“仅文本”。
- 该扩展的受控测试为 `test_reference_image_is_sent_as_a_multimodal_content_part` 和 `test_reference_image_uses_its_own_base_url_and_key`。
- 若按 v1 严格基线部署，应保持 `LLM_VISION_MODEL` 为空；这不影响参考图字节进入 VISUAL，也不改变 T14/T15 的生图链路。
- 本记录不把可选扩展写成 T13 通过条件，也不删除已有能力；它在 T12 决策记录中明确单列。

## 5. 受控传输测试

真实 API 与受控测试分离：

- `test_organize_sends_chat_request_and_parses_review`：POST `/v1/chat/completions`、Bearer、`model`、`response_format`、messages 顺序与一次调用；
- `test_needs_input_accepts_partial_fields_and_ignores_prompt`：部分字段和忽略 prompt；
- `test_json_fence_is_accepted`：兼容 Markdown 围栏但仍严格解析；
- `test_review_requires_a_prompt` / `test_review_requires_all_brief_fields`：缺字段拒绝；
- `test_http_rejection...` / 状态码参数化 / 5xx / 超时 / 网络：异常分类和脱敏；
- `test_builtin_registry_exposes_openai_chat_and_config_is_ready`：显式注册与配置就绪。

这些测试不调用真实供应商，不能替代 §2 的真实证据。

## 6. 安全与数据边界

- Key 只存在于后端 `.env` / SecretStr；不写入浏览器响应、OpenAPI、日志、数据库或文件名。
- 适配器错误信息最多 200 字符，并替换 Key、URL；真实 502 的存储错误为安全业务码 `LLM_CALL_FAILED`，不暴露供应商原文。
- 证据扫描：`docs/acceptance/evidence/secret-scan.json`，184 个文件、3 个 Key 值、0 匹配。
- 真实响应中的商品描述和 prompt 已脱敏保存；不包含认证头、Key、签名 URL 或供应商完整响应。

## 7. 结论与未完成项

**通过**：真实 LLM 文本路径的澄清、完整卡片、确认、反馈重整理和状态持久化；严格结构校验；400/413/429/5xx/超时/网络的安全映射；零生图确认；配置不因真实调用变成“已验证”。

**外部限制/未覆盖**：

- 供应商上下文窗口和真实超限阈值未知；
- 可选视觉 organizer 在 reference 阶段被实际启用，属于 v1 之外的扩展；严格基线需清空 `LLM_VISION_MODEL`；
- 真实图像质量、浏览器 UI 行为不在本记录范围（分别见 T14/T15 与 #13）。

## 8. 证据索引

- 真实 harness 汇总：`docs/acceptance/evidence/t15-summary.json`（52 checks 全部 PASS，其中 LLM 相关见 check 1–16）
- 真实响应：`.scratch/t15-acceptance/responses/message-vague.json`、`message-complete-message-1.json`、`message-feedback*.json`、`project-detail-llm.json`
- 配置状态：`docs/acceptance/evidence/config-status.json`
- 适配器：`main/backend/app/adapters/openai_chat.py`
- 测试：`backend/tests/test_openai_chat_adapter.py`（19 passed，含新增 400/413/429/network）
