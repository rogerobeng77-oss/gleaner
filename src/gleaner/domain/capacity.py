# src/gleaner/domain/capacity.py
"""Storage capacity arithmetic.

A pantry that cannot physically hold the food is not a match, no matter how well it
scores on every other axis. Keeping this separate from scoring makes the distinction
between a blocker and a preference explicit.
"""

from __future__ import annotations

from gleaner.domain.models import FoodLot, Pantry


def _capacity_kg(pantry: Pantry, lot: FoodLot) -> float:
    return {
        "chilled": pantry.cold_capacity_kg,
        "frozen": pantry.frozen_capacity_kg,
        "ambient": pantry.dry_capacity_kg,
    }[str(lot.temp_class)]


def can_accept(pantry: Pantry, lot: FoodLot) -> bool:
    """Whether the pantry has physical room for the whole lot."""
    return pantry.free_kg(lot.temp_class) >= lot.weight_kg


def shortfall_kg(pantry: Pantry, lot: FoodLot) -> float:
    """How much of the lot would not fit. Zero when it fits."""
    return max(0.0, lot.weight_kg - pantry.free_kg(lot.temp_class))


def utilisation_after(pantry: Pantry, lot: FoodLot) -> float:
    """Fraction of the relevant store that would be in use after placement.

    Returns 1.0 when the pantry has no capacity of that class at all, so callers
    treat "no freezer" and "full freezer" identically.
    """
    capacity = _capacity_kg(pantry, lot)
    if capacity <= 0:
        return 1.0
    used = capacity - pantry.free_kg(lot.temp_class)
    return min(1.0, (used + lot.weight_kg) / capacity)
