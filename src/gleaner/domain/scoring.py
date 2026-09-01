# src/gleaner/domain/scoring.py
"""Ranking pantries for a lot of food.

Two different ideas live here and are kept apart deliberately:

* **Blockers** are absolute. A pantry that is closed on arrival, has no room, or
  cannot accept the food on dietary grounds is not a candidate at any score.
* **Reasons** are preferences that add up to a score, each one phrased so the UI can
  show a coordinator why a pantry was chosen.

The equity term is the opinionated part: pantries that have received less fresh food
than their peers over the last 30 days are boosted, so surplus produce does not
simply flow to whichever site happens to be nearest the depot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from gleaner.domain.capacity import can_accept, utilisation_after
from gleaner.domain.models import FoodLot, Pantry

_BASE_SCORE = 50.0
_MAX_DISTANCE_PENALTY = 25.0
_DISTANCE_FULL_PENALTY_KM = 50.0
_MAX_EQUITY_BONUS = 30.0
_HEADROOM_BONUS = 15.0


@dataclass(frozen=True)
class ScoredPantry:
    pantry_id: str
    score: float
    reasons: list[str]
    blockers: list[str]

    @property
    def is_viable(self) -> bool:
        return not self.blockers


def score_pantry(
    pantry: Pantry,
    lot: FoodLot,
    *,
    eta: datetime,
    deadline: datetime,
    distance_km: float,
    median_fresh_kg_30d: float,
) -> ScoredPantry:
    """Score one pantry as a destination for one lot."""
    blockers: list[str] = []
    reasons: list[str] = []

    if eta > deadline:
        blockers.append(f"arrival {eta:%H:%M} is past the safety deadline {deadline:%H:%M}")
    if not pantry.is_open_at(eta):
        blockers.append(f"closed on arrival at {eta:%H:%M}")
    if not can_accept(pantry, lot):
        blockers.append(
            f"insufficient {lot.temp_class} capacity: "
            f"{pantry.free_kg(lot.temp_class):.0f} kg free, {lot.weight_kg:.0f} kg offered"
        )
    conflicts = sorted(set(lot.dietary_flags) & set(pantry.dietary_restrictions))
    if conflicts:
        blockers.append(f"dietary conflict: {', '.join(conflicts)}")

    if blockers:
        return ScoredPantry(pantry.pantry_id, 0.0, reasons, blockers)

    score = _BASE_SCORE
    reasons.append(f"open until {pantry.closes_at_hour:02d}:00 and has room")

    distance_penalty = min(
        _MAX_DISTANCE_PENALTY,
        _MAX_DISTANCE_PENALTY * (distance_km / _DISTANCE_FULL_PENALTY_KM),
    )
    score -= distance_penalty
    reasons.append(f"{distance_km:.1f} km away")

    if median_fresh_kg_30d > 0:
        deficit = (median_fresh_kg_30d - pantry.recent_fresh_kg_30d) / median_fresh_kg_30d
        if deficit > 0:
            bonus = min(_MAX_EQUITY_BONUS, _MAX_EQUITY_BONUS * deficit)
            score += bonus
            reasons.append(
                f"equity boost: received {pantry.recent_fresh_kg_30d:.0f} kg fresh in 30 days "
                f"against a median of {median_fresh_kg_30d:.0f} kg"
            )

    headroom = 1.0 - utilisation_after(pantry, lot)
    score += _HEADROOM_BONUS * headroom
    reasons.append(f"would sit at {utilisation_after(pantry, lot):.0%} of {lot.temp_class} capacity")

    return ScoredPantry(pantry.pantry_id, round(score, 2), reasons, blockers)


def rank_pantries(
    pantries: list[Pantry],
    lot: FoodLot,
    *,
    etas: dict[str, datetime],
    deadline: datetime,
    distances_km: dict[str, float],
    median_fresh_kg_30d: float,
) -> list[ScoredPantry]:
    """Viable pantries only, best first.

    ``etas`` is per pantry: a nearby site must be judged on when the driver would
    actually reach *it*, not on the longest trip in the set. A pantry with no entry is
    treated as unreachable and excluded.
    """
    scored = [
        score_pantry(
            pantry, lot,
            eta=etas[pantry.pantry_id], deadline=deadline,
            distance_km=distances_km.get(pantry.pantry_id, 0.0),
            median_fresh_kg_30d=median_fresh_kg_30d,
        )
        for pantry in pantries
        if pantry.pantry_id in etas
    ]
    return sorted(
        (candidate for candidate in scored if candidate.is_viable),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
