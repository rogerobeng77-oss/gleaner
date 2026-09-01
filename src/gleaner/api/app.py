"""The control plane the coordinator's browser talks to.

Mostly thin: it reads state the agents produce and writes back a human's decision.
The one route with weight is ``POST /api/offers``, which is where an offer actually
enters the system and the PLACE loop runs. Without it the ledger, timeline and impact
endpoints are empty by construction, because nothing else ever writes them.

Presentation-agnostic, so the frontend can be designed independently.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from gleaner.api.state import GleanerState
from gleaner.domain.impact import ImpactEntry, co2e_avoided_kg, meals_from_kg, summarise
from gleaner.domain.models import Decision, DonationOffer
from gleaner.domain.types import AutonomyLevel, DecisionOutcome
from gleaner.loops.place.runner import (
    PlaceRunResult,
    build_default_policy,
    build_place_agent,
    describe_offer,
    run_place,
)
from gleaner.policy.rules import THRESHOLDS

#: How an offer gets run. Injectable because the default reaches Amazon Bedrock, and
#: no test may do that; a test passes a stub returning a canned ``PlaceRunResult``.
OfferRunner = Callable[[GleanerState, DonationOffer], Awaitable[PlaceRunResult]]


async def run_offer_with_agent(state: GleanerState, offer: DonationOffer) -> PlaceRunResult:
    """The real runner: a live PLACE agent against the shared state.

    The agent writes straight into ``state.ledger`` and ``state.trail``, because those
    are the tool ledger and the hook trail - they are how the run records itself.
    """
    policy = build_default_policy(state.world, state.autonomy)
    agent = build_place_agent(state.world, state.ledger, policy, state.trail)
    return await run_place(agent, describe_offer(offer), policy)


class AnswerRequest(BaseModel):
    outcome: DecisionOutcome
    by: str
    role: str
    note: str = ""


class AutonomyRequest(BaseModel):
    level: AutonomyLevel


def create_app(offer_runner: OfferRunner | None = None) -> FastAPI:
    """Build the app. Pass ``offer_runner`` to run offers without calling a model."""
    app = FastAPI(title="Gleaner", version="0.1.0")
    app.state.gleaner = GleanerState()
    run_this_offer: OfferRunner = offer_runner or run_offer_with_agent

    def state() -> GleanerState:
        return app.state.gleaner

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/world")
    def world() -> dict:
        current = state()
        return {
            "now": current.world.now().isoformat(),
            "pantries": [p.model_dump(mode="json") for p in current.world.pantries()],
            "drivers": [d.model_dump(mode="json") for d in current.world.drivers()],
            "donors": [d.model_dump(mode="json") for d in current.world.donors()],
        }

    @app.get("/api/inbox")
    def inbox() -> list[dict]:
        return [
            {
                "decision_id": p.decision.decision_id,
                "offer_id": p.decision.offer_id,
                "rule": p.decision.rule,
                "prompt": p.decision.prompt,
                "rule_hits": p.rule_hits,
            }
            for p in state().inbox.pending()
        ]

    @app.post("/api/inbox/{decision_id}/answer")
    def answer(decision_id: str, body: AnswerRequest) -> dict:
        try:
            answered = state().inbox.answer(
                decision_id, body.outcome, by=body.by, role=body.role, note=body.note
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return answered.decision.model_dump(mode="json")

    @app.post("/api/offers")
    async def submit_offer(offer: DonationOffer) -> dict:
        """Take a donation offer and run the PLACE loop against it.

        Three outcomes are possible and all three are recorded:

        * the agent places it within its autonomy limits, and the placement lands in
          the ledger and the impact figures;
        * the policy stops it, and the paused run is parked in the inbox with the rule
          that fired and the snapshot needed to resume it later;
        * the agent finishes without committing anything, which the ledger shows by
          staying empty.

        **Impact follows the ledger, not the run's exit status.** The instinct to
        count only completed runs is right in spirit and wrong in mechanism: a run
        can commit a placement and *then* pause on a later guarded call, and the
        ledger already holds that commit. Keying impact off ``paused`` therefore made
        GET /api/ledger show food committed while GET /api/impact showed zero
        placements - two endpoints describing the same run and disagreeing. Counting
        what the ledger records as committed for this offer keeps them in step and
        still cannot inflate the headline number: a run that committed nothing has
        nothing in the ledger, so it counts nothing, paused or not.
        """
        current = state()
        # Register before running, not after. The escalation policy resolves the
        # offer record to check the agent's account of the food against the donor's
        # own - the pork it did not mention, the two hours out of the fridge it
        # rounded down. An unregistered offer resolves to None, that whole branch
        # never runs, and the guard silently falls back to trusting the model.
        current.world.register_offer(offer)
        current.trail.record("offer", f"offer {offer.offer_id} received", {
            "offer_id": offer.offer_id,
            "total_weight_kg": offer.total_weight_kg,
        })
        result = await run_this_offer(current, offer)

        # Read the ledger before branching. A paused run may still have committed.
        committed_kg = sum(
            float(e.get("weight_kg", 0.0))
            for e in current.ledger
            if e.get("kind") == "commit_placement" and e.get("offer_id") == offer.offer_id
        )
        if committed_kg > 0:
            current.impact.append(
                ImpactEntry(
                    offer_id=offer.offer_id,
                    kg=committed_kg,
                    meals=meals_from_kg(committed_kg),
                    co2e_kg=co2e_avoided_kg(committed_kg),
                    # Without a coordinator on this offer, this is what would have
                    # gone to landfill. The whole donation, not just the placed part.
                    counterfactual_kg_wasted=offer.total_weight_kg,
                )
            )

        if result.paused:
            decision = Decision(
                decision_id=f"dec-{uuid.uuid4().hex[:8]}",
                offer_id=offer.offer_id,
                rule=result.interrupt_reason or "unknown",
                prompt=result.text,
            )
            current.inbox.open(
                decision,
                interrupt_id=result.interrupt_id or "",
                snapshot=result.snapshot or {},
                rule_hits=result.rule_hits,
            )
            current.trail.record("escalation", f"paused: {decision.rule}", {
                "decision_id": decision.decision_id,
            })
            return {
                "offer_id": offer.offer_id,
                "status": "awaiting_decision",
                "decision_id": decision.decision_id,
                "rule": decision.rule,
                "rule_hits": result.rule_hits,
                # What the run had already committed before it stopped. Zero on a
                # run that paused on its first guarded call, which is the usual case.
                "committed_kg": committed_kg,
            }

        current.trail.record("offer", f"offer {offer.offer_id} finished", {
            "committed_kg": committed_kg,
        })
        return {
            "offer_id": offer.offer_id,
            "status": "placed" if committed_kg > 0 else "no_placement",
            "committed_kg": committed_kg,
        }

    @app.get("/api/ledger")
    def ledger() -> list[dict]:
        return state().ledger

    @app.get("/api/impact")
    def impact() -> dict:
        return summarise(state().impact)

    @app.get("/api/timeline")
    def timeline() -> list[dict]:
        return state().trail.as_dicts()

    @app.get("/api/autonomy")
    def get_autonomy() -> dict:
        level = state().autonomy
        limits = THRESHOLDS[level]
        return {
            "level": level,
            "thresholds": {
                "max_autonomous_kg": limits.max_autonomous_kg,
                "required_margin_minutes": limits.required_margin_minutes,
                "min_safety_confidence": limits.min_safety_confidence,
                "max_capacity_fraction": limits.max_capacity_fraction,
                "allow_novel_donor": limits.allow_novel_donor,
            },
        }

    @app.put("/api/autonomy")
    def set_autonomy(body: AutonomyRequest) -> dict:
        state().autonomy = body.level
        return get_autonomy()

    return app
