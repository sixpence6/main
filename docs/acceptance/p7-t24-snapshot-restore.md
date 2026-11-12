# P7 / T24 — 快照兼容、备份恢复与安全回滚演练（#110）

- 票：#110（T24，P7）；父问题 #86；集成分支基线 **06cc738**，本票从 **06cc738** 展开
- 分支：**ticket/110**；实现提交 **015ddc1**
- 迁移头：**0011_recovery_claims**（本票不新增迁移）
- 关键文件：**main/backend/app/maintenance/restore.py**、**backend/tests/test_v3_p7_t24_snapshot_restore.py**
- 证据 JSON：**docs/acceptance/evidence/p7-t24.json**

## 1. 安全边界

T24 在原有 restore_switch 的备份校验、staging、账号/会话/资源清理和原子切换之间增加两道只读检查：

1. **账本缺口检查**：读取当前源库与选中备份的 workflow_runs、generation_jobs、revision_budgets 和未撤销 approvals。当前有活动 run、未终结 job、恢复 claim、更高候选预算、缺失或摘要变化的 approval 时，返回 **RESTORE_LEDGER_GAP**，不切换、不写 restore gate，源 app.db 字节不变。
2. **快照兼容检查**：对备份中每个未撤销 approval 的 checks_json 调用 execution_snapshot_from_checks。无快照的旧 approval 保持旧语义；未知版本、错误 contract/encoding/digest/字段返回 **RESTORE_SNAPSHOT_INCOMPATIBLE**，不做猜测升级。

## 2. 测试覆盖

- 成功 round-trip：备份内已有 1.1.0 approval、prompt_candidate 预算 used=2、unknown job；恢复后三者和 checks_json 原样保留，当前多出的 usage/slot 通过 floor 保留。
- 账本阻止：当前插入 active run、recovery claim、unknown job、预算 used=2 后恢复被拒，错误 details 含 active_run/recovery_claim/unresolved_job/revision_budget，源库哈希不变且无 gate。
- 新 approval 阻止：备份后新增 approval 被视为账本缺口，不能靠回滚抹掉。
- 快照阻止：9.9.9 快照在切换前被拒绝，reason=SNAPSHOT_VERSION_UNSUPPORTED，源库哈希不变。
- gate 生命周期：成功恢复生成 open gate，必须显式 CLEAR_RESTORE_GAP 才清除。

## 3. 命令与结果

| 命令 | 结果 |
| --- | --- |
| T24 专项 | 5 passed in 4.42s |
| T24 + t82/t54/t81 维护回归 | 40 passed in 13.29s |
| ruff check | All checks passed! |

## 4. 验收映射

| 验收点 | 结论 | 证据 |
| --- | --- | --- |
| WG3-AC-06 | 通过 | 恢复前验证 1.1.0 快照；成功恢复后 approval checks_json 与快照摘要不变 |
| WG3-AC-07 | 通过 | 候选预算 higher-used 阻止回滚；成功恢复保留备份中的 used=2 |
| WG3-AC-08 | 通过 | unknown/未终结 job、恢复 claim 缺席时阻止；备份内已有 unknown job 可原样恢复 |
| WG3-AC-12 | 通过 | approval/job 历史保留；不兼容版本明确拒绝，不重解释旧图 |
| restore gate | 通过 | 成功恢复 open，拒绝恢复无 gate；必须显式清除后才恢复受理 |

## 5. 剩余未知

- 未重跑完整后端全量；本票证据为 T24 专项 5 项与维护回归 40 项。
- 演练使用临时 SQLite 与合成数据，未停止真实生产服务、未进行断电/真实生产回滚。
- 因当前存在无法由旧备份解释的 approval 时选择明确阻塞而非强制回滚；这是安全边界，不是回滚成功。
- 未连接真实模型、图片服务、浏览器或发布包；T25 仍需完成真实环境发布复验。
