import asyncio
import sys

sys.path.insert(0, r"D:\Desktop\新建文件夹\main\backend")

from app.auth.clock import Clock
from app.auth.passwords import HashGate
from app.auth.service import AlreadyInitialized, init_admin
from app.config import Settings
from app.db import build_session_factory, create_engine
from app.startup import ensure_data_dir, prepare_database

EMAIL = "baseline@example.test"
PASSWORD = "Wayfinder-2026-baseline"


async def main() -> None:
    settings = Settings()
    ensure_data_dir(settings)
    engine = create_engine(settings)
    await prepare_database(engine)
    factory = build_session_factory(engine)
    try:
        account = await init_admin(
            factory,
            email=EMAIL,
            password=PASSWORD,
            clock=Clock(),
            hash_gate=HashGate(),
        )
        print("created", account.email_normalized, account.id)
    except AlreadyInitialized as exc:
        print("already initialized:", exc)
    finally:
        await engine.dispose()


asyncio.run(main())
