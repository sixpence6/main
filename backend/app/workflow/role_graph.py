"""Consolidated model calls; user confirmations and delivery are program-owned."""
from __future__ import annotations

from typing import Any, Final

PERSONAS: Final[dict[str, str]] = {"planner": "需求助手", "reference": "参考图分析师", "appearance": "视觉策划师", "maker": "生成工程师"}
ROLE_DAG: Final[dict[str, Any]] = {
    "nodes": {
        "requirements_assistant": {"persona": "planner", "stage": "S03", "outputs": ("brief_draft", "requirements_questions")},
        "reference_analyst": {"persona": "reference", "stage": "S02", "outputs": ("reference_report",)},
        "visual_planner": {"persona": "appearance", "stage": "S04", "outputs": ("creative_direction", "visual_plan", "final_brief")},
        "generation_engineer": {"persona": "maker", "stage": "S07", "outputs": ("prompt_plan",)},
    },
    "edges": (
        {"from": "reference_analyst", "to": "requirements_assistant", "condition": "reference_usage_approved", "optional": True},
        {"from": "requirements_assistant", "to": "visual_planner", "condition": "brief_ready", "optional": False},
        {"from": "visual_planner", "to": "generation_engineer", "condition": "visual_ready", "optional": False},
    ),
    "user_gates": (),
    "program_steps": (
        {"after": "S08", "step": "S09", "before": "delivery", "label": "程序生图与本地技术校验"},
        {"after": "S09", "step": "S10", "before": "publication", "label": "程序生成交付清单，自动交付"},
    ),
    "back_edges": (
        {"from": "requirements_assistant", "to": "requirements_assistant", "condition": "clarification_or_revision", "label": "补充信息 / 需求修订"},
        {"from": "visual_planner", "to": "requirements_assistant", "condition": "missing_fact_or_conflict", "label": "补充阻塞事实"},
        {"from": "generation_engineer", "to": "requirements_assistant", "condition": "requirement_change", "label": "需求修订"},
    ),
}
ACTIVE_ROLE_IDS: Final[tuple[str, ...]] = tuple(ROLE_DAG["nodes"])


def assert_acyclic() -> None:
    nodes = set(ACTIVE_ROLE_IDS)
    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    indegree = dict.fromkeys(nodes, 0)
    for edge in ROLE_DAG["edges"]:
        source, target = edge["from"], edge["to"]
        if source not in nodes or target not in nodes:
            raise ValueError("unknown role edge")
        outgoing[source].append(target)
        indegree[target] += 1
    ready = [node for node in nodes if indegree[node] == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for target in outgoing[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(nodes):
        raise ValueError("role DAG contains a cycle")
