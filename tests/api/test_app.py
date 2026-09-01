from fastapi.testclient import TestClient

from gleaner.api.app import create_app
from gleaner.domain.models import Decision


def _client():
    app = create_app()
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
    assert response.json()["outcome"] == "approve"
    assert client.get("/api/inbox").json() == []


def test_answering_an_unknown_decision_is_404():
    client, _ = _client()
    response = client.post(
        "/api/inbox/nope/answer",
        json={"outcome": "approve", "by": "a", "role": "b", "note": ""},
    )
    assert response.status_code == 404


def test_autonomy_can_be_read_and_changed():
    client, _ = _client()
    assert client.get("/api/autonomy").json()["level"] == "balanced"
    assert client.put("/api/autonomy", json={"level": "trusted"}).status_code == 200
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
