from __future__ import annotations

import asyncio
import os
from pathlib import Path

from app.adapters.base import OrganizeResult
from app.adapters.registry import AdapterRegistry
from app.config import Settings
from app.main import create_app


class BlockingLLM:
    """Test-only LLM adapter that never answers, so the process can be killed mid-flight."""

    adapter_id = "stub-llm"

    async def organize(self, request) -> OrganizeResult:  # noqa: ARG002
        marker = os.environ.get("ORGANIZE_CALL_MARKER")
        if marker:
            await asyncio.to_thread(Path(marker).write_text, "called", encoding="utf-8")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def build_app():
    registry = AdapterRegistry()
    registry.register_llm("stub-llm", lambda role, client: BlockingLLM())
    return create_app(settings=Settings(workflow_role_policy="legacy"), registry=registry)


app = build_app()
