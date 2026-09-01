"""The control plane the coordinator's browser talks to.

Mostly thin: it reads state the agents produce and writes back a human's decision.
The one route with weight is ``POST /api/offers``, which is where an offer actually
enters the system and the PLACE loop runs. Without it the ledger, timeline and impact
endpoints are empty by construction, because nothing else ever writes them.

Presentation-agnostic, so the frontend can be designed independently.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
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
    resume_place,
    run_place,
)
from gleaner.persistence.inbox import PendingDecision
from gleaner.policy.rules import THRESHOLDS

#: How an offer gets run. Injectable because the default reaches Amazon Bedrock, and
#: no test may do that; a test passes a stub returning a canned ``PlaceRunResult``.
OfferRunner = Callable[[GleanerState, DonationOffer], Awaitable[PlaceRunResult]]

#: How an answered decision resumes its paused run. Injectable for the same reason.
DecisionResumer = Callable[[GleanerState, PendingDecision], Awaitable[PlaceRunResult]]


async def run_offer_with_agent(state: GleanerState, offer: DonationOffer) -> PlaceRunResult:
    """The real runner: a live PLACE agent against the shared state.

    The agent writes straight into ``state.ledger`` and ``state.trail``, because those
    are the tool ledger and the hook trail - they are how the run records itself.
    """
    policy = build_default_policy(state.world, state.autonomy)
    agent = build_place_agent(state.world, state.ledger, policy, state.trail)
    return await run_place(agent, describe_offer(offer), policy)


async def resume_with_agent(state: GleanerState, pending: PendingDecision) -> PlaceRunResult:
    """The real resumer: load the parked snapshot into a fresh agent and answer it.

    A *fresh* policy instance, deliberately. ``EscalationPolicy`` carries per-run
    mutable state, so reusing the one that paused this run would attribute its old
    hits to whatever the resumed run does next.
    """
    policy = build_default_policy(state.world, state.autonomy)
    agent = build_place_agent(state.world, state.ledger, policy, state.trail)
    decision = pending.decision
    return await resume_place(
        agent,
        pending.snapshot,
        pending.interrupt_id,
        {
            "decision": decision.outcome.value if decision.outcome else "reject",
            "by": decision.by,
            "role": decision.role,
            "note": decision.note,
        },
        policy,
    )


def _account_for(state: GleanerState, offer_id: str) -> float:
    """Recompute this offer's impact entry from the ledger. Returns committed kg.

    Replaces rather than appends, because an offer can reach the ledger twice: once
    when the run commits part of a load and pauses, and again when a human approves
    the rest. Appending would count the same kilograms as two placements and inflate
    the only number on the home screen anybody reads.

    The ledger is the source of truth here, not the run's exit status. A run that
    committed nothing has nothing in the ledger and so contributes nothing, paused
    or not.
    """
    committed_kg = sum(
        float(e.get("weight_kg", 0.0))
        for e in state.ledger
        if e.get("kind") == "commit_placement" and e.get("offer_id") == offer_id
    )
    state.impact[:] = [e for e in state.impact if e.offer_id != offer_id]
    if committed_kg <= 0:
        return 0.0

    offer = state.world.offer(offer_id)
    state.impact.append(
        ImpactEntry(
            offer_id=offer_id,
            kg=committed_kg,
            meals=meals_from_kg(committed_kg),
            co2e_kg=co2e_avoided_kg(committed_kg),
            # Without a coordinator on this offer, this is what would have gone to
            # landfill. The whole donation, not just the placed part.
            counterfactual_kg_wasted=offer.total_weight_kg if offer else committed_kg,
        )
    )
    return committed_kg


class AnswerRequest(BaseModel):
    outcome: DecisionOutcome
    by: str
    role: str
    note: str = ""


class AutonomyRequest(BaseModel):
    level: AutonomyLevel


def create_app(
    offer_runner: OfferRunner | None = None,
    decision_resumer: DecisionResumer | None = None,
) -> FastAPI:
    """Build the app. Pass the two runners to drive it without calling a model."""
    app = FastAPI(title="Gleaner", version="0.1.0")
    app.state.gleaner = GleanerState()
    run_this_offer: OfferRunner = offer_runner or run_offer_with_agent
    resume_this_decision: DecisionResumer = decision_resumer or resume_with_agent

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
    async def answer(decision_id: str, body: AnswerRequest) -> dict:
        """Record the human's decision, then resume the run that was waiting on it.

        Recording alone is not enough, and for a while that was all this did: the
        decision moved out of the inbox, the snapshot was dropped on the floor, and
        an approved 400 kg placement was never committed. The whole premise is that
        a paused run picks up where it stopped, so the resume belongs on this path,
        not in a test that calls ``resume_place`` directly.
        """
        try:
            answered = state().inbox.answer(
                decision_id, body.outcome, by=body.by, role=body.role, note=body.note
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        current = state()
        resumed = await resume_this_decision(current, answered)
        offer_id = answered.decision.offer_id
        _account_for(current, offer_id)
        current.trail.record("decision", f"{body.outcome.value} on {decision_id}", {
            "offer_id": offer_id,
            "paused_again": resumed.paused,
        })
        if resumed.paused:
            follow_up = Decision(
                decision_id=f"dec-{uuid.uuid4().hex[:8]}",
                offer_id=offer_id,
                rule=resumed.interrupt_reason or "unknown",
                prompt=resumed.prompt,
            )
            current.inbox.open(
                follow_up,
                interrupt_id=resumed.interrupt_id or "",
                snapshot=resumed.snapshot or {},
                rule_hits=resumed.rule_hits,
            )
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
        committed_kg = _account_for(current, offer.offer_id)

        if result.paused:
            decision = Decision(
                decision_id=f"dec-{uuid.uuid4().hex[:8]}",
                offer_id=offer.offer_id,
                rule=result.interrupt_reason or "unknown",
                prompt=result.prompt,
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

    _mount_console(app)
    return app


def console_dir() -> Path | None:
    """Where the built console lives, or ``None`` if it has not been built.

    ``GLEANER_CONSOLE_DIR`` wins, because the relative fallback only holds when the
    package is imported from a source checkout. Once ``pip install`` has moved the
    package into site-packages, walking up from ``__file__`` lands in the Python
    installation rather than the repository — so the container sets the variable
    explicitly instead of relying on a path that quietly stops being true.
    """
    override = os.environ.get("GLEANER_CONSOLE_DIR")
    if override:
        path = Path(override)
        return path if path.is_dir() else None
    checkout = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    return checkout if checkout.is_dir() else None


def _mount_console(app: FastAPI) -> None:
    """Serve the built console from the API, so deployment is one artifact.

    Mounted last, and only at the root, so every ``/api`` route above still wins.
    ``html=True`` makes unknown paths fall back to ``index.html``, which is what a
    single-page console needs when someone reloads on a sub-route.

    Absent in development — the console runs under Vite then, which proxies to this
    API. A missing build is therefore normal, not an error, and must not stop the
    control plane from starting.
    """
    console = console_dir()
    if console is not None:
        app.mount("/", StaticFiles(directory=console, html=True), name="console")
