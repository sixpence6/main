from __future__ import annotations

from typing import Any

from app.workflow.schemas_workflow import ActionView
from app.workflow.stages import (
    s01,
    s02,
    s03,
    s04,
    s05,
    s06,
    s07,
    s08,
    s09,
    s10,
    s11,
    s12,
)
from app.workflow.state import WorkflowContext

ACTION_OWNER: dict[str, Any] = {}
for _module in (s01, s02, s03, s04, s05, s06, s07, s08, s09, s10, s11, s12):
    for _action in _module.ACTIONS:
        ACTION_OWNER[_action] = _module

_ACTIONS: dict[str, dict[str, Any]] = {
    "submit_request": {"label": "开始整理需求", "kind": "text", "requires_text": True, "text_label": "描述你想要的商品图", "requires_confirmation": False},
    "answer_intent_clarification": {"label": "补充说明", "kind": "text", "requires_text": True, "text_label": "请补充范围或用途", "requires_confirmation": False},
    "analyze_reference": {"label": "分析参考图", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "confirm_reference_usage": {"label": "选择参考用途", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "compose_brief": {"label": "整理需求卡", "kind": "text", "requires_text": True, "text_label": "补充需求信息", "requires_confirmation": False},
    "answer_requirements_clarification": {"label": "回答需求问题", "kind": "text", "requires_text": True, "text_label": "回答以上问题", "requires_confirmation": False},
    "revise_brief_draft": {"label": "修改需求草稿", "kind": "text", "requires_text": True, "text_label": "说明需要修改的内容", "requires_confirmation": False},
    "plan_creative": {"label": "制定创意方向", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "revise_creative": {"label": "修改创意方向", "kind": "text", "requires_text": True, "text_label": "说明创意调整", "requires_confirmation": False},
    "plan_visual": {"label": "制定视觉方案", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "revise_visual": {"label": "修改视觉方案", "kind": "text", "requires_text": True, "text_label": "说明视觉调整", "requires_confirmation": False},
    "finalize_requirements": {"label": "生成最终需求卡", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "confirm_requirements": {"label": "确认需求卡", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "revise_requirements": {"label": "修改需求", "kind": "text", "requires_text": True, "text_label": "说明需要修改的需求内容", "requires_confirmation": False},
    "compile_prompt": {"label": "编制生图提示词", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "revise_prompt": {"label": "修改提示词", "kind": "text", "requires_text": True, "text_label": "说明提示词调整", "requires_confirmation": False},
    "submit_generation": {"label": "提交生成", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "review_candidate": {"label": "审查候选图", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "prepare_delivery": {"label": "准备交付", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "approve_delivery": {"label": "批准交付", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": True},
    "publish_delivery": {"label": "发布交付", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": True},
    "request_new_version": {"label": "基于反馈生成新版本", "kind": "text", "requires_text": True, "text_label": "说明本次修改要求", "requires_confirmation": False},
    "retrieve_original_result": {"label": "找回原结果", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "review_legacy_item": {"label": "补审历史内容", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "continue_workflow": {"label": "继续流程", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "recover_submission": {"label": "确认提交状态", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "resume_after_restart": {"label": "恢复并继续", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "cleanup_expired": {"label": "清理过期内容", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
}


def action_view(action_id: str) -> ActionView:
    spec = _ACTIONS.get(action_id)
    if spec is None:
        raise ValueError(f"unknown action {action_id}")
    return ActionView(id=action_id, **spec)


def known_actions() -> tuple[str, ...]:
    return tuple(_ACTIONS)


def owner(action_id: str):
    return ACTION_OWNER.get(action_id)


def owner_for_stage(stage: str):
    return {
        "S01": s01,
        "S02": s02,
        "S03": s03,
        "S04": s04,
        "S05": s05,
        "S06": s06,
        "S07": s07,
        "S08": s08,
        "S09": s09,
        "S10": s10,
        "S11": s11,
        "S12": s12,
    }.get(stage)


def _views_for(module: Any, ctx: WorkflowContext) -> list[ActionView]:
    views: list[ActionView] = []
    for item in module.available(ctx):
        if isinstance(item, ActionView):
            views.append(item)
            continue
        if isinstance(item, str):
            views.append(action_view(item))
            continue
        if isinstance(item, dict):
            action_id = str(item.get("id") or "")
            base = _ACTIONS.get(action_id)
            if base is None:
                continue
            merged = {**base, **{key: value for key, value in item.items() if key != "id"}}
            views.append(ActionView(id=action_id, **merged))
    # Updating the requirements remains an explicit user action after the
    # card was confirmed and the workflow advanced to prompt compilation.
    # The S06 handler re-validates the active confirmation before revoking it.
    reconfirmation_pending = ctx.run.status == "blocked" and (
        ctx.run.issue_code == "PROMPT_REQUIRES_RECONFIRMATION"
    )
    if (
        ctx.run.stage == "S07"
        and (ctx.run.status == "active" or reconfirmation_pending)
        and not any(view.id == "revise_requirements" for view in views)
    ):
        views.append(action_view("revise_requirements"))
    return views


def recovery_action_available(ctx: WorkflowContext, action_id: str) -> bool:
    if ctx.run is None or action_id not in s12.ACTIONS:
        return False
    if action_id == "continue_workflow":
        # Allow the idempotent repeat after a successful manual continue. The
        # stage handler still rejects requests with no persisted checkpoint.
        return ctx.run.status in {"active", "blocked", "failed"}
    return action_id in s12.available(ctx)


def available_actions(ctx: WorkflowContext) -> list[ActionView]:
    if ctx.run is None:
        return [action_view("submit_request")]
    # A technical failure exposes only the explicit recovery action. The stage
    # that failed remains current; it never auto-advances or re-calls a model
    # from a GET/status refresh.
    if ctx.run.status == "failed" or (
        ctx.run.status == "blocked" and ctx.run.issue_code == "REVIEW_UNAVAILABLE"
    ):
        return _views_for(s12, ctx)
    module = owner_for_stage(ctx.run.stage)
    if module is None:
        return []
    return _views_for(module, ctx)


async def handle_action(
    ctx: WorkflowContext, action: str, payload: dict[str, Any]
) -> None:
    module = owner(action)
    if module is None:
        raise ValueError(f"unknown action {action}")
    await module.handle(ctx, action, payload)


__all__ = [
    "ACTION_OWNER",
    "action_view",
    "recovery_action_available",
    "known_actions",
    "owner",
    "available_actions",
    "handle_action",
]
