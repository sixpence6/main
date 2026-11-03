"""Read-only restart probe; any model access is a test failure, never a real provider."""

from __future__ import annotations

import os
from pathlib import Path

from app.config import Settings
from app.main import create_app
from tests.support.adapters import LLMStub, VisualStub, build_test_registry


def unexpected(name):
    with Path(os.environ["T01_CALL_LOG"]).open("a", encoding="utf-8") as out:
        out.write(name + "\n")
    raise AssertionError("model access during read-only restart: " + name)


class NoText(LLMStub):
    async def complete(self, request):
        unexpected("text")


class NoImage(VisualStub):
    async def submit(self, request):
        unexpected("submit")

    async def poll(self, remote_task_id):
        unexpected("poll")

    async def fetch_result(self, locator):
        unexpected("fetch")


settings = Settings(_env_file=None)
app = create_app(settings=settings, registry=build_test_registry(llm=NoText(), visual=NoImage()))
