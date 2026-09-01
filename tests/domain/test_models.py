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


def test_pantry_opening_hours_are_read_in_the_offset_the_caller_passes():
    """Pinning a real sharp edge rather than leaving it to be rediscovered.

    ``is_open_at`` compares ``moment.hour``, so opening hours mean whatever the
    caller's UTC offset says they mean. One instant, two offsets, two answers: a
    Pittsburgh pantry open 08:00-18:00 is shut at 21:00 UTC and open at the same
    instant written in EDT. Callers must pass local time. This is documented on the
    method and asserted here so a future change to the domain cannot quietly alter
    it without a failing test.
    """
    pantry = Pantry(
        pantry_id="pan-tz", name="Hill District", lat=40.44, lon=-79.97,
        cold_capacity_kg=500, frozen_capacity_kg=0, dry_capacity_kg=0,
        opens_at_hour=8, closes_at_hour=18,
    )
    edt = timezone(timedelta(hours=-4))
    as_utc = datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc)
    as_local = as_utc.astimezone(edt)

    assert as_utc == as_local                       # the same instant, twice over
    assert not pantry.is_open_at(as_utc)            # 21:00 by the hour: shut
    assert pantry.is_open_at(as_local)              # 17:00 by the hour: open


def test_pantry_open_at_handles_overnight_window_late_evening():
    pantry = Pantry(
        pantry_id="pan-2", name="Overnight", lat=40.46, lon=-79.92,
        cold_capacity_kg=500, frozen_capacity_kg=0, dry_capacity_kg=0,
        opens_at_hour=22, closes_at_hour=6,
    )
    assert pantry.is_open_at(NOW.replace(hour=23))


def test_pantry_open_at_handles_overnight_window_early_hours():
    pantry = Pantry(
        pantry_id="pan-2", name="Overnight", lat=40.46, lon=-79.92,
        cold_capacity_kg=500, frozen_capacity_kg=0, dry_capacity_kg=0,
        opens_at_hour=22, closes_at_hour=6,
    )
    assert pantry.is_open_at(NOW.replace(hour=3))


def test_pantry_open_at_overnight_window_closed_in_afternoon():
    pantry = Pantry(
        pantry_id="pan-2", name="Overnight", lat=40.46, lon=-79.92,
        cold_capacity_kg=500, frozen_capacity_kg=0, dry_capacity_kg=0,
        opens_at_hour=22, closes_at_hour=6,
    )
    assert not pantry.is_open_at(NOW.replace(hour=14))


def test_pantry_open_at_equal_open_and_close_hour_means_always_open():
    pantry = Pantry(
        pantry_id="pan-3", name="AlwaysOpen", lat=40.46, lon=-79.92,
        cold_capacity_kg=500, frozen_capacity_kg=0, dry_capacity_kg=0,
        opens_at_hour=9, closes_at_hour=9,
    )
    assert pantry.is_open_at(NOW.replace(hour=3))
    assert pantry.is_open_at(NOW.replace(hour=9))
    assert pantry.is_open_at(NOW.replace(hour=23))


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
