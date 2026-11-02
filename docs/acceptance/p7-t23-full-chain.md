# T23 完整创建链与 R3 集成验收（#109）

- Issue: #109 / T23
- 父 Issue: #86
- 分支: ticket/109
- 基线: integration/v3 06cc738
- 代码提交: 86c848a
- 验收日期: 2026-11-02
- 状态: 阶段性通过（模拟适配器 + 临时数据库 + 全量前端回归）；正式发布证据仍由 T25 负责

## 1. 结论与边界

本票在隔离临时数据根中跑通完整 HTTP 创建链：准备最终需求 -> 一次确认受理 -> S07 编制与独立审核 -> 单次图片提交 -> 报告/图片独立批准 -> 交付列表 -> 原子发布 -> 只读成果预览与下载 -> 从原对话发起新版本。测试还覆盖精确重放、硬质量失败不产出可下载成果，以及发布后任务投影的 current 绑定。

本票不替代正式发布。所有模型与图片调用均来自计数 LLMStub/VisualStub，不访问真实供应商；真实设备、软键盘、可信 HTTPS 局域网、生产数据源与正式发布包仍由 T25 及 Release 票证关闭。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t109 |
| 分支 | ticket/109 |
| 基线 | integration/v3 06cc738 |
| 应用代码 | 86c848a |
| 规则版本 | v3.rules.2 |
| 执行快照 | schema 1.1.0（兼容读取 1.0.0），contract 1.0.0 |
| 迁移头 | 0011_recovery_claims |
| Python | backend/.venv Python 3.12.12 |
| 数据库 | 每测试临时 SQLite，alembic 0001 -> 0011 |
| 适配器 | LLMStub / VisualStub，计数可核，零真实网络调用 |
| 前端 | Vitest 5.0.5 + jsdom；vue-tsc；Vite production build |

## 3. 代码变更

### 3.1 修复任务 current 绑定

main/backend/app/workflow/projection.py 的 _binding 原先将 GenerationJob.confirmed_snapshot_digest（已批准最终需求摘要）与当前执行快照摘要比较。两者本就不是同一摘要，导致发布完成后 task_state.binding.current=false，资源状态被误判成 history_only。

修复后 _binding 读取本运行内已批准的 final_brief artifact 摘要作为绑定基准；只有项目修订、运行需求修订和最终需求摘要同时匹配时 current=true。这是 T13 投影契约中的真实缺陷，由完整链验收暴露，不是测试放宽。

### 3.2 新增完整链验收测试

backend/tests/test_v3_p7_t23_full_chain_acceptance.py 使用现有 T65 环境/播种/排空 helper 和 T75 交付语义，通过真实 HTTP 与临时 SQLite 验证：

1. 确认前：阶段 requirements，资源 none，零模型/图片调用，唯一 confirm_requirements 动作要求确认。
2. 一次确认：S07 active，GenerationUsage.used=1，一个 accepted GenerationIntent，三个 GenerationSlot（personal/instance/project）。
3. 精确重放同一 request_id：返回同一 intent，仍为 1 intent / 1 usage / 3 slots，只发生一次 generation_engineer 请求。
4. 排空后台任务：仅一次 VisualStub 提交；角色顺序为 generation_engineer -> output_reviewer -> quality_compliance -> output_reviewer -> delivery_manager -> output_reviewer；prompt_plan 候选预算 used=1 / max=3。
5. 发布前：阶段 S10 needs_input，资源 pending，delivery=None、versions=[]，无 publish 动作；候选资产直接访问返回 409（ASSET_UNAVAILABLE / LEGACY_UNREVIEWED）。
6. approve_delivery -> publish_delivery：S10 completed，交付 published，一个 version 可下载，尺寸与 PNG 一致，brief 保留 product_description；task_state 为 review/S10/terminal=true，资源 published/current，binding current=true。
7. 预览与下载字节一致；刷新返回同一 version，且不增加模型/图片调用。
8. request_new_version：新 S03 active，project_revision +1，新 WorkflowRun sequence_no=2；旧版本 JSON 不变，旧资产仍可下载；旧运行 completed，旧交付仍 published；全程零新增模型/图片调用。
9. 硬质量失败：S09 blocked IMAGE_NOT_APPROVED，资源 failed，versions=[]、delivery 为空，无 publish/download 动作；intent failed，三槽全部以 generation_terminal 释放；角色顺序恰为 generation_engineer -> output_reviewer -> quality_compliance -> output_reviewer；prompt_plan 预算仍为 1/3。

## 4. 命令与结果

    cd D:/Desktop/新建文件夹/.worktrees/t109/backend
    D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q tests/test_v3_p7_t23_full_chain_acceptance.py

结果：2 passed in 3.67s。

    ... -m pytest -q tests/test_v3_p1_t13_safe_projection.py tests/test_t75_delivery_history.py tests/test_t65_generation_cycle.py

结果：24 passed in 21.94s。

    ... -m pytest -q tests/test_v3_p1_t10_execution_snapshot.py tests/test_v3_p1_t11_constraint_layers.py tests/test_v3_p1_t12_legacy_compat.py tests/test_v3_p7_t23_full_chain_acceptance.py

结果：16 passed in 8.53s。

    cd D:/Desktop/新建文件夹/.worktrees/t109
    D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run

结果：46 个文件 / 217 个测试全部通过，42.45s，退出码 0。

    cd D:/Desktop/新建文件夹/.worktrees/t109/main
    D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p tsconfig.json
    npm run build

结果：vue-tsc 退出码 0；Vite 构建退出码 0，1669 modules，1.01s，dist CSS 437.16 kB / JS 1,174.40 kB。

后端全量回归按既有非 v2（62 文件）/ v2（19 文件）两组并行执行，结果记录于本文第 8 节及 D:/.dsh/tmp/spec86/t23-backend-nonv2.log、t23-backend-v2.log。

## 5. 计数与预算

| 场景 | LLM 角色请求 | 图片提交 | GenerationIntent | Usage | Slot | Delivery / 可下载版本 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 正常完整链 | 6 | 1 | 1（accepted -> delivered） | 1 | 3（全部释放 delivery_published） | 1 published / 1 |
| 硬质量失败 | 4 | 1 | 1（failed） | 1 | 3（全部释放 generation_terminal） | 0 / 0 |

- 候选预算：两场景 prompt_plan 均为 used=1、max_allowed=3；确认重放不增加候选。
- 正常链角色：generation_engineer、output_reviewer、quality_compliance、output_reviewer、delivery_manager、output_reviewer。
- 失败链角色：generation_engineer、output_reviewer、quality_compliance、output_reviewer。
- 预览、下载、刷新、切历史和新版本入口均未增加模型或图片调用。
- 以上为 stub 观测值，不等同真实供应商计费证据。

## 6. 验收矩阵

### 6.1 WG3-AC-01 至 WG3-AC-12

| 验收项 | 结论 | 证据归属与本次核对 |
| --- | --- | --- |
| WG3-AC-01 | 通过 | #98 p1-t12：S07 全 N/A/空证据拒绝，完整必审 pass 对照放行；T23 正常链仅在唯一有效确认后进入 S07 |
| WG3-AC-02 | 通过 | #98 p1-t12：必审全 pass 时建议 pass/fail/unknown/N/A 不改变门禁，含 S12 恢复 |
| WG3-AC-03 | 通过 | #98 p1-t12：伪造规则、非法来源、矛盾证据拒绝，advisory 不能吞安全发现 |
| WG3-AC-04 | 通过 | #97 p1-t11：仅一般风格/氛围未知可交付，商品/数量/文案/禁止项未知隔离 |
| WG3-AC-05 | 通过 | #97 p1-t11：用户锁定命名软项升级硬项，卡片摘要变化并重新确认，旧批准不移植 |
| WG3-AC-06 | 通过 | #96 p1-t10、#98/#110：专用 background/palette/text_layout/reference_treatment/preservation_rules 进入确认/编制/质检快照，历史读自己快照 |
| WG3-AC-07 | 通过 | #94 p1-t08：未变快照、可证明未提交的候选修复不新增受理；预算跨恢复不重置，耗尽停止 |
| WG3-AC-08 | 通过 | #94 p1-t08、#93 p1-t07：格式/覆盖修复不要求改需求，实质变化新版本，超时/重启/远端未知不自动重发图片 |
| WG3-AC-09 | 通过 | #95 p1-t09：多句/标点原样复制通过，缺字、否定、数量、数字与跨句拼接不能假通过 |
| WG3-AC-10 | 通过 | #99 p1-t13、#103/#104 fe3-t17/t18：任务卡安全原因、仅服务端动作、建议须批准、成果页无第二输入框 |
| WG3-AC-11 | 通过 | #101/#102 fe3-t15/t16：刷新/切号/历史/返回修改零模型调用；仅当前对话真实发布跳转一次；未放行图不因缓存可见 |
| WG3-AC-12 | 通过 | T23 硬失败测试 + #105 fe3-t19 + #96 p1-t10：报告 pass 但图片未批准时无图/无下载；候选资产 409、版本为空；发布后预览等于下载字节 |

### 6.2 FE3-AC-01 至 FE3-AC-15

| 验收项 | 结论 | 证据归属 |
| --- | --- | --- |
| FE3-AC-01 | 通过 | #108 fe3-t22：自有品牌/助手署名/本地配色，登录设计保留 |
| FE3-AC-02 | 通过 | #108 fe3-t22、#103 fe3-t17：单一对话承载描述/补充/卡片/授权/状态，无右侧常驻栏和四页导航 |
| FE3-AC-03 | 通过 | #103 fe3-t17：已知信息不重复询问，未批准正文不可见，只有当前有效卡片可授权 |
| FE3-AC-04 | 通过 | #103 fe3-t17 + T23 精确重放：一次主操作受理，重复点击/重放不重复生图或计数 |
| FE3-AC-05 | 通过 | #102 fe3-t16：仅当前活动对话从未发布到发布跳转一次，初次加载/刷新/切项目/切号不误跳不泄漏 |
| FE3-AC-06 | 通过 | #101 fe3-t15：路由、浏览器历史、下载、查看历史零模型调用，原任务独立归因 |
| FE3-AC-07 | 通过 | #105 fe3-t19 + T23：大图与下载对应实际可访问版本，未审/撤销/过期/缺失/无效 URL 不展示替代图 |
| FE3-AC-08 | 通过 | #104/#105 fe3-t18/t19：成果 Body 与折叠区无修改输入/反馈表单，返回修改只导航聚焦零写入 |
| FE3-AC-09 | 通过 | #104 fe3-t18：原输入框发送才创建新需求版本并重新审查/核对/授权，历史预览不改变修改基准 |
| FE3-AC-10 | 通过 | #106 fe3-t20、#100 p4-t14：本人标题搜索覆盖未加载分页，重命名持久化且不改需求/确认/意图/模型调用，迟到搜索隔离 |
| FE3-AC-11 | 通过 | #107 fe3-t21：Enter/Shift/Ctrl 或 Cmd+Enter、中文选字、单图上传/粘贴/拖入/替换/移除不串项目 |
| FE3-AC-12 | 通过 | #107 fe3-t21、#101 fe3-t15：同项目草稿保留、离开提醒、退出/切号清理、A->B->A 隔离 |
| FE3-AC-13 | 通过 | #103 fe3-t17、#99 p1-t13：失败/未知/内容未放行分别呈现，仅真实动作可提交，无假百分比/自动重试/伪取消 |
| FE3-AC-14 | 通过（发布残余） | #108 fe3-t22：320/768/1440 无整页横向溢出，真实 Edge 抽屉/焦点/标签可用；实体手机与软键盘待 T25 |
| FE3-AC-15 | 通过 | #103 fe3-t17、#99 p1-t13：任务卡区分需求变化/编制修复/硬失败/缺证据/协议技术故障/远端未知，按钮与后端一致，未审建议不泄露 |

### 6.3 R1 / R2 / R3 交叉

- R1 权限与会话：本票未修改认证/所有权代码；所有 HTTP 测试使用既有认证客户端，T23 不放松任何访问边界，既有 R1 门禁证据仍为所有者。
- R2 受理与资源：T23 直接验证一次受理、确认重放、三槽释放、无自动重发与预算持久化。
- R3 工作流/UI：T23 验证 HTTP 完整链；FE3 各项由 T15-T22 的 jsdom/真实 Edge 证据拥有，本票只做链上交叉核对。
- P1 快照/规则：T10/T11/T12 + T23 共 16 passed；P4 投影 + T75/T65 共 24 passed。

## 7. 资源与调用结论

- 发布前候选资产不可下载；只有 approve_delivery + publish_delivery 后 version 才出现且可下载。
- 预览与下载字节相同；刷新和发起新版本后旧版本字节不变且仍可下载。
- 硬质量失败无 version、无 delivery、无下载动作，三槽全部释放。
- 全流程零真实 provider 网络请求；所有计数均为 stub。

## 8. 全量后端回归

按非 v2 / v2 两组并行运行：

    cd D:/Desktop/新建文件夹/.worktrees/t109/backend
    ... -m pytest -q <62 个非 v2 测试文件>
    ... -m pytest -q <19 个 v2 测试文件>

结果：见本次执行日志 D:/.dsh/tmp/spec86/t23-backend-nonv2.log 与 t23-backend-v2.log；完成后将在提交说明与最终答复中补充精确通过数。任何既有失败按文件与原因记录，不通过修改保护断言掩盖。

## 9. 残余风险

1. 仅使用计数 stub，不代表真实模型质量、真实图片保留策略或供应商计费。
2. 未验证实体手机/桌面、软键盘遮挡、可信 HTTPS 局域网和多设备访问；这些是 T25/Release 的前置证据。
3. 全量后端套件耗时长，本次以非 v2/v2 分组并行执行；若存在与本票无关的既有失败，按原始失败记录，不宣称全绿。
4. 本票不宣称 V3 正式发布完成，只关闭 T23 的模拟完整链与 R3 集成验收。
