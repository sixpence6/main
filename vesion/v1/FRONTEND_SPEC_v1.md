# 电商生图智能体 v1：前端工作台与生成恢复规格

日期：2026-10-29。范围：v1 前端实现与验收。测试边界已获用户确认；应用实现与验收尚未完成。

## Problem Statement

电商图片制作者希望通过中文自然语言描述商品和画面，可选提供一张参考图，得到可以确认的需求与完整提示词，再明确决定是否生成、下载或反馈重生。用户需要在同一工作台查看历史项目与成功图片，知道当前需求是否保存、是否确认，以及当前生成究竟成功、失败还是结果未确认。

刷新、断网、切换项目和服务重启可能让提交结果难以判断。如果界面把导航、需求确认、读取状态或重取文件隐式连接到生成操作，用户可能在不知情时重复提交；如果浏览旧图覆盖当前需求，或失败清空旧结果，用户会丢失制作上下文。本规格要求把这些操作及其结果清楚分开，同时提供符合前端设计的响应式工作台。

当前仓库只有设计资料，尚无应用代码、可运行后端、生成的 OpenAPI 或现成测试。本规格定义待实现行为，不把设计文档中的完成声明当作实现或真实联调证据。

## Solution

提供本机单用户中文工作台，使用 Head／Side／Body 三个区域。Head 展示产品名，Side 提供项目与只读模型状态入口，Body 在需求对话和图片结果之间切换。用户按“描述需求 → 确认需求 → 生成图片 → 查看结果”完成单张图片制作，并能保留原始参考图进行反馈重生、预览和下载各成功版本。

所有写操作由用户明确触发。需求编辑、保存、重新整理、确认和生成分别提供入口；只读恢复不会自动提交整理或生成。界面以服务端实际修订号和任务记录为准，保留未保存内容与成功旧图，提供原提交恢复、未知结果确认、状态重新查询及现有结果重取等不同操作。模型未配置或契约尚未接通时显示实际限制，正式界面不以模拟图片冒充成功。

## User Stories

1. As an e-commerce image creator, I want a Chinese workspace for describing products, so that I can create images without writing specialist prompts.
2. As an e-commerce image creator, I want Head to show the product name, so that the workspace has a clear identity.
3. As an e-commerce image creator, I want project actions and model status in Side, so that I can find them from either Body mode.
4. As an e-commerce image creator, I want to expand or collapse Side, so that I can choose how much room the content receives.
5. As an e-commerce image creator, I want a usable layout in wide, medium and narrow windows, so that resizing does not hide essential actions.
6. As an e-commerce image creator, I want separate conversation and result modes, so that each task has enough space.
7. As an e-commerce image creator, I want stage navigation with unavailable reasons, so that I understand what I can view next.
8. As an e-commerce image creator, I want navigation to preserve the actual project status, so that browsing does not imply progress or start generation.
9. As an e-commerce image creator, I want a blank workspace when no project is selected, so that opening the app does not silently select or create a project.
10. As an e-commerce image creator, I want examples to fill the input without submitting it, so that I can adapt a suggestion first.
11. As an e-commerce image creator, I want the first valid message or reference upload to create a project once, so that subsequent operations share its history.
12. As an e-commerce image creator, I want input preserved when project creation is uncertain, so that I can check history before creating another record.
13. As an e-commerce image creator, I want multiline input and an explicit send shortcut, so that I can write detailed requirements.
14. As an e-commerce image creator, I want Chinese input composition to finish without accidentally sending, so that my message remains complete.
15. As an e-commerce image creator, I want a visible organizing state and a complete LLM reply, so that I know whether the app is waiting for a response.
16. As an e-commerce image creator, I want clarification questions in the conversation, so that I can resolve missing product or promotion information.
17. As an e-commerce image creator, I want unspecified product facts to remain unspecified, so that the interface does not invent claims about my product.
18. As an e-commerce image creator, I want a structured requirement card, so that I can review the product, purpose, style, composition and requested text.
19. As an e-commerce image creator, I want an empty image-text field to mean no requested text, so that absence of text is unambiguous.
20. As an e-commerce image creator, I want the full prompt and exact configured size to be visible before generation, so that I know what I am approving.
21. As an e-commerce image creator, I want only the latest requirement card to be editable, so that historical records remain reliable.
22. As an e-commerce image creator, I want local card edits to support save and cancel, so that I control when server data changes.
23. As an e-commerce image creator, I want saving a card to require explicit reorganization, so that saving alone does not call the LLM.
24. As an e-commerce image creator, I want to confirm the current card and prompt separately from generating, so that approval does not incur an image request.
25. As an e-commerce image creator, I want changed requirements, references and unsent drafts to block stale confirmation or generation, so that the submitted request matches my intent.
26. As an e-commerce image creator, I want a complete text-only flow, so that uploading a reference is optional.
27. As an e-commerce image creator, I want to select or drop one PNG or JPEG reference, so that I can guide the generated image.
28. As an e-commerce image creator, I want invalid, oversized, damaged or multiple files rejected before model calls, so that mistakes do not replace my valid reference.
29. As an e-commerce image creator, I want a pending upload preview distinguished from a saved reference, so that I know which image will actually be used.
30. As an e-commerce image creator, I want to replace or remove my reference while retaining the previous image on failure, so that a failed change is recoverable.
31. As an e-commerce image creator, I want unsupported or unknown reference capability explained, so that I can remove the image or finish configuration.
32. As an e-commerce image creator, I want feedback generation to retain my original reference, so that a generated result does not silently become the next input.
33. As an e-commerce image creator, I want the generation summary to show purpose, prompt, reference, size and one-image quantity, so that I can review the actual request.
34. As an e-commerce image creator, I want repeated clicks to produce at most one in-flight action, so that I do not accidentally duplicate requests.
35. As an e-commerce image creator, I want editing locked during conflicting operations while history remains readable, so that I can inspect existing work safely.
36. As an e-commerce image creator, I want actual queued, running, failed, succeeded and unknown states, so that waiting is not disguised as success or a guessed percentage.
37. As an e-commerce image creator, I want successful generation to show the resulting image, so that I can inspect the delivered file.
38. As an e-commerce image creator, I want version thumbnails and available metadata, so that I can find earlier successful results.
39. As an e-commerce image creator, I want viewing an old version to leave current requirements unchanged, so that browsing does not undo my edits.
40. As an e-commerce image creator, I want a new result notification while I am viewing an older image, so that the app does not replace my chosen preview.
41. As an e-commerce image creator, I want Continue editing to return to the conversation without sending, so that I can compose feedback first.
42. As an e-commerce image creator, I want feedback to produce a new review and confirmation cycle, so that regeneration uses an explicitly approved prompt.
43. As an e-commerce image creator, I want fit-to-window and original-size viewing, so that I can inspect the whole image and its details.
44. As an e-commerce image creator, I want downloading to use the selected version, so that the file matches the image I am viewing.
45. As an e-commerce image creator, I want prior successful versions retained after failure, so that unsuccessful attempts do not erase usable work.
46. As an e-commerce image creator, I want a paginated project drawer with empty, loading and retry states, so that I can navigate history reliably.
47. As an e-commerce image creator, I want project links and browser history to restore the selected project by reading it, so that navigation does not invoke models.
48. As an e-commerce image creator, I want a choice before discarding an unsent message or unsaved card, so that switching projects does not silently lose work.
49. As an e-commerce image creator, I want the limits of refresh and tab-close draft recovery explained accurately, so that I do not rely on storage that is not provided.
50. As an e-commerce image creator, I want late responses from another project ignored by the current view, so that switching projects does not corrupt its content.
51. As an e-commerce image creator, I want LLM and VISUAL configuration shown separately, so that I can identify the role that needs attention.
52. As an e-commerce image creator, I want configuration completeness distinguished from real call success, so that the app does not claim an untested connection works.
53. As an e-commerce image creator, I want keys to remain on the backend, so that configuration and error views do not expose credentials.
54. As an e-commerce image creator, I want refresh during an active operation to read existing records, so that it does not submit another message or generation.
55. As an e-commerce image creator, I want an uncertain generation submission shown explicitly, so that I do not mistake a missing receipt for rejection.
56. As an e-commerce image creator, I want manual recovery to reuse the original request ID and parameters, so that recovery refers to the same submission.
57. As an e-commerce image creator, I want invalid or unavailable recovery storage handled visibly, so that missing credentials do not trigger replacement requests.
58. As an e-commerce image creator, I want status polling to avoid overlapping automatic queries, so that waiting does not accumulate requests.
59. As an e-commerce image creator, I want polling paused after three consecutive read failures with an explicit query action, so that connection loss is actionable.
60. As an e-commerce image creator, I want hidden-page and project-switch polling cleaned up, so that client lifecycle changes do not imply server cancellation.
61. As an e-commerce image creator, I want a warning before a new generation after an unknown result, so that I can acknowledge that the previous request may already have been charged.
62. As an e-commerce image creator, I want retrieval of a known existing result separated from new generation, so that recovering a download does not create another image request.
63. As an e-commerce image creator, I want version conflicts to preserve a copy of my local text, so that I can load the latest record without silently overwriting either version.
64. As an e-commerce image creator, I want actionable errors for configuration, authentication, quota, invalid input and missing files, so that I can choose an appropriate next step.
65. As an e-commerce image creator, I want restart recovery to display the backend's actual records, so that interrupted work is not automatically resubmitted.
66. As an e-commerce image creator, I want keyboard access, labels, visible focus and understandable status text, so that every essential action remains accessible.
67. As an e-commerce image creator, I want drawers and image overlays to manage and restore focus, so that opening them does not interrupt keyboard navigation.
68. As an e-commerce image creator, I want readable panels with reduced-motion and blur fallbacks, so that appearance does not prevent use.
69. As an e-commerce image creator, I want real text-only, reference-image and feedback generation validated with downloadable files, so that simulated results are not mistaken for a working product.
70. As an e-commerce image creator, I want integration gaps reported honestly, so that a finished layout is not presented as completed real-model delivery.

## Implementation Decisions

1. **范围与术语。** 实现前端工作台，不承担数据库、服务端任务调度或供应商适配器实现。项目（Project）是一件商品的持续制作记录；生成记录（Generation）包含成功、失败及未知的单次尝试；图片版本指可选择的成功结果；资源（Asset）通过资源标识访问。当前需求与当前预览版本分别维护。

2. **技术与工程边界。** 沿用 Vue 3、TypeScript、Vite、Element Plus、Composition API 与单一工作台 composable；使用集中封装的浏览器 fetch，通过相对 `/api` 请求本机后端。不引入 Router、Pinia，不在组件中连接供应商。应用源码放在 main；测试、配置、文档及其他辅助资料位于 main 之外，工程配置显式指向源码和外置测试位置。

3. **模块职责。** 工作台外壳负责 Head／Side／Body 与抽屉；阶段导航只表达查看意图；对话负责消息、输入和滚动；需求卡负责字段缓冲及保存／整理／确认意图；参考图负责文件检查和临时预览；生成消息负责摘要与恢复入口；结果面板负责版本、大图与下载；项目抽屉负责分页；模型抽屉负责只读诊断。统一工作台状态入口执行读写、互斥、项目切换、查询调度与提交恢复；HTTP 边界集中解析契约与错误。组件通过 props／事件协作，不各自维护确认修订号或自动轮询。

4. **布局。** Head 只显示“电商生图智能体”，呈磨砂质感；Side 包含展开／收缩、新建项目、已有项目和底部模型状态；Body 内显示项目标题、常驻阶段导航及当前真实状态。Body 同一区域切换对话和结果，消息区独立滚动，输入区不随消息滚走；抽屉独立滚动并锁定背景。侧栏选择只在同一断点模式内保留，跨断点应用默认值，不持久化。

   | 视口宽度 | Head 高度 | Side 默认行为 | Body |
   |---|---|---|---|
   | ≥1280px | 64px | 展开 224px；收缩 64px | 内边距 24px；对话最大宽 880px，结果最大宽 1120px |
   | 768–1279px | 64px | 默认收缩，可手动展开 | 内边距 20px，随剩余空间收缩 |
   | <768px | 56px | 默认隐藏；Body 导航左侧打开侧栏抽屉，宽度不超过 272px 和视口 | 内边距 16px，单列 |

   项目抽屉桌面宽 360px，模型抽屉宽 400px，窄屏限制在视口内。窄屏先关闭侧栏再打开目标抽屉。页面无整体横向溢出；阶段导航和版本条可在各自区域横向滚动。

5. **视觉。** 首版只做浅色、暖中性色、留白充分的轻奢 ins 风，Element Plus 统一主题映射。页面背景 `#F4F0E8`，实色面板 `#FFFCF7`，玻璃面板 `rgba(255,252,247,0.84)` 并模糊 16px；主／次文字 `#2C2723`／`#6F665C`；主操作／悬停 `#705239`／`#5B4330` 配白字；边框 `#DED5C7`，装饰色 `#B89A68`。成功／提醒／错误／信息色分别为 `#42664C`／`#7C5A22`／`#A43D35`／`#4F6274`，同时提供文字说明。系统无衬线字体覆盖中文；正文 14/22px、输入 16/24px、区域标题 16/24px、产品名 20/28px；间距 4/8/12/16/24/32px；控件／面板／大图圆角 8/16/24px；阴影为 `0 8px 24px rgba(44,39,35,0.06)`。基础实色样式可独立使用，支持时启用背景模糊；需求卡、输入和图片区域使用清晰面板。图片完整包含显示，透明图配弱棋盘底。

6. **可用性。** 所有操作键盘可达、输入有文字标签；焦点使用 2px 主色轮廓与 2px 间隔，正文对比目标至少 4.5:1。收缩图标有可访问名称及悬停／聚焦提示，禁用原因也可被键盘用户取得。状态用文字、图标和合适的 live region 表达，同一轮询状态不反复播报。抽屉和确认弹窗管理焦点并在关闭后返回触发处，大图支持 Esc；窄屏有足够触摸区域。响应减少动态效果设置。仅展示可取得的阶段和等待信息，不捏造百分比、预计时间、价格或模型能力；图片占位只显示“尚未生成图片”。

7. **阶段导航与默认视图。** “描述需求”始终可查看；“确认需求”在有卡片后解锁，过期卡注明需整理；“生成图片”在有可生成需求或生成记录后解锁，点击定位摘要／状态；“查看结果”在有成功图片版本后解锁，缺失文件显示交付异常。导航不提交生成。打开项目时，整理中、活跃生成或最近失败／未知结果需处理则进入对话定位状态；否则有成功图时进入最新结果，无图进入对话。浏览位置不改变实际进度。

8. **首次进入与创建。** 无项目参数显示空白对话、简短用途说明及少量输入示例，不自动选择上次项目。示例只填输入，新建只进入空白页。首次有效发送或通过本地检查的上传先创建项目，创建期间同步防重；取得 ID 后更新 `?project=<uuid>` 并执行原操作。上传本身不调用 LLM。创建成功但后续失败时复用项目；创建回执不明时保留输入并引导到已有项目核实，不自动再次创建。

9. **对话。** 按时间展示用户原文、LLM 完整回复、可还原的卡片和生成状态；等待显示“正在整理需求…”，不做逐字输出。Enter 换行，Ctrl／⌘ + Enter 发送，组合输入期间不触发发送，纯空白不可发送。用户在底部时跟随新消息；向上阅读时保持位置并显示“有新内容”。历史卡片仅从实际快照还原，不解析普通消息拼造结构。

10. **需求卡。** 可编辑字段为商品描述、图片用途、风格、构图与场景、图片中的文字；固定输出尺寸只读，参考图以缩略图及有无状态呈现，完整提示词只读且首次展示当前有效版本时展开，可折叠。文字为空表示“不要求加字”；未知商品材质、功能、认证、价格保持未指定。仅最新卡可操作，历史卡只读。编辑使用本地缓冲；取消回到服务端卡片，保存携带期望修订号。保存失败保留缓冲；成功显示“需求已更新，请重新整理”，不自动调用 LLM。重新整理由用户单独触发，复用消息接口表达按当前卡片整理。用户看到同版本卡片和提示词后单独确认，确认不自动生成。

11. **版本与草稿门槛。** 服务端返回的需求修订、提示词修订和确认修订为事实来源；不由客户端推算加一。生成要求三者非空且 `revision = prompt_revision = confirmed_revision`。本地字段改变立即阻止确认／生成；未发送文字同样阻止；参考图保存变化使旧提示词与确认失效。确认还要求无必需澄清。整理要求有效输入或已保存需求、LLM 可用且无未保存卡片；生成要求 VISUAL、精确尺寸及当前参考图能力满足条件。禁用显示具体原因，服务端仍作最终判定。

12. **参考图。** 点击选择与拖放单张 PNG/JPEG，不做粘贴上传。应用上限为 SRS 的 10 MB，并取供应商已核实更严格限制；精确字节与未知能力沿接入契约映射，不自行猜定。多文件整批拒绝，本地检查类型、大小和可解码性，无效输入不请求模型、不替换有效图。临时预览注明待上传，成功响应后才是当前参考图；替换／移除失败保留旧图，回执不明先 GET 核实；取消选择无修改。替换、移除、卸载时释放相关对象 URL。无图路径完整可用；不支持参考图时阻止有图生成并提供移除入口；未知能力显示待核实。反馈沿用当前原始参考图，成功图片不自动变为参考图。

13. **双状态与互斥。** 需求状态沿用 `draft / organizing / needs_input / review / confirmed`；生成状态沿用 `queued / running / succeeded / failed / unknown`。客户端的正在保存、提交待核实、连接中断、正在重取结果是提示，不新增服务端枚举。顶部优先显示活跃操作，否则显示当前需求进度，附加最近待处理生成异常。选中旧成功图不掩盖最近失败，旧失败不覆盖当前需求待确认。首次异步等待前设同步互斥；整理、活跃生成、保存、参考图变更、确认、生成提交及提交待核实期间锁定当前项目冲突修改。查看、切换项目和下载旧图保持可用；重取期间也防重入并阻止冲突写操作。

14. **生成摘要与结果。** 生成按钮旁展示用途、完整提示词入口、参考图有无及缩略图、精确尺寸和数量一张。生成仅由独立生成按钮明确触发；成功后刷新项目版本。默认选最新成功版本；仍在当前对话或跟随最新结果时展示新图，主动查看旧图时保留预览并提示“有新结果”，切到别的项目不强行跳回。结果显示版本号、时间及实际可得尺寸，展开查看提交需求快照与只读提示词。查看旧版本不恢复旧需求。继续修改只返回对话聚焦输入，并说明反馈基于当前需求。大图支持适应窗口、原尺寸、超出区域滚动与关闭；下载通过当前所选资源的附件交付，实际编码与扩展名一致。失败保留旧图，文件缺失不显示下载成功。

15. **项目与草稿。** 已有项目按服务端更新时间倒序，展示实际标题，无标题为“未命名项目”，时间转换为浏览器本地时区；有真实状态字段才展示徽标。首次开抽屉取首批，显式加载更多，防重并按项目 ID 去重；后页失败保留已有列表，空态有新建入口。选中后关闭抽屉、更新查询参数、GET 详情。无效链接、资源不存在、服务不可达分别报错，不替换为其他项目。未发送文字和未保存卡片只存页面内存；站内新建、切换及前进／后退放弃草稿前提示“放弃修改／继续编辑”，取消恢复原项目和 URL。只在有草稿时注册离站提醒，浏览器是否提示及提示文字不作保证。

16. **隔离异步上下文。** 加载新项目时停止旧自动查询、清理旧读取，显示加载态；所有读写结果关联发起时项目与请求上下文，旧响应不得覆盖当前项目、预览或模式。取消读取、隐藏页面和项目切换不取消服务端已提交操作；保留写操作的受理核实上下文，重新打开对应项目时读取实际状态。

17. **只读模型状态。** 入口位于 Side 底部，缺配置或本次相关调用失败时提醒；分别展示 LLM 与 VISUAL 的配置完整性、模型、能力、缺项名称和可取得的限制。配置完整不表示认证成功，无证据显示“尚未验证实际调用”；本次真实调用证据仅作为会话提示。可手动刷新只读状态，无网页改 Key、换模型或付费探测入口。提示本机后端环境配置后重启；密钥不进入浏览器状态、构建变量或错误。空配置仍可查看历史与已有图片。

18. **原提交身份与最小恢复凭据。** 每次明确新生成创建 UUID `request_id`，发送前保留原请求到内存，并尝试按项目写入同源 sessionStorage：项目 ID、request_id、原始 confirmed_revision、acknowledge_unknown 是否存在及其值；收到回执可加生成记录 ID。存储读写、解析及格式校验可失败，无效凭据不触发请求。不存完整项目、卡片、图片、Key 或未发送草稿；关闭标签页后不承诺恢复，也不依赖其他标签页共享。凭据可在原记录终态清理。

19. **提交待核实。** 明确受理后关联已有生成记录并读取状态，明确拒绝显示原因；断网、超时或无法判断受理时显示提交待核实，阻止新生成并保留原请求。刷新先 GET 项目及已知记录，响应支持时按原 request_id 精确关联。未查到记录不单独证明原请求失败。无法确认时只允许用户明确“恢复本次提交”，复用完全相同的 ID、确认修订及可选参数的存在性和值，项目或配置变化也不重建原请求。按钮说明原请求若未受理，此操作可能执行该次生成；不同于只读重新查询及新请求再次生成。已知待核实而凭据缺失时从记录核实并说明恢复信息不可用，不自动替换为新请求；普通首次进入无证据时不凭空提示提交异常。不宣称远端绝不重复计费。

20. **自动与手动查询。** 当前项目 organizing 时读项目；存在 queued/running 时读生成状态。可见页面在前次结束约两秒后调度，工作台至多一条自动状态查询在途。成功读取清零连续失败次数；连续三次读取失败停止调度，持久显示连接中断与“重新查询”。HTTP 成功但业务 failed/unknown 是终态，不计读取失败。手动重新查询只发 GET，成功且仍活动才恢复；明确资源不存在直接报资源错误。切项目、卸载、终态、页面隐藏时清理调度和相关读取，主动中止不计失败；重新可见先读取实际状态，但三次失败导致的暂停仍等用户操作，不自行恢复循环。新图成功再读项目同步版本；恢复整理不重发消息。

21. **错误与恢复动作。** 错误留在对应表单、消息或结果区域，通知只补充。LLM 整理失败保留原文与已保存卡片，手动重新整理；普通生成失败由用户再次生成新请求。最近记录 unknown 时提示“上次请求可能已计费”，新生成前弹窗“我已了解并再次生成／返回查看”；取消无写请求，确认后用新 ID 与 `acknowledge_unknown=true` 提交。RESULT_FETCH_FAILED 且契约明确可重取时，用户点击只调用现有结果重取接口、更新原记录，不新建生成或版本；无可用定位信息显示原因。重取响应不明先 GET 核实，不自动重取或生成。

22. **冲突与其他异常。** 409 停止当前修改流程并保留可复制的本地文字／字段；用户主动加载最新记录，或只读查看本地副本，不自动合并或提交过期内容。413/422 定位相关文件／字段并保留输入，不自动缩图、截字或修正后发送。缺配置、认证、额度等显示具体角色及可操作原因；本机文件缺失、项目不存在各自说明。重启后按实际记录展示整理中断／结果未确认，不重发。统一解析安全错误体，无结构化内容时显示通用可理解错误，不输出供应商原响应、认证信息或堆栈。客户端无通用写请求自动重试，无任务取消接口时不提供取消生成按钮。

23. **既定 API 消费边界。** 使用技术设计已有方法及接口类别，不新增后端路径或配置写入接口。消息保存使用当前 expected_revision；卡片与参考图修改携带期望修订，确认指定 revision；生成仅传 request_id、confirmed_revision 及可选 acknowledge_unknown。项目与生成详情供刷新恢复，资源以 ID 预览，附件通过现有 download=true 交付。健康／配置查询不调用付费模型。精确响应、空值和分页由实际 OpenAPI 映射到集中类型，不根据 UI 需要自行添加返回字段。

   | 方法与 API 路径 | 既定用途及输入 |
   |---|---|
   | GET /api/health | 本机服务诊断，不访问外部模型 |
   | GET /api/config/status | 脱敏配置与能力，只读 |
   | POST /api/projects | 首次有效操作创建项目 |
   | GET /api/projects | 按更新时间倒序分页，精确参数待契约核实 |
   | GET /api/projects/{id} | 读取当前需求、对话、参考图及生成记录 |
   | POST /api/projects/{id}/messages | content、expected_revision；等待完整 LLM 回复 |
   | PATCH /api/projects/{id}/brief | expected_revision 与结构化可编辑字段；不自动整理 |
   | POST /api/projects/{id}/confirm | revision；只确认，不生成 |
   | POST /api/projects/{id}/reference | multipart 文件及 expected_revision |
   | DELETE /api/projects/{id}/reference | 查询参数 expected_revision；保留历史依赖 |
   | POST /api/projects/{id}/generations | request_id、confirmed_revision、可选 acknowledge_unknown；新请求受理后返回记录标识 |
   | GET /api/generations/{id} | 读取状态、真实阶段、资源标识及安全错误 |
   | POST /api/generations/{id}/retrieve-result | 用户手动重取原结果，不再次生成 |
   | GET /api/assets/{id} | 资源预览或 download=true 附件下载 |

24. **接入依赖处理。** 后端设计草案提供了候选 DTO、游标分页、历史卡片快照、原 request_id 关联和同步重取等建议，但尚无实现或生成的 OpenAPI。这些建议可供实现者核对，不视为用户逐项确认或已可调用的响应。前端可先以明确标识的受控测试响应实现行为；以下依赖必须在真实接入时核验并记录结果，不能以猜测填补：

   | 编号 | 核验内容 | 未明确时的行为 |
   |---|---|---|
   | FE-DEP-01 | 精确尺寸、供应商字节上限、等待上限及单位、能力字段 | 显示待配置／待核实，缺关键生成条件时阻止生成 |
   | FE-DEP-02 | 列表分页参数、摘要、下一页判定 | 不发明参数；不伪造状态；接通前标明分页未接入 |
   | FE-DEP-03 | 消息与历史卡片快照、生成快照、版本排序、request_id 关联 | 只显示可还原历史；原提交无法匹配时保留凭据与手动恢复入口 |
   | FE-DEP-04 | 生成阶段、错误细分、可重取原因和重取完成方式 | 仅显示已知状态；未证明可重取时禁用相应入口 |
   | FE-DEP-05 | 创建、确认、卡片、上传请求和响应字段、修订号与空值 | 按集中契约映射，不能本地推算修订号或把待响应操作显示成功 |
   | FE-DEP-06 | 配置完整性与实际调用证据来源 | 无证据显示尚未验证，不建立伪持久认证状态 |

25. **交付顺序。** 先完成静态布局、主题、导航与两种 Body 模式；再接入项目、只读配置、参考图和资源；接着完成对话、卡片、确认及操作门槛；随后完成生成、单路查询、最小凭据及异常恢复；最后进行浏览器与真实 API 联调。每步按可观察行为通过后推进。供应商、精确模型协议和真实服务配置仍是实际调用前提；布局完成或受控测试通过不代表真实生图验收通过。

## Testing Decisions

- **已确认的唯一主要自动化测试边界：完整工作台的用户交互与本机 HTTP 边界。** 用 Vitest 挂载工作台入口及真实子组件，通过可访问名称执行点击、输入、文件选择、项目导航等操作；在 HTTP 边界控制响应、延迟、断网和请求记录，同时控制时钟、页面可见性及存储失败。只断言可见内容、焦点、URL、按钮可用性、选中资源和外发请求的种类、次数及原始参数。不直接调用内部 composable 动作，不断言私有标记、组件树或具体定时器实现，不为每个组件新增独立状态测试边界。
- **覆盖模块。** 经这一工作台入口覆盖外壳与导航、对话、需求卡、参考图、生成状态、结果与版本、项目抽屉、模型状态、统一状态管理及 HTTP 错误映射。浏览器不能由 DOM 测试可靠代表的视觉布局、实际文件交付和焦点体验由 Chrome／Edge 检查补足。
- **现有基础。** 仓库没有可复用测试代码或已存在的测试入口；技术设计已选择 Vitest，并要求验证轮询清理、刷新后不重发与错误保留旧图。本规格沿用这些行为与前端 FE-AC-01–22，不假称已有测试套件。
- **好的测试。** 每个场景从用户意图开始，验证界面结果及必要网络副作用。对于“不自动生成”同时断言生成 POST 数为零；原提交恢复验证 ID、confirmed_revision 和 acknowledge_unknown 的存在性及值完全一致；未知结果新生成验证新 ID 与明确确认。用延迟响应验证隔离，用受控时钟验证查询不重叠及三次失败后暂停，不靠真实等待或实现细节测试。替身仅用于测试，不进入正式产品成功流程。
- **验收矩阵。** 保留前端设计的编号作为需求追踪，以下全部必须验证；其中浏览器事实、文件交付和真实链路单独留下实际证据。

| 编号 | 场景 | 外部可观察的通过条件 |
|---|---|---|
| FE-AC-01 | 三种视口与侧栏切换 | Head 只有名称且有磨砂／回退；断点默认值正确，无整体横向溢出；输入和导航可见 |
| FE-AC-02 | 阶段导航和未解锁项 | 仅查看／定位，禁用原因可取得，生成 POST 为零 |
| FE-AC-03 | 无参数、示例、新建、首次提交 | 不自动选历史或创建；示例只填字；有效首操作才创建，失败后复用已知项目 |
| FE-AC-04 | 澄清、卡片编辑、保存、整理、确认 | 历史卡只读；保存不调用 LLM；旧提示词过期；确认不提交生成 |
| FE-AC-05 | 未保存卡片、未发送反馈、参考图变化 | 相应确认／生成被阻止；已保存变化需重新整理与确认 |
| FE-AC-06 | 多图、非法／损坏／过大图、取消和有效图 | 无效选择不调用模型、不替换旧图；有效单图可上传、替换和移除 |
| FE-AC-07 | 快速重复操作、中文输入法与快捷键 | 同项在途操作不重发；组合输入不误发送；Enter 换行 |
| FE-AC-08 | 首图、继续修改、反馈、确认、新图 | 正确切换 Body；原参考图沿用；旧图、新图可选择且各自下载 |
| FE-AC-09 | 查看旧图／切到其他项目时新图完成 | 保留主动预览，不跳回旧项目；相应项目能发现新结果 |
| FE-AC-10 | 生成回执前断网并同标签刷新 | 提交待核实；先 GET，自动生成 POST 为零；手动恢复的原 ID 和参数完全相同 |
| FE-AC-11 | 存储读写失败、无效凭据、关标签再开 | 不凭坏数据发请求；从记录核实并解释恢复限制；不自动创建替代生成 |
| FE-AC-12 | 三次连续查询失败、手动重新查询 | 自动查询暂停且旧图保留；手动只 GET；成功且仍活动再继续；成功读取清零失败计数 |
| FE-AC-13 | 页面隐藏／恢复、快速项目切换、旧读取晚到 | 自动查询至多单路；旧响应不覆盖新项目；主动中止不计失败；暂停不等于取消服务端任务 |
| FE-AC-14 | 整理／生成中刷新或后端重启 | GET 恢复原记录，显示实际活动／中断／未知；自动消息及生成 POST 为零 |
| FE-AC-15 | unknown 后新生成，先取消再确认 | 显示可能已计费；取消无提交；确认后新 ID 且 acknowledge_unknown=true |
| FE-AC-16 | 重取现有结果及无可用定位信息 | 可重取时仅调用原结果重取，防重入，不新建版本或提交生成；失败保留旧图；不可重取时说明原因 |
| FE-AC-17 | 其他标签页更新后产生 409 | 本地输入可复制；加载最新由用户明确选择；无静默覆盖、合并或过期提交 |
| FE-AC-18 | 脏草稿下新建、切项目、前进／后退和离站 | 站内可取消并恢复原项目与 URL；离站提醒只随脏状态注册，不承诺必定弹出或保留草稿 |
| FE-AC-19 | 空配置、认证／额度问题、文件缺失、项目不存在、413/422 | 错误持久且定位明确；输入／已有可读图片保留；无假连接成功、假图片、假下载或自动修正重发 |
| FE-AC-20 | 键盘、抽屉／大图、减少动效、无背景模糊 | 名称、焦点、关闭和焦点返回正确；状态不限颜色；两种背景都清晰可读 |
| FE-AC-21 | 项目为空、多页、后页失败 | 空态能新建；加载更多防重且按 ID 去重；失败保留已加载列表；当前项目高亮 |
| FE-AC-22 | 真实 LLM／VISUAL 的纯文字、有参考图、反馈重生 | 实际生成文件可在浏览器之外打开；参考图确实进入视觉请求；前后版本都可下载 |

- **工程检查。** 实现后通过 TypeScript／Vue 类型检查、Vitest 关键行为检查和生产构建；测试代码、替身及配置放在 main 之外。构建不替代类型检查。浏览器检查覆盖验收时 Windows 上近期 Chrome 与 Edge，并在三种断点范围及 768px、1280px 边界附近验证布局。
- **真实验收门槛。** FE-AC-22 及 SRS 的 AC-01、02、03、07 必须使用实际配置的两类模型验证；记录与文件的刷新／重启恢复按 SRS AC-10–12 联调。受控响应只能证明前端对特定响应的行为，不能证明供应商适配、真实参考图输入、后端持久化或真实文件交付完成。没有真实服务或接口时列出未通过项，不能标记整体真实闭环已完成。

## Out of Scope

- 后端工程、数据库迁移、文件存储实现、任务执行器、供应商协议与适配器；本规格只消费已约定的本机 API，并列出接入前提。
- 网页配置 Key、切换模型、付费连通性测试、持久认证成功记录，以及未经核实的尺寸、时长、成本或能力承诺。
- 账号、支付、多商家、远程部署、多进程持久队列、多智能体编排、批量商品／多 SKU、多平台规则和自动上架。
- 多张参考图、粘贴上传、独立素材库、多张单次输出、多尺寸派生、深色主题及品牌视觉系统。
- 并排对比、裁剪、图层、局部编辑、像素不变保证、自动商品保真、自动合规认证、点击率或销量归因。
- 项目搜索、重命名、删除、批量管理；没有 API 的任务取消；把旧图片版本恢复为当前需求。
- 自动确认、确认后自动生成、失败后自动重新生图、恢复时重发消息、浏览器持久化完整对话／项目／草稿，以及远端 exactly-once 或绝不重复计费保证。

## Further Notes

- 直接依据为《电商生图智能体前端设计 v1》；产品范围与真实闭环验收依据《电商生图智能体 SRS v1》；技术栈、状态及既定 API 依据《电商生图智能体技术设计 v1》。前端设计对常驻左右两栏的细化采用 Head／Side／Body 和两种 Body 模式，不扩展五项核心功能。
- 《电商生图智能体后端设计 v1》明确是草案，其中新增字段、分页及修订分配是实现建议。引用它只说明已有候选映射，不将其提升为已确认事实；实际接入以生成的 OpenAPI 与真实响应核验 FE-DEP-01–06。
- 发布目标为 a13295877929-gif/main，使用 ready-for-agent 标签，无需额外 triage。该标签表示前端行为已具体到可着手实现；不表示后端或真实模型依赖已经可用，也不表示实现及验收已完成。
- 用户已确认采用完整工作台与本机 HTTP 的统一自动化测试边界，并以浏览器检查和真实 API 验收补足布局、键盘体验与实际图片交付。
