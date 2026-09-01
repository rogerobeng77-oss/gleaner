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
    """An in-memory inbox. Escalations live only as long as the process does.

    That is a real limitation and worth stating plainly, because it is in tension
    with what this class is for: parking a paused run so a human can answer it
    later. "Later" currently means later in the same process. Restart the API and
    every pending decision, and the snapshot needed to resume its run, is gone.

    The interface is deliberately narrow - open, pending, get, answer - so a durable
    implementation can be dropped in behind it without touching a caller. None
    exists yet.
    """

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
        """Park an escalation and return its id.

        `snapshot` and `rule_hits` are shallow-copied on the way in so a caller that
        mutates its own dict/list after calling `open()` cannot silently corrupt the
        parked state. A shallow copy is enough here: this data comes from
        `dataclasses.asdict` and its nested content is not shared with a live object,
        so there is nothing deeper worth copying.

        Raises `KeyError` if `decision_id` is already pending — re-opening a still-
        pending id would silently discard its earlier snapshot and interrupt_id, which
        is exactly the failure this inbox exists to prevent. Re-opening an id that has
        already been answered is fine: it is no longer in `_pending`, so it is treated
        as a fresh escalation.
        """
        if decision.decision_id in self._pending:
            raise KeyError(f"decision {decision.decision_id!r} is already pending")
        self._pending[decision.decision_id] = PendingDecision(
            decision=decision,
            interrupt_id=interrupt_id,
            snapshot=dict(snapshot),
            rule_hits=list(rule_hits),
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
