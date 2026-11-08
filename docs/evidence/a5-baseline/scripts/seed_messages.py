import asyncio
import sys
import uuid

sys.path.insert(0, r"D:\Desktop\新建文件夹\main\backend")

from sqlalchemy import select, update

import app.auth.models  # noqa: F401

from app.config import Settings
from app.db import build_session_factory, create_engine
from app.models import Message, Project
from app.startup import prepare_database
from app.timeutil import format_timestamp

PROJECT_ID = "fd12830f-9858-4dd3-a1d9-e53935f5c138"

TITLE = "秋季新款保温杯主图"

BRIEF = {
    "product_description": "316 不锈钢真空保温杯，磨砂哑光外壳，樱花粉配色，杯身印有极简几何纹样。",
    "purpose": "电商主图首图，用于天猫详情页与直通车素材。",
    "style": "高端沉稳，柔和自然光，浅灰象牙色背景，产品居中。",
    "composition": "产品居中占画面 65%，右上角留白放卖点文案。",
    "text_content": "12h 长效保温",
    "image_size": "3840x2160",
    "prompt": "",
}

MESSAGES = [
    ("user", "帮我做一张保温杯的电商主图，风格要高端沉稳一些，背景不要太花哨。", "conversation"),
    ("assistant", "已记录需求：316 不锈钢保温杯，樱花粉磨砂外壳，浅灰象牙色背景，产品居中。还需要补充卖点文案吗？", "conversation"),
    ("user", "卖点写“12h 长效保温”，画面右上角留白。", "conversation"),
    ("assistant", "好的，需求已整理完成。确认提示词后即可生成 3840x2160 的成品图。", "workflow"),
]


async def main() -> None:
    settings = Settings()
    engine = create_engine(settings)
    await prepare_database(engine)
    factory = build_session_factory(engine)
    now = format_timestamp()
    try:
        async with factory() as session:
            project = await session.get(Project, PROJECT_ID)
            if project is None:
                print("project missing")
                return
            project.title = TITLE
            project.product_description = BRIEF["product_description"]
            project.purpose = BRIEF["purpose"]
            project.style = BRIEF["style"]
            project.composition = BRIEF["composition"]
            project.text_content = BRIEF["text_content"]
            project.image_size = BRIEF["image_size"]
            project.prompt = (
                "高端电商产品主图：316 不锈钢真空保温杯，樱花粉磨砂哑光外壳，"
                "杯身极简几何纹样，浅灰象牙色无缝背景，柔和自然光从左上角入射，"
                "产品居中占画面 65%，右上角留白用于文案「12h 长效保温」，"
                "影棚级质感，8k 细节，无多余装饰。"
            )
            project.brief_status = "review"
            project.revision = 1
            project.updated_at = now

            existing = await session.scalar(
                select(Message).where(Message.project_id == PROJECT_ID)
            )
            if existing is None:
                for index, (role, content, kind) in enumerate(MESSAGES, start=1):
                    session.add(
                        Message(
                            id=str(uuid.uuid4()),
                            project_id=PROJECT_ID,
                            sequence_no=index,
                            role=role,
                            content=content,
                            project_revision=1 if index > 2 else 0,
                            brief_snapshot_json=None,
                            turn_kind=kind,
                            created_at=now,
                        )
                    )
            else:
                await session.execute(
                    update(Message).where(Message.project_id == PROJECT_ID).values(created_at=now)
                )
            await session.commit()
            print("seeded", PROJECT_ID)
    finally:
        await engine.dispose()


asyncio.run(main())
