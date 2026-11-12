# R3 T24 品牌登录与个人／管理页面完整交互验收记录

- Issue: #76
- 父 Issue: #52
- 分支: ticket/76
- 基线: integration/v3 492989f
- 代码提交: 6d10a569f5b2c038dbdcf042fd0f070a3f68553f
- 验收日期: 2026-11-01
- 状态: 通过（R3 T24 阶段条件；R3 门禁见 #77）

## 1. 结论与边界

本票把登录、一次性链接设置密码、账号安全和管理员账号管理统一为项目自有的品牌体验，同时保留 R1 的权限、会话、链接、限流和审计契约。所有视觉元素均在仓库内实现，不引用参考站脚本、图片、字体或外部运行依赖，也没有加入注册、邮件发送、充值或账号池等虚假业务。

1. 登录页使用自有品牌壳层，产品说明、账号表单和帮助信息在桌面为双栏，在窄屏为单栏。邮箱输入具有显式 label、username 自动填充、输入提示和关联错误；密码输入具有显式 label、current-password 自动填充、显示／隐藏按钮、关联提示和错误。
2. 登录失败时只显示服务端返回的通用凭据错误，不枚举账号；限流、服务不可用和一般失败分别显示可理解文案。无法登录的帮助明确要求联系管理员生成一次性重置链接，并说明系统不发送邮件、不会通过聊天、电话或工单索要密码。
3. 一次性链接页按 loading、invalid、ready、done 四种状态呈现。链接只从路由 hash 读取，先完成 preauth CSRF，再读取链接状态；设置成功后清空字段并替换路由以移除 token。页面显示 15–128 个 Unicode 码点、两次一致等实时要求，并说明密码不会被写入本地存储。
4. 账号安全页提供当前密码、新密码、确认新密码三个密码字段，均支持显示／隐藏、自动填充和字段级错误；保留本人会话列表、逐会话撤销、撤销其他会话和安全事件列表。页面明确说明忘记密码或账号被停用时应联系管理员。
5. 管理员账号页保留创建待设置密码账号、停用／恢复、签发一次性链接和配额管理。创建邮箱在前端做有效性校验并关联错误；链接弹窗显示目标账号和用途；账号状态使用正常、待设置密码、已停用等真实状态；窄屏表格改为带 data-label 的卡片，操作按钮不横向溢出。
6. 本票没有放宽任何服务端权限。普通账号仍不能访问管理员路由；管理员仍不能读取他人创作；所有变更动作继续要求会话、CSRF、Origin 与 Host 校验；链接仍是一次性、30 分钟、原子消费，GET 不消费也不登录。
7. 页面使用语义化标题、label、aria-describedby、aria-invalid、role=alert、role=status 和可聚焦控件。密码显示按钮暴露显示[标签]／隐藏[标签]、aria-pressed 和 aria-controls。基础样式中的 prefers-reduced-motion 规则继续生效。
8. 登录、设置密码和账号安全页都不把密码、会话 token 或一次性链接写入 localStorage／sessionStorage。真实浏览器检查确认登录后本地存储为空；页面也没有“零保留”或供应商不保留数据的承诺。
9. 没有新增后端路由、数据库迁移或后台任务；迁移头仍为 0010。所有账号、链接、会话与配额行为沿用 #55–#58、#67 的既有服务与审计。
10. 本票验收边界为临时 SQLite、完整 App、真实 HTTP 和 headless Edge，账号与链接数据由种子脚本创建；不访问真实数据根，不调用真实模型。headless Edge 的 CSS 像素和键盘操作不能替代真实手机／桌面硬件的人工验收，该边界在残余风险中列明。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t76 |
| 分支 | ticket/76 |
| 基线 | integration/v3 492989f |
| Node | v26.12.1 |
| npm / Vitest | Vitest 5.0.5 |
| Vite | 8.3.5 |
| Python | 3.12.13（uv 工作树 .venv） |
| 数据库 | 临时 SQLite，alembic 0010，26 张表 |
| 浏览器 | headless Microsoft Edge，CDP |
| 模型适配器 | LLMStub / VisualStub，无外部调用 |

## 3. 实现摘要

### 3.1 品牌壳层与标记

- 新增 main/src/components/BrandMark.vue：31 行内联 SVG 标记，currentColor，aria-hidden，无外部资源。
- 新增 main/src/components/AuthFrame.vue：361 行登录／设置密码共用壳层，包含产品介绍、内联 SVG 标识、纯 CSS 画布、useId 生成的标题关联、默认插槽和页脚插槽。桌面宽度 min(1120px, 100%)，两栏 minmax(0, 1.06fr) / minmax(420px, 0.94fr)；900px 以下单栏并隐藏说明区，520px 以下全宽无边框。
- 没有引用参考站的脚本、图片、字体、颜色变量或业务文案。

### 3.2 可复用密码字段

- 新增 main/src/components/PasswordField.vue：159 行，useId 生成 input／hint／error id，label 显式关联。
- 显示／隐藏按钮具有 显示[标签]／隐藏[标签]、aria-pressed、aria-controls；input 具有 aria-invalid、aria-describedby，保留调用方传入的 autocomplete、autocapitalize=off、spellcheck=false。
- label 中不使用必填星号，避免可访问名称被污染；input 仍为 required。

### 3.3 登录页

- LoginPage.vue 重写为 227 行：AuthFrame、ACCOUNT ACCESS／登录工作台、邮箱 label=登录邮箱、autocomplete=username、邮箱格式校验、PasswordField current-password、统一表单错误区、提交中状态。
- 401 显示通用凭据错误，429 显示尝试过多，503 显示服务暂不可用；错误通过 aria-describedby 关联到对应字段和表单。
- 帮助区明确联系管理员、系统不发邮件、不通过聊天／电话／工单索要密码；页脚说明会话只在受保护 Cookie 中，页面不把密码写入浏览器存储。

### 3.4 一次性链接设置密码页

- SetPasswordPage.vue 重写为 355 行：purpose 对应标题、loading 正在核实链接…、invalid 链接不可用、ready 表单、done 成功四态。
- 新密码和确认密码均为 PasswordField，autocomplete=new-password，15–128 长度限制；实时策略列表显示至少 15 个 Unicode 码点、不超过 128 个 Unicode 码点、两次输入一致。
- 校验错误覆盖空值、长度、两次不一致和服务端错误；成功后清空字段并 replace 路由，token 不再留在地址栏。

### 3.5 账号安全页

- AccountSecurityPage.vue 扩展为 663 行：品牌头部、账号信息、三个密码字段、修改密码、当前会话、其他会话撤销、安全事件和联系管理员帮助。
- 字段级错误包括 请输入当前密码。／新密码至少需要 15 个 Unicode 码点。／新密码不能与当前密码相同。／两次输入的新密码不一致。
- 会话与事件继续使用服务端安全投影字段，不显示 token、摘要或内部 id 以外的秘密。
- 修复了该页在 1440px 下由 flex min-content 造成的真实横向溢出：security-shell 使用 minmax(0, 1fr)，关键 flex 子项 min-width: 0，session-main overflow: hidden，session-list／event-list 使用 minmax(0, 1fr)。修复后实测 frame 780／728／296，三视口均无横向溢出。

### 3.6 管理员账号页

- AdminAccountsPage.vue 扩展为 1010 行：BrandMark、ADMIN 标识、总数／正常／待设置／已停用汇总、状态 pill、创建账号、签发链接、停用／恢复、配额面板与事件列表。
- 创建邮箱使用显式 label、输入提示和外部错误区 #create-account-error；无效邮箱不发请求。
- 链接弹窗显示目标账号：email · 用途：重置密码，成功后一次性显示链接并提示仅显示一次；Escape 可关闭。
- 720px 以下表格改为卡片，单元格使用 data-label，操作按钮全宽排列；三视口实测无横向溢出。

### 3.7 测试

- 新增 tests/t76-auth-experience.spec.ts，7 个前端测试；既有认证测试 5 个文件／15 个测试继续通过。
- 测试覆盖品牌与本地资源、label／autocomplete／错误关联、密码显示隐藏、一次性链接四态、账号安全字段错误和窄屏布局。
- 没有删除或放宽既有有效断言；全量前端由 43 个文件／248 个测试增至 44 个文件／255 个测试。

## 4. 命令与结果

### 4.1 专项前端测试

命令：

    cd D:/Desktop/新建文件夹/.worktrees/t76
    npm test -- --run tests/t76-auth-experience.spec.ts --reporter=dot

结果：1 个文件／7 个测试通过，退出码 0。认证相关既有 5 个文件／15 个测试通过，退出码 0。

### 4.2 全量前端测试

    npm test -- --run --reporter=dot

结果：44 个文件／255 个测试全部通过，22.07s，退出码 0。日志 .scratch/t76-evidence/t76-frontend-full.log。只有既有 fireEvent.change／input 警告。

### 4.3 类型检查

    npm run typecheck

结果：退出码 0。日志 .scratch/t76-evidence/t76-typecheck.log。

### 4.4 生产构建

    npm run build

结果：Vite 8.3.5，1676 个模块，658ms，退出码 0；dist/assets/index-C-melY_5.css 430.37 kB，dist/assets/index-BI7rhaDn.js 1,217.70 kB；只有 chunk 体积警告。日志 .scratch/t76-evidence/t76-build.log。

### 4.5 后端回归

    cd D:/Desktop/新建文件夹/.worktrees/t76/backend
    C:/Users/28425/.local/bin/uv.exe run python -m pytest [显式测试文件] -q

结果：非 v2 39 个文件／354 个测试通过，2 个警告，414.28s，退出码 0；v2 19 个文件／235 个测试通过，330.32s，退出码 0；合计 589 个测试，无失败、跳过或未知。唯一警告为 backend/tests/support/auth.py:42 的 per-request cookie 弃用警告。日志 t76-backend-nonv2.log、t76-backend-v2.log。

### 4.6 真实浏览器／HTTP／临时 SQLite／完整 App 验证

    D:/nodejs/node.exe D:/Desktop/新建文件夹/.scratch/t76-browser/run_auth.mjs

结果：退出码 0，ok=true，42 项检查通过。脚本启动临时 SQLite、执行 alembic 0010、以 uvicorn 启动完整 App，种子创建管理员、正常账号 B、待设置账号 C 和已停用账号 D，然后使用 headless Edge CDP 操作。

检查覆盖：

1. 登录页本地品牌、显式 label、username／current-password 自动填充、无障碍提示和密码显示按钮；页面没有外部脚本、图片或字体请求；减少动态效果时动画／过渡为 0.00001s。
2. 登录页 1440px 双栏 592.531／525.469（壳层 1120），768px 单栏 726，320px 单栏 320，均无横向溢出；Tab 从邮箱到密码，Space 显示密码。
3. 错误密码返回通用 401，字段 aria-invalid=true 且错误通过 aria-describedby 关联；正确账号 B 登录后进入工作台，localStorage／sessionStorage 为空。
4. 账号安全页存在三个密码字段、三个显示按钮、两个会话和联系管理员帮助；1440／768／320 无横向溢出。
5. 管理员页汇总为总数 4／正常 2／待设置 1／已停用 1，状态显示正常／待设置密码／已停用；无效创建邮箱关联错误且无 POST。
6. 重置链接弹窗目标为 B、用途为重置密码，生成一次性链接成功，Escape 关闭；管理员页三视口无横向溢出，320px 为 data-label 卡片。
7. 设置密码页通过真实一次性链接打开，账号掩码为 b***@example.com，标题为设置登录密码；三视口无溢出；17 个 Unicode 码点的中文密码被接受且未截断；键盘可切换显示；两次不一致显示关联错误且零 POST。
8. 设置成功后清空 hash 和字段，C 使用中文密码登录成功；全程没有注册请求、邮件请求、LLM 调用或图片提交，也没有外部网络请求。

临时数据库完整性检查通过，26 张表；没有读取或写入 backend/data 与 main/backend/data。

## 5. 验收条件对照

| 验收项 | 本票证据 | 结论 |
| --- | --- | --- |
| V3-AC-04 | 管理员创建待设置账号、邮箱唯一由 R1 服务保证；管理员页创建表单、状态汇总与无效邮箱检查；普通用户无管理路由访问 | 阶段条件通过 |
| V3-AC-05 | 账号安全页修改密码、字段校验、会话撤销；设置密码成功使旧密码和旧会话失效；停用／恢复沿用 R1 行为 | 阶段条件通过 |
| V3-AC-06 | 设置密码页四态；链接只从 hash 读取，先 preauth CSRF 再读取；GET 只查看不消费；一次性链接弹窗仅显示一次 | 阶段条件通过 |
| V3-AC-10 | 登录使用正式 Cookie；真实浏览器 localStorage／sessionStorage 为空；页面文案不承诺记住密码 | 阶段条件通过 |
| V3-AC-11 | 会话空闲／绝对时限由 R1 服务端执行；账号安全页只显示服务端会话状态，不在前端续期 | 阶段条件通过（浏览器临界见 R1） |
| V3-AC-12 | 账号安全页本人会话列表、逐会话撤销、撤销其他会话；改密／重置／禁用继续由 R1 撤销旧会话 | 阶段条件通过 |
| V3-AC-22 | AuthFrame、BrandMark、本地 CSS 与文案；无参考站脚本、图片、字体、注册、充值或账号池业务 | 通过 |
| V3-AC-34 | 账号页事件列表显示安全事件；敏感变更审计与回滚仍由 R1 服务执行，前端只展示安全字段 | 阶段条件通过 |
| V3-AC-38 | 1440／768／320 三视口实测 label、焦点、键盘、中文输入、状态与无横向溢出；真实硬件边界见残余风险 | 阶段条件通过 |
| V3-AC-39 | 本票页面不发送认证秘密；登录与设置密码提示不写入本地存储；没有零保留承诺；模型数据流提示由工作台与 #60 覆盖 | 阶段条件通过 |

## 6. 证据位置

- 前端测试：D:/Desktop/新建文件夹/.worktrees/t76/tests/t76-auth-experience.spec.ts
- 全量前端日志：D:/Desktop/新建文件夹/.scratch/t76-evidence/t76-frontend-full.log
- 类型检查日志：D:/Desktop/新建文件夹/.scratch/t76-evidence/t76-typecheck.log
- 构建日志：D:/Desktop/新建文件夹/.scratch/t76-evidence/t76-build.log
- 后端日志：D:/Desktop/新建文件夹/.scratch/t76-evidence/t76-backend-nonv2.log、t76-backend-v2.log
- 浏览器脚本：D:/Desktop/新建文件夹/.scratch/t76-browser/run_auth.mjs
- 浏览器结果：D:/Desktop/新建文件夹/.scratch/t76-browser/browser-result.json（42 项检查，ok=true）
- 浏览器服务日志：D:/Desktop/新建文件夹/.scratch/t76-browser/server.log

## 7. 残余风险与未覆盖边界

1. 真实浏览器为 headless Edge，不是实体手机或桌面人工验收；CSS 像素、键盘和焦点可重复，但触控手感、系统输入法候选框和真实密码管理器扩展仍需 Release 阶段人工复核。
2. 账号、链接和会话使用种子脚本与 fake 适配器创建；没有验证真实邮件（系统不发送邮件）、真实短信或第三方身份提供方。
3. 本票只证明页面行为和 R1 服务契约，不证明密码哈希成本、限流并发或审计回滚的底层实现；这些由 R1 测试与 #64 门禁继续覆盖。
4. 管理页配额面板只验证既有字段与交互，不重新证明配额竞争；配额由 #67／#70 覆盖。
5. 页面没有对供应商零保留、绝对安全或合规认证作出承诺；模型数据流提示的实际模型调用边界由 R2／Release 真实模型验收复核。
6. 本票不改变四页旧路由；三状态收敛和旧路由退场由 #77 复核。
