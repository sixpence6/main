# Release T28 可信同源 HTTPS 与受控局域网入口验收记录

- Issue: #80
- 父 Issue: #52
- 分支: ticket/80
- 基线: integration/v3 ebe680b
- 代码提交: 88317eb48ec59a0a11353745df09e60b9b228f6d
- 验收日期: 2026-11-01
- 状态: 通过（Release T28 阶段条件；正式开放由 #85 Release-GATE 决定）

## 1. 结论与边界

本票把正式入口从“回环 HTTP 开发”扩展为“显式受控的 HTTPS 局域网候选入口”：Host、Origin、APP_PUBLIC_URL、可信代理和绑定地址都必须显式配置，HTTP 只保留回环开发例外，HTTPS 入口通过受信任证书和 Nginx TLS 终止提供，后端仍只监听回环地址。用真实临时 SQLite、完整 App 和真实 Docker 容器完成了一条 HTTPS 业务链路，并用 headless Edge 验证了浏览器的 Cookie、CSRF、no-store、CSP 和同源行为。

1. ALLOWED_HOSTS 只接受精确主机名、IP 或带方括号的 IPv6，拒绝通配符、协议、端口和凭据；ALLOWED_ORIGINS 只接受精确的 http/https 源，拒绝通配符、路径、查询、片段和凭据。APP_PUBLIC_URL 的 host 必须在 ALLOWED_HOSTS 中或是显式 HOST。
2. HTTP 源只允许 127.0.0.1、localhost、::1，并且必须同时显式设置 ALLOW_LOOPBACK_HTTP=true 和 AUTH_COOKIE_SECURE=false；HTTPS 源不允许这两个开发例外。局域网叠加层把这两个值固定为 false/true。
3. TRUSTED_PROXY_CIDRS 默认空；只有来自配置 CIDR 的直连对端才读取 X-Real-IP，其他来源的伪造头被忽略，且从不信任 X-Forwarded-For。后端容器在本机只发布 127.0.0.1:8000，局域网直接连接后端超时。
4. Nginx TLS 入口在 80 端口只提供 /api/health 和 308 跳转到 HTTPS；443 使用 TLSv1.2/1.3、证书路径和私钥由部署环境提供，代理 /api 时传递 Host 与可信来源头。
5. 登录响应使用 __Host-session 与 __Host-csrf：Secure、HttpOnly（session）、SameSite=Strict、Path=/、无 Domain；API 响应 Cache-Control: no-store；错误 Origin、缺失 CSRF、错误 Host 分别返回 403 ORIGIN_INVALID、CSRF_INVALID、HOST_INVALID。
6. API 与静态响应统一携带 CSP、nosniff、Referrer-Policy、X-Frame-Options 与 Permissions-Policy；Nginx 在 /api 先隐藏后端同名头再添加统一头，避免重复。HTTPS 静态响应另带 HSTS。
7. 使用受控候选证书（本地 CA + 服务器证书，SAN 覆盖 localhost、127.0.0.1 与局域网地址 10.13.51.8）完成真实容器入口验证；浏览器不绕过证书警告。验收后已从本机 CurrentUser Root 删除临时 CA 信任。
8. 通过 HTTPS 完成管理员登录、创建账号、设置密码、业务用户登录、项目创建、S06 确认、S07–S10 后台链路、人工批准与发布、下载当前交付；S10 为 completed，下载 29,373 字节 image/png，用量为日限 20／已用 1／剩余 19。
9. fake provider 记录 6 次 chat 与 1 次 image 调用，角色顺序为 generation_engineer、output_reviewer、quality_compliance、output_reviewer、delivery_manager、output_reviewer；没有调用真实供应商或真实数据。
10. 本票提供的是受控候选环境，不提前正式开放；没有验证第二台实体设备、真实公网 DNS、企业 CA、证书自动轮换、多平台镜像或真实供应商。正式验收仍由 #85 收口，本票不关闭 #51。

## 2. 版本与环境

| 项目 | 值 |
| --- | --- |
| 仓库根 | D:/Desktop/新建文件夹 |
| 工作树 | D:/Desktop/新建文件夹/.worktrees/t80 |
| 分支 | ticket/80 |
| 基线 | integration/v3 ebe680b |
| 操作系统 | Windows；Python 3.12.13；Node v26.12.1；npm 11.20.0 |
| 前端 | Vite 8.3.5；Vitest 5.0.5 |
| 容器 | Docker 29.8.2；Compose 5.5.1 |
| 浏览器 | Microsoft Edge headless（CDP） |
| 数据库 | 临时 SQLite；alembic 0010；26 张表，integrity ok |
| TLS | 本地受控 CA；服务器证书 SAN localhost、127.0.0.1、10.13.51.8 |
| 模型 | 本地 fake OpenAI-compatible provider；无真实供应商调用 |
| 数据根 | 仅 .scratch/t80-tls 临时目录；未触碰 backend/data、main/backend/data |

## 3. 实现摘要

### 3.1 配置与边界

- main/backend/app/config.py 增加 trusted_proxy_cidrs，并校验 allowed_hosts、allowed_origins、app_public_url 的精确值；HTTP 只允许回环并且必须显式打开开发例外。
- 新增 main/backend/app/request_source.py：默认使用直连 peer，只有在 peer 属于 TRUSTED_PROXY_CIDRS 时才读取 X-Real-IP；非法值回退 peer，绝不读取 X-Forwarded-For。
- 所有认证、管理、资源和工作流路由的客户端地址来源统一改用 client_ip，避免代理场景下限流和审计取错来源。

### 3.2 安全响应头与 TLS 入口

- 新增 main/backend/app/security_headers.py，统一 CSP 与安全头；认证中间件对 JSON 错误和普通响应都应用；静态文件路由复用同一组头。
- main/docker/security-headers.conf 与 main/docker/hsts.conf 供 Nginx 使用；main/docker/nginx-tls.conf.template 提供 80/443 双监听、TLS 证书挂载、308 跳转、代理头和 HSTS。
- main/docker/nginx.conf.template 的 HTTP 开发入口也加上安全头；main/docker/Dockerfile 通过 NGINX_TEMPLATE 构建参数选择 HTTP 或 TLS 模板，并把安全头文件放入镜像。
- main/docker-compose.lan.yml 显式要求 PUBLIC_ORIGIN、PUBLIC_HOSTNAME、PUBLIC_BIND_ADDRESS，只发布 LAN 绑定的 HTTPS 端口，挂载只读证书目录，并把后端固定在回环端口。

### 3.3 部署文档与发布清单

- main/README.md 增加证书生成、客户端信任、精确 Host/Origin、Compose 叠加、防火墙、证书更新与回滚说明。
- main/backend/.env.example 增加 HTTPS/LAN 示例和“不要把 127.0.0.1 加入可信代理”的警告。
- release/package-manifest.json 增加 docker-compose.lan.yml 及 TLS/安全头模板的必需项，保证发布包不丢正式入口文件。
- 新增 backend/tests/test_t80_https_lan.py，覆盖通配符与公网 HTTP 拒绝、回环/HTTPS 边界、可信代理头、CSP 和模板静态约束。

## 4. 命令与结果

### 4.1 受控证书

在 D:/Desktop/新建文件夹/.scratch/t80-tls/certs 生成并安装临时 CA：

~~~bash
openssl req -x509 -newkey rsa:3072 -sha256 -days 3 -nodes \
  -keyout ca.key -out ca.crt -subj "/CN=T80 Local Smoke CA"
openssl req -newkey rsa:3072 -sha256 -nodes \
  -keyout server.key -out server.csr -subj "/CN=10.13.51.8" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:10.13.51.8"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 3 -sha256 \
  -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:10.13.51.8")
certutil.exe -user -addstore -f Root ca.crt
~~~

结果：服务器证书 CN=10.13.51.8，SAN 含 DNS:localhost、IP:127.0.0.1、IP:10.13.51.8，有效期至 2026-11-04 04:53:05 GMT。验收后执行 certutil -user -delstore Root <CA thumbprint>，确认 CurrentUser Root 不再包含 T80 Local Smoke CA。

### 4.2 构建真实镜像

~~~powershell
docker build -f main/docker/backend.Dockerfile -t t80-backend:local main
docker build -f main/docker/Dockerfile \
  --build-arg NGINX_TEMPLATE=docker/nginx-tls.conf.template \
  -t t80-frontend-tls:local main
~~~

结果：t80-backend:local ID a67771727911；t80-frontend-tls:local 最终 ID 8bbc3efff033。既有 ecommerce-image-workspace:local:8090 与 ecommerce-image-backend:local:8000 未重启、未改动。

### 4.3 入口冒烟

~~~powershell
D:/nodejs/node.exe D:/Desktop/新建文件夹/.scratch/t80-tls/run_tls.mjs
~~~

结果：退出码 0，result.json 的 ok=true。本次端口为 fake 2613、backend 2614、HTTPS 2615、HTTP 2616；基址 https://10.13.51.8:2615。

### 4.4 HTTPS 探测结果

probe.json 记录：

| 检查 | 结果 |
| --- | --- |
| /api/health | 200 {"status":"ok"} |
| CSP / HSTS / X-Frame / Referrer / Permissions / nosniff | 均为单值；HSTS max-age=31536000; includeSubDomains |
| 登录 Set-Cookie | __Host-session：HttpOnly、Max-Age=43200、Path=/、SameSite=strict、Secure；__Host-csrf 同样 Secure/Strict |
| session Cookie 属性 | secure=true、HttpOnly=true、SameSite=Strict、__Host- 且无 Domain |
| API 缓存 | no-store |
| 伪造 Origin | 403 ORIGIN_INVALID |
| 缺失 CSRF | 403 CSRF_INVALID |
| 代理层错误 Host | 403 HOST_INVALID |
| HTTP 登录 | 308 https://10.13.51.8:2615/login |
| 直连后端 | 正常 Host 200；错误 Host 403 |
| 局域网直连后端 | 超时，不可达 |

### 4.5 HTTPS 业务冒烟

同一 run_tls.mjs 调用 prod_smoke.py：

- 项目：3c652003-14f9-414f-8244-6bf3ac3bdbfe
- 请求：84a59e08-4446-4080-ae4f-863867c0634d
- 阶段／状态：S10 / completed
- 交付资源：f86735af-466d-488f-bcb1-4e7679bb262b，文件名 delivery-f86735af-r1.png
- 下载：29,373 字节，image/png
- 用量：日限 20、已用 1、剩余 19
- fake provider：chat 6、images 1，角色顺序与 R2 预期完全一致

### 4.6 真实浏览器

edge_https.mjs 使用 CDP 启动 Edge，以 prod-user@example.com 登录 https://10.13.51.8:2615/：

- 最终路径 /，标题“电商生图智能体”，document 协议 https；
- /api/auth/session 200、Cache-Control: no-store、account=true；
- document.cookie 只有 __Host-csrf，session 为 HttpOnly 不可见；
- 无 CSRF 的 POST /api/auth/logout 返回 403 CSRF_INVALID；
- localStorage 0、sessionStorage 0、externalRequests []；
- 登录页与文档响应包含 CSP、HSTS、Referrer-Policy no-referrer、X-Frame-Options DENY、nosniff；
- 页面显示模型数据流提示，未出现证书警告绕过或混合内容。

### 4.7 测试与回归

| 项目 | 结果 |
| --- | --- |
| tests/test_t80_https_lan.py | 5 个通过（1.29s） |
| tests/test_t56_account_links.py::test_app_public_url_validation | 1 个通过（更新为显式 allowed_hosts） |
| t78/t79/t80 定向 | 13 个通过（8.84s） |
| 前端全量 | 38 文件／173 测试通过（25.86s） |
| typecheck | 退出 0 |
| 生产构建 | Vite 8.3.5，1664 模块，769ms，CSS 430.37 kB、JS 1,159.11 kB，仅 chunk 警告 |
| 后端非 v2 | 367 个通过，2 个既有 cookie 弃用警告（422.84s） |
| 后端 v2 | 235 个通过（319.69s） |
| 后端合计 | 602 个通过；无失败、跳过或未知 |

## 5. 验收条件对照

| 验收项 | 本票证据 | 结论 |
| --- | --- | --- |
| V3-AC-01 | 前端只发布 LAN 绑定的 HTTPS 端口，后端只绑回环；probe 中局域网直连后端超时；没有公网映射 | 阶段条件通过 |
| V3-AC-02 | 显式 PUBLIC_HOSTNAME／PUBLIC_ORIGIN／PUBLIC_BIND_ADDRESS，无通配；HTTP 只保留回环开发例外 | 阶段条件通过 |
| V3-AC-07 | 以实际受控证书和完整容器入口验证 HTTPS；浏览器无证书警告绕过；验收后移除临时 CA 信任 | 阶段条件通过 |
| V3-AC-10 | 登录 Cookie Secure/HttpOnly/Strict、CSRF 403、Origin 403、Host 403、API no-store | 阶段条件通过 |
| V3-AC-13 | 受控代理 CIDR 下的 X-Real-IP 才被采纳；伪造头回退直连；后端无 LAN 监听 | 阶段条件通过 |
| V3-AC-20 | 发布清单纳入 LAN Compose 与 TLS/安全头模板；镜像由当前源码构建 | 阶段条件通过 |
| V3-AC-21 | 安全头、TLS 模板和配置边界有自动化测试；真实容器冒烟可重复 | 阶段条件通过 |
| V3-AC-38 | Edge 真实浏览器在 HTTPS 下完成登录与工作台呈现；320/768/1440 既有多视口证据沿用 R3，本票未引入新布局 | 阶段条件通过（沿用 R3） |
| V3-AC-40 | 提交代码、环境、命令、结果、临时 SQLite 与 fake provider 边界均已记录；真实数据根未触碰 | 阶段条件通过 |

## 6. 证据位置

- TLS 冒烟脚本：D:/Desktop/新建文件夹/.scratch/t80-tls/run_tls.mjs
- HTTPS 探测：D:/Desktop/新建文件夹/.scratch/t80-tls/probe_https.py、probe.json
- 业务冒烟：D:/Desktop/新建文件夹/.scratch/t80-tls/prod_smoke.py、smoke.json
- 浏览器脚本与结果：D:/Desktop/新建文件夹/.scratch/t80-tls/edge_https.mjs、edge-https.json
- 总结果：D:/Desktop/新建文件夹/.scratch/t80-tls/result.json（ok=true）
- 容器日志：D:/Desktop/新建文件夹/.scratch/t80-tls/backend.log、frontend.log、fake.log
- 前端日志：D:/Desktop/新建文件夹/.scratch/t80-evidence/frontend-full.log、typecheck.log、build.log
- 后端日志：D:/Desktop/新建文件夹/.scratch/t80-evidence/backend-nonv2.log、backend-v2.log
- 证书目录：D:/Desktop/新建文件夹/.scratch/t80-tls/certs（仅本地证据，未进入发布包）

## 7. 残余风险与未覆盖边界

1. 受控候选使用本地 CA 和同一台 Windows 主机的 Edge；没有第二台实体局域网设备、真实企业 CA 或公网 DNS。最终外部设备验收由 #85 完成。
2. 证书有效期为 3 天且只用于本票；没有实现自动续期、OCSP/CRL 或证书轮换自动化。
3. 业务与浏览器冒烟使用 fake provider、临时 SQLite 和固定尺寸 3840x2160；没有调用真实供应商。
4. 容器镜像只验证本机 Docker 单平台；没有多平台、镜像签名、SBOM 或供应链证明。
5. PUBLIC_BIND_ADDRESS 的防火墙规则由部署者执行；本票验证了绑定和访问路径，未修改主机防火墙。
6. 发布包已包含 LAN 模板和 Compose 叠加文件，但未在 #80 重新生成最终 ZIP；#79 的发布包重建应在 Release-GATE 统一执行。
7. 本票不关闭 #51；正式开放、全量 AC 证据和发布仍需 #81–#85。
