# src/gleaner/adapters/simulated.py
"""A deterministic Pittsburgh.

Chosen deliberately: Pittsburgh is where 412 Food Rescue and the Food Rescue Hero
platform were built, so demonstrating autonomous coordination here is a direct
comparison against the leading incumbent on its own ground.

Everything is seeded, so a judge running this repo sees exactly the scenarios the
video shows.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from gleaner.domain.models import Donor, DonationOffer, Driver, FoodLot, Pantry
from gleaner.domain.types import TempClass

_NEIGHBOURHOODS = [
    ("Hill District", 40.4419, -79.9739),
    ("Homewood", 40.4570, -79.8945),
    ("Lawrenceville", 40.4699, -79.9608),
    ("Hazelwood", 40.4084, -79.9436),
    ("Northside", 40.4553, -80.0089),
    ("Beechview", 40.4113, -80.0246),
    ("Oakland", 40.4416, -79.9553),
    ("Squirrel Hill", 40.4384, -79.9223),
]

_DONOR_KINDS = [
    ("Giant Eagle", "grocery"),
    ("Strip District Produce", "wholesale"),
    ("Penn Ave Bakery", "bakery"),
    ("Steel City Catering", "catering"),
    ("Sysco Pittsburgh", "distributor"),
]

_LOT_TEMPLATES = [
    ("mixed salad greens", TempClass.CHILLED, 20.0, 90.0, [], []),
    ("bagged carrots", TempClass.CHILLED, 15.0, 60.0, [], []),
    ("day-old sourdough", TempClass.AMBIENT, 8.0, 40.0, ["gluten"], []),
    ("frozen chicken portions", TempClass.FROZEN, 30.0, 120.0, [], ["halal-unknown"]),
    ("pork sausages", TempClass.CHILLED, 12.0, 50.0, [], ["pork"]),
    ("tinned tomatoes", TempClass.AMBIENT, 40.0, 200.0, [], []),
    ("yoghurt pots", TempClass.CHILLED, 10.0, 45.0, ["dairy"], []),
]

_EARTH_RADIUS_KM = 6371.0
_CITY_AVERAGE_SPEED_KMH = 28.0


class SimulatedCity:
    """A seeded, fully deterministic city."""

    def __init__(self, seed: int = 412, now: datetime | None = None) -> None:
        self.seed = seed
        self._now = now or datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        rng = random.Random(seed)
        self._pantries = self._build_pantries(rng)
        self._drivers = self._build_drivers(rng)
        self._donors = self._build_donors(rng)

    # -- world -------------------------------------------------------------

    def _build_pantries(self, rng: random.Random) -> list[Pantry]:
        pantries: list[Pantry] = []
        for index, (name, lat, lon) in enumerate(_NEIGHBOURHOODS):
            cold = rng.choice([120.0, 250.0, 400.0, 600.0])
            pantries.append(
                Pantry(
                    pantry_id=f"pan-{index + 1}",
                    name=f"{name} Pantry",
                    lat=lat,
                    lon=lon,
                    cold_capacity_kg=cold,
                    frozen_capacity_kg=rng.choice([0.0, 80.0, 200.0]),
                    dry_capacity_kg=rng.choice([400.0, 800.0, 1500.0]),
                    cold_used_kg=round(cold * rng.uniform(0.1, 0.7), 1),
                    opens_at_hour=rng.choice([8, 9, 10]),
                    closes_at_hour=rng.choice([16, 17, 18, 19]),
                    dietary_restrictions=rng.choice([[], [], ["pork"], ["pork", "alcohol"]]),
                    recent_fresh_kg_30d=round(rng.uniform(20.0, 400.0), 1),
                )
            )
        return pantries

    def _build_drivers(self, rng: random.Random) -> list[Driver]:
        names = ["Ama", "Marcus", "Priya", "Dee", "Yusuf", "Renata"]
        drivers: list[Driver] = []
        for index, name in enumerate(names):
            _, lat, lon = rng.choice(_NEIGHBOURHOODS)
            start = self._now - timedelta(minutes=rng.randint(0, 60))
            drivers.append(
                Driver(
                    driver_id=f"drv-{index + 1}",
                    name=name,
                    lat=lat,
                    lon=lon,
                    refrigerated=rng.random() < 0.4,
                    available_from=start,
                    available_until=start + timedelta(hours=rng.randint(3, 8)),
                )
            )
        return drivers

    def _build_donors(self, rng: random.Random) -> list[Donor]:
        donors: list[Donor] = []
        for index, (name, _kind) in enumerate(_DONOR_KINDS):
            _, lat, lon = rng.choice(_NEIGHBOURHOODS)
            count = rng.choice([0, 1, 5, 22, 60])
            donors.append(
                Donor(
                    donor_id=f"don-{index + 1}",
                    name=name,
                    lat=lat,
                    lon=lon,
                    verified=count > 0,
                    donation_count=count,
                )
            )
        return donors

    # -- WorldAdapter ------------------------------------------------------

    def pantries(self) -> list[Pantry]:
        return list(self._pantries)

    def drivers(self) -> list[Driver]:
        return list(self._drivers)

    def donors(self) -> list[Donor]:
        return list(self._donors)

    def now(self) -> datetime:
        return self._now

    def distance_km(self, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
        """Great-circle distance. The public-data adapter replaces this with real routing."""
        phi1, phi2 = math.radians(a_lat), math.radians(b_lat)
        d_phi = math.radians(b_lat - a_lat)
        d_lambda = math.radians(b_lon - a_lon)
        h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return round(2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h)), 3)

    def travel_minutes(self, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
        """Straight-line distance inflated by 1.3 to approximate street routing."""
        km = self.distance_km(a_lat, a_lon, b_lat, b_lon) * 1.3
        return round((km / _CITY_AVERAGE_SPEED_KMH) * 60.0, 1)

    # -- scenario generation ----------------------------------------------

    def generate_offer(self, rng_seed: int | None = None) -> DonationOffer:
        """A plausible donation offer. Deterministic for a given rng_seed."""
        rng = random.Random(self.seed if rng_seed is None else rng_seed)
        donor = rng.choice(self._donors)
        lots = []
        for description, temp, low, high, allergens, dietary in rng.sample(
            _LOT_TEMPLATES, k=rng.randint(1, 3)
        ):
            lots.append(
                FoodLot(
                    description=description,
                    weight_kg=round(rng.uniform(low, high), 1),
                    temp_class=temp,
                    allergens=list(allergens),
                    dietary_flags=list(dietary),
                )
            )
        return DonationOffer(
            offer_id=f"off-{rng.randint(1000, 9999)}",
            donor_id=donor.donor_id,
            lots=lots,
            ready_at=self._now + timedelta(minutes=rng.choice([0, 15, 30, 60])),
            pickup_address=f"{donor.name}, Pittsburgh, PA",
            lat=donor.lat,
            lon=donor.lon,
            minutes_out_of_refrigeration=float(rng.choice([0, 0, 30, 90, 200])),
            notes=rng.choice(["", "back dock, ask for the duty manager", "must go today"]),
        )
