# P4 / T14 — 本人标题搜索、查询绑定游标与持久改名的服务端证据（#100）

- 票：**#100（T14，P4）**；父问题 **#86**；批次 P4
- 集成分支：**integration/v3**；运行基线 **b304649**（T08 证据合并后）；本票代码提交 **e7bef04**；证据提交见本文件所在提交
- 规则目录版本：**RULESET_VERSION = v3.rules.1**；提示词版本 **2.0.0**；迁移头 **0011**
- 环境：隔离临时 SQLite、真实 ASGI/HTTP 路由、无真实 provider、无图片生成
- 本票代码文件：**main/backend/app/services/projects.py**（所有者过滤、标题查询归一化、查询/所有者绑定游标、持久改名、当前版本状态）、**main/backend/app/routers/projects.py**（GET q 与 PATCH 入口）、**main/backend/app/schemas.py**（UpdateProjectRequest）、**main/src/api/client.ts**（ListProjectsParams.q 与 renameProject）、**backend/tests/test_v3_p4_t14_project_search.py**（新增 7 项）
- 证据 JSON：**docs/acceptance/evidence/p4-t14-project-search.json**

## 1. 结论与交付内容

本票把项目列表从“前端对当前页过滤”改为**服务端先按 owner 与标题过滤，再分页**；游标内绑定规范化的查询词和所有者，切换查询或篡改所有者会被明确拒绝。项目标题通过 **PATCH /api/projects/{project_id}** 持久化，仅更新 title 与 updated_at，不触碰需求版本、最终 brief 摘要、审批、生成意图、候选预算、额度或槽位。首条消息的确定性命名继续使用既有 **messages._collapsed_title**，不引入模型命名。列表的 **latest_generation_status** 只采用与当前 **project.revision** 相同的最近生成版本；旧成功图不会把项目标记为当前完成。

实现要点：

- **services/projects.py**：**normalize_project_query** 合并空白并限制 120 字符；**normalize_project_title** 拒绝空白标题和超过 120 字符；**_escape_like** 转义 LIKE 特殊字符；**ProjectCursor** 编码 **id/owner/q/updated_at** 四元组；**list_projects** 在 SQL 层先过滤 owner 与标题，再按 updated_at/id 排序分页；**_latest_generation_statuses** 以 version_no 取最新生成，仅当 confirmed_revision == project.revision 时返回状态，否则 **unknown**；**rename_project** 在写事务中重新核验 principal 与 owner，只修改标题和 updated_at。
- **routers/projects.py**：GET 增加 **q**（长度上限 120）并传入服务；PATCH 返回 ProjectView，身份、归属或 CSRF 失败分别走既有 401/403/404/422。
- **schemas.py**：**UpdateProjectRequest** 使用 **extra='forbid'** 与 1–120 字符字段约束，未知字段被拒绝。
- **main/src/api/client.ts**：**ListProjectsParams.q** 同步到查询串，新增 **api.renameProject(projectId, title)**；**npm run typecheck** 通过。

## 2. 验收覆盖（票面八项）

| 票面验收 | 覆盖与结果 |
| --- | --- |
| 1. 本人标题搜索在服务端先做所有者和标题过滤再分页，能命中尚未加载的页；不以当前页前端过滤冒充全量，不搜索消息正文 | **test_search_finds_project_on_unloaded_page**：22 个项目，目标强制为较旧 updated_at，普通 limit=20 第一页不含目标，q=Project 03 仍能命中；**test_search_normalizes_whitespace_and_never_reads_message_body**：q='  ALPHA   BETA ' 只命中标题 Alpha Beta，消息正文含 secretneedle 时 q=secretneedle 返回空，q=空白 走正常列表 |
| 2. 定稿端点、有限标题长度、查询归一化和游标契约并同步客户端类型；空查询走正常列表，无匹配返回空结果，切查询重置分页 | **UpdateProjectRequest** 1–120 字符、extra=forbid；GET q 上限 120；空/纯空白 q 返回正常列表；无匹配返回空数组；切换查询时旧游标被拒绝；client.ts 已同步 **q** 与 **renameProject**，vue-tsc 无诊断 |
| 3. 游标与查询及所有者绑定，不匹配明确拒绝；跨账号及越权查询无项目、标题或状态泄漏 | **test_cursor_is_bound_to_query_and_owner**：同一游标换 q=beta 返回 422 **INVALID_CURSOR**；用 encode_cursor 伪造 owner 后同样 422；**test_admin_search_and_rename_stay_inside_owner_boundary**：项目改属另一账号后，管理员列表/搜索不返回，PATCH 返回 404 **PROJECT_NOT_FOUND** |
| 4. 重命名核验身份、归属、CSRF 和标题，只更新持久化元数据，不改变需求版本、确认摘要、意图、候选预算、额度或槽位 | **test_rename_validation_and_csrf**：空白、121 字符、缺失字段为 422 **VALIDATION_ERROR**，缺 CSRF 为 403；**test_rename_is_metadata_only_and_preserves_workflow_facts**：创建已批准 final_brief、requirements_card、RevisionBudget used=1 和 accepted GenerationIntent 后 PATCH，项目需求字段、artifact digest/payload/revision_no、approval checks/摘要、intent 状态/摘要/approval_id、预算 used/max 全部不变，仅 title/updated_at 改变，stub 模型调用为 0 |
| 5. 首条确定性命名与显式改名具备真实持久化入口，不引入模型命名调用，也不通过伪造浏览器状态表示保存成功 | 既有 **messages._collapsed_title** 仍是首条消息的确定性命名路径；新增 **PATCH /api/projects/{id}** 返回从持久化 Project 读取的 ProjectView，测试通过响应与数据库双重核对；**renameProject** 只发 PATCH，不伪造前端状态；全程无模型命名调用 |
| 6. 列表状态取当前版本／任务事实，旧成功图不标记当前完成；无可靠事实保留待读取／未知 | **test_list_status_uses_current_confirmed_revision_only**：project.revision=1 时，version 1 succeeded 但 confirmed_revision=0 返回 **unknown**；version 2 running 且 confirmed_revision=1 返回 **running** |
| 7. 沿 HTTP 请求和临时 SQLite 验证搜索分页、越权、游标错误、改名成功／校验失败／CSRF 拒绝，断言修改前后摘要、意图与模型调用次数，记录 FE3-AC-10 服务端证据 | 7 项测试全部走真实 HTTP 应用工厂与临时 SQLite；覆盖搜索分页、消息不参与搜索、游标 query/owner 篡改、跨账号隔离、改名成功/空白/超长/缺 CSRF、摘要/意图/预算/模型调用不变；FE3-AC-10 的服务端契约（本人搜索、空结果、游标绑定、当前状态、持久改名）均有对应用例 |
| 8. 实现及取证使用既有测试 seam；测试、临时文件和验收证据放业务目录外；列明当前基线、实际命令／结果、调用／计数对照及残余未知 | 复用 **tests.conftest**、**tests.support.auth** 与 **tests.test_v2_t40_prompt_review.setup_confirmed_project**；测试仅在 **backend/tests**，证据仅在 **docs/acceptance**，未在 **main/** 放测试脚手架或临时证据；本文与 JSON 记录基线、命令、调用计数和残余未知 |

## 3. 验证记录

以下命令均在工作树 **D:\Desktop\新建文件夹\.worktrees\t100\backend**，使用 **D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe**；前端类型检查在 **D:\Desktop\新建文件夹\.worktrees\t100** 根目录执行。

| 命令 / 范围 | 结果 |
| --- | --- |
| **ruff check**（projects service/router/schemas + focused 测试） | **All checks passed!** |
| **pytest -q tests/test_v3_p4_t14_project_search.py tests/test_t02_projects.py -x** | **23 passed in 32.39s** |
| **pytest -q tests/test_v3_p4_t14_project_search.py** | **7 passed in 9.27s** |
| **npm run typecheck**（vue-tsc 3.3.13） | 无诊断输出 |

### 3.1 调用 / 计数对照

| 场景 | 模型调用 | 候选预算 used | 意图/审批 | 结果 |
| --- | ---: | ---: | --- | --- |
| 22 项目搜索分页 | 0 | 未创建 | 无 | 目标在未加载页仍命中 |
| 空白/消息正文搜索 | 0 | 未创建 | 无 | 只搜索标题，消息正文不命中 |
| 游标 query/owner 篡改 | 0 | 未创建 | 无 | 均 422 INVALID_CURSOR |
| 跨账号列表/搜索/改名 | 0 | 未创建 | 无 | 无泄漏，PATCH 404 |
| 改名校验/CSRF | 0 | 未创建 | 无 | 422/403，不落库 |
| 改名元数据不变 | **0** | **1（保持）** | final_brief + requirements_card + accepted intent 保持 | 仅 title/updated_at 变化 |
| 当前状态聚合 | 0 | 未创建 | 无 | 旧成功 unknown；当前 running |

**合计**：model_text = **0**，model_vision = **0**，image_generation = **0**，visual submit = **0**。T14 全部为数据库/HTTP 路径，没有 provider 调用。

## 4. 残余未知与边界

1. **没有单次全量 pytest 汇总**：本票执行 focused 7 项与既有 project 回归 16 项（合计 23 项）；T07 记录的全量超时仍未解决，不能声称全量套件通过。
2. **SQLite lower() 的 ASCII 折叠**：当前实现用 SQLite lower() 做大小写不敏感匹配，非 ASCII 大小写变体不承诺折叠；中文标题在空白归一化后按原文匹配，符合本票场景。
3. **浏览器交互属于 T20/T22**：本票只交付服务端契约与客户端 API 类型；项目抽屉中的搜索框、改名交互、当前状态展示和浏览器级可访问性不在本票取证范围。
4. **无真实 provider**：列表、搜索、改名均为本地数据库路径，不存在 provider、TLS、图片或真实网络调用。

## 5. 下一入口

T14 服务端搜索、游标、改名与状态证据完成后，P4 继续 **T13（#99 安全原因/动作投影）**，并与 T15/T20 的前端契约衔接。本文件不构成 v3 发布或全量通过声明。
