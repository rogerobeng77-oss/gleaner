"""The autonomy boundary, expressed as code rather than as prompt text.

Every rule has a stable name so the decision inbox can explain in plain English why a
human is being asked. Two rules cannot be dialled away at any autonomy level: food
safety liability and dietary consent are not preferences.
"""

from __future__ import annotations

from dataclasses import dataclass

from gleaner.domain.types import AutonomyLevel


@dataclass(frozen=True)
class Thresholds:
    max_autonomous_kg: float
    required_margin_minutes: float
    min_safety_confidence: float
    max_capacity_fraction: float
    allow_novel_donor: bool


THRESHOLDS: dict[AutonomyLevel, Thresholds] = {
    AutonomyLevel.CONSERVATIVE: Thresholds(25.0, 120.0, 0.95, 0.50, False),
    AutonomyLevel.BALANCED: Thresholds(150.0, 60.0, 0.85, 0.80, False),
    AutonomyLevel.TRUSTED: Thresholds(500.0, 30.0, 0.70, 0.95, True),
}

#: Rules that fire regardless of autonomy level. Safety and consent are not knobs.
NON_NEGOTIABLE_RULES: frozenset[str] = frozenset({"liability_uncertain", "dietary_conflict"})


@dataclass(frozen=True)
class PlacementContext:
    """Everything the policy needs to judge one proposed placement."""

    weight_kg: float
    margin_minutes: float
    safety_confidence: float
    capacity_fraction: float
    liability_ok: bool
    dietary_ok: bool
    donor_is_novel: bool
    has_viable_match: bool


@dataclass(frozen=True)
class RuleHit:
    rule: str
    detail: str


def evaluate_rules(ctx: PlacementContext, level: AutonomyLevel) -> list[RuleHit]:
    """Every rule that requires a human for this placement at this autonomy level.

    An empty list means the agent may act alone.
    """
    limits = THRESHOLDS[level]
    hits: list[RuleHit] = []

    # Non-negotiable first, so they read first in the inbox.
    if not ctx.liability_ok:
        hits.append(
            RuleHit(
                "liability_uncertain",
                "Bill Emerson Act protection is not clearly established for this donation.",
            )
        )
    if not ctx.dietary_ok:
        hits.append(
            RuleHit("dietary_conflict", "The food conflicts with the pantry's dietary restrictions.")
        )

    if not ctx.has_viable_match:
        hits.append(
            RuleHit("no_viable_match", "No pantry can take this food in time; someone must call around.")
        )
    if ctx.weight_kg > limits.max_autonomous_kg:
        hits.append(
            RuleHit(
                "over_value_threshold",
                f"{ctx.weight_kg:.0f} kg exceeds the {level} limit of {limits.max_autonomous_kg:.0f} kg.",
            )
        )
    if ctx.margin_minutes < limits.required_margin_minutes:
        hits.append(
            RuleHit(
                "tight_window",
                f"Only {ctx.margin_minutes:.0f} minutes of slack; {level} requires "
                f"{limits.required_margin_minutes:.0f}.",
            )
        )
    if ctx.safety_confidence < limits.min_safety_confidence:
        hits.append(
            RuleHit(
                "safety_ambiguous",
                f"Safety confidence {ctx.safety_confidence:.2f} is below the {level} "
                f"threshold of {limits.min_safety_confidence:.2f}.",
            )
        )
    if ctx.capacity_fraction > limits.max_capacity_fraction:
        hits.append(
            RuleHit(
                "capacity_overrun",
                f"Placement would fill {ctx.capacity_fraction:.0%} of the store; "
                f"{level} allows {limits.max_capacity_fraction:.0%}.",
            )
        )
    if ctx.donor_is_novel and not limits.allow_novel_donor:
        hits.append(RuleHit("novel_donor", "First donation from this donor; not yet verified."))

    return hits
