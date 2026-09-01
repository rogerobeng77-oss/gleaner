# tests/policy/test_dependency.py
"""The prerequisite boundary, tested against a real ledger.

The live-run defect these pin: the policy held ``commit_placement`` on
``capacity_overrun``, ``dispatch_driver`` was judged on its own merits in the same
model turn, found unremarkable, and executed - so a volunteer was sent to collect
120 kg of pork that had nowhere to go.
"""

from types import SimpleNamespace

import pytest
from strands.interventions import Deny, Proceed

from gleaner.hooks.audit import AuditTrail
from gleaner.policy.dependency import DEPENDENT_TOOLS, PlacementDependencyGuard


def _event(tool_name, tool_input=None):
    return SimpleNamespace(tool_use={"name": tool_name, "input": tool_input})


def _commit(offer_id="off-1"):
    return {"kind": "commit_placement", "offer_id": offer_id, "weight_kg": 120.0}


DEPENDENT_CALLS = {
    "dispatch_driver": {"driver_id": "drv-2", "pickup_address": "123 Main St"},
    "notify_pantry": {"pantry_id": "pan-5", "message": "120 kg on its way"},
    "notify_donor": {"donor_id": "don-1", "message": "collected, thank you"},
}


def test_every_tool_that_acts_on_a_placement_is_covered():
    assert DEPENDENT_TOOLS == {"dispatch_driver", "notify_pantry", "notify_donor"}
    assert "commit_placement" not in DEPENDENT_TOOLS      # it is the prerequisite


@pytest.mark.parametrize("tool_name,args", DEPENDENT_CALLS.items())
def test_a_dependent_call_is_refused_when_no_placement_was_committed(tool_name, args):
    guard = PlacementDependencyGuard(ledger=[])
    action = guard.before_tool_call(_event(tool_name, {**args, "offer_id": "off-1"}))
    assert isinstance(action, Deny)


@pytest.mark.parametrize("tool_name,args", DEPENDENT_CALLS.items())
def test_a_dependent_call_proceeds_once_the_placement_is_in_the_ledger(tool_name, args):
    guard = PlacementDependencyGuard(ledger=[_commit("off-1")])
    action = guard.before_tool_call(_event(tool_name, {**args, "offer_id": "off-1"}))
    assert isinstance(action, Proceed)


@pytest.mark.parametrize("tool_name,args", DEPENDENT_CALLS.items())
def test_a_dependent_call_naming_no_offer_is_refused(tool_name, args):
    """Fail closed: a call that names no placement cannot be shown to follow one."""
    guard = PlacementDependencyGuard(ledger=[_commit("off-1")])
    assert isinstance(guard.before_tool_call(_event(tool_name, args)), Deny)


@pytest.mark.parametrize("bad", [None, "", "   ", 7, ["off-1"], {"id": "off-1"}])
def test_an_unusable_offer_id_is_refused_rather_than_coerced(bad):
    guard = PlacementDependencyGuard(ledger=[_commit("off-1")])
    action = guard.before_tool_call(_event("dispatch_driver", {"offer_id": bad}))
    assert isinstance(action, Deny)


def test_a_commit_for_a_different_offer_does_not_authorise_this_dispatch():
    guard = PlacementDependencyGuard(ledger=[_commit("off-OTHER")])
    action = guard.before_tool_call(_event("dispatch_driver", {"offer_id": "off-1"}))
    assert isinstance(action, Deny)


def test_the_guard_reads_the_ledger_live_not_a_copy_taken_at_construction():
    """The commit lands mid-run; the guard must see it without being rebuilt."""
    ledger: list[dict] = []
    guard = PlacementDependencyGuard(ledger)
    assert isinstance(guard.before_tool_call(_event("dispatch_driver", {"offer_id": "off-1"})), Deny)
    ledger.append(_commit("off-1"))
    assert isinstance(
        guard.before_tool_call(_event("dispatch_driver", {"offer_id": "off-1"})), Proceed
    )


def test_a_ledger_entry_that_is_not_a_commit_does_not_count_as_one():
    ledger = [{"kind": "notify_pantry", "offer_id": "off-1", "message": "hello"}]
    guard = PlacementDependencyGuard(ledger)
    assert isinstance(guard.before_tool_call(_event("dispatch_driver", {"offer_id": "off-1"})), Deny)


def test_commit_placement_itself_is_left_to_the_escalation_policy():
    guard = PlacementDependencyGuard(ledger=[])
    action = guard.before_tool_call(_event("commit_placement", {"offer_id": "off-1"}))
    assert isinstance(action, Proceed)


def test_a_read_only_tool_is_never_touched():
    guard = PlacementDependencyGuard(ledger=[])
    assert isinstance(guard.before_tool_call(_event("find_pantries", {})), Proceed)


def test_a_malformed_tool_input_is_refused_rather_than_crashing():
    guard = PlacementDependencyGuard(ledger=[_commit("off-1")])
    assert isinstance(guard.before_tool_call(_event("dispatch_driver", "not a dict")), Deny)
    assert isinstance(guard.before_tool_call(_event("dispatch_driver", None)), Deny)


# --- what the agent and the human are told ---------------------------------------


def test_the_refusal_names_the_offer_and_the_missing_approval():
    guard = PlacementDependencyGuard(ledger=[])
    action = guard.before_tool_call(_event("dispatch_driver", {"offer_id": "off-1"}))
    assert "off-1" in action.reason
    assert "has not been approved" in action.reason
    assert "dispatch driver" in action.reason


def test_the_refusal_borrows_no_sentence_from_the_placement_rule_set():
    """A capacity or liability sentence here would be false about this call."""
    guard = PlacementDependencyGuard(ledger=[])
    action = guard.before_tool_call(_event("dispatch_driver", {"offer_id": "off-1"}))
    for borrowed in ("capacity", "Emerson", "dietary", "slack", "safety confidence"):
        assert borrowed not in action.reason


def test_the_refusal_says_which_argument_is_missing_when_no_offer_is_named():
    guard = PlacementDependencyGuard(ledger=[])
    action = guard.before_tool_call(_event("notify_pantry", {"pantry_id": "pan-5"}))
    assert "offer_id" in action.reason


def test_a_refusal_lands_on_the_timeline_so_it_is_not_a_silent_non_event():
    trail = AuditTrail()
    guard = PlacementDependencyGuard(ledger=[], trail=trail)
    guard.before_tool_call(_event("dispatch_driver", {"offer_id": "off-1"}))
    blocked = [e for e in trail.entries if e.kind == "blocked"]
    assert len(blocked) == 1
    assert blocked[0].detail["offer_id"] == "off-1"
    assert blocked[0].detail["tool"] == "dispatch_driver"
    assert guard.last_block == blocked[0].detail["reason"]


def test_nothing_is_recorded_when_a_call_is_allowed_through():
    trail = AuditTrail()
    guard = PlacementDependencyGuard(ledger=[_commit("off-1")], trail=trail)
    guard.before_tool_call(_event("dispatch_driver", {"offer_id": "off-1"}))
    assert [e for e in trail.entries if e.kind == "blocked"] == []
    assert guard.last_block is None


def test_handler_overrides_before_tool_call_at_class_level():
    # The framework only detects class-level overrides.
    assert "before_tool_call" in PlacementDependencyGuard.__dict__


# --- and that it is actually wired in, ahead of the escalation policy -------------


def test_the_guard_is_registered_in_front_of_the_escalation_policy():
    """Order is load-bearing: Deny short-circuits, so the guard must go first.

    Reads Strands' private registry because the SDK exposes no public accessor for
    an agent's interventions - the same reason ``run_place`` is handed its policy
    rather than reading it off the agent.
    """
    from gleaner.adapters.simulated import SimulatedCity
    from gleaner.domain.types import AutonomyLevel
    from gleaner.loops.place.runner import build_default_policy, build_place_agent

    world = SimulatedCity(seed=412)
    ledger: list[dict] = []
    trail = AuditTrail()
    agent = build_place_agent(
        world, ledger, build_default_policy(world, AutonomyLevel.BALANCED), trail
    )

    handlers = agent._intervention_registry.handlers
    assert [h.name for h in handlers] == ["gleaner-placement-dependency", "gleaner-escalation"]
    assert handlers[0].ledger is ledger      # the live ledger, not a copy
    assert handlers[0].trail is trail
