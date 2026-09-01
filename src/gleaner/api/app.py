"""The control plane the coordinator's browser talks to.

Deliberately thin: it reads state the agents produce and writes exactly one thing
back - a human's decision. Presentation-agnostic, so the frontend can be designed
independently.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from gleaner.api.state import GleanerState
from gleaner.domain.impact import summarise
from gleaner.domain.types import AutonomyLevel, DecisionOutcome
from gleaner.policy.rules import THRESHOLDS


class AnswerRequest(BaseModel):
    outcome: DecisionOutcome
    by: str
    role: str
    note: str = ""


class AutonomyRequest(BaseModel):
    level: AutonomyLevel


def create_app() -> FastAPI:
    app = FastAPI(title="Gleaner", version="0.1.0")
    app.state.gleaner = GleanerState()

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
