# v3 清理记录与后续收缩门槛

日期：2026-10-31（America/Toronto）。对应 [HLD v3](hld_v3.md) 第 3 节。

状态：**小范围源码清理完成并回归；缓存物理删除受环境策略阻止；布局收敛与 R1—R3 业务实现尚未执行。**

## 1. 已执行

| 对象（仓库根相对路径） | 证据与处理 |
|---|---|
| `main/src/components/ResultPanel.vue` | 删除孤立旧结果展示 implementation；实际页面使用 `ResultsPage.vue`，源码／测试无 import，无动态注册或自动扫描装载；历史文档引用保留为历史记录 |
| `main/src/components/ResultPlaceholder.vue` | 删除无运行／测试消费者的旧占位展示；当前结果页面已有真实空态 |
| `main/src/utils/format.ts` | 删除随旧展示失去用途的 `snapshotFields`，以及无调用的 `latestGeneration`；同步移除仅为它使用的类型导入 |
| `main/src/components/ConversationPanel.vue` | 移除未使用的 `latestGeneration` 导入；实际对话／快照逻辑不变 |
| `main/backend/app/workflow/stages/s10.py` | 移除 Ruff F401 确认的未用 `latest_artifact` 导入，不改交付逻辑 |
| `.gitignore` | 排除仓库内 GitHub 本机配置／凭据、本机状态和 `pytest-of-*` 临时运行库；排除不等于物理删除或凭据撤销 |
| `AGENTS.md` | v3 工作的条件指针改为 HLD，明确 R1→R2→R3 串行；原提案只承担设计理由 |

## 2. 未物理删除的运行残留

以下均已核验为工作区内的普通目录、无根或嵌套 reparse link；测试副本名称可对应现有测试用例，且与受保护的两个数据根分开。递归删除命令被环境策略拒绝，**仍在磁盘上**；没有换工具强制删除，也没有删除业务库。

| 目录 | 核查时文件数 | 核查时字节数 |
|---|---:|---:|
| `pytest-of-28425/` | 68 | 7,769,173 |
| `.pytest_cache/` | 5 | 571 |
| `.ruff_cache/` | 3 | 1,770 |
| `backend/.pytest_cache/` | 5 | 42,709 |
| `backend/.ruff_cache/` | 9 | 7,698 |

合计 90 个文件、7,821,921 字节（约 7.46 MiB），**不是本轮释放的空间**。已用现有缓存规则和新增临时根规则排除版本控制；后续在允许删除的环境重新核验路径、无活动消费者后清除。诊断清单保存在忽略的 `.scratch/v3-hld-audit/cleanup.json`。

## 3. 明确保留

- `tests/`、`backend/tests/` 及其必要 support：仍在验证业务、恢复、门禁和适配器，不按“测试代码”整体删除。部署产物不携带这些开发验证资产。
- 当前四个阶段页、`StageNav`、`PageTemplate`、`useWorkflow` 和关联样式：有实际运行消费者，在 R3 替代验收后按引用图收缩。
- 原有数据库迁移、审批、恢复／未知状态路径、供应商 adapter 和测试替身：仍承担行为，不是脚手架。
- 根 `backend/data` 的数据库、WAL／SHM、参考图和生成图，以及 `main/backend/data`：不移动、不覆盖、不合并；真实来源及认领走 HLD 的维护门槛。
- `.env`、本机 GitHub 配置、现有 ZIP／图示／验收证据、依赖目录与虚拟环境：不擅自销毁，不输出秘密；凭据／开发内容按发布清单排除。
- 用户原有 `backend/migrations/env.py`、`backend/tests/conftest.py`、`main/backend/app/config.py` 改动及未跟踪的自包含打包文件：保留；本轮未接管布局迁移。

## 4. 验证结果

清理前：前端类型检查、构建通过，28 个测试文件／193 个测试通过；后端交付与遗留补审专项 27 个通过。

清理后重新执行：

| 位置与命令 | 结果／边界 |
|---|---|
| 仓库根 `npm run typecheck` | 通过 |
| 仓库根 `npm run test` | 28 个测试文件，193 个测试通过 |
| 仓库根 `npm run build` | 通过；仍有既有大 chunk 提示，未在本轮做打包优化 |
| `backend/`：`uv run ruff check ../main/backend/app/workflow/stages/s10.py --no-cache --select F401,F841` | 通过；仅证明所改范围的未用导入／变量检查 |
| `backend/`：`uv run pytest tests/test_v2_t13_delivery.py tests/test_v2_t49_legacy_review.py -q -p no:cacheprovider --basetemp .scratch/v3-hld-verify-after` | 27 个测试通过；使用临时库和替身，不触碰真实数据／模型 |

全库 Ruff 基线仍有既存长行、风格和测试未用导入等错误，**未全绿**；本轮没有批量格式化或删除测试来掩盖。没有跑后端全部用例，没有提供 v3 权限、真实 HTTPS、真实模型或恢复演练证据。既有测试通过不能视为 v3 已完成。

## 5. 后续按阶段收缩

1. **C0／路径收敛：**确认唯一生产入口和真实数据源后，使根开发命令引用 `main/` 的运行配置；验证 import、迁移 head、锁文件、构建资源和运行路径后删重复件。当前打包改动不是已废弃配置的证明。
2. **R1：**新身份／项目访问覆盖所有消费者后移除旧存在性／匿名旁路；保留旧内容审查与升级迁移。
3. **R2：**原子受理接通确认、编制、生图和恢复后，移除旧独立受理／重复额度路径；仍要保留旧受理记录的保守读取与恢复语义。
4. **R3：**连续工作台验收后删除旧必经四页、分散状态推导、无账号旧恢复键的自动兼容和无消费者资源；将仍有效的测试改写为新行为断言。
5. **发布：**从明确清单构建分发物，核查无测试、临时产物、秘密和历史运行数据库混入；数据通过独立受控迁移，不靠复制 ZIP 隐式认领。

每批删除都有替代行为、消费者迁移、回归证据和清单结论。尚未核实或被策略阻断的项目保持待办。
