from __future__ import annotations

from typing import Any


class BackupError(RuntimeError):
    """A maintenance backup failure with a stable, operator-facing code."""

    code = "BACKUP_ERROR"

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


class BackupIntegrityError(BackupError):
    """The staged or stored backup did not pass independent verification."""

    code = "BACKUP_INTEGRITY_FAILED"


__all__ = ["BackupError", "BackupIntegrityError"]
