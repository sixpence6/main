import asyncio

import pytest

from app.errors import ApiError
from app.workflow.stages import s10
from app.workflow.state import load_context
from tests.test_v2_t13_delivery import _approve, _prepare, _real_delivery_env


async def test_failed_duplicate_publisher_cannot_delete_published_image(full_settings, monkeypatch):
    async with _real_delivery_env(full_settings) as env:
        await _prepare(env)
        await _approve(env)
        ctx = await load_context(env.app, env.ids.project_id)
        real_to_thread = asyncio.to_thread
        first_waiting = asyncio.Event()
        release_first = asyncio.Event()
        write_count = 0

        async def interleave(func, *args, **kwargs):
            nonlocal write_count
            if func is s10.write_asset_file:
                write_count += 1
                if write_count == 1:
                    first_waiting.set()
                    await release_first.wait()
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(s10.asyncio, "to_thread", interleave)
        first = asyncio.create_task(s10._publish(ctx))
        try:
            await asyncio.wait_for(first_waiting.wait(), timeout=10)
            await s10._publish(ctx)
        finally:
            release_first.set()
        with pytest.raises(ApiError):
            await first
        preview = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert preview.status_code == 200, preview.text
        assert preview.content == env.ids.image_bytes
        download = await env.client.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert download.status_code == 200, download.text
        assert download.content == preview.content
        view = await env.client.get(f"/api/projects/{env.ids.project_id}/workflow")
        assert view.json()["versions"][0]["can_download"] is True


async def test_missing_published_file_reports_actual_reason(full_settings):
    async with _real_delivery_env(full_settings) as env:
        await _prepare(env)
        await _approve(env)
        ctx = await load_context(env.app, env.ids.project_id)
        await s10._publish(ctx)
        from app.models import Asset

        async with env.app.state.session_factory() as session:
            asset = await session.get(Asset, env.ids.asset_id)
            (env.settings.resolved_data_dir() / asset.relative_path).unlink()
        response = await env.client.get(f"/api/projects/{env.ids.project_id}/workflow")
        version = response.json()["versions"][0]
        assert version["can_download"] is False
        assert version["unavailable_reason"] == "ASSET_FILE_MISSING"
