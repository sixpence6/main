# T12 真实模型服务与协议决策记录

- **Issue**：GitHub #26《确定两类真实模型服务、协议和能力约束》
- **父规格**：GitHub #14；SRS/TECH_DESIGN/BACKEND_SPEC v1
- **记录日期**：2026-10-30（Asia/Shanghai）
- **状态**：**通过，含已声明外部限制**。本记录锁定 v1 使用的两类真实服务、协议、模型和能力边界；不把本记录当作真实调用、图像质量或端到端闭环的证明，后者分别见 T13/T14/T15。
- **敏感信息规则**：本文件不记录 API Key、认证头、签名 URL 或供应商完整响应；本机配置只记录必要的非敏感字段。

## 1. 决策摘要

| 角色 | 服务地址 | 模型 | 适配器 | 协议基础 | 决策 |
|---|---|---|---|---|---|
| LLM（需求整理） | `https://api.ai-pixel.online/v1` | `deepseek-v4.1-flash` | `openai-chat` | OpenAI Chat Completions 兼容；供应商有公开文档 | 用于 v1 文本需求整理 |
| LLM 可选视觉整理 | 与 LLM 相同或独立配置 | `gpt-6.1-sol`（本机可选配置） | `openai-chat` | 同一 Chat Completions 多模态消息格式 | 非 v1 基线；仅显式配置 `LLM_VISION_MODEL` 时启用，见 §2.3 |
| VISUAL（生图） | `https://usfast.ailzd.com/v1` | `gpt-image-2` | `openai-images` | OpenAI Images 兼容；**无公开官方文档**，按实测协议锁定 | 用于 v1 文生图与参考图生图，保留外部限制 |

**结论**：v1 使用上述真实服务，不使用模拟图或受控替身作为通过证据。LLM 按供应商公开文档实现；VISUAL 的供应商公开文档缺口是已知外部限制，不能声称“已获官方文档确认”，只能声称“按实测 OpenAI Images 兼容协议工作”。

## 2. LLM 服务与协议

### 2.1 连接与鉴权

| 项 | 值 | 证据 |
|---|---|---|
| Base URL | `https://api.ai-pixel.online/v1` | `backend/.env` 非敏感字段；供应商文档 |
| 适配器 | `openai-chat` | `main/backend/app/adapters/openai_chat.py`；注册表测试 |
| 模型 | `deepseek-v4.1-flash` | 本机配置；`GET /v1/models` 返回该模型 |
| 鉴权 | `Authorization: Bearer <LLM_API_KEY>` | 适配器与受控传输测试；Key 不落盘 |
| 超时 | 60 秒 | `LLM_TIMEOUT_SECONDS=60`；配置状态 API |
| 调用端点 | `POST {base}/chat/completions` | 适配器；真实调用 |
| 重定向 | 不跟随 | 适配器；重定向映射为安全拒绝 |

官方文档：

- <https://docs.ai-pixel.online/docs/normal-base-url>
- <https://docs.ai-pixel.online/docs/api/chat-completions>
- <https://docs.ai-pixel.online/docs/api/image-generations>（用于同一服务商的图像协议对照）

### 2.2 请求形态与结构化输出

适配器发送：

- `model`：当前 LLM 模型；
- `messages`：系统约束 + 只读上下文 + 已保存对话 + 本次用户文字；
- `temperature: 0.2`；
- `max_tokens: 4096`；
- `response_format: {"type":"json_object"}`；
- 不跟随重定向，按角色超时。

系统约束要求模型只输出一个 JSON 对象，状态只能是 `needs_input` 或 `review`；`review` 必须带五个需求字段和非空 `prompt`。客户端仍执行严格 JSON 解析、字段类型校验、状态校验和长度校验，**不把供应商的 `json_object` 当作可信输入**。官方文档未给出上下文窗口和最大输入 token；这是未测事实，不写猜测值。

### 2.3 文本能力、视觉能力边界与 `LLM_VISION_MODEL`

| 能力 | 结论 | 依据 |
|---|---|---|
| 纯文本需求整理 | 支持 | 真实 T13 调用；受控适配器测试 |
| `needs_input` / `review` 映射 | 支持 | T13 真实澄清与完整卡片；严格校验测试 |
| 商品/价格歧义提问 | 支持 | T13 真实 `needs_input` 回复集中追问商品、用途、风格、文字 |
| 视觉建议可见 | 支持 | 真实 review 的 style/composition 含具体可见建议 |
| 参考图仅传“存在性” | **v1 基线支持** | 默认 organizer 只传 `has_reference_image`，测试 `test_text_only_organizer_keeps_reference_flag_without_image_bytes` |
| 参考图多模态理解 | 可选扩展，不属于 v1 T13 基线 | 配置 `LLM_VISION_MODEL` 后，适配器把参考图作为 data URI 附到 organizer 请求；测试 `test_reference_image_is_sent_as_a_multimodal_content_part` |

**重要说明**：T13“参考图仅传存在性”的验收基线是文本 organizer；本机当前 `.env` 另配置了可选多模态模型，T15 的 reference 阶段实际使用了该扩展。因此：

1. 不笼统声称“所有配置下参考图都只传存在性”；
2. T13 的文本验收结论只覆盖未启用视觉 organizer 的路径；
3. 若要求严格 v1 基线，应清空 `LLM_VISION_MODEL`（`.env.example` 默认即为空），参考图仍会作为字节进入 VISUAL，但不会进入 LLM；
4. 该扩展是额外能力，不改变 v1 的 LLM 核心职责。

### 2.4 LLM 错误映射

| 上游情况 | 稳定错误码 | 证据/处理 |
|---|---|---|
| 401/403 | `LLM_AUTH_FAILED` | 安全拒绝；不泄露 Key/URL |
| 429 | `LLM_RATE_LIMITED` | 可重试提示；不自动补发 |
| 其他 4xx（含 400/413） | `LLM_REQUEST_REJECTED` | 上下文/请求拒绝；保留原文；不自动修复 JSON |
| 5xx | `LLM_UPSTREAM_ERROR` | 结果不确定；保留业务状态 |
| 超时 | `LLM_TIMEOUT` | 结果不确定 |
| 网络错误 | `LLM_NETWORK_ERROR` | 结果不确定 |
| 300–399 | `LLM_REQUEST_REJECTED` | 不跟随重定向 |
| 无效 JSON/字段/状态 | `LLM_RESPONSE_INVALID` | 不视为需求卡，不自动重试 |

受控测试覆盖 400、413、429、401、502、超时和连接错误；真实反馈整理曾出现一次 502 `LLM_CALL_FAILED` 并安全保留原状态，重试成功后完成，见 T13。

## 3. VISUAL 服务与协议

### 3.1 连接与文档状态

| 项 | 值 | 证据 |
|---|---|---|
| Base URL | `https://usfast.ailzd.com/v1` | 本机配置 |
| 适配器 | `openai-images` | 适配器与注册表测试 |
| 模型 | `gpt-image-2` | `GET /v1/models` 返回；真实调用 |
| 鉴权 | `Authorization: Bearer <VISUAL_API_KEY>` | 适配器；Key 不落盘 |
| 超时 | 300 秒 | `VISUAL_TASK_TIMEOUT_SECONDS=300` |
| 调用端点 | `POST {base}/images/generations`；有参考图时 `POST {base}/images/edits` | 适配器；真实调用 |
| 公开官方文档 | **未找到** | `/`、`/docs`、`/docs/api` 为网关前端；`/v1/models` 无 Key 时 401 |

**限制**：该供应商没有可引用的公开官方 API 文档页，因此本记录只声明“按实测 OpenAI Images 兼容协议”，不声明“官方文档确认”。若供应商变更协议，必须重新实测。

### 3.2 能力声明

| 能力 | 值 | 说明 |
|---|---|---|
| `text_to_image` | `true` | 真实纯文字生成成功 |
| `reference_image` | `true` | 真实参考图生成成功 |
| `asynchronous` | `false` | 同步响应；无 task id、无 poll |
| `supported_image_sizes` | `["1024x1024"]` | 固定方形；本机选择 1024x1024 |
| `selected_image_size` | `1024x1024` | 配置状态 API |
| `reference_mime_types` | `["image/png","image/jpeg"]` | 适配器接受；实际使用 PNG |
| `provider_max_reference_bytes` | `null`（未知） | 供应商未公布；不猜测 |
| `effective_reference_max_bytes` | `10000000` | 服务端 10 MB 上限，低于未知的供应商上限时先由服务端约束 |
| `poll_interval_seconds` | `null` | 同步适配器无轮询 |

### 3.3 请求、结果与下载边界

**无参考图**：JSON 请求体包含 `model`、`prompt`、`size=1024x1024`、`n=1`、`response_format=b64_json`。

**有参考图**：multipart `/images/edits`，字段包括 `image`（原始文件字节、实际 MIME、文件名）、`model`、`prompt`、`size`、`n=1`、`response_format=b64_json`。参考图以服务端已保存的原始字节上传，不传资源 ID、描述或前端对象 URL；受控测试断言了 multipart 中 `name="image"` 和原始字节。

**真实响应**：同步返回顶层 `data` 数组；本机实测同时出现 `b64_json` 和 `url`（CDN 主机 `oss-us.dimenoss.com`）。适配器优先使用 `b64_json`，仅在缺少 base64 时使用 URL fallback。真实生成耗时约 22–42 秒。

**结果获取边界**：

- `fetch_result` 只允许 HTTPS；
- 主机必须是配置的 base host 或 `.dimenoss.com` 后缀；
- 最多跟随 5 次重定向，每次重定向后重新校验主机；
- 下载结果不带模型 `Authorization`；
- 不暴露私有 locator 或签名 URL；
- 实际采用 base64 解码路径；URL 有效期未知，未把“可重取”建立在 URL 永不过期上；
- 成功结果不提供“再次重取生成”的假入口；失败且满足 `can_retrieve_result` 条件时才允许重取，且不新增版本、不重新提交生成。

### 3.4 OPEN-03 决策表

| OPEN-03 问题 | 决策/事实 | 状态 |
|---|---|---|
| 精确方形尺寸 | `1024x1024`，服务端固定；不支持其他尺寸 | 已确认 |
| 参考图格式 | `image/png`、`image/jpeg`；实际 PNG 成功 | 已确认 |
| 参考图字节上限 | 供应商上限未知；本服务端有效上限 10,000,000 字节 | 供应商值未知 |
| 输出格式 | 优先 `b64_json`；兼容 URL fallback；实际解码为 PNG | 已确认（实际） |
| 等待建议 | 同步接口；实测 22–42 秒；客户端最长等待按任务总等待，不把等待当取消 | 已确认（实测） |
| 同步/异步 | 同步；无 task id；不轮询 | 已确认 |
| 任务号/查询频率 | 无任务号；无轮询；不应假定可查询 | 不适用 |
| 结果来源/下载认证 | base64 或供应商 CDN URL；结果下载不带模型认证 | 已确认 |
| 下载重定向 | HTTPS + 主机 allowlist，最多 5 次 | 已确认（代码/受控） |
| URL 有效期/原结果重取保证 | 未知；只在服务端记录可用 locator 且满足条件时重取，不保证供应商长期可用 | 未知/外部限制 |

## 4. 安全与证据边界

- 三个 Key 均已在本机配置；记录中只出现“已配置”，不出现值、前缀、长度或认证头。
- T12 证据扫描：`docs/acceptance/evidence/secret-scan.json`，145 个文件、3 个 Key 值、0 匹配；不记录 Key。
- T12 本身不发起真实模型调用；真实调用证据见 T13/T14/T15。
- 不把受控适配器测试当作真实供应商协议证明；受控测试只锁定请求格式、调用次数和异常分类。

## 5. 已知未知与后续条件

| 未知/限制 | 影响 | 处理 |
|---|---|---|
| VISUAL 无公开官方文档 | 无法引用供应商正式协议 | 保留实测记录；协议变更需重测；不宣称官方认证 |
| LLM 上下文窗口/最大输入 token 未知 | 无法预测超限阈值 | 上游 400/413 映射安全拒绝；不自动重发/修复 |
| VISUAL 参考图供应商上限未知 | 大图可能被供应商拒绝 | 服务端 10 MB 先限；实际错误安全映射 |
| VISUAL URL 有效期未知 | CDN URL 可能过期 | 优先 base64；URL fallback 记录 locator；不保证长期重取 |
| 速率/额度限制未知 | 高并发可能 429 | 全局串行、每项目单 active；429 安全失败 |
| 图像质量主观 | 不能以“成功返回”代替质量验收 | T14/T15 只验证真实文件、尺寸、可打开和版本；质量由人工判断 |

## 6. 交付结论

T12 交付的是可执行的 provider 决策与能力记录，供 #27（真实 LLM）、#28（真实 VISUAL）、#29（双模型闭环）继续使用。**通过范围**：服务/模型/地址已锁定，鉴权、超时、结构化输出约束、能力字段、同步语义、结果来源和安全边界已记录。**未通过范围**：VISUAL 官方文档缺失、上下文窗口未知、参考图供应商上限未知、URL 有效期未知；这些均已在上面标为未知，不用推测填补。

## 7. 证据索引

- 配置状态（脱敏）：`docs/acceptance/evidence/config-status.json`
- 本机配置键名（不含值）：`backend/.env`（不提交）
- LLM 适配器与测试：`main/backend/app/adapters/openai_chat.py`、`backend/tests/test_openai_chat_adapter.py`
- VISUAL 适配器与测试：`main/backend/app/adapters/openai_images.py`、`backend/tests/test_openai_images_adapter.py`
- 真实调用：`docs/acceptance/t13-real-llm-acceptance.md`、`t14-real-visual-acceptance.md`、`t15-real-dual-model-acceptance.md`
