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
    "confirm_prompt": {
        "label": "确认使用以上提示词生成", "kind": "confirm", "requires_text": False,
        "text_label": None, "requires_confirmation": True,
    },
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
    "confirm_requirements": {"label": "确认需求卡", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": True},
    "revise_requirements": {"label": "修改需求", "kind": "text", "requires_text": True, "text_label": "说明需要修改的需求内容", "requires_confirmation": False},
    "compile_prompt": {"label": "编制生图提示词", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "revise_prompt": {"label": "修改提示词", "kind": "text", "requires_text": True, "text_label": "说明提示词调整", "requires_confirmation": False},
    "abandon_generation": {
        "label": "放弃本次生成", "kind": "confirm", "requires_text": False,
        "text_label": None, "requires_confirmation": True,
    },
    "verify_generation": {"label": "核实原任务", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "retry_generation": {"label": "重新生成", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": True},
    "retry_rejected_generation": {"label": "重新生成", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": True},
    "revise_prompt_and_retry": {"label": "调整提示词并重新生成", "kind": "confirm", "requires_text": True, "text_label": "说明本次画面修正（例如：所有匾额保持空白，不要任何题字；主体呈现为有底座的微缩建筑模型）", "requires_confirmation": True},
    "recover_generation": {"label": "找回原结果", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "submit_generation": {"label": "提交生成", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "close_generation_tracking": {"label": "结束本地跟踪", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": True},
    "start_new_generation": {"label": "确认风险并生成新任务", "kind": "confirm", "requires_text": False, "text_label": None, "requires_confirmation": True},
    "review_candidate": {"label": "审查候选图", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "recheck_candidate": {"label": "重新检查这张图片", "kind": "button", "requires_text": False, "text_label": None, "requires_confirmation": False},
    "request_correction": {
        "label": "提交修正要求",
        "kind": "text",
        "requires_text": True,
        "text_label": "说明需要修正的图片问题",
        "requires_confirmation": False,
    },
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


# T13 exposes the effect of an action from the server, not from UI copy.
_READ_ONLY_ACTIONS = frozenset(
    {
        "verify_generation",
        "recover_generation",
        "recover_submission",
        "retrieve_original_result",
    }
)
_MODEL_CALL_ACTIONS = frozenset(
    {
        "submit_request",
        "answer_intent_clarification",
        "analyze_reference",
        "compose_brief",
        "answer_requirements_clarification",
        "revise_brief_draft",
        "plan_creative",
        "revise_creative",
        "plan_visual",
        "revise_visual",
        "finalize_requirements",
        "revise_requirements",
        "compile_prompt",
        "revise_prompt",
        "retry_generation",
        "retry_rejected_generation",
        "revise_prompt_and_retry",
        "submit_generation",
        "confirm_prompt",
        "start_new_generation",
        "review_candidate",
        "recheck_candidate",
        "request_correction",
        "request_new_version",
        "review_legacy_item",
        "continue_workflow",
        "resume_after_restart",
    }
)
_MANUAL_RECOVERY_ACTIONS = frozenset(
    {
        "retry_generation",
        "retry_rejected_generation",
        "revise_prompt_and_retry",
        "recover_generation",
        "recover_submission",
        "resume_after_restart",
        "continue_workflow",
        "retrieve_original_result",
    }
)


def action_effect(action_id: str) -> tuple[str, bool, bool, str, bool]:
    if action_id in _READ_ONLY_ACTIONS:
        return "read_only", True, False, (
            "manual_only" if action_id in _MANUAL_RECOVERY_ACTIONS else "none"
        ), False
    if action_id in _MODEL_CALL_ACTIONS:
        return "model_call", False, True, (
            "manual_only" if action_id in _MANUAL_RECOVERY_ACTIONS else "none"
        ), False
    return "write", False, False, "none", False


def action_view(action_id: str) -> ActionView:
    spec = _ACTIONS.get(action_id)
    if spec is None:
        raise ValueError(f"unknown action {action_id}")
    effect, read_only, requires_model_call, retry_mode, automatic_retry = action_effect(
        action_id
    )
    return ActionView(
        id=action_id,
        **spec,
        effect=effect,
        read_only=read_only,
        requires_model_call=requires_model_call,
        retry_mode=retry_mode,
        automatic_retry=automatic_retry,
    )


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
            effect, read_only, requires_model_call, retry_mode, automatic_retry = (
                action_effect(item.id)
            )
            views.append(
                item.model_copy(
                    update={
                        "effect": effect,
                        "read_only": read_only,
                        "requires_model_call": requires_model_call,
                        "retry_mode": retry_mode,
                        "automatic_retry": automatic_retry,
                    }
                )
            )
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
    prompt_repair_pending = ctx.run.status == "blocked" and ctx.run.issue_code in {
        "PROMPT_INVALID",
        "REVISION_BUDGET_EXHAUSTED",
    }
    abandonment_pending = ctx.run.status == "blocked" and (
        ctx.run.issue_code == "PROMPT_ABANDONED"
    )
    if (
        ctx.run.stage == "S07"
        and (
            ctx.run.status in {"active", "needs_input"}
            or reconfirmation_pending
            or prompt_repair_pending
            or abandonment_pending
        )
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
        # WG3-AC-10: a technical failure exposes exactly one forward action,
        # the explicit recovery command. Stage actions are not offered from a
        # failed state, because they would bypass the persisted checkpoint or
        # imply a stage can repair the reviewer's own technical failure.
        return _views_for(s12, ctx)
    if ctx.run.status == "blocked" and ctx.run.issue_code in s12.RETRYABLE_REVIEW_ISSUES:
        # A typed reviewer contract defect exposes exactly one safe action: replay
        # the persisted review request. Stage actions would suggest the user can
        # repair the reviewer's own output.
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
