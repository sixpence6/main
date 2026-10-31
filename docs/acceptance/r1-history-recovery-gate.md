# R1 历史、找回与图片真实字节双重门禁验收记录

- **Issue**：#61 V3 T09 [R1]：历史、找回与图片真实字节双重门禁
- **父 Issue**：#52 V3 规格实施
- **分支**：ticket/61（基于 integration/v3 cfbb340）
- **代码提交**：4c17fb8d09cd35b1e064ff3aab682fdb65d847d9
- **验收日期**：2026-10-31
- **状态**：本票实现、目标测试、相关回归、全量后端／前端、构建与真实 HTTP 冒烟均通过；合并由 integration/v3 执行

## 1. 结论与边界

本票在 #59 项目归属与 #60 参考图／工作流归属闭环之上，补齐历史、找回与图片真实字节的双重门禁：历史、工作流、生成记录、参考图和资源出口先经过有效主体与本人项目校验，再按真实内容审批、摘要、发布状态和文件字节放行。跨项目关联 ID 即使被人为写入数据库，也不能把另一项目的参考图或生成结果元数据暴露给当前所有者；公开静态构建目录不能再被配置到私有数据目录内部。

本票只承担 ticket #61 的阶段条件，不代表整个 R1 已完成。明确未覆盖：

| 未覆盖项 | 归属 |
|---|---|
| 后台任务持久归属、账号停用后的下一模型调用／发布阻断 | #62 T10 |
| 来源核验的幂等旧数据认领 | #63 T11 |
| R1 全阶段真实路由全清单、跨专题矩阵、全制品秘密标记扫描 | #64 T12 R1-GATE |
| 产物、审批、交付、历史、恢复的完整 R2／R3 跨账号与跨版本矩阵 | #65–#70（R2）、#71–#77（R3） |
| 浏览器缓存层、缩略图组件和真实供应商侧数据流的专项验证 | R1-GATE 及后续阶段；本票只验证 HTTP 出口与真实字节 |

真实 HTTP 冒烟未配置 LLM／视觉供应商。已发布图片的字节门禁使用临时 SQLite 中的 v2 交付夹具（直接写入 manifest／approval／Delivery）后通过真实 HTTP 读取，不是真实供应商生成闭环；该边界在第五节和第七节如实标注。

## 2. 版本与环境

| 项 | 值 |
|---|---|
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t61 |
| 分支／基座 | ticket/61 / integration/v3 cfbb340 |
| 代码提交 | 4c17fb8d09cd35b1e064ff3aab682fdb65d847d9 |
| 操作系统 | Windows（本地开发环境） |
| Python | 3.12.13（backend/.venv） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| 数据库 | SQLite 临时数据目录（aiosqlite，WAL）；本票无迁移，版本保持 0005 |
| Web | 真实 uvicorn + 构建后的 dist；不使用真实业务数据 |

## 3. 实现摘要

### 3.1 受保护出口清点与认证

main/backend/app/main.py 当前注册 33 个公开 OpenAPI 操作：6 个公开入口（健康、配置状态、CSRF、登录、一次性链接 GET／POST）和 27 个受保护操作。新增测试逐一匿名请求 27 个受保护操作，全部返回 401 AUTH_REQUIRED，覆盖项目列表／创建／详情、需求卡修改／确认、生成提交／查询／找回、消息、参考图上传／删除、工作流读取／动作、资源字节、认证会话与账号管理、个人与管理安全事件。

### 3.2 历史与资源元数据按项目过滤

- main/backend/app/services/projects.py：项目详情的 reference_asset 仅在 Asset.project_id 等于当前项目时返回；result_assets 查询增加 Asset.project_id == project_id 条件。
- main/backend/app/services/generations.py：_load_result_asset 在资产不存在或属于其他项目时返回 None。
- main/backend/app/services/retrieval.py：找回前与最终落库后加载结果资产时，均拒绝跨项目资产。
- main/backend/app/presenters.py：generation_view 在结果资产被策略隐藏或实际不可见时，同时隐藏 result_asset 与 result_asset_id；project_view 在 reference_asset 被过滤时同步把 brief.reference_asset_id 置空，避免通过需求卡快照泄露其他项目资产 ID。
- 项目详情、工作流视图、生成查询与资源字节继续使用同一 Principal 和项目所有者校验；管理员没有代看豁免。

### 3.3 参考图真实字节摘要

main/backend/app/services/assets.py 的 attach_reference 对新上传参考图写入 sha256。

main/backend/app/workflow/access.py 的参考图分支不再是“本人项目即放行”：

1. 解析文件路径必须位于 DATA_DIR 内且是普通文件；缺失返回 ASSET_FILE_MISSING。
2. 读取实际字节；资产有 sha256 时必须与真实字节一致，否则 DIGEST_MISMATCH。
3. 解码图片并核对宽高；解析失败为 ASSET_UNAVAILABLE，尺寸不一致为 DIGEST_MISMATCH。
4. 旧参考图没有 sha256 时保留按尺寸核验的兼容路径；新上传一律写入摘要。

### 3.4 生成结果与发布字节

生成结果仍沿用 v2 G08／交付门禁：只有 review_status=approved、存在有效图片审批与匹配的 requirement snapshot／rules／prompt、GenerationJob 已发布且 Delivery 为 published、交付清单审批一致、文件摘要与尺寸匹配时，HTTP 才返回真实字节。待审、仅批准未发布、已撤销、已过期、缺失文件均被阻断。

main/backend/app/routers/assets.py 把 ASSET_FILE_MISSING 映射为 404；其余内容门禁拒绝映射为 409，错误正文不回传文件路径、locator 或内部诊断。

### 3.5 公开构建与私有数据分离

- main/backend/app/config.py：启动配置拒绝 WEB_DIST_DIR 与 APP_DATA_DIR 相同或互相包含，错误为 WEB_DIST_DIR must be outside APP_DATA_DIR。此前若把构建目录放进数据目录，静态路由可能把私有文件当公开资源。
- main/backend/app/routers/web.py：构建文件与未知点路径的 404 响应都带 Referrer-Policy no-referrer 与 X-Content-Type-Options nosniff；SPA 回退仍为 no-store。
- 没有把 references、generated、published 或 data 目录挂载为静态路由；旧静态路径与供应商 locator 形状的 URL 均 404。

### 3.6 找回边界

找回入口继续使用 v2 t48 的服务指纹、适配器白名单、locator 过期时间和项目状态门禁；本票只补跨项目结果资产过滤，不改变真实供应商找回协议，也不把 locator 暴露到 API。

## 4. 执行命令与结果

### 4.1 目标测试

工作目录：D:/Desktop/新建文件夹/.worktrees/t61/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t61-temp TMP=D:/Desktop/新建文件夹/.scratch/t61-temp \
    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings \
      --basetemp=D:/Desktop/新建文件夹/.scratch/t61-temp-basetemp tests/test_t61_history_recovery_gate.py

结果：4 passed in 3.48s，exit 0。

### 4.2 相关回归

同一工作目录：

    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings \
      tests/test_t61_history_recovery_gate.py tests/test_t09_reference_versions.py \
      tests/test_t05_assets.py tests/test_v2_t13_delivery.py tests/test_v2_t20_retention.py \
      tests/test_v2_t44_versions.py tests/test_t59_isolation.py tests/test_v2_t48_retrieval.py \
      tests/test_v2_t01_legacy.py tests/test_v2_t49_legacy_review.py tests/test_t15_contract.py

结果：97 passed, 1 warning in 72.51s，exit 0。

### 4.3 全量后端回归

工作目录：D:/Desktop/新建文件夹/.worktrees/t61/backend

    TEMP=D:/Desktop/新建文件夹/.scratch/t61-pytest TMP=D:/Desktop/新建文件夹/.scratch/t61-pytest \
    D:/Desktop/新建文件夹/backend/.venv/Scripts/pytest.exe -q --disable-warnings \
      --basetemp=D:/Desktop/新建文件夹/.scratch/t61-final-basetemp \
      > D:/Desktop/新建文件夹/.scratch/t61-backend-full.log 2>&1

结果：525 passed, 3 warnings in 541.40s (0:09:01)，exit 0。日志保存在 .scratch/t61-backend-full.log；warning 仅为测试临时目录 cwd 提示。

### 4.4 前端回归、类型检查与构建

工作目录：D:/Desktop/新建文件夹/.worktrees/t61

    D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --reporter=dot
    D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p D:/Desktop/新建文件夹/.worktrees/t61/tsconfig.json
    D:/Desktop/新建文件夹/node_modules/.bin/vite.cmd build

结果：33 个测试文件、206 个测试通过，17.55s；vue-tsc exit 0；Vite 构建成功 655ms，输出 dist/index.html、dist/assets/index-CoQKJWll.css（412.64 kB）、dist/assets/index-B9cC436c.js（1,175.91 kB）。仅有既存 chunk > 500 kB 警告与测试中的 fireEvent 警告。

说明：前端首次运行出现 1 个未识别失败（205 passed），未改前端代码，立即以 verbose 重跑全量，33 个文件／206 个测试全部通过，日志为 .scratch/t61-vitest.log。本票不据此声称前端零 flake。

### 4.5 静态检查

对 test_t61_history_recovery_gate.py 及 config.py、presenters.py、routers/assets.py、routers/web.py、services/assets.py、services/generations.py、services/projects.py、services/retrieval.py、workflow/access.py 执行 Ruff，结果 All checks passed。

### 4.6 真实 HTTP + 临时 SQLite 冒烟

脚本：.scratch/t61-run-smoke.py
结果：.scratch/t61-history-smoke/result.json
服务日志：.scratch/t61-history-smoke/uvicorn.log
冒烟日志：.scratch/t61-smoke.log

环境：完整 App，uvicorn 127.0.0.1:18773，DATA_DIR=.scratch/t61-history-smoke/data，WEB_DIST_DIR=.worktrees/t61/dist，APP_PUBLIC_URL=http://127.0.0.1:18773，ALLOWED_HOSTS=127.0.0.1/localhost，ALLOW_LOOPBACK_HTTP=true，AUTH_COOKIE_SECURE=false。管理员与 Alice／Bob 均通过真实 HTTP 创建与一次性链接设置密码；全部业务数据在临时目录。

关键断言：

| 场景 | 结果 |
|---|---|
| 匿名 GET /api/projects、/api/assets/{id} | 均 401 AUTH_REQUIRED |
| 管理员登录；Alice／Bob 设置密码并登录 | 200 |
| Alice 上传 PNG 参考图 | 200；数据根外没有按原始文件名创建文件（outside_file_created=false） |
| Alice 读取本人参考图 | 200，字节与上传完全一致 |
| Bob／管理员读取 Alice 参考图 | 均 404 |
| Bob／管理员读取 Alice 项目详情 | 均 404 |
| 篡改 Alice 参考图字节后读取 | 409，原始字节不出现在响应中 |
| 恢复参考图后读取 | 200，字节再次精确一致 |
| /references、/generated、/published、/data/references、/static 下的猜测路径 | 均 404，原始字节不出现 |
| 供应商 locator 形状的 /api/assets/{locator} | 404 |
| 未发布、仅批准未发布、已撤销、过期、隔离状态的生成图 | 409，图片字节不出现 |
| 发布后预览与下载 | 200，均为同一真实字节；下载附带 attachment 文件名 |
| 删除实际文件后读取 | 404，字节不出现 |
| 登录页／工作区页／OpenAPI | 200 |
| 服务日志包含管理员或用户密码 | false |

发布夹具说明：脚本复用 test_v2_t13_delivery.py 的 _seed_ready_state 生成管理员项目、已批准但未发布的生成资产；在“发布前”断言后，直接向临时 SQLite 写入 delivery_manifest 产物、审批与 published Delivery，并更新 GenerationJob 为 published，再通过真实 HTTP 读取。该夹具只验证交付后的字节门禁，不声称真实模型生成。

## 5. 验收覆盖表

| V3-AC | 本票条件 | 本票证据 | 结论 |
|---|---|---|---|
| V3-AC-15 | 所有非公开出口先认证；未知与他人物件不泄露存在性 | 33 个 OpenAPI 操作中 27 个受保护操作逐一匿名请求均 401 AUTH_REQUIRED；A／B／管理员的项目、工作流、参考图、资源字节与动作均为统一 404 | 本票受保护路由清点与对象出口覆盖；列表总数、轮询与全站矩阵由 #64 R1-GATE 复核 |
| V3-AC-16 | A/B 交换项目、参考图、任务、产物、审批、交付、历史、恢复 ID 不串数据 | 新增测试交换项目、参考图、生成结果资产与工作流动作 ID；人为把 A 项目的 reference_asset_id／Generation.result_asset_id 指向 B 资产后，项目详情不返回 B 的 ID／元数据，字节仍 404；t13／t20／t44／t48／t49／t59 回归覆盖交付、保留、版本、找回、旧数据与项目列表 | 历史／资源元数据与字节部分覆盖；浏览器缓存与完整交付矩阵留待 R1-GATE／R2／R3 |
| V3-AC-18 | 本人待审、撤销、过期、缺失图片，原始 locator、静态路径和历史 URL 不能绕过 G08 | 待审、仅批准未发布、已撤销、过期、隔离状态均 409 且字节不出现；缺失文件 404；静态猜测路径与 locator 形状 URL 均 404；参考图篡改 409；账号归属本身不改变 review_status／审批／Delivery 状态 | 服务端 HTTP／真实字节条件覆盖；前端缩略图缓存与供应商 locator 全协议留待后续 |
| V3-AC-20 | 用户文本／模型输出／文件名／路径／SQL 参数／任意网络位置不能造成脚本执行、越根读取、注入或任意请求 | 新增静态路径、locator 形状 ID、公开构建目录与数据目录重叠拒绝；既有 test_t05_assets 路径穿越、test_v2_t04 解压炸弹、test_t03 注入数据、test_v2_t13 交付文件名测试随 525 全量回归通过；SQL 使用绑定参数 | 回归层面覆盖；独立安全扫描不在本票 |
| V3-AC-21 | 测试秘密标记扫描响应／前端包／业务 DB／日志／文件名／备份；模型密钥／认证秘密不返回；普通用户无完整内部诊断 | 响应不含 locator、私有路径或图片字节；真实 HTTP 服务日志不含密码；错误仅返回稳定 code／message | 仅本票出口与日志阶段条件；全制品秘密标记扫描与诊断权限矩阵留待 #64 R1-GATE |

## 6. 证据文件

- backend/te
