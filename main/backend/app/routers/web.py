from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from app.config import Settings

MISSING_BUILD_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>工作台尚未构建</title></head>
<body style="font-family: sans-serif; padding: 2rem">
<h1>工作台尚未构建</h1>
<p>未找到 Vite 构建产物。请在仓库根目录运行：</p>
<pre>npm --prefix main run build</pre>
<p>构建完成后刷新此页面。</p>
</body></html>
"""

NOT_FOUND_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>页面不存在</title></head>
<body style="font-family: sans-serif; padding: 2rem"><h1>页面不存在</h1></body></html>
"""


def _safe_target(dist: Path, full_path: str) -> Path | None:
    try:
        target = (dist / full_path).resolve()
        target.relative_to(dist.resolve())
    except (ValueError, OSError):
        return None
    return target


def register_web_routes(app: FastAPI, settings: Settings) -> None:
    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve_workspace(full_path: str):
        dist = settings.resolved_web_dist_dir()
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(MISSING_BUILD_PAGE, status_code=503)
        if full_path:
            target = _safe_target(dist, full_path)
            if target is None:
                return HTMLResponse(NOT_FOUND_PAGE, status_code=404)
            if target.is_file():
                return FileResponse(target)
            if "." in Path(full_path).name:
                return HTMLResponse(NOT_FOUND_PAGE, status_code=404)
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-store"})
