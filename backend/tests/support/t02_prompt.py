"""Controlled adapters for real preparation/final-click HTTP scenarios."""

from tests.support.adapters import VisualStub
from tests.support.t01_prompt import responder as preparation_responder
from tests.test_t65_generation_cycle import _responder as reviewed_delivery_responder


def responder(request):
    if request.payload["task"]["stage"] in {"S09", "S10"}:
        return reviewed_delivery_responder(request)
    return preparation_responder(request)


async def ready_project(app, client):
    from tests.support.t01_prompt import prepared
    from tests.test_t65_generation_cycle import _drain

    project_id = await prepared(client)
    await _drain(app)
    view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
    return {"project_id": project_id, "revision": view["project_revision"], "workflow": view}


async def final_click(client, setup, command_id="22222222-2222-4222-8222-222222222114"):
    view = setup["workflow"]
    preview = view["prompt_preview"]
    return await client.post(
        f"/api/projects/{setup['project_id']}/workflow/actions/confirm_prompt",
        json={
            "expected_revision": view["project_revision"],
            "payload": {
                "snapshot_id": preview["id"],
                "snapshot_digest": preview["request_digest"],
                "command_id": command_id,
                "command_context": preview["command_context"],
            },
        },
    )


class SubmissionObservedVisual(VisualStub):
    """Observe entry to the external submit boundary, before the controlled gate."""

    def __init__(self, **kwargs):
        import asyncio

        super().__init__(**kwargs)
        self.submit_started = asyncio.Event()

    async def submit(self, request):
        self.submit_started.set()
        return await super().submit(request)
