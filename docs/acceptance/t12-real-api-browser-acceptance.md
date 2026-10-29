# T12 真实 API 联调与浏览器完整闭环验收记录

- **Issue**：GitHub #13《真实 API 联调与浏览器完整闭环验收》
- **前端基线分支**：`integration/frontend-v1`；后端 handoff：`integration/backend-v1`
- **核验基准备份**：`3ce5c75`（前端 T11 完成提交）
- **记录日期**：初记 2026-10-29；后端 T15 handoff 更新 2026-10-30（Asia/Shanghai）
- **结论**：**浏览器验收仍阻塞，不能关闭 #13**。真实后端、OpenAPI、LLM/VISUAL 配置与持久化证据已由 T15 提供；FE-DEP-01–06 契约/映射核验通过。Chrome 缺失且 Chrome/Edge 人工场景未执行，FE-AC-01–22 仍全部未验证，整体 v1 不能标记完成。

> 本文件只把后端真实证据作为浏览器验收前提，不把受控测试、真实 API 响应或布局检查替代 Chrome/Edge 人工验收。本文件不包含任何 API Key、供应商原始响应或敏感配置。
## 1. 外部前提与当前状态

| 前提 | 状态 | 证据 |
|---|---|---|
| 可运行的本机后端 | **具备** | `main/backend/app` + `backend/.venv`；迁移 `0001 (head)`；169 后端测试通过 |
| 本机前端 API 服务 | **具备** | uvicorn `127.0.0.1:8720`；`GET /api/health` 200；14 个 API 方法可调用 |
| 真实 OpenAPI | **具备** | 12 paths / 14 operations；`docs/acceptance/evidence/openapi-v1.json`；T15 contract 5 tests |
| 真实 LLM 配置 | **具备** | `openai-chat` / `deepseek-v4.1-flash`；真实澄清、review、反馈整理已执行 |
| 真实 VISUAL 配置 | **具备** | `openai-images` / `gpt-image-2`；真实纯文字 v1/v2 与参考图 v3 已生成 |
| 真实持久记录与重启 | **具备** | v1/v2/v3 重启后可读；interrupted → unknown 不重发；见 T15 |
| Windows Chrome | **缺失** | 仍待安装或由人工在具备 Chrome 的环境执行 |
| Windows Edge | **具备但未执行完整验收** | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`；需按 §6 执行真实浏览器场景 |

## 2. 自动化回归证据（非真实验收）

以下命令在 `D:\Desktop\新建文件夹` 执行，原始日志保存在 `docs/acceptance/evidence/`：

| 命令 | 结果 | 产物 |
|---|---|---|
| `npm run typecheck` | 通过 | `evidence/typecheck.txt` |
| `npm test` | 13 个测试文件、158 个测试全部通过 | `evidence/test.txt` |
| `npm run build` | 通过，Vite 8.3.5，1617 modules；JS 1,094.90 kB（gzip 347.88 kB），CSS 394.14 kB（gzip 53.68 kB）；保留 >500 kB chunk 警告 | `evidence/build.txt` |

这些结果只证明受控响应下的前端行为、类型与构建；**不证明**真实供应商适配、真实参考图输入、后端持久化、真实文件交付或 Chrome/Edge 双浏览器行为。

## 3. FE-DEP-01–06 核验

2026-10-30 后端 T15 交付后，已用实际 OpenAPI、真实响应与集中类型重新核验。详细字段对照见 `docs/acceptance/t15-real-dual-model-acceptance.md` §6–§7。

| 依赖 | 核验位置 | 核验结果 | 状态 |
|---|---|---|---|
| FE-DEP-01 精确尺寸、供应商字节上限、等待上限、能力字段 | `ConfigStatus` / `VisualConfigStatus` / `ConfigCapabilities`；`getConfigStatus`；`ModelStatusDrawer.vue` / `ReferenceUploader.vue` | 8 个能力字段与 OpenAPI 一致；真实 `1024x1024`、`provider_max_reference_bytes=null`、有效上限 10,000,000，前端无自造上限 | **通过（契约/映射核验）** |
| FE-DEP-02 列表分页参数、摘要、下一页判定 | `ProjectListResponse` / `ProjectSummary`；`listProjects`；`useWorkspace.loadMore` | 实际 `limit 1..100 default 20`、`cursor nullable`、`next_cursor`；真实 limit=1 分页走通 | **通过（契约/映射核验）** |
| FE-DEP-03 消息/需求快照、生成快照、版本排序、request_id 关联 | `ProjectDetailResponse`、`MessageView.brief_snapshot`、`GenerationView.brief_snapshot/version_no/request_id`；`adoptProjectDetail`、`succeededGenerations` | 真实消息 sequence、卡片 snapshot、v1/v2/v3 版本递增和 request_id 重放均与类型一致 | **通过（契约/映射核验）** |
| FE-DEP-04 生成阶段、错误细分、可重取原因和重取完成方式 | `GenerationView.status/stage/error/can_retrieve_result/retrieval_unavailable_reason`；`retrieveGenerationResult` | 真实 succeeded/stage done/can_retrieve false；retrieve 返回原 asset；错误码与类型一致 | **通过（契约/映射核验）** |
| FE-DEP-05 创建、确认、卡片、上传请求/响应字段、修订号与空值 | `PatchBriefRequest` / `ConfirmBriefRequest` / `SubmitGenerationRequest`；`client.ts` 14 方法；`useWorkspace` 修订门禁 | 路径、参数、请求体、nullable 与 OpenAPI 一致；typecheck 通过 | **通过（契约/映射核验）** |
| FE-DEP-06 配置完整性与实际调用证据来源 | `ConfigStatus.verification` 仅接受 `not_checked`；`ModelStatusDrawer.vue` | 真实 LLM/VISUAL 调用后后端仍返回 `not_checked`，前端未把它显示成“已验证” | **通过（契约/映射核验）** |

**结论**：FE-DEP-01–06 的契约与集中映射已核验通过，未发现需要修改 `types.ts`/`client.ts` 的偏差。这只证明前端能按实际契约编译和调用；浏览器呈现与人工验收仍归 #13，不能把本节状态外推到 FE-AC。

## 4. FE-AC-01–22 逐项记录

状态定义：**通过**=有真实环境证据；**失败**=真实环境执行后不满足；**未验证**=缺少前提或未执行。以下全部为未验证；括号内仅列已有的受控测试参考，不构成通过。

| 验收项 | 内容 | 状态 | 受控测试参考（非真实证据） |
|---|---|---|---|
| FE-AC-01 | 三种视口与侧栏切换：Head 只有名称且有磨砂／回退；断点默认值正确，无整体横向溢出；输入和导航可见 | **未验证** | t01/t07 布局与未保存确认测试 |
| FE-AC-02 | 阶段导航和未解锁项：仅查看／定位，禁用原因可取得，生成 POST 为零 | **未验证** | t01 阶段导航测试 |
| FE-AC-03 | 无参数、示例、新建、首次提交：不自动选历史或创建；示例只填字；有效首操作才创建，失败后复用已知项目 | **未验证** | t01/t03 首操作与防重建测试 |
| FE-AC-04 | 澄清、卡片编辑、保存、整理、确认：历史卡只读；保存不调用 LLM；旧提示词过期；确认不提交生成 | **未验证** | t03/t04 澄清与编辑测试 |
| FE-AC-05 | 未保存卡片、未发送反馈、参考图变化：相应确认／生成被阻止；已保存变化需重新整理与确认 | **未验证** | t04/t05 脏状态与参考图门禁测试 |
| FE-AC-06 | 多图、非法／损坏／过大图、取消和有效图：无效选择不调用模型、不替换旧图；有效单图可上传、替换和移除 | **未验证** | t05 参考图测试 |
| FE-AC-07 | 快速重复操作、中文输入法与快捷键：同项在途操作不重发；组合输入不误发送；Enter 换行 | **未验证** | t03/t05/t06 防重入与组合输入测试 |
| FE-AC-08 | 首图、继续修改、反馈、确认、新图：正确切换 Body；原参考图沿用；旧图、新图可选择且各自下载 | **未验证** | t06/t08 首图与反馈重生测试 |
| FE-AC-09 | 查看旧图／切到其他项目时新图完成：保留主动预览，不跳回旧项目；相应项目能发现新结果 | **未验证** | t08 旧版选择与项目切换测试 |
| FE-AC-10 | 生成回执前断网并同标签刷新：提交待核实；先 GET，自动生成 POST 为零；手动恢复的原 ID 和参数完全相同 | **未验证** | t09 提交待核实与恢复测试 |
| FE-AC-11 | 存储读写失败、无效凭据、关标签再开：不凭坏数据发请求；从记录核实并解释恢复限制；不自动创建替代生成 | **未验证** | t09 凭据与恢复限制测试 |
| FE-AC-12 | 三次连续查询失败、手动重新查询：自动查询暂停且旧图保留；手动只 GET；成功且仍活动再继续；成功读取清零失败计数 | **未验证** | t10 查询失败与手动重试测试 |
| FE-AC-13 | 页面隐藏／恢复、快速项目切换、旧读取晚到：自动查询至多单路；旧响应不覆盖新项目；主动中止不计失败；暂停不等于取消服务端任务 | **未验证** | t07/t10 页面隐藏与迟到响应测试 |
| FE-AC-14 | 整理／生成中刷新或后端重启：GET 恢复原记录，显示实际活动／中断／未知；自动消息及生成 POST 为零 | **未验证** | t10 刷新/中断只读恢复测试 |
| FE-AC-15 | unknown 后新生成，先取消再确认：显示可能已计费；取消无提交；确认后新 ID 且 acknowledge_unknown=true | **未验证** | t11 unknown 风险确认测试 |
| FE-AC-16 | 重取现有结果及无可用定位信息：可重取时仅调用原结果重取，防重入，不新建版本或提交生成；失败保留旧图；不可重取时说明原因 | **未验证** | t11 重取与不可用测试 |
| FE-AC-17 | 其他标签页更新后产生 409：本地输入可复制；加载最新由用户明确选择；无静默覆盖、合并或过期提交 | **未验证** | t07 409 本地副本测试 |
| FE-AC-18 | 脏草稿下新建、切项目、前进／后退和离站：站内可取消并恢复原项目与 URL；离站提醒只随脏状态注册，不承诺必定弹出或保留草稿 | **未验证** | t07 脏草稿导航/离站测试 |
| FE-AC-19 | 空配置、认证／额度问题、文件缺失、项目不存在、413/422：错误持久且定位明确；输入／已有可读图片保留；无假连接成功、假图片、假下载或自动修正重发 | **未验证** | t04/t05/t06/t11 错误持久化测试 |
| FE-AC-20 | 键盘、抽屉／大图、减少动效、无背景模糊：名称、焦点、关闭和焦点返回正确；状态不限颜色；两种背景都清晰可读 | **未验证** | t01/t02/t06/t07 键盘与抽屉测试 |
| FE-AC-21 | 项目为空、多页、后页失败：空态能新建；加载更多防重且按 ID 去重；失败保留已加载列表；当前项目高亮 | **未验证** | t02 分页与去重测试 |
| FE-AC-22 | 真实 LLM／VISUAL 的纯文字、有参考图、反馈重生：实际生成文件可在浏览器之外打开；参考图确实进入视觉请求；前后版本都可下载 | **未验证** | 无；真实模型链路未执行 |

## 5. SRS 真实验收项

真实后端证据已于 2026-10-30 具备，但“浏览器中可见/可操作”的验收仍须 #13 执行。

| SRS 项 | 后端真实证据 | 浏览器验收状态 |
|---|---|---|
| AC-01 两套真实配置且无 Key 泄露 | 真实 config status、真实 LLM/VISUAL 调用、secret scan 0 匹配 | **后端通过；浏览器未验证** |
| AC-02 纯文字需求卡→确认→实际图片下载 | 真实项目 64a44549… 的完整闭环，v1/v2 真实 PNG | **后端通过；浏览器未验证** |
| AC-03 参考图实际进入视觉请求 | 真实上传/参考图消息/v3 生成；受控 multipart 原始 bytes 测试 | **后端通过；浏览器未验证** |
| AC-07 反馈重生保留约束且新旧版可下载 | 真实反馈 revision 7、v2 与 v1 hash 不同、旧版可下载 | **后端通过；浏览器未验证** |
| AC-10 生成中刷新恢复原任务不重复提交 | 真实 in-flight 只读刷新；无新消息/生成 | **后端通过；浏览器未验证** |
| AC-11 成功版本重启后历史和图片仍可读 | v1/v2/v3 重启后 asset/hash 一致 | **后端通过；浏览器未验证** |
| AC-12 未完成生成重启变 unknown、不自动重生成 | 真实进程中断 → unknown/PROCESS_INTERRUPTED，2s 后仍 1 条 | **后端通过；浏览器未验证** |

## 6. 浏览器与人工验收待办

至少执行：

1. Windows 近期 Chrome 与 Edge 分别覆盖 375 / 768 / 1279 / 1280 / 1440 宽度；记录 Head/Side/Body、独立滚动、输入可见、无整体横向溢出、图片、版本选择、项目抽屉与两种 Body 模式。
2. 键盘走完商品描述、需求编辑、确认、生成、版本选择、项目抽屉和大图；记录可访问名称、焦点与返回、禁用原因、live region、正文对比、`prefers-reduced-motion` 与无 `backdrop-filter` 回退。
3. 用真实 LLM/VISUAL 完成 FE-AC-22 与 SRS AC-01/02/03/07；用真实持久记录与后端重启完成 AC-10/11/12；所有证据保存于 `docs/acceptance/evidence/`，不得包含 Key 或供应商敏感原响应。
4. 若真实 OpenAPI 与 `contracts.md` 或集中类型不一致，先修正 `main/src/api/types.ts`、`client.ts` 与相关映射/测试，再重跑 typecheck、完整测试和生产构建。

## 7. 结论

- **FE-DEP-01–06**：契约/映射核验通过（2026-10-30），依据实际 OpenAPI、真实响应与 `types.ts`/`client.ts`；浏览器呈现未验收。
- **FE-AC-01–22**：仍**全部未验证**，需要在真实后端已就绪的基础上执行 Chrome/Edge、视口、视觉、键盘与交互场景。
- **SRS AC-01/02/03/07/10/11/12**：后端真实证据已具备；浏览器验收未执行。
- **#13 不能关闭为已完成**；整体 v1 也不能因后端 T15 通过而标记完成。下一步按 §6 执行浏览器验收，发现前端偏差时先修 `types.ts`/`client.ts`/组件映射并重跑 typecheck、完整测试与构建。
