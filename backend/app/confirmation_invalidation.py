"""Why a previously reviewed prompt snapshot can no longer authorize an image.

The frozen request is the server's semantic snapshot: requirements, prompt,
approval record, reference content and the effective output/service facts.
Any drift invalidates the old final command; the client must read the preview
again and explicitly confirm the new snapshot. Only presentation metadata such
as the project title is outside this snapshot.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, NoReturn

from app.errors import conflict


class InvalidationReason(StrEnum):
    SNAPSHOT_INTEGRITY = "snapshot_integrity"
    REQUIREMENT_REVISION = "requirement_revision"
    PROMPT_CONTENT = "prompt_content"
    APPROVAL_RECORD = "approval_record"
    REFERENCE_CONTENT = "reference_content"
    OUTPUT_PARAMETERS = "output_parameters"
    SERVICE_CONFIGURATION = "service_configuration"
    INTENT_BINDING = "intent_binding"


LABELS: dict[InvalidationReason, str] = {
    InvalidationReason.SNAPSHOT_INTEGRITY: "快照完整性",
    InvalidationReason.REQUIREMENT_REVISION: "需求版本",
    InvalidationReason.PROMPT_CONTENT: "提示词内容",
    InvalidationReason.APPROVAL_RECORD: "提示词批准记录",
    InvalidationReason.REFERENCE_CONTENT: "参考图片或版本",
    InvalidationReason.OUTPUT_PARAMETERS: "输出参数",
    InvalidationReason.SERVICE_CONFIGURATION: "服务配置",
    InvalidationReason.INTENT_BINDING: "准备意图",
}

_FIELD_REASONS: dict[str, InvalidationReason] = {
    "contract_version": InvalidationReason.PROMPT_CONTENT,
    "project_id": InvalidationReason.SNAPSHOT_INTEGRITY,
    "run_id": InvalidationReason.INTENT_BINDING,
    "intent_id": InvalidationReason.INTENT_BINDING,
    "owner_account_id": InvalidationReason.SNAPSHOT_INTEGRITY,
    "operator_account_id": InvalidationReason.SNAPSHOT_INTEGRITY,
    "project_revision": InvalidationReason.REQUIREMENT_REVISION,
    "requirement_revision": InvalidationReason.REQUIREMENT_REVISION,
    "requirements": InvalidationReason.REQUIREMENT_REVISION,
    "requirements_artifact_id": InvalidationReason.REQUIREMENT_REVISION,
    "requirements_digest": InvalidationReason.REQUIREMENT_REVISION,
    "requirements_approval_id": InvalidationReason.APPROVAL_RECORD,
    "execution_requirements": InvalidationReason.REQUIREMENT_REVISION,
    "positive_prompt": InvalidationReason.PROMPT_CONTENT,
    "negative_prompt": InvalidationReason.PROMPT_CONTENT,
    "prompt": InvalidationReason.PROMPT_CONTENT,
    "negative_strategy": InvalidationReason.PROMPT_CONTENT,
    "references": InvalidationReason.REFERENCE_CONTENT,
    "output": InvalidationReason.OUTPUT_PARAMETERS,
    "service": InvalidationReason.SERVICE_CONFIGURATION,
}


def invalidation_reasons(
    frozen: Mapping[str, Any], actual: Mapping[str, Any]
) -> tuple[InvalidationReason, ...]:
    """Classify every effective field that no longer matches the frozen snapshot."""

    reasons: list[InvalidationReason] = []
    # The frozen snapshot also carries prompt-artifact bookkeeping that the
    # rebuildable request does not repeat (validated separately by the caller).
    for field, value in actual.items():
        if field not in frozen or frozen[field] != value:
            reason = _FIELD_REASONS.get(field, InvalidationReason.SNAPSHOT_INTEGRITY)
            if reason not in reasons:
                reasons.append(reason)
    return tuple(reasons)


def raise_for_reasons(reasons: Iterable[InvalidationReason]) -> NoReturn:
    ordered: list[InvalidationReason] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    if not ordered:
        ordered = [InvalidationReason.SNAPSHOT_INTEGRITY]
    labels = "、".join(LABELS[reason] for reason in ordered)
    raise conflict(
        "PROMPT_STALE",
        f"提示词快照已失效（{labels}已变化），请重新读取预览并再次确认。",
    )


def require_snapshot_binding(snapshot: Any, frozen: Mapping[str, Any]) -> None:
    """The snapshot must belong to the same project/run/intent it was built for."""

    if (
        frozen.get("project_id") != snapshot.project_id
        or frozen.get("run_id") != snapshot.run_id
        or frozen.get("intent_id") != snapshot.intent_id
    ):
        raise_for_reasons((InvalidationReason.INTENT_BINDING,))
