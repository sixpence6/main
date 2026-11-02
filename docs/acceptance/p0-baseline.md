# P0 基线与审查消费者清点（#86 / ticket #87）

## 1. 结论与边界

- 本记录只做四件事：保存用户既有改动、给出历史发现的当前状态分类、清点审查消费者与批次归属、判定既有 R1/R2/R3/Release 记录哪些证据可复用。
- **本记录不预填任何通过记录。** 下文凡是"未验证"的地方都显式标注，不用旧结论代替当前证据。
- 未改动应用配置、真实数据根、部署方式；未新增依赖、未改业务目录内的测试或证据；临时目录与探针都在 `backend/tests/`、`docs/acceptance/`、`.scratch/` 中。

## 2. 代码版本与环境

| 项 | 值 |
| --- | --- |
| 集成分支 | `integration/v3` |
| 基线提交 | `072c446` "chore(baseline): capture v3 gate/workbench baseline working tree (#86)" |
| 上一提交 | `b0ae81c` "Merge ticket/85: release gate acceptance (#85)" |
| 基线规模 | 36 个文件，+2105 / −182（33 个已跟踪修改 + 3 个新增门禁/前端/整改文档） |
| 规则/契约版本 | `prompt_version = "2.0.0"`（`app/workflow/prompts.py`），批准绑定在 `workflow/access.py:305`、`s10.py:200/249`、`s11.py:507` 校验为 `"2.0.0"`；规则目录版本随 P1 #88 引入 |
| 后端解释器 | `D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe`（工作目录 `backend/`，`testpaths=["tests"]`、`pythonpath=["../main/backend"]`） |
| 前端 | `main/src`（Vue3 + Vite，`npm run test` 走 vitest） |

### 2.1 基线保存方式

用户在工作区已有的改动**不能**只靠 HEAD 复现，因此 P0 先把它们固化成一次提交，后续 worktree 才看得到真实基线：

- 备份副本：`.scratch/baseline-20261102/`（`HEAD.txt`、`status.txt`、`tracked.diff` 266 KB、`diffstat.txt`、`docs/architecture/*.md` 副本）。
- 固化提交：`072c446`，包含 13 个 `backend/tests/*.py`、`AGENTS.md`、`docs/architecture/hld_v3.md`、`docs/architecture/{frontend-design-v3,workflow-gates-v3,workflow-remediation-plan-v3}.md`、后端 `adapters/{base,openai_chat,openai_images}.py`、`maintenance/{backup,manifest}.py`、`services/generations.py`、`workflow/{contracts,dispatch,state}.py`、`workflow/stages/{s01,s03,s04,s05,s07,s08,s09,s12}.py`、`main/src/components/workflow/WorkflowPanel.vue`、`vesion/v3/SRS_v3.md`。
- 工作区未跟踪的 `DAG依赖图.png/.svg`、`ecommerce-image-workspace-main.zip`、`backend/pytest-of-*` 与项目无关，保持未跟踪，不进入任何提交，也不进 `main/`。

### 2.2 基线已知红灯（重要）

基线不是全绿。`backend` 目录下下列 3 个既有用例在当前基线上失败，与本轮整改的验收位点无关，但必须显式记账，避免后续票被误判、也避免结案时用"全绿"掩盖：

| 用例 | 现状 | 与整改的关系 |
| --- | --- | --- |
| `tests/test_t03_intent_review.py::test_failed_s01_review_can_be_continued_from_completed_checkpoint` | 失败：受阻运行的 `allowed_actions` 多出 `submit_requirement`（期望仅 `continue_workflow`） | 属 WG3-AC-10 安全状态投影范围，归属 T07 #93 处理 |
| `tests/test_t11_retrieve.py::test_failed_download_can_retrieve_same_version_and_downloads_real_file` | 失败：重取返回 502 `RESULT_FETCH_FAILED`（"重取结果无法作为图片保存"） | 属资源字节出口范围，归属 T05 #91，由 T06 #92 复核 |
| `tests/test_t11_retrieve.py::test_retrieval_stays_failed_while_waiting_and_blocks_other_write` | 同上（同一字节出口） | 同上 |

复现命令：

```
cd D:\Desktop\新建文件夹\backend
.\.venv\Scripts\python.exe -m pytest tests/test_t03_intent_review.py::test_failed_s01_review_can_be_continued_from_completed_checkpoint "tests/test_t11_retrieve.py::test_failed_download_can_retrieve_same_version_and_downloads_real_file" "tests/test_t11_retrieve.py::test_retrieval_stays_failed_while_waiting_and_blocks_other_write" -q
```

结论：`3 failed in 4.37s`。这两处必须在对应票内修好，否则 R2/R3/Release 复验不得声称通过。

## 3. 历史发现分类

状态口径：**still-reproducing**（当前基线可复现）；**changed-needs-verification**（代码已变，需要运行进程侧复核）；**has-current-evidence**（当前已有可复现证据）；**unknown**（证据不足）。

| 编号 | 描述 | 状态 | 最小复现场景 | 验收 ID | 归属 |
| --- | --- | --- | --- | --- | --- |
| F1 | S07 两条必审规则全部 `not_applicable`（或空证据）仍被判 pass 并发出提示词批准 | **still-reproducing** | `tests/test_v3_p0_baseline.py::test_f1_s07_all_not_applicable_cannot_be_approved`（strict xfail）：两条规则均 `not_applicable`、decision=pass，`s07._validate_review` 未拒绝 | WG3-AC-01 | T02 #88 |
| F2 | blocking 全 pass，但额外 advisory 的 fail/unknown 让整体被拒 | **still-reproducing** | `tests/test_v3_p0_baseline.py::test_f2_s07_advisory_failure_does_not_block`（strict xfail）：两条 blocking pass + `advisory:aesthetics=fail`、`advisory:lighting=unknown`、decision=pass 被拒 | WG3-AC-02 | T02 #88；同型 5 阶段 10 组合由 T03/T04/T05 各自覆盖 |
| F3 | 覆盖/字面片段错误按错误消息子串路由到撤销+重新确认，而不是类型化原因 | **still-reproducing** | `s07._compile` 里 `any("覆盖值" in reason or "缺少字面片段" in reason)` → `_reconfirm_prompt(...)`；任何同文案的其他错误都会被误路由。复现：让生成角色返回 `requirement_coverage` 值被篡改 → 返回 409 `PROMPT_REQUIRES_RECONFIRMATION` 并放弃意图，而不是按原意图定点修复 | WG3-AC-07/08 | T07 #93 |
| F4 | 通用创意/风格/光线被登记为硬约束，`composition` 又混入偏好项 | **still-reproducing** | `tests/test_v3_p0_baseline.py::test_f4_creative_style_lighting_are_not_hard_requirements`（strict xfail）：`_hard_requirements` 产出 `hard:creative/hard:style/hard:lighting/hard:composition` | WG3-AC-05/06 | T10 #96（软硬分层）、T11 #97（快照字段） |
| F5 | 视觉方案只落在 `background/palette` 值里，执行快照丢字段 | **unknown**（当前基线未见代码级证据，需 T11 #97 用快照落地对比验证） | 需对比 S05 产物字段与 S07/S09 读取字段的字段映射表；当前 `_hard_requirements` 只读 `product_description/purpose/creative_direction/style_keywords/composition/lighting/texts/facts/must_include/must_preserve/forbidden/size/count/reference_asset_id`，没有 `background/palette/text_layout/reference_handling` 专用字段 | WG3-AC-06 | T11 #97 |
| H1 | 多句需求原样抄写仍被判未覆盖 | **changed-needs-verification** | 代码侧已按分句 + 单句内保序子序列实现（`_covers_requirement` → `_keeps_characters_in_order`），`tests/test_v3_p0_baseline.py::test_h1_*` 三条在基线上通过；**运行进程侧尚未复核**（旧记录来自外部编辑前） | WG3-AC-09 | T09 #95 |
| FE-1 | 单一 composer、跳转抖动、移动端/输入法/可达性问题 | **unknown**（前端侧未做逐项复现） | 见 `docs/architecture/frontend-design-v3.md` 的 FE3-AC-01—15；需在 T13 #99 / T14 #100 用现有 App/工作台交互测试逐条落证 | FE3-AC-01—15 | T13 #99、T14 #100 |

说明：F1/F2/F4 的 strict xfail 探针在对应票修好后会转为失败（xpass→failed），届时**必须由该票删除标记并保留断言**，这是设计意图，不是回归。

## 4. 审查消费者清单与批次归属

服务端目前**每个阶段各自实现一份评审校验**，这是 F1/F2/F3 的结构性原因：

| 消费者 | 位置 | 现象 | 迁移批次 |
| --- | --- | --- | --- |
| S01 输入/范围评审 | `workflow/stages/s01.py:199` `_validate_review` | 自建规则集合，无 applicability/证据类型概念 | T03 #89 |
| S02 参考识别评审 | `s02.py:44` `REQUIRED_REVIEW_RULES` + 校验 | 同上；参考图多实例未逐实例校验 | T03 #89 |
| S03 需求评审 | `s03.py:579` `_validate_review` | 同上 | T03 #89 |
| S04 创意评审 | `s04.py:382` `_validate_review_shape` | 同上 | T04 #90 |
| S05 视觉评审 | `s05.py:536` `_validate_review` | 同上 | T04 #90 |
| S06 最终需求卡 | `s06.py:180` `_hard_requirements` | 卡片侧硬项生成，无规则版本 | T04 #90 |
| S07 提示词评审 | `s07.py:311` `_validate_review` + `s07.py:163` `_hard_requirements` | F1/F2/F4 根因 | T02 #88（评审）／T10 #96、T11 #97（硬项与快照） |
| S09 图片质检 | `s09.py:603` `_validate_review` + `_validate_quality` + `_RULES` 表 + `_ADVISORY_REVIEW_RULE` 正则 | 唯一已有服务端规则表，但用正则授予 advisory 身份（须删除） | T05 #91 |
| S10 图片/交付批准 | `s10.py:263` `_validate_review` | 自建校验 | T05 #91 |
| S11 交付清单/发布 | `s11.py:169` `_validate_review` + `LEGACY_REPORT_RULES/LEGACY_DELIVERY_RULES` | 历史兼容规则集，迁移时须显式保留 | T05 #91 |
| S12 恢复入口 | `s12.py` `_resume_review`（S01/S03/S04/S02/S05/S09）、`_resume_producer`（S01/S03/S04/S05/S02/S09） | **没有 S07 恢复路径**；恢复走各 stage 的同一校验函数，因此迁移必须同时覆盖正常与恢复两条路径 | T02 #88（补 S07）、T03 #89、T04 #90、T05 #91 |

正常/恢复/历史三类入口与批次归属：正常（逐阶段 HTTP 入口）归各 stage 批次；S12 恢复归同一批次；历史/旧数据读取归 T05 #91（按原快照解释）。

## 5. 既有 R1/R2/R3/Release 记录的可复用性判定

| 记录 | 内容 | 复用判定 | 理由 |
| --- | --- | --- | --- |
| `r1-gate.md`、`r1-*.md`（10 篇） | 全出口权限、账号、会话、项目隔离、上传归属、历史/字节双重门禁 | **身份与归属矩阵可复用为回归清单，结论不可复用** | 本轮新增 `/projects/:id/chat`、`/projects/:id/results` 路由与新的状态投影，出口清单已变；且基线已有 T03 投影红灯 |
| `r2-gate.md`、`r2-replay-drift.md`、`r2-recovery-restart.md`、`r2-generation-cycle.md`、`r2-local-closure.md`、`r2-generation-quota.md` | 竞争/回滚/不重发、原意图重放与漂移、恢复与重启、闭环、额度 | **不可复用为门禁结论，用例可复用为回归** | 本轮改的正是 S07 的裁决与故障路由，R2 的"漂移反馈"结论建立在旧裁决之上；`test_t11_retrieve` 已出现字节出口红灯 |
| `r3-gate.md`、`r3-u1/u2/u3`、`r3-page-isolation.md`、`r3-cross-tab-history.md`、`r3-auth-experience.md` | 工作台、原位授权、交付、页面隔离、跨标签、登录体验 | **UI 行为清单可复用，结论不可复用** | 前端将改为单一壳层 + 只读结果路由（T13/T14/T15/T16），旧路由结构变化 |
| `release-gate.md`（32 KB）与 `release-*.md`（8 篇） | 发布关口总表、备份轮换、干净包、HTTPS、恢复、保留期、真实数据演练 | **流程与命令可复用，通过结论不可复用** | 发布关口必须以本轮 V3-Release 的 evidence（含规则版本、候选预算、调用计数）重跑；旧表未含新证据字段 |

## 6. 各票通用证据格式（后续每票必须遵守）

每票在 `docs/acceptance/<ticket>.md` 写人读记录，并在 `docs/acceptance/evidence/<ticket>.json` 写机器可读记录，至少包含：

```json
{
  "ticket": "#NN",
  "code": {"integration_head": "<sha>", "branch": "ticket/NN", "rule_version": "<catalog version or null>"},
  "environment": {"type": "isolated-temp-sqlite", "backend_python": "backend/.venv", "data_root": "temp", "network": "stubbed"},
  "inputs": ["<输入类别：如 normal / S12 recovery / history>"],
  "commands": ["<可复制的命令>"],
  "candidate_budget": {"artifact": "prompt_plan", "limit": 3, "used": 0},
  "calls": {"model_text": 0, "model_vision": 0, "image_generation": 0},
  "acceptance": {"WG3-AC-01": "pass|fail|not-covered", "...": "..."},
  "slots": {"rolling_24h_accepted": 0, "personal_active": 0, "instance_concurrency": 0},
  "results": {"ui": "n/a", "resources": ["<字节出口断言>"]},
  "residual_unknowns": ["<未验证项>"]
}
```

要求：`calls` 必须是可计数 double 的实测值（不是"应该为 0"）；`acceptance` 里 `not-covered` 必须显式出现，不能省略；`residual_unknowns` 为空也要写 `[]`。

## 7. 残留未知

- H1 仅在代码侧验证，运行进程侧（真实 HTTP + 角色 stub）尚未复核。
- F5 没有当前代码级证据，需要 T11 #97 的字段映射表逐项核对后才能定级。
- 前端 FE3-AC-01—15 尚未逐条复现，全部为 unknown。
- 基线 3 个红灯用例的根因尚未定位（只确认现象与归属票）。
- 真实模型/真实数据根相关结论一律不在本记录内（本轮不触碰真实数据根与部署）。
