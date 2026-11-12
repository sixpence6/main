# v3 架构评审：证据索引

用途：给 [改造方案](v3-refactor-plan.md) 的 R1—R3 提供可回查事实；这是 2026-10-31、`99c3fcd` 加工作区变更的快照，不是当前实现状态的永久副本。代码改变后重新定位符号。路径以仓库根为准，行号从 1 开始。

## 基线与取样

- 需求：`vesion/v3/SRS_v3.md`，重点第 5、7—12 节；保留该文指向的 v2 审查契约与 4K 修订。
- 方法：主查需求、身份与项目／资源出口、数据布局；两个只读侧查分别覆盖生成／恢复与前端状态。未运行真实模型、未查询业务数据库正文、未备份或迁移实际数据。
- 近期取样：最近 60 个非合并提交的路径频次中，`useWorkspace.ts` 11 次、`WorkflowPanel.vue` 10 次、后端 `main.py` 9 次、`services/generations.py` 8 次、`routers/workflow.py` 7 次。热度用于选择观察方向，不代表文件越大越应拆分。
- 评审开始已有变更：`.gitignore`、`backend/migrations/env.py`、`backend/tests/conftest.py`、`main/backend/app/config.py`；另有未跟踪 v3、独立打包与其他文件。全部保留，本轮不采纳或撤销这些工作。

## E1：当前身份与项目装载

| 源码锚点 | 直接事实 |
|---|---|
| `main/backend/app/models.py:19—65`，`Project` | 有项目、revision、参考图与确认字段，无账号所有者字段 |
| `main/backend/app/services/projects.py:114—162`，`list_projects` | 直接选择项目后排序／分页，无主体过滤条件 |
| 同文件 `174—225`，`get_project_detail` / `require_project` | 按 ID 装载项目；`require_project` 仅确认存在；详情继续读取消息、生成与资源 |
| `main/backend/app/main.py:154—161` | 直接注册项目、消息、需求、资源、生成与工作流入口 |
| `main/backend/app/routers/projects.py:35—68` | 创建／列表／详情路径传递 session factory 或项目 ID，没有服务器认证主体 |

**判断：**这些是 v2 单用户实现事实；不是声称它们已经宣告支持 v3。新增登录页不能自动使现有业务路径完成对象归属保护。

## E2：已有内容门禁必须保留

| 源码锚点 | 直接事实 |
|---|---|
| `main/backend/app/workflow/access.py:33`，`ProjectAccessPolicy` | 管理旧内容隔离与输出可见性，不是账号权限主体 |
| 同文件 `263—375`，`_v2_access` | 检查任务、交付、批准与不可变关联；供应商成功本身不表示可访问 |
| 同文件 `386—465`，`can_access_asset` | 拒绝旧未审／驳回／未批准图，校验摘要及批准，下载要求已发布交付；参考图也需在外部增加本人归属判断 |
| `main/backend/app/routers/assets.py:217—247`，`read_asset` | 按资产 ID 读取，执行到期及内容门禁，然后返回文件；未验证账号所有者 |

**判断：**深化的是前置身份／项目范围与门禁组合，不是删除现有内容 implementation。本人待审与非本人对象必须有各自正确结果，不能一个统一 404 掩盖所有业务状态。

## E3：实际出口不只有项目详情

- `main/backend/app/routers/generations.py:70—88` 直接按 generation ID 返回状态与找回信息；`100` 起有原结果找回动作。
- `main/backend/app/routers/workflow.py:21、36` 分别读取工作流和执行动作；`briefs.py:22、59`、`messages.py:25` 与 `assets.py:114、178` 另有写入口。
- `main/backend/app/routers/config_status.py:11` 暴露现有配置状态；v3 需要区分普通可用性视图与管理诊断。
- `main/backend/app/routers/web.py:26—53` 已限制构建目录路径；这不是私有图片鉴权。必须维持构建资源与业务文件分离。

上列是取样证据，不冒充完整出口覆盖清单。实施 R1 时从真实注册路由、响应嵌套字段、直接 ID 和资源字节生成覆盖矩阵；旧写入口要么受保护后保留，要么明确关闭，不能留下匿名兼容旁路。

## E4：v3 受理时机与现有 S06—S08 错位

| 源码锚点 | 直接事实 |
|---|---|
| `main/backend/app/workflow/stages/s06.py:343—435`，`_confirm` | 保存卡片确认并推进 S07；用户标识仍为逻辑字符串，未建立 v3 受理意图 |
| `main/backend/app/workflow/stages/s07.py:259—342、379`，`_compile` | 先调用提示词编制及独立审查，再进入 S08 |
| `main/backend/app/workflow/stages/s08.py:101—122`，`_load_prompt` | 执行入口要求已批准提示词；不能在最初确认时假定它已存在 |
| 同文件 `585—605`，`_request_id` / `_ack_request_id` | 执行幂等身份绑定提示词产物等字段 |
| 同文件 `630—650`，`_submit` | 提示词、配置和参考校验之后才寻找／创建生图任务 |
| 同文件 `204—298`，`_create_job` | 已在一个事务中关联 Generation、GenerationJob 与风险确认事件，可保留这一能力 |

**推断：**若在原 S08 才做 v3 受理，S07 失败前没有稳定意图与计数；与 `V3-GEN-01—03`、`V3-LIM-02` 不符。S08 应变成原意图的执行步骤，不是替代最初受理。

## E5：受理、执行尝试与恢复不是一个事实

- `main/backend/app/workflow/stages/s08.py:437—468` 在 `execute_generation` 前标记 job 为 submitted／已尝试；`main/backend/app/services/generations.py:1094—1104` 进入 semaphore 后再 claim，实际供应商提交在 `1012` 附近。
- `main/backend/app/services/recovery.py:133—167` 保守把部分遗留任务归为未知；`197—202` 明确只修复状态，不自动调用模型。
- `main/backend/app/workflow/stages/s11_retrieval.py:199—300` 找回原结果并隔离保存，不执行新的生图。

**推断，未专项复现：**等待执行时崩溃可能留下保守的未知分类；这不直接构成现有违规。它证明新受理事实不能仅靠 submitted 名称或内存锁推导，也不能因超时自动退次数／重发。

## E6：可以复用的一致性能力

- `main/backend/app/db.py:49—61`：`write_transaction` 用 `BEGIN IMMEDIATE` 包围短写事务；`76` 起的 `ProjectLocks` 是进程内早拒绝机制。
- `main/backend/app/main.py:74`：当前执行 semaphore 为 1；它不是跨重启持久化的用户／实例受理槽位。
- `main/backend/app/models.py:164—183`：Generation 已有项目版本、请求标识唯一约束与单项目活跃约束。
- `main/backend/app/services/generations.py:177—188`：原有 canonical 参数冲突检查；不因此重新开放被 v2 暂停的旧写入口。
- `main/backend/app/workflow/state.py:433—512`：`add_approval` 支持现有 session，有组合确认与受理事务的基础。
- `main/backend/app/workflow/tables.py:126—136`：`RevisionBudget` 是内容候选预算，不是 v3 用户生成额度。
- `main/backend/app/services/retention.py:39—50、515—527`：已有确认／恢复／风险证据的保留规则。新受理记录不能按普通诊断日志清除。

## E7：前端状态并非全局单例，已有隔离值得复用

- `main/src/layouts/WorkspaceLayout.vue:16` 与 `main/src/composables/useWorkspace.ts:3111—3120`：layout 调用 provide 时创建工作台，其他调用者 inject；缺的是账号生命周期，不是消灭一个不存在的模块级单例。
- `useWorkspace.ts:76—210` 的 `WorkspaceStore` 暴露布局、项目、草稿、生成、恢复和导航；`components/workflow/WorkflowPanel.vue:207—267、352—394` 又计算确认、可见动作和提交内容。
- `composables/useWorkflow.ts:21—85` 已有取消、序列、项目匹配、防重入和冲突后只读刷新。删除它会让复杂度回流，不是仅做转发。
- `useWorkspace.ts:778—794、2435—2465` 通过项目 context / sequence 过滤迟到响应；v3 要扩展到账号生命周期，不能只依赖 abort。

## E8：恢复元数据需要账号归属，不是改成存登录令牌

- `useWorkspace.ts:1145—1148、1204—1230` 使用 `sessionStorage` 项目键 `ecommerce-generation-pending:<projectId>`，仍兼容无项目后缀旧键；数据含稳定请求和修订等恢复信息，无账号绑定。
- `useWorkspace.ts:1063—1099` 恢复时先查询原记录，不自动提交；这是应保留的语义。
- `useWorkspace.ts:1115—1119` 当前把 401／403 解释为模型配置问题；v3 应区分应用会话失效、权限拒绝与供应商故障。

## E9：历史预览不应决定当前创作状态

- `useWorkspace.ts:434—439` 的历史选择只切预览并 pin，不改需求，这是可复用行为。
- `useWorkspace.ts:1031—1040` 的旧默认阶段会因任一历史成功图进入 results；不能直接作为“当前 revision 已发布”的 U3 判据。
- `tests/support/workspace.ts:35` 以真实 App 配合 HTTP／时间／可见性／存储替身测试，适合作为继续验证投影与生命周期的 seam。

## E10：两个布局的数据来源需要实际核验

- `main/backend/app/config.py:10—24` 按仓库 workspace 标记兼容仓库与独立包布局，决定 backend、默认数据根与环境文件；`startup.py:12—23`、`backend/migrations/env.py:13—19` 与测试导入也涉及布局。
- 仅检查文件元数据：`backend/data/` 存在 `app.db`、WAL／SHM 与图片目录；`main/backend/data/` 看到 `.gitkeep`。**这不是对运行实例实际 data_dir 的认定。** 环境覆盖、启动方式和挂载必须在迁移前核实。
- 无论何种布局，直接复制正在写入的 `app.db` 都不能代表数据库与文件的一致性备份。SQLite 数据库快照能力也不自动保证外部图片集合一致。

## 测试保留索引

| 现有测试 | 保留的行为与迁移关注 |
|---|---|
| `backend/tests/test_v2_t01_legacy.py`、`test_v2_t49_legacy_review.py` | 旧资产隔离与按对象审查；加所有者不能变成全部放行 |
| `backend/tests/test_v2_t13_delivery.py` | 审批、摘要、原子发布、撤销与快照；在其前方增加身份／归属 |
| `backend/tests/test_v2_t09_confirmation.py:179`、`test_v2_t40_prompt_review.py:316` | 确认绑定与确认后编制；旧分步入口的“不编制”断言留在对应子步骤，不机械约束 v3 一次主操作 |
| `backend/tests/test_v2_t11_generation.py:324`、`test_v2_t16_unknown_recovery.py:465` | 幂等、只读刷新、风险确认；增加意图、计数与账号关联 |
| `backend/tests/test_v2_t17_restart_recovery.py:972、1033` | 提交临界点与可证未提交的崩溃；增加受理后尚未执行的崩溃 |
| `backend/tests/test_v2_t48_retrieval.py:96`、`test_v2_t20_retention.py:237` | 找回不新生图，证据不按诊断清除；新增原意图不重复计数 |
| `tests/t07-navigation-conflict.spec.ts:266、311`、`v2-workflow-race.spec.ts:46` | 迟到响应不回填新项目；扩 A→B→A 与同账号新生命周期，包括错误和结束回调 |
| `tests/t09-uncertain-submit-recovery.spec.ts:157—233`、`t08-version-browsing.spec.ts:175、284` | 刷新只查询、恢复元数据最小化、历史不写入；加账号隔离与当前 U1＋旧图 |

### 本轮实际执行

以下命令在仓库根运行，测试使用隔离临时目录和 stub，不调用真实模型：

```powershell
uv run --project backend python -m pytest -c backend/pyproject.toml backend/tests/test_v2_t13_delivery.py backend/tests/test_v2_t49_legacy_review.py -q -p no:cacheprovider
# 27 passed in 12.42s

npm test -- --run tests/v2-workflow-race.spec.ts tests/v2-t09-confirmation.spec.ts tests/v2-t44-version-history.spec.ts
# 3 files / 8 tests passed

npm run typecheck
# exit 0
```

未运行全量回归、真实 4K 模型调用、v3 权限／并发／会话验证或局域网 HTTPS 浏览器验收；上述通过不能外推为 v3 安全、尺寸或部署已经合格。

## 官方原始资料核验

访问日期：2026-10-31。仅用于核验下列工程前提，不变更已确认的产品范围。

| 原始资料 | 对方案的支持 |
|---|---|
| OWASP Authorization Cheat Sheet：`https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html` | 默认拒绝与每次请求校验，包括对象和静态资源；支持 R1 全出口而非仅前端过滤 |
| OWASP Session Management Cheat Sheet：`https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html` | 服务端执行空闲／绝对时限与会话失效；支持账号生命周期与请求级保护 |
| SQLite Transaction：`https://sqlite.org/lang_transaction.html` | IMMEDIATE 预先建立写事务，也可能遇到忙错误；支持短事务竞争测试，不表示自动解决业务计数 |
| SQLite Online Backup：`https://sqlite.org/backup.html` | 数据库可获得一致快照；外部文件清单与业务写入仍须另行协调 |
