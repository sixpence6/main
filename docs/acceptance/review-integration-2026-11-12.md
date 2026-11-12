# Review integration 验收记录（2026-11-12）

状态：**集成进行中，未判定通过。** 本记录用于本轮修复范围与待验证事项交接，不构成发布批准，也不改变历史 release 的结论。

## 基线与证据边界

| 项目 | 记录 |
| --- | --- |
| 日期 | 2026-11-12（Asia/Shanghai） |
| 共享工作树 | `C:/Users/28425/.codex/worktrees/review-integration/新建文件夹` |
| 文档实施起始 HEAD | `8ed65fc3838f925ace643d7c239efcd84ead1386` |
| 最终集成版本 | 待负责人冻结；共享目录存在并行修改，起始 HEAD 不代表最终测试快照 |
| 规范 | `AGENTS.md`、`docs/architecture/hld_v3.md`、`docs/architecture/workflow-gates-v3.md` |
| 历史发布参考 | `docs/operations/release-clean-package.md`、`release-production-entry.md`、`release-gate.md`；`docs/acceptance/release-clean-package.md`、`release-gate.md` |
| 文档实施归属 | 5 号；仅 `README.md`、`main/README.md` 与本文件；未提交 Git |

HLD 的 R1 → R2 → R3 串行门槛继续适用。workflow-gates 的审计快照和旧用例数量不能自动关闭当前问题；顶部现行修订与历史流程须区分读取。本轮文档不宣称 v3 已完成、部署已成功或当前 ZIP 与历史发布物一致。

## 已确认的修复范围

此处的“已确认”只表示下列文档修改已落盘并可由 diff 核对；功能修复与运行结果须按后文补证。

| 范围 | 本轮文档修复 | 证据 |
| --- | --- | --- |
| 源码入口 | 删除源码 `main/` 是独立部署根、仓库文件与旧迁移包一致的描述；明确源码使用根 npm workspace / 锁文件，前端构建写入 `main/dist/` | `README.md`；`main/README.md` 源码 workspace 节 |
| 配置与命令目录 | 区分源码根 `backend/.env` / `backend/data/` 与 ZIP 解压根同名相对路径；源码原生入口明确导入 `main/backend/app/`，后续 ZIP 命令单独限定操作目录 | `main/README.md` 源码与 standalone 节；静态核对 `main/backend/app/config.py`、根 `backend/alembic.ini` |
| 发布物边界 | standalone ZIP 为生成的扁平产物，需要独立锁文件、适配后的 Docker 配置与本次构建 `dist/`；不把 checkout 中的旧 manifest / 摘要当成当前产物证据 | 两份 README；构建与隔离验证仍待执行 |
| 历史追溯 | 保留历史 tags / releases 的地位；旧验收只对应其记录的版本，不能预填本轮通过 | 两份 README；历史 release 文档未修改 |
| 验收交接 | 新建本记录，区分文档实施、实测结果与待验证事项 | 本文件 |

历史 manifest、摘要及构建文件的归档由集成负责人处理；5 号没有执行归档、删除、重建发布附件或修改历史 tags / releases。并行工作者的代码、测试、Docker 与打包工具改动不由本记录代签已修复。

## 本轮实际检查

| 检查 | 工作目录与命令 | 当前结果 | 证据与限制 |
| --- | --- | --- | --- |
| 文档静态核对 | 共享工作树根；对照根 `package.json`、`main/package.json`、Vite 配置、配置加载与迁移路径 | 已核对文档目录约定；不代表运行通过 | 两份 README diff；不以静态阅读替代部署 |
| 仓库规定检查 | 共享工作树根；`npm run check` | 失败，退出码 1 | 依赖图、typecheck 阶段完成；前端 32 文件中 30 通过、2 失败，268 项中 266 通过、2 失败；后端 lint / 定向测试未执行 |
| 最终集成复验 | 冻结后的共享工作树；`npm run check` | 待执行 | 并行修改期间的一次运行不能代表最终集成快照 |

本次前端失败详情（来源：本会话 `npm run check` 终端输出，运行开始时间 08:45:13；未另存日志文件）：

- `tests/check-command.spec.ts:11`：`prints the check plan without running the stages` 超过 5000ms 超时。尚未判定是否与并行运行负载有关。
- `tests/t125-task-status.spec.ts:266`：任务进行中输入补充说明后，发送按钮未满足 `toBeDisabled()`。本次仅记录观察，不推测根因。

这两项不在 5 号的文件所有权内，未修改测试或业务代码。后端 lint / 定向测试未执行，不能按命令预期覆盖范围记为通过。`git diff --check -- README.md main/README.md` 退出 0；新验收记录另做空白与本地链接核对。最终集成须修复或解释失败并重新运行。

## 待验证事项

| 项目 | 所需证据 | 当前状态 |
| --- | --- | --- |
| 业务代码修复范围 | 各实施者提供实际 diff、复现输入、定向回归、剩余未知；集成负责人确认最终纳入范围 | 待汇总；本文不推测具体缺陷已关闭 |
| 源码 workspace | 根锁文件干净安装、唯一前端依赖图、类型、前端测试、后端 lint 与定向测试；按失败范围补验 | 当前检查失败，最终快照待复验 |
| 源码 Docker | 基础 / HTTPS Compose 配置渲染；正确构建上下文、Dockerfile COPY 路径、模板参数、环境文件和数据挂载；镜像构建、启动与健康检查 | 待验证 |
| 源码原生入口 | 隔离空数据根上的依赖安装、迁移到实际 head、生产入口自检、静态页 / API 与管理员初始化 | 待验证；不得使用真实数据根作验收夹具 |
| Standalone 生成 | 最终白名单、锁文件与 Docker 配置转换；`tools/release_package.py --check --require-dist --json`；由当前源码重新生成包 | 待验证 |
| Standalone 独立性 | ZIP 在仓库外隔离解压；独立 `npm ci` / 构建 / 类型检查、Python 安装迁移启动；Docker 与 HTTPS 入口不得依赖仓库父目录 | 待验证 |
| 产物摘要与隔离 | 最终 `RELEASE-MANIFEST.json`、源码与构建归属、逐文件 SHA256、外部 ZIP 摘要；秘密 / 数据 / 测试 / 缓存排除检查 | 待验证；归档旧文件不等于生成新证据 |
| 工作流与恢复 | 对本轮受影响范围提供 HTTP / SQLite / adapter 证据；每次图片执行须用户确认提示词，读取 / 重启 / 恢复不得补发调用 | 待实施者与集成复验；不能用旧 workflow-gates 结果代签 |
| 正式开放所需补证 | 实际供应商、可信 TLS、第二设备、备份恢复及故障关闭按受影响范围复验，不能以桩或静态页代替 | 本轮尚未提供证据 |
| 最终双轴审查 | 在最终 diff 上分别执行 Standards 与 Spec 审查，记录发现与处理结论 | 待通知后独立审查；本次文档实施不是最终 Standards 审查 |

尚无本轮真实模型 / 图片调用、用量计数、确认快照、图片可见性或受控恢复结果可写入。若后续涉及 workflow-gates 场景，按其第 8 节记录规则与代码版本、隔离环境、输入、候选预算、调用次数、计数及资源结果，逐项判定，不以测试总数关闭整条交付链。

本轮保留开放状态，待最终代码快照、命令输出与发布物证据齐全后由集成负责人更新结论。
