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

## T05 核验记录：参考图上传、替换、移除与资源引用

- **状态**：未核验（仅后端设计草案、`contracts.md` 与受控测试响应；没有真实 OpenAPI 或可连接运行服务）。
- **接口映射（FE-DEP-01 / FE-DEP-05）**：
  - `POST /api/projects/{id}/reference` → `multipart/form-data`，字段 `file` 与 `expected_revision`（当前项目响应中的 `project.revision` 字符串）。成功按 `200 ProjectView` 整包替换当前项目；服务端增加 `revision`、清空 `prompt_revision / confirmed_revision`、把 `brief_status` 置为 `draft`，并在 `brief.reference_asset_id` 写入 `reference_asset.id`。前端不在本地递增修订号。
  - `DELETE /api/projects/{id}/reference?expected_revision=N` → 成功 `200 ProjectView`；无参考图时按 no-op 处理，不改变修订号。实际移除参考图时同样使提示词/确认失效并要求重新整理、确认。
  - 首次有效上传且尚无项目时，先复用 `POST /api/projects` 的惰性创建，再上传参考图；不发送消息、不调用 LLM、不创建生成记录。上传失败保留已知项目，重试复用同一项目和当前修订。
- **精确大小与格式映射（FE-DEP-01）**：
  - SRS 硬上限 `10,000,000` 字节；有效上限取 `min(10,000,000, provider_max_reference_bytes, effective_reference_max_bytes)`，仅把有限且 `>= 0` 的服务端值纳入，最低为 0。
  - 默认接受 `image/png`、`image/jpeg`；服务端 `reference_mime_types` 仅接受 PNG/JPEG 子集，`image/jpg` 归一为 `image/jpeg`，去重；配置为空或过滤后为空时回退到 PNG/JPEG。
  - 只接受单张文件；多文件整批拒绝。选择/拖放后先在浏览器读取文件签名并实际解码为图片，再按有效上限校验字节数；不把扩展名或 `File.type` 当作唯一证据。取消选择、无效类型、超限、损坏、多文件均不创建项目、不写请求、不替换当前参考图。不支持粘贴上传。
- **能力语义**：
  - `reference_image === true` → 允许上传；`false` → 明确说明当前模型不支持参考图，并保留“移除参考图”入口，移除后可继续纯文字流程；`null/unknown` → 显示“参考图能力待核实”，带参考图时锁定“生成图片”阶段并说明原因。
  - 前端不根据模型名推断能力，不捏造供应商限制；只有服务端配置字段或真实响应能改变状态。
- **待上传、失败与回执**：
  - 选择有效文件后先显示临时预览并标记“待上传”，成功响应前不把它当作当前参考图；成功后才采用服务端 `ProjectView`，失败保留旧参考图和当前项目修订。
  - 上传/移除请求返回网络错误、超时、`0` 或 `5xx` 等回执不明时，先 `GET /api/projects/{id}` 核实：上传以“参考图 ID 变化或 revision 增加”为成功证据，移除以“参考图变为 null”为成功证据；只有核实成功才更新界面，否则显示安全失败说明并要求用户主动重试。
  - `409 REVISION_CONFLICT / PROJECT_BUSY` 显示“参考图已在别处更新，请加载最新项目后再操作。”并保留当前旧图；由用户主动加载最新项目，不自动合并或重试。`404`、`413 UPLOAD_TOO_LARGE`、`422 INVALID_REFERENCE_IMAGE / UNSUPPORTED_REFERENCE_FORMAT / REFERENCE_NOT_SUPPORTED / VALIDATION_ERROR`、`503 LOCAL_STORAGE_UNAVAILABLE` 使用集中安全文案，不输出供应商原始响应。
- **资源引用与对象 URL**：
  - 参考图始终按 `AssetView.id` 通过 `GET /api/assets/{id}?download=false` 读取；不保存本地路径、blob URL 或供应商 URL 到项目数据，历史只引用原始资源 ID。生成成功记录即使有结果图，也不得替换或改写参考图关联。
  - 上传选择产生的临时 object URL 在替换、成功、失败后重新选择以及组件卸载时释放；`availability === "missing"` 时明确显示“参考图文件缺失”，不渲染伪造图片，并仍允许移除。
- **T05 证据**：`tests/t05-reference-upload.spec.ts` 的 20 个测试全部通过，覆盖首次惰性创建、拖放、取消、无效/超限/损坏/多文件、失败重试复用项目与修订、替换失败保留旧图、替换成功失效提示词和确认、键盘移除、回执不明 GET 核实、能力 false/unknown、待上传预览、防重入、卸载 object URL 释放及生成记录不改写参考图。测试响应均为受控测试替身，不代表真实服务已按此返回。
- **风险**：真实 OpenAPI 的 multipart 字段名、`expected_revision` 位置、错误码及 `effective_reference_max_bytes` 语义尚未核验；上传能力与图片格式支持必须以后端实际契约为准，未核验前不得宣称真实上传链路已完成。

## 后续接入约定

- 等真实 OpenAPI 或运行服务可用后，必须重新核验上述字段，并更新本文件的“状态”和“证据”。
- 若 T02 读取项目时新增字段，应复用 `main/src/api/types.ts` 与 `main/src/api/client.ts`，避免在组件内直接拼装响应或错误文案。

## T03 核验记录：首次创建、消息响应与修订值

- **状态**：未核验（仅后端设计草案与受控测试响应，没有真实 OpenAPI 或可连接服务）。
- **接口映射**：
  - `POST /api/projects` → 请求体固定 `{}`，成功按 `201 ProjectView` 消费 `id`、`revision`、`brief_status`、`brief`。创建后立即把 URL 更新为 `?project=<id>`，随后以响应中的 `project.revision` 作为首条消息的 `expected_revision`，不本地加一。
  - `POST /api/projects/{id}/messages` → 请求体 `{content, expected_revision}`；成功按 `MessageCreateResponse` 消费 `project`、`user_message`、`assistant_message`。`project` 作为最新服务端状态，两条消息按服务端返回追加；不从普通消息文本拼造卡片或回复。后续发送使用最近一次响应中的 `project.revision`。
  - `assistant_message.brief_snapshot` → 历史“当时的需求快照”只读卡；仅当快照有可读字段时展示。当前需求卡来自 `project.brief`，与历史快照分开。
  - `project.brief_error.code/message` → 持久整理失败说明与用户主动“重新整理”按钮；刷新后 `brief_status=organizing` 只读等待，不自动重发。
- **失败行为**：创建请求网络断开、超时、`5xx` 或非结构化失败均按“创建结果未确认”处理：保留输入、停止再次创建、显示核实提示与“打开已有项目”入口，不自动重试。消息请求失败保留该项目、URL 与用户原文，下一次发送复用现有项目；所有写请求均无通用自动重试。`409` 显示统一安全冲突说明并保留输入。缺少 LLM 配置时只显示可操作原因，不创建项目。
- **T03 证据**：`tests/t03-first-project.spec.ts` 的受控 HTTP 响应仅用于验证前端行为，不代表真实接口已确认；真实字段、状态码与排序仍需 OpenAPI/运行服务核验。
- **风险**：重新整理的消息内容在现有契约中未单独定义；T03 使用普通用户消息“请按当前已保存的需求重新整理。”触发现有消息接口，必须由后端契约或真实联调确认这是受支持的整理入口，未确认前不得宣称已完成真实整理链路。

## T04 核验记录：编辑、保存、重新整理与确认

- **状态**：未核验（仅后端设计草案与受控测试响应，没有真实 OpenAPI 或可连接服务）。
- **接口映射**：
  - `PATCH /api/projects/{id}/brief` → 请求体仅包含实际变化的可编辑字段，并携带 `expected_revision = project.revision`；成功按 `200 ProjectView` 整包替换当前项目，以响应中的 `revision`、`prompt_revision`、`confirmed_revision` 与 `brief` 为准，不本地递增修订号。
  - `POST /api/projects/{id}/confirm` → 请求体 `{revision}`，其中 `revision` 只取当前项目响应值；成功按 `200 ProjectView` 进入 `confirmed`，不调用生成接口。
  - 保存与确认均不发送消息、不调用 LLM；重新整理通过既有消息接口发送固定用户消息“请按当前已保存的需求重新整理。”，使用当前 `project.revision`，属于独立明确动作。
  - 保存响应若返回的提示词与当前 revision 不同步，展示“旧提示词已过期，请重新整理需求后再确认。”；该判断只依据响应字段，不自行生成提示词。
  - `409` 保留本地可复制编辑缓冲并阻止过期提交，由用户主动“加载最新需求”（`GET /api/projects/{id}`）后才替换缓冲；`413` / `422` 在需求卡区域给出安全可操作说明并保留缓冲。
  - 确认要求无必需澄清、无未发送输入、无未保存修改、提示词与当前 revision 同版本且尺寸存在；生成入口只在真实 `confirmed` 响应后解锁，确认动作本身不创建生成记录。
- **T04 证据**：`tests/t04-brief-edit.spec.ts` 的 PATCH/confirm/消息响应均为受控测试替身，不代表真实服务已按此返回；真实字段、状态码、确认规则与整理入口仍需 OpenAPI/运行服务核验。
- **风险**：重新整理复用消息接口的请求语义仍需后端确认；真实服务可能以独立整理接口或不同 `brief_status` 返回，届时必须更新集中类型与映射，不得在组件内猜测。

## T06 核验记录：生成首图、任务状态与结果资源

- **状态**：未核验（仅后端设计草案、`contracts.md` 与受控测试响应；没有真实 OpenAPI 或可连接运行服务）。
- **接口映射（FE-DEP-01 / 03 / 04 / 05）**：
  - `POST /api/projects/{id}/generations` → JSON `{request_id, confirmed_revision}`；`request_id` 为前端生成的 UUID，`confirmed_revision` 取当前项目响应中的 `confirmed_revision`（仅在 `revision === prompt_revision === confirmed_revision` 且非空时允许提交）。成功 `202 GenerationSubmitResponse {generation}`；相同请求幂等返回 `200`。前端只采纳响应中的 `generation`，不伪造任务 ID 或版本号。
  - 首次异步等待前以内存锁和 `generationSubmitBusy` 防重；不实现通用写请求自动重试。父规格要求的最小会话凭据保存在 `sessionStorage['ecommerce-generation-pending']`，只含 `projectId/requestId/confirmedRevision` 及后续恢复字段；写失败不影响本次提交。
  - `GET /api/generations/{id}` → `200 GenerationView`，消费 `status`、`stage`、`version_no`、`result_asset_id`、`result_asset`、`error` 等真实字段；`404 GENERATION_NOT_FOUND` 作为查询失败显示，不解释为生成失败。
  - 成功后重新 `GET /api/projects/{id}`，以项目详情中的 `generations` 和 `result_asset` 为准；刷新页面只读取已有记录，不重新整理、不重新生成。
  - 结果资源通过既有 `GET /api/assets/{id}?download=false` 预览、`?download=true` 下载；下载文件名为 `project-<project_id>-v<version_no>.<ext>`，扩展名由响应的 `mime_type` 映射（png/jpeg→jpg/webp/gif，未知类型安全回退），不使用本地路径、供应商 URL 或扩展名猜测。
- **生成门槛**：只有真实 `brief_status === confirmed`、三种修订相等且非空、`VISUAL` 配置已配置且可用、`text_to_image === true`、尺寸存在且受支持、无草稿/未保存编辑/活跃整理/参考图阻塞/活跃生成/待核实提交时才显示可点击的“生成首图”。能力为 `false` 明确说明不支持；为 `null/unknown` 明确说明“文生图能力尚未核实，不能生成图片。”；不根据模型名推断。
- **任务状态与查询**：queued/running 使用服务端 `status/stage` 显示真实状态；首次自动查询在前次响应结束后约 2 秒进行，单飞（同一时间最多一个自动查询），项目切换、终态、卸载时清理定时器和请求；连续 3 次 GET 失败停止自动查询并显示“暂时无法读取生成状态。已停止自动查询，请手动重试。”，提供手动重新查询。
- **回执不明**：提交网络错误、超时、`0` 或 `5xx` 等回执不明时显示“提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。”并锁住新生成；不把“未查到记录”当作明确拒绝。T06 只保证安全的只读 `GET` 核实与阻塞，完整手动恢复由 T09 #10 交付。明确拒绝（4xx 业务码）、业务 `failed/unknown` 与 GET 查询错误分别显示；失败保留当前需求和上一次成功旧图。
- **结果视图（FE-AC-07 / 08 / 13 / 14 / 19 / 20）**：成功记录只在有真实 `succeeded` 生成后解锁“查看结果”；显示版本号、完成时间和图片实际尺寸；无图显示空占位，不显示模拟成功图或虚构进度。结果图片使用弱棋盘底、完整包含显示、适应窗口/原始大小切换、可滚动大图、Esc 关闭并返回触发按钮焦点。`availability === missing` 显示“结果文件缺失”，不渲染图片或下载链接。
- **T06 证据**：`tests/t06-generation-first-result.spec.ts` 的 15 个测试全部通过；全量测试 93 个通过。覆盖已确认项目摘要与提示词入口、UUID request_id 与 confirmed_revision、单飞轮询与成功重读项目、双击防重、业务失败保留旧图、资源缺失、刷新零新增 POST、回执不明核实、查询失败停止、未知能力阻塞、明确拒绝、导航不生成、unknown 状态阻塞、在途查询不叠加及卸载清理。受控测试响应不代表真实服务已按此返回。
- **风险**：真实 OpenAPI 的生成提交/状态/资源字段、幂等语义、`acknowledge_unknown` 的后续恢复协议、`ProjectDetailResponse.generations` 是否总携带 `result_asset`、错误码和异步状态机尚未核验；未核验前不得宣称真实生图链路已完成。

## T07 核验记录：导航确认、草稿保留与版本冲突

- **状态**：未核验（纯前端行为与受控测试响应；没有真实 OpenAPI 或多标签页运行服务）。
- **脏状态与离站提醒**：未发送的输入框文字（去除首尾空白后非空）与当前需求卡未保存字段统一计入页面内存脏状态；已提交并收到响应的消息不再算草稿。脏内容不写入项目状态、不写入 sessionStorage/localStorage、不跨标签共享。仅在存在脏内容时注册 `beforeunload`，保存、取消编辑或清空输入后移除；提示文字由浏览器决定，不承诺刷新/关闭/崩溃一定提示或恢复。
- **新建、切换与前进/后退**：有脏内容时先显示自定义 `role="alertdialog"` 的“放弃未保存的修改？”弹窗，按钮为“继续编辑”（初始焦点）与“放弃修改”；支持 Escape/遮罩取消、Tab 焦点约束、关闭后把焦点还给触发按钮。取消恢复原项目、URL、输入文字和需求字段；确认后才清空并加载新上下文。浏览器前进/后退无法取消导航，因此先用 `history.replaceState` 把 URL 恢复为当前项目，再显示确认。
- **请求上下文隔离**：消息发送、重新整理、需求保存/确认/加载最新、参考图上传/移除、生成提交/查询均捕获发起项目上下文；切换或新建后，晚到的响应不得覆盖新项目、预览或视图。已提交的写请求不因离开自动重发；返回原项目只通过 `GET /api/projects/{id}` 读取实际记录。生成回执不明的完整 sessionStorage 凭据恢复和手动接管由 T09 #10 交付，T07 只保证导航期间不自动重发或伪成功。
- **版本冲突**：`409 REVISION_CONFLICT`/版本过期保留可复制的输入与字段，显示“加载最新需求”（主动 GET 后替换）或“只读查看本地副本”；本地副本为页面内存中的只读 textarea，可复制但不能保存、确认、重新整理或发送，不自动合并、不静默覆盖、不自动重试。
- **生成取消**：项目切换不取消服务器任务，也不调用任何取消接口；界面不提供“取消生成”按钮。
- **T07 证据**：`tests/t07-navigation-conflict.spec.ts` 的 10 个完整工作台测试通过；全量 103 个测试、`npm run typecheck`、`npm run build` 通过（commit 前验证）。覆盖干净状态不弹窗、草稿/需求字段的新建与切换确认、取消恢复、popstate URL 恢复、beforeunload 注册生命周期、迟到消息与保存响应隔离、409 本地副本/加载最新、键盘焦点与无取消生成入口。
- **风险**：浏览器真实前进/后退栈、原生 `beforeunload` 文案和多标签页冲突无法由 jsdom 完全复现；自定义弹窗不是原生 `confirm`，需真实浏览器核验焦点与滚动锁；T09 的凭据恢复不在本票范围内。

## T08 核验记录：反馈重生、历史版本浏览与逐版下载

- **状态**：未核验（仅后端设计草案、`contracts.md` 与受控测试响应；没有真实 OpenAPI 或可连接运行服务）。
- **版本数据与排序（FE-DEP-03）**：只从项目详情中的真实 `GenerationView` 取 `status === "succeeded"` 的记录，不使用前端假版本。按服务端 `version_no` 升序展示；同一版本号按 `completed_at ?? created_at`、再按 `id` 排序，无法比较时间时保持稳定顺序。默认选择版本号最大的成功版本；不在本地生成或递增版本号。
- **浏览旧版与当前需求隔离**：选择历史版本只改变结果预览、该版本快照、该版本尺寸、时间和下载链接；不修改当前项目 `brief`、`reference_asset`、`revision / prompt_revision / confirmed_revision`、确认状态或对话。当前需求区始终读取最新 `ProjectView`，并保留最近任务状态。
- **版本快照与提示词**：选中版本的字段来自该 `GenerationView.brief_snapshot` 与该记录 `prompt`；缺失时显示“该版本未保存可还原的需求快照，无法还原当时的需求字段。”，空提示词显示“该版本未保存完整提示词。”，绝不用当前需求补写或猜测。`GenerationView.brief_snapshot` 类型按可空处理（`Brief | null`）。
- **新结果到达时的选择**：用户主动查看旧版后，若用户不在结果视图或仍跟随最新版本，则自动切到新成功版本；若用户正在结果视图查看旧版，则保留旧版选择并提示“新结果：第 N 版已生成。当前仍显示第 M 版。”。新结果不得用旧成功图遮盖失败或 `unknown` 的最近任务；最近任务错误单独显示。
- **失败与未知**：最近生成 `failed` 时保留上一次成功图并显示安全失败说明；`unknown` 时显示未确认提示并阻止新生成。浏览旧成功版本不改变这些状态。
- **下载与资源缺失**：下载链接始终指向当前选中版本的 `result_asset_id`，文件名 `project-<project_id>-v<version_no>.<ext>`，扩展名取该资源 `mime_type`；`availability === "missing"` 时只在该版本显示“结果文件缺失”，不渲染图片或下载链接，其他版本仍可用。
- **继续修改**：只切回对话并聚焦输入框，不自动发送、不恢复旧需求；新反馈继续使用既有消息、整理、确认与生成接口，生成时使用当前完整需求和当前原始参考图，成功结果不会自动成为参考图。
- **可访问性与布局**：版本条为独立横向滚动区，使用 `role="group"`、`aria-pressed`、可访问版本名称，键盘可聚焦并选择；选中态有可见样式；移动端保持横向滚动，不压缩按钮文字。
- **T08 证据**：`tests/t08-version-browsing.spec.ts` 的 9 个完整工作台测试通过；全量 112 个测试、`npm run typecheck`、`npm run build` 通过（构建 1613 modules）。覆盖版本排序与默认最新、旧版预览不改当前需求、资源缺失隔离、失败不遮盖、快照缺失不编造、继续编辑不发送、查看旧版时新结果提示、切换项目后轮询不跳回、反馈到新版并逐版下载。受控测试响应不代表真实服务已按此返回。
- **风险**：真实 OpenAPI 的 `version_no` 唯一性、同版本排序、`brief_snapshot` 可空性、`result_asset` 是否总随详情返回、下载资源响应头与真实浏览器横向滚动/键盘行为尚未核验；未核验前不得宣称真实历史版本链路已完成。


## T09 核验记录：提交回执不明后的核实与手动恢复

- **状态**：未核验（仅后端设计草案、`contracts.md` 与受控测试响应；没有真实 OpenAPI 或可连接运行服务）。
- **最小凭据与写入时机**：每次生成 POST 发出前，内存保留原请求，并尝试写入同源 `sessionStorage` 键 `ecommerce-generation-pending`；内容仅 `projectId`、`requestId`、`confirmedRevision`、`acknowledgeUnknownPresent`、`acknowledgeUnknownValue`、可选 `generationId`。不保存卡片、图片、草稿或密钥。回执明确成功或进入可查询状态后清理；回执不明时凭据保留。
- **存储失败隔离**：`sessionStorage` 读取、JSON 解析、字段格式校验和写入失败均按“无凭据/不可持久化”安全处理。无效或项目不匹配的凭据不用于任何请求；写入失败时内存原请求仍可用于手动恢复，并显示“无法写入会话存储；本次提交仍保留在当前页面，刷新或关闭页面后将无法恢复。”。不承诺关闭标签页后恢复，也不依赖跨标签共享。
- **刷新自动核实**：项目详情加载后读取同项目凭据，先 GET `/api/projects/{id}`，在 `generations` 中按原 `request_id` 精确匹配；若凭据带有已知 `generationId` 且项目详情未包含，再 GET `/api/generations/{id}` 并按同一 `request_id` 复核。匹配到 queued/running 恢复状态轮询，匹配到 succeeded 恢复结果视图；自动核实只发 GET，不产生消息或生成 POST。
- **未匹配处理**：显示“提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。”及“仍未查到这次生成提交的记录；这不代表失败。可先只读核实，或明确恢复本次提交。”，保留原凭据和恢复入口；不把未匹配当作原请求失败，不自动创建替代请求。
- **手动恢复**：仅使用凭据中完全相同的 `request_id`、`confirmed_revision` 和 `acknowledge_unknown` 的存在性/值重放 POST，即使当前项目修订或模型配置已变化也不重算；恢复按钮说明“恢复本次提交仅重放原始请求参数；如果原请求未被受理，可能执行这次生成。”。恢复进行中防重入、不生成新 `request_id`；它与只读核实、主动新生成在界面和请求上明确区分。
- **终态清理与限制**：服务端记录进入终态后清理凭据并停止待核实状态；不宣称远端绝无重复计费，也不尝试跨标签协调。未知生成状态（`status === "unknown"`）仍按 T06/T08 规则阻止新生成，后续确认协议待真实契约核验。
- **T09 证据**：`tests/t09-uncertain-submit-recovery.spec.ts` 的 15 个完整工作台测试通过；全量 127 个测试、`npm run typecheck`、`npm run build` 通过（构建 1613 modules）。覆盖最小凭据、存储读写失败、刷新匹配 queued/succeeded、已知生成 ID 核实、未匹配保留、三种 `acknowledge_unknown` 手动恢复参数逐字段一致、恢复防重、无效凭据和无凭据不误报。受控测试响应不代表真实服务已按此返回。
- **风险**：真实 OpenAPI 的幂等语义（相同 `request_id`/参数返回 200 还是 202）、`UNKNOWN_ACK_REQUIRED` 与 `acknowledge_unknown` 的后续协议、5xx 是否代表受理不明、`sessionStorage` 在真实浏览器隐私模式/配额下的行为尚未核验；未核验前不得宣称真实断网恢复链路已完成。


## T10 核验记录：状态查询、断连与可见性恢复

- **状态**：未核验（仅后端设计草案、`contracts.md` 与受控测试响应；没有真实 OpenAPI 或可连接运行服务）。
- **查询目标**：工作台只有一个自动状态查询入口。`brief_status === organizing` 时 GET `/api/projects/{id}`，以详情响应更新需求卡与整理状态；存在 queued/running 的 `GenerationView` 时 GET `/api/generations/{id}`，只更新对应生成记录。每次查询结束约 2 秒后再发起下一次；同一时刻最多一个自动查询在途，手动查询也复用同一防重入口。
- **终态与失败**：项目不再 organizing、生成进入 succeeded/failed/unknown 或项目切换/卸载时停止自动查询并清理定时器与请求。HTTP 成功但业务状态为 failed/unknown 是终态，不计连接失败；只有请求失败才累计连续失败次数。连续三次失败后停止自动查询并持久显示“连接中断，生成状态暂不可知。”或“连接中断，项目状态暂不可知。”及“重新查询状态”，旧图、当前需求和已有记录保留。
- **资源不存在**：项目详情 404 显示“资源不存在：项目不可用。”及安全说明；生成记录 404 显示“资源不存在：生成记录不可用。”及安全说明；两者都停止自动查询，不把资源缺失解释为生成失败。
- **手动查询**：`重新查询状态` 只调用 GET（整理中读项目，否则读 activeGeneration 或最近记录）；成功且仍为 queued/running 才恢复自动查询，进入终态则停止；手动查询失败保留暂停说明。用户明确恢复生成提交的 POST 仍只由 T09 的恢复动作发起，T10 测试断言恢复路径零生成 POST。
- **页面可见性**：页面隐藏时清除定时器并中止在途的状态读取，不计失败；恢复可见时若仍有暂停错误则不自动恢复，否则立即读取一次，之后按约 2 秒继续。切换项目、卸载和业务终态均清理旧上下文的查询，晚到响应不得覆盖新项目。
- **刷新与中断**：刷新只读取项目详情中的实际 `generations`/状态，不重发消息或生成；整理中刷新继续按项目状态查询，整理中断按 `brief_error` 显示且不自动重发；结果未确认按服务端 unknown 显示，不推测阶段、百分比或剩余时间。
- **T10 证据**：`tests/t10-status-recovery.spec.ts` 的 11 个完整工作台测试通过；全量 138 个测试、`npm run typecheck`、`npm run build` 通过（构建 1613 modules）。覆盖 organizing 查询与停止、刷新恢复 queued、查询单飞、三次失败暂停与旧图保留、隐藏/显示中止与恢复、手动只读恢复、404 资源错误、成功清零失败、failed/unknown 终态、切项目隔离和整理中断不重发。受控测试响应不代表真实服务已按此返回。
- **风险**：真实 OpenAPI 的项目/生成状态字段、错误码、服务重启后的记录一致性、真实浏览器可见性事件与网络中止时序尚未核验；未核验前不得宣称真实断连恢复链路已完成。
