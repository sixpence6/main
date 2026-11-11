"""Controlled app for independent-process restart scenarios (issue #117 T05).

Every provider call is logged to T05_CALL_LOG. Crash modes exit the process at
one exact boundary so the next startup must recover from persisted facts alone.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from PIL import Image

from app.adapters.base import ProviderOutcome
from app.config import Settings
from app.main import create_app
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.t02_prompt import responder


def _log(event: str) -> None:
    path = os.environ.get("T05_CALL_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(event + "\n")


def _png(size: tuple[int, int] = (1024, 1024)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (210, 220, 235)).save(buffer, format="PNG")
    return buffer.getvalue()


def _exit_at(marker: str) -> None:
    _log(marker)
    os._exit(17)


def _crash_before_confirm(*args: Any, **kwargs: Any) -> None:
    # Called inside the single confirm transaction, before commit.
    _exit_at("crash:before-confirm")


def _crash_after_confirm(*args: Any, **kwargs: Any) -> None:
    # Called after the confirm transaction committed, before any adapter call.
    _exit_at("crash:after-confirm")


async def _crash_after_submit_mark(*args: Any, **kwargs: Any) -> None:
    # Called after the durable submit marker, before execute_generation.
    _exit_at("crash:after-submit-mark")


class RecordingVisual(VisualStub):
    """Record every provider boundary crossing, then optionally crash."""

    async def submit(self, request):  # type: ignore[no-untyped-def]
        mode = os.environ.get("T05_MODE", "normal")
        _log("visual:submit")
        if mode == "crash-in-submit":
            _exit_at("crash:in-submit")
        outcome = await super().submit(request)
        _log("visual:submit-accepted")
        if mode == "crash-after-submit":
            _exit_at("crash:after-submit")
        return outcome

    async def poll(self, remote_task_id: str) -> ProviderOutcome:
        _log(f"visual:poll:{remote_task_id}")
        if os.environ.get("T05_MODE") == "crash-in-poll":
            _exit_at("crash:in-poll")
        return await super().poll(remote_task_id)


def build_app():
    mode = os.environ.get("T05_MODE", "normal")
    if mode == "crash-before-confirm":
        import app.final_confirmation as final_confirmation

        final_confirmation.write_audit = _crash_before_confirm
    elif mode == "crash-after-confirm":
        import app.workflow.stages.s08 as s08

        s08._schedule = _crash_after_confirm
    elif mode == "crash-after-submit-mark":
        import app.workflow.stages.s08 as s08

        s08.execute_generation = _crash_after_submit_mark

    visual = RecordingVisual()
    if mode == "crash-in-poll":
        visual.submit_outcome = ProviderOutcome(
            state="pending", remote_task_id="remote-t05"
        )
    else:
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=_png(), image_count=1
        )
    visual.poll_outcomes = [
        ProviderOutcome(state="succeeded", image_bytes=_png(), image_count=1)
    ]
    return create_app(
        settings=Settings(workflow_role_policy="legacy"),
        registry=build_test_registry(
            llm=LLMStub(role_responder=responder), visual=visual
        ),
    )


app = build_app()
