# A5 基线：当前工作台 320/768/1440 截图与浏览器环境

对应 map「前端视觉系统升级：高端沉稳」的 A5 票。本目录是**改造前**的真实渲染基线，用于后续设计令牌落地后做前后对比。只采集，不改代码。

## 采集环境

| 项 | 值 |
| --- | --- |
| 浏览器 | Microsoft Edge 156.0.4314.58（headless，Playwright 1.56.1 `playwright-core` + `chromium.launch({ executablePath: msedge.exe })`） |
| 视口 | 320×800 / 768×1024 / 1440×1000，DPR = 1，`reducedMotion: no-preference` |
| 语言/时区 | `zh-CN` / `Asia/Shanghai` |
| 前端 | Vite 8.3.5 dev server（`vite --config main/vite.config.ts --host 127.0.0.1 --port 4173`） |
| 后端 | `main/backend` FastAPI + uvicorn，127.0.0.1:8001，隔离 SQLite（alembic 0001→0016），`VITE_API_PROXY_TARGET=http://127.0.0.1:8001` |
| 账号 | 隔离实例管理员 `baseline@example.test`（仅本地 `.tmp/a5/data/app.db`，与真实用户数据无关） |
| 实测量字体栈 | `"PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif`（正文 58–86 个节点）与 `"Times New Roman"`（登录页衬线标题，23 个节点） |

> 端口说明：Vite 默认 5173 落在 Windows 保留端口段（`netsh interface ipv4 show excludedportrange` 显示 5141–5240 等段），绑定返回 `EACCES`，因此改用 **4173**；后端 `ALLOWED_ORIGINS` 同步放行 `http://127.0.0.1:4173`。

## 状态矩阵（6 状态 × 3 宽度）

| 状态 | 文件 | 数据来源 |
| --- | --- | --- |
| 登录页 | `01-login-{320,768,1440}.png` | **全真实**：真实 dev server + 真实后端 401 会话 |
| 聊天空态 | `02-chat-empty-{320,768,1440}.png` | **全真实**：登录后工作台首屏 |
| 消息流 | `03-chat-messages-{320,768,1440}.png` | **全真实**：数据库内 4 条消息（2 轮 user/assistant），经真实 `GET /api/projects/{id}` 返回 |
| 输入区（聚焦 + 草稿） | `04-composer-{320,768,1440}.png` | **全真实**：同一页面，composer 聚焦并输入草稿 |
| 提示词确认 | `05-prompt-confirm-{320,768,1440}.png` | 真实组件树 + **浏览器网络层 fixture**：仅替换 `GET /api/projects/{id}/workflow`（`prompt_preview`、`stage=reviewing`、`confirm_prompt` 动作） |
| 图片状态 | `06-image-state-{320,768,1440}.png` | 真实组件树 + **网络层 fixture**：替换项目详情（1 条 succeeded generation）、workflow（`delivery.status=published` + 1 个 published version）与 `/api/assets/**`（1600×900 占位 PNG） |

**为什么有两态用 fixture**：真实产出「提示词已生成」与「成品图已发布」需要触发 `llm`（`deepseek-v4.1-flash`）与 `visual`（`gpt-image-2-4k`）真实模型调用；本票约束是不触碰真实数据/外部计费，因此这两态只固定**渲染输入**，页面组件、路由、样式均为真实代码。状态文案与布局（胶囊「等待确认提示词」「已交付」、结果页版本栏）均按真实 `WorkflowView`/`GenerationView` 契约构造。

## 关键测量（`metrics.json`，`03-chat-messages-*` 页面）

- **横向溢出**：320 / 768 / 1440 全部 `scrollWidth - clientWidth = 0`，无水平滚动条。
- **媒体查询命中**：320 → `max-width:767px`；768 → `min-width:768px` 且未命中 1024；1440 → 命中 `min-width:1024px`。768–1023 段仍是单一中宽度布局（A1 审计记的「断点崖」现象在本基线可见）。
- **字号分布**（出现次数）：14px（44/44/54）、16px（24/24/25）、12px（6/6/10）、26px（2）、13px、15px、10px、8px/9px —— 三档宽度下仍是多套字号并存，未收敛到令牌。
- **圆角分布**：`0px` 为主（68–86），另有 `28px`（面板）、`18px 18px 5px` / `18px 18px 18px 5px`（气泡非对称圆角）、`11px`、`10px`、`9px`、`22px`。
- **主色实测**：靛紫 CTA `rgb(88, 70, 232)`（`#5846E8`，用户气泡/主按钮）、冷灰底 `rgb(238, 240, 248)`（`#EEF0F8`）、卡片白 `rgb(255,255,255)`、浅灰画布 `rgb(245,246,250)`、淡紫选中 `rgb(238,234,255)`。
- **视觉分裂证据**：登录页左侧深暖褐（近 `#2E2119`）+ 衬线大标题 + 金色光球装饰，右侧象牙白表单与靛紫按钮；进入工作台后是冷灰紫系统 —— 与 A1 审计「登录暖米色 vs 聊天冷灰紫」结论一致。
- **控制台**：320/768/1440 各有 1 次 `401`（未登录态的会话探测，属预期）与 1 次 `404`（320 下 favicon/dev 资源）。

## 渲染内容校验（`verify.json`）

每张截图的像素尺寸与采集视口一致，且每态取 DOM 文本作非视觉证据：

| 宽度 | 六张截图尺寸 | 输入区焦点 | 登录页文本锚点 | 空态文本锚点 |
| --- | --- | --- | --- | --- |
| 320 | 320×800 ×6 | `document.activeElement === textarea` ✅ | 「登录工作台」「把商品想法，整理成一张可交付的主图」 | 「发来商品图片，或告诉我你想做什么样的电商图片」 |
| 768 | 768×1024 ×6 | ✅ | 同上 | 同上 |
| 1440 | 1440×1000 ×6 | ✅ | 同上 | 同上 |

消息流三档均渲染出 4 条真实消息 + 「当前状态 已就绪」胶囊；320/768 下侧栏收起为「对话」抽屉标题（A1 记的 768–1023 单一中宽度布局在此可见）。

## 复现步骤

```bash
# 1) 隔离后端（工作区外数据目录，避免污染真实数据）
export PYTHONPATH="$PWD/main/backend" DATA_DIR="$PWD/.tmp/a5/data" \
       DATABASE_URL="sqlite+aiosqlite:///$PWD/.tmp/a5/data/app.db" \
       ALLOW_LOOPBACK_HTTP=true AUTH_COOKIE_SECURE=false \
       ALLOWED_ORIGINS='["http://127.0.0.1:4173"]' ALLOWED_HOSTS='["127.0.0.1","localhost"]'
backend/.venv/Scripts/python.exe -m alembic -c main/backend/alembic.ini upgrade head
backend/.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# 2) 账号与种子数据
backend/.venv/Scripts/python.exe .tmp/a5/init_admin.py     # baseline@example.test
backend/.venv/Scripts/python.exe .tmp/a5/seed_messages.py  # 项目 + 4 条消息

# 3) 前端
VITE_API_PROXY_TARGET=http://127.0.0.1:8001 \
  node ./node_modules/vite/bin/vite.js --config main/vite.config.ts --host 127.0.0.1 --port 4173

# 4) 截图（Node 需能直接跑浏览器；本仓库 DSH 沙箱默认禁止 pipe stdio，需完整访问）
node .tmp/a5/capture.mjs
node .tmp/a5/capture6.mjs
```

脚本原样收录于 `scripts/`：`capture.mjs`（状态 1–5）、`capture6.mjs`（图片状态）、`verify.mjs`（渲染校验，产出 `verify.json`）、`seed_messages.py`、`init_admin.py`、`make_png.py`、`run-capture.sh`、`run-image.sh`、`placeholder-1600x900.png`。

## 局限

1. headless Chromium/Edge on Windows 的字体栅格与真实 macOS/高分屏略有差异；字体栈本身已实测记录。
2. 提示词确认与图片状态的数据在浏览器网络层注入，未经过真实工作流运行；其 DOM/样式与真实渲染一致，但若后端返回额外字段（例如质量报告、审核结论），本基线未覆盖。
3. 未触及任何真实用户数据、未调用外部模型；隔离库位于 `.tmp/a5/data/`，可随时删除。
4. 截图只覆盖 6 个状态，未包含：侧栏折叠动画中间帧、错误态（模型失败）、管理后台（`/admin/accounts`）、账号安全页。
