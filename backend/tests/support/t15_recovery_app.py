from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.adapters.base import ProviderError, RoleRequest
from app.config import Settings
from app.main import create_app
from tests.support.adapters import LLMStub, VisualStub, build_test_registry


def _log(event: str) -> None:
    path = os.environ.get("T15_CALL_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(event + "\n")


def _brief_data(request: RoleRequest) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    return {
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
    }


class ProcessScript:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.requirements_calls = 0

    def __call__(self, request: Any) -> Any:
        task = request.payload["task"]
        role = str(task["role"])
        if role == "intent_router":
            return {
                "role": "intent_router",
                "status": "ready",
                "data": {
                    "route": "image_request",
                    "action": "new_request",
                    "evidence_ids": [task["allowed_evidence_ids"][0]],
                },
                "issues": [],
                "questions": [],
            }
        if role == "output_reviewer":
            target = request.payload["review_targets"][0]
            target_id = str(target["target_id"])
            kind = target.get("target_kind")
            rules = (
                ("G01_SCOPE", "G01_ACTION")
                if kind == "intent_route"
                else (
                    "G03_STRUCTURE",
                    "G03_SUBJECT_PURPOSE",
                    "G03_SOURCE_BASIS",
                    "G03_TEXTS",
                    "G03_CONSTRAINTS",
                    "G03_SIZE",
                    "G03_UNRESOLVED",
                )
            )
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
        if role == "requirements_consultant":
            self.requirements_calls += 1
            _log(f"requirements:{self.requirements_calls}")
            if self.mode == "fail" and self.requirements_calls == 1:
                raise ProviderError(
                    "network down",
                    evidence="uncertain",
                    error_code="LLM_NETWORK_ERROR",
                )
            return {
                "role": "requirements_consultant",
                "status": "ready",
                "data": _brief_data(request),
                "issues": [],
                "questions": [],
            }
        raise AssertionError(f"unexpected role {role!r}")


class LoggingVisual(VisualStub):
    async def submit(self, request):  # type: ignore[no-untyped-def]
        _log("submit")
        return await super().submit(request)


def build_app():
    script = ProcessScript(os.environ.get("T15_MODE", "fail"))
    stub = LLMStub(role_responder=script)
    visual = LoggingVisual()
    return create_app(
        settings=Settings(workflow_role_policy="legacy"),
        registry=build_test_registry(llm=stub, visual=visual),
    )


app = build_app()
