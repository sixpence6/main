# R2 一次确认到批准交付最短生成闭环验收记录

- **Issue**：#65 V3 T13 [R2]：一次确认到批准交付的最短生成闭环
- **父 Issue**：#52 V3 规格实施；R2 阶段规范遵循 #51，本票不关闭 #51
- **分支**：ticket/65（基于 integration/v3 5fe67e5）
- **代码提交**：40b7b37fea6ba89853a80c08c7ccbbcf978de0c5
- **验收日期**：2026-10-31
- **状态**：本票 R2 阶段条件通过；全量后端 547 通过、前端 34 文件／207 测试通过、迁移 0001–0008、真实 HTTP 53 项冒烟通过。合并由 integration/v3 执行

## 1. 结论与边界

本票把“用户确认最终需求卡”到“已批准交付可发布”的最短闭环接到 R2 验收缝上：确认动作在单个 BEGIN IMMEDIATE 短事务中重验账号、本人项目、revision、卡片摘要、参考图和尺寸，并原子写入确认、生成意图、使用次数、个人／实例／项目占用和审计；事务提交后才允许后台编制提示词、独立审查和生图。执行阶段消费原意图，不另建用户授权、不另计一次使用；图片质量审查、交付清单审查、人工批准和原子发布继续沿用 v2 门禁。

本票只承接 #65 列出的 R2 阶段条件，不表示整个 R2 或 v3 完成。明确留待后继票：

| 留待项 | 归属 |
|---|---|
| 跨标签、丢失响应、跨请求重放的完整浏览器矩阵；需求漂移返回 U1 的完整 UI | #66 |
| 管理员额度调整接口与审计；窗口边界和降额操作的完整管理路径 | #67 |
| 远端未知核实、原任务恢复、结果找回、进程退出／重启恢复 | #68 |
| 本地跟踪结案与明确风险确认的新任务 | #69 |
| R2 全门禁：独立连接、每个写点故障注入、并发窗口边界、调用次数精确核对 | #70 |
| 真实供应商模型与真实图片服务；本票只使用假 LLM／Visual 适配器 | 真实环境验收阶段；本票不配置密钥 |
| 真实浏览器多标签、刷新、历史返回和可读存储 | #71–#77（R3） |
| 真实旧数据来源、正式备份归档与 Release 全制品扫描 | #78–#85 |

本票没有新增公开 HTTP 出口；确认、生图和交付仍通过既有工作流动作和 v2 交付接口完成。旧独立提交入口在存在 WorkflowRun 时返回 409 LEGACY_FLOW_DISABLED，由统一工作流受理。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t65 |
| 分支／基座 | ticket/65 / integration/v3 5fe67e5 |
| 代码提交 | 40b7b37fea6ba89853a80c08c7ccbbcf978de0c5 |
| 操作系统 | Windows（本地开发环境） |
| Python | 3.12.13（backend/.venv） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 数据库 | 临时 SQLite（aiosqlite，WAL），迁移 0008；未使用真实业务数据 |
| Web | 真实 uvicorn 127.0.0.1:10798，完整 App，临时 DATA_DIR／WEB_DIST_DIR |
| HTTP 客户端 | httpx 0.28.1 |
| 模型适配器 | LLMStub／VisualStub（测试与冒烟）；不是真实供应商 |

## 3. 实现摘要

### 3.1 单事务确认与原子受理

main/backend/app/workflow/stages/s06.py 的 v3 路径在 BEGIN IMMEDIATE 写事务中重新读取 Project、WorkflowRun 和 Artifact，校验：

- 当前账号 active，项目 owner_account_id 等于当前操作者；
- 项目 revision 与请求 expected_revision、需求卡确认的 requirement_revision 一致；
- 卡片存在、属于本项目本 run、状态 approved，artifact_digest 与客户端绑定一致；
- 参考图属于本项目且 kind=reference，reference_usage 与参考图存在性一致；
- 尺寸解析顺序为卡片 data.size、项目 image_size、settings.visual().image_size，且与配置尺寸一致；
- 复用或创建 requirements_card 审批，插入 GenerationIntent、GenerationUsage、三条 GenerationSlot（个人／实例／项目），写 generation_intent_accepted 审计，设置项目 brief_status=confirmed、confirmed_revision 和 run S07 active。

上述写入全成或全回滚。事务内没有模型网络调用；提交后才由 app/generation_cycle.py 调度后台链路。失败时项目确认、审批、意图、次数、占用和审计均不留下半成品。

### 3.2 意图身份、稳定请求与重放

app/acceptance.py 定义 GenerationIntent、GenerationUsage、GenerationSlot。意图绑定：

- 项目、run、owner、operator、request_id；
- 需求 revision、最终卡 artifact_id 与 digest；
- reference_asset_id、reference_usage、image_size；
- 规范化请求摘要 intent_request_digest。

同一项目同一 request_id 的重复提交，只有摘要、卡片、revision、参考和尺寸都一致且意图状态为 accepted／executing／delivered 时返回原意图；参数变化或不可复用状态返回 409 INTENT_REPLAY_CONFLICT。重放不新增意图、不新增使用次数、不重复占槽，也不再次调用模型。每天计数窗口为 UTC 毫秒区间 (now-24h, now]，默认每日 20 次；个人活跃 1、实例并发 2、单项目活跃 1。

### 3.3 后台链路与执行消费

- S07 对已有意图的受管项目校验最新意图的卡片 digest、requirement_revision、状态 accepted／executing；v2 旧路径无意图时保持兼容。
- S08 的 _submit／_create_job 消费意图，写入 Generation／GenerationJob 的 owner、operator、authorization_ref 和 intent_id，使用意图的 request_id；生成结果仍经过 v2 图片质量检查和交付清单审查。
- 图片质量审查终态 rejected／failed 时标记意图 failed 并释放占用（reason=generation_terminal）；unknown／quarantined 保留次数和占用，不伪装成本地终态。
- 交付发布事务把意图置 delivered 并释放占用（reason=delivery_published）；发布仍写已发布文件和公开字节，质量、批准、清单和原子发布门禁不变。
- 浏览器确认动作只产生确认和受理；前端显示本人用量与限制，不展示虚构的最终提示词。后台链路失败不会自动重发或伪造发布。

### 3.4 配置、迁移与兼容

config.py 新增 generation_daily_limit=20、generation_personal_slots=1、generation_instance_slots=2、generation_project_slots=1，均为正整数；visual_image_size 默认 3840x2160。迁移 0008_r2_generation_acceptance 在 main/backend/migrations/versions 与 backend/migrations/versions 两棵树逐字节一致（SHA256 4734c19f...），新增 generation_intents、generation_usage、generation_slots 及 generations／generation_jobs 的 intent_id 列。v2 继承测试通过测试夹具显式写入 intent，不改变真实旧数据。

## 4. 执行命令与结果

### 4.1 后端目标测试

在 D:/Desktop/新建文件夹/.worktrees/t65/backend 执行：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t65-target-basetemp-ref2 tests/test_t65_generation_cycle.py

结果：**5 passed in 7.07s**，exit 0。覆盖确认原子写入、同请求重放绕过额度且不重复计数、完整链路到 S10 并批准发布、同标识变参冲突、额度拒绝与项目占用拒绝无残留、外部参考图拒绝、旧入口关闭、质量终态释放占用。

### 4.2 全量后端回归

非 v2 全部测试（31 个文件）：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t65-full-nonv2-basetemp-final2 tests/test_openai_chat_adapter.py tests/test_openai_images_adapter.py tests/test_t*.py

结果：**312 passed, 2 warnings in 324.16s**，exit 0。

继承 v2 全部测试：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t65-full-v2-basetemp-final2 tests/test_v2_*.py

结果：**235 passed, 1 warning in 315.58s**，exit 0。合计 **547 passed**，无失败、无 skip、无 unknown。

### 4.3 前端回归与构建

在工作树根（不是 main/）执行：

    npm test -- --reporter=dot
    npm run typecheck
    npm run build

结果：**34 个测试文件／207 测试通过（18.26s）**；vue-tsc --noEmit exit 0；Vite 8.3.5 构建成功，1662 模块，659ms，dist/index.html 0.47 kB、index-CoQKJWll.css 412.64 kB、index-DdOyXBUd.js 1,177.40 kB。仅有 chunk 大小提示与 fireEvent.change 警告。

### 4.4 迁移

在临时数据目录执行 fresh 0001→0008 和 0007→0008 链：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m alembic -c D:/Desktop/新建文件夹/.worktrees/t65/backend/alembic.ini upgrade head

结果：fresh 链 alembic_version=0008、25 张表、generation_intents／generation_slots／generation_usage 存在、PRAGMA integrity_check=ok；chain 链保留 0007 的项目行 p-0007（revision 3），升级后 alembic_version=0008，generations 新增 intent_id VARCHAR(36)，integrity_check=ok。证据：.scratch/t65-migration-final。

### 4.5 真实 HTTP 冒烟

在临时 SQLite、完整 App 和真实 uvicorn 进程上运行 .scratch/t65-smoke/smoke.py，假 LLM／Visual 适配器：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t65-smoke/smoke.py

结果：**{"ok": true, "checks": 53}**，基址 http://127.0.0.1:10798。覆盖：一次确认写 S07／usage=1／intent accepted／无模型调用；同请求重放不重复计数；旧 /confirm 与 /generations 返回 409 LEGACY_FLOW_DISABLED；第二个项目 429 GENERATION_SLOT_BUSY；放行后角色顺序 generation_engineer→output_reviewer→quality_compliance→output_reviewer→delivery_manager→output_reviewer；仅 1 次 Visual submit；S10 needs_input；人工 approve／publish；精确 PNG 字节；数据库 1 个 delivered intent、1 条 usage、0 个活跃 slot、1 个 published delivery。

### 4.6 静态检查

新增及核心改动文件 Ruff check 全部通过。main/backend/app/workflow/tables.py 与 backend/tests/test_v2_t11_generation.py 仍存在基线 E501／未使用导入，均非本票引入；本票新增行已按 100 列限制书写。git diff --check 干净。

## 5. 验收覆盖表

| 验收项 | 本票证据 | 状态 |
|---|---|---|
| V3-AC-19 生成中退出／过期保留原任务归属；禁用阻止下一调用与发布，不伪称取消，恢复不自动调用 | S08 owner/operator/authorization_ref 持久化；#62 禁用守卫仍生效；本票不另建第二授权 | 本阶段条件通过；完整禁用矩阵留 #70／Release |
| V3-AC-24 一次明确主操作绑定真实账号与需求快照；确认后才编制／审查提示词；批准前无生图；不展示虚构最终提示词 | s06 BEGIN IMMEDIATE 原子确认；S07 先于 S08；冒烟确认阶段 visual submit=0；UI 仅显示需求卡确认和用量 | 通过 |
| V3-AC-25 篡改 revision／卡片摘要／参考归属／尺寸或门禁未满足时不受理；改需求回 U1 重新确认 | 目标测试外部参考 409 REQUIREMENTS_CHANGED；同标识变尺寸 409 INTENT_REPLAY_CONFLICT；S06 逐项重验 | 通过 |
| V3-AC-26 双击、丢响应、跨标签并发同请求：一个意图、一次计数、至多一次生图；同标识变参拒绝 | 同请求重放测试与冒烟；generation_engineer 等角色只调用一次；Visual submit=1 | 通过；完整跨连接矩阵留 #66／#70 |
| V3-AC-28 确认失败、提示词失败、远端未知、审核不可用、文件丢失保留真实状态；无自动重生／假发布／未审预览 | 确认失败无残留；S08 异常路径不伪造终态；S09 终态才释放；S10 原子发布 | 本阶段条件通过；远端未知恢复留 #68 |
| V3-AC-29 默认单图与第 12 节尺寸；旧快照尺寸不静默更新；反馈新版本不覆盖旧输出／原快照 | visual_image_size 默认 3840x2160；S06 尺寸解析与校验；v2 版本／快照测试全绿 | 通过 |
| V3-AC-30 不同任务竞争个人／实例槽位及窗口边界；原子限制有效，管理员无豁免，降额不取消已受理／不清零 | acceptance.py 三类活跃槽与每日 20 次；真实冒烟第二项目 429；配置无管理员豁免 | 本阶段条件通过；窗口边界与管理调整留 #67／#70 |
| V3-AC-31 提交前拒绝不计数；受理后失败／未知计数；核实／恢复／找回／幂等重发不另计；新任务另计；受限不排队 | 额度／槽拒绝无 intent／usage／slot；同请求重放不增计；无隐藏队列／自动重提 | 本阶段条件通过；恢复与结案留 #68／#69 |
| V3-AC-32 受理后崩溃／重启／恢复不清零、不穿透限制、不重发生图；未知不自动解释为未受理 | 意图／次数／槽持久化；崩溃恢复路径不自动调度；S08 不自动重发 | 本阶段条件通过；进程退出与重启实测留 #68／#70 |
| V3-AC-39 首次输入前告知模型数据流，不发送认证秘密，继承 v2 最小持久化且不承诺零保留 | 既有 DescribePage 数据流提示仍生效；#60 已验收；本票未改变模型入口文案 | 继承通过；Release 全制品复核 |
| V3-AC-21 响应／前端／业务库／日志／文件名／备份不泄漏认证秘密／模型密钥 | #64 秘密标记扫描覆盖既有出口；本票未新增公开出口，意图不存密钥 | 继承通过；Release 扫描 |

## 6. 证据文件

- .scratch/t65-target-ref2.log（5 passed，7.07s）
- .scratch/t65-full-nonv2-final2.log（312 passed，324.16s）
- .scratch/t65-full-v2-final2.log（235 passed，315.58s）
- .scratch/t65-frontend-test-final2.log（34 files／207 tests）
- .scratch/t65-frontend-typecheck-final2.log（exit 0）
- .scratch/t65-frontend-build-final2.log（Vite build 659ms）
- .scratch/t65-migration-final/{fresh-data,chain-data,fresh.log,chain-0007.log,chain-0008.log,verify-fresh.log,verify-0008.log}
- .scratch/t65-smoke/result.json（53 checks，ok=true）与 .scratch/t65-smoke-rerun.log
- 代码提交：40b7b37fea6ba89853a80c08c7ccbbcf978de0c5

## 7. 残余风险

1. 本票使用假 LLM／Visual 适配器；真实供应商的限流、超时、远端未知语义和真实 3840×2160 成本未在本票验证，属于真实环境验收。
2. 完整跨标签／丢响应／跨连接竞争矩阵在 #66／#70；本票只证明同请求重放和单进程后台链路。
3. 远端未知、进程退出、重启恢复和本地结案在 #68／#69；当前实现保守保留次数和占用，不自动重发。
4. 管理员额度调整接口和窗口边界管理留 #67；本票只提供部署配置。
5. 未触碰 backend/data、main/backend/data 或任何未授权真实来源；全部数据在 .scratch 临时目录。
