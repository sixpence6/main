from __future__ import annotations

from fastapi import APIRouter, Request

from app.db import check_database_readable
from app.errors import ApiError
from app.schemas import HealthResponse

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    try:
        await check_database_readable(request.app.state.engine)
    except Exception as exc:  # noqa: BLE001 - mapped to a safe public error
        raise ApiError(503, "DATABASE_BUSY", "数据库当前不可读取") from exc
    return HealthResponse(status="ok")
