# tests/domain/test_models.py
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from gleaner.domain.models import Decision, DonationOffer, Driver, FoodLot, Pantry, Placement
from gleaner.domain.types import DecisionOutcome, PlacementStatus, TempClass

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _lot(**kw):
    base = dict(description="romaine", weight_kg=40.0, temp_class=TempClass.CHILLED)
    return FoodLot(**{**base, **kw})


def _offer(**kw):
    base = dict(
        offer_id="off-1",
        donor_id="don-1",
        lots=[_lot()],
        ready_at=NOW,
        pickup_address="1 Penn Ave, Pittsburgh, PA",
        lat=40.4406,
        lon=-79.9959,
    )
    return DonationOffer(**{**base, **kw})


def test_offer_totals_weight_across_lots():
    offer = _offer(lots=[_lot(weight_kg=40.0), _lot(weight_kg=12.5)])
    assert offer.total_weight_kg == 52.5


def test_offer_computes_a_deadline_per_lot():
    offer = _offer(minutes_out_of_refrigeration=60)
    assert offer.deadline_for(offer.lots[0]) == NOW + timedelta(minutes=180)


def test_offer_deadline_is_the_earliest_across_lots():
    offer = _offer(lots=[_lot(temp_class=TempClass.AMBIENT), _lot(temp_class=TempClass.FROZEN)])
    assert offer.earliest_deadline() == NOW + timedelta(minutes=60)


def test_negative_weight_rejected():
    with pytest.raises(ValidationError):
        _lot(weight_kg=-1.0)


def test_pantry_reports_free_capacity_by_class():
    pantry = Pantry(
        pantry_id="pan-1",
        name="Eastside",
        lat=40.46,
        lon=-79.92,
        cold_capacity_kg=500,
        cold_used_kg=120,
        frozen_capacity_kg=200,
        dry_capacity_kg=1000,
        opens_at_hour=9,
        closes_at_hour=18,
    )
    assert pantry.free_kg(TempClass.CHILLED) == 380
    assert pantry.free_kg(TempClass.FROZEN) == 200


def test_pantry_open_at_respects_hours():
    pantry = Pantry(
        pantry_id="pan-1", name="Eastside", lat=40.46, lon=-79.92,
        cold_capacity_kg=500, frozen_capacity_kg=0, dry_capacity_kg=0,
        opens_at_hour=9, closes_at_hour=18,
    )
    assert pantry.is_open_at(NOW)                                    # 12:00
    assert not pantry.is_open_at(NOW.replace(hour=20))


def test_driver_window_containment():
    driver = Driver(
        driver_id="drv-1", name="Ama", lat=40.44, lon=-79.99, refrigerated=True,
        available_from=NOW, available_until=NOW + timedelta(hours=3),
    )
    assert driver.is_available_at(NOW + timedelta(hours=1))
    assert not driver.is_available_at(NOW + timedelta(hours=4))


def test_placement_defaults_to_proposed():
    p = Placement(placement_id="pl-1", offer_id="off-1", pantry_id="pan-1", driver_id="drv-1", eta=NOW)
    assert p.status is PlacementStatus.PROPOSED


def test_decision_starts_unanswered_and_records_who_answered():
    d = Decision(decision_id="dec-1", offer_id="off-1", rule="tight_window", prompt="Approve?")
    assert d.outcome is None
    answered = d.answer(DecisionOutcome.APPROVE, by="Ama", role="coordinator", note="called pantry")
    assert answered.outcome is DecisionOutcome.APPROVE
    assert answered.by == "Ama"
    assert answered.at is not None
