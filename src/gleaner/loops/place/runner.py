# src/gleaner/loops/place/runner.py
"""Running and resuming a PLACE agent.

The two halves of the human-in-the-loop contract live here:

``run_place`` runs until the agent either finishes or hits a guarded call the policy
will not allow it to make alone. In the second case the run stops with
``stop_reason == "interrupt"`` and we capture a snapshot.

``resume_place`` loads that snapshot into an agent and feeds back the human's decision,
picking the run up exactly where it stopped - potentially hours later, in a different
process, on a different machine. Resuming can itself pause again: approving a
``commit_placement`` can walk straight into a guarded ``dispatch_driver``, so reason
population has to happen on every pause, not just the first.

``checkpointing`` stays False throughout: with it enabled the run halts with
``stop_reason == "checkpoint"`` before ever reaching the tool call, and no interrupt
is produced.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from typing import Any

from strands import Agent, Snapshot
from strands.models import BedrockModel

from gleaner.adapters.base import WorldAdapter
from gleaner.domain.models import DonationOffer
from gleaner.domain.types import AutonomyLevel
from gleaner.hooks.audit import AuditTrail
from gleaner.loops.place.context import build_context_provider
from gleaner.loops.place.tools import build_place_tools
from gleaner.policy.escalation import EscalationPolicy

#: Overridable so a judge outside us-east-1, or on a different model, can still run
#: this without editing source. The defaults are what the demo was recorded against.
REASONING_MODEL = os.environ.get("GLEANER_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("GLEANER_AWS_REGION", "us-east-1")

SYSTEM_PROMPT = """You are Gleaner, a food rescue coordinator for Pittsburgh.

Your job is to get surplus food to a pantry before it spoils. Work in this order:
1. Use find_pantries to see which pantries can actually take the food.
2. Use find_driver and estimate_arrival to check someone can get it there in time.
3. Call commit_placement for the best option, then dispatch_driver.

When you call commit_placement, pass the food's temp_class, dietary_flags,
minutes_out_of_refrigeration and ready_at_iso along with the placement, and the
donor_id from the offer. A policy re-checks all of those against the real pantry
and the real donation record before letting the commit through, so leaving them
out or shading them does not help you - it only makes the call fail or escalate.

Be decisive and brief. Do not ask the human for permission yourself - a separate
policy decides when a human is needed, and it will interrupt you if so. If a commit
is refused, read the reason given and adapt rather than simply retrying.
"""


@dataclass
class PlaceRunResult:
    paused: bool
    interrupt_id: str | None
    interrupt_reason: str | None
    snapshot: dict[str, Any] | None
    text: str
    #: Every rule that fired on the pause, as ``{"rule": ..., "detail": ...}``.
    #: ``interrupt_reason`` names only the first; the inbox wants all of them.
    rule_hits: list[dict[str, str]] = field(default_factory=list)


def describe_offer(offer: DonationOffer) -> str:
    """The offer as a prompt, stating every fact the agent needs and none it does not."""
    lots = "; ".join(
        f"{lot.weight_kg:.1f} kg {lot.description} ({lot.temp_class}"
        + (f", flagged {', '.join(lot.dietary_flags)}" if lot.dietary_flags else "")
        + ")"
        for lot in offer.lots
    )
    return (
        f"Offer {offer.offer_id} from donor {offer.donor_id} at {offer.pickup_address} "
        f"({offer.lat:.4f}, {offer.lon:.4f}).\n"
        f"Food: {lots}.\n"
        f"Ready at {offer.ready_at.isoformat()}. It has been out of refrigeration for "
        f"{offer.minutes_out_of_refrigeration:.0f} minutes.\n"
        f"{offer.notes or 'No further notes.'}\n"
        f"Place it: find a pantry, find a driver, then commit and dispatch."
    )


def build_default_policy(world: WorldAdapter, level: AutonomyLevel) -> EscalationPolicy:
    """The policy as it should actually be constructed: judging the world, not the model.

    ``EscalationPolicy`` takes any context provider so tests can pin a context, but in
    production there is only one right answer, and this is it. Build a fresh policy
    per run - see ``EscalationPolicy``'s note on ``last_hits``.
    """
    return EscalationPolicy(level=level, context_provider=build_context_provider(world))


def build_place_agent(
    world: WorldAdapter,
    ledger: list[dict[str, Any]],
    policy: EscalationPolicy,
    trail: AuditTrail,
) -> Agent:
    """Construct a PLACE agent wired to a world, a policy and an audit trail."""
    return Agent(
        model=BedrockModel(model_id=REASONING_MODEL, region_name=REGION),
        tools=build_place_tools(world, ledger),
        interventions=[policy],
        hooks=[trail],
        system_prompt=SYSTEM_PROMPT,
        checkpointing=False,
        callback_handler=None,
    )


def _result_from(agent: Agent, raw: Any, policy: EscalationPolicy) -> PlaceRunResult:
    """Build a ``PlaceRunResult`` from a raw agent result, naming the rule that paused it.

    ``interrupt.name`` is always the intervention handler's own name
    (``"gleaner-escalation"``), never the specific rule that fired - Strands has no
    concept of a per-interrupt reason string beyond the ``Confirm`` prompt text, which
    is meant for a human to read, not to pattern-match on. The actual rule name always
    comes from ``policy.last_hits``, which ``EscalationPolicy.before_tool_call``
    populates on the very call that raised this interrupt.

    This function is the single place that does that lookup, and both ``run_place``
    and ``resume_place`` route through it, so a pause that happens mid-resume (e.g.
    approving ``commit_placement`` walks straight into a guarded ``dispatch_driver``)
    gets a real reason too, not just the first pause of a run.

    If ``policy.last_hits`` is empty while the run is paused - the policy instance
    passed in was not the one that actually decided this interrupt, most likely -
    there is no rule to report and ``interrupt_reason`` is deliberately left ``None``
    rather than guessing. Callers (the decision inbox included) must treat ``None``
    as "reason unknown", never as "no rule fired": the run would not be paused at all
    if no rule had fired.
    """
    interrupts = getattr(raw, "interrupts", None)
    if raw.stop_reason == "interrupt" and interrupts:
        interrupt = interrupts[0]
        snapshot = dataclasses.asdict(agent.take_snapshot(preset="session"))
        reason = policy.last_hits[0].rule if policy.last_hits else None
        return PlaceRunResult(
            paused=True,
            interrupt_id=interrupt.id,
            interrupt_reason=reason,
            snapshot=snapshot,
            text=str(raw),
            rule_hits=[{"rule": hit.rule, "detail": hit.detail} for hit in policy.last_hits],
        )
    return PlaceRunResult(False, None, None, None, str(raw))


async def run_place(agent: Agent, prompt: str, policy: EscalationPolicy) -> PlaceRunResult:
    """Run until completion or until the policy requires a human.

    The policy is passed in rather than read off the agent: Strands keeps handlers in a
    private registry and exposes no public accessor, and the interrupt itself carries only
    the handler's name, not the rule that fired.
    """
    raw = await agent.invoke_async(prompt)
    return _result_from(agent, raw, policy)


async def resume_place(
    agent: Agent,
    snapshot: dict[str, Any],
    interrupt_id: str,
    decision_record: dict[str, Any],
    policy: EscalationPolicy,
) -> PlaceRunResult:
    """Resume a paused run with a human's structured decision.

    Takes ``policy`` for the same reason ``run_place`` does: naming the reason for
    *this* pause (if the resumed run pauses again on a later guarded call) requires
    ``policy.last_hits``, which only the live policy instance carries.
    """
    agent.load_snapshot(Snapshot(**snapshot))
    raw = await agent.invoke_async(
        [{"interruptResponse": {"interruptId": interrupt_id, "response": decision_record}}]
    )
    return _result_from(agent, raw, policy)
