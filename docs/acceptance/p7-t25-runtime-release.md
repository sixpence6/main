# T25 / #111 运行版本与真实环境发布复验

- Issue: #111
- 父 Issue: #86
- 分支: ticket/111
- 合并基线: integration/v3 f2df5aa
- 应用代码提交: 9e13ba8（兼容性修复）、fafea37（发布工具 migration head）
- 集成合并: cd627ea15770b148c00b4b4a00be1871bb223a54
- 发布包版本: ecommerce-image-workspace 0.1.0
- 迁移头: 0011_recovery_claims
- 执行日期: 2026-11-02

## 1. 结论与边界

本轮完成了源码、静态构建、发布包和本机生产入口之间的一致性复验：

1. 源码 HEAD 为 `cd627ea15770b148c00b4b4a00be1871bb223a54`，发布包 `RELEASE-MANIFEST.json.source_commit` 与之一致。
2. `main/package.json`、发布包 manifest 和运行检查的版本均为 `0.1.0`。
3. 发布工具从源码迁移目录推导出 `0011`，manifest、`app.production --check` 与全新 SQLite 迁移链末端均为 `0011`。
4. `main/dist` 与发布包 `dist` 的 index、JS、CSS 三个文件逐字节一致；运行中的包入口返回的 index 哈希与包内 index 哈希一致。
5. 发布了可启动的本机生产包：`/api/health` 200，`/` 200，静态 JS 200，临时数据库从 0001 升级到 0011；检查结束后无残留 uvicorn 进程。

但本票要求的真实供应商、受控局域网 HTTPS/可信证书、第二台设备、真实桌面/手机软键盘、真实数据源与维护者确认等条件，本轮没有新的明确授权或真实设备证据。按票面要求“未获授权不调用，缺证据保持对应条件未通过”，**本票不能关闭，V3-Release 不能宣布完成**。本文件只记录已实际执行和未执行的部分，不把历史发布记录或本地测试通过当作本次真实环境证据。

| 发布条件 | 本轮结论 | 依据 |
| --- | --- | --- |
| 源码/进程/静态构建/发布包版本与配置一致性 | 通过（本机范围） | 见 §3、§4、§5 |
| 前序集成、兼容回滚与验收矩阵无未解决失败/未知 | 部分通过（本地回归） | 见 §5.4、§5.5；未扩大正式入口 |
| 真实供应商一次确认、修复、单图提交、独立批准、交付与资源访问 | 未通过 | 无明确本轮授权；未发起 provider 网络调用 |
| 受控局域网 HTTPS/可信证书、另一设备、多账号隔离 | 未通过 | 未创建当前 TLS 入口，无第二台设备 |
| 真实桌面/手机软键盘与交互 | 未通过 | T22 仅有 headless Edge；无物理手机/软键盘证据 |
| 真实环境导航/刷新/历史/下载零调用、一次发布跳转、无私图泄漏 | 未通过 | 仅 T15-T23 的 jsdom/HTTP/模拟证据 |
| 真实数据源、维护者确认、备份可恢复、迁移范围 | 未通过 | T24 仅临时 SQLite 演练 |

## 2. 本轮实际改动

### 2.1 发布包迁移头从源码推导

`tools/release_package.py` 原先硬编码 `migration_head: "0010"`。T08 已新增 `0011_recovery_claims`，旧值会让发布包在启动时与源码迁移头不一致。现在 `migration_head(source_root)` 扫描 `source_root/backend/migrations/versions/[0-9][0-9][0-9][0-9]_*.py`，排序后取最后一个四位前缀；没有迁移文件时显式失败。manifest 写入实际头，`test_t78_production_entry.py` 与 `test_t79_release_package.py` 断言 `0011` 且与 `head_revision()` 一致。

### 2.2 回归中发现的真实兼容问题

- `main/backend/app/services/retrieval.py` 与 `main/backend/app/workflow/stages/s11_retrieval.py` 对 `_validate_result_bytes` 的四返回值仍按三值解包，导致重取结果报 `RESULT_FETCH_FAILED`；已改为 `_normalized, info_image, relative_path, asset_id`。
- `main/backend/app/workflow/stages/s07.py` 增加 legacy coverage helpers，`s08.py` 传递 `legacy=True` 读取无 execution snapshot 的旧提示词；旧 pipe 分隔提示词按原有字符子序列语义兼容，新链仍使用 T09/T11 的严格覆盖和硬/软约束。

这两项修复属于 T25 最终回归发现的发布阻断问题；测试覆盖见 §5.4。

## 3. 源码、构建、包与运行版本一致性

| 项目 | 值 |
| --- | --- |
| 源码 HEAD | cd627ea15770b148c00b4b4a00be1871bb223a54 |
| 包 manifest source_commit | cd627ea15770b148c00b4b4a00be1871bb223a54（一致） |
| package.json version | 0.1.0 |
| RELEASE-MANIFEST version | 0.1.0（一致） |
| migration head（源码/tool） | 0011 |
| migration head（manifest） | 0011（一致） |
| migration head（runtime `--check`） | 0011（一致） |
| 生产入口 | backend/app/production.py → app.main:app |
| Python | 3.12.13 |
| Node / npm | v26.12.1 / 11.20.0 |
| Vite | 8.3.5 win32-x64 node-v26.12.1 |
| OS | Windows 10.0.19045 |

静态构建：`npm run build` 退出码 0，Vite 8.3.5，1669 modules，876ms。

| 文件 | 大小 | SHA256 |
| --- | ---: | --- |
| `main/dist/index.html` | 472 | `0c998ae38618359ce17f3007bfeb0d9b3201e86b285e6cd02b2801185e3f6a07` |
| `main/dist/assets/index-B8Q_t_Sj.js` | 1,174,406 | `2e06c9aba33c58aa834f1cda0ab8db7837f8c65c2d26db9e2bcb61d299d0bb6f` |
| `main/dist/assets/index-CQNG_L0h.css` | 437,164 | `44696c4984225aa7dd33a7237e4a356efd029725e81ef1902f70f552be221846` |

发布包 `dist` 中同名三个文件的 SHA256 与大小与上表完全相同；包内 `.js` 内容包含新品牌“电商生图工作台”，不包含旧品牌“电商生图智能体”。

## 4. 实际进程与旧静态资源检查

复验开始前，5173 端口存在旧的 Vite 开发进程 PID 32716，命令行指向 `main/vite.config.ts`，工作目录是 `.worktrees/t108`（T22 验收用），不是当前集成源码。该进程已显式停止；停止后 5173 无监听。

- 18991 端口：只在本轮发布包 smoke 期间启动，检查完成后停止，当前无监听。
- 8000 端口：PID 17496 `com.docker.backend.exe`，与本项目无关，未操作。
- 当前没有残留 uvicorn、vite 或 `app.production` 项目进程。

发布包 smoke 使用的临时数据库是 `release-artifacts/runtime-data/app.db`；它从空库迁移 0001→0010→0011，未触碰 `backend/data` 或 `main/backend/data`。包内 `app.production --check --json` 的 `database_path`、`web_dist_dir`、`import_root` 均指向发布包目录而不是开发工作区。

## 5. 命令与结果

### 5.1 静态构建

~~~text
cd D:/Desktop/新建文件夹
npm run build
~~~

结果：退出码 0；1669 modules；876ms；产物为 `main/dist/index.html`、`main/dist/assets/index-B8Q_t_Sj.js`、`main/dist/assets/index-CQNG_L0h.css`。

### 5.2 发布包构建与校验

~~~text
backend/.venv/Scripts/python.exe tools/release_package.py --build --no-build \
  --output-root D:/Desktop/新建文件夹/release-artifacts --json
backend/.venv/Scripts/python.exe tools/release_package.py --check --require-dist --json
backend/.venv/Scripts/python.exe tools/secret_scan.py \
  --root release-artifacts/ecommerce-image-workspace-0.1.0 \
  --zip release-artifacts/ecommerce-image-workspace-0.1.0.zip --json
~~~

结果：

- 包名 `ecommerce-image-workspace`，版本 `0.1.0`，source_commit `cd627ea...`；目录 `release-artifacts/ecommerce-image-workspace-0.1.0`；zip 1,040,844 bytes；file_count 191；dist_files 3；migration_head `0011`。
- `--check --require-dist`：`ok=true`，selected_tracked_files 185，dist_files 3，required_files 24，issues `[]`。
- 密钥扫描：`ok=true`，markers 0，hits `[]`。

### 5.3 生产入口运行检查

脚本：`D:/Desktop/新建文件夹/.scratch/t25-runtime-check.py`，结果写入 `release-artifacts/runtime-check.json`。

结果：

- `app.production --check --json`：entrypoint `app.main:app`，migration_head `0011`，database_path `release-artifacts/runtime-data/app.db`，web_dist_dir `release-artifacts/ecommerce-image-workspace-0.1.0/dist`，import_root 为包内 backend/app。
- 空 SQLite 迁移：0001→0010→0011，末尾日志为 `Running upgrade 0010 -> 0011, add persistent workflow recovery claims`。
- `GET /api/health`：200 `{"status":"ok"}`。
- `GET /`：200，472 bytes，引用 `/assets/index-B8Q_t_Sj.js`。
- `GET /assets/index-B8Q_t_Sj.js`：200，1,174,406 bytes。
- package_index_sha256 与 served_index_sha256 均为 `0c998ae38618359ce17f3007bfeb0d9b3201e86b285e6cd02b2801185e3f6a07`。
- `GET /api/openapi.json` 返回 401（该路由受认证保护）；`openapi_version` 记为 null，不把 401 解释为不存在。
- 检查结束后 18991 无残留进程。

### 5.4 发布相关自动化测试

~~~text
cd D:/Desktop/新建文件夹/backend
backend/.venv/Scripts/python.exe -m pytest -q \
  tests/test_t78_production_entry.py tests/test_t79_release_package.py
~~~

结果：`8 passed in 12.12s`。覆盖生产入口目录/锁文件一致性、发布包 manifest 与实际迁移头、dist 文件、SHA256SUMS、排除测试/数据/密钥及包内容检查。

### 5.5 回归与验收矩阵

- T23 全链路：`test_v3_p7_t23_full_chain_acceptance.py` 2 passed；T13+T75+T65 24 passed；T10/T11/T12/T23 16 passed；全前端 46 files / 217 tests passed；vue-tsc 0；Vite build 0。
- T24 快照/备份恢复：5 passed；T24 + t82 + t54 + t81 40 passed。
- T25 修复后的核心 v3 组合：T08/T09/T10/T11/T12/T13/T23/T24/T65/T75/T87/P0/T06 = 91 passed，1 xfailed。
- 全后端拆分复验（最终集成 HEAD cd627ea，.worktrees/t111/backend）：非 v2 63 个文件 573 passed, 1 xfailed, 2 warnings，702.51s；v2 19 个文件 257 passed, 1 warning，483.58s；合计 830 passed, 1 xfailed, 3 warnings。发布相关 9 文件守卫 55 passed in 48.13s。未出现失败。

## 6. 验收条件逐项对照

| 票面条件 | 结论 | 实际证据 |
| --- | --- | --- |
| 源码、实际运行进程、静态构建、发布包版本及配置分别确认，旧进程/旧静态资源不被掩盖 | 通过（本机范围） | §3、§4：源码/包 source_commit 一致；版本 0.1.0；迁移 0011；三个 dist 文件逐字节一致；旧 Vite PID 32716 已停止；包运行检查通过 |
| 前序集成、兼容回滚和全部验收矩阵无未解决失败/未知后才扩大正式入口 | 部分通过，未扩大入口 | §5.4、§5.5；T23/T24 证据及本地回归；真实 provider/TLS/设备条件未过，正式入口未扩大 |
| 按既有发布程序和明确授权取得真实供应商的一次确认、编制修复、单图提交、独立批准、交付与资源访问证据；未授权不调用 | 未通过 | 本轮无明确授权；provider_network=0、model_text=0、model_vision=0、image_generation=0；历史 release 文档不代签 |
| 受控局域网 HTTPS/可信证书、另一设备、多账号隔离、真实桌面/手机软键盘与交互 | 未通过 | 未建立当前 TLS 入口；无第二台物理设备；T22 仅 headless Edge 三视口，明确将物理手机/软键盘留给 T25 |
| 真实环境导航/刷新/历史/下载零新增调用、当前发布只跳一次、切号/跨标签/晚到回调无私图泄漏；报告通过不替代图片批准 | 未通过（真实环境） | 仅有 T15-T23 模拟/HTTP 证据；T23 已覆盖 stub 下的零调用、发布一次和资产 409 边界，但不等于真实部署 |
| 真实数据来源、维护者确认、备份可恢复、迁移范围沿原 Release 门槛单独核验 | 未通过 | T24 仅临时 SQLite + 合成行；无真实数据根、维护者确认或生产切换 |
| 交付实际变更、运行/包版本、命令与设备步骤、预算/调用/账本/资源结果、残余未知和逐项发布结论 | 部分通过 | 本文件、`p7-t25.json`；设备/TLS/真实 provider/真实数据项明确未通过 |
| 测试、临时文件和验收证据放业务目录外；全部所需证据齐全前保持本票未完成 | 通过（流程） | 证据在 docs/acceptance；临时 DB/脚本在 release-artifacts/.scratch；本票保持未完成 |

## 7. 调用、预算与资源结论

- 真实 provider 调用：0；真实图片生成：0；模型文本/视觉调用：0。
- 本轮的验收接受、预算、确认和交付计数均为 0：没有执行任何真实创建链，只执行了包启动、健康检查、迁移和静态资源访问。
- 运行检查产生的 SQLite 位于 `release-artifacts/runtime-data`，是临时数据；没有创建 GenerationIntent/Usage/Slot、Delivery 或可下载资产。
- 发布包不包含 `backend/data`、`main/backend/data`、测试、缓存或真实 `.env`；密钥扫描 markers=0。

## 8. 残余未知与发布决定

1. 无真实供应商授权和真实模型/图片质量证据；无法签署“一次确认 + 修复 + 单图提交 + 独立批准 + 交付/资源访问”。
2. 无受控局域网 HTTPS/可信证书、第二台设备、多账号真实隔离证据。
3. 无物理桌面/手机软键盘、安全区域和真实交互证据；headless Edge 不是替代。
4. 无真实数据源只读核验、维护者确认、真实备份恢复和生产迁移范围证据；T24 是临时库演练。
5. 全后端拆分运行完成后，在本文件 §5.5 和 `p7-t25.json` 记录准确通过/失败数；未完成前不声明全绿。
6. 本 Windows 工作副本的根 `package-lock.json` 存在 CRLF，和 `main/package-lock.json` 的 LF 不一致，导致首次逐字节测试失败；已仅将工作副本规范化为 LF，Git 对象哈希不变、`git diff` 为空。该环境细节不影响发布包内容。

**逐项发布结论：** 本机源码/构建/包/运行一致性条件通过；真实供应商、TLS/第二设备、物理手机软键盘、真实数据源与恢复条件未通过。**#111 保持打开，V3-Release 不宣布完成；不把本轮本地包验证写成正式发布。**

## 9. 证据位置

- JSON：`docs/acceptance/evidence/p7-t25.json`
- 本文件：`docs/acceptance/p7-t25-runtime-release.md`
- 静态构建与发布包：`release-artifacts/ecommerce-image-workspace-0.1.0/`、`release-artifacts/ecommerce-image-workspace-0.1.0.zip`
- 运行检查：`release-artifacts/runtime-check.json`
- 发布清单：`release-artifacts/ecommerce-image-workspace-0.1.0/RELEASE-MANIFEST.json`
- 临时运行数据：`release-artifacts/runtime-data/`（未纳入版本控制）
