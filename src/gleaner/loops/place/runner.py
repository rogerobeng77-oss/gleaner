# src/gleaner/loops/place/runner.py
"""Running and resuming a PLACE agent.

The two halves of the human-in-the-loop contract live here:

``run_place`` runs until the agent either finishes or hits a guarded call the policy
will not allow it to make alone. In the second case the run stops with
``stop_reason == "interrupt"`` and we capture a snapshot.

``resume_place`` loads that snapshot into an agent and feeds back the human's decision,
picking the run up exactly where it stopped - potentially hours later, in a different
process, on a different machine.

``checkpointing`` stays False throughout: with it enabled the run halts with
``stop_reason == "checkpoint"`` before ever reaching the tool call, and no interrupt
is produced.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from strands import Agent, Snapshot
from strands.models import BedrockModel

from gleaner.adapters.base import WorldAdapter
from gleaner.hooks.audit import AuditTrail
from gleaner.loops.place.tools import build_place_tools
from gleaner.policy.escalation import EscalationPolicy

REASONING_MODEL = "global.anthropic.claude-sonnet-4-6"
REGION = "us-east-1"

SYSTEM_PROMPT = """You are Gleaner, a food rescue coordinator for Pittsburgh.

Your job is to get surplus food to a pantry before it spoils. Work in this order:
1. Use find_pantries to see which pantries can actually take the food.
2. Use find_driver and estimate_arrival to check someone can get it there in time.
3. Call commit_placement for the best option, then dispatch_driver.

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


def _result_from(agent: Agent, raw: Any) -> PlaceRunResult:
    interrupts = getattr(raw, "interrupts", None)
    if raw.stop_reason == "interrupt" and interrupts:
        interrupt = interrupts[0]
        snapshot = dataclasses.asdict(agent.take_snapshot(preset="session"))
        return PlaceRunResult(
            paused=True,
            interrupt_id=interrupt.id,
            interrupt_reason=interrupt.name if interrupt.name != "gleaner-escalation" else None,
            snapshot=snapshot,
            text=str(raw),
        )
    return PlaceRunResult(False, None, None, None, str(raw))


async def run_place(agent: Agent, prompt: str, policy: EscalationPolicy) -> PlaceRunResult:
    """Run until completion or until the policy requires a human.

    The policy is passed in rather than read off the agent: Strands keeps handlers in a
    private registry and exposes no public accessor, and the interrupt itself carries only
    the handler's name, not the rule that fired.
    """
    raw = await agent.invoke_async(prompt)
    result = _result_from(agent, raw)
    if result.paused and policy.last_hits:
        result.interrupt_reason = policy.last_hits[0].rule
    return result


async def resume_place(
    agent: Agent,
    snapshot: dict[str, Any],
    interrupt_id: str,
    decision_record: dict[str, Any],
) -> PlaceRunResult:
    """Resume a paused run with a human's structured decision."""
    agent.load_snapshot(Snapshot(**snapshot))
    raw = await agent.invoke_async(
        [{"interruptResponse": {"interruptId": interrupt_id, "response": decision_record}}]
    )
    return _result_from(agent, raw)
