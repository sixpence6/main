from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus


@dataclass
class ApiError(Exception):
    """A safe, public error. Message never echoes raw input, URLs or credentials."""

    status_code: int
    code: str
    message: str
    headers: dict[str, str] | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"

    def as_body(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": self.code, "message": self.message}}


class StartupError(RuntimeError):
    """Raised when the process cannot safely serve traffic."""


def not_found_project() -> ApiError:
    return ApiError(404, "PROJECT_NOT_FOUND", "项目不存在")


def not_found_generation() -> ApiError:
    return ApiError(404, "GENERATION_NOT_FOUND", "生成记录不存在")


def not_found_asset() -> ApiError:
    return ApiError(404, "ASSET_NOT_FOUND", "图片资源不存在")


def validation_error(message: str = "请求参数不合法") -> ApiError:
    return ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", message)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(HTTPStatus.CONFLICT, code, message)
