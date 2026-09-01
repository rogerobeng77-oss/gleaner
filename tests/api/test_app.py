from fastapi.testclient import TestClient

from gleaner.api.app import create_app
from gleaner.domain.models import Decision, DonationOffer
from gleaner.loops.place.runner import PlaceRunResult


def _client(resumer=None):
    """An app with a stub resumer, so answering a decision never reaches Bedrock.

    The default stub commits nothing, which is the right neutral for tests about
    recording a decision. Tests about the resume itself pass their own.
    """
    async def noop_resume(state, pending):
        return PlaceRunResult(False, None, None, None, "resumed")

    app = create_app(decision_resumer=resumer or noop_resume)
    return TestClient(app), app.state.gleaner


OFFER = {
    "offer_id": "off-1",
    "donor_id": "don-1",
    "lots": [{"description": "salad", "weight_kg": 40.0, "temp_class": "chilled"}],
    "ready_at": "2026-09-01T12:00:00+00:00",
    "pickup_address": "Giant Eagle, Pittsburgh, PA",
    "lat": 40.44,
    "lon": -79.99,
}


def _stub_client(result: PlaceRunResult, commits: list[dict] | None = None):
    """An app whose PLACE loop is a stub, so no test ever reaches Bedrock.

    The stub writes into the same ledger a real run's tools would, because the route
    reads committed weight back out of it.
    """
    async def runner(state, offer):
        for entry in commits or []:
            state.ledger.append(entry)
        return result

    async def noop_resume(state, pending):
        return PlaceRunResult(False, None, None, None, "resumed")

    app = create_app(offer_runner=runner, decision_resumer=noop_resume)
    return TestClient(app), app.state.gleaner


def test_health():
    client, _ = _client()
    assert client.get("/health").json() == {"status": "ok"}


def test_world_exposes_pantries_and_drivers():
    client, _ = _client()
    body = client.get("/api/world").json()
    assert len(body["pantries"]) >= 6
    assert len(body["drivers"]) >= 4


def test_inbox_starts_empty():
    client, _ = _client()
    assert client.get("/api/inbox").json() == []


def test_inbox_lists_a_pending_decision_with_its_reasons():
    client, state = _client()
    state.inbox.open(
        Decision(decision_id="dec-1", offer_id="off-1", rule="tight_window", prompt="Approve?"),
        interrupt_id="int-1", snapshot={},
        rule_hits=[{"rule": "tight_window", "detail": "only 20 minutes of slack"}],
    )
    body = client.get("/api/inbox").json()
    assert body[0]["decision_id"] == "dec-1"
    assert body[0]["rule_hits"][0]["detail"] == "only 20 minutes of slack"


def test_answering_a_decision_records_the_human():
    client, state = _client()
    state.inbox.open(
        Decision(decision_id="dec-1", offer_id="off-1", rule="tight_window", prompt="Approve?"),
        interrupt_id="int-1", snapshot={}, rule_hits=[],
    )
    response = client.post(
        "/api/inbox/dec-1/answer",
        json={"outcome": "approve", "by": "Ama", "role": "coordinator", "note": "phoned"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "approve"
    assert body["by"] == "Ama"
    assert body["role"] == "coordinator"
    assert body["note"] == "phoned"
    assert body["at"] is not None
    assert client.get("/api/inbox").json() == []


def test_answering_an_unknown_decision_is_404():
    client, _ = _client()
    response = client.post(
        "/api/inbox/nope/answer",
        json={"outcome": "approve", "by": "a", "role": "b", "note": ""},
    )
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_autonomy_can_be_read_and_changed():
    client, _ = _client()
    assert client.get("/api/autonomy").json()["level"] == "balanced"
    put_response = client.put("/api/autonomy", json={"level": "trusted"})
    assert put_response.status_code == 200
    assert put_response.json()["level"] == "trusted"
    assert client.get("/api/autonomy").json()["level"] == "trusted"


def test_invalid_autonomy_level_is_rejected():
    client, _ = _client()
    assert client.put("/api/autonomy", json={"level": "reckless"}).status_code == 422


def test_autonomy_response_includes_the_active_thresholds():
    client, _ = _client()
    body = client.get("/api/autonomy").json()
    assert body["thresholds"]["max_autonomous_kg"] == 150.0


def test_impact_summary_is_zeroed_at_start():
    client, _ = _client()
    assert client.get("/api/impact").json()["placements"] == 0


def test_ledger_and_timeline_start_empty():
    client, _ = _client()
    assert client.get("/api/ledger").json() == []
    assert client.get("/api/timeline").json() == []


def _commit(offer_id="off-1", weight_kg=40.0):
    return {"kind": "commit_placement", "offer_id": offer_id, "weight_kg": weight_kg,
            "at": "2026-09-01T12:00:00+00:00"}


def test_a_placed_offer_fills_the_ledger_timeline_and_impact():
    """These three endpoints were empty by construction: nothing ever wrote them."""
    client, state = _stub_client(
        PlaceRunResult(False, None, None, None, "placed it"), commits=[_commit()]
    )
    body = client.post("/api/offers", json=OFFER).json()
    assert body["status"] == "placed"
    assert body["committed_kg"] == 40.0

    assert client.get("/api/ledger").json()
    assert client.get("/api/timeline").json()
    impact = client.get("/api/impact").json()
    assert impact["placements"] == 1
    assert impact["kg_rescued"] == 40.0
    assert impact["meals"] == 73                       # 40 / 0.544
    assert state.inbox.pending() == []


def test_an_escalated_offer_lands_in_the_inbox_with_its_rule():
    client, state = _stub_client(
        PlaceRunResult(
            True, "int-1", "tight_window", {"scope": "agent"}, "needs a human",
            rule_hits=[{"rule": "tight_window", "detail": "Only 12 minutes of slack."}],
        )
    )
    body = client.post("/api/offers", json=OFFER).json()
    assert body["status"] == "awaiting_decision"
    assert body["rule"] == "tight_window"

    pending = client.get("/api/inbox").json()
    assert len(pending) == 1
    assert pending[0]["decision_id"] == body["decision_id"]
    assert pending[0]["rule"] == "tight_window"
    assert pending[0]["rule_hits"][0]["detail"] == "Only 12 minutes of slack."
    parked = state.inbox.get(body["decision_id"])
    assert parked.interrupt_id == "int-1"
    assert parked.snapshot == {"scope": "agent"}       # resumable, not just reported


def test_an_escalated_offer_counts_no_impact():
    """A paused run has moved no food. Counting it would inflate the headline number."""
    client, _ = _stub_client(PlaceRunResult(True, "int-1", "novel_donor", {}, "?"))
    client.post("/api/offers", json=OFFER)
    assert client.get("/api/impact").json()["placements"] == 0


def test_a_run_that_committed_and_then_paused_counts_what_it_committed():
    """The ledger and the impact figures must not describe the same run differently.

    A run can clear the guard on commit_placement and pause on a later guarded call.
    Keying impact off ``paused`` showed food in GET /api/ledger and zero placements
    in GET /api/impact for that run.
    """
    client, _ = _stub_client(
        PlaceRunResult(True, "int-2", "novel_donor", {"scope": "agent"}, "needs a human"),
        commits=[_commit(weight_kg=40.0)],
    )
    body = client.post("/api/offers", json=OFFER).json()
    assert body["status"] == "awaiting_decision"       # still parked for a human
    assert body["committed_kg"] == 40.0

    ledger = client.get("/api/ledger").json()
    impact = client.get("/api/impact").json()
    assert [e["weight_kg"] for e in ledger] == [40.0]
    assert impact["placements"] == 1
    assert impact["kg_rescued"] == 40.0


def test_a_paused_run_that_committed_nothing_still_counts_nothing():
    """The half the old rule got right, kept: no commit in the ledger, no impact."""
    client, _ = _stub_client(PlaceRunResult(True, "int-3", "tight_window", {}, "?"))
    body = client.post("/api/offers", json=OFFER).json()
    assert body["committed_kg"] == 0.0
    assert client.get("/api/ledger").json() == []
    assert client.get("/api/impact").json()["placements"] == 0


def test_a_paused_run_counts_only_this_offer_s_commits():
    client, _ = _stub_client(
        PlaceRunResult(True, "int-4", "novel_donor", {}, "?"),
        commits=[_commit(weight_kg=40.0), _commit(offer_id="off-other", weight_kg=999.0)],
    )
    client.post("/api/offers", json=OFFER)
    assert client.get("/api/impact").json()["kg_rescued"] == 40.0


def test_an_offer_the_agent_declined_to_place_is_recorded_as_such():
    client, _ = _stub_client(PlaceRunResult(False, None, None, None, "found nowhere"))
    body = client.post("/api/offers", json=OFFER).json()
    assert body["status"] == "no_placement"
    assert client.get("/api/impact").json()["placements"] == 0
    assert client.get("/api/timeline").json()          # still visible on the timeline


def test_impact_counts_only_the_weight_committed_for_this_offer():
    client, _ = _stub_client(
        PlaceRunResult(False, None, None, None, "placed it"),
        commits=[_commit(weight_kg=40.0), _commit(offer_id="off-other", weight_kg=999.0)],
    )
    client.post("/api/offers", json=OFFER)
    assert client.get("/api/impact").json()["kg_rescued"] == 40.0


# --- the submitted offer must enter the world, or the guard has nothing to check ---


def test_a_submitted_offer_is_resolvable_in_the_world_afterwards():
    """POST /api/offers must register the offer, not merely parse it.

    The escalation policy reads the offer record to check the agent's account of the
    food against the donor's. An offer that never reached the registry resolves to
    None and that whole defence is skipped.
    """
    client, state = _stub_client(PlaceRunResult(False, None, None, None, "done"))
    assert state.world.offer("off-1") is None
    client.post("/api/offers", json=OFFER)
    registered = state.world.offer("off-1")
    assert registered is not None
    assert registered.offer_id == "off-1"
    assert registered.lots[0].weight_kg == 40.0


def test_registration_happens_before_the_loop_runs_not_after():
    """Ordering is the whole point: a guard consulted mid-run needs it already there."""
    seen: list[bool] = []

    async def runner(state, offer):
        seen.append(state.world.offer(offer.offer_id) is not None)
        return PlaceRunResult(False, None, None, None, "done")

    app = create_app(offer_runner=runner)
    TestClient(app).post("/api/offers", json=OFFER)
    assert seen == [True]


def test_a_rejected_offer_never_reaches_the_registry():
    client, state = _stub_client(PlaceRunResult(False, None, None, None, ""))
    client.post("/api/offers", json={**OFFER, "lots": []})
    assert state.world.offer("off-1") is None


def test_a_malformed_offer_is_rejected_before_any_agent_runs():
    client, state = _stub_client(PlaceRunResult(False, None, None, None, ""))
    response = client.post("/api/offers", json={**OFFER, "lots": []})
    assert response.status_code == 422
    assert state.ledger == []


def test_approving_a_decision_resumes_the_run_and_commits():
    """The defect this pins: answering recorded the human and dropped the snapshot.

    ``resume_place`` existed and was tested directly, but nothing on the API path
    ever called it, so an approved 400 kg placement cleared the inbox and committed
    nothing. The console said the decision was recorded; the ledger stayed empty.
    """
    resumed_with = {}

    async def resumer(state, pending):
        resumed_with["interrupt_id"] = pending.interrupt_id
        resumed_with["snapshot"] = pending.snapshot
        resumed_with["outcome"] = pending.decision.outcome.value
        state.ledger.append(_commit(weight_kg=400.0))
        return PlaceRunResult(False, None, None, None, "committed after approval")

    client, state = _client(resumer=resumer)
    state.world.register_offer(DonationOffer(**{**OFFER, "lots": [
        {"description": "sausages", "weight_kg": 400.0, "temp_class": "chilled"}]}))
    state.inbox.open(
        Decision(decision_id="dec-1", offer_id="off-1", rule="over_value_threshold",
                 prompt="Approve?"),
        interrupt_id="int-9", snapshot={"scope": "agent"}, rule_hits=[],
    )

    client.post("/api/inbox/dec-1/answer",
                json={"outcome": "approve", "by": "Ama", "role": "coordinator", "note": ""})

    assert resumed_with == {"interrupt_id": "int-9", "snapshot": {"scope": "agent"},
                            "outcome": "approve"}
    assert any(e["kind"] == "commit_placement" for e in state.ledger)
    impact = client.get("/api/impact").json()
    assert impact["placements"] == 1
    assert impact["kg_rescued"] == 400.0


def test_a_resumed_run_that_pauses_again_goes_back_into_the_inbox():
    async def resumer(state, pending):
        return PlaceRunResult(True, "int-10", "novel_donor", {"scope": "agent"},
                              "still needs a human",
                              rule_hits=[{"rule": "novel_donor", "detail": "first offer"}])

    client, state = _client(resumer=resumer)
    state.inbox.open(
        Decision(decision_id="dec-1", offer_id="off-1", rule="tight_window", prompt="?"),
        interrupt_id="int-9", snapshot={"scope": "agent"}, rule_hits=[],
    )
    client.post("/api/inbox/dec-1/answer",
                json={"outcome": "approve", "by": "Ama", "role": "coordinator", "note": ""})

    pending = client.get("/api/inbox").json()
    assert len(pending) == 1
    assert pending[0]["rule"] == "novel_donor"
    assert state.inbox.get(pending[0]["decision_id"]).interrupt_id == "int-10"


def test_a_resumed_commit_is_not_counted_twice():
    """An offer can reach the ledger before the pause and again after the approval."""
    async def resumer(state, pending):
        state.ledger.append(_commit(weight_kg=60.0))
        return PlaceRunResult(False, None, None, None, "done")

    client, state = _client(resumer=resumer)
    state.ledger.append(_commit(weight_kg=40.0))
    state.inbox.open(
        Decision(decision_id="dec-1", offer_id="off-1", rule="tight_window", prompt="?"),
        interrupt_id="int-9", snapshot={}, rule_hits=[],
    )
    client.post("/api/inbox/dec-1/answer",
                json={"outcome": "approve", "by": "Ama", "role": "coordinator", "note": ""})

    impact = client.get("/api/impact").json()
    assert impact["placements"] == 1                   # one offer, not two
    assert impact["kg_rescued"] == 100.0               # 40 committed, then 60 more
