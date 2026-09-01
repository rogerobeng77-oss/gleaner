# tests/domain/test_perishability.py
from datetime import datetime, timedelta, timezone

import pytest

from gleaner.domain.perishability import (
    DANGER_ZONE_BUDGET_MINUTES,
    SHELF_LIFE_HOURS,
    is_already_unsafe,
    margin_minutes,
    safe_until,
)
from gleaner.domain.types import TempClass

READY = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_chilled_budget_reduced_by_time_already_out():
    # 240 min budget, 60 already spent -> 180 min remaining
    assert safe_until(TempClass.CHILLED, READY, minutes_out_of_refrigeration=60) == READY + timedelta(minutes=180)


def test_chilled_with_no_prior_exposure_uses_full_budget():
    assert safe_until(TempClass.CHILLED, READY) == READY + timedelta(
        minutes=DANGER_ZONE_BUDGET_MINUTES[TempClass.CHILLED]
    )


def test_frozen_has_a_much_tighter_budget_than_chilled():
    assert DANGER_ZONE_BUDGET_MINUTES[TempClass.FROZEN] < DANGER_ZONE_BUDGET_MINUTES[TempClass.CHILLED]
    assert safe_until(TempClass.FROZEN, READY) == READY + timedelta(minutes=60)


def test_ambient_is_governed_by_shelf_life_not_danger_zone():
    expected = READY + timedelta(hours=SHELF_LIFE_HOURS[TempClass.AMBIENT])
    assert safe_until(TempClass.AMBIENT, READY, minutes_out_of_refrigeration=600) == expected


def test_stated_expiry_caps_the_computed_window():
    early = READY + timedelta(minutes=30)
    assert safe_until(TempClass.CHILLED, READY, stated_expiry=early) == early


def test_stated_expiry_does_not_extend_the_window():
    late = READY + timedelta(days=5)
    assert safe_until(TempClass.CHILLED, READY, stated_expiry=late) == READY + timedelta(minutes=240)


def test_exhausted_budget_means_already_unsafe():
    assert is_already_unsafe(TempClass.CHILLED, 240)
    assert is_already_unsafe(TempClass.CHILLED, 999)
    assert not is_already_unsafe(TempClass.CHILLED, 239)
    assert not is_already_unsafe(TempClass.AMBIENT, 999)


def test_already_unsafe_food_gets_no_window():
    assert safe_until(TempClass.CHILLED, READY, minutes_out_of_refrigeration=300) == READY


def test_negative_exposure_is_rejected():
    with pytest.raises(ValueError):
        safe_until(TempClass.CHILLED, READY, minutes_out_of_refrigeration=-1)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        safe_until(TempClass.CHILLED, datetime(2026, 9, 1, 12, 0))


def test_margin_is_positive_when_arrival_beats_the_deadline():
    assert margin_minutes(READY + timedelta(minutes=90), READY + timedelta(minutes=30)) == 60.0


def test_margin_is_negative_when_arrival_is_late():
    assert margin_minutes(READY + timedelta(minutes=30), READY + timedelta(minutes=90)) == -60.0
