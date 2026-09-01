"""Offline tests for how run_place/resume_place name the reason for a pause.

These pin the fix for a review finding on Task 12: ``resume_place`` originally never
applied the ``policy.last_hits`` override that ``run_place`` did, so any interrupt
returned from a resume (e.g. approving ``commit_placement`` walking straight into a
guarded ``dispatch_driver``) came back with ``interrupt_reason=None``. Both entry
points now route through the same ``_result_from``, so this is pinned once against
that shared function and again against each public entry point - all with stand-in
objects (no model call), in the style of tests/hooks/test_audit.py.
"""

from strands import Snapshot

from gleaner.loops.place.runner import _result_from, resume_place, run_place
from gleaner.policy.rules import RuleHit


def _fake_snapshot() -> Snapshot:
    return Snapshot(scope="agent", schema_version="1.0", data={}, app_data={}, created_at="")


def _fake_interrupt(interrupt_id: str = "int-1") -> object:
    return type(
        "FakeInterrupt", (),
        {"id": interrupt_id, "name": "gleaner-escalation", "reason": "some Confirm prompt text"},
    )()


def _fake_interrupted_raw(interrupt_id: str = "int-1") -> object:
    return type(
        "FakeResult", (), {"stop_reason": "interrupt", "interrupts": [_fake_interrupt(interrupt_id)]}
    )()


def _fake_finished_raw() -> object:
    return type("FakeResult", (), {"stop_reason": "end_turn", "interrupts": None})()


def _fake_policy(hits: list[RuleHit]) -> object:
    return type("FakePolicy", (), {"last_hits": hits})()


class _FakeAgent:
    """Stands in for a real ``Agent`` without touching the network."""

    def __init__(self, raw: object) -> None:
        self._raw = raw
        self.loaded_snapshot: Snapshot | None = None

    def take_snapshot(self, preset: str | None = None) -> Snapshot:
        return _fake_snapshot()

    def load_snapshot(self, snapshot: Snapshot) -> None:
        self.loaded_snapshot = snapshot

    async def invoke_async(self, _prompt) -> object:
        return self._raw


# --- _result_from directly ------------------------------------------------------


def test_interrupt_reason_comes_from_policy_last_hits():
    result = _result_from(
        _FakeAgent(_fake_interrupted_raw()),
        _fake_interrupted_raw(),
        _fake_policy([RuleHit("over_value_threshold", "300 kg exceeds the balanced limit.")]),
    )
    assert result.paused
    assert result.interrupt_reason == "over_value_threshold"


def test_interrupt_reason_uses_the_first_hit_when_several_fire_together():
    result = _result_from(
        _FakeAgent(_fake_interrupted_raw()),
        _fake_interrupted_raw(),
        _fake_policy([RuleHit("liability_uncertain", "..."), RuleHit("dietary_conflict", "...")]),
    )
    assert result.interrupt_reason == "liability_uncertain"


def test_interrupt_reason_is_none_when_no_rule_hit_is_recorded():
    """Chosen behaviour for an empty last_hits: None, not a guess.

    An empty ``last_hits`` while paused means the policy instance passed in isn't the
    one that decided this interrupt. Reporting ``None`` rather than fabricating a rule
    name keeps the gap visible instead of hiding it; callers must read ``None`` as
    "reason unknown", never as "no rule fired" (the run would not be paused otherwise).
    """
    result = _result_from(
        _FakeAgent(_fake_interrupted_raw()), _fake_interrupted_raw(), _fake_policy([])
    )
    assert result.paused
    assert result.interrupt_reason is None


def test_not_paused_when_the_run_finished_normally():
    result = _result_from(
        _FakeAgent(_fake_finished_raw()), _fake_finished_raw(),
        _fake_policy([RuleHit("over_value_threshold", "irrelevant here")]),
    )
    assert not result.paused
    assert result.interrupt_reason is None


# --- through the public entry points, proving the wiring isn't just in _result_from ---


async def test_run_place_names_the_reason():
    policy = _fake_policy([RuleHit("over_value_threshold", "...")])
    agent = _FakeAgent(_fake_interrupted_raw())
    result = await run_place(agent, "prompt", policy)
    assert result.paused
    assert result.interrupt_reason == "over_value_threshold"


async def test_resume_place_also_names_the_reason_not_just_run_place():
    """The exact regression: resume_place must apply the same override run_place does."""
    policy = _fake_policy([RuleHit("tight_window", "...")])
    agent = _FakeAgent(_fake_interrupted_raw("int-2"))
    result = await resume_place(
        agent, dict(scope="agent", schema_version="1.0", data={}, app_data={}, created_at=""),
        "int-1", {"decision": "approve", "by": "Ama", "role": "coordinator"}, policy,
    )
    assert result.paused
    assert result.interrupt_reason == "tight_window"
