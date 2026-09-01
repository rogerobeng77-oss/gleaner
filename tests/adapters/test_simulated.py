# tests/adapters/test_simulated.py
from datetime import datetime, timezone

from gleaner.adapters.base import WorldAdapter
from gleaner.adapters.simulated import SimulatedCity
from gleaner.domain.models import DonationOffer, Driver, Pantry

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_simulated_city_satisfies_world_adapter():
    assert isinstance(SimulatedCity(), WorldAdapter)


def test_same_seed_produces_an_identical_world():
    a, b = SimulatedCity(seed=412, now=NOW), SimulatedCity(seed=412, now=NOW)
    assert [p.model_dump() for p in a.pantries()] == [p.model_dump() for p in b.pantries()]


def test_different_seeds_differ():
    a, b = SimulatedCity(seed=1, now=NOW), SimulatedCity(seed=2, now=NOW)
    assert [p.model_dump() for p in a.pantries()] != [p.model_dump() for p in b.pantries()]


def test_world_is_populated():
    city = SimulatedCity(now=NOW)
    assert len(city.pantries()) >= 6
    assert len(city.drivers()) >= 4
    assert len(city.donors()) >= 4
    assert all(isinstance(p, Pantry) for p in city.pantries())
    assert all(isinstance(d, Driver) for d in city.drivers())


def test_geography_is_pittsburgh():
    for pantry in SimulatedCity(now=NOW).pantries():
        assert 40.3 < pantry.lat < 40.6
        assert -80.2 < pantry.lon < -79.7


def test_pantries_vary_in_cold_capacity():
    caps = {p.cold_capacity_kg for p in SimulatedCity(now=NOW).pantries()}
    assert len(caps) > 1


def test_some_pantry_has_a_dietary_restriction():
    assert any(p.dietary_restrictions for p in SimulatedCity(now=NOW).pantries())


def test_distance_between_distinct_points_is_positive():
    city = SimulatedCity(now=NOW)
    assert city.distance_km(40.44, -79.99, 40.46, -79.92) > 0


def test_distance_to_self_is_zero():
    assert SimulatedCity(now=NOW).distance_km(40.44, -79.99, 40.44, -79.99) == 0.0


def test_travel_minutes_grow_with_distance():
    city = SimulatedCity(now=NOW)
    near = city.travel_minutes(40.44, -79.99, 40.45, -79.98)
    far = city.travel_minutes(40.44, -79.99, 40.55, -79.75)
    assert far > near > 0


def test_generated_offer_is_valid_and_deterministic():
    a = SimulatedCity(seed=7, now=NOW).generate_offer(rng_seed=1)
    b = SimulatedCity(seed=7, now=NOW).generate_offer(rng_seed=1)
    assert isinstance(a, DonationOffer)
    assert a.model_dump() == b.model_dump()
    assert a.total_weight_kg > 0


def test_clock_is_fixed_and_aware():
    city = SimulatedCity(now=NOW)
    assert city.now() == NOW
    assert city.now().tzinfo is not None
