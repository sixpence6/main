# C0 隔离开发基线与安全清理记录

- **Issue**：GitHub #53《V3 T01 [C0]：隔离开发基线与有证据的安全清理》
- **父规格**：GitHub #52
- **分支**：`ticket/53`
- **代码版本**：`555837d4b899fc3bce9ae69f359f8c86e1ffea0f`（`555837d`，基线来自 `integration/v3`）
- **记录日期**：2026-10-31（Asia/Shanghai）
- **状态**：**C0 基线、隔离与清理证据已记录；不表示 v3 完成或任何跨阶段验收通过。**

## 1. 结论与边界

本记录只覆盖 C0：建立可重复的前后端基线、隔离临时数据根、区分真实数据候选与用户原有改动、复核已执行的小范围清理。没有新增业务代码、没有执行真实数据迁移／认领／备份，也没有删除任何真实数据库、WAL、图片或秘密配置。

C0 的结论是“可在隔离环境继续 R1 开发”，不是“生产布局已经收敛”。根目录与 `main/` 的两套运行配置仍并存；缓存仍有未物理删除项；真实数据来源仍待维护者选择。以下未完成项不记为通过。

## 2. 代码版本与环境

| 项 | 值 |
|---|---|
| 分支 | `ticket/53` |
| 基线提交 | `555837d4b899fc3bce9ae69f359f8c86e1ffea0f` |
| 前一提交 | `99c3fcd`（`integration/v2`） |
| `integration/v3` | 起始同为 `555837d`，本票开始时无需合并 |
| 操作系统 | Windows 10（`10.0.19045`，64 位，16 逻辑 CPU） |
| Python | 3.12.13（uv 管理的 CPython；`backend/.venv/Scripts/python.exe`） |
| pytest | 9.2.0 |
| Node.js | v26.12.1 |
| npm | 11.20.0 |

## 3. 设置来源与真实数据候选

### 3.1 设置解析顺序

`main/backend/app/config.py` 的 `Settings` 按以下顺序取得值：

1. **操作系统环境变量**（最高优先级）：`APP_DATA_DIR`／`DATA_DIR`、`DATABASE_URL`、`WEB_DIST_DIR`、`LLM_*`、`VISUAL_*` 等。
2. **`backend/.env`**：`DEFAULT_ENV_FILE = REPO_ROOT / "backend" / ".env"`。
3. **代码默认值**：仓库布局为 `<repo>/backend/data`；自包含 `main/` 布局为 `main/backend/data`；数据库默认是数据根下的 `app.db`。

`REPO_ROOT` 由用户既有改动通过 `backend/alembic.ini` 标记识别：仓库布局取仓库根，独立 `main/` 包取 `main/`。因此同一份代码在两种布局下默认指向两个不同的数据根，不能凭其中一个为空判断另一处没有历史数据。

只读观测（不记录任何秘密值）：

| 项 | 观测 |
|---|---|
| 宿主进程环境 | `APP_DATA_DIR`、`DATA_DIR`、`DATABASE_URL`、`WEB_DIST_DIR`、`LLM_*`、`VISUAL_*` 均未设置；测试命令仅把 `DATA_DIR` 指向临时根作为护栏，fixture 的显式 `data_dir` 仍覆盖它 |
| 仓库根 `backend/.env` | 存在；provider 相关键均有非空值（值未输出、未记录） |
| `main/backend/.env` | 存在；仅 `LLM_TIMEOUT_SECONDS`、`VISUAL_TASK_TIMEOUT_SECONDS` 非空 |

### 3.2 两个真实数据候选：均未选择、未触碰

`ticket/53` 是 Git worktree；两个 data 目录被 `.gitignore` 排除，因此 worktree 内没有同名目录。以下只读核验来自主工作区实际候选，没有在 worktree 创建、复制或挂载它们。

| 候选（仓库根相对路径） | 只读核验 | 选择状态 |
|---|---|---|
| `backend/data` | 17 个文件、30,857,479 字节；`app.db` 225,280 B、`app.db-wal` 0 B、`app.db-shm` 32,768 B；13 个生成 PNG；1 个参考 PNG。内容摘要 `e4222ce6d4b58e40f6fccff829e4e71e5d2692dc94d6bd8e99eedbb30ddc8ca9`（按路径／大小／文件 SHA-256 排序聚合） | **未选择（unselected）、未触碰（untouched）** |
| `main/backend/data` | 1 个文件 `.gitkeep`、0 字节。内容摘要 `329b754d39bd3ae82290ea1242de7bc964f82d19e3e27ef63022ddaddd4edb9f` | **未选择（unselected）、未触碰（untouched）** |

“未触碰”指：没有用 SQLite 打开业务库、没有执行迁移、没有认领／合并、没有移动／删除／重命名，也没有将候选作为测试数据根。测试前后对全部候选重新计算摘要，两个目录的内容摘要均未变化。只读清单和摘要只存在于未跟踪的 `.scratch/v3-c0-baseline/`，不进入提交。

## 4. 555837d 中的既有用户改动

`555837d` 共 36 个文件、6,687 次插入、376 次删除。它不是 C0 新建的代码提交；其中包含的既有用户改动原样保留：

| 文件／区域 | 既有改动 | C0 处理 |
|---|---|---|
| `backend/migrations/env.py` | `IMPORT_ROOT`：`main/backend` 存在时优先，否则回退 `backend/` | 保留，不重写 |
| `backend/tests/conftest.py` | 同上，测试导入根按布局选择 | 保留，不重写 |
| `main/backend/app/config.py` | `_REPO_CANDIDATE` + `backend/alembic.ini` 标记决定 `REPO_ROOT` | 保留，不重写 |
| `.gitignore` | 增加 `main/backend/data/`、`/.config/gh/`、`/.local/`、`/pytest-of-*/` 等排除 | 保留 |
| `AGENTS.md` | v3 工作指向 HLD 与串行门槛 | 保留 |
| 自包含 `main/` 打包 | `main/` 的 Docker、依赖锁、迁移、配置示例和前端源码 | 保留；布局迁移不在 C0 执行 |
| `docs/architecture/*`、`vesion/v3/SRS_v3.md` | v3 设计、清理记录、证据与 SRS | 作为本记录的依据保留 |

用户未跟踪的 ZIP、DAG 图片和本机临时运行库不属于本票提交内容，未删除、未移动、未提交。

## 5. 基线结果

所有测试都在独立临时根 `.scratch/v3-c0-baseline/` 下运行；`TEMP`／`TMP` 指向该根，pytest 另用 `--basetemp`，不读取两个真实数据候选。

| 套件 | 命令 | 本次结果 | C0 起始基线记录 |
|---|---|---|---|
| 后端 | `cd backend`；隔离环境后 `.venv\Scripts\python.exe -m pytest -q --disable-warnings --basetemp=...\pytest-basetemp` | **426 passed，299.48s（0:04:59），exit 0**；含进程启动的墙钟 313.86s | 426 passed／282.00s |
| 前端 | 仓库根 `npm test -- --reporter=dot` | **28 个测试文件、193 个测试通过，16.75s，exit 0**；墙钟 17.89s | 28 files／193 tests／18.99s |
| 前端类型检查 | 仓库根 `npm run typecheck` | 通过，exit 0 | `v3-cleanup` 记录通过 |
| 后端定向静态检查 | `cd backend`；`.venv\Scripts\python.exe -m ruff check ../main/backend/app/workflow/stages/s10.py --no-cache --select F401,F841` | `All checks passed!` | `v3-cleanup` 记录通过 |

“C0 起始基线记录”是本票开始时在同一 555837d 上已有的记录；本次复跑用于核对。说明：本次后端复跑比 C0 起始记录慢约 17s，但通过数相同；前端复跑略快。两者均为同一 `555837d` 代码、同一台机器，未通过删除测试或降低断言换取通过。测试输出中的前端 `fireEvent.change` 提示是既有测试写法告警，不是失败。

## 6. 隔离理由

1. **两个候选互斥且不能自动合并**：根 `backend/data` 有真实数据库／WAL／图片，`main/backend/data` 只有 `.gitkeep`。包内目录为空不能证明没有旧数据，也不能证明根目录就是维护者选定的来源。
2. **配置优先级可能把测试带到真实库**：`Settings` 先读 OS 环境再读 `backend/.env`。因此测试命令显式清除 `LLM_*`、`VISUAL_*`、`APP_DATA_DIR`、`DATA_DIR`、`DATABASE_URL`、`WEB_DIST_DIR`，并由 fixture 以 `Settings(_env_file=None, data_dir=<tmp>, web_dist_dir=<tmp>)` 创建应用。
3. **唯一临时根**：后端 pytest 的 `--basetemp`、Python 的 `TEMP`／`TMP`、TestClient 的 `data_dir` 全部位于 `.scratch/v3-c0-baseline/`；没有在 `backend/data` 或 `main/backend/data` 下创建文件。
4. **无真实模型凭据**：测试使用现有 adapter 替身；没有使用 `backend/.env` 的真实 provider 配置，也没有为了测试发起真实模型请求。
5. **可回验**：测试前后对两个候选做内容摘要，结果一致；健康检查另行确认临时 SQLite 可读。

## 7. 清理分类与证据

分类规则：先查静态引用、测试引用、动态加载／路由注册、构建入口和替代行为；无法证明无消费者的项目保留。

| 对象 | 分类 | 引用／替代证据 | 处理结果 | 遗留 |
|---|---|---|---|---|
| `main/src/components/ResultPanel.vue` | 无引用旧结果展示 implementation | 全仓源码／测试无 import；无动态注册或自动扫描；实际页面使用 `ResultsPage.vue`；历史文档仅作历史记录引用 | `555837d` 删除 338 行；前端测试、typecheck、构建回归保持有效 | 无 |
| `main/src/components/ResultPlaceholder.vue` | 无运行／测试消费者的旧占位展示 | 同上；当前结果页已有真实空态；仅历史文档提及 | `555837d` 删除 18 行；回归通过 | 无 |
| `main/src/utils/format.ts` 的 `snapshotFields`、`latestGeneration` | 随旧展示失去用途的死函数 | 代码／测试无引用；`latestGenerationView`／`latestGenerationError` 是仍在使用且不同的字段 | `555837d` 删除两函数及仅为它们使用的 `GenerationView` 类型导入（1 增 13 删）；typecheck、测试通过 | 无 |
| `main/src/components/ConversationPanel.vue` 的 `latestGeneration` 导入 | 未使用导入 | 仅该导入删除，实际对话／快照逻辑不变 | `555837d` 删除 1 行；回归通过 | 无 |
| `main/backend/app/workflow/stages/s10.py` 的 `latest_artifact` | Ruff F401 确认的未使用导入 | 不改变交付逻辑；定向 Ruff F401／F841 通过 | `555837d` 删除 1 行；426 后端测试通过 | 无 |
| `backend/pytest-of-unknown/`、`pytest-of-28425/`、`.pytest_cache/`、`.ruff_cache/`、`backend/.pytest_cache/`、`backend/.ruff_cache/`、`main/backend/.ruff_cache/` | pytest／Ruff 运行副本与缓存 | 已核验为工作区普通目录（reparse=False）；目录名／内容对应现有 pytest 用例与 Ruff 缓存；全仓 git grep 无引用；与两个受保护数据根分开 | **本轮已物理删除**：逐个使用绝对路径 Remove-Item -LiteralPath ... -Recurse -Force；backend/pytest-of-unknown 为 667 文件／115,411,386 B，pytest-of-28425 为 68 文件／7,769,173 B，其余为 5／3／5／9／4 文件的普通缓存；删除后全部 Test-Path=False | 无；这些缓存可在后续普通运行中再生成，清理前不应把再生成视为旧数据恢复 |

主工作区缓存清理前复核（“核查时”；2026-10-31 13:17 已按上表删除）：

| 目录 | 文件数 | 字节数 |
|---|---:|---:|
| `pytest-of-28425/` | 68 | 7,769,173 |
| `.pytest_cache/` | 5 | 48,667 |
| `.ruff_cache/` | 3 | 1,770 |
| `backend/.pytest_cache/` | 5 | 42,709 |
| `backend/.ruff_cache/` | 9 | 7,698 |
| `main/backend/.ruff_cache/` | 4 | 6,886 |

`.pytest_cache` 的字节数与 `v3-cleanup` 当时记录不同（当时 571 B），说明缓存可在普通运行中再生成；这不改变“无业务数据”的判断，但清理前仍需按最终路径重新核验。`main/backend/.ruff_cache/` 是本轮复核新发现的同类运行缓存，纳入同一待办。

明确保留（不清理）：

- `tests/`、`backend/tests/` 及 support：仍验证业务、恢复、门禁和 adapter，当前 28／193 与 426 用例通过；不随部署包分发。
- 四个阶段页、`StageNav`、`PageTemplate`、`useWorkflow` 及关联样式：仍有实际运行消费者，待 R3 替代验收后按引用图收缩。
- 根 `backend/data`、`main/backend/data`：受保护数据候选，未选择、未移动、未覆盖、未合并。
- `.env`、本机 GitHub 配置、ZIP／图示／验收证据、依赖目录与虚拟环境：未销毁、未输出或记录秘密值、未提交。

## 8. TestClient 健康检查（真实临时 SQLite）

使用 FastAPI／Starlette `TestClient` 启动完整应用生命周期，数据根为 `.scratch/v3-c0-baseline/health-root`，迁移从空库升到 `0002` head，再请求 `GET /api/health`：

```python
settings = Settings(
    _env_file=None,
    data_dir=temp_root / "data",
    web_dist_dir=temp_root / "web",
    llm_adapter=None, llm_base_url=None, llm_api_key=None, llm_model=None,
    visual_adapter=None, visual_base_url=None, visual_api_key=None,
    visual_model=None, visual_image_size=None,
)
# alembic upgrade head -> 0002
app = create_app(settings=settings, registry=AdapterRegistry())
with TestClient(app) as client:
    response = client.get("/api/health")
```

实际结果：

| 项 | 结果 |
|---|---|
| Alembic 迁移 | `0001_initial_schema` → `0002_workflow_v2` |
| 临时数据库 | `.scratch/v3-c0-baseline/health-root/data/app.db`，存在，245,760 B |
| HTTP 状态 | **200** |
| Content-Type | `application/json` |
| 响应体 | `{"status":"ok"}` |
| 真实数据 | 未读取、未写入；两个候选内容摘要在检查前后一致 |

运行时出现 `StarletteDeprecationWarning`（`TestClient` 与当前 `httpx` 组合的依赖提示），不影响健康检查的 200 结果；它不是应用错误，也不作为生产兼容性结论。

### 8.1 真实 HTTP 启动（uvicorn，独立进程）

除 ASGI TestClient 外，另用独立 uvicorn 进程和真实 TCP 回环请求复核启动边界。数据根仍为 `.scratch/spec-52/c0-http-data`，先在同一临时根执行 `alembic upgrade head`（`0001 -> 0002`），再启动：

- 环境：`APP_DATA_DIR=.../c0-http-data`；`WEB_DIST_DIR=.../main/dist`；未设置 `DATABASE_URL`；无真实模型凭据。
- 命令：`cd main/backend`；`../backend/.venv/Scripts/python.exe -m alembic -c alembic.ini upgrade head`；`../backend/.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18765`。

| 项 | 结果 |
|---|---|
| Alembic | exit 0；`Running upgrade -> 0001`、`0001 -> 0002` |
| `GET /api/health` | **200**，`{"status":"ok"}`，`server: uvicorn` |
| `GET /api/config/status` | **200**；仅返回 `generation/image_generation/llm/text/vision/visual` 配置状态键 |
| 临时数据根 | 自动创建 `app.db`、`app.db-shm`、`app.db-wal`、`generated/`、`references/`、`tmp/`；不使用真实候选 |
| 进程退出 | 请求后终止 uvicorn；无残留监听进程 |
| 原始日志 | `.scratch/spec-52/c0-http-uvicorn.log`、`.scratch/spec-52/c0_http_smoke.py` |

该检查只证明完整应用在临时 SQLite 上可启动并响应健康／配置状态；不证明 R1 认证、真实模型、LAN HTTPS 或发布验收。

## 9. V3-AC-20／21／33／40 的 C0 拆分

以下只记录 C0 可证明的部分。跨阶段条件仍为**待验收**，不从本记录外推为通过。

| 验收编号 | C0 已核对／通过 | 仍待后续阶段（不计通过） | 当前状态 |
|---|---|---|---|
| **V3-AC-20** 不可信输入、文件名、路径、SQL、模型输出与网络定位不造成注入／越界访问 | 现有 426 个后端回归通过，其中包含既有 `test_asset_path_traversal_is_not_served`、`test_prompt_injection_is_data_only_and_cannot_authorize`、`test_download_from_unapproved_host_is_rejected_before_request`、`test_cleanup_rejects_escape_and_wrong_ownership` 等路径／注入／定位符检查；C0 未新增业务路由 | R1 新增身份／归属／真实字节出口的完整恶意输入矩阵；R2／R3 新增状态与请求参数；Release 真实网络定位、文件名、SQL 与模型输出复核 | **C0 部分通过；整项待验收** |
| **V3-AC-21** 响应、前端包、业务库、日志、文件名与备份不泄漏认证秘密／模型密钥 | 现有 `test_config_status_never_leaks_credentials`、`test_shared_vision_configuration_never_leaks_shared_credentials` 及 adapter 安全拒绝测试通过；配置状态响应未包含测试秘密；C0 未读取真实 Key | 合成秘密标记对全部响应、前端 bundle、业务库、日志、文件名、模型请求、分发物及备份的扫描；普通用户诊断边界；R1 新认证秘密出现后的回归 | **C0 部分通过；整项待验收** |
| **V3-AC-33** 单来源和多来源迁移可验证；备份前不认领；重复执行不丢数据；新账号无旧项目 | 已列出并只读核验两个数据候选（`backend/data`、`main/backend/data`）；两者均明确未选择、未触碰；测试前后摘要不变；未执行迁移、认领或备份 | 维护者选择真实来源与迁移范围；完整备份及隔离回退点；单／多来源冲突选择；幂等认领；内容隔离；新账号空工作区；Release 恢复演练 | **C0 只完成核源前置；整项待验收** |
| **V3-AC-40** 鉴权配置、数据库、审计或归属校验不可用时按职责安全失败；维护说明可由另一操作者复现 | 临时 SQLite 可读时 `GET /api/health` 返回 200；现有启动／进程测试通过；C0 未新增匿名兼容入口 | R1 的鉴权／归属失败关闭、审计写入失败回滚；Release 的数据库／审计故障注入；另一操作者在局域网环境复现维护步骤 | **C0 无新增匿名旁路；整项待验收** |

## 10. 未完成事项与门槛声明

以下项目明确保持待办：

1. **缓存物理删除**：已于 2026-10-31 13:17 完成上述 7 个普通目录的绝对路径／reparse／引用核验并删除；后续运行会再生成普通缓存，发布打包仍须按清单排除，不能把“缓存已删”当成发布物已清洁的证明。
2. **唯一生产入口**：根目录与 `main/` 的打包／配置／启动路径仍并存；需在 #78／#79 证明 import、迁移 head、锁文件、资源根一致后再删重复件。
3. **真实数据来源**：`backend/data` 与 `main/backend/data` 均未选择；任何迁移／认领前须由维护者确认来源、完成可恢复备份与回退点（#54、#84、Release）。
4. **跨阶段验收**：V3-AC-20／21／33／40 只完成上表列出的 C0 部分，其余保持待验收；R1—R3、正式 HTTPS、备份恢复和真实模型证据均不在本记录范围。

本记录不关闭 #53，也不把 C0 文档当作 v3 完成、发布安全或真实数据已处理的证明。

## 11. 证据与复现

- 后端原始日志：`.scratch/v3-c0-baseline/backend-pytest.log`
- 前端原始日志：`.scratch/v3-c0-baseline/frontend-test.log`
- 健康检查日志与脚本：`.scratch/v3-c0-baseline/health-check.log`、`.scratch/v3-c0-baseline/health-check.py`
- 真实 HTTP 启动日志与脚本：`.scratch/spec-52/c0-http-uvicorn.log`、`.scratch/spec-52/c0_http_smoke.py`
- 数据候选摘要：`.scratch/v3-c0-baseline/data-manifest-before.json`

上述文件位于被忽略的 `.scratch/`，不随提交分发；本文件已摘录关键命令、结果和边界。复现时应保留相同的临时根与无真实模型凭据条件。
