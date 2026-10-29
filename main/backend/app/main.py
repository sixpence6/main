from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.adapters.registry import AdapterRegistry, build_default_registry
from app.config import Settings
from app.db import ProjectLocks, build_session_factory, create_engine
from app.errors import ApiError, StartupError
from app.routers import assets, briefs, config_status, health, messages, projects, web
from app.services.recovery import repair_leftover_state
from app.startup import ensure_data_dir, prepare_database

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    ensure_data_dir(settings)
    engine = create_engine(settings)
    app.state.engine = engine
    try:
        await prepare_database(engine)
        app.state.session_factory = build_session_factory(engine)
        app.state.locks = ProjectLocks()
        app.state.http = httpx.AsyncClient(timeout=None)
        await repair_leftover_state(app)
        logger.info("backend ready on data dir %s", settings.resolved_data_dir())
        yield
    except StartupError:
        logger.error("startup aborted: %s", "see previous log entry")
        raise
    finally:
        client = getattr(app.state, "http", None)
        if client is not None:
            await client.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None, registry: AdapterRegistry | None = None) -> FastAPI:
    resolved = settings or Settings()
    app = FastAPI(
        title="电商生图智能体后端",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = resolved
    app.state.registry = registry or build_default_registry()

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.as_body(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        message = "请求参数不合法"
        errors = exc.errors()
        if errors:
            location = [part for part in errors[0].get("loc", ()) if isinstance(part, str)]
            field = next((part for part in reversed(location) if part not in {"body", "query", "path"}), None)
            if field:
                message = f"字段 {field} 不合法"
        return JSONResponse(
            {"error": {"code": "VALIDATION_ERROR", "message": message}}, status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404 and request.url.path.startswith("/api/"):
            return JSONResponse(
                {"error": {"code": "ROUTE_NOT_FOUND", "message": "接口不存在"}}, status_code=404
            )
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse({"error": {"code": "HTTP_ERROR", "message": detail}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            {"error": {"code": "INTERNAL_ERROR", "message": "服务内部错误"}}, status_code=500
        )

    app.include_router(health.router)
    app.include_router(config_status.router)
    app.include_router(projects.router)
    app.include_router(messages.router)
    app.include_router(briefs.router)
    app.include_router(assets.router)

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    async def unknown_api(rest: str):  # noqa: ARG001 - path is only used for matching
        raise ApiError(404, "ROUTE_NOT_FOUND", "接口不存在")

    web.register_web_routes(app, resolved)
    return app


app = create_app()
