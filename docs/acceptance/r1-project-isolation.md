# R1 本人项目、对话与需求浏览隔离验收记录

- **Issue**：#59 V3 T07 [R1]：本人项目、对话与需求浏览隔离
- **父 Issue**：#52 V3 规格实施
- **分支**：ticket/59（基于 integration/v3 22ab28e）
- **代码提交**：1436095b8b9a5090a7adcd984e58090ee56d858d
- **验收日期**：2026-10-31
- **状态**：本票实现、目标测试、全量回归、迁移检查与真实 HTTP 冒烟均通过；合并由 integration/v3 执行

## 1. 结论与边界

本票为项目增加稳定、唯一的账号所有者，并让可信子对象通过 project_id 继承归属。对外 API 在读取主体后、排序／分页／计数之前按所有者过滤；项目、对话消息、需求简报、工作流、参考图上传／删除、资源字节、生成提交／读取均验证了 A/B 隔离。匿名请求返回 401；他人项目、未知项目与未归属旧项目统一返回 404，响应体一致；管理员账号没有代看用户项目的通用读取分支。客户端伪造 owner_account_id、is_admin 等字段返回 422，不能参与授权；事务内重新校验账号与会话状态，停用后的旧 Principal 立即失败。

本票只承担 ticket #59 的阶段条件，不代表整个 R1 已完成。明确未覆盖：

| 未覆盖项 | 归属 |
|---|---|
| 参考图上传／工作流关闭的剩余边界与整体闭环 | #60 T08 |
| 历史、恢复、图片字节的双重门禁 | #61 T09 |
| 后台任务持久归属、禁用截断与模型调用阻断 | #62 T10 |
| 来源核验的幂等旧数据认领 | #63 T11 |
| R1 全阶段跨专题验收与回归门槛 | #64 T12 R1-GATE |
| 产物、审批、交付、历史、恢复等后续专题的完整跨账号矩阵 | #65–#70（R2）、#71–#77（R3）及 R1-GATE |

旧数据兼容策略：迁移只增加可空 owner_account_id，不伪造归属。owner_account_id 为 NULL 的旧项目在内部服务仍可由受控代码读取，但任何外部 API 路径都按不存在处理（404）；如何补录归属需要单独的数据治理决定，不在本票内自动猜测或放行。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t59 |
| 分支／基座 | ticket/59 / integration/v3 22ab28e |
| 代码提交 | 1436095b8b9a5090a7adcd984e58090ee56d858d |
| 操作系统 | Windows（本地开发环境） |
| Python | 3.12.13（backend/.venv） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 数据库 | SQLite 临时数据目录（aiosqlite，WAL）；迁移版本 0005 |
| Web | 真实 uvicorn + 构建后的 dist；不使用真实业务数据 |

## 3. 实现摘要

### 3.1 所有者模型与迁移

- Project 新增 owner_account_id：String(36)，可空，外键 accounts.id，ON DELETE RESTRICT。
- 新增复合索引 ix_projects_owner_updated(owner_account_id, updated_at, id)，支撑“先按所有者过滤、再排序与分页”。
- 迁移 0005_r1_project_ownership.py 在 main/backend/migrations/versions 与 backend/migrations/versions 两处完全一致；down_revision 为 0004。SQLite 使用 batch_alter_table 复制表方式添加外键与索引，已在全新库和 0004→0005 升级库验证。未修改已应用的 0003／0004。

### 3.2 归属判定与主体重验

新增 main/backend/app/ownership.py：

- revalidate_principal：在事务内重新读取账号与会话，拒绝不存在、不可登录、session_epoch 不一致、已撤销或已过期会话，统一返回 401 AUTH_REQUIRED。
- require_owned_project：先重验主体，再按 id + owner_account_id 查询项目；他人、未知与未归属统一抛 not_found_project，避免存在性侧信道。
- require_project_scope：内部调用可传 principal=None 保持受控的旧行为；API 调用必须传主体。
- require_project_owner：以 read_session 包装归属检查，供路由在解析上传体、写文件前使用。

### 3.3 服务与路由接线

- projects：创建时写入 principal.account_id；列表在排序、limit/cursor 之前按所有者过滤；详情在读取子对象前校验归属。
- messages、briefs、assets、generations、retrieval：所有对外读写路径接收 principal 并调用 require_project_scope 或等价归属查询；生成读取、结果检索在事务内再次校验项目所有者。
- assets：上传与删除在解析 multipart、写文件之前先做归属检查；资源字节读取先校验 Asset 所属 Project 的所有者，再执行保留期与访问策略，确保不会因 409／404 差异泄露存在性。
- workflow/state：WorkflowContext 保存 principal，load_context 在带主体时重验并过滤项目，reload_context 继续传递主体；内部后台调用仍可显式无主体运行。
- 路由层统一 require_principal；OpenAPI 继续为所有错误响应声明统一 ErrorResponse 信封。

### 3.4 最小界面

不新增页面。现有项目列表、项目详情和工作台继续通过已认证 API 工作；真实 HTTP 冒烟验证 /login、工作区页面与项目页面可正常加载。管理员界面不增加“查看他人项目”的入口或旁路。

## 4. 执行命令与结果

### 4.1 目标测试

工作目录：D:/Desktop/新建文件夹/.worktrees/t59/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t59-temp TMP=D:/Desktop/新建文件夹/.scratch/t59-temp \
    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings \
      --basetemp=D:/Desktop/新建文件夹/.scratch/t59-temp-basetemp tests/test_t59_isolation.py

结果：5 passed in 3.51s，exit 0。

覆盖：所有者持久化与分页前过滤；子对象 ID 不跨账号；资源字节仅本人可读；伪造 owner／role 字段；停用后的旧 Principal；未归属旧项目；匿名业务请求。

### 4.2 迁移检查

检查脚本：.scratch/t59-migration/migration_check.py  
结果文件：.scratch/t59-migration/result.json

关键结果：

| 检查 | 结果 |
|---|---|
| 全新库升级 | fresh_version = 0005 |
| projects 列 | 含 owner_account_id |
| 索引 | ix_projects_owner_updated、ix_projects_reference_asset、ix_projects_updated_id 等 |
| 外键 | owner_account_id → accounts.id，ON DELETE RESTRICT |
| 既有 0004 库升级 | 0004 → 0005，项目与账号保留 |
| 既有项目 | id 00000000-0000-4000-8000-000000000001、标题与 brief_status 保留，owner 为 NULL |
| 归属回填演练 | 更新后 owned_project 正确关联既有账号 00000000-0000-4000-8000-000000000002 |
| 双迁移树一致性 | migration_copies_identical = true |

### 4.3 全量后端回归

工作目录：D:/Desktop/新建文件夹/.worktrees/t59/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t59-pytest TMP=D:/Desktop/新建文件夹/.scratch/t59-pytest \
    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings \
      --basetemp=D:/Desktop/新建文件夹/.scratch/t59-final-basetemp-5 \
      > D:/Desktop/新建文件夹/.scratch/t59-backend-full.log 2>&1

结果：518 passed, 3 warnings in 516.76s (0:08:36)，exit 0。日志已覆盖保存于 .scratch/t59-backend-full.log。

为让既有的 V2 旧数据回归夹具符合新归属约束，测试夹具在仅测试路径中把其直接种入的项目绑定到测试管理员；产品代码不对 owner_account_id 为 NULL 的项目开后门。涉及 test_v2_t01_legacy.py、test_v2_t13_delivery.py、test_v2_t20_retention.py、test_v2_t44_versions.py、test_v2_t48_retrieval.py、test_v2_t49_legacy_review.py；未删除或放宽这些回归。

### 4.4 前端回归与构建

工作目录：D:/Desktop/新建文件夹/.worktrees/t59

    D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --reporter=dot
    D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p D:/Desktop/新建文件夹/.worktrees/t59/tsconfig.json
    D:/Desktop/新建文件夹/node_modules/.bin/vite.cmd build

结果：32 个测试文件、205 个测试通过，19.29s；vue-tsc exit 0；Vite 构建成功 698ms，输出 dist/index.html、dist/assets/index-DMeDRV3c.css（412.14 kB）、dist/assets/index-BPQA8OzL.js（1,175.35 kB）。仅有既存的 chunk > 500 kB 警告与测试中的 fireEvent 警告。

### 4.5 静态检查

对新增及主要修改的核心模块、迁移、新测试执行 Ruff，结果 All checks passed。旧回归夹具保留仓库既有的未使用导入／行长偏差，本票仅做必要导入排序，未以此扩大改动。

### 4.6 真实 HTTP + 临时 SQLite 冒烟

脚本：.scratch/t59-run-smoke.py  
结果：.scratch/t59-isolation-smoke/result.json  
服务日志：.scratch/t59-isolation-smoke/uvicorn.log

环境：完整 App，uvicorn 127.0.0.1:18771，DATA_DIR=.scratch/t59-isolation-smoke/data，WEB_DIST_DIR=.worktrees/t59/dist，APP_PUBLIC_URL=http://127.0.0.1:18771，ALLOWED_HOSTS=127.0.0.1/localhost，ALLOW_LOOPBACK_HTTP=true，AUTH_COOKIE_SECURE=false。管理员与 Alice／Bob 均通过真实 HTTP 创建与一次性链接设置密码。

关键断言：

| 场景 | 结果 |
|---|---|
| 匿名 GET /api/projects | 401 |
| 管理员、Alice、Bob 登录／设置密码 | 200 |
| 管理员创建项目 | 201 |
| 各账号列表（limit=1，B 项目最后创建） | 只返回本人项目 |
| Alice 读取本人项目 | 200 |
| Bob 读取 Alice 项目／未知项目 | 均 404，响应体一致 |
| 管理员读取 Alice 项目／工作流 | 404，无代看豁免 |
| Bob 对 Alice 项目改简报、发消息、工作流动作、提交生成、读工作流 | 均 404 |
| Bob 上传／删除 Alice 参考图 | 均 404 |
| Alice 读取本人资源字节 | 200，字节与上传 PNG 完全一致 |
| Bob／管理员读取 Alice 资源 | 均 404 |
| 伪造 owner_account_id／is_admin | 均 422 VALIDATION_ERROR |
| 管理员读取本人项目 | 200 |
| Alice／Bob 读取管理员项目 | 均 404 |
| 登录页／工作区页 | 200 |
| 服务日志包含密码 | false |

## 5. 验收覆盖表

| V3-AC | 本票条件 | 本票证据 | 结论 |
|---|---|---|---|
| V3-AC-15 | 所有非公开出口先认证；未知与他人物件不泄露存在性 | 匿名业务路径 401；他人／未知项目同为 404 且响应体相同；未归属项目 404；工作流、资源、生成读取同样 404 | 本票范围内覆盖；全站逐出口清点留待 R1-GATE |
| V3-AC-16 | A/B 交换项目、参考图、任务、产物、审批、交付、历史、恢复 ID 不串数据 | A/B 项目、消息、简报、工作流、参考图上传删除、资源字节、生成提交／读取／结果检索均隔离；列表分页前过滤 | 项目树内覆盖；产物／审批／交付／历史／恢复专题由 #60、#61、#65–#70 等继续验证 |
| V3-AC-17 | 普通用户伪造角色／所有者字段或直接调用管理能力失败；管理员读取 B 业务数据失败，无通用超管读取分支 | owner／role 字段 422；普通用户停用账号在 #58 已 403；管理员读取用户项目与工作流 404；客户端字段不参与授权 | 本票范围内覆盖；全管理能力矩阵留待 R1-GATE |
| V3-AC-20 | 安全／注入不受影响 | 所有者来自服务端 Principal，不接受客户端字段；参数化 SQLAlchemy 查询；全量 518 回归通过 | 回归层面覆盖；专项安全扫描不属于本票 |

## 6. 证据文件

- backend/tests/test_t59_isolation.py（5 个隔离测试）
- main/backend/app/ownership.py
- main/backend/app/models.py（Project.owner_account_id 与索引）
- main/backend/app/services/{projects,messages,briefs,assets,generations,retrieval}.py
- main/backend/app/routers/{projects,messages,briefs,assets,generations,workflow}.py
- main/backend/app/workflow/state.py
- main/backend/migrations/versions/0005_r1_project_ownership.py
- backend/migrations/versions/0005_r1_project_ownership.py
- backend/tests/support/auth.py（测试夹具归属绑定辅助）
- .scratch/t59-migration/result.json
- .scratch/t59-isolation-smoke/result.json
- .scratch/t59-backend-full.log
- docs/operations/r1-project-isolation.md

## 7. 残余风险与后续

1. owner_account_id 允许 NULL 只用于兼容旧库；这些项目当前对外不可服务。归属补录必须走显式数据治理与备份，不在本票自动推断。
2. 管理员没有代看用户项目的豁免，这是安全要求；运营支持只能由用户本人操作或在明确授权的维护窗口内进行，且不得绕过审计。
3. 归属检查依赖每次请求的主体重验与数据库读取；对高并发列表有额外查询成本，后续可在不改变语义的前提下评估缓存，但不得缓存授权结果。
4. 本票未覆盖跨进程锁、后台任务在账号停用后的模型调用／发布阻断；由 #62 处理。
5. 旧 V2 数据、历史与恢复路径的完整双门禁由 #61 与 #64 复核；本票不把内部服务兼容当作外部可访问证明。
