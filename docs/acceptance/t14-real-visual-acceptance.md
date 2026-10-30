# T14 真实 VISUAL 生图验收记录

- **Issue**：GitHub #28《接入真实 VISUAL 纯文字与参考图生图》
- **父规格**：GitHub #14；SRS AC-01（VISUAL）/AC-02/03/07；BE-AC-05/08/09/12/13
- **记录日期**：2026-10-30（Asia/Shanghai）
- **状态**：**通过（真实同步生图，含供应商文档外部限制）**
- **证据性质**：真实 VISUAL 调用见 §2；受控适配器/服务测试见 §4，两者不互相替代。
- **后续变更**：2026-10-30 默认尺寸已调整为 `3840x2160`，见 [t14-4k-size-amendment.md](t14-4k-size-amendment.md)；本记录保留原 `1024x1024` 历史证据。

## 1. 环境与能力

| 项 | 值 |
|---|---|
| 适配器 | `openai-images`（显式注册） |
| Base URL | `https://usfast.ailzd.com/v1` |
| 模型 | `gpt-image-2` |
| 鉴权 | Bearer，仅服务端持有 |
| 同步/异步 | 同步；无 task id、无 poll |
| 尺寸 | 固定 `1024x1024` |
| 参考图 MIME | `image/png`、`image/jpeg` |
| 供应商参考图上限 | 未知（`null`） |
| 服务端有效参考上限 | 10,000,000 字节 |
| 超时 | 300 秒 |
| 输出 | 优先 `b64_json`；兼容 CDN URL fallback；实际为 PNG |

供应商没有公开官方 API 文档（详见 T12 决策记录）。本记录只按实测协议声明能力，不冒充官方文档确认。配置状态 API 见 `docs/acceptance/evidence/config-status.json`。

## 2. 真实生成证据

### 2.1 纯文字生图

| 版本 | Generation ID | Request ID | 结果 |
|---|---|---|---|
| v1 | `8e21bb4f-28f5-4b06-a075-6d78278dbeb7` | `b38d9dd5-4572-42d3-8c82-57e13d7e8ea4` | succeeded；耗时 22.2s；`can_retrieve_result=false` |
| v2（反馈后） | `83ed1da1-688d-4e86-a128-3820c5ac4034` | `fdd6c679-4809-4cd2-9450-6c886287a3e0` | succeeded；耗时 41.5s；version 2 |

v1 只提交一张图，无自动重试 submit。真实结果文件：

- `docs/acceptance/evidence/generation-v1.png`：PNG，1024×1024，1,084,652 bytes，SHA-256 `265f9c65af2f19d1d29bfd6734932c29fc9a0367f51f77a15315218d23bedf7e`
- `docs/acceptance/evidence/generation-v2.png`：PNG，1024×1024，1,074,333 bytes，SHA-256 `3a59636a8ce6e97924e2c64256733c0ab75ea02feba1fba84ec12f97507e750f`

下载响应用 `Content-Disposition: attachment` 和真实 `.png` 文件名；同一 asset 的 inline GET 不设置 attachment，浏览器可内联预览。两文件均可用 Pillow 重新解码验证尺寸，不依赖前端。

### 2.2 参考图生图

- 参考图：`docs/acceptance/evidence/reference-vision-red.png`，64×64 PNG，184 bytes。
- 上传后项目 revision 8，`reference_asset_id=6fda10c1-e072-4203-ba61-9a28c1d64034`，`availability=available`，存储字节与上传字节 hash 一致。
- 参考图消息整理为 review revision 10，卡片保留 `reference_asset_id`；确认后提交 v3。
- v3：`d596ae56-871f-4479-9f9f-2515d2a98491`，request_id `9577ebff-3bef-4d06-a4e2-23bb5fcc5496`，succeeded，22.3s，`reference_asset_id=6fda...`。
- 输出 `docs/acceptance/evidence/generation-v3.png`：PNG，1024×1024，1,086,956 bytes，SHA-256 `44f209eec5ffa9c7cd099f2a752a246b2619ca7985b71b883e1d093259e6736c`。
- 生成中项目详情只读刷新不产生新消息/新生成；删除参考图后 revision 11，旧参考 asset 文件仍可读取。

**原始字节进入 VISUAL 的证据**：适配器对 `/images/edits` 发送 multipart，`image` 字段使用服务端保存的参考文件字节、实际 MIME 和稳定文件名；受控测试 `test_reference_image_uses_edits_endpoint` 断言 `name="image"`、`name="response_format"` 和原始 bytes `b"reference-bytes"`。没有把 asset ID、描述或前端对象 URL 当作参考图发送。

### 2.3 版本、下载与原结果恢复

- v1、v2、v3 版本号递增且不复用；v2 与 v1 hash 不同，v3 与 v2 hash 不同。
- 重启后 v1/v2/v3 下载 hash 与重启前完全相同（见 T15）。
- `POST /api/generations/{id}/retrieve-result` 对已 succeeded 记录返回原 generation/原 asset，不新增版本、不重新 submit；对失败且 locator 可用、配置 fingerprint 匹配且项目 idle 的记录才允许真实重取。受控测试 `test_failed_download_can_retrieve_same_version_and_downloads_real_file` 覆盖“失败后重取成功且不增版本”。
- 成功记录 `can_retrieve_result=false`，因为结果已交付；不提供虚假“再次生成”按钮语义。

## 3. 错误、来源与安全边界

| 场景 | 行为 |
|---|---|
| 401/403 | `VISUAL_AUTH_FAILED` |
| 429 | `VISUAL_RATE_LIMITED` |
| 其他 4xx | `VISUAL_REQUEST_REJECTED`，错误消息脱敏、去 URL |
| 5xx | `VISUAL_UPSTREAM_ERROR`，结果不确定 |
| 超时 | `VISUAL_TIMEOUT`，不自动重发 |
| 网络错误 | `VISUAL_NETWORK_ERROR`，结果不确定 |
| 多图/无图 | `VISUAL_MULTIPLE_IMAGES` / `VISUAL_NO_IMAGE`，不伪造成功 |
| base64 解码失败/尺寸或落盘失败 | `RESULT_FETCH_FAILED` / failed，不写 Asset |
| URL fallback | 仅 HTTPS；base host 或 `.dimenoss.com`；最多 5 重定向；不带模型 Authorization；私有 locator 不暴露 |
| 过期/无 locator | `can_retrieve_result=false` 并给安全原因，不重新 submit |
| 配置变更 | fingerprint 不匹配则不可重取，避免用新模型重跑旧任务 |

实际 v1/v2/v3 都使用 b64 响应成功；没有把 CDN URL 当作唯一交付路径。供应商 URL 有效期未知，文档不承诺长期可重取。

## 4. 受控适配器测试

`backend/tests/test_openai_images_adapter.py` 当前 12 passed，覆盖：

- `test_text_to_image_requests_and_returns_base64`：JSON 请求、单图、base64 解码；
- `test_reference_image_uses_edits_endpoint`：multipart 原始字节、字段名、response_format；
- `test_http_rejection_is_classified_without_leaking_credentials`、5xx、超时：安全分类；
- `test_invalid_json_is_uncertain`、`test_multiple_results_are_rejected_explicitly`：不伪造成功；
- `test_url_fallback_downloads_without_model_auth_and_follows_approved_redirect`：allowlist、重定向、无模型鉴权；
- `test_download_from_unapproved_host_is_rejected_before_request`：未批准主机不发出请求；
- 注册表与配置就绪测试。

服务层测试 `test_t08_async.py`、`test_t11_retrieve.py` 覆盖同步成功交付、失败/unknown 区分、结果校验、重取不 submit；`test_synchronous_success_outcome_is_delivered` 是本次修复的真实回归。

## 5. 结论

**通过**：真实纯文字生成、真实参考图上传与生图、1024×1024 PNG、附件下载/内联预览、版本独立、重启后可读、原结果重取语义、同步协议能力声明、参考字节进入官方 generation 请求的受控证据。

**限制**：供应商无公开官方文档；参考图供应商上限、URL 有效期、速率限制未知；图像质量与浏览器交互不在本记录（浏览器归 #13）。不把“接口返回成功”写成“视觉质量通过”。

## 6. 证据索引

- `docs/acceptance/evidence/generation-v1.png`、`generation-v2.png`、`generation-v3.png`
- `docs/acceptance/evidence/reference-vision-red.png`
- `docs/acceptance/evidence/t15-summary.json`（checks 17–42）
- `docs/acceptance/evidence/config-status.json`
- `main/backend/app/adapters/openai_images.py`
- `backend/tests/test_openai_images_adapter.py`、`test_t08_async.py`、`test_t11_retrieve.py`
