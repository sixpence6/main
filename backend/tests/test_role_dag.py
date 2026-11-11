from app.config import Settings
from app.workflow import contracts, prompts
from app.workflow.role_graph import ACTIVE_ROLE_IDS, PERSONAS, ROLE_DAG, assert_acyclic


def test_active_team_has_four_personas_and_optional_reference_work():
    assert set(PERSONAS) == {"appearance", "planner", "maker", "reference"}
    assert set(ACTIVE_ROLE_IDS) == {
        "requirements_assistant",
        "reference_analyst",
        "visual_planner",
        "generation_engineer",
    }
    assert set(prompts.ROLE_INCREMENTS) == set(ACTIVE_ROLE_IDS)
    assert set(prompts.PERSONA_BY_ROLE) == set(ACTIVE_ROLE_IDS)
    assert set(contracts.ENVELOPES) == set(ACTIVE_ROLE_IDS)
    assert Settings(_env_file=None).workflow_role_policy == "collaborative"


def test_role_dag_is_acyclic_and_reference_branch_is_optional():
    assert_acyclic()
    optional = {(e["from"], e["to"]) for e in ROLE_DAG["edges"] if e.get("optional")}
    assert optional == {("reference_analyst", "requirements_assistant")}
    forward = {(e["from"], e["to"]) for e in ROLE_DAG["edges"]}
    assert ("requirements_assistant", "visual_planner") in forward
    assert ("visual_planner", "generation_engineer") in forward


def test_current_team_has_no_user_gates_and_delivers_automatically():
    assert ROLE_DAG["user_gates"] == ()
    assert any(step["step"] == "S10" for step in ROLE_DAG["program_steps"])


def test_retired_roles_are_only_available_through_legacy_contracts():
    for role in (
        "intent_router",
        "conversation_router",
        "requirements_consultant",
        "creative_planner",
        "visual_director",
        "delivery_manager",
        "output_reviewer",
        "quality_compliance",
    ):
        assert role not in ACTIVE_ROLE_IDS
        assert role in contracts.LEGACY_ENVELOPES
        assert role in prompts.LEGACY_ROLE_INCREMENTS
        try:
            prompts.build_system_prompt(role, allow_retired=False)
        except KeyError:
            pass
        else:
            raise AssertionError("retired role must not be in the active prompt registry")
    for role in ACTIVE_ROLE_IDS:
        prompt = prompts.build_system_prompt(role)
        assert "task.role" in prompt and "【程序依赖】" in prompt
