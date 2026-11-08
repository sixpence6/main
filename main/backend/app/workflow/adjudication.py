"""Server-owned rule catalog and the single effective adjudication path.

Every gate answers the same two questions: which rules are required for a
target, and whether a reviewer result actually satisfies them. The catalog owns
the first answer; this module owns the second. No stage re-derives either from
natural language, from message text or from a rule-name pattern.

Only two rule kinds exist. A blocking rule must pass with legal evidence for
every required instance before a gate opens. A registered advisory rule is
recorded and never changes a blocking gate that is already satisfied.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Final, Literal

RULESET_VERSION = "v3.rules.1"

SUPPORTED_RULESET_VERSIONS: Final[frozenset[str]] = frozenset({RULESET_VERSION})
_MAX_PERSISTED_RULES = 256

GateMode = Literal["strict", "requirements_only", "off"]
_GATE_MODE: ContextVar[GateMode] = ContextVar("workflow_gate_mode", default="strict")


def set_gate_mode(mode: str) -> None:
    """Set the request/task-local gate policy before workflow work starts."""
    if mode not in {"strict", "requirements_only", "off"}:
        raise ValueError(f"unknown workflow gate mode: {mode}")
    _GATE_MODE.set(mode)  # type: ignore[arg-type]


def current_gate_mode() -> GateMode:
    return _GATE_MODE.get()


def gate_mode_for_target(target_kind: str) -> GateMode:
    """Return the effective mode for one target kind.

    The requirements stage keeps its independent review in every mode: its
    output is immediately shown to the user and must not be garbled or
    ungrounded. Other rule catalogues are bypassed until strict mode returns.
    """
    mode = current_gate_mode()
    if mode == "requirements_only" and target_kind != "requirement_draft":
        return "off"
    return mode


class RulesetCompatibilityError(ValueError):
    """A persisted catalog cannot authorize a current adjudication."""

Outcome = Literal["pass", "fail", "unknown", "not_applicable"]
Kind = Literal["blocking", "advisory"]

_OUTCOMES = frozenset({"pass", "fail", "unknown", "not_applicable"})
_EVIDENCE_PREFIXES = frozenset(
    {
        "image",
        "reference",
        "requirement",
        "technical",
        "report",
        "artifact",
        "prompt_plan",
        "snapshot",
    }
)
_PASS_DECISIONS = frozenset({"pass"})
_FAIL_DECISIONS = frozenset({"revise", "reject", "fail"})
_UNKNOWN_DECISIONS = frozenset({"unknown", "revise", "reject"})

UNKNOWN_RULE = "UNKNOWN_RULE"
ILLEGAL_EVIDENCE = "ILLEGAL_EVIDENCE"
WRONG_TARGET = "WRONG_TARGET"
CONTRADICTION = "CONTRADICTION"
DUPLICATE_INSTANCE = "DUPLICATE_INSTANCE"
MISSING_RESULT = "MISSING_RESULT"
INVALID_OUTCOME = "INVALID_OUTCOME"
UNSUPPORTED_DECISION = "UNSUPPORTED_DECISION"

_MESSAGES = {
    UNKNOWN_RULE: "审查结果引用了未登记规则，未放行",
    ILLEGAL_EVIDENCE: "审查证据不属于该规则允许的来源，未放行",
    WRONG_TARGET: "审查目标与当前产物不一致，未放行",
    CONTRADICTION: "审查结论与规则结果矛盾，未放行",
    DUPLICATE_INSTANCE: "同一规则实例出现重复结果，未放行",
    MISSING_RESULT: "必审规则结果缺失，未放行",
    INVALID_OUTCOME: "规则结果不在允许范围，未放行",
    UNSUPPORTED_DECISION: "审查结论不在允许范围，未放行",
}


class ProtocolIssue(ValueError):
    """A reviewer result that is not a legal decision for this catalog.

    These are contract defects, never service outages: callers translate them
    into a typed protocol rejection and keep the gate closed.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    @property
    def message(self) -> str:
        return _MESSAGES.get(self.code, "审查结果不合法，未放行")


@dataclass(frozen=True)
class Rule:
    """One server-owned requirement for one target kind.

    `instance` gives a rule a stable identity when the same rule has to be
    checked more than once (per text item, per reference). Duplicate or missing
    instances are contract errors, never silently merged.
    """

    rule_id: str
    label: str
    kind: Kind = "blocking"
    evidence_types: tuple[str, ...] = ("artifact",)
    applicable: bool = True
    reason: str = ""
    instance: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"blocking", "advisory"}:
            raise ValueError(f"unknown rule kind: {self.kind}")
        if not self.evidence_types:
            raise ValueError(f"rule {self.rule_id} needs at least one evidence type")
        for evidence_type in self.evidence_types:
            if evidence_type not in _EVIDENCE_PREFIXES:
                raise ValueError(f"rule {self.rule_id} declares unknown evidence type")
        if not self.applicable and not self.reason:
            raise ValueError(f"rule {self.rule_id} must state why it does not apply")

    @property
    def slot(self) -> tuple[str, str]:
        return (self.rule_id, self.instance)


@dataclass(frozen=True)
class RuleSet:
    """The catalog for one target kind, versioned so history stays readable."""

    target_kind: str
    rules: tuple[Rule, ...]
    version: str = RULESET_VERSION
    decisions: tuple[str, ...] = ("pass", "revise", "reject", "unknown")

    def blocking(self) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.kind == "blocking")

    def advisory(self) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.kind == "advisory")

    def slot(self, slot: tuple[str, str]) -> Rule | None:
        for rule in self.rules:
            if rule.slot == slot:
                return rule
        return None

    def describe(self) -> list[dict[str, Any]]:
        """Reviewer-facing view: the same table the model is asked to fill."""
        return [
            {
                "rule_id": rule.rule_id,
                "label": rule.label,
                "kind": rule.kind,
                "instance": rule.instance,
                "applicability": "applicable" if rule.applicable else "not_applicable",
                "reason": rule.reason,
                "evidence_types": list(rule.evidence_types),
            }
            for rule in self.rules
        ]


@dataclass(frozen=True)
class Check:
    """One normalized reviewer result for one catalog slot."""

    rule_id: str
    outcome: str
    evidence_ids: tuple[str, ...] = ()
    note: str = ""
    instance: str = ""

    @property
    def slot(self) -> tuple[str, str]:
        return (self.rule_id, self.instance)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rule_id": self.rule_id,
            "outcome": self.outcome,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.instance:
            payload["instance"] = self.instance
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass(frozen=True)
class Adjudication:
    """The effective result of one review, as the server decided it."""

    target_id: str
    decision: str
    declared_decision: str
    checks: tuple[Check, ...]
    blocking_failures: tuple[str, ...]
    blocking_unknowns: tuple[str, ...]
    advisory: dict[str, str] = field(default_factory=dict)
    ruleset_version: str = RULESET_VERSION

    @property
    def approved(self) -> bool:
        return self.decision == "pass"

    def check_payloads(self) -> list[dict[str, Any]]:
        return [check.as_payload() for check in self.checks]

    def review_checks(self) -> dict[str, Any]:
        return {
            "ruleset_version": self.ruleset_version,
            "checks": self.check_payloads(),
        }


def evidence_type(evidence_id: str) -> str:
    prefix, separator, rest = evidence_id.partition(":")
    if separator and rest and prefix in _EVIDENCE_PREFIXES:
        return prefix
    return "artifact"


def _as_check(raw: Any, ruleset: RuleSet) -> Check:
    if not isinstance(raw, dict):
        raise ProtocolIssue(MISSING_RESULT, "审查结果不是对象")
    rule_id = raw.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id:
        raise ProtocolIssue(UNKNOWN_RULE, "审查结果缺少规则编号")
    instance = raw.get("instance")
    if instance is not None and not isinstance(instance, str):
        raise ProtocolIssue(UNKNOWN_RULE, "审查结果实例编号不合法")
    instance = instance or ""
    rule = ruleset.slot((rule_id, instance))
    if rule is None:
        raise ProtocolIssue(UNKNOWN_RULE, f"未登记的规则：{rule_id}")
    outcome = raw.get("outcome")
    if outcome not in _OUTCOMES:
        raise ProtocolIssue(INVALID_OUTCOME, f"规则 {rule_id} 的结果不合法")
    evidence = raw.get("evidence_ids")
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        raise ProtocolIssue(ILLEGAL_EVIDENCE, f"规则 {rule_id} 的证据引用不合法")
    note = raw.get("note") or ""
    if not isinstance(note, str) or "://" in note:
        raise ProtocolIssue(ILLEGAL_EVIDENCE, f"规则 {rule_id} 的说明不合法")
    return Check(
        rule_id=rule_id,
        outcome=str(outcome),
        evidence_ids=tuple(evidence),
        note=note,
        instance=instance,
    )


def _check_evidence(check: Check, rule: Rule, allowed: set[str]) -> None:
    if not rule.applicable:
        if check.outcome != "not_applicable":
            raise ProtocolIssue(CONTRADICTION, f"规则 {rule.rule_id} 由服务端判定不适用")
        return
    if check.outcome == "not_applicable":
        raise ProtocolIssue(CONTRADICTION, f"规则 {rule.rule_id} 由服务端判定适用")
    if not check.evidence_ids:
        # A failing or unknown result without an evidence pointer is still an
        # unproven claim; it must not be counted as a legal negative either.
        raise ProtocolIssue(ILLEGAL_EVIDENCE, f"规则 {rule.rule_id} 缺少证据引用")
    for evidence_id in check.evidence_ids:
        if evidence_id not in allowed:
            raise ProtocolIssue(ILLEGAL_EVIDENCE, f"规则 {rule.rule_id} 引用了非本次证据")
        if evidence_type(evidence_id) not in rule.evidence_types:
            raise ProtocolIssue(ILLEGAL_EVIDENCE, f"规则 {rule.rule_id} 证据来源不匹配")


def _relaxed_adjudication(
    ruleset: RuleSet,
    *,
    target_id: str,
    declared_decision: str,
    checks: Iterable[Any],
    allowed_evidence: Iterable[str],
) -> Adjudication:
    """Collect well-formed observations without letting the catalogue block.

    The target and the declared decision still have to be structurally legal.
    Unknown rules, duplicate instances and evidence-id mistakes are ignored
    rather than promoted into hard stops; this is the deliberate soft-gate
    rollout path.
    """
    allowed = set(allowed_evidence)
    seen: dict[tuple[str, str], Check] = {}
    for raw in checks:
        try:
            check = _as_check(raw, ruleset)
            rule = next((item for item in ruleset.rules if item.slot == check.slot), None)
            if rule is None:
                continue
            _check_evidence(check, rule, allowed)
        except ProtocolIssue:
            continue
        seen.setdefault(check.slot, check)
    ordered = tuple(seen[rule.slot] for rule in ruleset.rules if rule.slot in seen)
    return Adjudication(
        target_id=target_id,
        decision="pass",
        declared_decision=declared_decision,
        checks=ordered,
        blocking_failures=(),
        blocking_unknowns=(),
        advisory={check.rule_id: check.outcome for check in ordered},
        ruleset_version=ruleset.version,
    )


def adjudicate(
    ruleset: RuleSet,
    *,
    target_id: str,
    result_target_id: str,
    declared_decision: str,
    checks: Iterable[Any],
    allowed_evidence: Iterable[str],
) -> Adjudication:
    """Validate one reviewer result and return the server's effective decision."""
    if result_target_id != target_id:
        raise ProtocolIssue(WRONG_TARGET, "审查目标与当前产物不一致")
    if declared_decision not in ruleset.decisions:
        raise ProtocolIssue(UNSUPPORTED_DECISION, "审查结论不在允许范围")
    if gate_mode_for_target(ruleset.target_kind) == "off":
        return _relaxed_adjudication(
            ruleset,
            target_id=target_id,
            declared_decision=declared_decision,
            checks=checks,
            allowed_evidence=allowed_evidence,
        )
    allowed = set(allowed_evidence)
    seen: dict[tuple[str, str], Check] = {}
    for raw in checks:
        check = _as_check(raw, ruleset)
        if check.slot in seen:
            raise ProtocolIssue(DUPLICATE_INSTANCE, f"重复的规则实例：{check.rule_id}")
        seen[check.slot] = check

    blocking_checks: list[Check] = []
    for rule in ruleset.blocking():
        check = seen.get(rule.slot)
        if check is None:
            if rule.applicable:
                raise ProtocolIssue(MISSING_RESULT, f"缺少必审结果：{rule.rule_id}")
            continue
        _check_evidence(check, rule, allowed)
        blocking_checks.append(check)

    failures = tuple(check.rule_id for check in blocking_checks if check.outcome == "fail")
    unknowns = tuple(check.rule_id for check in blocking_checks if check.outcome == "unknown")
    if failures:
        state = "fail"
    elif unknowns:
        state = "unknown"
    else:
        state = "pass"

    allowed_decisions = {
        "pass": _PASS_DECISIONS,
        "fail": _FAIL_DECISIONS,
        "unknown": _UNKNOWN_DECISIONS,
    }[state]
    if declared_decision not in allowed_decisions:
        raise ProtocolIssue(CONTRADICTION, "审查结论与规则结果矛盾")

    advisory: dict[str, str] = {}
    for rule in ruleset.advisory():
        check = seen.get(rule.slot)
        if check is None:
            continue
        for evidence_id in check.evidence_ids:
            if evidence_id not in allowed:
                raise ProtocolIssue(ILLEGAL_EVIDENCE, f"规则 {rule.rule_id} 引用了非本次证据")
        advisory[check.rule_id] = check.outcome

    return Adjudication(
        target_id=target_id,
        decision=declared_decision,
        declared_decision=declared_decision,
        checks=tuple(seen[rule.slot] for rule in ruleset.rules if rule.slot in seen),
        blocking_failures=failures,
        blocking_unknowns=unknowns,
        advisory=advisory,
        ruleset_version=ruleset.version,
    )


def s07_rules(*, echo_rules: Iterable[str] = ()) -> RuleSet:
    """The two pre-submit checks are always applicable for a prompt plan.

    General aesthetics is registered as advisory on purpose: it is feedback the
    reviewer may report, never a gate that can block an approved prompt.
    `echo_rules` carries the brief's compilation rule ids: the server already
    enforces them on the candidate, so a reviewer echo is recorded as advisory
    feedback instead of a second, differently-derived gate.
    """
    known = {
        "G03_CONFIRMATION",
        "G05_EXECUTION_BOUNDARY",
        "advisory:aesthetics",
        "advisory:composition",
        "advisory:lighting",
        "advisory:creative",
    }
    rules = [
        Rule("G03_CONFIRMATION", "提示词与当前确认需求一致", evidence_types=("artifact",)),
        Rule(
            "G05_EXECUTION_BOUNDARY",
            "提示词没有越出已确认的执行边界",
            evidence_types=("artifact",),
        ),
        Rule(
            "advisory:aesthetics",
            "通用审美与风格反馈",
            kind="advisory",
            evidence_types=("artifact",),
        ),
        Rule(
            "advisory:composition",
            "构图偏好反馈",
            kind="advisory",
            evidence_types=("artifact",),
        ),
        Rule(
            "advisory:lighting",
            "光线偏好反馈",
            kind="advisory",
            evidence_types=("artifact",),
        ),
        Rule(
            "advisory:creative",
            "创意方向反馈",
            kind="advisory",
            evidence_types=("artifact",),
        ),
    ]
    for rule_id in echo_rules:
        if not isinstance(rule_id, str) or not rule_id or rule_id in known:
            continue
        known.add(rule_id)
        rules.append(
            Rule(
                rule_id,
                "编制覆盖复核（服务端候选校验已强制）",
                kind="advisory",
                evidence_types=("artifact",),
            )
        )
    return RuleSet(target_kind="prompt_plan", rules=tuple(rules))


def recovery_ruleset(payload: dict[str, Any]) -> RuleSet:
    """Build the current server catalog for replaying a persisted review.

    The historical request is evidence of what the reviewer saw, never an
    authority to weaken the current contract. Only a supported catalogue
    version may be replayed; rule strength, applicability and evidence
    permissions are always taken from the server. Historical rule IDs are
    retained as advisory echo checks so an old reviewer answer still explains
    its coverage without becoming a gate.
    """
    target_kind = payload.get("target_kind")
    version = payload.get("ruleset_version")
    raw_rules = payload.get("rules")
    if target_kind != "prompt_plan":
        raise RulesetCompatibilityError("目标类型不受支持")
    if not isinstance(version, str) or version not in SUPPORTED_RULESET_VERSIONS:
        raise RulesetCompatibilityError("规则版本不受支持")
    if not isinstance(raw_rules, list) or not raw_rules or len(raw_rules) > _MAX_PERSISTED_RULES:
        raise RulesetCompatibilityError("规则目录不可识别")
    echo_rules: list[str] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            raise RulesetCompatibilityError("规则目录不可识别")
        rule_id = item.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            raise RulesetCompatibilityError("规则目录不可识别")
        echo_rules.append(rule_id)
    if version != RULESET_VERSION:
        raise RulesetCompatibilityError("规则版本不受支持")
    return s07_rules(echo_rules=tuple(echo_rules))


def ruleset_from_payload(payload: dict[str, Any]) -> RuleSet | None:
    """Explain the catalog a persisted reviewer request was issued with.

    This function is intentionally for historical display only. A current
    adjudication must use :func:`recovery_ruleset` (or the live stage catalog),
    never the caller-supplied strengths and evidence permissions here.
    """
    target_kind = payload.get("target_kind")
    raw_rules = payload.get("rules")
    if not isinstance(target_kind, str) or not target_kind or not isinstance(raw_rules, list):
        return None
    rules: list[Rule] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            return None
        rule_id = item.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            return None
        evidence_types = item.get("evidence_types")
        rules.append(
            Rule(
                rule_id,
                str(item.get("label") or rule_id),
                kind="advisory" if item.get("kind") == "advisory" else "blocking",
                evidence_types=(
                    tuple(evidence_types) if isinstance(evidence_types, list) else ("artifact",)
                ),
                applicable=item.get("applicability") != "not_applicable",
                reason=str(item.get("reason") or ""),
                instance=str(item.get("instance") or ""),
            )
        )
    version = str(payload.get("ruleset_version") or RULESET_VERSION)
    return RuleSet(target_kind=target_kind, rules=tuple(rules), version=version)

# Governance observation rules the reviewer prompt asks reviewers to report.
# They are registered explicitly (never by pattern): an id the server does not
# know here is a protocol defect, not an extra advisory opinion.
ADVISORY_GOVERNANCE_RULES: Final[frozenset[str]] = frozenset(
    {
        "G01_SCOPE",
        "G01_ACTION",
        "G02_FACT_SOURCE",
        "G02_IMAGE_VISIBLE",
        "G02_NO_UNSUPPORTED_INFERENCE",
        "G02_OBSERVATION_GROUNDED",
        "G02_REFERENCE_USAGE",
        "G02_REPORT",
        "G02_TEXT_IS_DATA",
        "G02_UNKNOWN",
        "G03_AUTH_BOUNDARY",
        "G03_BRIEF",
        "G03_CONFIRMATION",
        "G03_CONFIRMATION_STATE",
        "G03_CONSTRAINTS",
        "G03_COPY",
        "G03_EVIDENCE_COVERAGE",
        "G03_REQUIREMENTS",
        "G03_REQUIREMENT_CONSISTENCY",
        "G03_SINGLE_DIRECTION",
        "G03_SIZE",
        "G03_SOURCE",
        "G03_SOURCE_BASIS",
        "G03_STRUCTURE",
        "G03_SUBJECT_PURPOSE",
        "G03_TEXTS",
        "G03_UNRESOLVED",
        "G04_PROHIBITED",
        "G05_EXECUTION_BOUNDARY",
        "G05_OUTPUT_STRUCTURE",
        "G06_EVIDENCE_COVERAGE",
        "G06_IMAGE",
        "G07_FACT_SOURCE",
        "G07_HARD_REQUIREMENT",
        "G07_MANIFEST_ASSET",
        "G07_MANIFEST_CAPTION",
        "G07_MANIFEST_EVIDENCE",
        "G07_MANIFEST_FILENAME",
        "G07_MANIFEST_REQUIREMENT",
        "G07_OUTPUT_STRUCTURE",
        "G07_PROHIBITED_CONTENT",
        "G07_SCOPE_ROLE",
        "G07_UNAUTHORIZED_ACTION",
        "G07_UNKNOWN_AS_FACT",
        "G08_AUTH",
        "review:quality_report",
        "review:scope",
        "review:evidence",
        "review:consistency",
        "review:summary",
        "review:scope_and_duty",
        "review:fact_source",
        "review:unknown_as_certain",
        "review:evidence_coverage",
        "review:unauthorized_action",
        "review:no_unresolved_critical",
        "review:unauthorized_claim",
        "review:scope_and_unauthorized",
        "review:evidence_and_unknowns",
        "review:scope_and_evidence",
        "review:unauthorized",
    }
)


def governance_ruleset(
    target_kind: str,
    blocking: Iterable[tuple[str, str]],
    *,
    decisions: Iterable[str] = ("pass", "revise", "reject", "unknown"),
    evidence_types: Iterable[str] = ("artifact",),
    version: str = RULESET_VERSION,
) -> RuleSet:
    """Build a stage catalogue: its blocking rules plus registered governance advisories."""
    rules: list[Rule] = []
    known: set[str] = set()
    for rule_id, label in blocking:
        if rule_id in known:
            continue
        known.add(rule_id)
        rules.append(
            Rule(
                rule_id,
                label,
                kind="blocking",
                evidence_types=tuple(evidence_types),
            )
        )
    advisory_evidence = tuple(dict.fromkeys((*evidence_types, "artifact")))
    for rule_id in sorted(ADVISORY_GOVERNANCE_RULES - known):
        rules.append(
            Rule(
                rule_id,
                "治理观察项（不改变放行结论）",
                kind="advisory",
                evidence_types=advisory_evidence,
            )
        )
    return RuleSet(
        target_kind=target_kind,
        rules=tuple(rules),
        version=version,
        decisions=tuple(decisions),
    )


def review_output(
    output_data: object,
    *,
    target_id: str,
    ruleset: RuleSet,
    allowed_evidence: Iterable[str],
) -> Adjudication:
    """Shape-check one reviewer result for one target and adjudicate it."""
    if not isinstance(output_data, dict):
        raise ProtocolIssue(MISSING_RESULT, "审查输出缺少 data，未放行")
    results = output_data.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise ProtocolIssue(MISSING_RESULT, "审查结果必须且只能包含一个目标，未放行")
    result = results[0]
    if not isinstance(result, dict):
        raise ProtocolIssue(MISSING_RESULT, "审查结果格式不可识别，未放行")
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ProtocolIssue(MISSING_RESULT, "审查结果没有给出任何规则核验，未放行")
    return adjudicate(
        ruleset,
        target_id=target_id,
        result_target_id=result.get("target_id"),
        declared_decision=result.get("decision"),
        checks=checks,
        allowed_evidence=allowed_evidence,
    )


def review_results(
    output_data: object,
    *,
    target_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Shape-check a multi-target reviewer answer and index results by target."""
    expected = tuple(target_ids)
    if not isinstance(output_data, dict):
        raise ProtocolIssue(MISSING_RESULT, "审查输出缺少 data，未放行")
    results = output_data.get("results")
    if not isinstance(results, list):
        raise ProtocolIssue(MISSING_RESULT, "审查结果必须是目标列表，未放行")
    indexed: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ProtocolIssue(MISSING_RESULT, "审查结果格式不可识别，未放行")
        target_id = result.get("target_id")
        if target_id not in expected:
            raise ProtocolIssue(WRONG_TARGET, "审查结果声明了本次调用之外的目标，未放行")
        if target_id in indexed:
            raise ProtocolIssue(WRONG_TARGET, "审查结果重复声明同一目标，未放行")
        checks = result.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ProtocolIssue(MISSING_RESULT, "审查结果没有给出任何规则核验，未放行")
        indexed[target_id] = result
    missing = [target_id for target_id in expected if target_id not in indexed]
    if missing:
        raise ProtocolIssue(MISSING_RESULT, "审查结果缺少目标，未放行")
    return indexed


__all__ = [
    "ADVISORY_GOVERNANCE_RULES",
    "Adjudication",
    "Check",
    "ProtocolIssue",
    "RULESET_VERSION",
    "Rule",
    "RuleSet",
    "RulesetCompatibilityError",
    "SUPPORTED_RULESET_VERSIONS",
    "adjudicate",
    "current_gate_mode",
    "evidence_type",
    "gate_mode_for_target",
    "governance_ruleset",
    "review_output",
    "recovery_ruleset",
    "review_results",
    "ruleset_from_payload",
    "s07_rules",
    "set_gate_mode",
]
