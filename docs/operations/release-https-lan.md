# Release T28 受控 HTTPS 局域网入口运行手册

本文说明如何在受控局域网中用受信任证书发布本应用，以及如何验证 Host、Origin、Cookie、CSRF、CSP、代理来源和后端隔离。它只覆盖 #80 的 HTTPS／局域网候选入口；正式开放、备份、恢复、保留期和真实数据认领属于 #81–#85。

## 1. 目标与边界

- 浏览器必须通过证书覆盖的 HTTPS 地址访问，例如 https://10.13.51.8:8443。
- 前端 Nginx 只发布独立的 HTTPS 端口；后端只绑定 127.0.0.1:8000，不直接暴露到局域网。
- Host、Origin、APP_PUBLIC_URL、可信代理 CIDR 和绑定地址都必须显式配置，禁止通配符。
- HTTP 只保留回环开发例外；局域网和正式环境不能设置 ALLOW_LOOPBACK_HTTP=true 或 AUTH_COOKIE_SECURE=false。
- 真实数据目录、真实 .env、备份、诊断数据和证书私钥不进入 Git、发布包或镜像。

## 2. 前置条件

- 目标机器有固定局域网地址或可解析的域名，并与证书 SAN 一致。
- 维护者控制证书私钥，目录权限只允许服务账号读取。
- 客户端信任签发该证书的 CA 或自签证书；不允许通过浏览器忽略证书警告。
- Docker Engine 29.x 与 Compose v2；或本地 Python 3.12 生产入口。
- 明确的 PUBLIC_ORIGIN（含端口）、PUBLIC_HOSTNAME、PUBLIC_BIND_ADDRESS 和防火墙开放端口。

## 3. 证书准备与信任

在受控目录生成证书：

~~~bash
mkdir -p certs
openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes \
  -keyout certs/ca.key -out certs/ca.crt -subj "/CN=Internal Dev CA"
openssl req -newkey rsa:3072 -sha256 -nodes \
  -keyout certs/server.key -out certs/server.csr -subj "/CN=10.13.51.8" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:10.13.51.8"
openssl x509 -req -in certs/server.csr -CA certs/ca.crt -CAkey certs/ca.key \
  -CAcreateserial -out certs/server.crt -days 825 -sha256 \
  -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:10.13.51.8")
chmod 600 certs/ca.key certs/server.key
~~~

把 certs/ca.crt 安装到每台允许访问的客户端信任存储。Windows 客户端可用证书管理器导入“受信任的根证书颁发机构”；不要安装来源不明的证书。服务器证书的 CN/SAN 必须包含浏览器地址栏使用的主机名或 IP。

更新证书时：

1. 先签发新证书并验证 SAN 与链；
2. 替换 certs/server.crt 与 certs/server.key，保持私钥权限 600；
3. 重启 frontend 容器；
4. 在受控客户端用 --cacert 或浏览器重新验证；
5. 旧证书到期后再从客户端信任存储移除。

## 4. 配置

在发布包根复制配置模板：

~~~bash
cp backend/.env.example backend/.env
~~~

至少设置：

~~~dotenv
APP_PUBLIC_URL=https://10.13.51.8:8443
ALLOWED_ORIGINS=["https://10.13.51.8:8443"]
ALLOWED_HOSTS=["10.13.51.8"]
ALLOW_LOOPBACK_HTTP=false
AUTH_COOKIE_SECURE=true
TRUSTED_PROXY_CIDRS=["172.16.0.0/12"]
~~~

PUBLIC_ORIGIN 必须与浏览器地址栏完全一致（协议、主机、端口）；ALLOWED_HOSTS 必须包含 PUBLIC_HOSTNAME；APP_PUBLIC_URL 的 host 也必须在 ALLOWED_HOSTS 中。不要把 127.0.0.1 加入 TRUSTED_PROXY_CIDRS，除非后端端口没有在本机回环发布；应用默认只信任 Docker 网桥来源。

## 5. 启动与停止

在发布包根执行：

~~~bash
export PUBLIC_HOSTNAME=10.13.51.8
export PUBLIC_ORIGIN=https://10.13.51.8:8443
export PUBLIC_BIND_ADDRESS=10.13.51.8
export PUBLIC_HTTPS_PORT=8443
export TLS_CERT_DIR=./certs
docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.lan.yml ps
docker compose -f docker-compose.yml -f docker-compose.lan.yml logs -f frontend
~~~

停止：

~~~bash
docker compose -f docker-compose.yml -f docker-compose.lan.yml down
~~~

停止不会删除 backend/data；不要在服务运行时直接复制 SQLite。备份前先停止 backend，再整体复制 data 目录（含 WAL/SHM）。

## 6. 验证

### 6.1 命令行

~~~powershell
curl --cacert certs/ca.crt https://10.13.51.8:8443/api/health
curl -I --cacert certs/ca.crt https://10.13.51.8:8443/
curl -I http://10.13.51.8/
~~~

期望：

- /api/health 返回 200 {"status":"ok"}；
- 根页面 200，含 CSP、HSTS、Referrer-Policy、X-Frame-Options、Permissions-Policy、X-Content-Type-Options；
- HTTP 的 80 端口只返回健康检查或 308 跳转到 HTTPS；
- 不通过证书警告绕过访问。

### 6.2 浏览器

1. 打开 https://<PUBLIC_HOSTNAME>:<PUBLIC_HTTPS_PORT>/login；
2. 确认地址栏无证书警告；
3. 登录后检查开发者工具：session Cookie 为 __Host-session、Secure、HttpOnly、SameSite=Strict、无 Domain；__Host-csrf 为 Secure、非 HttpOnly；
4. API 响应 Cache-Control 为 no-store；
5. 页面无混合内容、无外部字体/脚本/图片；
6. 退出登录后不应保留业务数据。

### 6.3 边界检查

- 从局域网其他地址直接连接后端 8000 端口应超时或拒绝；
- 伪造 Host 请求应返回 403 HOST_INVALID；
- 伪造 Origin 的写请求应返回 403 ORIGIN_INVALID；
- 缺失 CSRF 的写请求应返回 403 CSRF_INVALID；
- 不应能通过 /data、/generated、/published 等静态路径读取资源文件。

## 7. 故障排查

| 现象 | 含义／处理 |
| --- | --- |
| 浏览器证书错误 | SAN 与地址不一致、客户端未信任 CA、证书过期或链不完整；重新签发并安装 CA，不要忽略警告 |
| 启动即失败，提示 APP_PUBLIC_URL host | APP_PUBLIC_URL 的 host 未加入 ALLOWED_HOSTS；添加精确主机名 |
| 403 HOST_INVALID | 请求 Host 不在 ALLOWED_HOSTS，或代理丢失 Host；检查 PUBLIC_HOSTNAME 与 Nginx 代理头 |
| 403 ORIGIN_INVALID | ALLOWED_ORIGINS 与浏览器地址不一致；必须含完整协议、主机和端口 |
| 403 CSRF_INVALID | 前端 CSRF Cookie 未带上或跨源；确认同源访问、Cookie Secure、页面刷新后重试 |
| 502 Bad Gateway | Nginx 到后端 upstream 不通；检查 API_UPSTREAM、后端健康、容器网络 |
| 局域网能访问后端 8000 | 后端端口被错误发布；恢复为 127.0.0.1:8000，检查 Compose 覆盖 |
| HSTS 缺失 | 访问了 HTTP 或错误模板；确认 frontend 使用 nginx-tls.conf.template 构建 |
| 登录后立刻掉线 | Cookie 被浏览器拒绝；检查 Secure、SameSite、__Host- 前缀和证书地址一致性 |
| 证书私钥泄露风险 | 立即停止服务、吊销/更换证书、限制目录权限并审计访问 |

## 8. 安全与数据边界

- 只开放 HTTPS 端口；不开放 8000、SQLite 文件、data 目录或诊断旁路。
- 私钥、CA 私钥、.env 和真实数据库不进入 Git、镜像或发布包。
- PUBLIC_ORIGIN、ALLOWED_HOSTS、ALLOWED_ORIGINS 使用精确值；不使用通配符或动态反射。
- 可信代理只填实际反向代理的网段；不要为了“能登录”而信任任意来源。
- 证书到期前完成轮换；轮换期间保留旧证书的信任直到新证书验证通过。
- 正式开放前按 #85 Release-GATE 重新跑全量测试、真实设备验收、备份与恢复演练。
