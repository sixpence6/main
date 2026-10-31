# R1 本机首管理员初始化

本文只说明 ticket #55 提供的**本机维护入口**。它不提供网页注册、网页首管理员初始化或远程管理。

## 前置条件

- 实例为空：数据库中还没有管理员，`instance_bootstrap` 没有已初始化记录。
- 在服务器／维护者本机的真实终端中执行；标准输入和标准输出都必须是 TTY。
- 应用进程已停止，或至少确认没有并发初始化；数据库路径与正式服务一致。
- 已安装项目后端依赖，包括 `argon2-cffi`；组件不可用时命令会拒绝创建管理员。

## 命令

在 `main/backend` 工作目录执行项目虚拟环境中的 Python：

```powershell
cd main/backend
python -m app.auth.cli init-admin
```

可选的邮箱位置参数（密码仍然只能交互输入）：

```powershell
python -m app.auth.cli init-admin admin@example.com
```

## 交互与结果

1. 如果未提供邮箱，提示输入管理员邮箱；邮箱会去除首尾空白并做大小写不敏感规范化。
2. 使用 `getpass` 读取密码两次，终端不回显；没有 `--password` 参数，不接受命令行明文。
3. 密码要求：至少 15 个 Unicode code point、最多 128 个；空格与 Unicode 保留，不静默裁剪或截断；命中版本化本地弱口令表会被拒绝。
4. 成功时在同一数据库事务中创建管理员账号和初始化审计；随后打印规范化邮箱与不可变 UUID。
5. 已有管理员时拒绝重入，退出码非 0；不会覆盖或重置任何现有账号。
6. 非交互管道／重定向执行会被拒绝，退出码 2；并发初始化由数据库唯一约束和事务保证只有一个成功。

## 安全边界

- 该入口不监听网络，也不存在 `POST /api/...init-admin` 之类的 HTTP 路由。
- 密码、哈希和会话 token 不写入命令行、URL、普通日志或前端存储。
- 本入口只在空库创建首管理员；普通账号创建与一次性链接见 `#56`／`docs/operations/r1-account-links.md`，本人改密与多端会话见 `#57`／`docs/acceptance/r1-personal-sessions.md`，管理员遗失恢复见 `docs/operations/r1-local-admin-recovery.md`，禁用／恢复见 `#58`；不要通过数据库手工插入绕过。
- 正式环境使用 HTTPS；`AUTH_COOKIE_SECURE=true` 是默认值。只有显式本机回环开发才可设置 `ALLOW_LOOPBACK_HTTP=true` 与 `AUTH_COOKIE_SECURE=false`，不得用于局域网或生产。

## 失败处理

| 现象 | 含义／处理 |
|---|---|
| 提示“实例已经初始化，拒绝重复创建管理员” | 已有管理员；保留现有账号，按后续恢复流程处理，不要删除数据 |
| 密码不符合要求 | 按提示修正；不会写入账号 |
| 认证组件不可用 | `argon2-cffi`／弱口令表不可用；先修复依赖，命令 fail-closed |
| 非 TTY 退出 2 | 必须在本机真实终端运行 |
| 数据库迁移不完整／不可写 | 先按运维流程执行迁移并检查数据目录权限 |

## 证据

对应的验收记录见 `docs/acceptance/r1-auth-foundation.md`。CLI 的非 TTY、无 `--password`、并发一次性与审计回滚在 `backend/tests/test_t55_auth_foundation.py` 中有自动化覆盖。
