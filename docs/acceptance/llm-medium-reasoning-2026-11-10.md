# 全部文字与参考图识读请求使用 medium 推理强度

日期：2026-11-10（Asia/Shanghai）。用户要求：将思考程度统一开启到 medium，保留上一轮 3600 秒上限。

## 配置与行为

- 当前本地配置：LLM_THINKING_MODE=enabled、LLM_REASONING_EFFORT=medium、LLM_VISION_REASONING_EFFORT=medium、LLM_TIMEOUT_SECONDS=3600。
- 需求助手、视觉策划和提示词编制等文字调用发送 thinking.type=enabled 与 reasoning_effort=medium；参考图识读模型单独发送 reasoning_effort=medium，避免把文字模型专用 thinking 参数强加给独立视觉模型。
- 现代角色接口与旧整理入口复用同一选项构造；生图接口不增加它不支持的推理参数。生成前展示可编辑提示词、用户确认后执行的规则不变。
- 新增两项配置支持 auto／low／medium／high，auto 默认不发送扩展参数；示例配置同步说明。当前两处本地 .env 均显式设为 medium。
- 实际生效的推理强度进入模型指纹；原 auto、未指定 effort 的指纹保持兼容。关闭文字思考时不再发送与之冲突的非空 effort。

## 网关验证与边界

对当前文字模型和参考图识读模型分别进行一次受控验证，仅要求返回简短 JSON；识读使用内存生成的 16×16 空白 PNG，没有提交图片生成。

- 文字模型：HTTP 200，6.75 秒，finish_reason=stop，存在正文，返回 reasoning_tokens=19。
- 参考图识读模型：HTTP 200，14.08 秒，finish_reason=stop，存在正文，返回 reasoning_tokens=16。

两次均显式传递 reasoning_effort=medium；文字模型另传 thinking.type=enabled。结果证明当前网关接受参数并返回结果，不能证明代理内部如何映射厂商原生推理档位，也不能保证所有未来调用延迟。

## 回归

新增 6 项测试覆盖文字与视觉的角色调用、旧整理调用、默认兼容和指纹区分；连同旧适配器与思考开关回归共 37 项已通过。没有增加自动重试。

## 最终结果

- 根目录 `npm run check` 全部通过：依赖图、类型检查、前端 235 项、后端 Ruff、后端 223 项（229.73 秒）。日志见 [check.log](evidence/llm-medium-reasoning-20261110/check.log)。
- 已构建并更新本地后台；运行容器核实 enabled／medium／medium／3600，实际适配器的文字和视觉请求均传递 medium。API 代理健康检查通过。
- 本轮仅执行两次受控模型验证，没有提交图片生成，没有代用户确认提示词。
