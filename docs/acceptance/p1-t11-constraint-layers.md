# P3 / T11 — 硬约束分层、软偏好与强度重确认（#97）

- 票：#97（T11，P3）；父问题 #86；集成分支基线 **eddea7e**，本票从 **87d69a9**（T10 代码合并）展开
- 分支：**ticket/97**；本票 HEAD：**760e610**
- 快照：schema **1.1.0**（仍可读取 1.0.0），contract **1.0.0**，规则版本 **v3.rules.2**
- 迁移头：**0011_recovery_claims**（T11 不新增关系列，无迁移）
- 关键文件：**main/backend/app/workflow/snapshot.py**、**contracts.py**、**stages/s05.py**、**s06.py**、**s07.py**、**s09.py**、**backend/tests/test_v3_p1_t11_constraint_layers.py**
- 证据：**docs/acceptance/evidence/p1-t11.json**

## 1. 实现摘要

T11 把“已确认内容”和“硬约束判定”拆成两层。硬约束只包含商品身份/事实、准确文字、必须出现、必须保留、明确禁止、输出尺寸/数量、参考图绑定和参考用途。风格、氛围、背景、配色、光影、构图、文字布局、参考处理和模型创意默认是软偏好。软字段仍然进入 final_brief 和 S07 的可执行内容，但不作为图片合格与否的硬门禁。

用户明确锁定某个一般审美字段时，确认 payload 的 **user_locks** 以“字段路径 -> 用户依据”的形式记录；快照只升级该字段，并同时写入 **strength=hard**、**user_lock_basis** 和稳定 **constraint_id**。没有用户依据的模型建议、保留规则或字段类别不会自动升级。

S07 从确认快照读取 hard requirements；对没有快照的旧批准保留兼容路径并显式返回旧语义。S09 在传入快照时仅启用适用的硬规则，**不会再把 style_keywords／lighting／background／palette／text_layout／reference_treatment 合并进 hard:composition 兜底否决图片**。不传快照的旧调用者仍得到七个 legacy 规则，以保持既有契约测试。

## 2. 强度变更与历史

确认快照的 digest 覆盖 brief、user_locks、strength、constraint_id、artifact/revision/reference 绑定。增加、删除或修改用户锁定都会改变 snapshot digest；S06 的 **_matches_existing** 比较 artifact digest、requirement revision、reference 绑定和 user_locks，不匹配时旧批准不能复用。发布历史按每个版本自己的 final_brief digest 与 requirements_card approval 取回原快照，不读取当前项目列，也不改写旧 digest。

T11 的 HTTP 测试先通过真实路由确认一次卡片，再验证：

- 同卡同 user_locks 可以匹配；
- 同卡增加/修改 user_locks 返回不匹配，旧批准不能移植；
- 工作流视图只保留原来的 active requirements_card，其 confirmation.user_locks 为空；
- 快照中 background/composition 仍为 soft，style_keywords 在锁定后为 hard 并带依据。

## 3. 验收映射

| 验收点 | 结论 | 证据 |
| --- | --- | --- |
| WG3-AC-04 硬／软分层 | **通过** | 默认 10 个硬路径、11 个软路径；S07/S09 使用同一快照；软项不进入 hard:composition |
| WG3-AC-05 显式锁定与重确认 | **通过** | user_locks 进入确认 payload 与快照；digest/approval 绑定变化后旧批准不可移植；历史保留原快照 |
| 确认卡/安全投影区分必须与偏好 | **通过（后端）** | WorkflowView 的 execution_snapshot 字段暴露 strength、user_lock_basis、constraint_id；不新增原始评审文本 |
| 仅软项 unknown 可继续 | **通过（规则层）** | 默认 soft composition 规则 applicability=not_applicable；T10 HTTP 流程在软项未知情况下继续编制/质检 |
| 硬项 unknown 仍隔离 | **共享边界** | 既有 S09 硬证据校验负责隔离；T11 将锁定项纳入硬集合，并保留该路径 |
| 历史原语义 | **通过** | 每个发布版本读取自己的 snapshot；T10/T11 HTTP 回归覆盖发布后修改项目仍读旧快照 |

## 4. 实际命令与结果

工作目录：**D:\Desktop\新建文件夹\.worktrees\t97**；Python：**D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe**。

| 命令 | 结果 |
| --- | --- |
| ruff check（snapshot、contracts、s05、s06、s07、s09、T11/T10/T09/T87/P0 测试） | **All checks passed!** |
| pytest tests/test_v3_p1_t11_constraint_layers.py -q | **4 passed in 1.60s** |
| pytest T11+T10+T09+T06+T40+T87+P0+T08 -q | **74 passed, 1 xfailed in 78.01s** |

T11 定向套件本身不调用模型或图片服务；共享 T10 HTTP 流程的计数 stub 观测为 **model_text=6、model_vision=0、image_generation=1**，候选数为 1，确认重放不会创建第二个候选。T11 不重置 RevisionBudget，预算持久化由 T07/T08 负责。

## 5. 残余未知

1. 没有真实 provider 网络调用；模型调用次数来自计数 stub，不代表真实 token、延迟或费用。
2. 未在本票重跑完整 backend 全量套件；证据边界是 74 项定向回归加此前 T08/T09/T10 的 focused 结果。
3. 前端确认卡尚未在浏览器中区分“必须/偏好”；后端已提供 strength 与 user_lock_basis，UI 验收属于后续 UI 票。
4. 硬项 unknown 的完整矩阵由既有 S09 证据测试覆盖，T11 没有新增一套独立的 unknown 证据矩阵。
5. T11 的 HTTP 测试停在确认/快照绑定；完整确认→编制→质检→发布由更新为 schema 1.1.0 的 T10 HTTP 流程共享覆盖。

## 6. P3 入口

T11 完成后，P3 的下一票是 T12（#98）legacy 兼容与 R1/R2 合并门禁。T12 必须保留快照 1.1.0/1.0.0 双读、旧批准不重写、临时库沿迁移链升级与失败原子性，再进入 P4。
