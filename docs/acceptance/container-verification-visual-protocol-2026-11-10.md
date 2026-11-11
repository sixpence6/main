# 运行容器核对与视觉策划协议修订

日期：2026-11-10（Asia/Shanghai）。

## 容器与请求时间核对

用户反馈“还是一分钟超时，是否没有更新容器”。读取实际运行的 ecommerce-image-backend，启动时间为 19:38:29，实际配置为 timeout=3600、thinking=enabled、文字／参考图 effort=medium。

现场最新记录：19:41:26—19:41:40 需求助手成功；19:41:40—19:41:58 视觉策划失败，错误为 LLM_RESPONSE_INVALID／语言模型返回的内容不是有效 JSON。最后一条 LLM_TIMEOUT 为 19:17:53—19:18:53，早于上述容器更新。因此最新故障不能归因为仍使用 60 秒上限。

## 格式调查与修订

原 JSON 解码失败没有保存返回正文，不能断言已还原其具体语法错误。使用同一视觉策划请求受控复测，观察到另一种可复现的契约不匹配：模型输出 planned_waiting_confirmation／plan_only 等应用状态及 visual_plan、generation_prompt，而内部角色应返回 role/status/data/issues/questions，以及 data.creative 和 data.visual。

增加禁止项和字段说明的试验未通过，已撤回，没有部署。原生 json_schema strict 请求返回 HTTP 200，但字段仍不匹配，不能将这个网关的参数接受误当成严格执行保证，未启用该方案。

在原视觉策划系统提示词末尾添加一个完整、无业务事实的正向字段示例，明确 ready 只表示角色产物完成，用户生成确认由程序随后处理。同请求复测 HTTP 200，27.04 秒，finish_reason=stop，正文 1191 字符，JSON 解码及原有 visual_planner 契约均通过。

本次补丁仅添加该已验证的字段示例，不修改解析器，不补造缺失业务字段，不放宽校验，不执行图片生成或代替用户最终确认。一次通过不能证明所有未来模型输出和设计质量都通过产品验收。

## 部署核验

已重新构建并更新后台，容器启动时间 20:07:52（Asia/Shanghai），镜像标识 ab506ffbb6a7c76716c076c78988ac41531b40b64b0a30136e9b96652281c936。运行容器中的 prompts.py 与工作区规范化文本摘要一致，字段示例已安装。

通过实际前端代理读取 /api/config/status，运行中的应用返回 TEXT=3600、VISION=3600、IMAGE_GEN=300；这进一步确认不是只检查了磁盘 .env 或另起进程的配置。思考保持 enabled，文字及参考图 effort 均为 medium。API 代理健康检查通过。

根目录 npm run check 全部通过：前端 235 项、后端 223 项（244.53 秒）、类型检查、依赖图与后端 Ruff 均通过。日志见 [check.log](evidence/container-verification-visual-protocol-20261110/check.log)。没有提交图片请求，未自动重试当前用户工作流，待用户手动继续原流程。
