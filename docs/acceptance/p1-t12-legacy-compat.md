# P3 / T12 — 旧卡、旧批准与恢复检查点的只读兼容（#98）

- 票：#98（T12，P3）；父问题 #86；集成分支基线 **53c0c9f**，本票从 **53c0c9f** 展开
- 分支：**ticket/98**；实现提交 **3b33aaa**（docs 证据另提一提交）
- 快照：schema **1.1.0**，仍可只读识别 **1.0.0**；contract **1.0.0**；规则版本 **v3.rules.2**
- 迁移头：**0011_recovery_claims**（本票把 SQLite 双列变更改为 batch recreate，保证原子）
- 关键文件：**main/backend/app/workflow/stages/s09.py**、**stages/s12.py**、**backend/migrations/versions/0011_recovery_claims.py**、**main/backend/migrations/versions/0011_recovery_claims.py**、**backend/tests/test_v3_p1_t12_legacy_compat.py**
- 证据 JSON：**docs/acceptance/evidence/p1-t12.json**

## 1. 实现摘要

T12 不新造一套“兼容格式”，而是把“能否解释、能否验证”作为读取旧数据的唯一入口：

- 没有 execution_snapshot 的旧 requirements_card 批准继续按旧语义工作；S07 走无快照的 legacy 路径，不补写快照，也不把旧批准升级成新强度模型。
- 已存储的 **1.0.0** 快照可被 **1.1.0** 代码只读校验和读取；旧快照里的 hard 字段（包括 style/composition/lighting）按旧语义仍为 hard，digest 不重算、不重写，历史返回的 digest 与原始 checks_json 保持一致。
- 未识别版本（如 **9.9.9**）返回 **SNAPSHOT_VERSION_UNSUPPORTED**；**1.1.0** 缺少 **user_locks** 返回 **SNAPSHOT_LOCKS_MISSING**。不猜测、不填默认、不把缺字段当成“空锁”静默升级。
- 正常 S09 与 S12 恢复读取的是同一个 requirements_card 冻结快照：两者使用同一套 hard 规则、同一份 rule payload。恢复不能借“重启”为由重新解释授权，也不能把旧批准重新拼成新规则。
- 项目/消息/资源的所有权与访问边界不变；本票没有新增公开读接口，也没有放宽任何命令权限。

## 2. 旧批准与恢复的语义

### 2.1 旧卡与旧快照

| 输入 | 行为 |
| --- | --- |
| approval 无 snapshot | 返回 None；S07 旧路径按旧 hard 集合执行，历史与批准原样保留 |
| 1.0.0 snapshot | 通过版本校验；requirements_from_snapshot 继续生成旧的 hard 规则（例如 hard:style）；digest 与 checks_json 不变 |
| 未知 snapshot 版本 | 拒绝，reason 为 SNAPSHOT_VERSION_UNSUPPORTED，不进入生成 |
| 1.1.0 缺 user_locks | 拒绝，reason 为 SNAPSHOT_LOCKS_MISSING，不静默补空锁 |
| 1.1.0 正常快照 | 按字段 strength/user_lock_basis/constraint_id 读取，soft 项不进入 hard:composition |

### 2.2 正常编译与恢复编译

test_normal_and_recovery_use_the_same_frozen_snapshot 同时调用正常路径的 s09._rule_specs / s09._rule_payload 与恢复路径的 s12._s09_recovery_inputs，并将两边的 rule 列表、requirement 文本和 applicability 做相等比较。测试使用一个带显式 style 锁的快照，要求：

- 正常与恢复都产生同一个 **hard:composition**（锁定的 style 参与）；
- 两边 requirement 都包含“极简”，不得只出现在某一侧；
- 恢复 payload 中带出的 **execution_snapshot** 与正常路径是同一份对象内容；
- 恢复不会重新调用 S06、不会重写 approval，也不会把锁降级或升级为另一套规则。

## 3. 迁移原子性

0011 的两列（recovery_claim_id、recovery_claimed_at）在 SQLite 上必须一次表重建完成。实现使用 batch_alter_table("workflow_runs", recreate="always")，在同一批操作中添加两列。

测试覆盖两条路径：

1. **成功升级**：从 0010 升级到 head，alembic_version 为 0011，两个 recovery 列存在；旧 approval 的 checks_json 字节完全一致，且 execution_snapshot_from_checks 仍返回 None。
2. **失败升级**：预先放置 _alembic_tmp_workflow_runs 触发 batch 失败，升级抛出 sqlalchemy.exc.OperationalError；alembic_version 仍为 0010，workflow_runs 上不存在 recovery_claim_id，不留下半套列。

这条测试证明“版本与表结构一起回退”，不把失败迁移留在半完成状态；它不是断电测试，也不替代生产备份演练。

## 4. R1 / R2 合并门

- T14 新增的 **PATCH /api/projects/{project_id}** 已加入 test_t61_history_recovery_gate.py 的 PROTECTED_ROUTES；R1 精确路由清单从 37 项更新为 **38 项**，默认拒绝校验通过。
- R2 资源测试仍然覆盖项目、消息、资源、生成任务、投递与恢复的所有权/隔离；本票没有放宽任何路由或资源谓词。
- 同 id 不同参数仍拒绝，同参数重放仍返回原 intent；预算跨恢复保留、已接受失败不退款、submitted/unknown 永不重发，均由既有 T07/T08/T62/T66/T68 套件回归覆盖。

## 5. 验收映射

| 验收点 | 结论 | 证据 |
| --- | --- | --- |
| WG3-AC-01 | 通过（P1 回归） | 116 项 P1 全绿；S07 全 N/A、空证据、缺失审查仍由既有用例覆盖 |
| WG3-AC-02 | 通过 | P1 回归覆盖 mandatory-pass 与 advisory pass/fail/unknown/N/A；T12 比较正常/恢复同一 payload |
| WG3-AC-03 | 通过（P1 回归） | 规则伪造、非法来源、证据冲突仍在 S03/S04/S09 用例中，不放宽 advisory |
| WG3-AC-04 | 通过 | 软项 unknown 不隔离；硬项 unknown 仍隔离；T11/T10 用例回归 |
| WG3-AC-05 | 通过 | user_locks 只升级指定字段并带依据；变更后 digest 改变，旧批准不可移植 |
| WG3-AC-06 | 通过 | T10 专用视觉字段 + T12 正常/恢复同快照；1.0.0 历史只读不改写 |
| WG3-AC-07 | 通过（P1 回归） | T07/T08 有界文本修复和恢复账本全绿；T12 不重置预算 |
| WG3-AC-08 | 通过（P1 回归） | T08 远程未知/不重发/重启/技术失败覆盖仍绿；T12 不加重发路径 |
| WG3-AC-09 | 通过（P1 回归） | T09 多句精确覆盖与防伪通过全绿 |
| WG3-AC-12 | 通过 | T10/T11 历史用例 + T12 旧批准只读；历史读自己的快照，不改写旧 digest |
| R1 默认拒绝 | 通过 | 38 项精确路由（含 PATCH 项目），匿名仍 AUTH_REQUIRED |
| R2 资源隔离 | 通过 | 58 项隔离/所有权/配额/槽位/恢复/不重发回归全绿 |

## 6. 实际命令与结果

工作目录：**D:\Desktop\新建文件夹\.worktrees\t98\backend**；Python：**D:\Desktop\新建文件夹\backend\.venv\Scripts\python.exe**。

| 命令 | 结果 |
| --- | --- |
| pytest tests/test_v3_p1_t12_legacy_compat.py -q | **6 passed in 1.69s** |
| pytest 全 P1 T02–T12 -q | **116 passed in 96.31s** |
| pytest tests/test_t64_r1_gate.py -q | **6 passed in 11.08s** |
| pytest tests/test_t70_r2_gate.py tests/test_t61_history_recovery_gate.py -q | **16 passed in 26.18s** |
| pytest t59/t60/t61/t62/t63/t65/t66/t67/t68/t69/t71 -q | **58 passed in 106.58s** |
| ruff check s09/s12/两处 0011/T12 测试 | **All checks passed!** |

T12 定向套件不调用模型或图片服务；P1 回归使用计数 stub，但没有把计数汇总成统一账本，故本文件不把它写成真实 provider 账单。候选预算由既有 RevisionBudget 数据负责，T12 没有重置或退款代码。

## 7. 剩余未知

- 未重跑完整 backend 全量套件；证据边界是 T12 6 项、P1 116 项、R1/R2 门 22 项、资源回归 58 项。
- 未调用真实模型、图片服务或浏览器，也未在生产数据库上执行迁移。
- 迁移失败注入是预先放置 batch 临时表，不是断电/崩溃注入；生产备份恢复演练仍属于 T24/T25。
- 不声称 R3 或 V3 Release 已验收；本票只关闭 P3 兼容性门，下一前沿是 P4/T13。

## 8. 结论

T12 已满足“旧数据只读识别、缺失字段不猜测、正常与恢复同快照、迁移失败原子回退、R1/R2 合并门”的要求，可以进入 P4。
