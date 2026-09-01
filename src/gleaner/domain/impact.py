"""Counterfactual impact accounting.

Two numbers matter to a coordinator: how many meals this became, and what would have
happened without the agent. Both are computed from published conversion factors rather
than invented, and every figure in the UI can be traced back to this module.

Sources:
  * 1.2 lb (0.544 kg) of food per meal - Feeding America's standard conversion, also
    used by ReFED when reporting meals from rescued food.
  * 2.5 kg CO2e avoided per kg of food diverted from landfill - EPA WARM mid-range
    figure for mixed food waste.
"""

from __future__ import annotations

from dataclasses import dataclass

KG_PER_MEAL: float = 0.544
CO2E_KG_PER_KG_FOOD: float = 2.5


def meals_from_kg(kg: float) -> int:
    """Whole meals represented by a mass of rescued food. Partial meals do not count.

    Adds a 1e-9 epsilon before truncating so that IEEE-754 rounding noise (e.g.
    54.4 / 0.544 landing on 99.99999999999999 instead of 100) doesn't shave a whole
    meal off an exact multiple; the epsilon is far too small to promote a genuinely
    partial meal.
    """
    return int(kg / KG_PER_MEAL + 1e-9)


def co2e_avoided_kg(kg: float) -> float:
    """Kilograms of CO2e avoided by keeping this food out of landfill."""
    return round(kg * CO2E_KG_PER_KG_FOOD, 2)


@dataclass(frozen=True)
class ImpactEntry:
    offer_id: str
    kg: float
    meals: int
    co2e_kg: float
    counterfactual_kg_wasted: float


def summarise(entries: list[ImpactEntry]) -> dict:
    """Totals across a set of placements."""
    return {
        "placements": len(entries),
        "kg_rescued": round(sum(e.kg for e in entries), 2),
        "meals": sum(e.meals for e in entries),
        "co2e_kg": round(sum(e.co2e_kg for e in entries), 2),
        "counterfactual_kg_wasted": round(sum(e.counterfactual_kg_wasted for e in entries), 2),
    }
