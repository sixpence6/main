from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError


class ContractError(ValueError):
    """A role output that does not match the v2.0.0 contract."""


def _unique_ids(value: list[str]) -> list[str]:
    if len(value) != len(set(value)):
        raise ValueError("evidence_ids must be unique")
    return value


Id = Annotated[str, Field(min_length=1, max_length=160)]
EvidenceIds = Annotated[list[Id], Field(max_length=64), AfterValidator(_unique_ids)]
IssueCode = Literal[
    "MISSING_INPUT", "CONFLICTING_INPUT", "UNREADABLE_IMAGE", "MISSING_EVIDENCE",
    "OUT_OF_SCOPE", "MIXED_SCOPE", "UNSAFE_REQUEST", "UNAUTHORIZED_ACTION",
    "CONTRACT_ERROR", "REQUIREMENT_DRIFT", "HARD_REQUIREMENT_FAILED",
    "TECHNICAL_UNAVAILABLE",
]
Outcome = Literal["pass", "fail", "unknown", "not_applicable"]
Decision = Literal["pass", "revise", "reject", "unknown"]
Binding = Literal["positive_prompt", "negative_prompt", "size", "count", "reference_asset_id"]
Usage = Literal["none", "product", "style", "composition"]
Size = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,4}x[1-9][0-9]{0,4}$", max_length=11)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Issue(StrictModel):
    code: IssueCode
    path: str = Field(max_length=256)
    detail: str = Field(min_length=1, max_length=800)
    evidence_ids: EvidenceIds


class Check(StrictModel):
    rule_id: Id
    outcome: Outcome
    evidence_ids: EvidenceIds
    note: str = Field(max_length=800)
    # Optional stable identity for a rule that is checked more than once (for
    # example per text item). Empty means the catalogue has one instance.
    instance: str = Field(default="", max_length=160)


class Fact(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    basis: Literal["user_declared", "image_observed"]
    evidence_ids: Annotated[list[Id], Field(min_length=1, max_length=64)]


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: Annotated[list[Id], Field(min_length=1, max_length=64)]


class Observation(StrictModel):
    kind: Literal["product", "color", "shape", "texture", "logo", "quantity", "composition"]
    description: str = Field(min_length=1, max_length=2000)
    confidence: Literal["clear", "uncertain"]
    evidence_ids: Annotated[list[Id], Field(min_length=1, max_length=64)]


class ImageText(StrictModel):
    text: str = Field(max_length=2000)
    legibility: Literal["clear", "unclear"]
    evidence_ids: Annotated[list[Id], Field(min_length=1, max_length=64)]


class Coverage(StrictModel):
    rule_id: Id
    binding: Binding
    value: str = Field(min_length=1, max_length=12000)


class TargetReview(StrictModel):
    target_id: Id
    decision: Decision
    checks: Annotated[list[Check], Field(min_length=1, max_length=256)]


class IntentData(StrictModel):
    route: Literal["image_request", "workflow_action", "out_of_scope", "mixed", "unclear"]
    action: Literal[
        "new_request", "edit_request", "feedback", "confirm_brief", "submit_generation",
        "upload_help", "status_query", "download", "none",
    ]
    evidence_ids: EvidenceIds


class ReferenceData(StrictModel):
    reference_usage: Usage
    observations: Annotated[list[Observation], Field(max_length=128)]
    texts: Annotated[list[ImageText], Field(max_length=128)]
    uncertainties: Annotated[list[str], Field(max_length=64)]


class BriefData(StrictModel):
    product_description: str | None = Field(max_length=3000)
    purpose: str | None = Field(max_length=2000)
    facts: Annotated[list[Fact], Field(max_length=128)]
    creative_direction: str | None = Field(max_length=2000)
    style_keywords: Annotated[list[str], Field(max_length=32)]
    composition: str | None = Field(max_length=2000)
    lighting: str | None = Field(max_length=2000)
    texts: Annotated[list[str], Field(max_length=64)]
    must_include: Annotated[list[str], Field(max_length=128)]
    must_preserve: Annotated[list[str], Field(max_length=128)]
    forbidden: Annotated[list[str], Field(max_length=128)]
    size: Size
    reference_asset_id: Id | None
    reference_usage: Usage
    assumptions: Annotated[list[str], Field(max_length=64)]
    unresolved: Annotated[list[str], Field(max_length=64)]


class FinalBriefData(BriefData):
    """Approved execution brief.

    The base brief fields are the historical v2 contract. T10 adds the
    dedicated visual and creative fields that previously existed only in the
    approved visual_plan/creative_direction artifacts, plus source and lock
    metadata needed to build an immutable execution snapshot without
    consulting current project rows.
    """

    background: str | None = Field(default=None, max_length=2000)
    palette: Annotated[list[str], Field(max_length=32)] = Field(default_factory=list)
    text_layout: str | None = Field(default=None, max_length=2000)
    reference_treatment: str | None = Field(default=None, max_length=2000)
    preservation_rules: Annotated[list[str], Field(max_length=128)] = Field(default_factory=list)
    selling_points: Annotated[list[Claim], Field(max_length=64)] = Field(default_factory=list)
    copy_lines: Annotated[list[str], Field(max_length=64)] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
    field_strengths: dict[str, str] = Field(default_factory=dict)
    user_locks: dict[str, str] = Field(default_factory=dict)


class CreativeData(StrictModel):
    direction: str = Field(min_length=1, max_length=3000)
    selling_points: Annotated[list[Claim], Field(max_length=64)]
    copy_lines: Annotated[list[str], Field(max_length=64)]


class VisualData(StrictModel):
    style_keywords: Annotated[list[str], Field(max_length=32)]
    composition: str = Field(min_length=1, max_length=3000)
    lighting: str = Field(min_length=1, max_length=2000)
    background: str = Field(min_length=1, max_length=2000)
    palette: Annotated[list[str], Field(max_length=32)]
    text_layout: str = Field(min_length=1, max_length=2000)
    reference_treatment: str = Field(min_length=1, max_length=2000)
    preservation_rules: Annotated[list[str], Field(max_length=128)]


class GenerationData(StrictModel):
    positive_prompt: str = Field(min_length=1, max_length=12000)
    negative_prompt: str = Field(max_length=6000)
    size: Size
    count: Literal[1]
    reference_asset_id: Id | None
    requirement_coverage: Annotated[list[Coverage], Field(min_length=1, max_length=256)]


class QualityData(StrictModel):
    asset_id: Id
    decision: Literal["pass", "fail", "unknown"]
    checks: Annotated[list[Check], Field(min_length=1, max_length=256)]
    soft_feedback: Annotated[list[str], Field(max_length=64)]
    limitations: Annotated[list[str], Field(max_length=64)]
    user_summary: str = Field(max_length=800)


class DeliveryData(StrictModel):
    asset_id: Id
    requirement_revision: int = Field(ge=1)
    filename_hint: str = Field(min_length=1, max_length=120)
    caption: str = Field(max_length=800)


class ReviewData(StrictModel):
    results: Annotated[list[TargetReview], Field(min_length=1, max_length=16)]


class _Envelope(StrictModel):
    data: object | None
    issues: Annotated[list[Issue], Field(max_length=64)]
    questions: Annotated[list[str], Field(max_length=8)]


def _envelope(role: str, statuses: tuple[str, ...], data_type: type[BaseModel]):
    return type(
        role.title().replace("_", "") + "Envelope",
        (BaseModel,),
        {
            "model_config": ConfigDict(extra="forbid"),
            "__annotations__": {
                "role": Literal[role],  # type: ignore[valid-type]
                "status": Literal[statuses],  # type: ignore[valid-type]
                "data": data_type | None,
                "issues": Annotated[list[Issue], Field(max_length=64)],
                "questions": Annotated[list[str], Field(max_length=8)],
            },
        },
    )


IntentEnvelope = _envelope("intent_router", ("ready", "blocked"), IntentData)
ReferenceEnvelope = _envelope("reference_analyst", ("ready", "blocked"), ReferenceData)
RequirementsEnvelope = _envelope("requirements_consultant", ("ready", "blocked", "needs_input"), BriefData)
CreativeEnvelope = _envelope("creative_planner", ("ready", "blocked", "needs_input"), CreativeData)
VisualEnvelope = _envelope("visual_director", ("ready", "blocked", "needs_input"), VisualData)
GenerationEnvelope = _envelope("generation_engineer", ("ready", "blocked", "needs_input"), GenerationData)
QualityEnvelope = _envelope("quality_compliance", ("ready", "blocked"), QualityData)
DeliveryEnvelope = _envelope("delivery_manager", ("ready", "blocked"), DeliveryData)
ReviewEnvelope = _envelope("output_reviewer", ("ready", "blocked"), ReviewData)

ENVELOPES: dict[str, type[BaseModel]] = {
    "intent_router": IntentEnvelope,
    "reference_analyst": ReferenceEnvelope,
    "requirements_consultant": RequirementsEnvelope,
    "creative_planner": CreativeEnvelope,
    "visual_director": VisualEnvelope,
    "generation_engineer": GenerationEnvelope,
    "quality_compliance": QualityEnvelope,
    "delivery_manager": DeliveryEnvelope,
    "output_reviewer": ReviewEnvelope,
}

ARTIFACT_DATA: dict[str, type[BaseModel]] = {
    "reference_report": ReferenceData,
    "brief_draft": BriefData,
    "creative_direction": CreativeData,
    "visual_plan": VisualData,
    "final_brief": FinalBriefData,
    "prompt_plan": GenerationData,
    "quality_report": QualityData,
    "quality_recheck": QualityData,
    "delivery_manifest": DeliveryData,
}

NEEDS_INPUT_ROLES = {"requirements_consultant", "creative_planner", "visual_director", "generation_engineer"}


def _validate_envelope(role: str, obj: object) -> BaseModel:
    try:
        model = ENVELOPES[role].model_validate(obj)
    except ValidationError as exc:
        raise ContractError("角色输出不符合 v2.0.0 契约") from exc
    if getattr(model, "role", None) != role:
        raise ContractError("角色字段与当前角色不一致")
    status = model.status
    issues = model.issues
    questions = model.questions
    data = model.data
    if status == "ready":
        if data is None or issues or questions:
            raise ContractError("ready 必须包含 data 且 issues/questions 为空")
    elif status == "blocked":
        if data is not None or not issues or questions:
            raise ContractError("blocked 必须 data 为空、至少一个 issue、questions 为空")
    elif status == "needs_input":
        if role not in NEEDS_INPUT_ROLES:
            raise ContractError("当前角色不允许 needs_input")
        if not issues:
            raise ContractError("needs_input 必须至少一个 issue")
        if role != "requirements_consultant":
            if data is not None or questions:
                raise ContractError("该角色 needs_input 必须 data 为空且 questions 为空")
        elif not questions:
            raise ContractError("需求顾问的 needs_input 必须提出至少一个澄清问题")
    else:
        raise ContractError("未知状态")
    return model


def validate_role_output(role: str, raw: object) -> BaseModel:
    if role not in ENVELOPES:
        raise ContractError("未知角色")
    return _validate_envelope(role, raw)


def validate_data(kind: str, payload: object) -> BaseModel:
    model = ARTIFACT_DATA.get(kind)
    if model is None:
        raise ContractError("未知产物类型")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ContractError("产物内容不符合 v2.0.0 契约") from exc


def digest_payload(payload: object) -> str:
    import hashlib
    import json

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
