# tests/policy/test_escalation.py
from types import SimpleNamespace

from strands.interventions import Confirm, Proceed

from gleaner.domain.types import AutonomyLevel
from gleaner.policy.escalation import (
    GUARDED_TOOLS,
    UNASSESSABLE_RULE,
    EscalationPolicy,
    decision_is_approval,
    rules_that_paused,
    tool_use_id_of,
)
from gleaner.policy.rules import PlacementContext


def _ctx(**kw):
    base = dict(
        weight_kg=10.0, margin_minutes=180.0, safety_confidence=0.99,
        capacity_fraction=0.2, liability_ok=True, dietary_ok=True,
        donor_is_novel=False, has_viable_match=True,
    )
    return PlacementContext(**{**base, **kw})


def _event(tool_name, tool_input=None):
    return SimpleNamespace(tool_use={"name": tool_name, "input": tool_input or {}})


def _policy(ctx=None, level=AutonomyLevel.BALANCED):
    return EscalationPolicy(
        level=level, context_provider=lambda _tool, _inp: ctx or _ctx()
    )


def test_unguarded_tool_always_proceeds():
    action = _policy(_ctx(liability_ok=False)).before_tool_call(_event("check_capacity"))
    assert isinstance(action, Proceed)


def test_guarded_tools_cover_every_consequential_action():
    assert GUARDED_TOOLS == frozenset(
        {"commit_placement", "dispatch_driver", "notify_donor", "notify_pantry", "send_outreach"}
    )


def test_routine_guarded_call_proceeds_without_a_human():
    action = _policy().before_tool_call(_event("commit_placement"))
    assert isinstance(action, Proceed)


def test_rule_violation_requests_confirmation():
    action = _policy(_ctx(margin_minutes=2.0)).before_tool_call(_event("commit_placement"))
    assert isinstance(action, Confirm)
    assert action.reason == "tight_window"


def test_confirm_prompt_explains_itself_in_plain_english():
    action = _policy(_ctx(margin_minutes=2.0)).before_tool_call(_event("commit_placement"))
    assert "minutes of slack" in action.prompt


def test_confirm_never_uses_the_permissive_default_evaluate():
    action = _policy(_ctx(liability_ok=False)).before_tool_call(_event("commit_placement"))
    assert action.evaluate is decision_is_approval


def test_non_negotiable_rule_wins_even_at_trusted():
    action = _policy(_ctx(dietary_ok=False), AutonomyLevel.TRUSTED).before_tool_call(
        _event("commit_placement")
    )
    assert isinstance(action, Confirm)
    assert action.reason == "dietary_conflict"


def test_policy_records_what_fired_for_the_inbox():
    policy = _policy(_ctx(margin_minutes=2.0, weight_kg=999.0))
    policy.before_tool_call(_event("commit_placement"))
    assert {hit.rule for hit in policy.last_hits} == {"tight_window", "over_value_threshold"}


def test_last_hits_cleared_on_a_clean_call():
    policy = _policy(_ctx(margin_minutes=2.0))
    policy.before_tool_call(_event("commit_placement"))
    policy.context_provider = lambda _tool, _inp: _ctx()
    policy.before_tool_call(_event("commit_placement"))
    assert policy.last_hits == []


def test_approval_predicate_accepts_only_structured_approval():
    assert decision_is_approval({"decision": "approve"})
    assert not decision_is_approval({"decision": "reject"})
    assert not decision_is_approval("yes")
    assert not decision_is_approval(True)
    assert not decision_is_approval(None)


def test_handler_overrides_before_tool_call_at_class_level():
    # The framework only detects class-level overrides.
    assert "before_tool_call" in EscalationPolicy.__dict__


# --- the policy asks about a named tool, and copes when the answer is "I cannot" ---


def test_the_policy_tells_the_provider_which_tool_it_is_judging():
    """Without the name the provider cannot tell a placement from a dispatch."""
    seen: list[str] = []

    policy = EscalationPolicy(
        level=AutonomyLevel.BALANCED,
        context_provider=lambda tool, _inp: seen.append(tool) or _ctx(),
    )
    policy.before_tool_call(_event("dispatch_driver", {"driver_id": "drv-1"}))
    assert seen == ["dispatch_driver"]


def test_an_undescribable_guarded_tool_escalates_rather_than_proceeding():
    policy = EscalationPolicy(
        level=AutonomyLevel.TRUSTED, context_provider=lambda _tool, _inp: None
    )
    action = policy.before_tool_call(_event("send_outreach"))
    assert isinstance(action, Confirm)
    assert action.reason == UNASSESSABLE_RULE
    assert action.evaluate is decision_is_approval


def test_the_undescribable_reason_reaches_the_inbox_rather_than_going_missing():
    """``_result_from`` reads ``last_hits`` to name the pause; it must not be empty."""
    policy = EscalationPolicy(
        level=AutonomyLevel.TRUSTED, context_provider=lambda _tool, _inp: None
    )
    policy.before_tool_call(_event("send_outreach"))
    assert [hit.rule for hit in policy.last_hits] == [UNASSESSABLE_RULE]
    assert "send outreach" in policy.last_hits[0].detail


def test_the_undescribable_prompt_claims_nothing_about_the_call():
    policy = EscalationPolicy(
        level=AutonomyLevel.CONSERVATIVE, context_provider=lambda _tool, _inp: None
    )
    action = policy.before_tool_call(_event("send_outreach"))
    assert action.prompt == (
        "Gleaner has no way to judge the risk of a send outreach, so it will not "
        "make one unattended. Approve this send outreach?"
    )


def test_an_unguarded_tool_is_never_even_described():
    """The provider must not be consulted for a read-only tool."""
    calls: list[str] = []

    policy = EscalationPolicy(
        level=AutonomyLevel.CONSERVATIVE,
        context_provider=lambda tool, _inp: calls.append(tool) or _ctx(),
    )
    assert isinstance(policy.before_tool_call(_event("find_pantries")), Proceed)
    assert calls == []


# --- naming the pause after a *later* guarded call has been decided ---------------
#
# Live-run defect: a model turn held both an escalating ``commit_placement`` and a
# ``dispatch_driver``. Both went through ``before_tool_call``; the dispatch passed
# cleanly and blanked ``last_hits``, so the inbox showed ``rule: unknown`` for a
# capacity_overrun it could have named exactly.


def _event_with_id(tool_name, tool_use_id, tool_input=None):
    return SimpleNamespace(
        tool_use={"name": tool_name, "input": tool_input or {}, "toolUseId": tool_use_id}
    )


def _interrupt_id(tool_use_id):
    return f"v1:before_tool_call:{tool_use_id}:8ed8b3ec-b1a5-5d3f-9f39-0d4a2a4c1f00"


def test_an_escalation_is_recorded_against_the_call_that_raised_it():
    policy = _policy(_ctx(capacity_fraction=0.87))
    policy.before_tool_call(_event_with_id("commit_placement", "tooluse_abc"))
    assert [h.rule for h in policy.escalations["tooluse_abc"]] == ["capacity_overrun"]


def test_a_clean_later_call_does_not_erase_the_escalation_that_paused_the_run():
    policy = _policy(_ctx(capacity_fraction=0.87))
    policy.before_tool_call(_event_with_id("commit_placement", "tooluse_abc"))
    policy.context_provider = lambda _tool, _inp: _ctx()
    assert isinstance(
        policy.before_tool_call(_event_with_id("dispatch_driver", "tooluse_def")), Proceed
    )

    assert policy.last_hits == []                       # last_hits is still the last call
    hits = rules_that_paused(policy, _interrupt_id("tooluse_abc"))
    assert [h.rule for h in hits] == ["capacity_overrun"]
    assert "87%" in hits[0].detail


def test_a_clean_call_is_never_recorded_as_an_escalation():
    policy = _policy()
    policy.before_tool_call(_event_with_id("commit_placement", "tooluse_abc"))
    assert policy.escalations == {}


def test_tool_use_id_is_read_out_of_a_before_tool_call_interrupt_id():
    assert tool_use_id_of(_interrupt_id("tooluse_abc")) == "tooluse_abc"


def test_an_interrupt_id_of_another_shape_yields_no_tool_use_id():
    for value in ("int-1", "", "v1:after_tool_call:tooluse_abc:uuid", None, 7):
        assert tool_use_id_of(value) is None


def test_rules_that_paused_falls_back_to_last_hits_for_an_unmatched_interrupt():
    """A policy that never saw this interrupt still answers what it last decided."""
    policy = _policy(_ctx(margin_minutes=2.0))
    policy.before_tool_call(_event("commit_placement"))          # no toolUseId at all
    assert [h.rule for h in rules_that_paused(policy, "int-1")] == ["tight_window"]


def test_rules_that_paused_is_empty_when_the_policy_decided_nothing():
    assert rules_that_paused(_policy(), _interrupt_id("tooluse_abc")) == []
