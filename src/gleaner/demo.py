# src/gleaner/demo.py
"""Run one PLACE loop end to end and print what happened.

    python -m gleaner.demo            # the default seeded offer
    python -m gleaner.demo 7          # a different offer from the same city

Or, once installed, ``gleaner-demo``.

**This calls a live model.** It builds a real Bedrock-backed agent and invokes it,
so it costs money and needs AWS credentials. That is why it is a script and not a
test: nothing in the test suite may reach the network. Set ``GLEANER_MODEL_ID`` and
``GLEANER_AWS_REGION`` if the defaults do not suit your account.

What it is for: the guard is only interesting if you can watch it fire. This prints
the offer, the agent's own account of what it did, and - when the policy stopped it -
the rule that fired and the plain-English detail a coordinator would read in the
inbox.
"""

from __future__ import annotations

import asyncio
import sys

from gleaner.adapters.simulated import SimulatedCity
from gleaner.domain.models import DonationOffer
from gleaner.domain.types import AutonomyLevel
from gleaner.hooks.audit import AuditTrail
from gleaner.loops.place.runner import (
    PlaceRunResult,
    build_default_policy,
    build_place_agent,
    describe_offer,
    run_place,
)

_RULE = "-" * 72


def _print_offer(offer: DonationOffer) -> None:
    print(_RULE)
    print(f"OFFER {offer.offer_id}  -  {offer.pickup_address}")
    print(_RULE)
    for lot in offer.lots:
        flags = f"  flags={','.join(lot.dietary_flags)}" if lot.dietary_flags else ""
        print(f"  {lot.weight_kg:8.1f} kg  {lot.temp_class:<8} {lot.description}{flags}")
    print(f"  ready at {offer.ready_at.isoformat()}")
    print(f"  {offer.minutes_out_of_refrigeration:.0f} minutes out of refrigeration")
    print(f"  earliest safety deadline {offer.earliest_deadline().isoformat()}")


def _print_result(result: PlaceRunResult, ledger: list[dict], trail: AuditTrail) -> None:
    print()
    print(_RULE)
    if result.paused:
        print("PAUSED - the policy will not let the agent do this alone")
        print(_RULE)
        print(f"  rule: {result.interrupt_reason or 'unknown'}")
        for hit in result.rule_hits:
            print(f"    - {hit['rule']}: {hit['detail']}")
        print(f"  interrupt {result.interrupt_id}, snapshot captured for resume")
        # Not "nothing was committed": a run can clear the guard on commit_placement
        # and stop on the dispatch that follows it, and the ledger printed below
        # would then flatly contradict the claim.
        committed = [e for e in ledger if e["kind"] == "commit_placement"]
        if committed:
            print(f"  {len(committed)} placement(s) already committed before it stopped")
        else:
            print("  nothing was committed yet")
        print("  a coordinator would answer this in the inbox")
    else:
        print("COMPLETED - the agent acted within its autonomy limits")
        print(_RULE)
    print()
    print("Agent's account:")
    print(f"  {result.text.strip()}")
    print()
    print(f"Ledger ({len(ledger)} entries):")
    for entry in ledger:
        print(f"  {entry['at']}  {entry['kind']}  {entry}")
    print()
    print(f"Timeline ({len(trail.entries)} entries):")
    for item in trail.entries:
        print(f"  {item.at.isoformat()}  {item.kind:<12} {item.label}")


async def run_demo(
    seed: int | None = None, level: AutonomyLevel = AutonomyLevel.BALANCED
) -> PlaceRunResult:
    """Build a city, generate an offer, and run the PLACE loop against it."""
    world = SimulatedCity()
    offer = world.generate_offer(rng_seed=seed)
    _print_offer(offer)

    ledger: list[dict] = []
    trail = AuditTrail()
    policy = build_default_policy(world, level)
    agent = build_place_agent(world, ledger, policy, trail)

    print()
    print(f"Running PLACE at autonomy level '{level}'...")
    result = await run_place(agent, describe_offer(offer), policy)
    _print_result(result, ledger, trail)
    return result


def main() -> int:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(run_demo(seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
