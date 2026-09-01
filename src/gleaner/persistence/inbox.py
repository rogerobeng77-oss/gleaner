"""The decision inbox.

When the agent escalates, the paused run's snapshot and interrupt id are parked here
alongside the question. A human answers; the orchestrator uses the stored snapshot to
resume the very same run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gleaner.domain.models import Decision
from gleaner.domain.types import DecisionOutcome


@dataclass
class PendingDecision:
    decision: Decision
    interrupt_id: str
    snapshot: dict[str, Any]
    rule_hits: list[dict[str, str]] = field(default_factory=list)


class DecisionInbox:
    """In-memory inbox. Task 12 swaps the storage without changing this interface."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingDecision] = {}
        self._answered: list[PendingDecision] = []

    def open(
        self,
        decision: Decision,
        *,
        interrupt_id: str,
        snapshot: dict[str, Any],
        rule_hits: list[dict[str, str]],
    ) -> str:
        """Park an escalation and return its id."""
        self._pending[decision.decision_id] = PendingDecision(
            decision=decision, interrupt_id=interrupt_id, snapshot=snapshot, rule_hits=rule_hits
        )
        return decision.decision_id

    def pending(self) -> list[PendingDecision]:
        """Unanswered escalations, oldest first."""
        return list(self._pending.values())

    def answered(self) -> list[PendingDecision]:
        return list(self._answered)

    def get(self, decision_id: str) -> PendingDecision | None:
        return self._pending.get(decision_id)

    def answer(
        self,
        decision_id: str,
        outcome: DecisionOutcome,
        *,
        by: str,
        role: str,
        note: str = "",
    ) -> PendingDecision:
        """Record a human's decision and move it out of the pending queue."""
        if decision_id not in self._pending:
            raise KeyError(f"no pending decision {decision_id!r}")
        pending = self._pending.pop(decision_id)
        pending.decision = pending.decision.answer(outcome, by=by, role=role, note=note)
        self._answered.append(pending)
        return pending
