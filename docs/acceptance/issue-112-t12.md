# Issue #124 T12 验收记录：唯一输入框的单图附件与自然语言用途

## 1. 范围与结论

- 工单：#124（V3 纯对话 T12，父票 #112）。
- 目标：在 #122 的唯一底部输入框中支持选择、粘贴或拖放一张商品参考图，发送前预览、替换、移除；发送后图片来自真实消息附件关系，并携带稳定资产版本与内容摘要；用途由自然语言表达或由服务端追问，不增加用途下拉、第二输入表单或第二条发送路径。
- 验收基线：集成分支 `codex/issue-112-integration` 的 `686f082`（T11 合并与验收文档提交后）。本票从该提交新建 `codex/issue-112-t124` worktree。
- 本票变更：`main/src/api/types.ts`、`main/src/api/client.ts`、`main/src/composables/useWorkspace.ts`、`main/src/composables/useChatFlow.ts`、`main/src/pages/ChatPage.vue`、`main/src/components/UnsavedChangesDialog.vue`，以及新增 `tests/t124-attachment.spec.ts`。未修改后端生产代码、数据库迁移、SRS 参数或次数/槽位口径；附件暂存、发送、用途绑定继续调用 #120 已交付的真实接口。
- 结论：唯一输入框的单图选择/粘贴/拖放、预览/替换/移除、首次发送建项目后暂存、真实消息附件展示、用途澄清、上传失败、切换提醒和账号失效晚响应通过前端集成测试、类型检查、生产构建和既有后端附件 HTTP/SQLite 回归；真实浏览器视觉、手机软键盘与真实供应商留 #129/#128。

## 2. 实现面

| 面 | 实现 |
|---|---|
| 唯一输入 | 保留 #122 的固定底部 composer 和唯一 textarea；新增隐藏 `input[type=file]`（aria-label `选择参考图文件`）与按钮 `添加参考图`，没有用途下拉、第二表单或第二个发送入口 |
| 选择/粘贴/拖放 | 文件选择、textarea `paste`、composer `drop` 三条入口最终都调用 `chooseAttachment()`；只接受 PNG/JPEG，其他类型显示安全错误 |
| 单图约束 | `pendingAttachment` 是单值状态；再次选择会替换并尽力删除上一张未发送的暂存资产；待发送区 aria-label `待发送的图片`，只渲染一张预览，提供 `替换图片`/`移除图片` |
| 暂存与最终引用分离 | 使用既有 `POST /api/projects/{id}/message-attachments` 暂存；暂存只创建未绑定 reference Asset，不改变项目 revision、业务参考图或需求；只有发送消息时才向 `POST /api/projects/{id}/messages` 传 `attachment_asset_id` |
| 首次发送 | 根页面未有项目时，先 `POST /api/projects` 建项目，再暂存图片，最后发送带 `attachment_asset_id` 的消息；发送前不建项目、不产生消息 |
| 真实消息附件 | 发送响应的 `workflow` 直接采纳，随后刷新项目详情；消息列表只从 `MessageView.attachment` 渲染图片，alt 为 `随消息发送的图片，用途：{usage}`，不从当前项目缩略图反推历史 |
| 自然语言用途 | 前端不提供用途控件；用途明确时服务端把附件绑定到工作流并返回更新后的 `workflow`；用途不明确时服务端返回澄清问题，消息仍保留 `usage=none`，不猜测业务用途 |
| 生命周期 | 待发送图片计入 `attachmentPending`，切换项目会触发现有 `放弃未保存的修改？`；账号 `sessionRevision` 变化和项目切换会清除本地预览并尽力删除暂存资产；晚到的暂存响应在 token/项目/待发送状态不匹配时删除返回资产，不写入新上下文 |
| 失败处理 | 暂存失败显示后端安全文案或 `图片没有上传成功，请重试。`；失败时保留本地预览用于重试，并阻止无附件的消息发送 |
| 权限与审计 | 未新增入口；暂存/删除/预览/发送均复用 #120 的会话、项目 owner、Asset 访问门禁、内容检查、CSRF 来源和审计路径 |

## 3. 测试面

新增 `tests/t124-attachment.spec.ts`，7 个测试函数/7 个用例，挂载真实 App、路由和 Element Plus；只替换 fetch、时钟、可见性和存储，不 mock 前端业务模块：

- T12-1 文件选择、预览、替换和移除严格只有一张图；重命名/用途下拉不存在；暂存请求为 multipart `file`，替换和移除分别删除旧的暂存资产。
- T12-2 粘贴和拖放替换同一张待发送图片；根页面首次发送前无任何写请求，发送顺序为建项目、暂存、消息，消息携带 `attachment_asset_id`、UUID `request_id` 和真实 revision。
- T12-3 明确自然语言用途后，消息附件以 `usage=product` 展示，图片来自消息附件 `preview_url` 而非项目参考图；只存在一个 textarea，没有旧的 `/reference` 更新请求。
- T12-4 用途不明确时只显示服务端澄清问题，附件保持 `usage=none`，不产生业务用途或工作流动作。
- T12-5 暂存 422 时显示后端安全原因并保留预览；点击发送不会产生消息请求。
- T12-6 有未发送图片时切换对话弹出 `放弃未保存的修改？`，明确列出 `尚未发送的图片`；放弃后切到新项目并删除暂存资产。
- T12-7 账号生命周期变化后清除待发送图片；晚到的暂存响应删除其返回资产，不发送消息、不写入新上下文。

RED 证据：在从 `686f082` 创建的 detached worktree 中运行同一测试文件，7 个用例全部失败（首个失败为找不到 `选择参考图文件`），证明测试覆盖的是新增交互而不是既有能力；随后在实现 worktree 中 7 个用例全部通过。

## 4. 命令与结果

工作目录：`C:/Users/28425/.codex/worktrees/issue-112-t124/新建文件夹`。

```powershell
D:/nodejs/node.exe node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.json
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run tests/t124-attachment.spec.ts --reporter=verbose
D:/nodejs/node.exe node_modules/vitest/vitest.mjs run --reporter=json --outputFile=D:/Desktop/issue-112-context/t124-full2.json
D:/nodejs/node.exe node_modules/vite/bin/vite.js build --config main/vite.config.ts
```

后端回归（既有真实 HTTP + 临时 SQLite 测试，无本票生产代码变更）：

```powershell
cd C:/Users/28425/.codex/worktrees/issue-112-t124/新建文件夹/backend
C:/Users/28425/.codex/worktrees/issue-112-integration/新建文件夹/backend/.venv/Scripts/python.exe -m pytest tests/test_issue_120_t08.py tests/test_issue_121_t09.py -q -p no:cacheprovider --basetemp=D:/Desktop/issue-112-context/t124-backend1
```

结果：

- 类型检查：通过。
- T12 专项：`7 passed`（最新复跑 7.53s）。
- 当前前端全套：13 个 spec 文件，`48 passed / 48`，全部通过；JSON `D:/Desktop/issue-112-context/t124-full2.json`。
- 生产构建：通过（1654 modules；仅有既有大 chunk 警告）。
- 后端回归：`21 passed in 71.52s`（`test_issue_120_t08.py` 6 个消息附件/暂存/安全检查用例 + `test_issue_121_t09.py` 15 个消息入口/工作流用例）。
- RED：基线 detached worktree 中 `7 failed`，JSON `D:/Desktop/issue-112-context/t124-red.json`。
- 静态检查：`git diff --check` 无输出；未在 `main/` 下新增测试、截图或临时产物。

## 5. #124 AC 逐项判定

| 条件 | 结果 | 证据 |
|---|---|---|
| 1 同一输入框选择/粘贴/拖放图片，发送前可预览/替换/移除；严格单张参考图；没有用途下拉或第二输入表单 | 通过 | T12-1/T12-2 断言三种入口、单一预览、替换/移除、只剩一个 textbox 且无 combobox；实现只保留一个 composer |
| 2 发送后图片来自真实消息附件关系，具有稳定资产版本；不能从当前项目缩略图反推历史 | 通过 | T12-3 断言 `MessageView.attachment.preview_url` 与 `usage=product`；后端 `test_issue_120_t08.py` 覆盖 `content_version`、sha256、mime、尺寸和消息绑定；前端不读取项目 `reference_asset` 作为历史 |
| 3 用途由自然语言或追问表达；需求/提示词修改回到唯一输入框；实际依赖与旧授权失效与后端一致 | 通过（前端 + 既有后端） | T12-4 只请求澄清；T12-3 明确用途进入工作流；唯一输入框不新增第二编辑面；用途绑定、旧 PreparedPrompt 失效和最终请求依赖由 #120 后端回归覆盖 |
| 4 草稿/临时 URL 按账号、生命周期和项目隔离；切换提醒、退出清理；晚到上传不能写入新上下文 | 通过 | T12-6 切换对话框列出未发送图片并在放弃后删除暂存；T12-7 账号生命周期递增后清预览、晚到暂存删除返回资产；项目切换和授权失效清除 `messageAttachmentPending` |
| 5 浏览器文本+图片操作结合真实 API/依赖绑定，不用假图片替代持久化 | 通过（集成层；真实浏览器留 #129） | 前端测试通过真实组件 + 受控 fetch 验证交互和请求；后端用真实 HTTP、临时 SQLite 和文件系统证明暂存、消息绑定、访问控制；未使用假图片作为业务持久化替代 |
| 6 仅隔离环境；保护用户未提交修改；测试/文档/截图/临时产物不入应用本体 | 通过 | 只在专用 worktree 修改；原用户树未操作；测试在 `tests/`，文档在 `docs/acceptance/`；构建产物 `main/dist/` 被 gitignore |
| 7 新入口遵守身份/所有者/对象关联/内容门禁/CSRF 来源/审计 | 通过（复用 #120 接口） | 未新增后端写路由；暂存/删除/预览/发送均走既有会话、owner、Asset `can_access_asset`、图片内容检查、CSRF 和审计；T12-5 显示安全错误；后端 #120 覆盖 401/404/403/422、跨项目、已发送和文件变化 |
| 8 不改 SRS 参数或次数口径；不新增免费预览、自动超时、隐式排队、自动图片重试 | 通过 | 无后端/迁移/SRS 变更；上传失败只提供用户主动重试；没有自动重试、超时队列、免费预览或额度变化 |

## 6. 旧展示测试替换关系

#122 已退役旧 V2 展示 spec；本票把“参考图选择/上传/草稿附件”责任落到 T12-1—T12-7，覆盖原 `t05-reference-upload` 与 `t107-draft-attachments` 中仍适用的行为，但不恢复旧的独立上传表单、用途下拉或项目参考图快捷入口。旧项目参考图接口未被复用为消息附件，避免把“选择/预览/移除”伪装成已发送需求变更。

## 7. 残余未知与边界

- 未做真实浏览器视觉、真实手机软键盘、真实供应商生成或真实网络时延验收；#129/#128 负责，当前结论只覆盖 jsdom 组件集成与后端 HTTP/SQLite。
- 严格只支持单张 PNG/JPEG；不做去重、多图、粘贴富文本或拖入目录。
- 暂存文件与数据库绑定不是同一事务；替换/移除/切换时删除为尽力而为，进程硬崩溃可能留下孤立暂存文件（#120 已记录的窄窗口），不影响已发送消息和业务引用。
- 历史图片依赖 `GET /api/assets/{id}` 的 owner/retention 门禁；授权撤销或保留期到期后图片不可读，前端不回退到项目缩略图或缓存。
- 自然语言用途分类由服务端严格契约完成；前端只显示澄清或采纳返回的 workflow。后续补用途路径已有后端 #120 覆盖，本票未新增专门的前端补用途测试。
- 前端测试使用受控 fetch；不证明跨标签页、真实并发上传或浏览器权限弹窗行为，账号生命周期在 T12-7 用 `sessionRevision` 模拟。

## 8. 合并复核（Lead）

- 功能提交：`5c1393bf528e36077de7f7a797cd5ceccd9af311 feat(chat): attach one image to chat messages with natural-language usage (#124)`（8 个文件，+948/−10；唯一输入框单图附件、API 类型/客户端、生命周期状态、7 个测试和验收文档，无后端生产代码或迁移变更）。
- 集成合并：`ab8046cdf69ac3ca362adaed3328680e39c85147 merge: T12 single image attachment with natural-language usage (#124)`，以 `--no-ff` 合入 `codex/issue-112-integration`（第一父提交 `686f082`，第二父提交 `5c1393b`）。
- 合并后前端验收：13 个 spec 文件，`48 passed / 48`（JSON `D:/Desktop/issue-112-context/t124-postmerge.json`）；`vue-tsc --noEmit -p tsconfig.json` 通过；`vite build --config main/vite.config.ts` 通过（1654 modules，既有大 chunk 警告）。
- 合并后后端回归：`test_issue_120_t08.py` + `test_issue_121_t09.py` 共 `21 passed in 81.13s`（basetemp `D:/Desktop/issue-112-context/t124-postmerge-backend`）；本票未改后端生产代码。
- 测试文件一致性：`git diff 5c1393b ab8046c -- tests/t124-attachment.spec.ts docs/acceptance/issue-112-t12.md` 无输出；提交 blob 的测试文件 SHA-256 为 `2f53d0ee0f5a78ae25b139d24722219fffe2b0e63af93aee47f843e3fd24afc4`，功能提交与合并提交一致。
- 合并后工作树：`git status --short` 为空；`git diff --check` 无输出。
- 结论：#124 的唯一输入框单图选择/粘贴/拖放、预览/替换/移除、首次发送暂存、真实消息附件、用途澄清、切换提醒和账号失效晚响应通过合并后前端验收、类型检查、生产构建及既有后端附件/工作流回归；真实浏览器视觉、手机软键盘与真实供应商留 #129/#128。
