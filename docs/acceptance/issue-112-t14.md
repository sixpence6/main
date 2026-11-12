# Issue #126 T14 验收记录：多轮图片历史与旧链接只读定位

## 1. 范围与结论

- 工单：#126（V3 纯对话 T14，父票 #112）。
- 目标：多轮修改后的每张图片与当时真实使用的提示词都留在原对话；刷新、回看、放大、下载和旧阶段/成果链接只读定位，不改变修改基准，也不触发生成副作用。
- 验收基线：集成分支 `codex/issue-112-integration` 的 `5b2c849`（T13 合并与验收文档提交后）。本票从该提交新建 `codex/issue-112-t126` worktree。
- 本票变更：`main/src/composables/useChatFlow.ts`、`main/src/pages/ChatPage.vue`，以及新增 `tests/t126-history.spec.ts`。未修改后端生产代码、数据库迁移、SRS 参数或次数/槽位口径；历史数据继续来自既有 `GET /api/projects/{id}/workflow` 的 `versions` 投影。
- 结论：多轮图片与各自已批准提示词按版本展示，刷新后不重复插入；查看历史不改变当前基准；撤销、过期、文件缺失或内容门禁不通过时明确显示不可用，不用当前图或其他版本兜底；旧阶段链接只进入对话入口，旧成果链接保留只读结果页定位；所有浏览、放大、下载和定位均无业务写请求或生成副作用。真实浏览器视觉、手机软键盘和真实供应商留 #129/#128。

## 2. 实现面

| 面 | 实现 |
|---|---|
| 历史事实来源 | `useChatFlow.imageHistory` 只读取服务端 `WorkflowView.versions`；每个条目保留 `run_id`、`sequence_no`、`requirement_revision`、`asset_id`、`filename`、`caption`、`published_at`、`prompt`、`can_download`。不使用当前项目缩略图、当前参考图或本地缓存反推旧消息图片 |
| 多轮追加 | 每个已发布版本独立渲染为 `article[aria-label="版本 N 结果"]`；新图片和新提示词追加到同一对话，不覆盖旧条目。刷新只重新读取同一 `versions` 投影，不向本地数组插入，因此不会重复 |
| 提示词对应关系 | 每个版本展开自己的 `prompt.positive_prompt`（兼容 `prompt.prompt`）和 `negative_prompt`；某版本没有可展示提示词时显示“该版本提示词不可用”，不回退到当前提示词 |
| 当前与历史 | `run_id` 与当前 `WorkflowView.run_id` 相同的版本标为“当前版本”；最后发布的版本标为“最新已发布”；其余标为“历史版本”。查看旧版本仅展开提示词或打开大图，不写入任何选择状态、不改变当前基准 |
| 刷新恢复 | 图片、版本号、需求版本、提示词和下载能力全部由 `versions` 快照派生；测试卸载后重新挂载，仍恰好两条版本、两张图，没有重复节点 |
| 不可访问状态 | 版本 `asset_id` 为空时不渲染图片、大图链接或下载链接，显示“该版本图片不可用”和固定安全说明；当前交付被撤销时显示单独“当前结果不可用”区域，不展示旧图兜底 |
| 当前成功口径 | 只有当前版本确有可预览 `asset_id` 时才显示“图片已经生成，可以保存使用了。”；当前图不可访问时不会把旧版本或占位状态称作成功 |
| 放大与下载 | 图片大图链接指向受保护的 `GET /api/assets/{asset_id}`；下载链接追加 `?download=true`，仍由既有资产 owner、保留期和 `can_access_asset` 门禁控制 |
| 旧成果链接 | 每个历史版本提供 `/projects/{projectId}/results?version={runId}` 的只读结果页链接；既有 `ResultsPage` 继续只展示对应已发布可下载版本，缺版本时给出安全说明且不做其他版本兜底 |
| 旧阶段链接 | `/describe`、`/confirm`、`/generate`、`/results` 继续保持既有重定向到对话根入口；测试断言只渲染欢迎态、路径为 `/`，且没有业务写请求 |
| 副作用边界 | 浏览历史、展开提示词、打开大图、下载和结果页定位都不调用 `POST /workflow/actions/*`；任务动作仍只来自服务端 `available_actions`，不补造最终授权或自动重生成 |
| 权限与审计 | 未新增后端写入口；历史查询使用既有 owner/项目关联读取路径，资产字节仍走既有权限/内容门禁；页面只进行 GET 读取 |

## 3. 测试面

新增 `tests/t126-history.spec.ts`，7 个测试函数/7 个用例。测试挂载真实 App、路由和 Element Plus，只替换 fetch、时钟、可见性和存储，不 mock 前端业务模块。

- T14-1 两轮发布图片和各自提示词按版本保留，旧图、当前图、提示词详情互不串用；卸载并重新挂载后仍恰好两条版本、两张图片，没有重复插入。
- T14-2 查看历史提示词不切换当前基准；页面仍只有一个底部输入框，且零业务写请求、零动作 POST。
- T14-3 历史版本资产不可访问时显示“该版本图片不可用”，不渲染图片、大图或下载链接；该版本提示词仍可读，当前图片不受影响。
- T14-4 当前版本图片不可访问时不把旧版本标成当前成功；显示“当前结果不可用”安全说明，旧版本只作为“历史版本”保留。
- T14-5 当前交付已撤销时不展示旧图兜底，也没有“图片历史”假成功区域。
- T14-6 旧版本下载、 查看大图、只读结果页链接指向真实 `run_id`/资产，且零业务写请求。
- T14-7 旧阶段链接 `/describe`、`/confirm`、`/generate`、`/results` 只进入对话根入口，不触发准备或生成。

既有前端证据：`tests/t105-results-history.spec.ts` 4 个用例继续通过，覆盖 `/projects/:id/results?version=run-old` 的只读历史、缺失提示词不回退、缺失版本不替代和零写请求。

后端既有证据（本票未改后端生产代码）：

- `test_v2_t44_versions.py::test_request_new_version_creates_fresh_run_and_preserves_published_history`
- `test_v2_t44_versions.py::test_versions_view_contains_only_published_approved_snapshots`
- `test_v2_t44_versions.py::test_two_published_versions_keep_each_other_downloadable`
- `test_v2_t44_versions.py::test_approved_but_unpublished_version_can_start_feedback_but_not_history`
- `test_v2_t44_versions.py::test_replacing_reference_after_publish_keeps_completed_run_and_old_file`
- `test_v2_t44_versions.py::test_late_old_run_artifact_cannot_replace_current_version`

以上 6 个用例通过（`6 passed in 9.61s`），覆盖新版本保留旧 run/旧资产、`versions` 只含已发布批准快照、两版互相可下载、未发布版本不进历史、替换参考后旧文件保留、迟到旧 run 不覆盖当前版本。

RED 证据：在基线 `5b2c849` 的独立 detached worktree 中先放入 `tests/t126-history.spec.ts`，运行结果为 `5 failed / 5`，首个失败为找不到 `region` 名称“图片历史”；随后实现历史区并补齐 2 个边界用例，当前 7/7 通过。

## 4. 命令与结果

工作目录：`C:/Users/28425/.codex/worktrees/issue-112-t126/新建文件夹`。

```powershell
D:/nodejs/node.exe node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.json
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run tests/t126-history.spec.ts --reporter=verbose
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run --reporter=json --outputFile=D:/Desktop/issue-112-context/t126-full3.json
D:/nodejs/node.exe node_modules/vite/bin/vite.js build --config main/vite.config.ts
```

后端既有历史测试：

```powershell
cd C:/Users/28425/.codex/worktrees/issue-112-t126/新建文件夹/backend
C:/Users/28425/.codex/worktrees/issue-112-integration/新建文件夹/backend/.venv/Scripts/python.exe -m pytest <上述 6 个用例> -q -p no:cacheprovider --basetemp=D:/Desktop/issue-112-context/t126-backend2
```

结果：

- 类型检查：通过。
- T14 专项：`7 passed`（3.94s）。
- 当前前端全套：30 个 spec 文件，`62 passed / 62`，全部通过；JSON `D:/Desktop/issue-112-context/t126-full3.json`。
- 生产构建：通过（1654 modules；仅有既有大 chunk 警告）。
- 后端既有版本历史 6 个用例：`6 passed in 9.61s`（basetemp `D:/Desktop/issue-112-context/t126-backend2`）。
- 静态检查：`git diff --check` 无输出；未在 `main/` 下新增测试、截图或临时产物。
- 已知既有红：`test_v2_t44_versions.py::test_full_new_version_chain_preserves_old_and_publishes_new` 与 `test_t75_delivery_history.py::test_current_delivery_content_gate_and_feedback_preserve_history` 仍按旧 V2 动作路径调用 `submit_generation`，在基线已因当前 `FINAL_CONFIRMATION_REQUIRED` 契约失败；不是本票引入，本票未改后端生产代码。

## 5. #126 AC 逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| 1 至少两轮实质修改各自准备/展示/最终确认，新图追加不覆盖旧图；刷新从真实需求/执行/交付快照恢复且不重复插入 | 通过 | T14-1 两版本不同 `run_id`、需求版本、图片和提示词；卸载重挂后仍 2 条版本/2 张图。后端 6 个用例证明新版本保留旧 run/资产、`versions` 只含已发布批准快照、两版互相可下载 |
| 2 历史图片/提示词对应各自轮次，默认继续最新，查看历史不改基准或派生新分支；修改仍回唯一输入框 | 通过 | T14-1 展开各版本只显示自身提示词；T14-2 查看旧版本后仍有唯一 textbox 且零写请求；页面没有历史选择状态或第二输入框 |
| 3 撤销/过期/文件缺失正确显示，清理缓存并拒绝访问，无旧缓存私有图或其他版本兜底 | 通过 | T14-3 旧版本资产 null/不可下载时不渲染图片/链接但仍保留自身提示词；T14-4 当前版本不可访问时不把旧版本称当前成功；T14-5 撤销交付无旧图兜底；后端内容门禁测试与 `test_replacing_reference_after_publish...` 覆盖旧资产仍受权限控制 |
| 4 权限成立后旧阶段/成果 URL 定位原对话或对应历史结果，不建独立成果工作流、不自动跳转 | 通过 | T14-7 四个旧阶段 URL 只到对话根入口；T14-6 结果页链接带真实 `run_id`；既有 `t105-results-history` 4 用例覆盖只读结果页、缺失版本不替代；`ResultsPage` 未新增生成/发布动作 |
| 5 导航、打开、刷新、历史定位、放大、下载无准备/生成副作用，资产字节仍走权限/内容门禁 | 通过 | T14-1/2/6/7 全部断言零业务写请求和零动作 POST；图片/下载使用既有 `GET /api/assets/{id}` 门禁；无新增后端入口 |
| 6 仅隔离环境验证，不操作真实用户数据/凭据/运行配置；保护用户未提交修改；测试、文档、截图、临时产物不进应用本体 | 通过 | 只在专用 worktree 修改；原用户树仅只读核对；测试在 `tests/`、接受文档在 `docs/acceptance/`；构建产物被 gitignore；无后端迁移/运行配置变更 |
| 7 新入口遵守身份/所有者/对象关联/内容门禁/CSRF 来源与审计 | 通过（复用既有读路径） | 无新增后端写路由；历史由既有 owner 过滤的 workflow 查询返回；资产/下载继续走既有资产权限与内容门禁；页面只读，不产生业务审计事件 |
| 8 不改 SRS 参数或次数口径；不新增免费预览、自动等待超时、隐式排队或自动图片重试；证据不足保持未知 | 通过 | 无 SRS/迁移/后端生产变更；无免费预览、超时、队列或自动重试；真实浏览器/手机/供应商证据未声称通过，留 #129/#128 |

## 6. 旧展示与测试替换关系

#122 已退役旧 V2 展示 spec，本票把“多轮图片历史、历史提示词、旧链接只读定位”责任落到 `t126-history.spec.ts`。旧 `ResultsPage` 保留为只读历史定位页，不恢复旧阶段工作台、旧结果页独立生成流程或自动跳转；其既有 `t105-results-history.spec.ts` 继续通过。

## 7. 残余未知与边界

- 未做真实浏览器视觉、真实手机软键盘、真实供应商生成或真实网络时延验收；#129/#128 负责，当前结论只覆盖 jsdom 组件集成与既有后端 HTTP/SQLite 证据。
- 前端历史测试使用受控 `WorkflowView.versions` 响应验证展示与零写；未实际调用真实生成供应商。
- `versions` 投影只包含后端已发布的交付版本；未发布、撤销或内容门禁失败的版本不会出现在历史中，当前交付不可用时由安全状态区提示。
- 历史条目按 `sequence_no` 升序展示，最新版本在底部；没有额外的“只看当前”筛选、分页或批量下载，本票不扩展这些交互。
- 旧 `ResultsPage` 的只读定位能力沿用既有实现；真实旧链接来源、权限过期和跨标签页生命周期完整组合仍留 #127/#129。
- 既有旧 V2 全链路用例的失败原因是 `submit_generation` 未先完成当前最终确认契约，属于基线已知问题，不在本票扩大修复。

## 8. 合并复核（Lead）

- 功能提交：`df2ab87931c816ff301f15e1dcaee3863cf2d450 feat(chat): preserve multi-round image history and read-only links (#126)`（4 个文件，+596/−14；多轮图片历史、各自提示词、旧链接只读定位、7 个测试和验收文档，无后端生产代码或迁移变更）。
- 集成合并：`8771430a230d1afe45f8189aba8125bbe92f32dd merge: T14 multi-round image history and read-only links (#126)`，以 `--no-ff` 合入 `codex/issue-112-integration`（第一父提交 `5b2c849`，第二父提交 `df2ab87`）。
- 合并后前端验收：30 个 spec 文件，`62 passed / 62`（JSON `D:/Desktop/issue-112-context/t126-postmerge.json`）；`vue-tsc --noEmit -p tsconfig.json` 通过；`vite build --config main/vite.config.ts` 通过（1654 modules，既有大 chunk 警告）。`tests/t105-results-history.spec.ts` 的 4 个旧结果页只读用例仍包含在全套通过中。
- 合并后后端版本历史：选定的 6 个 `test_v2_t44_versions.py` 用例 `6 passed in 11.00s`（basetemp `D:/Desktop/issue-112-context/t126-postmerge-backend`）；本票未改后端生产代码。
- 测试文件一致性：`git diff df2ab87 8771430 -- tests/t126-history.spec.ts docs/acceptance/issue-112-t14.md` 无输出；测试文件 SHA-256 为 `c80f4503a0db405293a66df839095d0c06993d1a0c7b0778cfa58e0df5fff6c3`，功能提交与合并提交一致。
- 合并后工作树：`git status --short` 为空；`git diff --check` 无输出。
- 结论：#126 的多轮图片/提示词历史、刷新不重复、历史浏览不切基准、不可访问安全状态、下载/大图/旧成果链接只读定位和旧阶段链接无副作用，通过合并后前端验收、类型检查、生产构建及既有后端版本历史回归；真实浏览器视觉、手机软键盘和真实供应商留 #129/#128。
