# 前端依赖核验记录

本文件记录 T01 前端所依赖、但尚未由真实后端接口或 OpenAPI 核验的契约。测试中的 `tests/support/fixtures.ts` 只是受控响应，不代表运行契约已经确认；前端不得据此推断真实供应商能力。

## FE-DEP-01：读取 LLM / VISUAL 配置状态

- **核验内容**：`GET /api/config/status` 的响应结构、字段可空性，以及“配置完整 / 就绪”与“真实调用已验证”的区别。
- **当前前端字段映射**：
  - `llm.configured` / `visual.configured` → 配置完整性（配置完整 / 配置不完整）
  - `llm.ready` / `visual.ready` → 运行就绪（已就绪 / 未就绪）
  - `adapter_id` → 适配器（null 显示“未配置”）
  - `model` → 模型（null 显示“未配置”）
  - `missing_fields[]` → 缺失字段名列表
  - `issues[].code` / `issues[].message` → 配置问题安全说明（前端过滤疑似 Key、URL、堆栈等敏感文本）
  - `timeout_seconds` → 超时（秒）
  - `verification` → 当前类型仅允许 `'not_checked'`，固定显示“尚未验证实际调用”
- **状态**：未核验
- **证据**：仅后端草案 §8.3；没有真实 OpenAPI，也没有可连接的运行服务。`main/src/api/types.ts` 与 `main/src/api/client.ts` 是按草案先行的前端类型与安全错误映射。
- **未明确时 UI 行为**：空值或未知值不猜测；适配器/模型显示“未配置”，不确定字段显示“待核实”或“待配置”。即使 configured/ready 为 true，也只显示“配置完整”“已就绪”，绝不显示“认证成功”“连接成功”“已验证”。
- **读取失败**：只展示 `ApiError` 的安全消息，持久保留到下一次成功刷新；不展示 Key、供应商原始响应、堆栈或原始响应体。刷新按钮只发 `GET /api/config/status`，不写配置、不探测付费能力。

## FE-DEP-06：VISUAL 能力与图片限制

- **核验内容**：`GET /api/config/status` 响应中 `visual.capabilities` 的字段名称、类型、可空语义与实际后端取值。
- **当前前端字段映射**：
  - `text_to_image` → 文生图：true=支持，false=不支持，null=待核实
  - `reference_image` → 参考图：true=支持，false=不支持，null=待核实
  - `asynchronous` → 异步生成：true=支持，false=不支持，null=待核实
  - `supported_image_sizes[]` → 支持尺寸；null/空显示“待配置”
  - `selected_image_size` → 当前尺寸；null 显示“待配置”
  - `reference_mime_types[]` → 参考图格式；null/空显示“待配置”
  - `provider_max_reference_bytes` → 供应商参考图上限（字节）；null 显示“待配置”
  - `effective_reference_max_bytes` → 实际参考图上限（字节）；null 显示“待配置”
- **状态**：未核验
- **证据**：仅后端草案 §8.3；没有真实 OpenAPI 或运行服务验证这些能力位与字节上限。测试中的 `configuredConfig()`、`unknownCapabilitiesConfig()` 只是受控 fixture。
- **未明确时 UI 行为**：绝不编造尺寸、MIME 类型或字节上限；不把 null 当成 false，也不把“配置完整”当成供应商能力已确认。没有真实证据时仅展示只读状态。

## 后续接入约定

- 等真实 OpenAPI 或运行服务可用后，必须重新核验上述字段，并更新本文件的“状态”和“证据”。
- 若 T02 读取项目时新增字段，应复用 `main/src/api/types.ts` 与 `main/src/api/client.ts`，避免在组件内直接拼装响应或错误文案。
