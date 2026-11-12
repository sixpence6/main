# P3 / T10 — 不可变执行快照贯穿确认、编制、质检与历史（#96）

- 票：#96（T10，P3）；父问题 #86；集成分支基线 **cb478a7**
- 工作分支：**ticket/96**；本票最终代码／测试提交 **8fb8df0**（父实现提交 9f20633，合并 integration/v3 提交 87d69a9）
- 规则版本：**RULESET_VERSION = v3.rules.1**；提示词版本 **2.0.0**；快照 schema **1.0.0**；执行契约 **1.0.0**；编码 **canonical-json-sha256-v1**
- 迁移头：**0011_recovery_claims**（来自 T08；T10 不新增关系列，因此没有 T10 迁移）
- 环境：隔离临时 SQLite、计数型 **LLMStub**、**VisualStub**、临时数据根、网络全部 stub；没有真实 provider、真实图片服务或生产数据库副本
- 本票文件：**main/backend/app/workflow/snapshot.py**（新增规范快照）、**contracts.py**（FinalBriefData 专用字段）、**schemas_workflow.py**（历史快照暴露）、**state.py**（按版本取原快照）、**stages/s05.py**（专用字段进入 final_brief）、**stages/s06.py**（确认时构建并原子持久化）、**stages/s07.py**（从快照取 requirements）、**stages/s08.py**、**stages/s09.py**（同一快照质检）、**stages/s12.py**（三元返回兼容）、**backend/tests/test_v3_p1_t10_execution_snapshot.py**
- 证据：**docs/acceptance/evidence/p1-t10.json**

## 1. 结论与交付内容

T10 在既有 S05→S06→S07→S09→S10→历史链路上引入一个版本冻结、可校验、不可变的执行快照。快照由批准后的 final_brief 构建，包含批准需求、创意和视觉的全部字段映射、稳定约束 ID、原值／归一值、来源、强度、用户锁定依据、去向或非执行理由，以及 artifact／revision／参考依赖和执行参数绑定。S06 把快照写入既有 **Approval.checks_json** 的 confirmation 文档；S07 和 S09 从同一 approval 读取；发布历史按每个版本自己的 final_brief artifact 与 requirements_card approval 取回原快照，而不是读取当前项目列。

结论：票面八项均有实现和定向 HTTP／单元证据。全量 pytest 因环境端口／重启 helper 卡住而在 600000ms 超时，已按残余未知记录，未据此声称全量通过。

### 1.1 全部字段到确认快照、编制、质检与历史的映射

规范字段顺序固定为以下 24 项。下表的 destination 是快照字段的 explicit 去向；strength 中 hard 表示既有阻断语义，soft 表示专用视觉引导（除非用户显式锁定），not_applicable 表示非执行字段。source 由 S05 的 field_sources 或字段类别给出；缺失的 user_locks 只会得到 null，不会伪造用户锁定。

| 字段 | 去向 | 强度 | 来源 | 映射说明 |
| --- | --- | --- | --- | --- |
| product_description | positive_prompt | hard | brief_draft | hard:subject，质检 hard:subject 需求文本 |
| purpose | positive_prompt | hard | brief_draft | hard:purpose |
| facts | positive_prompt | hard | brief_draft | hard:fact 列表，质检上下文保留同值同源 |
| creative_direction | positive_prompt | hard | creative_direction | hard:creative |
| style_keywords | positive_prompt | hard | visual_plan | hard:style；视觉专用时为 visual_plan |
| composition | positive_prompt | hard | visual_plan | hard:composition |
| lighting | positive_prompt | hard | visual_plan | hard:lighting |
| background | positive_prompt | soft | visual_plan | 新专用视觉字段；无锁定时 soft，质检 hard:composition 文本也带背景 |
| palette | positive_prompt | soft | visual_plan | 新专用视觉字段；按条目生成 req:palette:i |
| text_layout | positive_prompt | soft | visual_plan | 新专用视觉字段；质检 hard:composition 文本带文字布局 |
| reference_treatment | positive_prompt | soft | visual_plan | 新专用视觉字段；质检 hard:composition 文本带参考处理 |
| texts | positive_prompt | hard | brief_draft 或 creative_direction | hard:text:i；creative copy_lines 并入后仍保留来源 |
| must_include | positive_prompt | hard | brief_draft | hard:must_include:i |
| must_preserve | positive_prompt | hard | brief_draft+visual_plan | hard:must_preserve:i；专用 preservation_rules 并入 |
| preservation_rules | positive_prompt | soft | visual_plan | 作为专用字段保留，mapping_note 说明已并入 must_preserve 执行 |
| forbidden | negative_prompt | hard | brief_draft | hard:forbidden:i |
| size | size | hard | brief_draft | hard:size，同时绑定执行参数 |
| reference_asset_id | reference | hard | brief_draft | hard:reference_asset_id；无参考图时说明非执行 |
| reference_usage | reference | hard | brief_draft | 绑定 reference_asset_id；未使用时说明非执行 |
| count | count | hard | contract | hard:count，固定单件执行参数 |
| assumptions | non_executable | not_applicable | brief_draft | 仅说明批准依据，不独立进入提示词 |
| unresolved | non_executable | not_applicable | brief_draft | 未决问题不是执行指令；存在未决问题时确认被阻止 |
| selling_points | qc_only | not_applicable | creative_direction | 用于审查背景，不单独成为执行约束 |
| copy_lines | positive_prompt | soft | creative_direction | 文案并入 texts 执行，保留 creative_direction 来源 |

每个列表条目都有稳定 ID **req:&lt;path&gt;[:index]**；scalar 为 **req:path**。约束记录同时包含 original_value、normalized_value、source、strength、user_lock_basis、applicability、destination、binding、rule_id、mapping_note。S05 的 FinalBriefData 只增加可选专用字段和 field_sources／user_locks，不改变既有字段的合并语义；S06 构建快照时保留 legacy confirmation 的 schema_version 2.0.0、artifact／revision、reference、hard_requirements 等字段，避免旧客户端读路径被破坏。

### 1.2 冻结的摘要、版本和绑定

**snapshot.py** 冻结：

- **EXECUTION_SNAPSHOT_SCHEMA_VERSION = 1.0.0**
- **EXECUTION_CONTRACT_VERSION = 1.0.0**
- **EXECUTION_SNAPSHOT_ENCODING = canonical-json-sha256-v1**
- **rules_version = v3.rules.1**
- digest 使用既有 contracts.digest_payload：UTF-8、sort_keys、紧凑分隔符、sha256；只排除外层 confirmation envelope 的 schema_version／confirmed_by／confirmed_at，不排除执行正文。

快照正文包含 artifact_id、artifact_digest、artifact_revision_no、project_revision、requirement_revision、reference_asset_id、reference_usage、referenced_material_digest、brief、fields、constraints、hard_requirements 和 snapshot_digest。这样当前卡片、确认、原意图（同一 final_brief artifact 和 requirement revision）、项目 revision、参考依赖与执行参数都绑定同一正文；**approval.requirement_snapshot_digest** 和 **GenerationJob.confirmed_snapshot_digest** 仍保持原有 final_brief artifact digest 语义，执行快照 digest 是独立字段，不偷换旧字段含义。

### 1.3 S07、S09 与历史读取同一快照

- S06 **_confirm_atomic** 在同一个 BEGIN IMMEDIATE 事务中写 Approval、intent、usage、slots 和项目确认投影；confirmation 内嵌快照。幂等 replay 返回原 confirmation，不重写 approval digest，不新增模型调用。replay 保护条件 **advance_confirmation = run.stage in {S06,S07}** 防止已进入 S08/S09 的运行被回退到 S07 而丢失质检调用。
- S07 **_require_confirmed** 返回 final_brief artifact、brief 和 execution_snapshot；校验 artifact id/digest、project／requirement revision、brief 相等和参考绑定，任何 canonical 快照异常返回 **409 SNAPSHOT_INCOMPATIBLE**。编制时 requirements 来自 **requirements_from_snapshot**（稳定 hard constraints），generation_engineer 的 approved_inputs 同时带 final_brief 与 execution_snapshot。
- S09 **_confirmed_snapshot** 优先匹配当前 requirements_card approval 与 approved final_brief artifact，校验 artifact id/digest 后返回同一快照；quality_compliance 的 approved_inputs 带 execution_snapshot 和 final_brief，hard:composition 规则文本带背景、配色、文字布局、参考处理。
- state **_version_execution_snapshot** 用每个发布版本自己的 run 的 approved final_brief artifact 和同 artifact digest 的 requirements_card approval 取原快照；不读当前项目列，不回写旧 approval。历史 brief 按 **job.confirmed_snapshot_digest** 精确取该 generation job 消费的 final_brief revision，prompt 按 **job.prompt_artifact_id** 精确取原 prompt，quality 取该 run 的 approved artifact payload；仅当精确 artifact 不满足 run／kind／approved 条件时才回退到该 run 的最新 approved revision。回归用例在同一 run 发布后插入 final_brief revision 2 和 prompt_plan revision 2，历史仍返回原 background、原 prompt 与原 snapshot digest。

### 1.4 旧记录兼容与失败闭合

**execution_snapshot_from_checks** 只有缺少 snapshot_schema_version 时才返回 None；这是显式 legacy 结果，继续使用既有 approved final_brief／brief_draft payload 回退，绝不会用当前项目字段重新解释历史。出现 canonical 但版本缺失、版本不支持、contract 版本不支持、encoding 不支持、digest 缺失／不一致、fields 缺失、destination 缺失、reason 缺失或 constraints 缺失时抛出 **SnapshotCompatibilityError**，调用方转为 409 **SNAPSHOT_INCOMPATIBLE**，不静默降级。

T10 没有新增关系列；现有 Approval.checks_json 已是持久 JSON，足以保存快照，因此无需迁移。若未来改为 SQL 列，必须在 **backend/migrations/versions** 和 **main/backend/migrations/versions** 同时新增迁移并覆盖升级／失败回滚；本次没有伪造迁移。

## 2. 验收覆盖（票面八项）

1. **全部字段映射**：24 个字段都在 **fields** 中有 value、original_value、source、executable、destination／reason、strength、user_lock_basis、applicability、constraint_id(s) 和 mapping_note。执行字段有去向；assumptions、unresolved、selling_points 有非执行理由。S05 专用字段保留测试断言 background、palette、text_layout、reference_treatment、preservation_rules、copy_lines 和 field_sources。
2. **专用字段同值同源**：HTTP 测试从 confirmation 取得 fields，再在 generation_engineer 的 approved_inputs 和 quality_compliance 的 approved_inputs 中比对 background、palette、text_layout、reference_treatment、preservation_rules、facts、composition、lighting 的 value 与 source；四个视觉专用值同时出现在 hard:composition 的 requirement 文本。
3. **稳定约束元数据**：测试明确断言 **req:background**、**req:palette:0/1**、**req:preservation_rules:0** 和 must_include 稳定前缀；旧字段仍为 hard，新专用视觉字段默认 soft，user_lock_basis 缺失时为 null，不从缺失历史伪造锁定。
4. **冻结编码与绑定**：快照 schema／contract／encoding／rules 版本冻结；同一输入两次构建 digest 相同，改变 background 后 digest 不同；replay 返回同一 digest；数据库 approval 的 confirmation digest 与 view 一致且不等于 artifact digest。
5. **同一确认快照与历史原快照**：S07 与 S09 都从 requirements_card approval 取快照；S07 的 requirements_from_snapshot 和 S09 的 quality approved_inputs 使用同一 digest；发布历史通过该 run 的 approved final_brief artifact 取原 approval。发布后修改项目商品描述、风格和 revision，历史仍返回旧商品描述、旧 background 和旧 digest，approval 未被改写；随后为同一 run 插入 final_brief revision 2 与 prompt_plan revision 2，历史仍按 job 的原 digest／prompt artifact id 返回旧 brief、旧 prompt 和旧 snapshot digest。
6. **持久化与兼容**：快照存放在既有 Approval.checks_json，无 T10 迁移；legacy confirmation（仅 schema_version 2.0.0，无 snapshot_schema_version）显式返回 None；版本 9.9.9 的畸形 canonical 快照抛 SNAPSHOT_VERSION_UNSUPPORTED，调用方 fail closed，不重解释项目列。既有 t65/t66/v2_t09/v2_t40 定向回归通过。
7. **真实 HTTP 确认→编制→质检→历史**：**test_execution_snapshot_flows_confirm_compile_qc_and_history** 使用真实 HTTP confirm_requirements、S07、S09、approve_delivery、publish_delivery 和 workflow GET，断言值、来源、digest、approval、published asset_id／filename／can_download=true，并直接读 SQLite 的 Approval／Artifact。记录 WG3-AC-06；AC-12 只记录共享边界，不冒充 T05 的完整图片失败矩阵。
8. **测试与取证位置**：测试在 **backend/tests/test_v3_p1_t10_execution_snapshot.py**，证据在 **docs/acceptance/**；没有把测试脚手架、临时文件或证据写进 **main/** 业务目录。基线、实际命令／结果、调用计数和残余未知见下。

**WG3-AC-06 原文**：背景、配色、文字布局、参考处理只在专用字段出现也进入确认／编制／质检快照；历史仍用原快照。本票证据：confirmation fields、S07 approved_inputs、S09 approved_inputs／hard:composition、published version 的 execution_snapshot 均保持同值同源，发布后项目字段变化不影响历史 brief／digest。

**WG3-AC-12 原文**：报告 pass 但图片 fail／unknown 时无图片／下载；历史批准、规则变化及正常／恢复路径均验证相同放行边界。本票只记录共享边界：T05 的 test_report_pass_with_image_failure_never_exposes_bytes 与 test_report_pass_with_image_unknown_never_exposes_bytes 负责图片 fail／unknown 无字节／下载；T10 负责正常批准／发布历史的 asset_id／filename／can_download 可见性与原快照一致性，不声称覆盖完整 AC-12 矩阵。

## 3. 验证记录

以下命令均从工作树 **D:\Desktop\新建文件夹\.worktrees\t96\backend** 调用 **D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe**，环境为临时 SQLite、stub 适配器、无网络。ruff 从工作树根目录执行。

| 命令／范围 | 实际结果 |
| --- | --- |
| ruff check（snapshot.py、contracts.py、schemas_workflow.py、state.py、s05、s06、s07、s08、s09、s12、T10 测试） | **All checks passed!** |
| pytest tests/test_v3_p1_t10_execution_snapshot.py -q | **4 passed in 2.12s** |
| V3 定向块：t03／t04／t05／t07／t09／t10 -q | **72 passed in 64.57s (0:01:04)** |
| 工作流／历史块：t65／t66／v2_t09／v2_t40／t61／t75 -q | **42 passed in 62.73s (0:01:02)** |
| 合计定向测试 | **114 passed**（分块执行，不是单次全量） |
| 全量 pytest -q | 达到 600000ms 上限，卡在 tests.support.restart_app／端口 6968，无单次全量汇总；按环境性超时记录 |

### 3.1 调用／计数对照（计数型 stub）

| 场景 | generation_engineer | output_reviewer | quality_compliance | delivery_manager | prompt artifact | 预算 used | 图片生成 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T10 正常确认→编制→质检→发布 | 1 | 3（编制后、质检后、交付后） | 1 | 1 | 1 approved | 1（normal prompt candidate） | 1 |
| 同一 request_id 确认 replay | 0 | 0 | 0 | 0 | 0 新增 | 0 新增 | 0 新增 |
| 发布后修改项目字段并读历史 | 0 | 0 | 0 | 0 | 0 新增 | 0 新增 | 0 新增 |

说明：计数型 stub 的 model_text = 6，model_vision = 0，image_generation = 1；没有真实 provider 网络调用，也没有真实图片服务。T10 不修改 RevisionBudget，T07／T08 负责预算边界；这里的一个 prompt 候选是正常流程观测值。

## 4. 已知红色与残余未知

### 4.1 已知红色

- 在本次 T10 定向范围内没有已知红色：T10 4 项、V3 定向块 72 项、工作流／历史块 42 项全部通过。
- 全量套件没有完成（600000ms 超时），因此不声称全量零红，也不把未执行的分块当作通过证据。

### 4.2 残余未知

1. **全量单次执行**：pytest 在 tests.support.restart_app／端口 6968 卡住并触达工具 600000ms 上限，无法给出全量汇总；已报告 114 项分块结果，不伪造聚合通过。
2. **真实 provider**：T10 使用计数型 LLMStub 与 VisualStub，没有真实模型 token、延迟、费用或图片服务证据。
3. **前端**：workflow TypeScript 类型与界面未在 T10 修改或浏览器验收；快照字段在 API 中为可选扩展，前端展示留待后续 UI 工作。
4. **迁移**：T10 没有 SQL 列变更，快照写入既有 Approval.checks_json；若后续选择列存储，必须按双树迁移链新增迁移并测试升级／失败回滚。
5. **历史兼容**：legacy 回退由既有 t65／t66／v2_t09／v2_t40 和 T10 单元探针覆盖，但没有真实 pre-T10 生产数据库副本；只能声明有限旧格式兼容，不声明所有历史数据均已迁移。
6. **并发／崩溃**：T10 的 replay 保护已被 HTTP 测试覆盖；进程级崩溃、并发确认和跨进程竞态不在本票定向范围。
7. **S09 规则目录**：保持既有七个稳定 rule_id；专用字段通过同一 execution snapshot 与 hard:composition 文本进入质检，不新增规则 ID，避免破坏 T03／T05／T09 的既有断言。

## 5. P3 入口

T10 的快照 schema 1.0.0、contract 1.0.0、canonical-json-sha256-v1 和 rules v3.rules.1 已冻结；后续 #97／#98 的字段合成、卡片展示和 UI 工作不得静默改变其编码或 digest。任何格式变化必须新增版本并增加兼容性测试。T10 未合并 integration/v3，由 lead 串行集成。
