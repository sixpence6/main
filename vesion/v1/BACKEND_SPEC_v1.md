# 电商生图智能体 v1：后端持久化、生成执行与恢复规格

日期：2026-10-29。范围：为现有前端提供真实本机后端、持久记录与图片交付。状态：测试边界已获用户确认；后端实现与真实模型验收尚未完成。

## Problem Statement

电商图片制作者希望用文字描述商品，可选提供一张参考图，经过需求整理和明确确认后得到一张可预览、可下载的真实图片，再根据反馈生成新版本。用户需要在刷新和服务重启后继续查看需求、对话、原始参考图及历史成功图片，并分清生成失败、交付失败和结果未确认。

当前仓库已有 Vue 工作台、集中 HTTP 客户端及工作台行为测试，但没有后端应用、数据库迁移或实际生成的 OpenAPI。前端使用的配置、分页、修订、快照、原请求关联和结果重取契约仍来自设计草案及测试替身，无法据此完成真实闭环验收。生成回执丢失或后端中断时，如果自动重发、用当前需求重建旧提交或把不确定结果当作失败，就可能造成重复调用、快照错配和历史丢失。

## Solution

提供仅监听本机的单用户 FastAPI 后端，为现有工作台实现项目、对话、需求卡、参考图、单张生成、状态查询、版本文件及现有结果重取。SQLite 保存业务记录，图片保存在本机文件系统；LLM 与 VISUAL 各自读取独立的本机配置，通过明确注册的供应商适配器调用。

每次生成先持久化不可变的提交快照，再由进程内后台任务执行。相同原始请求返回原记录；查询、刷新、启动修复和重取文件都不重新提交生图。服务中断后未完成生成变为 unknown，用户明确接受可能重复计费的风险后才能创建新的生成尝试。只有图片实际验证、落盘并关联成功，记录才进入 succeeded。

空模型配置下仍可启动、诊断和读取历史。接口和错误契约通过 Pydantic 生成 OpenAPI，并与现有前端集中类型核验。供应商、精确尺寸和协议仍待选定；可先实现本机框架及受控行为，实际无图、有图、反馈重生和逐版下载均需真实模型验收。

## User Stories

1. As an e-commerce image creator, I want the app to run only on my computer, so that I can use a personal workspace.
2. As an e-commerce image creator, I want the app to start without model configuration, so that I can inspect setup problems and read existing work.
3. As an e-commerce image creator, I want separate LLM and VISUAL configuration, so that each role uses its intended service and credentials.
4. As an e-commerce image creator, I want credentials to stay in local backend configuration, so that the browser cannot read or modify them.
5. As an e-commerce image creator, I want configuration changes to take effect after restart, so that setup behavior is predictable.
6. As an e-commerce image creator, I want configuration completeness distinguished from successful authentication, so that I do not mistake local checks for a verified connection.
7. As an e-commerce image creator, I want startup and status reads to avoid model calls, so that inspecting the app does not incur model charges.
8. As an e-commerce image creator, I want unknown model capabilities shown as unknown, so that I am not offered unsupported generation options.
9. As an e-commerce image creator, I want the selected size and effective reference limit reported precisely, so that I can prepare supported inputs.
10. As an e-commerce image creator, I want saved work to survive service restarts, so that I can continue earlier projects.
11. As an e-commerce image creator, I want project creation to return a persistent identifier and initial revision, so that later actions share one project history.
12. As an e-commerce image creator, I want my first saved description to provide a readable project title, so that I can recognize projects without another model call.
13. As an e-commerce image creator, I want projects listed in recent activity order, so that I can find ongoing work.
14. As an e-commerce image creator, I want explicit pagination and an end cursor, so that loading more projects is predictable.
15. As an e-commerce image creator, I want project details to include ordered messages and generation attempts, so that I can recover the complete local record.
16. As an e-commerce image creator, I want reading history to leave current requirements unchanged, so that browsing does not restore an older brief.
17. As an e-commerce image creator, I want natural language organized into editable requirements, so that I do not need specialist prompt-writing skills.
18. As an e-commerce image creator, I want necessary clarification questions returned together, so that I can resolve ambiguous products or prices.
19. As an e-commerce image creator, I want unspecified product facts to stay unspecified, so that generated requirements do not invent materials or claims.
20. As an e-commerce image creator, I want suggested visual choices to be visible, so that I can review them before confirmation.
21. As an e-commerce image creator, I want a complete reply and prompt after organizing, so that I can inspect what will guide generation.
22. As an e-commerce image creator, I want my original message saved before the external organizing call, so that a later model failure does not erase it.
23. As an e-commerce image creator, I want missing LLM configuration to reject organizing before saving the message, so that an unavailable operation is not recorded as started.
24. As an e-commerce image creator, I want organizing failures recoverable through project details, so that a refresh explains what happened.
25. As an e-commerce image creator, I want historical requirement cards to preserve their original snapshots, so that later edits do not rewrite the conversation.
26. As an e-commerce image creator, I want to save editable brief fields without calling a model, so that I can revise requirements deliberately.
27. As an e-commerce image creator, I want saving unchanged fields to preserve the revision, so that harmless saves do not invalidate confirmation.
28. As an e-commerce image creator, I want actual text or reference changes to invalidate the prompt and confirmation, so that generation uses the reviewed requirements.
29. As an e-commerce image creator, I want reorganizing to be an explicit message action, so that saving does not silently invoke the LLM.
30. As an e-commerce image creator, I want confirmation to apply to one valid revision without generating an image, so that I control the final submission.
31. As an e-commerce image creator, I want stale edits and late organizing replies rejected, so that another operation cannot overwrite newer requirements.
32. As an e-commerce image creator, I want conflicting writes rejected promptly, so that outdated actions do not silently wait and execute later.
33. As an e-commerce image creator, I want a valid text-only generation path, so that a reference image remains optional.
34. As an e-commerce image creator, I want to upload one actual PNG or JPEG, so that I can guide generation with an original reference.
35. As an e-commerce image creator, I want damaged, disguised, oversized and multiple image uploads rejected before model calls, so that invalid input never reaches generation.
36. As an e-commerce image creator, I want upload limits enforced on actual streamed bytes, so that missing or misleading length headers do not bypass them.
37. As an e-commerce image creator, I want a failed replacement to preserve the previous reference, so that valid work is not lost.
38. As an e-commerce image creator, I want replacement and removal to update the brief revision, so that I must review the changed visual input.
39. As an e-commerce image creator, I want old references retained for historical generations, so that prior versions remain understandable.
40. As an e-commerce image creator, I want reference assets checked for project ownership, so that one project cannot accidentally use another project's files.
41. As an e-commerce image creator, I want unsupported reference input rejected with a reason, so that I can remove it and continue with text.
42. As an e-commerce image creator, I want the original reference passed to VISUAL during feedback regeneration, so that feedback does not silently substitute a generated image.
43. As an e-commerce image creator, I want generation to require matching current, prompt and confirmed revisions, so that stale prompts cannot be submitted.
44. As an e-commerce image creator, I want each accepted generation to save its prompt, size, reference and model context, so that execution can use the original submission.
45. As an e-commerce image creator, I want a durable generation identifier before remote execution, so that I can recover the attempt after losing the response.
46. As an e-commerce image creator, I want the same request identifier and original parameters to return the existing attempt, so that manual recovery does not generate again.
47. As an e-commerce image creator, I want changed parameters under an existing request identifier rejected, so that conflicting submissions are not mistaken for recovery.
48. As an e-commerce image creator, I want old request recovery to work after requirements or configuration change, so that I can still identify the original attempt.
49. As an e-commerce image creator, I want simultaneous submissions for one project constrained to one active attempt, so that rapid actions do not create parallel generations.
50. As an e-commerce image creator, I want generation across projects limited to one executing attempt, so that local execution follows the chosen concurrency limit.
51. As an e-commerce image creator, I want queued and running states distinguished, so that waiting for a local slot is clear.
52. As an e-commerce image creator, I want real execution stages without invented percentages, so that progress reflects available evidence.
53. As an e-commerce image creator, I want a remote asynchronous task queried by its original identifier, so that status polling never creates another task.
54. As an e-commerce image creator, I want generation submission to avoid automatic retries, so that an uncertain connection does not silently trigger another charge.
55. As an e-commerce image creator, I want the local waiting deadline distinguished from remote cancellation, so that I understand the limits of a timeout.
56. As an e-commerce image creator, I want explicit provider rejection recorded as failed, so that I can address authentication, quota or other known errors.
57. As an e-commerce image creator, I want uncertain remote acceptance or completion recorded as unknown, so that I can judge the risk of another attempt.
58. As an e-commerce image creator, I want business failure details returned with the generation record, so that failed work remains readable through normal status queries.
59. As an e-commerce image creator, I want one verified local image before generation is marked successful, so that a task number or temporary URL cannot masquerade as delivery.
60. As an e-commerce image creator, I want non-image, wrong-size and multiple-image responses classified as delivery failures, so that success matches the single-image contract.
61. As an e-commerce image creator, I want delivered files available by resource identifier, so that previews and downloads do not expose local paths or signed provider URLs.
62. As an e-commerce image creator, I want downloaded filenames and extensions to match the actual image encoding, so that files open outside the browser.
63. As an e-commerce image creator, I want missing local files reported separately from missing records, so that history remains visible when a file is unavailable.
64. As an e-commerce image creator, I want resource requests restricted to the application data directory, so that arbitrary local files cannot be read.
65. As an e-commerce image creator, I want known successful remote results retained when local delivery fails, so that an eligible result can be retrieved again.
66. As an e-commerce image creator, I want eligibility and a reason for unavailable retrieval, so that I understand whether an existing result can be recovered.
67. As an e-commerce image creator, I want manual retrieval to update the same generation without submitting again, so that recovering a file does not create a new version.
68. As an e-commerce image creator, I want retrieval rejected when the original service context no longer matches, so that the app does not fetch from the wrong provider.
69. As an e-commerce image creator, I want a repeated retrieval after success to return that success, so that duplicate actions do not damage the result.
70. As an e-commerce image creator, I want feedback applied to current requirements while preserving unchanged constraints, so that new versions reflect the requested change.
71. As an e-commerce image creator, I want failed attempts to keep their version numbers, so that the attempt history is not rewritten.
72. As an e-commerce image creator, I want earlier successful images preserved after later failures, so that I can still download usable work.
73. As an e-commerce image creator, I want refresh and history reads to avoid organizing or generation calls, so that reopening work does not change it or incur charges.
74. As an e-commerce image creator, I want interrupted organizing to retain my message and show an interruption, so that I can decide whether to organize again.
75. As an e-commerce image creator, I want interrupted queued and running attempts marked unknown after restart, so that incomplete local execution is not mistaken for a confirmed outcome.
76. As an e-commerce image creator, I want restart recovery to preserve snapshots and remote identifiers without requeueing, so that historical evidence survives without new submissions.
77. As an e-commerce image creator, I want another attempt after the latest unknown result to require explicit risk acknowledgement, so that possible duplicate charging is a deliberate decision.
78. As an e-commerce image creator, I want repeated startup repair to leave already repaired records unchanged, so that restarting does not duplicate interruption messages.
79. As an e-commerce image creator, I want consistent safe error codes and messages, so that the workspace can explain validation, conflicts and service failures.
80. As an e-commerce image creator, I want credentials excluded from responses, logs, database records and image names, so that troubleshooting does not expose them.
81. As an e-commerce image creator, I want the existing built workspace served with the API in daily use, so that one local backend process is sufficient after building.
82. As an e-commerce image creator, I want unknown API routes to return JSON errors, so that the workspace never interprets an HTML page as an API response.
83. As a frontend integrator, I want generated OpenAPI and actual responses aligned with the centralized client types, so that draft field assumptions can be resolved.
84. As a backend implementer, I want application-level tests with real temporary persistence and controlled providers, so that concurrency, delivery and restart behavior can be verified without model charges.
85. As a project maintainer, I want actual model and file-delivery evidence kept separate from controlled test results, so that v1 completion reflects the real user workflow.

## Implementation Decisions

1. **现有工程与目录约束。** 保留当前 Vue 工作台、集中客户端、根级 npm/Vite 工程与现有构建输出安排，不因旧目录示意搬迁前端。后端应用源码进入 main 中的独立后端子目录；依赖、锁文件、配置、迁移、数据库、图片运行数据、测试与验收资料分别放在 main 之外的对应目录。日常网页入口解析当前 Vite 配置实际输出的产物。路径按工程位置解析为绝对路径，不依赖启动终端的工作目录。

2. **技术与部署。** 沿用 Python 3.12、FastAPI、Uvicorn、Pydantic v2、pydantic-settings、SQLAlchemy 2、aiosqlite、Alembic、HTTPX、Pillow、python-multipart；uv 锁定后端依赖，Ruff 和 pytest/pytest-asyncio 执行后端检查。仅监听 127.0.0.1，单进程、一个 worker，进程内 BackgroundTasks，全局生图执行并发为 1。开发复用现有 Vite API 代理，不新增通配 CORS；不把已有前端容器当成后端服务。

3. **模块职责。** 应用装配负责生命周期及网页入口；配置模块负责路径、密钥和脱敏状态；数据库与迁移负责持久化约束；项目服务负责创建、分页、详情；需求服务负责对话、修订、确认；生成服务负责受理、去重、执行、中断修复；资源服务负责上传、验证、落盘、下载、重取；适配器契约及注册表隔离供应商协议；HTTP 层只解析、调用服务并映射响应。不引入通用仓储框架或多智能体框架。

4. **配置。** LLM 与 VISUAL 各有 ADAPTER、BASE_URL、API_KEY、MODEL；VISUAL 另有 IMAGE_SIZE；整理初始总等待上限为 60 秒，生图为 300 秒，另有数据目录配置。模型、地址、Key、适配器及尺寸在配置示例中留空。操作系统同名变量覆盖本机配置文件，修改后重启生效。Key 使用 SecretStr，只在实际构造供应商请求时读取明文，不进入业务输入、数据库或公开 DTO。

5. **启动与停止。** 解析配置和路径、创建数据目录、检查数据库迁移版本，在接受请求前修复遗留状态，再装配共享 HTTP 客户端、项目门闩、全局生图信号量及路由；关闭时释放客户端和数据库。表结构由 Alembic 维护，不以运行时自动建表绕过迁移。每个连接启用外键；采用 WAL 和 5,000 毫秒 busy timeout 的设计默认值。数据库不可写或迁移未完成时明确停止正常启动；模型缺失、未知适配器或能力不符仅阻止对应模型操作，保留本机诊断和历史读取。

6. **只读配置状态。** 两个角色返回 configured、ready、adapter_id、model、missing_fields、issues、timeout_seconds、verification；verification 固定 not_checked。configured 表示字段齐全，ready 表示本地配置及能力校验通过，均不证明真实认证或连通成功。VISUAL 能力包含文生图、参考图、异步能力，精确支持尺寸和当前尺寸，参考图 MIME、供应商字节上限及实际字节上限。能力未知为 null，不伪装成 false；空配置仍返回本机上传上限。状态不返回服务地址、Key、签名 URL 或持久认证成功标记，读取时不访问模型。

7. **领域实体与持久记录。** Project 是一个商品的持续制作记录；Message 是用户原文、助手回复或系统中断说明；Generation 是一次单张生成尝试，包括失败与未知；Asset 是一份本机图片。Project 保存当前 Brief、修订、提示词关联修订、确认修订、当前参考图、整理操作 ID、整理错误及时间；Message 保存项目归属、序号、角色、原文、关联修订、操作 ID 与可空 Brief 快照；Generation 保存版本、request_id、规范化原请求及摘要、完整快照、提示词、参考图、适配器/模型/服务指纹、远端任务号、私有结果定位、结果资源、错误、阶段和时间；Asset 保存项目归属、reference/generated 类型、内部相对位置、实际 MIME、像素、字节数和时间。

8. **标识、排序与初始值。** 服务端标识为 UUID，request_id 由客户端生成，持久化使用规范 UUID 字符串；时间为 UTC、API 使用带时区 ISO 8601。新项目标题为“未命名项目”，revision=0、draft，提示词和确认关联修订、参考图、尺寸为空，文本为空。首次有效用户文字折叠空白后取前 32 字符设置标题，此后保持；不额外调用 LLM 命名。消息按 sequence_no 升序，生成按 version_no 升序。项目持久修改、整理及生成交付更新 updated_at，GET 不更新。

9. **数据库不变量。** 项目加消息序号、项目加版本号、项目加 request_id 各自唯一；版本从 1 递增且失败不释放。部分唯一索引保证每项目最多一个 queued/running。外键和服务归属校验共同保证参考图、结果图属于本项目且类型正确；修订非负，关联修订不超过当前修订，状态/角色/资源类型受约束。建立项目更新时间及消息/版本顺序索引。替换引用不级联删除历史图片。

10. **写入互斥和事务。** 项目写操作先取进程内门闩，忙时立即 409 PROJECT_BUSY；门闩覆盖本次 HTTP 写操作，包括外部整理或同步重取，最终释放。活跃生成通过持久状态阻止项目冲突写入；读历史、读状态和下载保持可用。写入使用独立 session、新短事务与 BEGIN IMMEDIATE；不依赖 SQLite 行锁。模型调用、排队、下载和解码期间不持有写事务。请求与后台任务不共享 session/ORM 实例，后台只接收 Generation ID。约束竞争回滚后重读并映射为相同原提交或活跃冲突，不一律返回 500。

11. **统一 Brief。** 当前卡片、历史卡片和生成快照共用 product_description、purpose、style、composition、text_content、image_size、reference_asset_id、prompt。前五项为可编辑字符串，text_content 空串表示不要求加字；后三项由后端管理。image_size 是已核实的精确方形像素字符串或 null，prompt 空串表示尚无提示词。当前参考图以项目引用为权威；历史快照保存完整当时值，不随当前需求改写。

12. **需求修订。** 保存用户消息并开始整理使 revision 加 1、清空两种关联修订，进入 organizing；有效 LLM 回复再产生新修订，needs_input 清空关联修订，review 将 prompt_revision 设为新 revision。卡片或参考图实际变化使 revision 加 1、清空关联修订、进入 draft；完全相同的卡片保存及移除本来为空的参考图不变更修订。确认不增加 revision，将 confirmed_revision 设为当前 revision。整理失败/中断及生成状态变化不增加需求修订。旧提示词可供阅读，其有效性仅由 prompt_revision 判断。

13. **整理受理和防止晚到覆盖。** 消息非空、期望修订匹配、无活跃生成且 LLM 本地可用时，先事务保存用户原文、消息序号和整理操作 ID，再外部调用一次 organize。配置明确不可用时不保存本次消息。完整回复回来后，必须同时匹配项目、起始修订、organizing 状态和操作 ID 才能提交助手消息、快照及新修订；否则 409 STALE_ORGANIZATION，保留用户原文。失败只允许仍持有原操作 ID 的流程转回 draft 并持久脱敏错误，通过 502/504 返回；详情可以恢复该错误。不自动追加第二次调用修复 JSON。

14. **LLM 输入与输出。** 输入为已保存对话、当前需求、用户本次意见和参考图是否存在；参考图字节不传给纯文本 LLM。业务文字作为数据处理，不替代服务端系统约束。输出严格区分 needs_input（完整问题及可选部分文本字段）和 review（完整文本字段及非空完整提示词），经 Pydantic 校验。服务端注入参考图 ID 与整理开始时的已核实尺寸，不接受 LLM 自选模型、资源或尺寸。商品主体、用户提及价格/活动有歧义时集中提问；未知商品事实不编造，视觉建议可见。反馈保留未要求改变的约束。上下文无法容纳时明确失败，不静默截断需求；结构校验不等于事实正确性保证。

15. **保存、重新整理和确认。** PATCH 只接收允许编辑的文本字段和 expected_revision，缺席字段不变、显式 null 无效，至少一个可编辑字段；保存不调用模型。重新整理沿用消息接口，支持当前前端发送的“请按当前已保存的需求重新整理。”，不是独立新路径。确认校验当前修订、有效非空提示词、可识别商品主体、无待澄清问题、已核实且与当前选择一致的尺寸及无冲突操作；重复确认同一有效版本返回当前项目，不调用任何模型。前端未发送/未保存草稿由现有工作台阻止，后端只依据已保存状态校验。

16. **先处理原请求身份。** 生图 JSON 仅包含 request_id、confirmed_revision 和可选 acknowledge_unknown。字段类型校验后，先查询项目加 request_id，再进入门闩和当前需求/配置门槛。规范化原请求包含项目 UUID、请求 UUID、整数确认修订、确认标记的出现与否及布尔值，稳定排序 JSON 并存 SHA-256 摘要及原文。省略 acknowledge_unknown 与显式 false 是不同原参数。相同原参数返回 200 原 Generation，不挂新后台任务；同 ID 改参数为 409 IDEMPOTENCY_CONFLICT。此判断不依赖当前卡片、配置或后续活跃任务，只提供本机去重，不承诺远端 exactly-once 计费。

17. **新生成受理。** 门闩内短事务再次检查去重，再校验无整理/活跃生成，revision、prompt_revision、confirmed_revision 非空且相等，VISUAL 本地配置与文生图能力就绪，固定尺寸与卡片一致；参考图如存在须文件可用、归属正确且能力/格式/字节受支持。最近一条生成按最大 version_no 判断，若为 unknown，新 ID 必须显式 acknowledge_unknown=true，否则 409 UNKNOWN_ACK_REQUIRED；旧 unknown 保留。同一事务分配新版本并保存完整快照、提示词、参考图、模型上下文和原请求，创建 queued；提交后只以记录 ID 注册后台，返回 202。可观测的调度中断写 unknown，否则由下次启动修复。

18. **后台领取与执行快照。** 后台等待全局信号量时保持 queued/waiting_slot；取得执行机会后条件领取仍为 queued 的记录，写 running 和 started_at，领取失败立即退出。执行输入从记录快照加载，不使用后来卡片替换。每次成功领取最多一次 ImageAdapter.submit，显式关闭 HTTPX transport 与供应商 SDK 的提交重试。数量固定为一张。

19. **异步查询、阶段与等待。** 得到远端任务号立即持久化，只 poll 同一任务；查询短暂失败可在总时限内继续查询，不能回到 submit。阶段为 waiting_slot、submitting、waiting_remote、fetching_result、done，同步接口跳过 waiting_remote；成功为 done，失败/未知保留最后实际阶段。总等待从 running 开始覆盖提交、查询和落盘，排队另计；分阶段 HTTP 超时不能替代总上限。本机停止等待不表示远端已取消，不生成虚构百分比、ETA 或费用。

20. **状态与证据分类。** queued 可到 running，queued/running 中断为 unknown；running 到 succeeded/failed/unknown。failed 只有满足既有结果重取条件时可到 succeeded；unknown 保持；succeeded 保持，文件可用性单独计算。确定未提交或明确拒绝/认证/限流/额度/任务失败为 failed；提交后断连、受理不明、等待耗尽为 unknown，保留已有远端号。已知远端成功而下载、解码、尺寸或落盘失败为 failed/RESULT_FETCH_FAILED。适配器按 not_submitted、rejected、uncertain、result_available 提供证据，不能将所有 5xx、解析异常或写超时当作明确失败。业务终态保存在 Generation，状态 GET 仍是 200。

21. **启动中断修复。** 接受请求前短事务将遗留 queued/running 改为 unknown、PROCESS_INTERRUPTED 和终态时间；保留快照、远端号及定位。遗留 organizing 改 draft，清空操作 ID 和两种关联修订，保存 ORGANIZATION_INTERRUPTED，保留用户消息和旧卡片；如追加系统说明只能一次。重复修复不产生新增变化，不调用 LLM、submit、poll，不重新入队。进程内后台任务不具备持久调度保证，本版不自动恢复远端任务。

22. **参考图接收。** 单张实际 PNG/JPEG，本机硬上限 10,000,000 字节，与已核实供应商更小上限取小值。multipart 解析前限制实际请求体为有效文件上限加 64 KiB 协议预算，再校验文件流实际字节；不只相信 Content-Length。超限 413，多文件、重复/未知字段、不符格式或损坏为 422。临时 UUID 文件经 Pillow verify 后重新打开 load，使用实际 MIME/尺寸，将解压炸弹警告视为失败。解码/文件处理移出事件循环，不将数据库 session 传入线程。

23. **引用替换与历史保留。** 验证完成后取门闩，事务重新核验修订和活动状态，移动合法文件并插入 Asset、更新当前引用与修订。失败保留旧有效图；删除仅清空当前引用。上传、替换、移除均不调用模型，成功生成也不覆盖原始参考图；历史依赖文件不自动删除。

24. **真实结果落盘。** Base64 在后端解码；远端文件流读取设置上限，首版本机默认单文件 64 MiB，另按供应商协议核验。只接受恰好一张已核实栅格格式的可完整解码图片，像素符合提交快照；多图、HTML、文本、损坏或尺寸不符都是 RESULT_FETCH_FAILED，并保留可用定位。下载仅访问适配器认可来源，逐跳验证重定向，不接受客户端任意 URL，CDN 请求不沿用模型认证头。先在同一数据目录临时写入、验证、移动为 UUID 文件，再提交 Asset 与 succeeded。SQLite 与文件系统不具备共同原子事务，提交失败的孤立文件不得作为成功资源暴露。

25. **资源交付和文件缺失。** 公共资源只按 Asset UUID 读取，内部相对位置解析后必须仍在数据目录内。按实际 MIME 预览，download=true 返回附件；生成下载名携带项目 ID、版本号与实际编码扩展名，参考图按资源 ID 命名，不使用原上传名。AssetView.availability 为 available/missing。文件丢失保留元数据及成功历史，资源 GET 返回 404 ASSET_FILE_MISSING，不在 GET 中改任务状态或重新生图。

26. **手动重取原结果。** 同步 POST，成功返回 200 同一 Generation，不创建新版本或扩展状态。允许原记录 failed/RESULT_FETCH_FAILED、有可用且未过已知有效期的定位、原适配器可用、当前服务指纹匹配，且无整理/活跃生成/其他写操作。指纹来自适配器、规范地址、模型，不含 Key；定位可含私有临时签名地址，不能进入公开 DTO/日志。服务端计算 can_retrieve_result 及安全不可用原因；无定位的纯字节结果不承诺重取。执行持有项目门闩但不持数据库事务，沿用生图等待上限；期间记录仍 failed。成功更新原记录、清空错误，失败保持 RESULT_FETCH_FAILED 并返回 502/504。重复请求若已成功返回现有成功。重取只取同一已知结果，submit 次数必须为零；刷新、断网和启动不自动重取。

27. **HTTP 公共规则。** snake_case，JSON UUID、非负修订整数和布尔严格校验，不接受布尔字符串/整数或未定义字段；query/multipart 按传输文本解析。null 表未知或无关联，空集合为 []。消息上限 20,000 字符，可编辑卡片各字段 8,000 字符，完整提示词 32,000 字符；超限 422，不自动截断。成功 mutation 返回提交后实际状态。所有校验、404 和未捕获异常统一映射安全错误，错误对象只有 code/message，不回显原始输入或堆栈。写请求无通用自动重试，回执不明由现有前端 GET 核实。

28. **既有十四种方法和路径。** 保持现有前端调用入口，不新增配置写入、任务取消或付费探测接口。

   | 方法与 API 路径 | 输入 | 成功响应 |
   |---|---|---|
   | GET /api/health | 无 | 200，status=ok；只检查本机应用与数据库可读 |
   | GET /api/config/status | 无 | 200 ConfigStatus |
   | POST /api/projects | 空 JSON 对象 | 201 ProjectView |
   | GET /api/projects | limit 默认 20、范围 1–100；可选 cursor | 200，items 与 next_cursor |
   | GET /api/projects/{id} | 项目 UUID | 200，project、messages、generations |
   | POST /api/projects/{id}/messages | content、expected_revision | 200，project、user_message、assistant_message；完整等待整理 |
   | PATCH /api/projects/{id}/brief | expected_revision 与至少一个可编辑文本字段 | 200 ProjectView |
   | POST /api/projects/{id}/confirm | revision | 200 ProjectView |
   | POST /api/projects/{id}/reference | 唯一 file 与 expected_revision 的 multipart | 200 ProjectView |
   | DELETE /api/projects/{id}/reference | query expected_revision | 200 ProjectView |
   | POST /api/projects/{id}/generations | request_id、confirmed_revision、可选 acknowledge_unknown | 新记录 202；相同原请求 200；均为 generation 包装 |
   | GET /api/generations/{id} | 生成 UUID | 200 GenerationView，包含 failed/unknown |
   | POST /api/generations/{id}/retrieve-result | 空 JSON 对象 | 同步 200 GenerationView |
   | GET /api/assets/{id} | 资源 UUID，可选 download 默认 false | 200 实际文件流 |

29. **公共 DTO。** AssetView 返回 id、kind、mime_type、width、height、byte_size、created_at、availability。ProjectView 返回 id、title、revision、prompt_revision、confirmed_revision、brief_status、brief、reference_asset、brief_error、created_at、updated_at。MessageView 返回 id、sequence_no、role、content、project_revision、brief_snapshot、created_at。GenerationView 返回 id、project_id、version_no、request_id、confirmed_revision、status、stage、brief_snapshot、prompt、reference_asset_id、adapter_id、model、remote_task_id、result_asset_id、result_asset、error、can_retrieve_result、retrieval_unavailable_reason、created_at、started_at、completed_at。资源及错误关联可为 null；无卡片消息的 brief_snapshot 为 null，生成提交快照必须完整。公开远端号必须是任务标识，不是认证 URL。响应按字段白名单组装，不整体序列化 ORM 或配置。

30. **列表与关联。** ProjectSummary 返回 id、title、brief_status、latest_generation_status、updated_at；无生成时生成状态为 null。按 updated_at DESC、id DESC 游标分页，cursor 编码末条 UTC 时间与 UUID，读取 limit+1 判断下一页，末页 next_cursor=null，无效游标 422。列表不提供跨页快照，前端继续按 ID 去重，重新加载从首批开始。详情一次返回本项目全部消息/生成。历史卡片通过助手消息快照恢复，生成通过提交快照恢复，待核实提交通过 request_id 精确关联，不从普通文本推断卡片或任务。

31. **错误语义。** 404 区分 PROJECT_NOT_FOUND、GENERATION_NOT_FOUND、ASSET_NOT_FOUND、ASSET_FILE_MISSING、ROUTE_NOT_FOUND；409 覆盖 REVISION_CONFLICT、PROJECT_BUSY、ACTIVE_GENERATION_EXISTS、STALE_ORGANIZATION、IDEMPOTENCY_CONFLICT、UNKNOWN_ACK_REQUIRED、BRIEF_NOT_READY、IMAGE_SIZE_CHANGED、RESULT_NOT_RETRIEVABLE、RETRIEVE_CONFIG_CHANGED；413 为 UPLOAD_TOO_LARGE；422 包括 VALIDATION_ERROR、INVALID_REFERENCE_IMAGE、UNSUPPORTED_REFERENCE_FORMAT、REFERENCE_NOT_SUPPORTED、INVALID_CURSOR；503 包括 MODEL_NOT_CONFIGURED、ADAPTER_UNAVAILABLE、MODEL_CAPABILITY_MISMATCH、DATABASE_BUSY、LOCAL_STORAGE_UNAVAILABLE；同步整理/重取失败为 502，超时为 504；意外异常为安全 INTERNAL_ERROR。Generation.error 另承载规范化视觉认证、限流、额度、任务失败、结果不明、进程中断、参考文件缺失及交付错误；它们不改变正常状态 GET 的 200。

32. **供应商契约。** LLMAdapter.organize 一次返回严格判别联合；ImageAdapter.capabilities 只读本地声明；submit 接收完整生成快照、固定尺寸、可选参考文件且数量一张，返回图片数据/定位或远端任务号；poll 仅查询该号，返回运行、结果或明确失败。鉴权、请求格式、错误证据及来源限制均在适配器中。每个角色首版适配一种选定协议，可来自同一或不同供应商；可改 BASE_URL 不代表兼容任意协议。未注册能力未知，生产不能回退到测试替身或模拟成功图。

33. **网页入口与日志。** 仅公开当前前端构建产物和资源 ID 接口；配置、数据库、图片数据目录不整目录公开。未知 API 返回 JSON 404，不落入网页入口；缺少网页产物时给出构建提示，诊断 API 仍可用。日志记录项目/生成/请求 ID、阶段、耗时、规范错误码；默认不记录完整对话、提示词、Base64、供应商原始响应、签名 URL 或认证信息，不直接启用 HTTP 客户端敏感调试日志。

34. **前端对齐与证据。** 后端 Pydantic 生成的 OpenAPI 作为运行契约，与真实响应共同核验 FE-DEP-01–06。同步现有集中类型、客户端和必要行为映射，修正草案与实际的可空性、分页、修订、能力和重取差异；若偏离产品/后端设计，先同步设计和验收约定，不维持两套含义。既有前端 fixture 中的简化标识或示例状态不是合法 UUID/阶段的后端事实，不照抄为服务数据。本规格只要求必要接入修正，既有前端整体浏览器验收继续由 #13 跟踪。

35. **交付顺序与完成边界。** 依次完成启动/配置/迁移和诊断；项目/消息/资源与上传；LLM 组织契约、需求卡与确认；生成快照、去重、执行和重启；交付/重取/错误及前端对齐；最后接入两个角色的真实协议并验收。模型选择未定不阻止前五阶段的受控实现，不能因此宣称真实 v1 完成。实际供应商接入前必须查官方文档核实鉴权、文生图及参考图能力、精确尺寸、格式/字节限制、等待策略、异步查询及结果有效期；Key 由用户在本机填写，不写入规格或 Issue。

## Testing Decisions

- **已确认的唯一主要自动化边界：完整 FastAPI 应用的公开 HTTP 行为。** 测试经真实路由、校验、服务、后台执行、迁移后的临时 SQLite 及真实临时图片目录运行；仅在声明的供应商适配器/外部传输边界控制回复、延迟、断连和调用记录，不 mock 数据库、事务、资源服务或业务状态机。后端尚无现成入口，需要在应用装配处提供外部配置与供应商实例注入，复用设计中已有适配器契约，不为每个服务另建测试缝。
- **重启和调度事实。** 同一 HTTP 边界下补充真实单 worker 服务进程的启动/终止/再启动场景，重新使用同一临时数据目录；先通过 HTTP 制造 organizing/queued/running 等真实在途状态，再中断和读取，不通过私有恢复函数伪造过程。验证受理响应与后台仍在途的测试必须保留真实响应/后台时序，不能让测试客户端先等后台完成再把它当成 202 及时返回的证据。并发通过可控供应商屏障和独立 HTTP 请求制造，避免仅串行调用后断言防重。
- **好的测试。** 从公开操作开始，断言 HTTP 状态/安全响应、公开记录与修订/快照、文件实际内容/尺寸/编码，以及模型 submit/organize/poll 的必要次数和输入；同时检查禁止发生的副作用。原请求恢复比较所有原参数及确认字段存在性，重取断言原版本不变且新增 submit 为零。少量检查数据库/日志/数据文件仅用于密钥不落地和迁移持久性证据，不以私有 ORM 字段或函数调用顺序作行为断言。
- **覆盖模块。** 通过该入口覆盖配置和生命周期、项目/消息/Brief 修订、生成去重与执行、图片资源和重取、统一异常及前端契约；供应商协议接入时以官方样例和受控传输验证其外发格式/证据分类，真实能力仍需真实调用。
- **可复用先例。** 现有前端工作台测试从完整 App 和 HTTP 边界验证行为，已有原提交恢复、unknown 风险确认、重取、刷新零自动 POST、旧版保留等场景。复用其场景、集中 DTO 和外部副作用断言原则；既有测试不会运行后端，fixture 不能证明数据库、供应商或文件交付正确。后端测试及替身置于 main 之外。
- **验收矩阵。** 下列 BE-AC 编号沿用后端设计，全部为待实现后验证；增加契约、并发和脱敏维度时仍经同一应用边界。

| 编号 | 受控场景 | 可观察通过条件 |
|---|---|---|
| BE-AC-01 | 空配置、分别缺角色、未知适配器、错误 Key、基础存储不可写 | 空配置可启动读历史；对应操作正确 503/业务错误；基础设施失败明确停止；启动/状态读取模型调用为零；响应、日志、持久文件无测试 Key |
| BE-AC-02 | 澄清、完整整理、编辑保存、同值保存、重新整理、重复确认 | 用户原文保留；修订符合事件规则；历史卡片不变；保存无模型调用，确认无生图；JSON 类型、未知字段、null 和长度限制正确 |
| BE-AC-03 | 两次并发整理、冲突编辑、晚到回复 | 一个获得处理权；409 不覆盖当前数据；用户原文保留；晚到结果不能提交新状态 |
| BE-AC-04 | MIME/后缀伪装、损坏/解压炸弹、多图/重复字段、边界字节、无长度超大请求体 | 实际解码与流式限制生效；413/422 正确；旧图不变；模型调用为零 |
| BE-AC-05 | 替换/移除、有图/无图、跨项目资源、引用文件缺失 | 真实变更使旧确认失效；合法当前原始参考图进入生成输入；跨项目拒绝；旧历史文件可读；失败不替换旧图 |
| BE-AC-06 | 相同 ID 并发、不同 ID 竞争、同 ID 改参数、省略确认标记改 false、跨项目排队 | 同原请求仅一个记录及一次 submit；竞争/冲突符合契约；每项目至多一个活跃，全局至多一个执行；后台重复领取不新增 submit |
| BE-AC-07 | 终态后修改需求、配置、再恢复旧原提交 | 200 返回原 ID、版本及快照；不按当前状态重建，不调用模型；同 ID 改参数仍 409 |
| BE-AC-08 | 同步/异步响应、查询短暂失败、提交后断连、受理不明、总等待耗尽 | 仅一次 submit；poll 始终原任务号；failed/unknown 按证据区分；等待不算取消；原阶段和已知编号保留 |
| BE-AC-09 | 首版成功，后续成功/失败，查看并下载旧版 | 版本不复用；各版快照、提示词和原始参考图正确；成功旧文件保持实际可解码 |
| BE-AC-10 | 整理/排队/生成中只读刷新、进程中断重启、再次重启 | 读取不发新整理/生图；遗留状态只修复一次；原消息和快照/远端号保留；重启不 submit/poll/organize/重取 |
| BE-AC-11 | 最新 unknown 后新 ID，缺省/false/true 风险标记 | 缺省及 false 均 409；仅 true 且其余条件有效才能新建；旧 unknown 保持；原 ID 恢复不要求新的确认 |
| BE-AC-12 | 已知成功但下载/解码/尺寸/落盘失败，多图响应，重取与生成竞争、配置变更/过期定位 | 保存可用定位，能力及原因准确；重取不增版本且 submit 为零；失败仍 failed；重复成功返回原记录；冲突被拒 |
| BE-AC-13 | 成功文件丢失、缺记录、路径越界、CDN 重定向及响应鉴权检查 | 元数据 missing 与资源 404 区分；不改成功状态；数据目录外不可读；下载不带模型 Key；不暴露私有定位 |
| BE-AC-14 | 多页/同时间项目、坏游标、无生成项目、历史卡片和待核实提交 | 排序与末页准确；无效 cursor 422；消息/版本顺序稳定；快照可还原、request_id 可精确匹配；实际 OpenAPI 覆盖既有十四种方法及 DTO |
| BE-AC-15 | 当前前端产物、未知 API、无产物、开发代理 | 日常入口使用现有实际构建目录；未知 API JSON 404；无产物仍可诊断；配置和数据目录不直接公开 |

- **工程检查。** 实现后运行 Ruff、pytest 关键后端场景及迁移/启动检查；核验 OpenAPI 与 FE-DEP-01–06 后，运行既有 Vue/TypeScript 类型检查、工作台完整测试与生产构建。本文是文档交付，编写规格本身不执行这些尚不存在的后端检查，也不把过去前端通过记录写成本次结果。
- **真实验收。** SRS AC-01、02、03、07 必须由实际 LLM 和同时支持无图/参考图的 VISUAL 完成：两套配置不泄露、文字整理确认生图、参考图字节实际传入、反馈保留约束、新旧版下载文件可在浏览器外打开。AC-10–12 使用真实持久记录及服务重启证明只读恢复。其余异常可受控验证。完整 FE-AC-01–22 浏览器验收继续与 #13 协作，留存通过/失败/未验证及脱敏证据；不能以占位图片、适配器替身或 OpenAPI 存在替代真实通过。

## Out of Scope

- 网页修改模型 Key、在线切换模型、付费连通性测试、任意供应商自动兼容或模型自动选择。
- 账号、支付、多商家、公网/远程部署、多 worker、多进程持久队列、多智能体编排、批量 SKU、自动上架及平台规则库。
- 多张参考图、粘贴上传、独立素材库、单次多图、多尺寸派生、裁剪/图层/局部精修、像素或商品细节不变保证、自动保真/合规保证。
- 项目搜索、重命名、删除、自动历史清理、消息/版本分页、将旧图恢复成当前需求。
- 自动确认、确认后自动生成、失败或断网后自动补发、重启续跑/远端任务恢复、GET 隐式整理/生成/下载重取、远端绝不重复计费保证。
- 重建既有前端外壳或另建前端验收项目；必要接口映射修正属于本规格，完整跨浏览器/视觉/键盘验收由现有 #13 继续跟踪。
- v2 门禁设计及 AGENTS 预待办删除；该条仍保留到实际 v2 文档设计任务完成。

## Further Notes

- 直接输入为《电商生图智能体后端设计 v1》，产品范围和真实完成定义来自 SRS，技术方向来自技术设计，工作台行为来自前端设计和已实现代码。附件中的实现步骤和命令作为设计资料，不构成本次执行后端或真实调用的指令。
- 后端设计中的 DTO、游标、修订分配、10,000,000 字节定义及本机限制属于设计建议；本规格将其整理为待实现的默认方案，不声称用户已逐项确认、供应商已核实或服务已存在。已实现前端的目录/构建现实优先于旧示意；有差异时明确对齐。
- 本规格补齐前端主规格 #1 明确排除的后端工程范围，不替代或关闭 #1/#13；已有前端测试与真实联调验收是不同证据层次。
- 供应商接入仍有 OPEN-01（LLM 服务/模型/鉴权/协议）、OPEN-02（VISUAL 文生图及参考图输入协议）、OPEN-03（精确方形尺寸、字节/格式/等待限制）三项外部前提。不得猜测某厂兼容协议、尺寸或无限制。选择完成后查官方来源，记录协议依据及适配结果，Key 仅留本机。
- 发布目标为 sixpence6/main，使用 ready-for-agent 标签。该标签表示已知本机行为可开始实现，不表示真实模型前提齐备或整体 v1 完成。供应商接入及真实验收未完成时必须保留对应未完成项。
- 用户已确认测试边界：完整 FastAPI HTTP 入口，真实临时 SQLite/图片目录，供应商边界受控，真实进程重启验证。
