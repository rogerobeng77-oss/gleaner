# src/gleaner/domain/perishability.py
"""The perishability clock.

Encodes published cold-chain guidance as arithmetic so the rest of the system can
reason about deadlines without asking a language model to do time maths.

Chilled food carries a cumulative budget of time above 5 degrees C; once that budget
is spent the food is no longer safe to redistribute. Frozen food has a far tighter
budget. Ambient food has no danger-zone budget and is bounded by shelf life alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from gleaner.domain.types import TempClass

#: Nominal shelf life from the moment the food is ready for collection.
SHELF_LIFE_HOURS: dict[TempClass, float] = {
    TempClass.AMBIENT: 72.0,
    TempClass.CHILLED: 24.0,
    TempClass.FROZEN: 168.0,
}

#: Cumulative minutes the food may spend above safe holding temperature.
#: ``inf`` for ambient, which has no cold chain to break.
DANGER_ZONE_BUDGET_MINUTES: dict[TempClass, float] = {
    TempClass.AMBIENT: float("inf"),
    TempClass.CHILLED: 240.0,
    TempClass.FROZEN: 60.0,
}


def _require_aware(moment: datetime, label: str) -> None:
    if moment.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")


def is_already_unsafe(temp_class: TempClass, minutes_out_of_refrigeration: float) -> bool:
    """True when the danger-zone budget is exhausted before we even start."""
    return minutes_out_of_refrigeration >= DANGER_ZONE_BUDGET_MINUTES[temp_class]


def safe_until(
    temp_class: TempClass,
    ready_at: datetime,
    minutes_out_of_refrigeration: float = 0.0,
    stated_expiry: datetime | None = None,
) -> datetime:
    """The latest moment this food may still be safely distributed.

    Args:
        temp_class: handling class of the lot.
        ready_at: when the food becomes available for collection.
        minutes_out_of_refrigeration: cold-chain budget already spent.
        stated_expiry: donor-stated expiry, which may only shorten the window.

    Returns:
        A timezone-aware deadline. Equal to ``ready_at`` when the food is already unsafe.
    """
    _require_aware(ready_at, "ready_at")
    if stated_expiry is not None:
        _require_aware(stated_expiry, "stated_expiry")
    if minutes_out_of_refrigeration < 0:
        raise ValueError("minutes_out_of_refrigeration cannot be negative")

    if is_already_unsafe(temp_class, minutes_out_of_refrigeration):
        return ready_at

    remaining_budget = DANGER_ZONE_BUDGET_MINUTES[temp_class] - minutes_out_of_refrigeration
    shelf_deadline = ready_at + timedelta(hours=SHELF_LIFE_HOURS[temp_class])

    deadline = shelf_deadline
    if remaining_budget != float("inf"):
        deadline = min(deadline, ready_at + timedelta(minutes=remaining_budget))
    if stated_expiry is not None:
        deadline = min(deadline, stated_expiry)
    return deadline


def margin_minutes(safe_until_at: datetime, arrival_at: datetime) -> float:
    """Minutes of slack between arrival and the deadline. Negative means too late."""
    _require_aware(safe_until_at, "safe_until_at")
    _require_aware(arrival_at, "arrival_at")
    return (safe_until_at - arrival_at).total_seconds() / 60.0
