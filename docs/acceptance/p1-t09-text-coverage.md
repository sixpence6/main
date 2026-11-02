# P2 / T09 — 多句原样覆盖与语义反假通过（#95）

- 票：**#95（T09，P2）**；父问题 **#86**
- 工作树：**D:\Desktop\新建文件夹\.worktrees\t95**；分支 **ticket/95**
- 基线：**integration/v3 cb478a7**；T09 代码提交 **a5f38f8**；合并 integration/v3 **2846845**（合并时 tip **b304649**）
- 规则目录：**v3.rules.1**；提示词版本 **2.0.0**；迁移头 **0011**
- 环境：隔离临时 SQLite、计数型 **LLMStub** / **VisualStub**、认证 HTTP 客户端、网络全部 stub；无真实 provider、无真实图片生成
- 证据 JSON：**docs/acceptance/evidence/p1-t09.json**

## 1. 结论

本票把 S07 的覆盖判断从“规范化字符子序列”改为**分句、分词、数值原子化**的覆盖判断：需求分句必须在提示词某一个分句中以连续 token 出现。分号、句号、中文标点、ASCII 句点和换行只作为分句/分词边界，不再造成原样覆盖漏判；字符子序列、跨句拼接、插入非白名单字词、数值/数量变化、否定翻转和来源边界合并均不能通过。

自然语言允许的只有显式连接词 **四周 / 保留**。语序重排或连接词插入会生成 **coverage_explanations**（**word_order** / **connective**），并随候选一起送入独立 **output_reviewer**；没有 reviewer pass 不能投影为 approved。真正漏覆盖被分类为 **REPAIRABLE_COMPOSITION**，仅在已有原意图时使用一次有界自动修复；数值等危险漂移保持 **TRUE_DRIFT**，放弃原意图并回新版本确认。未新增预算、授权、硬项或 evidence 旁路。

## 2. 实现要点

- **s07.py**：新增分句器 **_CLAUSE_SPLIT**、token 化 **_COVERAGE_TOKEN**、数值 token 原子匹配、极性 token 计数、显式连接词白名单、**_assess_requirement** / **_coverage_explanations**；覆盖项和缺失项分别给出 **content_intact**，并将 **coverage_explanations** 写入 output_reviewer 输入和 **prompt_coverage_review** 诊断。
- **reasons.py**：**_AUTO_REPAIRABLE_DEFECTS** 纳入 **MISSING_COVERAGE**，但仍要求 **content_intact=True**；非完整内容或硬边界仍走 **TRUE_DRIFT**。
- **s07.py 自动修复门**：只在有 owned intent，或属于既有“覆盖值映射错误”场景时允许自动修复；未拥有意图的缺字/删除文本仍一次模型调用后返回 409 **PROMPT_INVALID**。
- **t87**：移除 numeric 350→3500 的 **xfail**，保留其为真实回归用例。

## 3. 验收覆盖（票面七项）

| 票面验收 | 当前证据 |
| --- | --- |
| 1. 分号/句号/换行原样复制不漏覆盖，保留 H1 | **test_exact_multi_sentence_copy_with_punctuation_and_newline_is_covered** 两个参数化 HTTP 用例（中文标点、ASCII 标点、换行）均为 200 approved；T87 H1 用例在回归组保持绿色 |
| 2. 缺字、数值/数量、否定、来源边界、跨句拼字不假通过 | 缺字、350→3500、两件装→三件装、否定翻转、双重否定、跨句插字、合并来源分句、拆分需求分句共 8 个 negative HTTP 用例均 409 **PROMPT_INVALID**，reviewer 与图片提交均为 0 |
| 3. 语序/连接词变化需可解释覆盖与独立语义检查 | **test_connective_wording_has_explainable_coverage_and_independent_review** 和 **test_word_order_change_has_explainable_coverage_and_independent_review** 检查 reviewer 输入包含 **connective** / **word_order**；reviewer reject 用例返回 409 **PROMPT_REVIEW_REJECTED** |
| 4. 真正漏覆盖进入原意图有界修复；危险漂移回新版本 | owned true omission：intent 未放弃、1 usage、3 slots 未释放、预算 used=2、一个 approved revision 2、1 次图片提交；owned numeric drift：intent abandoned、slots 释放、无 artifact、预算 used=1、无图片 |
| 5. HTTP 受理→候选→独立审查→结果投影 | 15 项 HTTP/工作流用例走认证客户端、真实临时 SQLite、计数 stub；断言 RevisionBudget、intent/usage/slots、artifact status/revision、diagnostics 和 VisualStub **submit_calls** |
| 6. WG3-AC-09 当前证据且不绕过约束 | 上表 1–4 即 WG3-AC-09 正反证据；S07 仍经 **_require_confirmed**、**_ensure_budget**、硬项 size/reference 和 G03/G05 独立审查；未新增绕过路径 |
| 7. 使用既有 seam；测试/临时/证据在业务目录外；列明基线、命令、计数、残余未知 | 复用 T40 **environment/setup_confirmed_project/candidate_data**、T65/T66 **_environment/_setup_s06/_drain**；测试在 **backend/tests**，证据在 **docs/acceptance**；本文与 JSON 记录基线、命令、预算/调用和残余未知 |

## 4. 实际验证记录

均在 **D:\Desktop\新建文件夹\.worktrees\t95\backend** 下执行，Python 为 **D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe**。

| 命令 / 范围 | 结果 |
| --- | --- |
| **pytest tests/test_v3_p1_t09_text_coverage.py -q** | **15 passed in 17.86s** |
| **pytest p0 baseline + T87 + T40 + T07 -q** | **39 passed, 7 xfailed in 50.32s** |
| **pytest P1 t02–t09 -q** | **102 passed in 95.65s (0:01:35)** |
| **pytest t65 + t66 + t03 -q** | **34 passed in 52.26s** |
| **pytest tests/test_v3_p1_t08_recovery.py -q** | **9 passed in 14.38s** |
| **ruff check**（s07.py、reasons.py、T09 测试、T87 测试） | **All checks passed!** |

实现前红灯记录：**9 failed, 4 passed in 19.34s**（当时 13 个用例；之后补了 merged-source 与 connective 两个用例，当前 15 个用例全绿）。该记录是缺陷证据，不是当前失败。

### 4.1 调用 / 预算对照（计数型 stub）

| 场景 | generation_engineer | output_reviewer | 图片提交 | prompt 预算 used |
| --- | ---: | ---: | ---: | ---: |
| exact 多句（2 参数） | 2 | 2 | 0 | 1 |
| negative 8 项 | 8 | 0 | 0 | 1 |
| connective | 1 | 1 | 0 | 1 |
| word-order pass | 1 | 1 | 0 | 1 |
| word-order reject | 1 | 1 | 0 | 1 |
| owned true omission | 2 | 1 | 1 | 2 |
| owned numeric drift | 1 | 0 | 0 | 1 |
| **合计** | **16** | **6** | **1** | **最大 2** |

- **model_text = 22**（16 generation_engineer + 6 output_reviewer）；**model_vision = 0**；**image_generation = 1**（仅 owned true omission 的 VisualStub submit）。
- 预算上限为 **3**，T09 最大观测为 **2**；T09 未覆盖 3 槽耗尽，T07 已覆盖。所有计数为 stub 观测，不是生产 token/延迟/费用。

## 5. 已知红色与残余未知

**已知红色（T07 记录，T09 未重跑，不作为本票通过证据）**：**test_t11_retrieve.py** 两项、**test_v2_t48_retrieval.py** 两项、**test_t83_retention_secrets.py** 一项，均为既有 retrieval / 环境数据根基线。

1. **未重跑全量 pytest**：当前证据为 15 项 T09、102 项 P1、34 项核心工作流和 39 项 focused 回归组，不能声称全量套件通过。
2. **计数为 stub 观测**：真实 provider 的 token、延迟、费用和图片生成未测量。
3. **reviewer 为脚本化 stub**：语序/连接词的真实模型语义判断未测量；当前只证明 explainable coverage 被传给独立审查且拒绝可阻断。
4. **连接词白名单很窄**（四周 / 保留）：其他自然语言连接词会被保守拒绝，后续需要独立证据再扩展。
5. **标点范围有限**：覆盖中文/ASCII 分号、句号、换行，不包含所有 Unicode 标点形式。
6. **无 T09 专用授权负例**：所有请求走认证客户端，既有 T07/T40 确认门禁保持；授权拒绝本身由既有账号/归属测试覆盖。
7. **合并后 integration/v3 又前进到 f782daf**（仅项目搜索/改名，不涉及 S07）；按 lead 指示未再次合并。
8. **临时文件**：工作树中 **backend/pytest-of-unknown** 已清理；lead 提到的 **tmpoeuf3ulw / tmpxhdbiee9** 在当前工作树和主根目录均未找到。

本文件不构成 v3 发布或全量通过声明；完成判断以当前 HTTP T09 用例和上述实际命令结果为准。
