"""P0 baseline probes for the v3 gate remediation (#86 / ticket #87).

These probes pin the *current* behaviour of the seams that the remediation
tickets will move, so that a later change cannot quietly "fix" a symptom
without the owning ticket. They are intentionally cheap pure-function probes:
the HTTP level regressions live in the owning stage tickets.

F1 and F2 were still reproducing on the recorded baseline and are now plain
regressions: #88 replaced the S07 review gate with the server rule catalog.
F4 is still reproducing, so it stays a strict xfail until P3 (#96).

H1 (multi sentence verbatim requirement was not covered) has already been
repaired by the local revisions captured in the P0 baseline commit, so it is
a plain regression here.
"""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.workflow.roles import RoleOutput
from app.workflow.stages import s07


def _review(decision: str, checks: list[dict[str, object]]) -> RoleOutput:
    return RoleOutput(
        role="output_reviewer",
        status="ready",
        data={"results": [{"target_id": "art-1", "decision": decision, "checks": checks}]},
    )


def _check(rule_id: str, outcome: str) -> dict[str, object]:
    evidence = [] if outcome == "not_applicable" else ["art-1"]
    return {"rule_id": rule_id, "outcome": outcome, "evidence_ids": evidence, "note": "ok"}


def test_f1_s07_all_not_applicable_cannot_be_approved() -> None:
    output = _review(
        "pass",
        [
            _check("G03_CONFIRMATION", "not_applicable"),
            _check("G05_EXECUTION_BOUNDARY", "not_applicable"),
        ],
    )

    with pytest.raises(ApiError) as caught:
        s07._validate_review(output, target_id="art-1", allowed_evidence=("art-1", "art-2"))

    assert caught.value.status_code == 409
    assert caught.value.code == "PROMPT_REVIEW_INVALID"


def test_f2_s07_advisory_failure_does_not_block() -> None:
    output = _review(
        "pass",
        [
            _check("G03_CONFIRMATION", "pass"),
            _check("G05_EXECUTION_BOUNDARY", "pass"),
            _check("advisory:aesthetics", "fail"),
            _check("advisory:lighting", "unknown"),
        ],
    )

    s07._validate_review(output, target_id="art-1", allowed_evidence=("art-1", "art-2"))


def test_f4_creative_style_lighting_are_not_hard_requirements() -> None:
    brief = {
        "product_description": "白色陶瓷马克杯",
        "purpose": "电商主图",
        "creative_direction": "清晨窗边生活场景",
        "style_keywords": ["极简", "自然光"],
        "composition": "居中构图",
        "lighting": "柔和侧光",
        "size": "3840x2160",
    }

    rule_ids = {item["rule_id"] for item in s07._hard_requirements(brief)}

    assert "hard:creative" not in rule_ids
    assert "hard:style" not in rule_ids
    assert "hard:lighting" not in rule_ids


def test_h1_multi_sentence_requirement_is_covered_verbatim() -> None:
    requirement = "白色陶瓷马克杯；杯身无图案。放在木质桌面上"

    assert s07._covers_requirement(requirement, "白色陶瓷马克杯；杯身无图案。放在木质桌面上")
    assert s07._covers_requirement(requirement, "白色陶瓷马克杯。杯身无图案。放在木质桌面上")


def test_h1_natural_prompt_labels_are_covered() -> None:
    requirement = "大唐不夜城的盛唐街区景象，写实风格夜景（场景类创作，非实物商品）；尺寸不限定"
    prompt = (
        "画面内容：大唐不夜城的盛唐街区景象，写实风格夜景（场景类创作，非实物商品）。"
        "关于尺寸：尺寸不限定，本次按已确认的横版画幅统一组织。"
    )

    assert s07._covers_requirement(requirement, prompt)


def test_h1_containment_does_not_hide_negation() -> None:
    assert not s07._covers_requirement("要反光", "不要反光")


def test_h1_characters_cannot_be_assembled_across_clauses() -> None:
    requirement = "白色陶瓷马克杯；木质桌面"

    assert not s07._covers_requirement(requirement, "白色；陶瓷马克杯；木质桌面")
    assert not s07._covers_requirement(requirement, "白色陶瓷马克杯")
    assert not s07._covers_requirement("不加包装盒", "加包装盒")


def test_h1_number_and_negation_drift_is_not_covered() -> None:
    assert not s07._covers_requirement("两件装", "三件装")
    assert not s07._covers_requirement("不要反光", "要反光")
