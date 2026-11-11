from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

APP_DIR = Path(__file__).resolve().parent
MAIN_DIR = APP_DIR.parent.parent

# Two layouts are supported: the repository, where the backend workspace lives
# beside main/, and the standalone package, where main/ itself is the project
# root (the docker image uses /app). The workspace marker decides.
_REPO_CANDIDATE = MAIN_DIR.parent
REPO_ROOT = (
    _REPO_CANDIDATE if (_REPO_CANDIDATE / "backend" / "alembic.ini").is_file() else MAIN_DIR
)

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

_HOSTNAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)

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
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "[::1]"]
    )
    allowed_origins: list[str] = Field(default_factory=list)
    allow_loopback_http: bool = False
    auth_cookie_secure: bool = True
    app_public_url: str | None = None
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    # Historical protocol controls. The current collaborative workflow always
    # skips content review; strict/requirements_only only serve compatibility.
    workflow_gate_mode: Literal["strict", "requirements_only", "off"] = "off"
    workflow_role_policy: Literal["collaborative", "consolidated", "legacy"] = "collaborative"

    # Release backup scheduling. The scheduler is disabled when APP_BACKUP_DIR
    # is absent; manual CLI backup remains available either way.
    backup_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_BACKUP_DIR", "BACKUP_DIR"),
    )
    backup_interval_seconds: int = Field(
        default=86400,
        gt=0,
        le=86400,
        validation_alias=AliasChoices(
            "APP_BACKUP_INTERVAL_SECONDS", "BACKUP_INTERVAL_SECONDS"
        ),
    )
    backup_keep: int = Field(
        default=7,
        ge=1,
        le=100,
        validation_alias=AliasChoices("APP_BACKUP_KEEP", "BACKUP_KEEP"),
    )
    backup_timeout_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias=AliasChoices(
            "APP_BACKUP_TIMEOUT_SECONDS", "BACKUP_TIMEOUT_SECONDS"
        ),
    )

    # R2 acceptance limits. Positive integers; they apply to every account,
    # including administrators. Changing a limit never deletes usage.
    generation_daily_limit: int = Field(default=20, gt=0)
    generation_personal_slots: int = Field(default=1, gt=0)
    generation_instance_slots: int = Field(default=2, gt=0)
    generation_project_slots: int = Field(default=1, gt=0)

    llm_adapter: str | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_vision_model: str | None = None
    llm_vision_base_url: str | None = None
    llm_vision_api_key: SecretStr | None = None
    workflow_handoff_format: Literal["json", "markdown"] = "markdown"
    llm_timeout_seconds: int = 3600
    llm_thinking_mode: Literal["auto", "enabled", "disabled"] = "auto"
    llm_reasoning_effort: Literal["auto", "low", "medium", "high"] = "auto"
    llm_vision_reasoning_effort: Literal["auto", "low", "medium", "high"] = "auto"

    visual_adapter: str | None = None
    visual_base_url: str | None = None
    visual_api_key: SecretStr | None = None
    visual_model: str | None = None
    visual_image_size: str | None = "3840x2160"
    visual_timeout_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "VISUAL_TASK_TIMEOUT_SECONDS", "VISUAL_TIMEOUT_SECONDS"
        ),
    )

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _validate_allowed_hosts(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("ALLOWED_HOSTS must be a list of host names")
        hosts: list[str] = []
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError("ALLOWED_HOSTS entries must be strings")
            host = raw.strip().lower()
            if not host:
                raise ValueError("ALLOWED_HOSTS entries must not be empty")
            if any(ch in host for ch in "*/@?#") or "://" in host:
                raise ValueError("ALLOWED_HOSTS must contain exact host names, not wildcards")
            if host.startswith("["):
                if not host.endswith("]"):
                    raise ValueError("ALLOWED_HOSTS contains an invalid IPv6 literal")
                try:
                    ipaddress.IPv6Address(host[1:-1])
                except ValueError as exc:
                    raise ValueError("ALLOWED_HOSTS contains an invalid IPv6 literal") from exc
            elif ":" in host:
                raise ValueError("ALLOWED_HOSTS must not contain ports")
            else:
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    if not _HOSTNAME_RE.fullmatch(host):
                        raise ValueError(
                            "ALLOWED_HOSTS contains an invalid host name"
                        ) from None
            if host not in hosts:
                hosts.append(host)
        return hosts

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _validate_allowed_origins(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("ALLOWED_ORIGINS must be a list of exact origins")
        origins: list[str] = []
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError("ALLOWED_ORIGINS entries must be strings")
            text = raw.strip()
            if not text or "*" in text:
                raise ValueError("ALLOWED_ORIGINS must contain exact origins, not wildcards")
            try:
                parts = urlsplit(text)
                _ = parts.port
            except ValueError as exc:
                raise ValueError("ALLOWED_ORIGINS contains an invalid origin") from exc
            if parts.scheme.lower() not in {"http", "https"}:
                raise ValueError("ALLOWED_ORIGINS must use http or https")
            if not parts.netloc or not parts.hostname:
                raise ValueError("ALLOWED_ORIGINS must include a host")
            if parts.username is not None or parts.password is not None:
                raise ValueError("ALLOWED_ORIGINS must not contain credentials")
            if parts.query or parts.fragment or parts.path not in {"", "/"}:
                raise ValueError("ALLOWED_ORIGINS must not contain a path, query or fragment")
            origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
            if origin not in origins:
                origins.append(origin)
        return origins

    @field_validator("trusted_proxy_cidrs", mode="before")
    @classmethod
    def _validate_trusted_proxy_cidrs(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("TRUSTED_PROXY_CIDRS must be a list of CIDRs")
        networks: list[str] = []
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError("TRUSTED_PROXY_CIDRS entries must be strings")
            text = raw.strip()
            if not text:
                raise ValueError("TRUSTED_PROXY_CIDRS entries must not be empty")
            try:
                network = ipaddress.ip_network(text, strict=False)
            except ValueError as exc:
                raise ValueError("TRUSTED_PROXY_CIDRS contains an invalid CIDR") from exc
            if network.prefixlen == 0:
                raise ValueError("TRUSTED_PROXY_CIDRS must not trust every address")
            canonical = str(network)
            if canonical not in networks:
                networks.append(canonical)
        return networks

    @field_validator("app_public_url", mode="before")
    @classmethod
    def _validate_app_public_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("APP_PUBLIC_URL must be a string")
        text = value.strip()
        if not text:
            return None
        try:
            parts = urlsplit(text)
            # Accessing port catches malformed ports such as ':abc'.
            _ = parts.port
        except ValueError as exc:
            raise ValueError("APP_PUBLIC_URL must be a valid absolute URL") from exc
        if parts.scheme.lower() not in {"http", "https"}:
            raise ValueError("APP_PUBLIC_URL must use http or https")
        if not parts.netloc or not parts.hostname:
            raise ValueError("APP_PUBLIC_URL must include a host")
        if parts.username is not None or parts.password is not None:
            raise ValueError("APP_PUBLIC_URL must not contain credentials")
        if parts.query or parts.fragment:
            raise ValueError("APP_PUBLIC_URL must not contain a query or fragment")
        if parts.path not in {"", "/"}:
            raise ValueError("APP_PUBLIC_URL must not contain a path")
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}"

    @field_validator("backup_dir", mode="before")
    @classmethod
    def _optional_backup_path(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            return Path(text) if text else None
        return value

    @field_validator("data_dir", "web_dist_dir", mode="before")
    @classmethod
    def _resolve_relative(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value)
        return value

    @model_validator(mode="after")
    def _validate_https_boundary(self) -> Settings:
        """HTTP is only a loopback development exception; production is HTTPS."""
        http_origins = [o for o in self.allowed_origins if o.startswith("http://")]
        if http_origins:
            if not self.allow_loopback_http:
                raise ValueError("HTTP origins require ALLOW_LOOPBACK_HTTP=true")
            for origin in http_origins:
                host = (urlsplit(origin).hostname or "").lower()
                if host not in {"127.0.0.1", "localhost", "::1"}:
                    raise ValueError("HTTP origins are allowed only for loopback hosts")
            if self.auth_cookie_secure:
                raise ValueError("AUTH_COOKIE_SECURE must be false for loopback HTTP")
        if self.app_public_url:
            parts = urlsplit(self.app_public_url)
            if parts.scheme == "http":
                if not self.allow_loopback_http:
                    raise ValueError("APP_PUBLIC_URL may use HTTP only for loopback development")
                if (parts.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
                    raise ValueError("APP_PUBLIC_URL may use HTTP only for loopback hosts")
                if self.auth_cookie_secure:
                    raise ValueError("AUTH_COOKIE_SECURE must be false for loopback HTTP")
            public_host = (parts.hostname or "").lower()
            allowed = {host.lower() for host in self.allowed_hosts}
            allowed.add(self.host.lower())
            if public_host not in allowed:
                raise ValueError("APP_PUBLIC_URL host must be present in ALLOWED_HOSTS")
        return self

    @model_validator(mode="after")
    def _keep_public_build_outside_data_dir(self) -> Settings:
        """Build output is public; the data root is private. Never overlap them."""
        data_dir = self.resolved_data_dir().resolve()
        web_dist_dir = self.resolved_web_dist_dir().resolve()
        if (
            web_dist_dir == data_dir
            or web_dist_dir.is_relative_to(data_dir)
            or data_dir.is_relative_to(web_dist_dir)
        ):
            raise ValueError("WEB_DIST_DIR must be outside APP_DATA_DIR")
        backup_dir = self.resolved_backup_dir()
        if backup_dir is not None:
            resolved_backup = backup_dir.resolve()
            for private_root in (data_dir, web_dist_dir):
                if (
                    resolved_backup == private_root
                    or resolved_backup.is_relative_to(private_root)
                    or private_root.is_relative_to(resolved_backup)
                ):
                    raise ValueError("APP_BACKUP_DIR must be outside APP_DATA_DIR and WEB_DIST_DIR")
        return self

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

    def resolved_backup_dir(self) -> Path | None:
        if self.backup_dir is None:
            return None
        return _resolve_backend_path(self.backup_dir)

    def llm(self) -> RoleConfig:
        base_url = _blank(self.llm_base_url)
        api_key = (
            self.llm_api_key
            if self.llm_api_key and self.llm_api_key.get_secret_value().strip()
            else None
        )
        vision_api_key = (
            self.llm_vision_api_key
            if self.llm_vision_api_key
            and self.llm_vision_api_key.get_secret_value().strip()
            else api_key
        )
        return RoleConfig(
            role=ROLE_LLM,
            adapter_id=_blank(self.llm_adapter),
            base_url=base_url,
            api_key=api_key,
            model=_blank(self.llm_model),
            timeout_seconds=max(int(self.llm_timeout_seconds), 1),
            thinking_mode=self.llm_thinking_mode,
            reasoning_effort=self.llm_reasoning_effort,
            vision_reasoning_effort=self.llm_vision_reasoning_effort,
            image_size=None,
            vision_model=_blank(self.llm_vision_model),
            vision_base_url=_blank(self.llm_vision_base_url) or base_url,
            vision_api_key=vision_api_key,
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
    vision_model: str | None = None
    vision_base_url: str | None = None
    vision_api_key: SecretStr | None = None
    thinking_mode: Literal["auto", "enabled", "disabled"] = "auto"
    reasoning_effort: Literal["auto", "low", "medium", "high"] = "auto"
    vision_reasoning_effort: Literal["auto", "low", "medium", "high"] = "auto"

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
