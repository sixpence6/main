# 设计令牌 v3：颜色、表面、字阶、间距与组件规则

> 状态：已评审（#135，2026-11-06）。本文件是 v3 前端视觉的令牌规格与组件规则，供实现票逐条落地。
> 它不修改 #112 的行为、路由、权限、文案与安全契约；真实浏览器证据（逐屏截图与验收）归 #137 和实现票。
> 输入：A1 视觉审计（#133）、A2 原型 A（#134）。方向定为 **A 冷银极简**；B 石墨画廊不采用。

## 0. 不变量

1. 只用系统字体栈。不引入外部字体、图片、图标字体或运行时依赖。
2. 文字颜色一律实色。任何把 `rgba()` 叠在文字上的做法都是缺陷（当前侧栏 8–10px 文字用 alpha 叠色，是「字融入背景」的主因）。
3. 每个文字令牌对其可能出现的每个底色 ≥ 4.5:1。入表的色值都已实测（见 §2），未达标的值不得进表。
4. CJK 文字最小 12px；11px 只允许纯拉丁大写微标签（`letter-spacing ≥ .08em`，且不是唯一信息载体）。13px 档取消。
5. 交互控件边界（输入框、按钮描边、选中条）≥ 3:1；装饰性发丝线不做此要求，但不得承载语义。
6. 状态表达永远三重：形状/图标 + 文字 + 颜色；颜色只是第三重。
7. 键盘焦点永远可见：2px 实色环 + 2px offset。任何组件不得 `outline: 0`。
8. 尊重 `prefers-reduced-motion`。
9. 深色壳只属于聊天工作台；登录/账号/管理员页跟随颜色与字阶令牌，不套壳、不保留暖米色 + 宋体 + 金色装饰。
10. 令牌只在 `tokens.css` 定义一次；组件里不写颜色、字号、间距字面量。

## 1. 颜色令牌

### 1.1 表面

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--surface-canvas` | `#f6f4ef` | 主画布、消息流背景 |
| `--surface-panel` | `#fcfbf7` | 卡片、输入区、浮层卡、状态胶囊 |
| `--surface-inset` | `#efede6` | 内嵌块、user 气泡、代码块、结果底 |
| `--surface-media` | `#e9e6de` | 图片占位底、附件缩略图底 |
| `--surface-glass` | `rgba(252,251,247,.88)` | 唯一允许的浅色 alpha，仅用于覆盖内容之上的浮层 |
| `--surface-shell` | `#17191d` | 侧栏与顶栏 |
| `--surface-shell-raised` | `#1e2126` | 侧栏内嵌块（搜索框、用户块） |
| `--surface-shell-hover` | `#202329` | 侧栏列表 hover |
| `--surface-shell-active` | `#24272d` | 侧栏列表选中 |
| `--scrim` | `rgba(12,14,17,.4)` | 唯一允许的深色 alpha，仅抽屉/弹层遮罩 |

深色壳上的 hover/选中必须用实色（上表），不得再用 `rgba(255,255,255,.035)` 这类叠加。

### 1.2 线

| 令牌 | 值 | 对 panel 对比度 | 用途 |
| --- | --- | --- | --- |
| `--line-hairline` | `#dfddd5` | 1.31 | 装饰分隔、卡片边界、气泡边界 |
| `--line-hover` | `#c9c8c2` | 1.62 | 卡片 hover / 选中边界（唯一的「加深」状态） |
| `--line-control` | `#8f8c84` | 3.24 | **控件边界**：输入框、按钮描边、选中指示 |
| `--line-inverse` | `#2c2f35` | 深色 1.31 | 深色壳发丝线 |
| `--line-inverse-control` | `#6b7077` | 深色 3.24 | 深色壳控件边界 |

发丝线只做视觉分隔，不表达状态；表达状态的边界必须用 `--line-control`（浅）或 `--line-inverse-control`（深）。

### 1.3 文字

浅色面三档：

| 令牌 | 值 | canvas | panel | inset |
| --- | --- | --- | --- | --- |
| `--text-primary` | `#1b1d21` | 15.35 | 16.30 | 14.41 |
| `--text-secondary` | `#4a4f57` | 7.50 | 7.96 | 7.04 |
| `--text-muted` | `#636870` | 5.10 | 5.42 | 4.79 |

深色壳四档：

| 令牌 | 值 | shell | raised | active |
| --- | --- | --- | --- | --- |
| `--text-inverse-primary` | `#f4f1e9` | 15.59 | 14.30 | 13.26 |
| `--text-inverse-secondary` | `#c7cbd1` | 10.80 | 9.91 | 9.19 |
| `--text-inverse-muted` | `#a9adb4` | 7.81 | 7.17 | 6.64 |
| `--text-inverse-meta` | `#8c9198` | 5.55 | 5.09 | 4.72 |

作废：`#81858c`（3.37–3.58:1，原型里用在 8–9px 上）与所有 alpha 文字色。

### 1.4 动作与焦点

| 令牌 | 值 | 备注 |
| --- | --- | --- |
| `--action-primary` | `#1b1d21` | 主按钮底（墨色），无品牌色相 |
| `--action-primary-hover` | `#2c3037` | 主按钮 hover（ivory 字对比 11.74） |
| `--action-primary-text` | `#f4f1e9` | 主按钮文字（对底 14.95） |
| `--action-soft` | `#eceae3` | 次级强调底（替代 `#EEEAFF`） |
| `--action-link` | `#1e5a73` | 链接、可点文字（对 canvas 6.91） |
| `--focus-ring-light` | `#1e5a73` | 浅色面上的焦点环（6.48–7.33） |
| `--focus-ring-dark` | `#cfe6f2` | 深色壳上的焦点环（11.58–13.62） |

`#1e5a73` 对 `#17191d` 只有 2.32:1，所以深色壳必须用 `--focus-ring-dark`：焦点环的颜色随所在表面切换，不能只用一个环色。

### 1.5 语义状态

| 令牌 | 值 | 柔底 | 状态色作文字于柔底 |
| --- | --- | --- | --- |
| `--state-success` | `#35745B` | `#e7eee9` | 4.68 |
| `--state-warning` | `#8A611E` | `#f0eae0` | 4.61 |
| `--state-danger` | `#B23B4B` | `#f2e5e6` | 4.73 |
| `--state-info` | `#4965A8` | `#e5e9f0` | 4.65 |

状态色在其可能出现的浅色面上 4.71–5.60，合格。深色壳不使用语义色（对比不足），状态只用银灰文字 + 形状/图标表达。

## 2. 实测对比表

测算方式：WCAG 2.x 相对亮度公式，用 Node 脚本对写死的十六进制值求比值；不是浏览器渲染测量，也不含抗锯齿与色域差异。所有表面均为实色（alpha 只出现在 `--surface-glass` 与 `--scrim`，不承载文字），因此表中不含 alpha 表面。

| 前景 | `#f6f4ef` | `#fcfbf7` | `#efede6` | 判定 |
| --- | --- | --- | --- | --- |
| `#1b1d21` 文字 | 15.35 | 16.30 | 14.41 | 通过 |
| `#4a4f57` 文字 | 7.50 | 7.96 | 7.04 | 通过 |
| `#636870` 文字 | 5.10 | 5.42 | 4.79 | 通过 |
| `#1e5a73` 链接/焦点 | 6.91 | 7.33 | 6.48 | 通过（非文本仍需 ≥3） |
| `#35745B` | 5.03 | 5.34 | 4.72 | 通过 |
| `#8A611E` | 5.02 | 5.32 | 4.71 | 通过 |
| `#B23B4B` | 5.28 | 5.60 | 4.95 | 通过 |
| `#4965A8` | 5.15 | 5.47 | 4.83 | 通过 |
| `#8f8c84` 控件边界 | 3.06 | 3.24 | — | 通过（非文本 ≥3） |
| `#dfddd5` 发丝线 | 1.24 | 1.31 | — | 装饰，不承载信息 |
| `#c9c8c2` hover 线 | 1.53 | 1.62 | — | 装饰，不承载信息 |

| 前景 | `#17191d` | `#1e2126` | `#24272d` | 判定 |
| --- | --- | --- | --- | --- |
| `#f4f1e9` 文字 | 15.59 | 14.30 | 13.26 | 通过 |
| `#c7cbd1` 文字 | 10.80 | 9.91 | 9.19 | 通过 |
| `#a9adb4` 文字 | 7.81 | 7.17 | 6.64 | 通过 |
| `#8c9198` 文字 | 5.55 | 5.09 | 4.72 | 通过（`#202329` 上 4.96） |
| `#cfe6f2` 焦点环 | 13.62 | 12.50 | 11.58 | 通过 |
| `#6b7077` 控件边界 | 3.53 | 3.24 | — | 通过（非文本 ≥3） |
| `#2c2f35` 发丝线 | 1.31 | 1.20 | — | 装饰，不承载信息 |
| `#62676e` | 3.09 | 2.83 | — | 不采用（raised 上不达标） |

其它已核对的对：`#f4f1e9` on `#1b1d21` 14.95；on `#2c3037` 11.74；`#1b1d21` on `#eceae3` 14.02；`#4a4f57` on `#eceae3` 6.85。`#636870` on `#e9e6de` 恰为 4.50（边缘），媒体占位上的文字改用 `#4a4f57`。

## 3. 字体与字阶

```css
--font-sans: system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
--font-mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
```

把 `system-ui` 提到首位：Windows 上拉丁与数字落到 Segoe UI（原型 A 的观感来源），macOS 落到 SF，CJK 落到苹方/雅黑。等宽只用于代码、ID、时间戳。

| 令牌 | 字号/行高 | 字重 | 用途 |
| --- | --- | --- | --- |
| `--text-caption` | 12/18 | 500 | 元信息、时间、计数、标签 |
| `--text-body` | 14/22 | 400 | 界面默认正文、列表、按钮 |
| `--text-reading` | 15/25 | 400 | 消息正文、提示词正文、确认卡正文 |
| `--text-section` | 17/24 | 600 | 区块标题、卡片标题 |
| `--text-page` | 20/28 | 600 | 页面/面板标题 |
| `--text-display` | `clamp(26px, 3.2vw, 40px)/1.15` | 600 | 登录页与空态的主标题 |

规则：

- 320 / 768 / 1440 三档**不缩放字号**，只换内边距与顶栏高度（§8）。因此 12px 是全局下限，不是窄屏下限。
- 取消 13px 档：原型里的 13.5px 统一落到 `--text-body`（14）或 `--text-reading`（15），不再有半像素字号。
- 11px 只允许纯拉丁大写微标签（如 `3 MONTHS`），`letter-spacing: .08em`，且同一信息必须以可读形式出现在别处。
- 字重只有 400 / 500 / 600；不用 300（CJK 细体在浅底上发灰），不用 700 以上。
- 数字与计数加 `font-variant-numeric: tabular-nums`，避免时间跳动。
- 行高只允许上表的值；正文不再出现 1.735、1.8 这类原型值。

## 4. 间距、圆角、边框、阴影、布局

**间距**：4 点制，`--space-1` … `--space-16` = 4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64。补上缺失的 `--space-5: 20px`（当前被引用但未定义）。

**圆角**：只有四档。

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--radius-sm` | `8px` | 控件、按钮、列表项、气泡内缩略图 |
| `--radius-md` | `14px` | 卡片、输入区、user 气泡、结果卡 |
| `--radius-lg` | `19px` | 大块容器、浮层、抽屉边缘 |
| `--radius-pill` | `999px` | 胶囊、头像、状态点容器 |

原型的 22px composer、10/16/28 旧档全部并入上表；图片内嵌圆角用 `--radius-sm`（8），与容器 14 形成清晰的内外层次（原型曾写 12，为守住四档收敛为 8）。

**边框**：统一 1px 实线；不使用 2px 以上描边，除焦点环（2px）与选中左条（2px）。

**阴影**：只有两级，且不用于消息气泡。

```css
--shadow-composer: 0 10px 30px rgba(23, 25, 29, 0.06);
--shadow-overlay: 0 24px 64px rgba(12, 14, 17, 0.24);
```

**布局**：

| 令牌 | 值 | 说明 |
| --- | --- | --- |
| `--sidebar-width` | `264px` | 常驻侧栏（≥1024） |
| `--topbar-height` | `48px` | 聊天顶栏；<768 为 44px |
| `--conversation-max` | `760px` | 对话列，恒 `min(760px, 100%)` |
| `--results-max` | `1120px` | 结果页维持现值，宽度策略交 #136 |

删除 `--radius-shell`（28px）与 `--body-padding`：壳不再有 12px 外边距与白卡浮起，横向内边距改由 §8 的三档变量提供。

## 5. 图标

- 内联 SVG，`stroke-width: 1.5`，`stroke: currentColor`，无填充（状态点等实心标记除外）。
- 三档：`--icon-sm: 16px`、`--icon-md: 20px`、`--icon-lg: 24px`。不再出现 14/18/22。
- 废除字形成图标（`＋`、`×`、`⋯` 当按钮）与图标字体；这些位置改为 SVG，保留 `aria-label`。
- 图标单独出现时必须带可访问名称；与文字并排时 `gap: var(--space-2)`。

## 6. 动效

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--ease-out` | `cubic-bezier(.22,.8,.3,1)` | 唯一曲线 |
| `--dur-fast` | `120ms` | hover、颜色、透明度 |
| `--dur-base` | `180ms` | 展开、淡入、面板出现 |
| `--dur-slow` | `240ms` | 抽屉、整块位移 |

- 删除 `--ease-spring`（14 处）与所有回弹/过冲；不做循环动画与呼吸效果。
- `prefers-reduced-motion: reduce` 下取消位移、缩放与循环动画，只允许 ≤120ms 的透明度变化；抽屉改为直接出现。

## 7. 组件规则

### 7.1 侧栏（深色壳，264px）

- 结构：品牌区 48px（与顶栏同高）→ 搜索框 → 会话分组 → 底部用户块。
- 分组标签：`--text-caption` 12/18，500，`--text-inverse-meta`；拉丁标签可大写 + `.08em`。
- 会话行：高 36px，`--radius-sm`，左右 12px 内边距；标题 `--text-body` 14/22，`--text-inverse-secondary`；时间/计数 `--text-caption`，`--text-inverse-meta`。
- 状态：hover `--surface-shell-hover`；选中 `--surface-shell-active` + 左侧 2px `#c7cbd1` 竖条（内缩 4px）+ 标题升为 `--text-inverse-primary`。选中不得只靠颜色，竖条是第二重。
- 搜索框：`--surface-shell-raised` 底 + 1px `--line-inverse-control` 边 + `--radius-sm`；占位文字 `--text-inverse-muted`（7.17 以上）。
- 底部用户块：24px 圆形头像 + 名字 14/500 + 套餐元信息 12/`--text-inverse-meta`。
- 禁止：任何 9/10px 文字、任何 alpha 文字色、任何只靠颜色区分选中。

### 7.2 消息气泡

- user：`--surface-inset` 底 + 1px `--line-hairline` + `--radius-md` **四角统一**（去掉原型的 4px 尾角）；`max-width: 78%`；内边距 12/16；正文 `--text-reading` 15/25，`--text-primary`。
- assistant：不套气泡，直接排在画布上；宽度 92% 列宽；正文 `--text-reading` 15/25，`--text-primary`；角色/时间标签 `--text-caption`，`--text-muted`。
- 消息内代码块：`--surface-inset` 底 + `--radius-sm`，等宽 13/20（唯一允许的 13px，仅限代码与 ID）。
- 消息间距：同角色 12px，换角色 24px；不使用分割线。
- 附件：缩略图 34×42、`--radius-sm`、`--surface-media` 底；文件名 12/`--text-secondary`。
- 不允许：气泡阴影、渐变、毛玻璃、透明度表达「发送中」（用文字或图标）。

### 7.3 输入区（composer）

- 容器：`--surface-panel` 底 + 1px `--line-hairline` + `--radius-md` + `--shadow-composer`；内边距 12/16。
- 文本域：`--text-reading` 15/25，`--text-primary`；占位文字 `--text-muted`（对 panel 5.42）。
- 聚焦：`outline: 2px solid var(--focus-ring-light)` + `outline-offset: 2px`，同时边框转 `--line-control`；不得只用光晕（当前 2.50:1 的做法作废）。
- 动作行：左侧附件/工具按钮 32×32，`--radius-sm`，20px 图标，hover `--surface-inset`；右侧发送按钮 36×36 墨色圆角 8，图标 `--action-primary-text`。
- 提交中/禁用：禁用 = `--surface-inset` 底 + `--text-muted` 文字，不降整体透明度（透明度会让对比度不可控）。
- 移动端（<768）：输入区贴底，`padding-bottom: max(var(--space-3), env(safe-area-inset-bottom))`。

### 7.4 按钮与状态

- 主按钮：`--action-primary` 底、`--action-primary-text` 文字、`--radius-sm`、高 36、左右 16、`--text-body` 500；hover `--action-primary-hover`；无阴影。
- 次按钮：透明底 + 1px `--line-control` + `--text-secondary`；hover 底 `--surface-inset`。
- 幽灵按钮：无边框，hover `--surface-inset`，文字 `--text-secondary`。
- 状态胶囊：`--surface-panel` 底 + 1px `--line-hairline` + `--radius-pill`，高 24，12px 文字；内含 16px 图标 + 文字。
- 四态（成功/警告/失败/信息）必须同时出现图标与文字：图标形状可辨（对勾/三角/叉/圆），文字给结论（「已完成」「需要处理」），颜色只作第三重。深色壳上的状态同样用图标 + 文字 + 银灰，不用语义色。
- 不允许：只靠红色/绿色区分、状态点无文字、双层阴影胶囊。

### 7.5 图片与结果卡

- 结果卡：`--surface-panel` 底 + 1px `--line-hairline` + `--radius-md`；内边距 12；hover 只把边框换成 `--line-hover`，不加阴影、不位移、不缩放。
- 3:4 图片：`--radius-sm`，底 `--surface-media`；图片自带圆角，不额外裁切容器。
- 不可用图：`--surface-inset` 底 + 图标 + 一行说明文字（`--text-secondary`），不降透明度、不显示灰色渐变占位。
- 卡内文字：标题 `--text-body` 500；来源/尺寸 `--text-caption` `--text-muted`；媒体占位上的文字用 `--text-secondary`（`#4a4f57`），因为 `#636870` 在 `#e9e6de` 上恰为 4.50。
- 点击区域整卡可点，焦点环画在卡片外沿（offset 2），避免被 `overflow: hidden` 裁掉。

### 7.6 焦点（全局）

- 统一 `:focus-visible`：`outline: 2px solid var(--focus-ring-light); outline-offset: 2px;`；深色壳容器内改用 `--focus-ring-dark`。
- 组件不得写 `outline: 0` / `outline: none`；确需自定义时用 `box-shadow` 模拟环也必须 ≥3:1 且保留 `outline` 兜底。
- 键盘顺序：侧栏 → 顶栏动作 → 消息区 → 输入区；抽屉打开时焦点入抽屉并 `aria-modal`，关闭后回到触发按钮。

## 8. 响应式（320 / 768 / 1440）

| 档位 | 横向内边距 | 顶栏 | 侧栏 | 对话列 |
| --- | --- | --- | --- | --- |
| < 768 | 16px | 44px | 抽屉，宽 `min(272px, 100vw - 32px)` | `min(760px, 100%)` |
| 768–1023 | 24px | 48px | 抽屉（行为沿用 #112） | 同上 |
| 1024–1279 | 24px | 48px | 常驻 264px | 同上 |
| ≥ 1280 | 32px | 48px | 常驻 264px | 同上 |

- 三档只变内边距、顶栏高度与侧栏形态；颜色、圆角、阴影、字号完全一致，不做「窄屏缩小字号」。
- 320 宽下：可用列宽 288px，user 气泡 `max-width: 78%` ≈ 224px；结果卡改为单列；结果页外层内边距必须降到 16px（当前仍是 24px，是已知缺陷）。
- 768–1023 不得同时出现移动顶栏与 68px 桌面顶栏（当前 768×1024 同时存在 52px mobile-bar 与 68px topbar 的断点崖，属于本次要修的结构问题）。
- `env(safe-area-inset-*)` 只作用于 <768 的底部输入区。

## 9. 旧 → 新映射与删除顺序

| 旧令牌 | 旧值 | 新令牌 | 新值 |
| --- | --- | --- | --- |
| `--color-page` | `#EEF0F8` | `--surface-canvas` | `#f6f4ef` |
| `--color-panel` | `#FFFFFF` | `--surface-panel` | `#fcfbf7` |
| `--color-panel-soft` | `#F5F6FA` | `--surface-inset` | `#efede6` |
| `--color-code` | `#F5F6FA` | `--surface-inset` | `#efede6` |
| `--color-glass` | `rgba(255,255,255,.86)` | `--surface-glass` | `rgba(252,251,247,.88)` |
| `--color-text` | `#1F2438` | `--text-primary` | `#1b1d21` |
| `--color-text-muted` | `#626D88` | `--text-muted` | `#636870` |
| `--color-primary` | `#5846E8` | `--action-primary` | `#1b1d21` |
| `--color-primary-hover` | `#4736CD` | `--action-primary-hover` | `#2c3037` |
| `--color-primary-soft` | `#EEEAFF` | `--action-soft` | `#eceae3` |
| `--color-border` | `#E5E8F3` | `--line-hairline` | `#dfddd5` |
| `--color-border-soft` | `#EEF0F7` | `--line-hairline` | `#dfddd5` |
| `--color-accent` | `#7A67F0` | `--action-link` | `#1e5a73` |
| `--color-success` | `#35745B` | `--state-success` | 同值 |
| `--color-warning` | `#8A611E` | `--state-warning` | 同值 |
| `--color-danger` | `#B23B4B` | `--state-danger` | 同值 |
| `--color-info` | `#4965A8` | `--state-info` | 同值 |
| `--radius-control` | 10px | `--radius-sm` | 8px |
| `--radius-panel` | 16px | `--radius-md` | 14px |
| `--radius-image` | 20px | `--radius-sm` | 8px（卡片内嵌图） |
| `--radius-shell` | 28px | 删除 | 壳全出血，无容器圆角 |
| `--shadow-panel` | `0 12px 40px rgba(35,40,70,.07)` | `--shadow-composer` / `--shadow-overlay` | 见 §4 |
| `--ease-spring` | 回弹曲线 | 删除 | 只用 `--ease-out` |
| `--sidebar-width` | 288px | 同 | 264px |
| `--topbar-height` | 68px | 同 | 48px（<768 为 44） |
| `--conversation-width` / `--conversation-max` | 880px | `--conversation-max` | 760px |
| `--body-padding` | 16/20/24 | 删除 | 由 §8 三档内边距取代 |
| `--head-height` | 56/64 | 删除 | 登录页直接用 `--topbar-height` 语义 |

删除顺序（每一步都可单独提交、单独回滚）：

1. `tokens.css`：追加新令牌；旧名改成 `var()` 别名指向新值；补 `--space-5`、`--text-small`、`--text-heading`、`--color-error`、`--color-muted`、`--radius-card`（前两个尺寸指 `--text-caption` / `--text-section`，颜色指 `--state-danger` / `--text-muted`，圆角指 `--radius-md`），先修掉组件里的无效引用（结果页状态块丢 padding 即由此产生）。
2. `base.css`：统一 `:focus-visible`，删掉 1.20:1 的光晕写法。
3. 删除 `workspace.css` L9–32 的 `.workspace-shell` 重复令牌覆盖（该类未渲染，真实壳是 `.pure-shell`）。
4. 按组件替换：壳（全出血、去白卡）→ 侧栏（264、实色 hover/选中、去掉 8–10px）→ 消息（气泡四角 14、正文 15/25）→ 输入区（14 + `--shadow-composer` + 焦点环）→ 按钮/状态（三重表达）→ 结果卡（悬停只换线色）。
5. 删除旧名别名（保留一个迁移周期后），`grep -r "var(--color-" src` 归零。
6. 删除未使用 CSS 与 `WorkflowPanel` 死代码；`prefers-reduced-motion` 与 768 断点崖一并在这一步收口。

## 10. 验收清单

自动检查（可在 CI 跑）：

- [ ] `grep -rn "font-size: *\([0-9]\|1[01]\)px" src` 为空（8/9/10/11px 清零；11px 仅允许 `.label--micro` 白名单）。
- [ ] `grep -rnE "color: *rgba\(" src` 为空（文字禁 alpha）。
- [ ] `grep -rn "outline: *\(0\|none\)" src` 为空。
- [ ] 每个 `var(--x)` 都有定义（脚本比对 `tokens.css` 中的 `--x:`）。
- [ ] `grep -rn "#[0-9A-Fa-f]\{6\}" src --include=*.css` 只命中 `tokens.css`（组件不写死色值）。
- [ ] 焦点环：键盘 Tab 遍历时每一步都有可见环（自动化可查 `:focus-visible` 规则存在 + 手工确认）。

手工验收（真实浏览器，归 #137）：

- [ ] 320 / 768 / 1440 三档：无横向滚动；对话列 ≤760 且居中；user 气泡 ≤78%；结果卡单列不溢出。
- [ ] 768×1024 只有一套顶栏；侧栏为抽屉且宽 ≤ 视口 −32。
- [ ] 键盘可完成「切会话 → 发消息 → 打开结果卡 → 关闭浮层」全流程，焦点不丢、不落进隐藏元素。
- [ ] 深色壳上页面缩放到 200%，侧栏文字仍可读（12px 下限 + 7.17 以上对比）。
- [ ] 状态四态各截一张：颜色去掉后仍能靠图标与文字区分。
- [ ] `prefers-reduced-motion: reduce` 下无位移/缩放/循环动画。
- [ ] 登录页不再出现暖米色、宋体标题、金色装饰；沿用同一套颜色与字阶。

## 11. 未决

- 结果页宽度策略（`--results-max` 1120 是否收窄、双列在 320 的降级）交 #136 屏幕级规格。
- 登录/账号/管理员页的具体版式（不是令牌问题）留待实现票；本文件只约束它们必须使用同一套颜色与字阶。
- 视觉实现与真实截图归 #137 与实现票；本文件不含实现代码，也不改动 `main/src` 之外的结构。
- 迁移期间旧别名保留多久（建议：与实现票同一周期，不跨版本）。

## 附录 A：`tokens.css` 目标形态

```css
:root {
  /* 表面 */
  --surface-canvas: #f6f4ef;
  --surface-panel: #fcfbf7;
  --surface-inset: #efede6;
  --surface-media: #e9e6de;
  --surface-glass: rgba(252, 251, 247, 0.88);
  --surface-shell: #17191d;
  --surface-shell-raised: #1e2126;
  --surface-shell-hover: #202329;
  --surface-shell-active: #24272d;
  --scrim: rgba(12, 14, 17, 0.4);

  /* 线 */
  --line-hairline: #dfddd5;
  --line-hover: #c9c8c2;
  --line-control: #8f8c84;
  --line-inverse: #2c2f35;
  --line-inverse-control: #6b7077;

  /* 文字 */
  --text-primary: #1b1d21;
  --text-secondary: #4a4f57;
  --text-muted: #636870;
  --text-inverse-primary: #f4f1e9;
  --text-inverse-secondary: #c7cbd1;
  --text-inverse-muted: #a9adb4;
  --text-inverse-meta: #8c9198;

  /* 动作 / 焦点 / 状态 */
  --action-primary: #1b1d21;
  --action-primary-hover: #2c3037;
  --action-primary-text: #f4f1e9;
  --action-soft: #eceae3;
  --action-link: #1e5a73;
  --focus-ring-light: #1e5a73;
  --focus-ring-dark: #cfe6f2;
  --state-success: #35745B;
  --state-warning: #8A611E;
  --state-danger: #B23B4B;
  --state-info: #4965A8;

  /* 字体 */
  --font-sans: system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  --font-mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  --text-caption: 12px;   --text-caption-line: 18px;
  --text-body: 14px;      --text-body-line: 22px;
  --text-reading: 15px;   --text-reading-line: 25px;
  --text-section: 17px;   --text-section-line: 24px;
  --text-page: 20px;      --text-page-line: 28px;
  --text-display: clamp(26px, 3.2vw, 40px); --text-display-line: 1.15;

  /* 间距 */
  --space-1: 4px;  --space-2: 8px;  --space-3: 12px; --space-4: 16px;
  --space-5: 20px; --space-6: 24px; --space-8: 32px; --space-10: 40px;
  --space-12: 48px; --space-16: 64px;

  /* 形状 / 阴影 / 动效 */
  --radius-sm: 8px;
  --radius-md: 14px;
  --radius-lg: 19px;
  --radius-pill: 999px;
  --shadow-composer: 0 10px 30px rgba(23, 25, 29, 0.06);
  --shadow-overlay: 0 24px 64px rgba(12, 14, 17, 0.24);
  --ease-out: cubic-bezier(0.22, 0.8, 0.3, 1);
  --dur-fast: 120ms;
  --dur-base: 180ms;
  --dur-slow: 240ms;

  /* 图标 / 布局 */
  --icon-sm: 16px;
  --icon-md: 20px;
  --icon-lg: 24px;
  --sidebar-width: 264px;
  --topbar-height: 48px;
  --conversation-max: 760px;
  --results-max: 1120px;
  --shell-pad-x: 16px;
}

@media (min-width: 768px) { :root { --shell-pad-x: 24px; } }
@media (min-width: 1280px) { :root { --shell-pad-x: 32px; } }
@media (max-width: 767px) { :root { --topbar-height: 44px; } }
```
