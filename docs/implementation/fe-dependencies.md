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

## FE-DEP-02：项目列表与分页

- **核验内容**：`GET /api/projects` 的请求参数（`limit`、`cursor`）、`ProjectListResponse` 的 `{ items, next_cursor }` 结构，以及 `ProjectSummary.{id,title,brief_status,latest_generation_status,updated_at}` 的字段语义和排序保证。
- **当前证据**：仅后端设计草案 §5/§8.3、`contracts.md` 与本仓库受控测试响应；没有真实 OpenAPI，也没有可连接的运行服务。`tests/t02-project-read.spec.ts` 与 `tests/t02-project-read-supplement.spec.ts` 中的响应是测试替身，不代表真实服务已按此返回。
- **当前前端字段映射**：
  - 请求固定 `GET /api/projects?limit=20`，仅当响应给出非空 `next_cursor` 时以原样字符串追加 `cursor`，不自行发明排序、搜索或分页参数。
  - `items[]` → 项目项，保持服务端返回顺序；按 `id` 去重，不自行重排。
  - `title` 为 null 或纯空白 → “未命名项目”。
  - `updated_at` → `<time datetime>` 保留原值，可见文本用 `toLocaleString()` 按浏览器本地时区显示。
  - `brief_status` → 真实需求状态徽标；`latest_generation_status` 仅在非 null 时显示生成状态徽标。
  - `next_cursor === null` → 停止加载更多；非 null → 显示“加载更多”，在途禁用并显示加载文案，失败保留已加载列表并从同一 cursor 重试。
- **状态**：未核验（分页契约待真实 OpenAPI 核验）。
- **未明确时 UI 行为**：不发明分页参数，不按更新时间在前端重排，不伪造状态徽标；真实接口若与草案不一致，应更新集中类型与映射并标记“分页待接入”，而不是在组件内猜测。空列表提供“新建项目”入口；首屏失败显示持久安全错误与重试，且不自动切换或创建项目。

## FE-DEP-03：项目详情、历史消息与快照

- **核验内容**：`ProjectDetailResponse.{project,messages,generations}` 的完整结构；`MessageView.{sequence_no,role,content,project_revision,brief_snapshot}`；`GenerationView.{version_no,status,stage,brief_snapshot,prompt,result_asset_id}` 的排序和可空语义。详情在 v1 不分页。
- **当前证据**：仅后端设计草案 §5/§8.3 与受控响应；没有真实 OpenAPI 或运行服务验证消息顺序、快照字段与生成记录版本顺序。
- **当前前端字段映射**：
  - `messages[]` → 按服务端返回顺序原样显示用户原文与 assistant 完整 `content`，不截断、不改写、不做 HTML 渲染。
  - assistant 的 `brief_snapshot` 非 null 且至少有一个可读字段（可编辑文本字段或 `prompt`）→ 只读“当时的需求快照”卡；`brief_snapshot` 为 null 或全空 → 不渲染卡片，也不从普通消息文本拼造结构。
  - `generations[]` → “生成记录 N 条”；最近版本按真实 `version_no` 最大值展示状态，不编造版本号、成功图或下载入口。T02 不实现轮询、结果图、下载或重取。
- **状态**：未核验（快照与版本排序待真实 OpenAPI 核验）。
- **未明确时 UI 行为**：只展示实际可还原的历史；缺少快照的消息保持普通消息；不把 assistant 消息中的 JSON/代码块解析为卡片或 HTML；不根据消息内容推断修订号或生成成功。

## FE-DEP-05：项目详情的修订、状态与空值

- **核验内容**：`ProjectView.{revision,prompt_revision,confirmed_revision,brief_status,brief,brief_error,reference_asset}` 的真实返回；`Brief.{product_description,purpose,style,composition,text_content,image_size,reference_asset_id,prompt}` 的字段可空性；`reference_asset` 的 `mime_type / width / height / byte_size / availability`。
- **当前证据**：仅后端设计草案 §5/§8.3 与受控响应；没有真实 OpenAPI 或运行服务。`brief_status=confirmed`、`revision=prompt_revision=confirmed_revision` 等规则来自草案，尚未在真实响应上核验。
- **当前前端字段映射**：
  - `brief_status` → 真实需求状态徽标；仅当真实值为 `confirmed` 时解锁“生成图片”阶段（T02 不实际提交生成）。
  - 生成记录中真实存在 `status === "succeeded"` 的 `GenerationView` 时才解锁“查看结果”阶段，不使用前端假 `hasGeneration`。
  - 可编辑需求字段：空字符串/纯空白 → “未填写”；`image_size === null` → “待配置”；`prompt === ""` → “尚未生成完整提示词”。
  - `reference_asset`：可用时用 `/api/assets/{id}` 预览并带可访问名称；`availability === "missing"` → 明确“参考图文件缺失”，不渲染伪造图片；null → “参考图记录不可用”。
  - `revision / prompt_revision / confirmed_revision` 只读取响应，不在本地 `+1`，不用它们推断成功或生成可用性。
- **状态**：未核验（字段与空值语义待真实 OpenAPI 核验）。
- **未明确时 UI 行为**：只展示真实字段状态，不推算修订号、不把待响应操作显示为成功；空值不猜测像素、提示词或参考图内容；错误沿用统一安全错误映射，不输出供应商原始响应、Key、URL 或堆栈。

## 后续接入约定

- 等真实 OpenAPI 或运行服务可用后，必须重新核验上述字段，并更新本文件的“状态”和“证据”。
- 若 T02 读取项目时新增字段，应复用 `main/src/api/types.ts` 与 `main/src/api/client.ts`，避免在组件内直接拼装响应或错误文案。
