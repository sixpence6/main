"""Explicit user confirmation for end-to-end tests that need an actual image.

Waiting/refresh acceptance tests must use the original _drain instead.
"""
from uuid import uuid4

from sqlalchemy import select

from app.workflow.tables import WorkflowRun
from tests.support.t02_prompt import final_click
from tests.test_t65_generation_cycle import _drain


async def finish_with_prompt_confirmation(app, client):
    await _drain(app)
    if app.state.settings.workflow_role_policy != "collaborative":
        return
    async with app.state.session_factory() as session:
        projects = (await session.scalars(select(WorkflowRun.project_id).where(
            WorkflowRun.stage == "S07", WorkflowRun.status == "needs_input"
        ).distinct())).all()
    for project_id in projects:
        response = await client.get(f"/api/projects/{project_id}/workflow")
        if response.status_code != 200 or not response.json().get("prompt_preview"):
            continue
        workflow = response.json()
        assert workflow["prompt_preview"]["can_confirm"]
        clicked = await final_click(client, {"project_id": project_id, "workflow": workflow}, command_id=str(uuid4()))
        assert clicked.status_code == 200, clicked.text
        await _drain(app)
