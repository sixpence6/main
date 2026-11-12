# P0 基线与审查消费者清点（#86 / ticket #87）

## 1. 结论与边界

- 本记录只做四件事：固定代码与测试基线、给出历史发现的当前状态分类、清点全部审查消费者与批次归属、判定既有 R1/R2/R3/Release 记录哪些证据可复用。
- **本记录不预填任何通过记录。** 未验证项一律写 `unknown`；没有当前命令输出支撑的旧结论不作为当前证据。
- 本轮不实现 P1 修复，不改应用配置、真实数据根、部署、数据库内容或业务目录中的测试/证据。
- 行为代码基线固定为 `072c446`。`integration/v3` 的 `ef92b33` 只新增 P0 文档和纯函数探针，未修改 `main/backend/app` 应用代码；本记录在该记录上补充可执行 HTTP/workflow 回归与更正分类。完成前按任务要求合并 `integration/v3` 到 `8b482ca`（#88 S07 规则目录），因此最终分支还包含 #88；第 3 节仍按 `072c446` 基线记录，合并后的变化单列在 7.1。
- 固定基线 `072c446` 上仍有 3 个既有红灯用例（见 2.3），F1—F5 全部仍复现，H1 只部分修复，前端多处仅为源码级证据；合并 #88 后只有 F1 的 S07 路径转绿（见 7.1），F2—F5 仍未关闭。因此不能把本记录当作任何阶段或发布门禁的通过证明。

## 2. 代码版本与环境

| 项 | 值 |
| --- | --- |
| 固定基线提交 | `072c4467dd7d512778ae65f3436a23488e4b7ebe`（`chore(baseline): capture v3 gate/workbench baseline working tree (#86)`） |
| 基线父提交 | `b0ae81c3c70af29cd59c11c1c98e960b9eb92ec7`（`Merge ticket/85: release gate acceptance (#85)`） |
| 分支 | `ticket/87`（从 `072c446` 执行 `git merge integration/v3`：`ef92b33` 后继续快进到 `8b482ca`，即 #88；随后追加本票证据提交） |
| 基线规模 | 36 个文件，+2105 / −182；33 个已跟踪修改 + 3 个新增门禁/前端/整改文档 |
| 提示词版本 | `PROMPT_VERSION = "2.0.0"`（`main/backend/app/workflow/prompts.py:8`） |
| 规则版本 | `RULES_VERSION = "2.0.0"`（同文件 `:9`） |
| 规则目录版本 | `null`；服务端规则目录/统一裁决由 P1 #88 才引入，本基线不存在 |
| 数据/契约 schema | `SCHEMA_VERSION = "2.0.0"`（`main/backend/app/workflow/stages/s06.py:37`）；`main/backend/app/workflow/contracts.py` 注释为 v2.0.0 |
| 迁移版本 | `0001_initial_schema` … `0010_r2_local_tracking_closure`；head revision `0010`，down_revision `0009` |
| 后端解释器 | `D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe` |
| 后端测试目录 | `worktree/backend`；`testpaths=["tests"]`、`pythonpath=["../main/backend"]` |
| 前端 | `worktree/main/src`，Vue 3 + Vite + Vitest；本次只做源码级核对，未启动浏览器/真实设备 |
| 数据库 | 每次 pytest 使用临时 SQLite（`backend/pytest-of-unknown/`，不提交），不触碰真实数据根 |
| 网络/模型 | `LLMStub` / `VisualStub` / 既有测试 harness；无真实 provider、无真实图片调用 |
| 本机环境 | Windows 10.0.19045.6456；Python 3.12.13；pytest 9.2.0；Node v26.12.1；npm 11.20.0；SQLite 3.50.4 |

说明：`072c446` 只是把用户原有的未提交工作树改动固化为可复现基线；它不等于整改已完成。旧 issue 关闭、旧文档中的“通过”或单测变绿，都不能替代本表固定基线上的重新运行。

### 2.1 基线保存方式

- 备份副本：`.scratch/baseline-20261102/`（`HEAD.txt`、`status.txt`、`tracked.diff` 266 KB、`diffstat.txt`、`docs/architecture/*.md` 副本）。
- 固化提交 `072c446` 覆盖：`backend/tests/*.py` 13 个既有测试文件、`AGENTS.md`、`docs/architecture/{hld_v3,frontend-design-v3,workflow-gates-v3,workflow-remediation-plan-v3}.md`、后端 `adapters/{base,openai_chat,openai_images}.py`、`maintenance/{backup,manifest}.py`、`services/generations.py`、`workflow/{contracts,dispatch,state}.py`、`workflow/stages/{s01,s03,s04,s05,s07,s08,s09,s12}.py`、`main/src/components/workflow/WorkflowPanel.vue`、`vesion/v3/SRS_v3.md`。
- 未跟踪的 `DAG依赖图.png/.svg`、`ecommerce-image-workspace-main.zip`、`backend/pytest-of-*` 属于无关文件，不提交、不放进 `main/`。

### 2.2 可复现命令

```powershell
cd D:\Desktop\新建文件夹\.worktrees\t87\backend
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m pytest tests/test_v3_p0_baseline.py -q
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m pytest tests/test_v3_p0_baseline.py -q --runxfail
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m pytest tests/test_t87_gate_regressions.py -q
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m pytest tests/test_t87_gate_regressions.py -q --runxfail --tb=line
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m pytest tests/test_t03_intent_review.py::test_failed_s01_review_can_be_continued_from_completed_checkpoint tests/test_t11_retrieve.py::test_failed_download_can_retrieve_same_version_and_downloads_real_file tests/test_t11_retrieve.py::test_retrieval_stays_failed_while_waiting_and_blocks_other_write -q
| `tests/test_v2_t08_visual_final_brief.py::test_visual_technical_failure_has_zero_automatic_retries` | 失败：`available_actions` 同时有 `plan_visual` 与 `continue_workflow`（期望仅后者） | 与 T03 同类状态投影，归 #93；扩展相关套件时发现，未 bisect 到 `072c446`，来源标 unknown |
D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe -m ruff check tests/test_t87_gate_regressions.py tests/test_v3_p0_baseline.py
```

### 2.3 基线已知红灯（本轮实测，不是整改缺陷清单）

| 用例 | 当前结果 | 归属/阻塞范围 |
| --- | --- | --- |
| `tests/test_t03_intent_review.py::test_failed_s01_review_can_be_continued_from_completed_checkpoint` | 失败：受阻运行的 `available_actions` 多出 `submit_requirement`（期望仅 `continue_workflow`） | WG3-AC-10 安全状态投影，归 #93；修好前不能宣称 R1/R2 回归全绿 |
| `tests/test_t11_retrieve.py::test_failed_download_can_retrieve_same_version_and_downloads_real_file` | 失败：重取返回 502 `RESULT_FETCH_FAILED`（“重取结果无法作为图片保存”） | 资源字节出口，归 #91；由 #92 复核，修好前不能宣称 R2 通过 |
| `tests/test_t11_retrieve.py::test_retrieval_stays_failed_while_waiting_and_blocks_other_write` | 失败：与上一项同一字节出口 | 同 #91/#92 |

复现结果（2026-11-02，cwd=`worktree/backend`）：前三项基线固定红灯一起执行仍为 `3 failed in 7.76s`；第四项在相关套件扩展运行中为 `1 failed, 70 passed in 102.84s`，不是本票改动引入，但是否在 `072c446` 已存在未做 bisect，因此只作为当前观察，不计入前述三项基线事实。不得用其他套件的绿色掩盖。

## 3. 历史发现分类与最小复现场景

状态口径：`still-reproducing`（当前基线可复现）、`changed-needs-verification`（代码已变，运行侧仍待复核）、`has-current-evidence`（当前有可复现/源码级证据）、`unknown`（证据不足）。

| 编号 | 状态 | 最小复现场景与当前结果 | 验收 ID | 归属 |
| --- | --- | --- | --- | --- |
| F1 | **still-reproducing** | `tests/test_t87_gate_regressions.py::test_s07_rejects_all_not_applicable_prompt_review`：两条 S07 必审均为 `not_applicable`、`evidence_ids=[]`，`compile_prompt` 实际返回 200 且 `prompt_plan` 为 approved；预期 409 `PROMPT_REVIEW_INVALID`。纯函数探针 `test_v3_p0_baseline.py::test_f1_s07_all_not_applicable_cannot_be_approved` 同样在 `--runxfail` 下失败（DID NOT RAISE）。 | WG3-AC-01 | #88（P1） |
| F2 | **still-reproducing** | `test_visual_gate_ignores_advisory_failure_when_blocking_checks_pass`：S05 必审全部 pass，仅额外 `SCOPE_AND_RESPONSIBILITY=fail`，`plan_visual` 实际 503 `REVIEW_UNAVAILABLE`；预期 200/approved。S07 纯函数探针也显示 advisory fail/unknown 被拒。S12 走同一批校验器。 | WG3-AC-02（规则来源 AC-03 由 #88 建边界） | 根因 #88；S01/S03 #89、S04/S05 #90、S09/S10/S11 #91 |
| F3 | **still-reproducing** | `test_prompt_coverage_format_error_keeps_original_intent`：`generation_engineer` 返回被篡改的覆盖值，后台先 `PROMPT_INVALID`，随后 `PROMPT_REQUIRES_RECONFIRMATION`；`GenerationIntent.status=abandoned`、1 条 usage、槽位释放、`visual.submit_calls=[]`。预期保留原意图、定向修复、不新增生成计数。 | WG3-AC-07/08 | #93（P2） |
| F4 | **still-reproducing** | `test_soft_visual_preferences_are_not_hard_prompt_coverage_requirements[hard:creative|hard:style|hard:lighting]`：缺任一软偏好覆盖时 `compile_prompt` 实际 409 `PROMPT_INVALID`；另 `test_image_composition_rule_does_not_merge_soft_preferences` 显示 `s09._composition_requirement` 把 creative/style/lighting 合并进 `hard:composition`。预期软偏好不成为硬覆盖门槛、不混入 composition 硬项。 | WG3-AC-04/05 | #97（P3；快照字段由 #96） |
| F5 | **still-reproducing** | `test_final_card_preserves_dedicated_visual_fields`：`plan_visual` 通过后 `finalize_requirements` 返回 200，但 `final_brief.payload.background/palette/text_layout/reference_treatment` 全为 `None`；预期与视觉方案对应字段一致并跨确认/编制/QC 保留。 | WG3-AC-06 | #96（P3） |
| H1 | **changed-needs-verification（部分修复）** | HTTP 路径：换行多句精确复制 `test_multi_sentence_exact_copy_with_newline_is_covered` 通过（200/approved）；否定翻转、跨句字符拼接分别返回 409 `PROMPT_INVALID`，说明这两类已修复。但 `test_numeric_quantity_change_is_not_false_passed` 将 `350 毫升` 改成 `3500 毫升` 实际仍 200/approved，说明数值/数量漂移仍会假通过；其余标点与历史候选需 #95 继续覆盖。 | WG3-AC-09 | #95（P2） |

合并说明：最终合并 `integration/v3` 的 `8b482ca`（#88）后，F1 的 S07 HTTP 回归与上游 S07 advisory 纯函数探针已通过；F2 的 S05 消费者仍返回 503 `REVIEW_UNAVAILABLE`。表中 F1/F2 的 **still-reproducing** 是 `072c446` 固定基线分类，不因后续 #88 改写；最终分支观测见 7.1。

### 3.1 前端缺口（FE3 验收矩阵）

前端证据来自 `worktree/main/src` 的源码核对；没有启动浏览器或真实设备，因此除明确标注源码级外，全部是 `unknown`，不能当作 FE3 通过。路由事实：`main/src/router/index.ts:13-17` 只有登录/改密/账号安全/管理账号；`:23-25` 根路径挂 `WorkspaceLayout` → `DescribePage`；`:28-32` 的 `/describe`、`/confirm`、`/generate`、`/results` 全部 redirect 到 `describe`，通配也回 `describe`，没有 `/projects/:id/chat` 或独立结果路由。布局事实：`main/src/pages/DescribePage.vue:21-28` 为左 `ConversationPanel`、右 `WorkflowPanel + HistoryPreview` 两列；`:34-36` 桌面双列 `minmax(0,1.15fr)/minmax(0,0.85fr)`；`:54-57` 仅 `max-width:1023px` 单列。侧栏：`main/src/components/AppSidebar.vue:69` 只有“已有项目”，没有搜索或重命名入口。

| 验收 ID | 状态 | 当前证据/最小复核场景 | 主要归属 |
| --- | --- | --- | --- |
| FE3-AC-01 | unknown | 品牌/视觉签名/本地无参考站运行时未测；需真实 App 启动+截图/可访问性检查 | #108 |
| FE3-AC-02 | **still-reproducing（源码级）** | 桌面永久右栏 `DescribePage.vue:21-28/34-36`；没有单一 chat 消息流；需在运行进程确认“描述/补充/卡片/授权/进度同一 chat” | #103、#108 |
| FE3-AC-03 | unknown | 已知信息不重复问、快捷答案可编辑、未批准正文不可见、仅当前有效卡可授权均未逐项运行 | #103 |
| FE3-AC-04 | unknown | 重复点击/跨标签/丢响应/陈旧卡是否重复受理/生成/计数未测 | #103 |
| FE3-AC-05 | unknown | 活动项目发布跳转、初始加载/刷新/返回不重跳、迟回调不抢占/泄漏未测 | #102 |
| FE3-AC-06 | **has-current-evidence（源码级缺口）** | 无 `/projects/:id/chat` 目标壳层；旧 `/describe` 等全部 redirect；无法证明路由/浏览器历史/下载不新增模型调用 | #101 |
| FE3-AC-07 | **has-current-evidence（源码级缺口）** | 无独立结果/下载路由；未审/撤销/过期/缺失/非法 URL 不回退缓存或他版未测 | #105 |
| FE3-AC-08 | **has-current-evidence（源码级缺口）** | 无独立结果折叠区；返回编辑仅导航/聚焦、结果区无输入/反馈表单未测 | #104、#105 |
| FE3-AC-09 | unknown | 真实编辑在新版本/审查/授权前接受、历史不改编辑基线/覆盖旧图未测 | #104 |
| FE3-AC-10 | **has-current-evidence（源码级缺口）** | `AppSidebar.vue:69` 无标题搜索/重命名入口；服务端搜索/重命名另由 #100 实现，UI 由 #106 验证持久性、跨账号隔离、零模型调用 | #100、#106 |
| FE3-AC-11 | unknown | `ConversationPanel` 有 IME composing/keyCode229 处理，但单图选择/粘贴/拖拽/替换/移除/附件不跨项目未逐项运行 | #107 |
| FE3-AC-12 | **has-current-evidence（源码级缺口）** | 无 `/projects/:id/chat` 和稳定壳层；同项目 Body 切换保留草稿、离开守卫、登出/切号/跨标签失效未测 | #101、#107 |
| FE3-AC-13 | unknown | 失败/未知/未放行区分、仅真实动作、无假百分比/自动技术重试/假取消未测 | #99、#103 |
| FE3-AC-14 | unknown | 320/768/1440 与真实桌面/移动无横向溢出、软键盘/抽屉/焦点/标签可用未测 | #108 |
| FE3-AC-15 | unknown | 原任务卡原因区分、后端动作对齐、修复不重复确认/计数、未审建议不泄漏未测 | #99、#103 |

## 4. 全量审查消费者清单

下表覆盖 `main/backend/app/workflow/stages` 中所有 `output_reviewer` 消费者。规则目录/统一有效裁决由 #88 引入；本表记录的是当前各自实现的现状，迁移时不能只改一个 stage。

| 阶段/正常入口 | 强制审查 ID（必须全部出现） | 重复实例与通过规则 | S12 / 历史入口 | 本批归属 |
| --- | --- | --- | --- | --- |
| S01 `submit_request`、`answer_intent_clarification`（`s01.py:64,199`） | `G01_SCOPE`、`G01_ACTION` | required 可重复；每个 required 至少一次，每个实例都必须 pass；advisory 仅 pass/N-A；证据限 `{message_id}` | S12 `_resume_s01_review`（`s12.py:292,311,315,347`）；无历史入口 | #89（规则来源 #88） |
| S02 `analyze_reference`、`confirm_reference_usage`（`s02.py:44,276`） | `G02_IMAGE_VISIBLE`、`G02_OBSERVATION_GROUNDED`、`G02_TEXT_IS_DATA`、`G02_NO_UNSUPPORTED_INFERENCE` | required 集合必须完全相等且无重复；每条证据非空，至少一条引用 image evidence；无 advisory | S12 `_resume_s02_analysis`（`s12.py:582`）；无历史入口 | #89 |
| S03 `compose_brief`、`answer_requirements_clarification`、`revise_brief_draft`（`s03.py:48,579`） | `G03_STRUCTURE`、`G03_SUBJECT_PURPOSE`、`G03_SOURCE_BASIS`、`G03_TEXTS`、`G03_CONSTRAINTS`、`G03_SIZE`、`G03_UNRESOLVED` | required 可重复；每个实例只能 pass/fail/unknown（永不 N/A），每个 required 至少一次；decision=pass 时任何 fail/unknown repair 即 `REVIEW_UNAVAILABLE`；非 pass 必须有 repair code；advisory 可为 Gnn_/review:* | S12 `_resume_s03_review`（`s12.py:381/443`）；无历史入口 | #89 |
| S04 `plan_creative`、`revise_creative`（`s04.py:40,382`） | `G03_SOURCE`、`G03_COPY`、`G03_REQUIREMENTS`、`G03_SINGLE_DIRECTION` | required 可重复、每个实例 pass；额外 advisory fail/unknown 在 decision=pass 时触发 `_fixed_review`（F2）；非 pass 不查重 | S12 `_resume_s04_review`（`s12.py:508/526`）；无历史入口 | #90 |
| S05 `plan_visual`、`revise_visual`、`finalize_requirements`（`s05.py:36,46,536`） | visual 8 项：`S05_STYLE`、`S05_COMPOSITION`、`S05_BACKGROUND`、`S05_PALETTE`、`S05_LIGHTING`、`S05_TEXT_LAYOUT`、`S05_REFERENCE_TREATMENT`、`S05_PRESERVATION`；final card 10 项：`S06_PRODUCT_SOURCE`、`S06_PURPOSE`、`S06_CREATIVE`、`S06_STYLE`、`S06_COMPOSITION_LIGHTING`、`S06_REFERENCE`、`S06_TEXT`、`S06_CONSTRAINTS`、`S06_SIZE`、`S06_BLOCKERS` | required 必须全出现且不重复、全部 pass；advisory 允许 pass/fail/unknown/N-A，但 decision=pass 且 advisory fail/unknown 触发 `REVIEW_UNAVAILABLE`（F2）；非 pass 拒绝重复 | S12 `_resume_s05_review` 从 checkpoint `task.required_rule_ids` 取值后复用（`s12.py:631-642`）；无历史入口 | #90 |
| S06 最终卡硬项生成（不是 output_reviewer 校验器，`s06.py:180`） | 无审查 ID；从卡片生成硬项，当前无规则版本 | 不适用；但决定下游硬项集合，F4/F5 与此相关 | 无独立 S12 审查入口 | #90（卡片）；#96/#97（字段与强度） |
| S07 `compile_prompt`、`revise_prompt`（`s07.py:20,311`） | `G03_CONFIRMATION`、`G05_EXECUTION_BOUNDARY` | 当前无规则白名单/重复检查；required set 子集即可；N/A 可用空证据，decision=pass 只要求所有 outcome ∈ {pass,not_applicable} → F1；F2 同型 advisory 拒绝 | **无 S12 恢复入口**；#88 必须补；无历史入口 | #88 |
| S09 `review_candidate`、`recheck_candidate`、`retry_rejected_generation`、`revise_prompt_and_retry`（`s09.py:46-70,488,603`） | quality target: `review:quality_report`；image target 动态：`hard:subject`、`hard:product_consistency`（仅参考图时 applicable）、`hard:quantity`、`hard:text_logo`、`hard:forbidden`、`hard:composition`、`hard:size_format` | 恰好两个 results（quality+image）；非 `review:*` 重复拒绝，`review:*` 重复允许；quality decision=pass 时所有 checks 必须 pass；image 每个 applicable hard 恰好一次，pass 时全 pass | S12 `_resume_s09_producer`（`s12.py:747`）复用 `_validate_quality`；`_resume_s09_review`（`:839`）调用 `_apply_review`（`:1020`）；无历史入口 | #91 |
| S10 `prepare_delivery`、`approve_delivery`、`publish_delivery`、`request_new_version`（`s10.py:56,263`） | `G07_MANIFEST_ASSET`、`G07_MANIFEST_REQUIREMENT`、`G07_MANIFEST_EVIDENCE`、`G07_MANIFEST_FILENAME`、`G07_MANIFEST_CAPTION` | 单个 result 且 decision=pass；每条 rule 必须 required 或 `G07_*`，outcome=pass、证据非空合法、note 不含 URL；重复允许；required 必须全出现 | 历史/恢复 `_copy_reference_report`（`s10.py:745`）复制旧 review（checks=[]），不调模型；S12 不消费 | #91 |
| S11 `retrieve_original_result`、`review_legacy_item`（历史入口，`s11.py:44-66,169`） | text: `LEGACY_TARGET_IDENTITY/SNAPSHOT/SOURCE_BASIS/CONTENT_UNCHANGED`；prompt: 前三项 + `LEGACY_PROMPT_UNCHANGED`；report: `LEGACY_REPORT_ACCURATE/EVIDENCE/SNAPSHOT`；delivery: `LEGACY_DELIVERY_ASSET/SNAPSHOT/SAFE/CAPTION`；image 动态：`LEGACY_IMAGE_FILE/SNAPSHOT/SIZE` + `PRODUCT` 或 `NO_REFERENCE` + `TEXT` 或 `NO_EXTRA_TEXT` | pass 要求集合完全相等且无重复、每 check pass；非 pass 只拒重复；`_validate_quality:215` 同集合规则；证据非空合法、note 无 URL | `s11_retrieval.py:356/365` 添加历史 review；S12 不消费 | #91 |
| S12 恢复重放 `continue_workflow`、`recover_submission`、`resume_after_restart`、`cleanup_expired`（`s12.py:902-938`） | 复用 S01/S02/S03/S04/S05/S09 的规则与校验器；**无 S07/S10/S11** | 继承对应 stage 的重复实例规则；S12 不能作为独立规则来源 | 本身是恢复入口；规则来源 #88，阶段消费者分别归 #89/#90/#91 | #92（规则来源 #88） |

关键缺口：S07 没有 S12 恢复路径，S12 也没有 S07/S10/S11 分派；因此 #88 补规则目录时必须同时补 S07 的 normal + recovery 两条路径，不能只修正常 `compile_prompt`。S10 的历史复制不调用模型、不重新审查，是历史证据而非当前 review 证据。

## 5. 既有 R1/R2/R3/Release 证据复用性

| 记录 | 可复用的证据 | 不可复用的证据 | 阻塞范围/解除条件 |
| --- | --- | --- | --- |
| `c0-baseline.md`（#53，代码 `555837d`） | 隔离环境、启动方式、后端 426/前端 28 文件 193 的测试选择与命令模板 | 任何当前通过结论、代码/依赖/路由兼容结论 | 只作历史环境参考；当前基线重跑后才可作为门禁前置 |
| R1 系列（`r1-gate.md` + 10 篇，代码 `17fb815`，后端 542、前端 33/206、迁移 0001–0007、59 HTTP/33 OpenAPI） | 身份/归属/项目隔离/上传归属/历史与字节双门禁的矩阵、fixture 选择、HTTP 用例 | 通过记录、59/33 计数、旧路由出口清单、旧 OpenAPI 操作表 | 新 `/projects/:id/chat`、`/projects/:id/results` 与状态投影改变出口；且当前基线有 T03 投影红灯（#93）；R1 门禁须等 #89/#92/#93 修复后重跑 |
| R2 系列（`r2-gate.md` + 5 篇，代码 `2d61d64`，12 定向 16.83s、70 passed 88.46s） | 生成周期、回放/漂移、恢复/重启、额度、并发竞争用例与计数方法 | “漂移反馈”“不重发”“额度不重置”的通过结论、旧 S07 裁决、旧调用计数 | 本轮改 S07/恢复/错误路由；当前 `test_t11_retrieve` 两处字节出口红灯（#91/#92）；R2 须等 #91/#92/#93/#94/#95 修复并重跑 |
| R3 系列（`r3-gate.md` + U1/U2/U3、页面隔离、跨标签、登录体验，代码 `5ced256`，38 文件/173 测试） | U1/U2/U3 的用户行为清单、旧页面隔离/跨标签用例、前端测试选择 | “单一 describe/旧四页收缩”作为新 chat/results shell 的通过证明；旧布局/路由/截图 | 前端目标壳层、单消息流、结果/历史/下载尚未实现或未验证（#101—#108）；R3 结论不得复用 |
| Release 系列（`release-gate.md` + 8 篇，代码 `f8f30d9`/source `2cb9d7f`，后端 643、前端 38/173、37 OpenAPI、包 185 文件/983274 字节） | 发布流程、备份轮换、干净包、HTTPS、恢复、保留期、真实数据演练的命令与检查表 | 任何 release 通过结论、旧包大小/操作数/真实 provider 闭环、物理设备和第二设备结论 | 必须等 P1—P7 全部完成、当前基线 3 红灯关闭，并以本轮 evidence 格式（规则版本、候选预算、调用计数、资源出口）重跑；真实数据根不在本票范围 |

结论：已有记录最多只能复用“测试选择、fixture、命令、行为清单”，不能复用“通过/失败状态”或旧版本计数。旧 issue 关闭、旧文档通过、单测全绿、截图存在，均不等于当前 `072c446` 基线的阶段/发布通过。

调用数比较：本轮所有 workflow 测试使用 `LLMStub`/`VisualStub`，没有真实 provider 调用；`model_text` 未插桩记为 `null`，`model_vision=0`、`image_generation=0`。因此旧 R1/R2/R3/Release 文档中的真实 provider 调用数、图片生成数和包体积不能作为当前基线的可比计数。

## 6. 全体后续 ticket 共用证据格式

每个后续 ticket 必须在 `docs/acceptance/<ticket>.md` 写人读记录，并在 `docs/acceptance/evidence/<ticket>.json` 写机器可读记录。机器 schema 见 `docs/acceptance/evidence/evidence-format-v1.json`；最低字段如下：

```json
{
  "ticket": "#NN",
  "code": {
    "baseline_head": "072c446",
    "branch": "ticket/NN",
    "app_code_head": "<实际被测 sha>",
    "prompt_version": "2.0.0",
    "rules_version": "2.0.0|null",
    "rule_catalog_version": null,
    "migration_head": "0010"
  },
  "environment": {
    "type": "isolated-temp-sqlite",
    "backend_python": "backend/.venv/Scripts/python.exe",
    "data_root": "temp",
    "network": "stubbed"
  },
  "scope": ["normal"],
  "inputs": ["<normal|S12|history / 具体输入类别>"],
  "commands": ["<可复制命令>"],
  "candidate_budget": {"artifact": "prompt_plan", "limit": 3, "used": 0, "reset_scope": "<跨恢复是否重置>"},
  "calls": {"model_text": 0, "model_vision": 0, "image_generation": 0},
  "acceptance": {"WG3-AC-01": "pass|fail|not-covered|unknown"},
  "slots": {"rolling_24h_accepted": 0, "personal_active": 0, "instance_concurrency": 0},
  "results": {"ui": "n/a", "resources": [], "artifacts": []},
  "evidence": {"source_paths": [], "run_ids": [], "commands_output": []},
  "residual_unknowns": []
}
```

硬性要求：

- `code` 必须能唯一回答“在哪个代码/规则版本上测的”；规则目录未引入时 `rule_catalog_version=null`，不能省略。
- `scope`/`inputs` 区分 normal、S12 recover、历史/旧数据；每个入口单独列命令。
- `candidate_budget.used`、`calls.*`、`slots.*` 必须是可计数 double 的**实测值**，不是“应该为 0”；没有计数能力时填 `null` 并在 `residual_unknowns` 说明，不能伪造 0。
- `acceptance` 只允许 `pass|fail|partial|not-covered|unknown`；未覆盖必须显式写 `not-covered`，不能省略。`partial` 只表示已覆盖子项通过、其余子项仍有失败/未知，只能关闭已覆盖的验收 ID，不得把整票标 pass。
- `results` 记录 UI/资源/产物结果；`evidence.source_paths` 必须包含可复现测试、命令输出、运行日志或截图路径；截图/单测绿不能替代全链证据。
- 文档和历史结论不能作为当前完成证据；任何 pass 必须附当前命令/运行记录和计数。

## 7. 本轮实际验证记录（2026-11-02）

| 命令/检查 | 实际结果 |
| --- | --- |
| `git show --stat --oneline 072c446` | 36 files changed, 2105 insertions(+), 182 deletions(-)；基线提交标题正确 |
| `git log -1 --format=%H integration/v3` | `ef92b33`（仅 P0 文档/探针提交，未改 `main/backend/app` 应用代码） |
| `pytest tests/test_v3_p0_baseline.py -q` | `3 passed, 3 xfailed in 1.17s`（严格 xfail 未 xpass） |
| `pytest tests/test_v3_p0_baseline.py -q --runxfail` | `3 failed, 3 passed in 1.18s`，F1/F2/F4 仍复现；F1 `DID NOT RAISE`，F2 `PROMPT_REVIEW_INVALID`，F4 `hard:creative` 仍在 |
| `pytest tests/test_t87_gate_regressions.py -q` | `3 passed, 9 xfailed in 16.57s`（新增 HTTP/workflow 回归；3 个绿色为 WG3-AC-09 的换行精抄、否定翻转、跨句拼接） |
| `pytest tests/test_t87_gate_regressions.py -q --runxfail --tb=line` | `3 passed, 9 failed`；F1 实际 200/approved，F2 实际 503，F3 实际 intent=abandoned，F4 实际 409，F5 实际 `background=None`，数值漂移实际 200/approved |
| 三个已知红灯用例（见 2.2 最后一条 pytest 命令） | `3 failed in 7.76s`；分别是 T03 多出 `submit_requirement`、T11 两次 `RESULT_FETCH_FAILED` |
| `python -m ruff check tests/test_t87_gate_regressions.py tests/test_v3_p0_baseline.py` | 新/改探针文件通过；全 backend `ruff check` 仍有既有 90 个错误（不属于本票改动） |
| 前端源码核对 | `router/index.ts` 无 chat/results 目标路由；`DescribePage.vue` 桌面双列；`AppSidebar.vue` 无搜索/重命名；均为源码级，不是浏览器验收 |

全量 `pytest tests -q` 一次在 600s 工具上限内未完成；`pytest --collect-only -q` 显示 677 个测试。未完成的套件不能写成全绿；本记录只报告已实际执行的命令。

### 7.1 最终合并后复核（integration/v3 8b482ca，#88）

| 检查 | 实际结果 |
| --- | --- |
| 合并 | `git merge integration/v3`：`ef92b33..8b482ca` fast-forward；引入 #88 的 S07 规则目录、S07/S12 裁决与 10 个文件 |
| `pytest tests/test_v3_p0_baseline.py tests/test_t87_gate_regressions.py -q` | `9 passed, 9 xfailed in 17.01s`（严格 xfail 无 xpass；F1 已由普通断言通过） |
| 同上加 `--runxfail --tb=line` | `9 passed, 9 failed in 16.74s`；失败为 F4 纯函数、S05 advisory F2、F3、F4 的 3 个 HTTP 参数、composition 合并、F5、数值漂移；F1 与上游 S07 advisory 已通过 |
| `python -m ruff check tests/test_t87_gate_regressions.py tests/test_v3_p0_baseline.py` | 通过；全 backend 仍有既有 90 个非本票错误 |

结论：#88 只修 S07 的两项必审裁决和 S12 入口；S05 的 advisory 消费者、软硬分层、专用视觉字段、typed reason/原意图、数值覆盖仍分别由 #90/#96/#97/#93/#95 负责。不能因为 #88 合并而把本记录或 F2—F5 标为通过。

## 8. 残留未知与阻塞

- **F5 / #96**：当前只能证明最终卡缺字段；还需 #96 证明全快照跨 S05 确认、S07 编制、S09 QC 和历史原快照一致，WG3-AC-06/12 才算关闭。
- **H1 / #95**：当前 HTTP 证据只覆盖换行精确复制、否定翻转、跨句拼接、350→3500 数值漂移；分号/句号/多句历史候选、数量词、跨无关句等需继续补证，不能因 3 个绿测宣称 AC-09 全过。
- **F3 / #93**：当前用 fake 覆盖值触发的是消息子串/放弃重确认路径；typed reason、原意图文本修复、预算跨恢复不重置仍属 #93/#94。
- **F4/F5 / #96/#97**：软硬分层、用户锁定强度升级、专用视觉字段迁移仍待实现；本轮仅固定当前缺陷与硬/软默认。
- **S07 S12**：`s12.py` 没有 S07 恢复分派，`#88` 必须在补规则目录时同时补 normal + S12；否则恢复路径仍可绕过 F1。
- **前端 FE3-AC-01—15**：除源码级路由/布局/侧栏缺口外，运行行为、浏览器历史、下载、可访问性、真实设备全部 unknown；由 #99—#108 逐项执行。
- **基线 3 红灯**：T03 投影、T11 字节出口根因尚未定位，分别阻塞 #93 与 #91/#92；修复前不得以 R1/R2/R3/Release 旧记录替代。
- **全量套件**：677 tests 的全量结果未知（600s 未完成）；本票不宣称全量通过。
- **候选预算/调用计数**：P0 汇总多个隔离运行，未导出每 run 的 `RevisionBudget.used`；`prompt_plan` 硬上限为 3，本记录不宣称预算通过。`LLMStub` 文本调用未插桩，机器记录 `model_text=null`；未使用真实 provider，`model_vision=0`、`image_generation=0`。
- **真实环境**：未使用真实模型、真实数据根、真实发布/备份/恢复；所有结论仅限隔离临时 SQLite + stub。

## 9. 给 P1（#88）的实施提示

1. **先建服务端规则目录**：为每个 target/阶段登记稳定 rule ID、职责、blocking/advisory、适用性、允许证据类型、规则版本；模型输出只能引用目录，不能自定义/删除必审或自行豁免。
2. **统一裁决**：blocking 必须每个必审规则有合法证据且 pass；missing/fail/unknown/无依据 N/A 全部阻断；服务端显式 N/A 才可接受并附理由；advisory fail/unknown 不推翻已满足的 blocking；未知规则/非法来源/矛盾证据是 typed protocol issue，不是 outage/pass。
3. **S07 专项**：`G03_CONFIRMATION`、`G05_EXECUTION_BOUNDARY` 恒适用；全 N/A/空/缺失都必须 409 `PROMPT_REVIEW_INVALID`；保留正面控制（完整适用 blocking 全 pass）。
4. **补 S12**：把 S07 的 normal 与 recovery 入口都接入同一裁决器；补 checkpoint task 的 required_rule_ids 和恢复测试。
5. **重复实例**：允许多实例时每个实例都必须 pass；后续 pass 不能覆盖先前 fail；非 pass 时重复实例也必须拒绝或明确隔离。
6. **保留 P0 xfail 作为回归**：修好对应票时把 `strict=True` xfail 改为普通断言，不要删除断言；否则无法证明缺陷真的关闭。
7. **错误路由**：不要继续用错误文案子串判断“覆盖值/缺少字面片段”；在 #93 引入 typed reason，并让预算、原意图、技术 vs 远端未知分开。

## 10. 关联文件

- 纯函数 P0 探针：`backend/tests/test_v3_p0_baseline.py`（6 个用例；最终合并后 5 passed / 1 strict xfailed，F4 待 #97）。
- HTTP/workflow 可执行回归：`backend/tests/test_t87_gate_regressions.py`（12 个用例；最终合并后 4 passed / 8 strict xfailed；F1 已转普通回归）。
- 机器可读本记录：`docs/acceptance/evidence/p0-baseline.json`。
- 通用证据 schema：`docs/acceptance/evidence/evidence-format-v1.json`。
- 原始命令输出：`docs/acceptance/evidence/t87-verification.txt`。
