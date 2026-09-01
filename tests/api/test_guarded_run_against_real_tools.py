"""What the real tools and the real guards do to the ledger, driven through the API.

The gap these close. Every other API test injects an ``OfferRunner`` that returns a
canned ``PlaceRunResult`` and appends canned ledger entries, so nothing exercised
what ``build_place_tools`` and the policy layer actually do to ``state.ledger``
during a held run. Three defects lived in exactly that blind spot, all of them found
by running the real API against Bedrock rather than by any of the 254 tests:

* a driver was dispatched for a placement the policy had held;
* ``/api/inbox`` reported ``"rule": "unknown"`` with an empty ``rule_hits`` for an
  escalation the policy could name precisely;
* ``prompt`` came back as the Python repr of the interrupt list, interrupt ids and
  all.

So the runner injected here is still a stand-in for the *model* - no test may reach
Bedrock - but nothing else is stubbed. The genuine ``build_place_tools``, the genuine
``PlacementDependencyGuard``, the genuine ``EscalationPolicy`` and the genuine
``_result_from`` all run, against the real simulated city, in the real order the SDK
runs them. What is scripted is only which tool calls the model proposes, which is the
one thing a test cannot afford to leave to a model.

The scenario is the live failure, reproduced arithmetically: 100 kg of chilled greens
into Northside (pan-5, 226.7 of 400 kg already used) fills 82% of the cold store,
against balanced's 80%. ``capacity_overrun`` fires on the commit and nothing fires on
the dispatch that follows it - which is precisely why the dispatch went through.
"""

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from strands import Snapshot
from strands.interventions import Confirm, Deny, Proceed

from gleaner.api.app import create_app
from gleaner.loops.place.runner import _result_from, build_default_policy
from gleaner.loops.place.tools import build_place_tools
from gleaner.policy.dependency import PlacementDependencyGuard

OFFER = {
    "offer_id": "off-1",
    "donor_id": "don-5",                       # Sysco: 5 previous donations, not novel
    "lots": [{"description": "salad greens", "weight_kg": 100.0, "temp_class": "chilled"}],
    "ready_at": "2026-09-01T12:00:00+00:00",
    "pickup_address": "Strip District, Pittsburgh, PA",
    "lat": 40.45,
    "lon": -79.97,
    "minutes_out_of_refrigeration": 20.0,
}

COMMIT = ("commit_placement", {
    "offer_id": "off-1", "pantry_id": "pan-5", "driver_id": "drv-2",
    "weight_kg": 100.0, "eta": "2026-09-01T12:30:00+00:00", "temp_class": "chilled",
    "dietary_flags": [], "minutes_out_of_refrigeration": 20.0,
    "ready_at_iso": "2026-09-01T12:00:00+00:00", "donor_id": "don-5",
})
DISPATCH = ("dispatch_driver", {
    "driver_id": "drv-2", "offer_id": "off-1", "pickup_address": "Strip District, Pittsburgh, PA",
})
NOTIFY_PANTRY = ("notify_pantry", {
    "pantry_id": "pan-5", "message": "100 kg of greens on its way", "offer_id": "off-1",
})

APPROVE = {"decision": "approve", "by": "Ama", "role": "coordinator", "note": "phoned them"}
REJECT = {"decision": "reject", "by": "Ama", "role": "coordinator", "note": "no room"}

#: Distinguishes "the human refused" from "no human has answered yet". Both cancel the
#: tool; only the second parks the run in the inbox.
_UNANSWERED = object()


class _FakeAgent:
    """Enough of an ``Agent`` for ``_result_from``. Never reaches a model."""

    def take_snapshot(self, preset: str | None = None) -> Snapshot:
        return Snapshot(scope="agent", schema_version="1.0", data={}, app_data={}, created_at="")


def _interrupt(tool_use_id: str, prompt: str, handler: str) -> SimpleNamespace:
    """An interrupt shaped exactly as Strands builds one for a before_tool_call."""
    return SimpleNamespace(
        id=f"v1:before_tool_call:{tool_use_id}:{uuid.uuid5(uuid.NAMESPACE_OID, handler)}",
        name=handler,
        reason=prompt,
    )


def scripted_runner(script, answers=None):
    """An ``OfferRunner`` that proposes ``script`` and runs it through the real stack.

    Mirrors ``strands.interventions.registry``: handlers are consulted in registration
    order, ``Deny`` short-circuits the rest, and a ``Confirm`` whose response fails
    ``evaluate`` cancels the tool. Every call in the script is offered, including ones
    after a pause - that is not an artificial flourish but the shape of the live
    failure, where the model emitted the commit and the dispatch in a single turn and
    both went through ``before_tool_call``.
    """
    answers = answers or {}

    async def runner(state, offer):
        tools = {t.tool_name: t for t in build_place_tools(state.world, state.ledger)}
        policy = build_default_policy(state.world, state.autonomy)
        guard = PlacementDependencyGuard(state.ledger, state.trail)
        interrupts: list[SimpleNamespace] = []

        for index, (name, tool_input) in enumerate(script):
            tool_use_id = f"tooluse_{index}_{name}"
            event = SimpleNamespace(
                tool_use={"name": name, "input": dict(tool_input), "toolUseId": tool_use_id}
            )
            state.trail.record("tool_call", name, dict(tool_input))

            action = guard.before_tool_call(event)
            if isinstance(action, Proceed):
                action = policy.before_tool_call(event)

            if isinstance(action, Deny):
                continue
            if isinstance(action, Confirm):
                answer = answers.get(name, _UNANSWERED)
                if answer is _UNANSWERED:
                    interrupts.append(_interrupt(tool_use_id, action.prompt, policy.name))
                    continue
                if not action.evaluate(answer):
                    continue

            tools[name](**tool_input)
            state.trail.record("tool_result", name)

        raw = SimpleNamespace(
            stop_reason="interrupt" if interrupts else "end_turn",
            interrupts=interrupts or None,
        )
        return _result_from(_FakeAgent(), raw, policy)

    return runner


def _client(script, answers=None):
    app = create_app(offer_runner=scripted_runner(script, answers))
    return TestClient(app), app.state.gleaner


def _kinds(ledger):
    return [entry["kind"] for entry in ledger]


# --- DEFECT A: a dispatch is only ever as good as the commit it serves -----------


def test_a_held_commit_leaves_no_dispatch_in_the_ledger():
    """The live failure, exactly: held placement, driver sent anyway."""
    client, _ = _client([COMMIT, DISPATCH])
    body = client.post("/api/offers", json=OFFER).json()

    assert body["status"] == "awaiting_decision"
    assert _kinds(client.get("/api/ledger").json()) == []


def test_a_dispatch_for_an_offer_with_no_commit_at_all_never_runs():
    client, _ = _client([DISPATCH])
    client.post("/api/offers", json=OFFER)
    assert _kinds(client.get("/api/ledger").json()) == []


def test_a_commit_the_human_refused_still_blocks_the_dispatch():
    client, _ = _client([COMMIT, DISPATCH], answers={"commit_placement": REJECT})
    client.post("/api/offers", json=OFFER)
    assert _kinds(client.get("/api/ledger").json()) == []


def test_a_notification_about_a_held_placement_is_never_sent():
    """Same harm one step removed: a pantry clearing shelves for food that was held."""
    client, _ = _client([COMMIT, NOTIFY_PANTRY])
    client.post("/api/offers", json=OFFER)
    assert _kinds(client.get("/api/ledger").json()) == []


def test_an_approved_commit_lets_the_dispatch_through_in_order():
    client, _ = _client([COMMIT, DISPATCH], answers={"commit_placement": APPROVE})
    body = client.post("/api/offers", json=OFFER).json()

    assert body["status"] == "placed"
    assert body["committed_kg"] == 100.0
    ledger = client.get("/api/ledger").json()
    assert _kinds(ledger) == ["commit_placement", "dispatch_driver"]
    assert ledger[1]["driver_id"] == "drv-2"
    assert client.get("/api/impact").json()["placements"] == 1


def test_a_dispatch_for_a_different_offer_is_not_carried_by_this_commit():
    other_offer = ("dispatch_driver", {**DISPATCH[1], "offer_id": "off-OTHER"})
    client, _ = _client([COMMIT, other_offer], answers={"commit_placement": APPROVE})
    client.post("/api/offers", json=OFFER)
    assert _kinds(client.get("/api/ledger").json()) == ["commit_placement"]


def test_the_blocked_dispatch_is_visible_and_says_something_true():
    client, _ = _client([COMMIT, DISPATCH])
    client.post("/api/offers", json=OFFER)

    blocked = [e for e in client.get("/api/timeline").json() if e["kind"] == "blocked"]
    assert len(blocked) == 1
    reason = blocked[0]["detail"]["reason"]
    assert "off-1" in reason
    assert "has not been approved" in reason
    # not a borrowed sentence from the rule that actually held the *commit*
    assert "capacity" not in reason


def test_the_timeline_shows_the_dispatch_attempted_and_never_completed():
    client, _ = _client([COMMIT, DISPATCH])
    client.post("/api/offers", json=OFFER)

    timeline = client.get("/api/timeline").json()
    assert ("tool_call", "dispatch_driver") in [(e["kind"], e["label"]) for e in timeline]
    assert "tool_result" not in {e["kind"] for e in timeline}


# --- DEFECT B: the inbox can name the rule that stopped the run ------------------


def test_the_inbox_names_the_rule_and_lists_every_hit():
    client, _ = _client([COMMIT, DISPATCH])
    body = client.post("/api/offers", json=OFFER).json()

    assert body["rule"] == "capacity_overrun"
    pending = client.get("/api/inbox").json()
    assert len(pending) == 1
    assert pending[0]["rule"] == "capacity_overrun"
    assert [hit["rule"] for hit in pending[0]["rule_hits"]] == ["capacity_overrun"]
    assert "82%" in pending[0]["rule_hits"][0]["detail"]
    assert "balanced allows 80%" in pending[0]["rule_hits"][0]["detail"]


def test_the_rule_survives_a_clean_guarded_call_later_in_the_same_turn():
    """The mechanism of the defect: the dispatch's clean verdict blanked last_hits.

    A notify_pantry naming the committed offer passes both guards, so the escalation
    policy really does decide a second, clean call after the one that paused the run.
    """
    client, _ = _client([COMMIT, NOTIFY_PANTRY], answers={"notify_pantry": APPROVE})
    body = client.post("/api/offers", json=OFFER).json()
    assert body["rule"] == "capacity_overrun"
    assert client.get("/api/inbox").json()[0]["rule"] == "capacity_overrun"


# --- DEFECT C: the prompt is the question, not the plumbing ----------------------


def test_the_inbox_prompt_is_the_question_a_coordinator_is_meant_to_read():
    client, state = _client([COMMIT, DISPATCH])
    body = client.post("/api/offers", json=OFFER).json()
    prompt = client.get("/api/inbox").json()[0]["prompt"]

    assert prompt == (
        "Placement would fill 82% of the store; balanced allows 80%. "
        "Approve this commit placement?"
    )
    # the interrupt id is still parked, because the resume path needs it
    assert state.inbox.get(body["decision_id"]).interrupt_id.startswith("v1:before_tool_call:")


def test_the_inbox_prompt_leaks_no_interrupt_id_and_no_python_repr():
    client, state = _client([COMMIT, DISPATCH])
    body = client.post("/api/offers", json=OFFER).json()
    prompt = client.get("/api/inbox").json()[0]["prompt"]

    assert state.inbox.get(body["decision_id"]).interrupt_id not in prompt
    for leak in ("v1:before_tool_call", "tooluse_", "namespace(", "[{", "}]",
                 "'id':", "'reason':", "'response':", "gleaner-escalation"):
        assert leak not in prompt
