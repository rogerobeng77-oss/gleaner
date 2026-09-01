# tests/domain/test_capacity.py
from gleaner.domain.capacity import can_accept, shortfall_kg, utilisation_after
from gleaner.domain.models import FoodLot, Pantry
from gleaner.domain.types import TempClass


def _pantry(**kw):
    base = dict(
        pantry_id="pan-1", name="Eastside", lat=40.46, lon=-79.92,
        cold_capacity_kg=100.0, frozen_capacity_kg=50.0, dry_capacity_kg=200.0,
        opens_at_hour=9, closes_at_hour=18,
    )
    return Pantry(**{**base, **kw})


def _lot(weight, temp=TempClass.CHILLED):
    return FoodLot(description="x", weight_kg=weight, temp_class=temp)


def test_accepts_when_it_fits():
    assert can_accept(_pantry(), _lot(80.0))


def test_rejects_when_it_does_not_fit():
    assert not can_accept(_pantry(cold_used_kg=50.0), _lot(80.0))


def test_exact_fit_is_accepted():
    assert can_accept(_pantry(cold_used_kg=20.0), _lot(80.0))


def test_utilisation_after_placement():
    assert utilisation_after(_pantry(cold_used_kg=20.0), _lot(60.0)) == 0.8


def test_utilisation_is_one_when_capacity_is_zero():
    assert utilisation_after(_pantry(frozen_capacity_kg=0.0), _lot(1.0, TempClass.FROZEN)) == 1.0


def test_shortfall_is_zero_when_it_fits():
    assert shortfall_kg(_pantry(), _lot(80.0)) == 0.0


def test_shortfall_reports_the_overflow():
    assert shortfall_kg(_pantry(cold_used_kg=50.0), _lot(80.0)) == 30.0
