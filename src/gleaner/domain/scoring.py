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
from gleaner.domain.perishability import margin_minutes as _margin_minutes

_BASE_SCORE = 50.0
_MAX_DISTANCE_PENALTY = 25.0
_DISTANCE_FULL_PENALTY_KM = 50.0
_MAX_EQUITY_BONUS = 30.0
_HEADROOM_BONUS = 15.0
#: Demotion for a placement the current autonomy level would not commit unattended.
#: Larger than every bonus this module can award combined, so a pantry that would
#: breach the fill ceiling always ranks below one that would not - but it stays in
#: the list, because when nothing else can take the food a coordinator still has to
#: be offered the choice.
_OVER_CEILING_PENALTY = 60.0


@dataclass(frozen=True)
class ScoredPantry:
    """One pantry judged as a destination for one lot.

    ``reasons`` and ``blockers`` are prose for a human to read. ``margin_minutes``
    and ``capacity_fraction`` are the same facts as numbers, so a caller that has to
    *decide* something (the escalation policy, most importantly) does not have to
    parse them back out of English. Both are populated even for a blocked pantry,
    because "how badly did it miss" is exactly what a coordinator wants to know.

    Attributes:
        margin_minutes: minutes between arrival and the safety deadline. Negative
            means the food would arrive already unsafe.
        capacity_fraction: fraction of the relevant store (chilled / frozen / dry)
            that would be in use after this placement, in [0.0, 1.0].
    """

    pantry_id: str
    score: float
    reasons: list[str]
    blockers: list[str]
    margin_minutes: float
    capacity_fraction: float

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
    fill_ceiling: float | None = None,
) -> ScoredPantry:
    """Score one pantry as a destination for one lot.

    ``fill_ceiling`` is the fraction of a store the current autonomy level will let
    the agent fill unattended. Passing it keeps the ranking and the escalation policy
    in agreement: without it the equity term will happily recommend the emptiest-fed
    pantry even when that placement is one the policy is certain to stop, so the
    agent proposes a placement it cannot make and a human is woken for nothing.
    """
    blockers: list[str] = []
    reasons: list[str] = []
    margin = _margin_minutes(deadline, eta)
    capacity_fraction = utilisation_after(pantry, lot)

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
        return ScoredPantry(pantry.pantry_id, 0.0, reasons, blockers, margin, capacity_fraction)

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

    headroom = 1.0 - capacity_fraction
    score += _HEADROOM_BONUS * headroom
    reasons.append(f"would sit at {capacity_fraction:.0%} of {lot.temp_class} capacity")

    if fill_ceiling is not None and capacity_fraction > fill_ceiling:
        score -= _OVER_CEILING_PENALTY
        reasons.append(
            f"over the {fill_ceiling:.0%} unattended fill limit, so this one needs a human"
        )

    return ScoredPantry(
        pantry.pantry_id, round(score, 2), reasons, blockers, margin, capacity_fraction
    )


def rank_pantries(
    pantries: list[Pantry],
    lot: FoodLot,
    *,
    etas: dict[str, datetime],
    deadline: datetime,
    distances_km: dict[str, float],
    median_fresh_kg_30d: float,
    fill_ceiling: float | None = None,
) -> list[ScoredPantry]:
    """Viable pantries only, best first.

    ``etas`` is per pantry: a nearby site must be judged on when the driver would
    actually reach *it*, not on the longest trip in the set. A pantry missing from
    either ``etas`` or ``distances_km`` is treated as unreachable/unmeasurable and
    excluded.
    """
    scored = [
        score_pantry(
            pantry, lot,
            eta=etas[pantry.pantry_id], deadline=deadline,
            distance_km=distances_km[pantry.pantry_id],
            median_fresh_kg_30d=median_fresh_kg_30d,
            fill_ceiling=fill_ceiling,
        )
        for pantry in pantries
        if pantry.pantry_id in etas and pantry.pantry_id in distances_km
    ]
    return sorted(
        (candidate for candidate in scored if candidate.is_viable),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
