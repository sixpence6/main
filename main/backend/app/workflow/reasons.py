from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class ReasonCode(StrEnum):
    """Typed workflow reasons.

    State transitions and authorization decisions read these codes, never
    localized user-facing error copy.
    """

    MISSING_INFORMATION = "missing_information"
    REPAIRABLE_COMPOSITION = "repairable_composition"
    TRUE_DRIFT = "true_drift"
    HARD_FAILURE = "hard_failure"
    CONTENT_REJECTION = "content_rejection"
    MISSING_EVIDENCE = "missing_evidence"
    PROTOCOL_ERROR = "protocol_error"
    TECHNICAL_FAILURE = "technical_failure"
    REMOTE_UNKNOWN = "remote_unknown"
    AUTHORIZATION_INVALID = "authorization_invalid"


class CandidateDefectCode(StrEnum):
    DUPLICATE_COVERAGE = "duplicate_coverage"
    UNKNOWN_COVERAGE = "unknown_coverage"
    BINDING_MISMATCH = "binding_mismatch"
    COVERAGE_MISMATCH = "coverage_mismatch"
    MISSING_COVERAGE = "missing_coverage"
    SIZE_MISMATCH = "size_mismatch"
    COUNT_MISMATCH = "count_mismatch"
    REFERENCE_MISMATCH = "reference_mismatch"
    CONTRACT_INVALID = "contract_invalid"


class CandidateDefect:
    """One typed defect found while validating a generated text candidate."""

    __slots__ = ("code", "message", "rule_id", "content_intact")

    def __init__(
        self,
        code: CandidateDefectCode,
        message: str,
        *,
        rule_id: str | None = None,
        content_intact: bool = True,
    ) -> None:
        self.code = code
        self.message = message
        self.rule_id = rule_id
        self.content_intact = content_intact

    def __repr__(self) -> str:
        return (
            f"CandidateDefect(code={self.code!r}, rule_id={self.rule_id!r}, "
            f"content_intact={self.content_intact!r})"
        )


_HARD_BOUNDARY_DEFECTS = frozenset(
    {
        CandidateDefectCode.SIZE_MISMATCH,
        CandidateDefectCode.COUNT_MISMATCH,
        CandidateDefectCode.REFERENCE_MISMATCH,
    }
)
_METADATA_DEFECTS = frozenset(
    {
        CandidateDefectCode.DUPLICATE_COVERAGE,
        CandidateDefectCode.UNKNOWN_COVERAGE,
        CandidateDefectCode.BINDING_MISMATCH,
    }
)
_AUTO_REPAIRABLE_DEFECTS = frozenset(
    {CandidateDefectCode.COVERAGE_MISMATCH, CandidateDefectCode.MISSING_COVERAGE}
)


def classify_candidate_defects(defects: Iterable[CandidateDefect]) -> ReasonCode | None:
    """Classify a non-empty candidate defect list into one typed reason."""
    items = list(defects)
    if not items:
        return None
    if any(item.code == CandidateDefectCode.CONTRACT_INVALID for item in items):
        return ReasonCode.PROTOCOL_ERROR
    if any(item.code in _HARD_BOUNDARY_DEFECTS or not item.content_intact for item in items):
        return ReasonCode.TRUE_DRIFT
    if any(item.code in _AUTO_REPAIRABLE_DEFECTS for item in items):
        return ReasonCode.REPAIRABLE_COMPOSITION
    if any(item.code in _METADATA_DEFECTS for item in items):
        return ReasonCode.REPAIRABLE_COMPOSITION
    return ReasonCode.REPAIRABLE_COMPOSITION


def is_auto_repairable(defects: Iterable[CandidateDefect]) -> bool:
    """Only a coverage defect with proven intact content is auto-repairable."""
    items = list(defects)
    return bool(items) and all(
        item.code in _AUTO_REPAIRABLE_DEFECTS and item.content_intact for item in items
    )


def repair_codes_from_defects(defects: Iterable[CandidateDefect]) -> tuple[str, ...]:
    codes: list[str] = []
    for item in defects:
        value = item.rule_id or str(item.code)
        if value not in codes:
            codes.append(value)
    return tuple(codes)


def classify_review_decision(decision: str) -> ReasonCode | None:
    if decision == "pass":
        return None
    if decision == "revise":
        return ReasonCode.REPAIRABLE_COMPOSITION
    if decision == "reject":
        return ReasonCode.CONTENT_REJECTION
    if decision == "unknown":
        return ReasonCode.MISSING_EVIDENCE
    return ReasonCode.PROTOCOL_ERROR


def classify_issue_code(code: str | None) -> ReasonCode:
    if not code:
        return ReasonCode.MISSING_INFORMATION
    if code.startswith("G02_") and "REJECT" in code:
        return ReasonCode.CONTENT_REJECTION
    if code in {
        "REVIEW_REJECTED",
        "IMAGE_NOT_APPROVED",
        "SAFETY_REFUSAL",
        "SCOPE_REFUSAL",
        "UNSAFE_REQUEST",
        "OUT_OF_SCOPE",
        "MIXED_SCOPE",
    }:
        return ReasonCode.CONTENT_REJECTION
    if code == "REVIEW_REVISE":
        return ReasonCode.REPAIRABLE_COMPOSITION
    if code in {
        "MISSING_INPUT",
        "CONFLICTING_INPUT",
        "MISSING_INFORMATION",
    }:
        return ReasonCode.MISSING_INFORMATION
    if code in {
        "MISSING_EVIDENCE",
        "UNREADABLE_IMAGE",
        "HARD_REQUIREMENT_UNKNOWN",
        "CANDIDATE_EXPIRED",
        "CANDIDATE_UNAVAILABLE",
        "LEGACY_UNREVIEWED",
        "REVIEW_UNKNOWN",
    }:
        return ReasonCode.MISSING_EVIDENCE
    if code in {
        "REQUIREMENT_DRIFT",
        "PROMPT_REQUIRES_RECONFIRMATION",
        "CONFIRM_REQUIRED",
        "RUN_SUPERSEDED",
    }:
        return ReasonCode.TRUE_DRIFT
    if code == "HARD_REQUIREMENT_FAILED":
        return ReasonCode.HARD_FAILURE
    if code in {
        "CONTRACT_ERROR",
        "PROMPT_REVIEW_INVALID",
        "REVIEW_INVALID",
        "REVIEW_CONFIRM_INVALID",
        "ACTION_NOT_AVAILABLE",
    }:
        return ReasonCode.PROTOCOL_ERROR
    if code in {
        "GENERATION_UNKNOWN",
        "REMOTE_UNKNOWN",
        "RESULT_UNKNOWN",
        "RESULT_RETRIEVAL_PENDING",
        "LOCAL_TRACKING_CLOSED",
    }:
        return ReasonCode.REMOTE_UNKNOWN
    if code in {
        "SERVICE_UNAVAILABLE",
        "CAPABILITY_NOT_READY",
        "TECHNICAL_UNAVAILABLE",
        "REVIEW_UNAVAILABLE",
        "PROCESS_INTERRUPTED",
    }:
        return ReasonCode.TECHNICAL_FAILURE
    if code in {
        "ASSET_UNAVAILABLE",
        "ACCOUNT_DISABLED",
        "ACCOUNT_INACTIVE",
        "UNAUTHORIZED_ACTION",
    }:
        return ReasonCode.AUTHORIZATION_INVALID
    return ReasonCode.MISSING_INFORMATION

def classify_api_error(code: str | None) -> ReasonCode:
    if code in {
        "SERVICE_UNAVAILABLE",
        "CAPABILITY_NOT_READY",
        "REVIEW_UNAVAILABLE",
        "TECHNICAL_UNAVAILABLE",
        "LLM_TIMEOUT",
    }:
        return ReasonCode.TECHNICAL_FAILURE
    if code in {
        "PROMPT_REVIEW_INVALID",
        "REVIEW_INVALID",
        "CONTRACT_ERROR",
        "ACTION_NOT_AVAILABLE",
    }:
        return ReasonCode.PROTOCOL_ERROR
    if code in {"GENERATION_UNKNOWN", "REMOTE_UNKNOWN", "RESULT_UNKNOWN"}:
        return ReasonCode.REMOTE_UNKNOWN
    if code in {
        "REQUIREMENT_DRIFT",
        "PROMPT_REQUIRES_RECONFIRMATION",
        "CONFIRM_REQUIRED",
    }:
        return ReasonCode.TRUE_DRIFT
    if code == "HARD_REQUIREMENT_FAILED":
        return ReasonCode.HARD_FAILURE
    if code in {"ACCOUNT_DISABLED", "ACCOUNT_INACTIVE", "UNAUTHORIZED_ACTION"}:
        return ReasonCode.AUTHORIZATION_INVALID
    if code in {"IMAGE_NOT_APPROVED", "DIGEST_MISMATCH", "ASSET_UNAVAILABLE"}:
        return ReasonCode.MISSING_EVIDENCE
    return ReasonCode.MISSING_INFORMATION
