from __future__ import annotations

import os
from pathlib import Path

from app.adapters.base import GenerationRequest, OrganizeRequest, RoleRequest
from app.config import Settings
from app.main import create_app
from tests.support.adapters import LLMStub, VisualStub, build_test_registry


def _log(event: str) -> None:
    path = os.environ.get("T20_CALL_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(event + "\n")


class LoggingLLM(LLMStub):
    async def complete(self, request: RoleRequest):
        _log("llm.complete")
        return await super().complete(request)

    async def organize(self, request: OrganizeRequest):
        _log("llm.organize")
        return await super().organize(request)


class LoggingVisual(VisualStub):
    async def submit(self, request: GenerationRequest):
        _log("visual.submit")
        return await super().submit(request)

    async def poll(self, remote_task_id: str):
        _log("visual.poll")
        return await super().poll(remote_task_id)

    async def fetch(self, remote_task_id: str):
        _log("visual.fetch")
        return await super().fetch(remote_task_id)


def build_app():
    # T20：真实重启验收使用与生产相同的 create_app，仅替换外部适配器并记录调用。
    return create_app(
        settings=Settings(),
        registry=build_test_registry(llm=LoggingLLM(), visual=LoggingVisual()),
    )


app = build_app()
