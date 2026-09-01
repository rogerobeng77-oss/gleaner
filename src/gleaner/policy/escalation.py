# src/gleaner/policy/escalation.py
"""The autonomy boundary as a Strands intervention handler.

Read-only tools are never guarded: the agent is free to look at whatever it likes.
Only the consequential ones - the ones that move food, dispatch a person, or send a
message to somebody - pass through the policy. When a rule fires, ``Confirm`` breaks
the agent out of its loop so the run can be parked until a human answers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from strands import InterventionHandler
from strands.interventions import Confirm, Proceed

from gleaner.domain.types import AutonomyLevel
from gleaner.policy.rules import PlacementContext, RuleHit, evaluate_rules

#: Tools with real-world consequences. Everything else runs unguarded.
GUARDED_TOOLS: frozenset[str] = frozenset(
    {"commit_placement", "dispatch_driver", "notify_donor", "notify_pantry", "send_outreach"}
)


def decision_is_approval(response: Any) -> bool:
    """Approval predicate for ``Confirm``.

    Strands' default accepts ``True`` and the strings "y"/"yes". We deliberately
    accept neither: an approval must be a structured record naming who decided and
    why, because that record is also the audit entry.
    """
    return isinstance(response, dict) and response.get("decision") == "approve"


class EscalationPolicy(InterventionHandler):
    """Decides, per consequential tool call, whether the agent may act alone.

    ``before_tool_call`` must be defined at class level - Strands inspects the class
    to discover which lifecycle methods a handler implements, so assigning the method
    on an instance would silently disable the policy.
    """

    name = "gleaner-escalation"

    def __init__(
        self,
        level: AutonomyLevel,
        context_provider: Callable[[dict[str, Any]], PlacementContext],
    ) -> None:
        self.level = level
        self.context_provider = context_provider
        self.last_hits: list[RuleHit] = []

    def before_tool_call(self, event: Any) -> Any:
        tool_name = event.tool_use["name"]
        if tool_name not in GUARDED_TOOLS:
            return Proceed(reason="tool has no real-world consequence")

        context = self.context_provider(event.tool_use.get("input", {}))
        hits = evaluate_rules(context, self.level)
        self.last_hits = hits

        if not hits:
            return Proceed(reason=f"within {self.level} autonomy limits")

        primary = hits[0]
        detail = " ".join(hit.detail for hit in hits)
        return Confirm(
            prompt=f"{detail} Approve this {tool_name.replace('_', ' ')}?",
            reason=primary.rule,
            evaluate=decision_is_approval,
        )
