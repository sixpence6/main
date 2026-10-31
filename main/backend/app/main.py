from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.adapters.registry import AdapterRegistry, build_default_registry
from app.auth.admin_router import router as admin_router
from app.auth.clock import Clock
from app.auth.link_router import router as link_router
from app.auth.middleware import AuthMiddleware
from app.auth.passwords import HashGate
from app.auth.router import router as auth_router
from app.config import Settings
from app.db import ProjectLocks, build_session_factory, create_engine
from app.errors import ApiError, StartupError
from app.maintenance.coordinator import (
    BUSINESS_WRITE,
    EXPIRY_DELETION,
    MaintenanceBlocked,
    MaintenanceCoordinator,
    get_active_coordinator,
    set_active_coordinator,
)
from app.routers import (
    assets,
    briefs,
    config_status,
    generations,
    health,
    messages,
    projects,
    web,
    workflow,
)
from app.services.recovery import repair_leftover_state
from app.services.retention import run_retention_cleanup
from app.startup import ensure_data_dir, prepare_database

logger = logging.getLogger("app")

# T20：运行中的到期清理间隔。启动时先执行一次，之后按固定周期复核。
RETENTION_INTERVAL_SECONDS = 15 * 60


async def _run_retention_cleanup(app: FastAPI) -> None:
    """保留期清理失败不能阻断服务启动，也不能向调用方泄露内部信息。"""
    coordinator = getattr(app.state, "maintenance", None)
    try:
        if coordinator is None:
            await run_retention_cleanup(app)
        else:
            async with coordinator.guard(EXPIRY_DELETION):
                await run_retention_cleanup(app)
    except MaintenanceBlocked:
        # A maintenance window owns the data root; skip this pass entirely.
        return
    except Exception:
        logger.exception("retention cleanup failed")


async def _retention_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(RETENTION_INTERVAL_SECONDS)
        await _run_retention_cleanup(app)


def build_http_client() -> httpx.AsyncClient:
    """Shared outbound client for model providers.

    Ambient proxy configuration is ignored on purpose: environments that export
    bracketed IPv6 entries in NO_PROXY make httpx 0.28 abort while building a
    client, which would prevent the backend from starting at all. Provider
    reachability must be decided by explicit configuration, not by the shell.
    """
    return httpx.AsyncClient(timeout=None, trust_env=False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    set_active_coordinator(app.state.maintenance)
    ensure_data_dir(settings)
    engine = create_engine(settings)
    app.state.engine = engine
    try:
        await prepare_database(engine)
        app.state.session_factory = build_session_factory(engine)
        app.state.locks = ProjectLocks()
        app.state.generation_slots = asyncio.Semaphore(1)
        app.state.generation_tasks = set()
        app.state.http = build_http_client()
        await repair_leftover_state(app)
        # T20：修复遗留状态后先清理一次，再启动周期清理任务。
        await _run_retention_cleanup(app)
        app.state.retention_task = asyncio.create_task(_retention_loop(app))
        logger.info("backend ready on data dir %s", settings.resolved_data_dir())
        yield
    except StartupError:
        logger.error("startup aborted: %s", "see previous log entry")
        raise
    finally:
        retention_task = getattr(app.state, "retention_task", None)
        if retention_task is not None:
            retention_task.cancel()
            await asyncio.gather(retention_task, return_exceptions=True)
        tasks = getattr(app.state, "generation_tasks", set())
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client = getattr(app.state, "http", None)
        if client is not None:
            await client.aclose()
        await engine.dispose()
        if get_active_coordinator() is app.state.maintenance:
            set_active_coordinator(None)


def create_app(
    settings: Settings | None = None, registry: AdapterRegistry | None = None
) -> FastAPI:
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
    app.state.maintenance = MaintenanceCoordinator()
    app.state.clock = Clock()
    app.state.hash_gate = HashGate()

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.as_body(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(MaintenanceBlocked)
    async def _maintenance_blocked(_request: Request, exc: MaintenanceBlocked) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": exc.code, "message": "服务维护中，请稍后重试"}},
            status_code=503,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        message = "请求参数不合法"
        errors = exc.errors()
        if errors:
            location = [part for part in errors[0].get("loc", ()) if isinstance(part, str)]
            field = next(
                (part for part in reversed(location) if part not in {"body", "query", "path"}),
                None,
            )
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
        return JSONResponse(
            {"error": {"code": "HTTP_ERROR", "message": detail}}, status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            {"error": {"code": "INTERNAL_ERROR", "message": "服务内部错误"}}, status_code=500
        )

    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(link_router)
    app.include_router(admin_router)
    app.include_router(config_status.router)
    app.include_router(projects.router)
    app.include_router(messages.router)
    app.include_router(briefs.router)
    app.include_router(assets.router)
    app.include_router(generations.router)
    app.include_router(workflow.router)

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    async def unknown_api(rest: str):  # noqa: ARG001 - path is only used for matching
        raise ApiError(404, "ROUTE_NOT_FOUND", "接口不存在")

    @app.middleware("http")
    async def _maintenance_write_gate(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith(
            "/api/"
        ):
            try:
                async with app.state.maintenance.guard(BUSINESS_WRITE):
                    return await call_next(request)
            except MaintenanceBlocked as exc:
                return JSONResponse(
                    {"error": {"code": exc.code, "message": "服务维护中，请稍后重试"}},
                    status_code=503,
                )
        return await call_next(request)

    web.register_web_routes(app, resolved)
    app.add_middleware(AuthMiddleware, settings=resolved)
    return app


app = create_app()
