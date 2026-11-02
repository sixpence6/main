from __future__ import annotations

import copy
from typing import Any

from app.workflow.contracts import digest_payload

# Frozen compatibility surface for T10. Changing any of these values requires a
# new snapshot/contract version and an explicit compatibility test; existing
# confirmations must never be reinterpreted under a newer shape.
EXECUTION_SNAPSHOT_SCHEMA_VERSION = "1.0.0"
EXECUTION_CONTRACT_VERSION = "1.0.0"
EXECUTION_SNAPSHOT_ENCODING = "canonical-json-sha256-v1"
SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS = frozenset({EXECUTION_SNAPSHOT_SCHEMA_VERSION})
SUPPORTED_CONTRACT_VERSIONS = frozenset({EXECUTION_CONTRACT_VERSION})

# These keys belong to the surrounding legacy confirmation envelope. They are
# deliberately outside the canonical execution body so the digest stays equal
# whether the snapshot is built standalone or embedded in Approval.checks.
_LEGACY_ENVELOPE_KEYS = frozenset({"schema_version", "confirmed_by", "confirmed_at"})

_RULES_VERSION = "v3.rules.1"

_FIELD_ORDER = (
    "product_description",
    "purpose",
    "facts",
    "creative_direction",
    "style_keywords",
    "composition",
    "lighting",
    "background",
    "palette",
    "text_layout",
    "reference_treatment",
    "texts",
    "must_include",
    "must_preserve",
    "preservation_rules",
    "forbidden",
    "size",
    "reference_asset_id",
    "reference_usage",
    "count",
    "assumptions",
    "unresolved",
    "selling_points",
    "copy_lines",
)

_LEGACY_HARD_FIELDS = frozenset(
    {
        "product_description",
        "purpose",
        "facts",
        "creative_direction",
        "style_keywords",
        "composition",
        "lighting",
        "texts",
        "must_include",
        "must_preserve",
        "forbidden",
        "size",
        "reference_asset_id",
        "reference_usage",
        "count",
    }
)
_NON_EXECUTABLE_REASONS = {
    "assumptions": "假设仅用于说明批准依据，不单独进入执行提示词。",
    "unresolved": "未决问题不是执行指令；存在未决问题时确认本身被阻止。",
    "selling_points": "创意卖点用于审查背景，不单独成为执行约束。",
}

_RULE_ORDER = {
    "hard:subject": 0,
    "hard:purpose": 1,
    "hard:creative": 2,
    "hard:style": 3,
    "hard:composition": 4,
    "hard:lighting": 5,
    "hard:text": 6,
    "hard:fact": 7,
    "hard:must_include": 8,
    "hard:must_preserve": 9,
    "hard:forbidden": 10,
    "hard:size": 11,
    "hard:count": 12,
    "hard:reference_asset_id": 13,
}


class SnapshotCompatibilityError(ValueError):
    """A confirmation snapshot is missing or was written by an unknown version."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _source(brief: dict[str, Any], path: str) -> str:
    sources = brief.get("field_sources")
    if isinstance(sources, dict) and isinstance(sources.get(path), str) and sources[path]:
        return sources[path]
    if path in {
        "background",
        "palette",
        "text_layout",
        "reference_treatment",
        "preservation_rules",
    }:
        return "visual_plan"
    if path in {"creative_direction", "selling_points", "copy_lines"}:
        return "creative_direction"
    if path == "count":
        return "contract"
    return "brief_draft"


def _user_lock(brief: dict[str, Any], path: str) -> str | None:
    locks = brief.get("user_locks")
    if not isinstance(locks, dict):
        return None
    value = locks.get(path)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strength(brief: dict[str, Any], path: str) -> str:
    if _user_lock(brief, path):
        return "hard"
    # The pre-T10 fields keep their historical blocking semantics. Only the
    # newly introduced dedicated visual fields default to soft guidance, so an
    # old confirmed card is never silently weakened or strengthened.
    return "hard" if path in _LEGACY_HARD_FIELDS else "soft"


def _normal_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
    return str(value)


def _constraint(
    *,
    brief: dict[str, Any],
    path: str,
    index: int | None,
    original_value: Any,
    normalized_value: Any,
    destination: str,
    binding: str | None,
    rule_id: str | None,
    note: str | None = None,
) -> dict[str, Any]:
    suffix = "" if index is None else f":{index}"
    lock_basis = _user_lock(brief, path)
    return {
        "constraint_id": f"req:{path}{suffix}",
        "path": path,
        "index": index,
        "original_value": copy.deepcopy(original_value),
        "normalized_value": normalized_value,
        "source": _source(brief, path),
        "strength": _strength(brief, path),
        "user_lock_basis": lock_basis,
        "applicability": "applicable",
        "destination": destination,
        "binding": binding,
        "rule_id": rule_id,
        "mapping_note": note,
    }


def _field(
    *,
    brief: dict[str, Any],
    path: str,
    value: Any,
    executable: bool,
    destination: str,
    reason: str | None,
    constraint_ids: list[str],
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "value": copy.deepcopy(value),
        "original_value": copy.deepcopy(value),
        "source": _source(brief, path),
        "executable": executable,
        "destination": destination,
        "reason": reason,
        "strength": _strength(brief, path) if executable else "not_applicable",
        "user_lock_basis": _user_lock(brief, path) if executable else None,
        "applicability": "applicable" if executable else "not_applicable",
        "constraint_id": constraint_ids[0] if constraint_ids else None,
        "constraint_ids": constraint_ids,
        "mapping_note": note,
    }


def _build_fields(brief: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fields: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []

    def add_scalar(
        path: str,
        destination: str,
        binding: str | None,
        rule_id: str | None,
        *,
        default: Any = None,
        note: str | None = None,
    ) -> None:
        value = brief.get(path, default)
        executable = value not in (None, "", [])
        if not executable:
            fields.append(
                _field(
                    brief=brief,
                    path=path,
                    value=value,
                    executable=False,
                    destination="non_executable",
                    reason="字段未提供，不进入执行。",
                    constraint_ids=[],
                )
            )
            return
        constraint = _constraint(
            brief=brief,
            path=path,
            index=None,
            original_value=value,
            normalized_value=_normal_text(value),
            destination=destination,
            binding=binding,
            rule_id=rule_id,
            note=note,
        )
        constraints.append(constraint)
        fields.append(
            _field(
                brief=brief,
                path=path,
                value=value,
                executable=True,
                destination=destination,
                reason=None,
                constraint_ids=[constraint["constraint_id"]],
                note=note,
            )
        )

    def add_list(
        path: str,
        destination: str,
        binding: str | None,
        rule_prefix: str | None,
        *,
        note: str | None = None,
    ) -> None:
        values = brief.get(path) or []
        if not isinstance(values, list):
            values = [values]
        ids: list[str] = []
        if not values:
            fields.append(
                _field(
                    brief=brief,
                    path=path,
                    value=values,
                    executable=False,
                    destination="non_executable",
                    reason="字段为空，没有需要执行的条目。",
                    constraint_ids=[],
                )
            )
            return
        for index, value in enumerate(values):
            rule_id = f"{rule_prefix}:{index}" if rule_prefix else None
            constraint = _constraint(
                brief=brief,
                path=path,
                index=index,
                original_value=value,
                normalized_value=_normal_text(value),
                destination=destination,
                binding=binding,
                rule_id=rule_id,
                note=note,
            )
            constraints.append(constraint)
            ids.append(constraint["constraint_id"])
        fields.append(
            _field(
                brief=brief,
                path=path,
                value=values,
                executable=True,
                destination=destination,
                reason=None,
                constraint_ids=ids,
                note=note,
            )
        )

    add_scalar("product_description", "positive_prompt", "positive_prompt", "hard:subject")
    add_scalar("purpose", "positive_prompt", "positive_prompt", "hard:purpose")
    add_list("facts", "positive_prompt", "positive_prompt", "hard:fact")
    add_scalar("creative_direction", "positive_prompt", "positive_prompt", "hard:creative")
    add_scalar("style_keywords", "positive_prompt", "positive_prompt", "hard:style")
    for constraint in constraints:
        if constraint["path"] == "style_keywords" and constraint["index"] is None:
            values = brief.get("style_keywords") or []
            constraint["normalized_value"] = "、".join(str(item) for item in values)
    add_scalar("composition", "positive_prompt", "positive_prompt", "hard:composition")
    add_scalar("lighting", "positive_prompt", "positive_prompt", "hard:lighting")
    add_scalar("background", "positive_prompt", "positive_prompt", None)
    add_list("palette", "positive_prompt", "positive_prompt", None)
    add_scalar("text_layout", "positive_prompt", "positive_prompt", None)
    add_scalar("reference_treatment", "positive_prompt", "positive_prompt", None)
    add_list("texts", "positive_prompt", "positive_prompt", "hard:text")
    add_list("must_include", "positive_prompt", "positive_prompt", "hard:must_include")
    add_list("must_preserve", "positive_prompt", "positive_prompt", "hard:must_preserve")
    add_list(
        "preservation_rules",
        "positive_prompt",
        "positive_prompt",
        None,
        note="保留规则已并入 must_preserve 执行。",
    )
    add_list("forbidden", "negative_prompt", "negative_prompt", "hard:forbidden")
    add_scalar("size", "size", "size", "hard:size")

    reference_id = brief.get("reference_asset_id")
    add_scalar("reference_asset_id", "reference", "reference_asset_id", "hard:reference_asset_id")
    reference_usage = brief.get("reference_usage")
    if reference_id is None and reference_usage == "none":
        for item in fields:
            if item["path"] == "reference_asset_id":
                item["reason"] = "未绑定参考图，参考图不是本版本的执行输入。"
    add_scalar("reference_usage", "reference", "reference_asset_id", None)
    if reference_usage == "none":
        for item in fields:
            if item["path"] == "reference_usage":
                item["reason"] = "未使用参考图，不产生参考图约束。"

    count_constraint = _constraint(
        brief=brief,
        path="count",
        index=None,
        original_value=1,
        normalized_value="1",
        destination="count",
        binding="count",
        rule_id="hard:count",
    )
    constraints.append(count_constraint)
    fields.append(
        _field(
            brief=brief,
            path="count",
            value=1,
            executable=True,
            destination="count",
            reason=None,
            constraint_ids=[count_constraint["constraint_id"]],
        )
    )

    for path in ("assumptions", "unresolved"):
        values = brief.get(path) or []
        fields.append(
            _field(
                brief=brief,
                path=path,
                value=values,
                executable=False,
                destination="non_executable",
                reason=_NON_EXECUTABLE_REASONS[path],
                constraint_ids=[],
            )
        )
    selling_points = brief.get("selling_points") or []
    fields.append(
        _field(
            brief=brief,
            path="selling_points",
            value=selling_points,
            executable=False,
            destination="qc_only",
            reason=_NON_EXECUTABLE_REASONS["selling_points"],
            constraint_ids=[],
        )
    )
    add_list(
        "copy_lines",
        "positive_prompt",
        "positive_prompt",
        None,
        note="文案已并入 texts 执行。",
    )

    fields.sort(key=lambda item: _FIELD_ORDER.index(item["path"]))
    return fields, constraints


def _rule_sort_key(constraint: dict[str, Any]) -> tuple[int, int]:
    rule_id = constraint.get("rule_id")
    if not rule_id:
        return (99, 0)
    prefix, _, suffix = rule_id.rpartition(":")
    rank = _RULE_ORDER[prefix] if prefix in _RULE_ORDER else _RULE_ORDER.get(rule_id, 99)
    try:
        index = int(suffix)
    except ValueError:
        index = 0
    return (rank, index)


def _hard_requirements_from_constraints(
    constraints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for constraint in sorted(constraints, key=_rule_sort_key):
        if constraint.get("strength") != "hard" or not constraint.get("rule_id"):
            continue
        requirements.append(
            {
                "rule_id": constraint["rule_id"],
                "requirement": constraint["normalized_value"],
                "allowed_bindings": [constraint["binding"]],
            }
        )
    return requirements


def build_execution_snapshot(
    *,
    artifact_id: str,
    artifact_digest: str,
    artifact_revision_no: int,
    project_revision: int,
    requirement_revision: int,
    reference_asset_id: str | None,
    reference_usage: str,
    referenced_material_digest: str,
    brief: dict[str, Any],
    hard_requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fields, constraints = _build_fields(brief)
    body: dict[str, Any] = {
        "snapshot_schema_version": EXECUTION_SNAPSHOT_SCHEMA_VERSION,
        "contract_version": EXECUTION_CONTRACT_VERSION,
        "rules_version": _RULES_VERSION,
        "snapshot_encoding": EXECUTION_SNAPSHOT_ENCODING,
        "artifact_id": artifact_id,
        "artifact_digest": artifact_digest,
        "artifact_revision_no": artifact_revision_no,
        "project_revision": project_revision,
        "requirement_revision": requirement_revision,
        "reference_asset_id": reference_asset_id,
        "reference_usage": reference_usage,
        "referenced_material_digest": referenced_material_digest,
        "brief": copy.deepcopy(brief),
        "fields": fields,
        "constraints": constraints,
    }
    body["hard_requirements"] = (
        copy.deepcopy(hard_requirements)
        if hard_requirements is not None
        else _hard_requirements_from_constraints(constraints)
    )
    body["snapshot_digest"] = digest_payload(body)
    return body


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    body = {
        key: value
        for key, value in snapshot.items()
        if key != "snapshot_digest" and key not in _LEGACY_ENVELOPE_KEYS
    }
    return digest_payload(body)


def validate_execution_snapshot(snapshot: dict[str, Any]) -> None:
    if not isinstance(snapshot, dict):
        raise SnapshotCompatibilityError("SNAPSHOT_INVALID", "确认快照格式无效。")
    schema_version = snapshot.get("snapshot_schema_version")
    if schema_version is None:
        raise SnapshotCompatibilityError(
            "SNAPSHOT_VERSION_MISSING", "确认快照缺少快照版本，不能按当前格式解释。"
        )
    if schema_version not in SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS:
        raise SnapshotCompatibilityError(
            "SNAPSHOT_VERSION_UNSUPPORTED",
            f"不支持的确认快照版本：{schema_version}。",
        )
    contract_version = snapshot.get("contract_version")
    if contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        raise SnapshotCompatibilityError(
            "CONTRACT_VERSION_UNSUPPORTED",
            f"不支持的执行契约版本：{contract_version}。",
        )
    if snapshot.get("snapshot_encoding") != EXECUTION_SNAPSHOT_ENCODING:
        raise SnapshotCompatibilityError(
            "SNAPSHOT_ENCODING_UNSUPPORTED", "确认快照编码不受支持。"
        )
    stored = snapshot.get("snapshot_digest")
    if not isinstance(stored, str) or len(stored) != 64:
        raise SnapshotCompatibilityError("SNAPSHOT_DIGEST_MISSING", "确认快照摘要缺失。")
    if snapshot_digest(snapshot) != stored:
        raise SnapshotCompatibilityError("SNAPSHOT_DIGEST_MISMATCH", "确认快照摘要不一致。")
    fields = snapshot.get("fields")
    if not isinstance(fields, list):
        raise SnapshotCompatibilityError("SNAPSHOT_FIELDS_MISSING", "确认快照字段映射缺失。")
    for item in fields:
        if not isinstance(item, dict) or not item.get("path"):
            raise SnapshotCompatibilityError("SNAPSHOT_FIELDS_INVALID", "确认快照字段映射无效。")
        if item.get("executable") and not item.get("destination"):
            raise SnapshotCompatibilityError("SNAPSHOT_DESTINATION_MISSING", "执行字段缺少去向。")
        if not item.get("executable") and not item.get("reason"):
            raise SnapshotCompatibilityError("SNAPSHOT_REASON_MISSING", "非执行字段缺少理由。")
    if not isinstance(snapshot.get("constraints"), list):
        raise SnapshotCompatibilityError("SNAPSHOT_CONSTRAINTS_MISSING", "确认快照约束缺失。")


def execution_snapshot_from_checks(checks: object) -> dict[str, Any] | None:
    """Return the frozen execution snapshot from an approval checks blob.

    None means the approval predates execution snapshots. It is an explicit
    legacy result, never a silent substitute built from current project rows.
    """
    if not isinstance(checks, dict):
        return None
    confirmation = checks.get("confirmation")
    if not isinstance(confirmation, dict):
        return None
    if "snapshot_schema_version" not in confirmation:
        return None
    validate_execution_snapshot(confirmation)
    return confirmation


def requirements_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return S07-shaped requirements from a frozen execution snapshot."""
    constraints = snapshot.get("constraints")
    if isinstance(constraints, list) and constraints:
        return _hard_requirements_from_constraints(constraints)
    legacy = snapshot.get("hard_requirements")
    if isinstance(legacy, list):
        return [
            {
                "rule_id": item.get("rule_id", item.get("id")),
                "requirement": item.get("requirement", item.get("value")),
                "allowed_bindings": [item.get("applies_to")],
            }
            for item in legacy
            if isinstance(item, dict) and item.get("applies_to")
        ]
    return []
