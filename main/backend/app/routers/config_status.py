from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import ConfigStatus
from app.services.config_status import build_config_status

router = APIRouter()


@router.get("/api/config/status", response_model=ConfigStatus)
async def config_status(request: Request) -> ConfigStatus:
    state = request.app.state
    return build_config_status(state.settings, state.registry)
