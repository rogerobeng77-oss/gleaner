# src/gleaner/policy/escalation.py
"""The autonomy boundary as a Strands intervention handler.

Read-only tools are never guarded: the agent is free to look at whatever it likes.
Only the consequential ones - the ones that move food, dispatch a person, or send a
message to somebody - pass through the policy. When a rule fires, ``Confirm`` breaks
the agent out of its loop so the run can be parked until a human answers.

The policy stays deliberately ignorant of what any particular tool *does*. It knows
which names are consequential, it asks the context provider to describe the call, and
it applies ``rules.py``. Knowing that a driver dispatch has no dietary dimension is
the provider's job, and putting it there keeps one description of each tool rather
than two that can drift apart. The one thing the policy does decide for itself is
what to do when the provider says it cannot describe a tool at all: escalate, and say
so in those words, because the alternative is to report a rule that is not true.
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

#: Reported when the context provider cannot describe a guarded tool.
#:
#: Deliberately not a rule in ``rules.py``: it is not a judgement about a placement,
#: it is the policy declining to make one. ``send_outreach`` is the live example - it
#: is guarded by name but belongs to the SOURCE loop and has no tool behind it yet.
#: A guarded tool nobody can describe is the definition of a call that needs a human,
#: and this is the only honest thing to put in front of them.
UNASSESSABLE_RULE: str = "tool_not_assessable"


def decision_is_approval(response: Any) -> bool:
    """Approval predicate for ``Confirm``.

    Strands' default accepts ``True`` and the strings "y"/"yes". We accept neither:
    an approval has to be a structured object that says ``approve`` in a named field,
    so that a stray truthy value coming back through the interrupt channel cannot be
    mistaken for a human's consent.

    It checks nothing beyond that. Who approved, in what role, and why is captured by
    ``DecisionInbox.answer``, which builds the audit record via ``Decision.answer``
    and requires ``by`` and ``role`` there. Attribution is enforced on the path that
    actually writes the audit trail; this predicate only decides whether the agent
    may continue.
    """
    return isinstance(response, dict) and response.get("decision") == "approve"


class EscalationPolicy(InterventionHandler):
    """Decides, per consequential tool call, whether the agent may act alone.

    **Build one instance per run.** ``last_hits`` is per-instance mutable state that
    callers read *after* the run returns, to name the rule that paused it. Two offers
    sharing a policy would overwrite each other's hits and the inbox would attribute
    one offer's escalation reason to the other. ``build_default_policy`` in the PLACE
    runner is the intended constructor.

    ``before_tool_call`` must be defined at class level - Strands inspects the class
    to discover which lifecycle methods a handler implements, so assigning the method
    on an instance would silently disable the policy.
    """

    name = "gleaner-escalation"

    def __init__(
        self,
        level: AutonomyLevel,
        context_provider: Callable[[str, dict[str, Any]], PlacementContext | None],
    ) -> None:
        self.level = level
        self.context_provider = context_provider
        self.last_hits: list[RuleHit] = []

    def before_tool_call(self, event: Any) -> Any:
        tool_name = event.tool_use["name"]
        if tool_name not in GUARDED_TOOLS:
            return Proceed(reason="tool has no real-world consequence")

        action = tool_name.replace("_", " ")
        context = self.context_provider(tool_name, event.tool_use.get("input", {}))

        if context is None:
            hits = [
                RuleHit(
                    UNASSESSABLE_RULE,
                    f"Gleaner has no way to judge the risk of a {action}, "
                    f"so it will not make one unattended.",
                )
            ]
        else:
            hits = evaluate_rules(context, self.level)
        self.last_hits = hits

        if not hits:
            return Proceed(reason=f"within {self.level} autonomy limits")

        primary = hits[0]
        detail = " ".join(hit.detail for hit in hits)
        return Confirm(
            prompt=f"{detail} Approve this {action}?",
            reason=primary.rule,
            evaluate=decision_is_approval,
        )
