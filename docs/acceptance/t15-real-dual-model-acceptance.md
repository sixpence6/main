# T15 真实双模型闭环、持久化与契约验收

- **Issue**：GitHub #29《真实双模型闭环与持久化验收》
- **父规格**：GitHub #14；SRS AC-01/02/03/07/10/11/12；BE-AC-01–15；FE-DEP-01–06
- **记录日期**：2026-10-30（Asia/Shanghai）
- **状态**：**后端验收通过；整体 v1 未完成**。真实 LLM/VISUAL、持久化、重启恢复、OpenAPI、FE-DEP 契约核验均已有证据；Chrome/Edge、视口、视觉与键盘验收仍归 #13，不能据此关闭父规格或宣称整体 v1 完成。
- **受控与真实分离**：§2–§4 是真实供应商与服务重启证据；§5 的 BE-AC 受控测试是补充，不替代真实模型事实。

## 1. 验收环境与复现

| 项 | 值 |
|---|---|
| 仓库/分支 | `a13295877929-gif/main` / `integration/backend-v1` |
| 后端 | FastAPI + uvicorn，`127.0.0.1:8720`，`backend/data/app.db`，迁移 `0001 (head)` |
| LLM | `openai-chat` / `deepseek-v4.1-flash` @ `https://api.ai-pixel.online/v1`，60s |
| VISUAL | `openai-images` / `gpt-image-2` @ `https://usfast.ailzd.com/v1`，同步，300s |
| 前端 | Vue 3 + Vite；`main/` 为产物根；`dist/` 由实际构建生成 |
| Harness | `.scratch/t15-acceptance/run_acceptance.py`，phases `llm/generate/reference/restart` |
| 汇总 | `docs/acceptance/evidence/t15-summary.json`：**52 checks，52 PASS，0 FAIL** |
| 图片 | v1/v2/v3 PNG、参考图，见 `docs/acceptance/evidence/` |

复现（PowerShell，仓库根）：

```powershell
cd backend; .venv\Scripts\python.exe -m alembic upgrade head
# 启动 8720 后（可用 .scratch/serve_8720.py）
.venv\Scripts\python.exe ..\.scratch\t15-acceptance\run_acceptance.py --phases llm,generate,reference,restart
```

该脚本会真实调用两个供应商，产生计费；不得在 CI 或每次提交时自动运行。`.scratch/` 已被 gitignore，仓库内保留脱敏 summary 与最终图片。

## 2. 真实工作台闭环

### 2.1 文字描述 → 澄清 → 需求卡 → 确认 → 单图下载

项目 `64a44549-a929-4a26-8775-37744908b196`：

1. “我想做一张商品图” → 真实 LLM `needs_input`，集中追问商品/用途/风格/构图/参考图/文字；用户原文 sequence 1，助手 sequence 2。
2. 完整描述 → 真实 LLM `review`，五字段齐全、中文 prompt 非空、服务端 `image_size=1024x1024`；sequence 3/4；`prompt_revision=4`。
3. 确认 revision 4 成功；重复确认 200 且状态不变；LLM 阶段 generation 数为 **0**。
4. 提交 v1：202 新记录，轮询至 `succeeded`、`stage=done`；只提交一次，无自动重试。
5. 下载：`Content-Disposition: attachment`、`.png` 文件名；文件为真实 1024×1024 PNG（1,084,652 bytes，SHA-256 `265f9c65...bedf7e`），Pillow 可解码，浏览器之外可打开。
6. 同 request_id 重放返回原 generation/version；succeeded 的 retrieve 返回原 asset，不新增版本、不 re-submit。

### 2.2 反馈 → 新版本

反馈“保持商品和构图不变，把背景改成暖米色，并在杯身右侧加一道柔和高光，整体更温暖” → 真实 LLM review revision 7（商品描述不变、构图保留并加高光、风格改暖米色、text_content 仍空、新 prompt）→ 确认 → v2。v2 为独立成功版本（1,074,333 bytes，SHA-256 `3a5963...e750f`），与 v1 hash 不同；v1 仍可下载。首次反馈整理真实返回 502 `LLM_CALL_FAILED` 时旧状态安全保留，显式重试成功，无自动补发。

### 2.3 参考图 → 生成

- 参考 PNG 上传后 revision 8，`reference_asset_id=6fda10c1-e072-4203-ba61-9a28c1d64034`，存储字节与上传字节 hash 相同。
- 参考图消息真实整理为 review revision 10，卡片携带参考 asset；确认后生成 v3 `d596ae56-871f-4479-9f9f-2515d2a98491`，succeeded、22.3s、原始 `reference_asset_id` 保留。
- v3 为真实 PNG 1024×1024（1,086,956 bytes，SHA-256 `44f209...e6736c`）；生成中只读刷新不新增消息/生成。
- 删除参考图 revision 11，当前引用清空，旧参考文件仍可读。
- 受控 multipart 测试证明进入 `/images/edits` 的是原始参考字节，不是 asset ID 或描述。

### 2.4 版本与重启

- v1/v2/v3 版本号递增、不复用；重启后三版下载 hash 与重启前完全一致。
- 重启前提交一个真实生成并立即杀掉进程；重启后该记录为 `unknown` / `PROCESS_INTERRUPTED`，等待 2 秒仍只有 1 条 generation，没有自动重发或轮询。
- 重启后主项目 revision、消息顺序、generation 数量不变；配置状态仍 `verification=not_checked`。

## 3. 52 项真实检查分组

| 组 | check 序号 | 数量 | 结果 |
|---|---|---|---|
| 配置能力与项目初始状态 | 1–3 | 3 | PASS |
| 真实 LLM 澄清/卡片/确认/编辑 | 4–16 | 13 | PASS |
| 纯文字 v1 生成/下载/重放/重取 | 17–26 | 10 | PASS |
| 反馈、v2、版本隔离 | 27–32 | 6 | PASS |
| 参考图上传/整理/v3/删除 | 33–42 | 10 | PASS |
| 进程中断与重启持久化 | 43–50 | 8 | PASS |
| 分页游标与 OpenAPI | 51–52 | 2 | PASS |
| **合计** | 1–52 | **52** | **52 PASS / 0 FAIL** |

逐项名称和布尔值见 `docs/acceptance/evidence/t15-summary.json`；每个 phase 的完成时间和项目 ID 亦在其中。主要 check 包括：真实回复映射 brief 状态、五字段/prompt、服务端尺寸、零 generation、确认幂等、编辑 revision/draft、未 ready 确认 409、真实 PNG/元数据、attachment/inline、request_id 重放、原结果重取、反馈新 prompt、版本不复用、参考字节不变、生成中只读、unknown 无重发、重启后三版 hash 一致、坏游标 422、OpenAPI 覆盖全部已执行响应。

## 4. 持久化与恢复（SRS AC-10–12）

| 验收 | 真实证据 | 结果 |
|---|---|---|
| 生成中刷新只读恢复原任务 | 参考图阶段 in-flight 项目详情刷新：无新消息、无新 generation；状态/阶段来自服务端 | PASS |
| 成功版本重启后历史和图片可读 | v1/v2/v3 重启后 asset、metadata、hash 全部一致 | PASS |
| 未完成生成重启变 unknown 且不重发 | `824146fb-1bf8-4a4d-b514-c4fe1101ad24` → `unknown`/`PROCESS_INTERRUPTED`；2s 后仍 1 条；无 submit/poll | PASS |
| 遗留 organizing 一次性修复 | `test_t10_restart.py` 覆盖 organizing→draft+`ORGANIZATION_INTERRUPTED`，重复启动 no-op | PASS（受控） |
| 重启不发起 organize/submit/poll/retrieve | `test_t10_restart.py` + 实际重启观察；日志无相关外呼 | PASS |

## 5. BE-AC-01–15 逐项结论

状态均为后端范围结论；`PASS（受控）` 表示由隔离测试证明，`PASS（真实）` 或 `PASS（真实+受控）` 含真实供应商/重启证据。不存在把未执行项写成 pass 的行。

| AC | 结论 | 主要证据 |
|---|---|---|
| BE-AC-01 空配置/缺角色/未知 adapter/错 Key/存储不可写；启动/状态零模型调用；无 Key | PASS（受控+真实配置） | `test_t01_boot.py`、`test_t15_contract.py`、真实 config status、secret scan |
| BE-AC-02 澄清/完整整理/编辑/同值/重整/重复确认；JSON 类型/未知字段/null/长度 | PASS（真实+受控） | 真实 LLM 路径；`test_t03_organize.py`、`test_t04_brief.py` |
| BE-AC-03 两并发整理/冲突编辑/晚到回复；一个处理权、409、原文保留 | PASS（受控） | `test_t03_process.py`、`test_t03_organize.py`、`test_t04_brief.py` |
| BE-AC-04 MIME/伪装/损坏/炸弹/多图/重复字段/边界/超大；零模型调用 | PASS（受控+真实上传） | `test_t05_assets.py`；真实参考上传 hash |
| BE-AC-05 替换/移除/有图/无图/跨项目/文件缺失；旧确认失效、旧历史可读 | PASS（真实+受控） | 真实上传/删除/旧文件读取；`test_t09_reference_versions.py` |
| BE-AC-06 同 ID 并发/不同 ID 竞争/同 ID 改参/ack 省略 vs false/跨项目 | PASS（受控+真实重放） | `test_t06_generation.py`、`test_t07_idempotency.py`；真实 replay 同 ID |
| BE-AC-07 终态后改需求/配置/恢复旧请求；200 原 id/version/snapshot；同 ID 改参 409 | PASS（受控） | `test_t07_idempotency.py`、`test_t11_retrieve.py` |
| BE-AC-08 同步/异步/查询短暂失败/断连/受理不明/总等待；一次 submit、同任务 poll、failed vs unknown | PASS（真实同步+受控异步） | 真实 v1–v3；`test_t08_async.py` |
| BE-AC-09 首版/后续/失败版本；版本不复用、快照/提示词/参考图正确、旧文件可解码 | PASS（真实） | v1/v2/v3 ID、hash、重启下载 |
| BE-AC-10 整理/排队/生成中刷新/中断/重启；只读恢复、只修复一次、不重发 | PASS（真实重启+受控） | `test_t10_restart.py`；真实 interrupted 记录 |
| BE-AC-11 最新 unknown 新 ID 省略/false/true ack；旧 unknown 保留、原 ID 恢复 | PASS（受控） | `test_t11_retrieve.py`、前端 T11 测试 |
| BE-AC-12 下载/解码/尺寸/落盘失败/多图/竞争/配置变更/过期 locator；重取不增版本 | PASS（受控+真实成功重取） | `test_t08_async.py`、`test_t11_retrieve.py`；真实 retrieve 原 asset |
| BE-AC-13 文件丢失/缺记录/路径越界/CDN 重定向/鉴权；missing vs 404、不暴露 locator | PASS（受控） | `test_t05_assets.py`、`test_openai_images_adapter.py`、`test_t11_retrieve.py` |
| BE-AC-14 多页/同时间项目/坏游标/无生成/历史卡片/待核实；排序、422、request_id 精确匹配、OpenAPI 14 方法 | PASS（真实+受控） | 真实分页/坏游标；`test_t02_projects.py`、`test_t15_contract.py` |
| BE-AC-15 实际 dist/未知 API JSON 404/无产物可诊断/配置数据目录不公开 | PASS（后端+构建范围） | 实际 `npm run build` 产物；`docs/acceptance/evidence/runtime-smoke.json` 中未知 API 404 JSON；`test_t01_boot.py`；浏览器验收留 #13 |

## 6. OpenAPI 与运行时契约

实际 `GET /api/openapi.json`：**12 paths / 14 operations**，与 `docs/acceptance/evidence/openapi-v1.json` 和 harness 的 `contract_check.py` 一致；harness 执行了全部 14 个方法、54 个响应，输出 `operations=14 exercised=14 responses=54 issues=0`。

- 所有 4xx/5xx 使用统一 `ErrorResponse{error:{code,message}}`；实际坏游标为 422 `INVALID_CURSOR`。
- 生成 POST 同时声明默认 202 与幂等重放 200，二者共用 `GenerationResponse{generation}`。
- asset GET 媒体类型仅 `image/png,image/jpeg`，使用 FileResponse；下载/inline 语义与真实响应一致。
- 公开 DTO：`ProjectView`、`GenerationView`、`MessageView`、`ProjectSummary`、`ProjectListResponse`、`ProjectDetailResponse`、`ConfigStatus`、`LLMConfigStatus`、`VisualConfigStatus`、`VisualCapabilities`、`ConfigIssue`、`ErrorResponse`、`GenerationResponse`、`MessageExchangeResponse`；无 Key/供应商私有字段。
- nullable/required 与真实响应一致；`verification` 常量 `not_checked`；分页 `limit 1..100 default 20`、`cursor nullable`。
- `backend/tests/test_t15_contract.py` 5 tests 锁定 14 个唯一 operationId、错误响应 schema、202/200 同 DTO、asset 媒体类型和公共 DTO 关键字段。

## 7. FE-DEP-01–06 核验

核验对象：实际 OpenAPI、`main/src/api/types.ts`、`main/src/api/client.ts`、相关组件/组合式函数；结论为**契约与映射核验通过**，浏览器视觉/交互仍由 #13 验收。

| 依赖 | 核验结果 | 说明 |
|---|---|---|
| FE-DEP-01 精确尺寸、供应商字节上限、等待上限、能力字段 | **通过（契约/映射核验）** | `ConfigCapabilities` 8 字段与 `VisualCapabilities` 一致；真实 `1024x1024`、`provider_max_reference_bytes=null`、`effective 10,000,000`；无前端自造上限 |
| FE-DEP-02 分页参数、摘要、下一页判定 | **通过** | `ProjectListResponse`/`ProjectSummary`、`limit/cursor`、`next_cursor` 与实际 200 一致；真实 limit=1 分页走通 |
| FE-DEP-03 消息/需求快照、生成快照、版本排序、request_id | **通过** | `MessageView.brief_snapshot`、`GenerationView.brief_snapshot/version_no/request_id` 与真实响应/OpenAPI 一致；真实版本递增 |
| FE-DEP-04 阶段、错误细分、可重取原因、重取完成方式 | **通过** | `status/stage/error/can_retrieve_result/retrieval_unavailable_reason` 类型与真实值一致；重取返回原 asset |
| FE-DEP-05 创建/确认/卡片/上传请求响应字段、修订号与空值 | **通过** | 14 个 client 方法路径/参数与 OpenAPI 一致；`expected_revision`、nullable 字段、multipart 上传均匹配；typecheck 通过 |
| FE-DEP-06 配置完整性与实际调用证据来源 | **通过** | `verification` 仅 `not_checked`；真实调用后仍未被前端写成“已验证”；来源为后端 config status |

`main/src/api/types.ts` 中 `ConfigCapabilities`/`ModelConfigStatus` 是本地别名，字段结构与公开 `VisualCapabilities`/`LLMConfigStatus` 对应，不构成契约偏差。核验未发现需要修改前端映射的偏差；若 #13 浏览器验收发现展示问题，按 #13 流程修正并重跑本节检查。

## 8. 验证、构建与安全

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端全量 pytest | **169 passed in 65.11s** | `docs/acceptance/evidence/test.txt` |
| Ruff | All checks passed | `docs/acceptance/evidence/backend-checks.txt` |
| Alembic | `0001 (head)` | `docs/acceptance/evidence/backend-checks.txt` |
| 前端 typecheck | 通过（`vue-tsc --noEmit`） | `docs/acceptance/evidence/typecheck.txt` |
| 前端完整测试 | **13 files / 158 tests passed**，9.56s | `docs/acceptance/evidence/test.txt` |
| 前端生产构建 | 通过，1617 modules；JS 1,094.90 kB（gzip 347.88）、CSS 394.14 kB（gzip 53.68）；保留 >500kB chunk 警告 | `docs/acceptance/evidence/build.txt` |
| Key 扫描 | 184 个文件、3 个 Key 值、0 匹配；日志无 `sk-`/Bearer/Authorization/b64_json | `docs/acceptance/evidence/secret-scan.json` |
| 启动/GET 零付费调用 | config status、项目读取、OpenAPI、asset GET 不触发模型；受控测试断言零调用；真实 LLM 阶段 generation=0 | 测试 + t15-summary |
| 无模拟成功回退 | 真实 502 保留失败；unknown 不被写成成功；无假图片/假下载 | 真实反馈 502、interrupted 记录 |

## 9. 外部限制与未完成项

- **浏览器验收未完成**：#13 的 Chrome/Edge、375/768/1279/1280/1440 视口、视觉对比、键盘/焦点、减少动效、真实图片在页面中的展示，均未在本记录执行；FE-AC-01–22 仍不能标通过。
- 供应商文档：VISUAL 无公开官方文档，能力按实测；参考图供应商上限/URL 有效期/限流阈值未知。
- LLM 上下文窗口和真实超限阈值未知；上下文超限仅由受控 400/413 映射覆盖。
- 真实失败路径并非全部可付费复现；错误/边界/并发主要由受控测试证明，已逐项标注。
- 图像主观质量、设计审美不在自动化验收范围；本记录只证明真实 PNG 交付、版本和可读性。
- 可选多模态 organizer 在 reference 阶段启用，属于 v1 扩展；严格文本基线需清空 `LLM_VISION_MODEL`（见 T13 §4）。

## 10. 交给 #13 的证据

- 真实闭环：T13、T14 本目录记录；`t15-summary.json` 52 checks。
- 三版真实图片：`generation-v1.png`、`generation-v2.png`、`generation-v3.png`；参考图：`reference-vision-red.png`。
- 契约：`openapi-v1.json`、`t15-summary.json` 的 OpenAPI 检查、T15 §6/§7。
- 重启/持久化：T15 §4；真实 interrupted generation ID 与恢复项目 ID 在 summary。
- 浏览器待办：按 `t12-real-api-browser-acceptance.md` §4/§6 执行；不得把本记录的“后端通过”改写成“FE-AC 通过”。

## 11. 结论

**T15 后端交付通过**：真实双模型闭环、参考图、反馈新版本、刷新只读、进程中断/重启、历史图片可读、OpenAPI 14 方法、FE-DEP 契约核验、169+158 测试与构建均有可复核证据。**整体 v1 仍未完成**，因为 #13 的 Chrome/Edge 人工浏览器验收尚未执行；父规格 #14 保持 open，不因本记录关闭或改写。
