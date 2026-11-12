# T14 后续变更：默认生图尺寸调整为 3840x2160

- **日期**：2026-10-30（Asia/Shanghai）
- **范围**：T12–T15 验收后的运行配置变更，不替代原 T14 的 1024x1024 历史证据
- **状态**：**通过（真实供应商 4K 文生图/参考图编辑 + 应用内 4K 闭环）**

## 1. 变更

| 项 | 变更前 | 变更后 |
|---|---|---|
| 适配器声明尺寸 | `1024x1024` | `3840x2160` |
| `VISUAL_IMAGE_SIZE` | `1024x1024` | `3840x2160` |
| 尺寸解析 | 仅接受正方形 | 接受合法宽高对（仍拒绝 0、空格、Unicode × 等非规范值） |

代码位置：

- `main/backend/app/adapters/openai_images.py` 的 `_IMAGE_SIZES`
- `main/backend/app/services/generations.py` 的 `parse_image_size`
- `backend/.env`、`.env.example`、`backend/README.md`

## 2. 供应商实测

供应商为 `https://usfast.ailzd.com/v1`，模型 `gpt-image-2-4k`，请求参数 `size=3840x2160`、`n=1`。

| 路径 | HTTP | 耗时 | 返回 |
|---|---|---|---|
| `POST /images/generations`（纯文字） | 200 | 133.49s | 1 张 PNG，3840×2160，4,890,790 bytes |
| `POST /images/edits`（单张参考图） | 200 | 209.62s | 1 张 PNG，3840×2160，5,487,247 bytes |

两条路径均返回单条 `data`，适配器现有的单图合同未被放宽。参考图路径已接近当前 `VISUAL_TASK_TIMEOUT_SECONDS=300` 的上限，但本次实测成功；若供应商持续变慢，应单独评估超时配置。

## 3. 应用内真实闭环

- 项目：`fd11b545-a4e9-473c-8dae-d654622d9d48`
- Generation：`ced1dd32-f59b-48b2-892b-0dbb0d77e978`
- Request：`33c112b0-3452-4d15-8be9-b987236c1e21`
- 确认修订：2；状态：`succeeded`，stage `done`
- 耗时：2026-10-30T09:32:13.841Z → 09:35:44.377Z（约 210.5s）
- 结果 Asset：`f6678951-e149-4ade-b7e0-6209f8a336f3`
- 文件：PNG，3840×2160，7,424,479 bytes
- SHA-256：`de5a47d7623a5b4bead3516d1f24c00d49c97f0af081c6ad094b70a86cfea1e9`
- 结果页无头浏览器核验：出现“实际尺寸：3840 × 2160”、结果图、历史版本与下载链接；无配置读取失败。

## 4. 校验

- `backend/.venv/Scripts/python.exe -m pytest -q`：**170 passed**
- 配置状态 API：VISUAL `ready=true`，`supported_image_sizes=["3840x2160"]`，`selected_image_size="3840x2160"`
- 前端结果页：`/results?project=fd11b545-a4e9-473c-8dae-d654622d9d48` 返回 200，渲染 4K 结果
- 历史 1024x1024 验收证据仍保留在 `t14-real-visual-acceptance.md`，不因本次变更而失效

## 5. 已知影响

已有项目的 brief 保存的是创建时的尺寸快照。将 `VISUAL_IMAGE_SIZE` 改为 `3840x2160` 后，旧的 1024x1024 项目在生成时会得到 `409 IMAGE_SIZE_CHANGED`；需要重新整理并确认需求，让项目取得新尺寸后再生成。该行为保留了已确认需求的快照语义，没有静默改写历史项目。
