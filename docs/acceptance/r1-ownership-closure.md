# R1 参考上传与工作流变更归属闭环验收记录

- **Issue**：#60 V3 T08 [R1]：参考上传与工作流变更的归属闭环
- **父 Issue**：#52 V3 规格实施
- **分支**：ticket/60（基于 integration/v3 b2092d5）
- **代码提交**：8436d80af1b7ab6335a5becaaf4c867927c9bc5a
- **验收日期**：2026-10-31
- **状态**：本票实现、目标测试、相关回归、全量后端／前端、构建与真实 HTTP 冒烟均通过；合并由 integration/v3 执行

## 1. 结论与边界

本票在 #59 的项目所有者基础上，补齐参考图上传与工作流动作的归属闭环：上传、删除、工作流动作装载前都经过同一套有效主体／本人项目校验；提交事务内再次校验主体；跨账号的参考图、资源字节、工作流动作与伪造产物 ID 均被拒绝；管理员没有代看用户项目的读取豁免。实际操作者写入既有 audit_events，保留 v2 的模型职责、审批角色和产物状态机，不把前端自报的 approved_by、owner_account_id、is_admin 当成授权。

同时新增最小外部模型数据说明，明确文字、需求卡和参考图会发送给已配置的外部模型服务，提醒不要输入密码、会话凭证、恢复链接等秘密，并明确不承诺供应商零保留。

本票只承担 ticket #60 的阶段条件，不代表整个 R1 已完成。明确未覆盖：

| 未覆盖项 | 归属 |
|---|---|
| 历史、恢复、图片字节的双重门禁 | #61 T09 |
| 后台任务持久归属、禁用截断与下一模型调用阻断 | #62 T10 |
| 来源核验的幂等旧数据认领 | #63 T11 |
| R1 全阶段跨专题验收与回归门槛 | #64 T12 R1-GATE |
| 产物、审批、交付、历史、恢复等完整跨账号矩阵 | #65–#70（R2）、#71–#77（R3）及 R1-GATE |

真实 HTTP 冒烟没有配置 LLM／视觉供应商；工作流动作成功路径由使用假 registry 的后端测试覆盖，真实 HTTP 只验证了动作分发前的归属拦截，以及无模型配置时返回 503 CAPABILITY_NOT_READY 且仍写入 failure 审计行。这一边界在第五节如实标注，不把无模型环境当作外部模型闭环证明。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t60 |
| 分支／基座 | ticket/60 / integration/v3 b2092d5 |
| 代码提交 | 8436d80af1b7ab6335a5becaaf4c867927c9bc5a |
| 操作系统 | Windows（本地开发环境） |
| Python | 3.12.13（backend/.venv） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 数据库 | SQLite 临时数据目录（aiosqlite，WAL）；本票无迁移，版本保持 0005 |
| Web | 真实 uvicorn + 构建后的 dist；不使用真实业务数据 |

## 3. 实现摘要

### 3.1 实际操作者审计

新增 main/backend/app/operator_audit.py：

- record_operator_event 使用 app.state.clock 生成 UTC 时间，在独立写事务中调用既有 write_audit，记录 actor_kind=account、actor_account_id=实际 Principal、target_kind=project、target_id=项目 ID、result、reason_code、source_ref 与 allowlist 化 detail。
- record_operator_event_safely 对审计写入失败做 best-effort：记录服务日志后不掩盖已经完成的参考图或工作流动作。账号安全类变更仍在各自事务内原子审计；本票面对的是既有 v2 工作流／参考图事务，不能在动作已提交后伪装成失败，但正常请求会留下可查询的持久审计行。
- 不新增表、不改变 v2 的模型职责、审批角色、产物状态机或前端可自报字段的语义。

### 3.2 工作流动作

main/backend/app/routers/workflow.py：

- 保持 #59 的 require_principal 与 state.load_context(principal) 前置归属校验，项目锁与 expected_revision 校验不变。
- 在 dispatch.handle_action 周围记录 workflow_action：成功记 success；ApiError 401／403 记 denied，其他 ApiError 记 failure 并保留 code；未预期异常记 failure / INTERNAL。
- detail 只包含 action_id、expected_revision，以及分发前已有 run 的 run_id、stage；不记录请求正文、密码、令牌、Cookie 或模型原始错误。
- 未知动作、修订冲突、当前阶段不可用等分发前错误不写入本事件；本票只审计真正进入动作分发器的尝试。

### 3.3 参考图上传与删除

main/backend/app/routers/assets.py：

- 上传与删除继续在解析 multipart、写文件之前执行 require_project_owner；他人、未知和未归属项目统一 404。
- 上传成功后记录 reference_uploaded，detail 为 mime_type、width、height、bytes；删除成功后记录 reference_removed，detail 为 had_reference。
- main/backend/app/services/assets.py 的 ReferenceMutation 现在携带 project、asset、removed，删除返回 asset=None 且 removed=True；路由不返回已删除的旧资产。

### 3.4 既有 v2 归属与内容边界

- S06 的 _client_binding 把 artifact_id、card_digest、artifact_digest、requirement_revision 仅当作断言；真实批准仍由服务端校验 active ArtifactReview 与当前项目绑定，add_approval(approved_by=user) 不接受前端自报角色。
- S08 的 reference_asset_id 来自服务端 canonical prompt，校验 asset.project_id 与 kind；previous_generation_id 必须匹配原生成与项目。
- S09 校验当前候选图和项目资产；S11 读取 item／generation 时校验 project_id；S12 恢复只使用服务端 DiagnosticEvent checkpoint，不使用客户端对象 ID。
- 旧 v1 写入口继续由 workflow/access.py 的 old_entries_paused 与 ensure_legacy_entry_available 控制；未迁移项目在旧写入路径返回 409 LEGACY_FLOW_DISABLED。

### 3.5 最小外部模型数据流说明

main/src/pages/DescribePage.vue 在首次业务输入区域前增加具名 region「外部模型数据说明」，说明文字描述、需求卡信息和参考图会发送给已配置的外部模型服务，提醒不要填写密码／会话凭证／恢复链接，并明确不承诺供应商零保留。没有第三方脚本、遥测或远程资源。

## 4. 执行命令与结果

### 4.1 目标测试

工作目录：D:/Desktop/新建文件夹/.worktrees/t60/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t60-temp TMP=D:/Desktop/新建文件夹/.scratch/t60-temp \
    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings \
      --basetemp=D:/Desktop/新建文件夹/.scratch/t60-temp-basetemp tests/test_t60_ownership_closure.py

结果：3 passed in 2.59s，exit 0。

覆盖：恶意上传文件名不得改变存储路径；上传／删除审计行记录实际账号与项目；B 上传／动作／资源读取均 404；伪造 artifact 与自报 approved_by／owner／is_admin 不产生审批；CSRF 缺失与旧入口关闭。

### 4.2 相关回归

同一工作目录：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings \
      tests/test_t60_ownership_closure.py tests/test_v2_t09_confirmation.py \
      tests/test_t05_assets.py tests/test_t15_contract.py

结果：25 passed in 18.45s，exit 0。

### 4.3 全量后端回归

工作目录：D:/Desktop/新建文件夹/.worktrees/t60/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t60-pytest TMP=D:/Desktop/新建文件夹/.scratch/t60-pytest \
    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings \
      --basetemp=D:/Desktop/新建文件夹/.scratch/t60-final-basetemp \
      > D:/Desktop/新建文件夹/.scratch/t60-backend-full.log 2>&1

结果：521 passed, 3 warnings in 467.21s (0:07:47)，exit 0。日志保存在 .scratch/t60-backend-full.log。

### 4.4 前端回归、类型检查与构建

工作目录：D:/Desktop/新建文件夹/.worktrees/t60

    D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --reporter=dot
    D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p D:/Desktop/新建文件夹/.worktrees/t60/tsconfig.json
    D:/Desktop/新建文件夹/node_modules/.bin/vite.cmd build

结果：33 个测试文件、206 个测试通过，19.01s；vue-tsc exit 0；Vite 构建成功 714ms，输出 dist/index.html、dist/assets/index-CVMbvuUC.css（412.64 kB）、dist/assets/index-BrOqNNSO.js（1,175.91 kB）。仅有既存 chunk > 500 kB 警告与测试中的 fireEvent 警告。

### 4.5 静态检查

对 operator_audit.py、routers/workflow.py、routers/assets.py、services/assets.py 与 test_t60_ownership_closure.py 执行 Ruff，结果 All checks passed。

### 4.6 真实 HTTP + 临时 SQLite 冒烟

脚本：.scratch/t60-run-smoke.py  
结果：.scratch/t60-ownership-smoke/result.json  
服务日志：.scratch/t60-ownership-smoke/uvicorn.log

环境：完整 App，uvicorn 127.0.0.1:18772，DATA_DIR=.scratch/t60-ownership-smoke/data，WEB_DIST_DIR=.worktrees/t60/dist，APP_PUBLIC_URL=http://127.0.0.1:18772，ALLOWED_HOSTS=127.0.0.1/localhost，ALLOW_LOOPBACK_HTTP=true，AUTH_COOKIE_SECURE=false。管理员与 Alice／Bob 均通过真实 HTTP 创建与一次性链接设置密码；全部业务数据在临时目录。

关键断言：

| 场景 | 结果 |
|---|---|
| 匿名 GET /api/projects | 401 |
| 管理员登录；Alice／Bob 设置密码并登录 | 200 |
| Alice 创建项目 | 201 |
| Alice 上传文件名为 ../../outside.png 的 PNG | 200；服务端按 asset_id 生成存储名，临时数据根不存在 outside.png（精确路径由目标测试断言） |
| Alice 读取本人资源字节 | 200，字节与上传 PNG 完全一致 |
| Bob／管理员读取 Alice 资源 | 均 404 |
| Bob 向 Alice 项目上传参考图 | 404 PROJECT_NOT_FOUND |
| 缺少 CSRF 的上传 | 403 CSRF_INVALID |
| 错误 Origin 的上传 | 403 ORIGIN_INVALID |
| Alice 删除参考图 | 200；reference_removed 审计行 actor 为 Alice、target 为项目、had_reference=true |
| Bob／管理员对 Alice 项目执行工作流动作 | 均 404 |
| Alice 在无模型配置环境执行 submit_request | 503 CAPABILITY_NOT_READY；仍写入一条 workflow_action failure 审计行，actor 为 Alice、target 为项目 |
| 构建产物包含「外部模型数据说明」「不承诺供应商零保留」 | 均为 true |
| 登录页／工作区页 | 200 |
| 服务日志包含管理员或用户密码 | false |

说明：真实 HTTP 环境未配置 LLM／视觉供应商，因此 submit_request 的 503 是预期的能力边界，不是把无模型环境当作成功生成；动作成功路径由 test_t60_ownership_closure.py 的假 registry 测试覆盖。

## 5. 验收覆盖表

| V3-AC | 本票条件 | 本票证据 | 结论 |
|---|---|---|---|
| V3-AC-13 | 跨站、缺失或错误 CSRF 的上传／确认／管理请求被拒绝；外部返回地址不可任意 | 真实 HTTP 上传缺 CSRF 为 403 CSRF_INVALID、错误 Origin 为 403 ORIGIN_INVALID；全量认证回归继续通过；未新增重定向入口 | 上传路径覆盖；登录／登出／链接／管理的全矩阵仍由 #55–#59 与 #64 复核 |
| V3-AC-15 | 非公开出口先认证；未知与他人物件不泄露存在性 | 匿名项目 401；Bob／管理员访问 Alice 参考图、资源字节、工作流动作均 404；错误与未知项目响应一致 | 本票动作与参考路径覆盖；全站逐出口清点留待 R1-GATE |
| V3-AC-16 | A/B 交换项目、参考图、任务、产物、审批、交付、历史、恢复 ID 不串数据 | 参考图上传／删除、资源字节、工作流动作、伪造 artifact_id／artifact_digest 均被 404 或 409 拒绝；无跨账号 Approval | 参考图／工作流／审批部分覆盖；交付、历史、恢复矩阵由 #61、#64 及 R2／R3 继续 |
| V3-AC-17 | 普通用户伪造角色／所有者字段或直接调用管理能力失败；管理员读取 B 业务数据失败 | confirm_requirements 携带 approved_by=attacker、owner_account_id=B、is_admin=true 不产生审批；管理员对 Alice 项目动作 404；无管理代看旁路 | 本票动作路径覆盖；完整角色／管理能力矩阵留待 R1-GATE |
| V3-AC-20 | 用户文本／模型输出／文件名／路径／SQL 参数／任意网络位置不能造成脚本执行、越根读取、注入或任意请求 | 上传文件名恶意仍按服务端 asset_id 存储；既存 test_t05_assets 路径穿越、test_v2_t04 解压炸弹、test_t03 注入数据测试随 521 全量回归通过；请求参数使用 SQLAlchemy 绑定 | 回归层面覆盖；专项安全扫描不在本票 |
| V3-AC-39 | 首次业务输入前解释必要外部模型数据流；测试请求不携带密码／会话／恢复秘密；不宣称供应商零保留 | DescribePage 具名说明 region 与前端测试；真实 HTTP 模型请求未配置，无法证明供应商侧数据流；界面明确不承诺零保留 | 说明与最小数据流边界覆盖；真实供应商闭环留待后续验收，不夸大 |

## 6. 证据文件

- backend/tests/test_t60_ownership_closure.py（3 个归属闭环测试）
- main/backend/app/operator_audit.py
- main/backend/app/routers/workflow.py
- main/backend/app/routers/assets.py
- main/backend/app/services/assets.py
- main/src/pages/DescribePage.vue
- tests/t60-dataflow-notice.spec.ts
- .scratch/t60-ownership-smoke/result.json
- .scratch/t60-ownership-smoke/uvicorn.log
- .scratch/t60-backend-full.log
- .scratch/t60-run-smoke.py

## 7. 残余风险与后续

1. operator_audit 是 best-effort：参考图或工作流动作已提交后，如果审计写入失败，只记录服务日志，不把动作伪装成失败。账号安全类原子审计不受影响。后续如要求“动作与审计强一致”，必须在 v2 事务边界内重新设计，不在本票偷偷引入第二套事务。
2. reference_uploaded、reference_removed、workflow_action 目前写入 audit_events，但不进入 #58 的最小安全事件 allowlist；管理员安全事件页面不会显示这些操作者事件。若 R1-GATE 要求管理视图统一呈现，应另开显式展示与权限评审。
3. 真实 HTTP 冒烟没有配置外部模型，不能证明供应商侧数据最小化或零保留；只证明了应用侧说明、归属拦截和无秘密日志。
4. 本票不覆盖后台任务在账号停用后的模型调用／发布阻断、历史／恢复双门禁、旧数据认领与跨进程锁。
5. 旧 v2 夹具未删除或放宽；仅在测试路径中按现有管理员绑定项目归属，产品 API 对 NULL owner 项目仍统一 404。
