from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

from app.config import Settings
from app.main import create_app
from app.workflow.stages.s02 import REQUIRED_REVIEW_RULES as VISION_RULES
from app.workflow.stages.s03 import REVIEW_RULE_IDS
from tests.support.adapters import LLMStub, VisualStub, build_test_registry


def _log(event: str) -> None:
    path = os.environ.get("T17_CALL_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(event + "\n")


def _evidence_for(rule: dict[str, Any], allowed: list[str]) -> list[str]:
    evidence: list[str] = []
    for evidence_type in rule.get("evidence_types", []):
        match = next(
            (item for item in allowed if item.startswith(f"{evidence_type}:")), None
        )
        if match is not None:
            evidence.append(match)
    return evidence


def _review_pass(target_id: str, rules: tuple[str, ...] = REVIEW_RULE_IDS) -> dict[str, Any]:
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {
                    "target_id": target_id,
                    "decision": "pass",
                    "checks": [
                        {
                            "rule_id": rule,
                            "outcome": "pass",
                            "evidence_ids": [target_id],
                            "note": "",
                        }
                        for rule in rules
                    ],
                }
            ]
        },
        "issues": [],
        "questions": [],
    }


def _ready_brief(request: Any) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    return {
        "role": "requirements_consultant",
        "status": "ready",
        "data": {
            "product_description": "米色托特包",
            "purpose": "电商商品主图",
            "facts": [
                {
                    "text": "用户声明商品为米色托特包",
                    "basis": "user_declared",
                    "evidence_ids": [evidence],
                }
            ],
            "creative_direction": None,
            "style_keywords": [],
            "composition": None,
            "lighting": None,
            "texts": [],
            "must_include": ["一只米色托特包"],
            "must_preserve": [],
            "forbidden": [],
            "size": request.payload["capabilities"]["size"] or "1024x1024",
            "reference_asset_id": None,
            "reference_usage": "none",
            "assumptions": [],
            "unresolved": [],
        },
        "issues": [],
        "questions": [],
    }


def _reference_ready(request: Any) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    return {
        "role": "reference_analyst",
        "status": "ready",
        "data": {
            "reference_usage": "product",
            "observations": [
                {
                    "kind": "product",
                    "description": "米色托特包",
                    "confidence": "clear",
                    "evidence_ids": [evidence],
                }
            ],
            "texts": [],
            "uncertainties": [],
        },
        "issues": [],
        "questions": [],
    }


def _quality_response(request: Any) -> dict[str, Any]:
    payload = request.payload
    rules = payload["input_data"]["rules"]
    allowed = list(payload["task"]["allowed_evidence_ids"])
    checks: list[dict[str, Any]] = []
    for rule in rules:
        if rule["applicability"] != "applicable":
            continue
        checks.append(
            {
                "rule_id": rule["rule_id"],
                "outcome": "pass",
                "evidence_ids": _evidence_for(rule, allowed),
                "note": "已核对",
            }
        )
    return {
        "role": "quality_compliance",
        "status": "ready",
        "data": {
            "asset_id": payload["input_data"]["asset_id"],
            "decision": "pass",
            "checks": checks,
            "soft_feedback": [],
            "limitations": [],
            "user_summary": "图片符合确认要求。",
        },
        "issues": [],
        "questions": [],
    }


def _review_response(request: Any) -> dict[str, Any]:
    targets = request.payload["review_targets"]
    allowed = list(request.payload["task"]["allowed_evidence_ids"])
    results: list[dict[str, Any]] = []
    for target in targets:
        checks: list[dict[str, Any]] = []
        if target["target_kind"] == "quality_report":
            checks.append(
                {
                    "rule_id": "review:quality_report",
                    "outcome": "pass",
                    "evidence_ids": [f"report:{target['artifact_id']}"],
                    "note": "报告与本次检查一致",
                }
            )
        else:
            by_id = {rule["rule_id"]: rule for rule in target["rules"]}
            for rule_id in target["required_rule_ids"]:
                rule = by_id[rule_id]
                checks.append(
                    {
                        "rule_id": rule_id,
                        "outcome": "pass",
                        "evidence_ids": _evidence_for(rule, allowed),
                        "note": "已直接查看图片并核对",
                    }
                )
        results.append(
            {
                "target_id": target["target_id"],
                "decision": "pass",
                "checks": checks,
            }
        )
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {"results": results},
        "issues": [],
        "questions": [],
    }


class RestartScript:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.intent_calls = 0
        self.requirements_calls = 0
        self.reference_calls = 0
        self.quality_calls = 0
        self.review_calls = 0
        self._block = threading.Event()

    def __call__(self, request: Any) -> Any:
        role = str(request.payload["task"]["role"])
        if role == "intent_router":
            self.intent_calls += 1
            _log(f"intent:{self.intent_calls}")
            evidence = request.payload["task"]["allowed_evidence_ids"][0]
            return {
                "role": "intent_router",
                "status": "ready",
                "data": {
                    "route": "image_request",
                    "action": "new_request",
                    "evidence_ids": [evidence],
                },
                "issues": [],
                "questions": [],
            }
        if role == "requirements_consultant":
            self.requirements_calls += 1
            _log(f"requirements:{self.requirements_calls}")
            if self.mode == "block-requirements":
                self._block.wait()
            return _ready_brief(request)
        if role == "reference_analyst":
            self.reference_calls += 1
            _log(f"reference:{self.reference_calls}")
            if self.mode == "block-reference":
                self._block.wait()
            return _reference_ready(request)
        if role == "quality_compliance":
            self.quality_calls += 1
            _log(f"quality:{self.quality_calls}")
            if self.mode == "block-quality":
                self._block.wait()
            return _quality_response(request)
        if role == "output_reviewer":
            self.review_calls += 1
            target = request.payload["review_targets"][0]
            target_kind = target.get("target_kind")
            _log(f"review:{self.review_calls}:{target_kind}:images={len(request.images)}")
            if self.mode == "block-review":
                self._block.wait()
            if target_kind == "intent_route":
                return _review_pass(str(target["target_id"]), ("G01_SCOPE", "G01_ACTION"))
            if target_kind == "reference_report":
                image_evidence = "reference:" + str(target.get("reference_asset_id"))
                return {
                    "role": "output_reviewer",
                    "status": "ready",
                    "data": {
                        "results": [
                            {
                                "target_id": str(target["target_id"]),
                                "decision": "pass",
                                "checks": [
                                    {
                                        "rule_id": rule,
                                        "outcome": "pass",
                                        "evidence_ids": [
                                            str(target["target_id"]),
                                            image_evidence,
                                        ],
                                        "note": "",
                                    }
                                    for rule in VISION_RULES
                                ],
                            }
                        ]
                    },
                    "issues": [],
                    "questions": [],
                }
            if target_kind == "brief_draft":
                return _review_pass(str(target["target_id"]))
            return _review_response(request)
        raise AssertionError(f"unexpected role {role!r}")


class LoggingVisual(VisualStub):
    block: threading.Event | None = None

    async def submit(self, request):  # type: ignore[no-untyped-def]
        _log("visual:submit")
        if self.block is not None:
            await asyncio.to_thread(self.block.wait)
        return await super().submit(request)


def build_app():
    mode = os.environ.get("T17_MODE", "success")
    script = RestartScript(mode)
    if mode == "block-submit":
        LoggingVisual.block = script._block
    stub = LLMStub(role_responder=script)
    visual = LoggingVisual()
    return create_app(
        settings=Settings(),
        registry=build_test_registry(llm=stub, visual=visual),
    )


app = build_app()
