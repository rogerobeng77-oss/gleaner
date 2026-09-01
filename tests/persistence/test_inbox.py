import pytest

from gleaner.domain.models import Decision
from gleaner.domain.types import DecisionOutcome
from gleaner.persistence.inbox import DecisionInbox


def _decision(did="dec-1"):
    return Decision(decision_id=did, offer_id="off-1", rule="tight_window", prompt="Approve?")


def test_opened_decision_appears_in_pending():
    inbox = DecisionInbox()
    inbox.open(_decision(), interrupt_id="int-1", snapshot={"a": 1}, rule_hits=[])
    assert [p.decision.decision_id for p in inbox.pending()] == ["dec-1"]


def test_answering_removes_it_from_pending_and_records_the_human():
    inbox = DecisionInbox()
    inbox.open(_decision(), interrupt_id="int-1", snapshot={}, rule_hits=[])
    answered = inbox.answer("dec-1", DecisionOutcome.APPROVE, by="Ama", role="coordinator", note="phoned")
    assert inbox.pending() == []
    assert answered.decision.outcome is DecisionOutcome.APPROVE
    assert answered.decision.by == "Ama"
    assert answered.decision.note == "phoned"
    assert answered.decision.at is not None


def test_answered_decisions_are_retained_for_audit():
    inbox = DecisionInbox()
    inbox.open(_decision(), interrupt_id="int-1", snapshot={}, rule_hits=[])
    inbox.answer("dec-1", DecisionOutcome.REJECT, by="Ama", role="coordinator", note="freezer down")
    assert [p.decision.decision_id for p in inbox.answered()] == ["dec-1"]


def test_snapshot_and_interrupt_id_survive_so_the_agent_can_resume():
    inbox = DecisionInbox()
    inbox.open(_decision(), interrupt_id="int-9", snapshot={"messages": ["hi"]}, rule_hits=[])
    pending = inbox.get("dec-1")
    assert pending.interrupt_id == "int-9"
    assert pending.snapshot == {"messages": ["hi"]}


def test_rule_hits_are_preserved_so_the_inbox_can_explain_itself():
    inbox = DecisionInbox()
    hits = [{"rule": "tight_window", "detail": "only 5 minutes of slack"}]
    inbox.open(_decision(), interrupt_id="i", snapshot={}, rule_hits=hits)
    assert inbox.get("dec-1").rule_hits == hits


def test_answering_an_unknown_decision_raises():
    with pytest.raises(KeyError):
        DecisionInbox().answer("nope", DecisionOutcome.APPROVE, by="a", role="b", note="")


def test_answering_twice_raises():
    inbox = DecisionInbox()
    inbox.open(_decision(), interrupt_id="i", snapshot={}, rule_hits=[])
    inbox.answer("dec-1", DecisionOutcome.APPROVE, by="a", role="b", note="")
    with pytest.raises(KeyError):
        inbox.answer("dec-1", DecisionOutcome.APPROVE, by="a", role="b", note="")


def test_pending_is_ordered_oldest_first():
    inbox = DecisionInbox()
    inbox.open(_decision("a"), interrupt_id="i", snapshot={}, rule_hits=[])
    inbox.open(_decision("b"), interrupt_id="i", snapshot={}, rule_hits=[])
    assert [p.decision.decision_id for p in inbox.pending()] == ["a", "b"]
