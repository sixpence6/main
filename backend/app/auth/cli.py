from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.auth.passwords import AuthUnavailable, PasswordPolicyError
from app.auth.personal import RecoveryNotFound, recover_admin
from app.auth.service import AlreadyInitialized, init_admin
from app.config import Settings
from app.db import build_session_factory, create_engine
from app.errors import ApiError
from app.startup import ensure_data_dir, prepare_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.auth.cli",
        description="Local maintenance commands. Passwords are read from the terminal only.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-admin", help="Initialize the single local administrator")
    init.add_argument("email", nargs="?", help="Administrator email (prompted when omitted)")
    recover = commands.add_parser(
        "recover-admin", help="Reset the sole administrator password from this local terminal"
    )
    recover.add_argument("email", nargs="?", help="Administrator email (sole admin when omitted)")
    return parser


def _read_password(label: str = "管理员密码") -> str:
    first = getpass.getpass(f"{label}（输入不回显）: ")
    second = getpass.getpass(f"再次输入{label}: ")
    if first != second:
        print("两次输入的密码不一致。", file=sys.stderr)
        raise SystemExit(2)
    return first


def _local_runtime():
    settings = Settings()
    ensure_data_dir(settings)
    engine = create_engine(settings)
    return settings, engine


async def _run_init(email: str, password: str) -> None:
    settings, engine = _local_runtime()
    try:
        await prepare_database(engine)
        factory = build_session_factory(engine)
        from app.auth.clock import Clock
        from app.auth.passwords import HashGate

        account = await init_admin(
            factory,
            email=email,
            password=password,
            clock=Clock(),
            hash_gate=HashGate(),
        )
        print(f"管理员已创建：{account.email_normalized}（{account.id}）")
    finally:
        await engine.dispose()


async def _run_recover(email: str | None, password: str) -> None:
    settings, engine = _local_runtime()
    try:
        await prepare_database(engine)
        factory = build_session_factory(engine)
        from app.auth.clock import Clock
        from app.auth.passwords import HashGate

        account = await recover_admin(
            factory,
            email=email,
            password=password,
            clock=Clock(),
            hash_gate=HashGate(),
        )
        print(f"管理员密码已恢复：{account.email_normalized}（{account.id}）")
        print("该账号的全部会话和未使用凭证已撤销。")
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command not in {"init-admin", "recover-admin"}:
        parser.error("unknown command")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("本机维护命令只能在本机交互终端中运行。", file=sys.stderr)
        return 2

    email = args.email
    if args.command == "init-admin":
        if not email:
            email = input("管理员邮箱: ").strip()
        password_label = "管理员密码"
    else:
        email = email.strip() if email else None
        password_label = "新的管理员密码"

    try:
        password = _read_password(password_label)
    except (EOFError, KeyboardInterrupt):
        print("已取消。", file=sys.stderr)
        return 2
    try:
        if args.command == "init-admin":
            asyncio.run(_run_init(email or "", password))
        else:
            asyncio.run(_run_recover(email, password))
    except AlreadyInitialized:
        print("实例已经初始化，拒绝重复创建管理员。", file=sys.stderr)
        return 1
    except RecoveryNotFound:
        print("未找到唯一的首管账号，已拒绝恢复。", file=sys.stderr)
        return 1
    except PasswordPolicyError as exc:
        print(f"密码不符合要求：{exc.message}", file=sys.stderr)
        return 2
    except (AuthUnavailable, ApiError) as exc:
        message = getattr(exc, "message", "认证组件不可用")
        print(f"认证组件不可用，已拒绝执行：{message}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"参数不合法：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
