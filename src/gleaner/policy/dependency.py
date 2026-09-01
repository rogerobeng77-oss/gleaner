# src/gleaner/policy/dependency.py
"""The prerequisite boundary: nothing may act on a placement that never happened.

``EscalationPolicy`` judges each consequential call on its own merits - is *this*
action within the agent's autonomy? That is the right question for a placement, and
the wrong one for everything downstream of it. A driver dispatch is not an
independent decision: it is only meaningful if the commit it serves actually went
through. The live run that produced this module is the proof. The policy held
``commit_placement`` on ``capacity_overrun``, the model emitted ``dispatch_driver``
in the same turn, the guard judged the dispatch on its own (a known donor, food
still inside its cold-chain budget: nothing to escalate) and let it run. A volunteer
was sent across Pittsburgh to collect 120 kg of pork that had nowhere to go, while
the placement sat unanswered in a coordinator's inbox.

So this handler asks the other question - has the thing this call depends on
actually happened? - and it asks the **ledger**, not the agent. The ledger is the
record of what the tools really did; the transcript is the model's account of what
it believes it did, and an account is exactly what must not be trusted here. A
placement that was held, refused, never attempted, or attempted and rejected by the
tool all look the same in the ledger, because they are the same thing: no commit.

**Deny, not Confirm.** The escalation policy's answer to a risky call is to ask a
human. That is wrong here. Offering a coordinator "approve this dispatch?" for a
placement they have not approved invites them to authorise the second half of a
decision whose first half is still an open question in the same inbox. There is
nothing for a human to weigh: the dispatch simply has no placement to serve yet.
``Deny`` cancels the call and hands the model the reason, so it can wait for the
commit rather than assume it succeeded.

**Which tools depend on a commit.** ``dispatch_driver`` obviously. The two
notification tools too, and for the same reason rather than by analogy: in the PLACE
loop a message to a pantry or a donor is a message *about a placement* - "it is on
its way", "expect 120 kg at four" - and sending it while the placement is held is
the same harm one step removed. A pantry clears shelf space, a donor stops looking
for another taker, and the coordinator still thinks nothing has happened. So all
three carry an ``offer_id`` and all three are checked against the ledger.

**Fail closed on a missing offer id.** A dependent call that names no offer cannot be
shown to follow an approved placement, so it is refused. The cost is real and worth
stating: the PLACE agent cannot send a bare "sorry, we could not take this" to a
donor. That message belongs to the coordinator holding the escalation, not to an
agent whose placement was stopped. The alternative - waving through any notification
that omits an argument - would make the check optional at the model's discretion,
which is not a check.

``commit_placement`` itself is deliberately absent: it is the prerequisite, it
depends on nothing, and ``EscalationPolicy`` remains the only thing standing in
front of it.
"""

from __future__ import annotations

from typing import Any

from strands import InterventionHandler
from strands.interventions import Deny, Proceed

#: Tools that only mean anything once the offer's placement has been committed.
DEPENDENT_TOOLS: frozenset[str] = frozenset({"dispatch_driver", "notify_pantry", "notify_donor"})

#: The ledger entry that counts as "the placement happened".
COMMIT_KIND: str = "commit_placement"


class PlacementDependencyGuard(InterventionHandler):
    """Blocks any call that acts on a placement the ledger does not show as committed.

    Built per run with the same ``ledger`` list the tools append to, so it always
    reads the current state of the run rather than a copy taken at construction. Pass
    the run's ``AuditTrail`` too and every refusal becomes a visible line on the
    coordinator's timeline instead of a silent non-event - a dispatch that quietly
    does not happen is indistinguishable from one nobody attempted.

    Register it **before** ``EscalationPolicy``: ``Deny`` short-circuits the
    intervention pipeline, so a call refused here never reaches the escalation policy
    and therefore never offers a human an approval they should not be asked for.

    ``before_tool_call`` must be defined at class level - Strands inspects the class
    to discover which lifecycle methods a handler implements.
    """

    name = "gleaner-placement-dependency"

    def __init__(self, ledger: list[dict[str, Any]], trail: Any | None = None) -> None:
        self.ledger = ledger
        self.trail = trail
        #: The reason for the most recent refusal, or ``None`` if nothing was refused.
        self.last_block: str | None = None

    def before_tool_call(self, event: Any) -> Any:
        tool_name = event.tool_use["name"]
        if tool_name not in DEPENDENT_TOOLS:
            return Proceed(reason="does not depend on a committed placement")

        raw_input = event.tool_use.get("input")
        tool_input = raw_input if isinstance(raw_input, dict) else {}
        offer_id = tool_input.get("offer_id")
        action = tool_name.replace("_", " ")

        if not isinstance(offer_id, str) or not offer_id.strip():
            return self._refuse(
                tool_name,
                None,
                f"This {action} names no offer_id, so nothing shows which placement it "
                f"is acting on or whether that placement was ever approved. Name the "
                f"offer_id of a placement that commit_placement has already committed.",
            )

        offer_id = offer_id.strip()
        if not self._is_committed(offer_id):
            return self._refuse(
                tool_name,
                offer_id,
                f"The placement for offer {offer_id} has not been approved: the ledger "
                f"holds no committed placement for it, so this {action} has nothing to "
                f"serve. A placement that is waiting on a coordinator's decision, one "
                f"that was refused, and one that was never made all read the same here. "
                f"Wait for commit_placement on {offer_id} to go through; do not retry "
                f"this call before then.",
            )

        return Proceed(reason=f"offer {offer_id} has a committed placement")

    def _is_committed(self, offer_id: str) -> bool:
        """Whether the ledger records a committed placement for this offer."""
        return any(
            entry.get("kind") == COMMIT_KIND and entry.get("offer_id") == offer_id
            for entry in self.ledger
        )

    def _refuse(self, tool_name: str, offer_id: str | None, reason: str) -> Deny:
        self.last_block = reason
        if self.trail is not None:
            self.trail.record(
                "blocked",
                f"{tool_name} blocked: no approved placement",
                {"tool": tool_name, "offer_id": offer_id, "reason": reason},
            )
        return Deny(reason=reason)
