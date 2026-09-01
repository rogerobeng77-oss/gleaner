from datetime import datetime, timezone

import pytest

from gleaner.adapters.simulated import SimulatedCity
from gleaner.domain.types import AutonomyLevel, DecisionOutcome
from gleaner.hooks.audit import AuditTrail
from gleaner.loops.place.runner import build_place_agent, resume_place, run_place
from gleaner.persistence.inbox import DecisionInbox
from gleaner.policy.escalation import EscalationPolicy
from gleaner.policy.rules import PlacementContext

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
PROMPT = (
    "Giant Eagle has 300 kg of chilled salad greens ready now at 40.44,-79.99. "
    "Find a pantry with find_pantries, pick the single best one, then call commit_placement "
    "exactly once, in one call, for the full 300 kg together, using driver drv-1 and eta "
    "2026-09-01T13:00:00+00:00. Do not split this shipment across more than one "
    "commit_placement call. If that single commit_placement call comes back refused or "
    "requiring approval you do not receive, stop there - do not retry commit_placement "
    "against a different pantry, driver, or amount."
)


def _setup(**ctx_overrides):
    base = dict(
        weight_kg=300.0, margin_minutes=180.0, safety_confidence=0.99,
        capacity_fraction=0.2, liability_ok=True, dietary_ok=True,
        donor_is_novel=False, has_viable_match=True,
    )
    ctx = PlacementContext(**{**base, **ctx_overrides})
    world = SimulatedCity(seed=412, now=NOW)
    ledger: list[dict] = []
    policy = EscalationPolicy(AutonomyLevel.BALANCED, lambda _inp: ctx)
    trail = AuditTrail()
    return build_place_agent(world, ledger, policy, trail), ledger, policy, trail


async def test_heavy_load_pauses_for_a_human():
    agent, ledger, policy, _ = _setup()
    result = await run_place(agent, PROMPT, policy)
    assert result.paused
    assert result.interrupt_id
    assert result.interrupt_reason == "over_value_threshold"
    assert result.snapshot
    assert ledger == []          # nothing committed while awaiting a decision


async def test_approval_resumes_and_commits():
    agent, ledger, policy, _ = _setup()
    result = await run_place(agent, PROMPT, policy)
    assert result.paused
    resumed = await resume_place(
        agent, result.snapshot, result.interrupt_id,
        {"decision": "approve", "by": "Ama", "role": "coordinator", "note": "confirmed by phone"},
        policy,
    )
    assert not resumed.paused
    assert any(e["kind"] == "commit_placement" for e in ledger)


async def test_rejection_resumes_without_committing():
    agent, ledger, policy, _ = _setup()
    result = await run_place(agent, PROMPT, policy)
    resumed = await resume_place(
        agent, result.snapshot, result.interrupt_id,
        {"decision": "reject", "by": "Ama", "role": "coordinator", "note": "freezer failed inspection"},
        policy,
    )
    assert not resumed.paused
    assert not any(e["kind"] == "commit_placement" for e in ledger)


async def test_routine_placement_never_pauses():
    agent, ledger, policy, _ = _setup(weight_kg=10.0)
    result = await run_place(agent, PROMPT.replace("300 kg", "10 kg"), policy)
    assert not result.paused
    assert any(e["kind"] == "commit_placement" for e in ledger)


async def test_the_inbox_can_carry_a_pause_across_agent_instances():
    from gleaner.domain.models import Decision
    agent, ledger, policy, _ = _setup()
    inbox = DecisionInbox()
    result = await run_place(agent, PROMPT, policy)

    inbox.open(
        Decision(decision_id="dec-1", offer_id="off-1",
                 rule=result.interrupt_reason, prompt="Approve?"),
        interrupt_id=result.interrupt_id,
        snapshot=result.snapshot,
        rule_hits=[{"rule": h.rule, "detail": h.detail} for h in policy.last_hits],
    )
    answered = inbox.answer("dec-1", DecisionOutcome.APPROVE,
                            by="Ama", role="coordinator", note="ok")

    fresh_agent, fresh_ledger, fresh_policy, _ = _setup()
    resumed = await resume_place(
        fresh_agent, answered.snapshot, answered.interrupt_id,
        {"decision": "approve", "by": answered.decision.by,
         "role": answered.decision.role, "note": answered.decision.note},
        fresh_policy,
    )
    assert not resumed.paused
    assert any(e["kind"] == "commit_placement" for e in fresh_ledger)


async def test_audit_trail_captures_the_run():
    agent, _, policy, trail = _setup()
    await run_place(agent, PROMPT, policy)
    kinds = {e.kind for e in trail.entries}
    assert "tool_call" in kinds
