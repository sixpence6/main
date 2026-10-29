from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

APP_DIR = Path(__file__).resolve().parent
MAIN_DIR = APP_DIR.parent.parent
REPO_ROOT = MAIN_DIR.parent

BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_DATA_DIR = BACKEND_DIR / "data"
DEFAULT_WEB_DIST_DIR = MAIN_DIR / "dist"
DEFAULT_ENV_FILE = REPO_ROOT / "backend" / ".env"

ROLE_LLM = "llm"
ROLE_VISUAL = "visual"

#: Hard local cap for a reference upload, independent of the provider capability.
LOCAL_REFERENCE_MAX_BYTES = 10_000_000
#: Upload requests may carry this much multipart framing on top of the file cap.
REFERENCE_MULTIPART_OVERHEAD_BYTES = 64 * 1024
#: Result files larger than this are rejected before they reach the data dir.
LOCAL_RESULT_MAX_BYTES = 64 * 1024 * 1024

REQUIRED_ROLE_FIELDS = ("adapter", "base_url", "api_key", "model")


def _resolve_backend_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BACKEND_DIR / path


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class Settings(BaseSettings):
    """Configuration resolved from project locations, OS env first, then backend/.env."""

    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    data_dir: Path = Field(
        default=DEFAULT_DATA_DIR,
        validation_alias=AliasChoices("APP_DATA_DIR", "DATA_DIR"),
    )
    database_url: str | None = None
    web_dist_dir: Path = DEFAULT_WEB_DIST_DIR
    host: str = "127.0.0.1"
    port: int = 8000

    llm_adapter: str | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_vision_model: str | None = None
    llm_timeout_seconds: int = 60

    visual_adapter: str | None = None
    visual_base_url: str | None = None
    visual_api_key: SecretStr | None = None
    visual_model: str | None = None
    visual_image_size: str | None = None
    visual_timeout_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "VISUAL_TASK_TIMEOUT_SECONDS", "VISUAL_TIMEOUT_SECONDS"
        ),
    )

    @field_validator("data_dir", "web_dist_dir", mode="before")
    @classmethod
    def _resolve_relative(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value)
        return value

    @property
    def database_path(self) -> Path:
        if self.database_url:
            url = make_url(self.database_url)
            database = url.database
            if database and database != ":memory:":
                return _resolve_backend_path(database)
            return Path(database or "")
        return self.resolved_data_dir() / "app.db"

    @property
    def database_dsn(self) -> str:
        if self.database_url:
            url = make_url(self.database_url)
            database = url.database
            if (
                url.get_backend_name() == "sqlite"
                and database
                and database != ":memory:"
            ):
                path = Path(database)
                if not path.is_absolute():
                    url = url.set(database=str(_resolve_backend_path(path)))
            return url.render_as_string(hide_password=False)
        return "sqlite+aiosqlite:///" + self.database_path.as_posix()

    @property
    def references_dir(self) -> Path:
        return self.resolved_data_dir() / "references"

    @property
    def generated_dir(self) -> Path:
        return self.resolved_data_dir() / "generated"

    @property
    def temp_dir(self) -> Path:
        return self.resolved_data_dir() / "tmp"

    def resolved_data_dir(self) -> Path:
        return _resolve_backend_path(self.data_dir)

    def resolved_web_dist_dir(self) -> Path:
        path = self.web_dist_dir
        return path if path.is_absolute() else REPO_ROOT / path

    def llm(self) -> RoleConfig:
        return RoleConfig(
            role=ROLE_LLM,
            adapter_id=_blank(self.llm_adapter),
            base_url=_blank(self.llm_base_url),
            api_key=(
                self.llm_api_key
                if self.llm_api_key and self.llm_api_key.get_secret_value().strip()
                else None
            ),
            model=_blank(self.llm_model),
            timeout_seconds=max(int(self.llm_timeout_seconds), 1),
            image_size=None,
            image_model=_blank(self.llm_vision_model),
        )

    def visual(self) -> RoleConfig:
        return RoleConfig(
            role=ROLE_VISUAL,
            adapter_id=_blank(self.visual_adapter),
            base_url=_blank(self.visual_base_url),
            api_key=self.visual_api_key
            if self.visual_api_key and self.visual_api_key.get_secret_value().strip()
            else None,
            model=_blank(self.visual_model),
            timeout_seconds=max(int(self.visual_timeout_seconds), 1),
            image_size=_blank(self.visual_image_size),
        )


@dataclass(frozen=True)
class RoleConfig:
    role: str
    adapter_id: str | None
    base_url: str | None
    api_key: SecretStr | None
    model: str | None
    timeout_seconds: int
    image_size: str | None = None
    image_model: str | None = None

    @property
    def configured(self) -> bool:
        return not self.missing_fields()

    def missing_fields(self) -> list[str]:
        values = {
            "adapter": self.adapter_id,
            "base_url": self.base_url,
            "api_key": self.api_key.get_secret_value() if self.api_key else None,
            "model": self.model,
        }
        return [name for name in REQUIRED_ROLE_FIELDS if not values.get(name)]
