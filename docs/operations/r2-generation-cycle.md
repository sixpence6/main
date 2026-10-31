# R2 一次确认到批准交付最短生成闭环操作说明

- **适用版本**：v3 R2，ticket #65（代码提交 40b7b37 及之后）
- **适用对象**：部署／运维人员、管理员、验收人员
- **前置阶段**：C0 备份基线（#54）、R1 认证与归属（#55–#64）已合并
- **状态**：本说明覆盖 #65 的确认、受理、额度／占用、生成与发布操作步骤；#66–#70 扩展重放、恢复和门禁

## 1. 范围与硬边界

1. 浏览器只通过现有工作流确认动作提交一次明确主操作；服务端在同一条 BEGIN IMMEDIATE 事务中写确认、意图、次数和三类占用，事务内不等待模型。
2. 每个项目必须有 owner；未认领旧项目不能走本流程。确认后后台链路先编制提示词并独立审查，再提交图片服务；图片必须经过质量审查、交付清单审查、人工批准和原子发布。
3. 默认额度：每账号滚动 24 小时 20 次受理；个人活跃 1；实例并发 2；单项目活跃 1。管理员创作同样受限。
4. 受理后意图、次数和占用持久化；失败或未知不会自动重发，也不会自动清零。只有可证本地终态或已发布交付释放占用。
5. 旧独立提交入口 POST /api/projects/{id}/confirm 和 POST /api/projects/{id}/generations 在存在 WorkflowRun 后返回 409 LEGACY_FLOW_DISABLED，不再绕过统一受理。
6. 真实业务数据、真实模型密钥和真实供应商不得用于演练；演练只用临时 DATA_DIR、临时 SQLite 和假适配器。

## 2. 前置条件

| 项 | 要求 |
|---|---|
| 服务版本 | 已包含 #65 代码提交 40b7b37；R1 迁移 0003–0007 已应用 |
| 数据库 | SQLite；迁移目标 0008_r2_generation_acceptance |
| 迁移树 | main/backend/migrations/versions 与 backend/migrations/versions 版本一致；不得修改已应用迁移 |
| Python | backend/.venv 内 Python 3.12.13；pytest 9.2.0；httpx 0.28.1 |
| Node.js | v26.12.1；前端依赖已安装 |
| 配置 | APP_DATA_DIR 与 WEB_DIST_DIR 为不同且互不包含的目录；APP_PUBLIC_URL 为绝对 http／https 源；ALLOWED_HOSTS 含实际主机 |
| 备份 | 升级前按 docs/operations/maintenance-backup.md 做一致性备份并验证；未验证不得迁移 |
| 数据 | 真实数据禁止用于本说明的演练；使用 .scratch 临时目录 |

## 3. 配置

在 backend/.env 或进程环境中设置（均为正整数，且不得为 0）：

    GENERATION_DAILY_LIMIT=20
    GENERATION_PERSONAL_SLOTS=1
    GENERATION_INSTANCE_SLOTS=2
    GENERATION_PROJECT_SLOTS=1
    VISUAL_IMAGE_SIZE=3840x2160

说明：

- GENERATION_DAILY_LIMIT 是每账号滚动 24 小时受理次数，不是金额或成本上限。
- 三个 SLOTS 分别是个人活跃、实例并发、单项目活跃上限；管理员无豁免。
- 调低额度不会删除已用记录、不会取消已受理意图，也不会绕过新检查。
- VISUAL_IMAGE_SIZE 是默认单图尺寸；旧快照不会因配置变化被静默改写。
- #65 通过部署配置调整额度；管理员在线调整接口和审计在 #67。

## 4. 升级与迁移

1. 停止业务写入或进入维护窗口，完成并验证备份，记录备份 ID 和摘要。
2. 在 backend 目录执行 alembic current，确认 0007；执行：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m alembic -c D:/Desktop/新建文件夹/.worktrees/t65/backend/alembic.ini upgrade head

3. 确认 alembic_version=0008，业务表包含 generation_intents、generation_usage、generation_slots，PRAGMA integrity_check 为 ok。
4. 记录部署代码提交：git rev-parse HEAD；不得用“最新代码”代替。

## 5. 日常操作

### 5.1 确认并受理

1. 用户打开项目需求卡，核对最终卡片、参考图、尺寸和硬要求。
2. 点击唯一主操作“确认以上需求并生成图片”。前端生成并复用稳定的 request_id；同一需求 revision 的重复点击复用同一 id。
3. 服务端返回后，页面显示本人今日已受理次数、剩余次数及个人／实例／项目占用。受理成功即进入 S07；此时尚未调用图片服务。
4. 若返回 409 REQUIREMENTS_CHANGED、409 INTENT_REPLAY_CONFLICT、429 GENERATION_LIMIT_REACHED 或 429 GENERATION_SLOT_BUSY，按第 7 节处理；不要通过重复点击或改前端绕开。

### 5.2 生成、审查与交付

1. 后台链路按 S07 编制提示词、独立审查、S08 提交图片、S09 质量审查、S10 交付清单审查运行；不需要也不允许用户再次授权。
2. 图片未通过质量审查时任务进入 blocked（IMAGE_NOT_APPROVED），不展示未审预览，不自动重发。
3. 清单就绪后由人工批准交付并发布；发布事务写入已发布文件，置意图 delivered 并释放占用。
4. 只有已批准且已发布的资源字节可下载；旧历史、参考图和找回仍重新检查归属与门禁。

### 5.3 额度与占用观察

- 个人视图显示 daily_limit、used、remaining、personal_active／limit、instance_active／limit、project_active／limit。
- 额度窗口按 UTC 毫秒滚动 24 小时，区间为 (now-24h, now]；刷新、重启、禁用账号不会清空已用记录。
- 受理后失败或未知仍计一次；核实、恢复原任务、找回原结果和幂等重放不另计；明确风险确认的新任务另计。
- 超限在模型调用前拒绝，没有隐藏队列、自动排队或自动重提。

## 6. 回滚与故障处理

### 6.1 应用回滚

优先回滚应用版本，保留 0008 数据库结构；0008 只新增表／列，不回写旧数据。不要直接删除 generation_* 表来“重置额度”，那会丢失受理和审计证据。

### 6.2 数据库回滚

只有在维护者确认、已备份且业务允许丢失本票受理记录时，才可执行 alembic downgrade 0007；该操作删除意图／次数／占用列和表，属于灾难恢复步骤，不是日常运维步骤。

### 6.3 常见错误

| 现象 | 处理 |
|---|---|
| 409 REQUIREMENTS_CHANGED | 需求卡、revision、参考图或尺寸已变化；回到 U1 重新确认，不复用旧授权 |
| 409 INTENT_REPLAY_CONFLICT | 同一 request_id 参数变化；使用新任务和新 id，不修改旧意图 |
| 429 GENERATION_LIMIT_REACHED | 等待滚动窗口释放或由管理员按 #67 调整；不得改库清零 |
| 429 GENERATION_SLOT_BUSY | 等待个人／实例／项目占用释放；不要并发重试制造更多任务 |
| 409 LEGACY_FLOW_DISABLED | 项目已有 WorkflowRun；使用统一工作流确认，不调用旧独立提交入口 |
| 任务 blocked IMAGE_NOT_APPROVED | 保留原任务和计数；按 v2 恢复／人工处理流程，不自动重发 |
| 远端未知或进程中断 | 保留次数和占用，按 #68 核实原任务；不得超时即当作未受理 |

## 7. 验收命令

后端目标测试：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t65-target-basetemp-ref2 tests/test_t65_generation_cycle.py

预期 5 passed，约 7 秒。

全量后端分两组（单条命令可能超过 600 秒）：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t65-full-nonv2-basetemp-final2 tests/test_openai_chat_adapter.py tests/test_openai_images_adapter.py tests/test_t*.py
    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --basetemp=D:/Desktop/新建文件夹/.scratch/t65-full-v2-basetemp-final2 tests/test_v2_*.py

预期合计 547 passed，无失败／skip／unknown。

前端在工作树根执行：

    npm test -- --reporter=dot
    npm run typecheck
    npm run build

预期 34 文件／207 测试通过，typecheck exit 0，Vite 构建成功。

真实 HTTP 冒烟（临时 SQLite、完整 App、假适配器）：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe D:/Desktop/新建文件夹/.scratch/t65-smoke/smoke.py

预期 {"ok": true, "checks": 53}，不触碰真实数据。

## 8. 证据与责任边界

- 验收记录：docs/acceptance/r2-generation-cycle.md
- 代码提交：40b7b37fea6ba89853a80c08c7ccbbcf978de0c5
- 临时证据：.scratch/t65-target-ref2.log、t65-full-nonv2-final2.log、t65-full-v2-final2.log、t65-frontend-*-final2.log、t65-migration-final、t65-smoke/result.json
- 真实供应商、真实旧数据、正式备份与 Release 全制品扫描不在本票；本票不关闭 #51
