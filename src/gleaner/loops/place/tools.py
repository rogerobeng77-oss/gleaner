# src/gleaner/loops/place/tools.py
"""Tools for the PLACE loop.

Read-only tools let the agent look around freely. The five consequential ones -
commit, dispatch, and the three notifications - are the names the escalation policy
guards, so they must not be renamed without updating ``GUARDED_TOOLS``.

Every tool returns plain JSON-compatible data. Domain reasoning lives in the domain
package; these are thin adapters so the agent never does arithmetic itself.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any

from strands import tool

from gleaner.adapters.base import WorldAdapter
from gleaner.domain.models import FoodLot
from gleaner.domain.perishability import safe_until
from gleaner.domain.scoring import rank_pantries
from gleaner.domain.types import TempClass


def build_place_tools(
    world: WorldAdapter,
    ledger: list[dict[str, Any]],
    fill_ceiling: float | None = None,
) -> list:
    """Build the tool set bound to a world and an append-only action ledger."""

    def _pantry(pantry_id: str):
        return next((p for p in world.pantries() if p.pantry_id == pantry_id), None)

    def _driver(driver_id: str):
        return next((d for d in world.drivers() if d.driver_id == driver_id), None)

    def _donor(donor_id: str):
        return next((d for d in world.donors() if d.donor_id == donor_id), None)

    @tool
    def list_pantries() -> list[dict]:
        """List every pantry with its location, hours and free capacity."""
        return [
            {
                "pantry_id": p.pantry_id,
                "name": p.name,
                "lat": p.lat,
                "lon": p.lon,
                "opens_at_hour": p.opens_at_hour,
                "closes_at_hour": p.closes_at_hour,
                "free_chilled_kg": p.free_kg(TempClass.CHILLED),
                "free_frozen_kg": p.free_kg(TempClass.FROZEN),
                "free_dry_kg": p.free_kg(TempClass.AMBIENT),
                "dietary_restrictions": p.dietary_restrictions,
            }
            for p in world.pantries()
        ]

    @tool
    def check_capacity(pantry_id: str, temp_class: str) -> dict:
        """Check how many kilograms of a given temperature class a pantry can still take.

        temp_class must be one of "chilled", "frozen", "ambient".
        """
        pantry = _pantry(pantry_id)
        if pantry is None:
            return {"error": f"unknown pantry {pantry_id}"}
        return {
            "pantry_id": pantry_id,
            "name": pantry.name,
            "temp_class": temp_class,
            "free_kg": pantry.free_kg(TempClass(temp_class)),
        }

    @tool
    def find_pantries(
        description: str,
        weight_kg: float,
        temp_class: str,
        origin_lat: float,
        origin_lon: float,
        dietary_flags: list[str] | None = None,
        minutes_out_of_refrigeration: float = 0.0,
        ready_at_iso: str | None = None,
    ) -> list[dict]:
        """Rank pantries that can actually take this food, best first, with reasons.

        Pantries that are closed on arrival, lack capacity, cannot be reached before the
        food is unsafe, or conflict on dietary grounds are excluded rather than ranked low.

        The deadline is the real perishability deadline, computed from the food's
        temperature class and how long it has already been out of refrigeration - not a
        nominal window.

        Arrival is measured from the later of now and ready_at: a driver cannot collect
        food before it exists, so an offer that is ready in six hours is judged on
        arriving in six hours' time, not on arriving now.

        The arrival estimate covers the **pickup-to-pantry leg only**. It does not
        include the driver's own run to the pickup point, which find_driver measures
        separately. The margin this returns is therefore optimistic; when it is thin,
        add the driver's distance before trusting it.

        temp_class must be one of "chilled", "frozen", "ambient". weight_kg and
        minutes_out_of_refrigeration are in kilograms and minutes respectively.
        ready_at_iso, if given, must be an ISO-8601 string WITH a timezone offset (a
        naive value raises ValueError from safe_until); omit it to mean "available now".
        """
        lot = FoodLot(
            description=description,
            weight_kg=weight_kg,
            temp_class=TempClass(temp_class),
            dietary_flags=list(dietary_flags or []),
        )
        ready_at = (
            datetime.fromisoformat(ready_at_iso) if ready_at_iso else world.now()
        )
        deadline = safe_until(lot.temp_class, ready_at, minutes_out_of_refrigeration)

        pantries = world.pantries()
        if not pantries:
            return []
        # The driver cannot leave the pickup before the food is ready for collection.
        departs_at = max(world.now(), ready_at)
        distances = {
            p.pantry_id: world.distance_km(origin_lat, origin_lon, p.lat, p.lon) for p in pantries
        }
        etas = {
            p.pantry_id: departs_at
            + timedelta(minutes=world.travel_minutes(origin_lat, origin_lon, p.lat, p.lon))
            for p in pantries
        }
        median_fresh = statistics.median(p.recent_fresh_kg_30d for p in pantries)

        ranked = rank_pantries(
            pantries, lot,
            etas=etas, deadline=deadline,
            distances_km=distances, median_fresh_kg_30d=median_fresh,
            fill_ceiling=fill_ceiling,
        )
        by_id = {p.pantry_id: p for p in pantries}
        return [
            {
                "pantry_id": r.pantry_id,
                "name": by_id[r.pantry_id].name,
                "score": r.score,
                "reasons": r.reasons,
                "distance_km": distances[r.pantry_id],
                "margin_minutes": round(r.margin_minutes, 1),
                "capacity_fraction": round(r.capacity_fraction, 3),
            }
            for r in ranked
        ]

    @tool
    def find_driver(
        origin_lat: float, origin_lon: float, needs_refrigeration: bool = False
    ) -> dict:
        """Find the nearest available driver, honouring a refrigeration requirement."""
        now = world.now()
        candidates = [
            d
            for d in world.drivers()
            if d.is_available_at(now) and (d.refrigerated or not needs_refrigeration)
        ]
        if not candidates:
            return {"error": "no available driver matches the requirement"}
        nearest = min(candidates, key=lambda d: world.distance_km(origin_lat, origin_lon, d.lat, d.lon))
        return {
            "driver_id": nearest.driver_id,
            "name": nearest.name,
            "refrigerated": nearest.refrigerated,
            "distance_km": world.distance_km(origin_lat, origin_lon, nearest.lat, nearest.lon),
        }

    @tool
    def estimate_arrival(
        origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float
    ) -> dict:
        """Estimate travel time and arrival for a pickup-to-pantry leg."""
        minutes = world.travel_minutes(origin_lat, origin_lon, dest_lat, dest_lon)
        return {
            "travel_minutes": minutes,
            "eta": (world.now() + timedelta(minutes=minutes)).isoformat(),
        }

    @tool
    def commit_placement(
        offer_id: str,
        pantry_id: str,
        driver_id: str,
        weight_kg: float,
        eta: str,
        temp_class: str,
        dietary_flags: list[str] | None = None,
        minutes_out_of_refrigeration: float = 0.0,
        ready_at_iso: str | None = None,
        donor_id: str | None = None,
    ) -> dict:
        """Irreversibly commit this donation to a pantry. Consequential: guarded by policy.

        You must describe the food itself, not only where it is going. The policy that
        decides whether this may happen without a human re-derives the cold chain, the
        capacity and the dietary fit from these arguments against the real world, so
        omitting or misstating them does not buy you permission - it costs it.

        Args:
            offer_id: the donation offer being placed.
            pantry_id: destination pantry.
            driver_id: driver who will carry it.
            weight_kg: mass of the placement, in kilograms.
            eta: arrival at the pantry, an ISO-8601 string WITH a timezone offset.
            temp_class: one of "chilled", "frozen", "ambient".
            dietary_flags: dietary markers on the food, e.g. ["pork"]. Empty when none.
            minutes_out_of_refrigeration: cold-chain budget already spent, in minutes.
            ready_at_iso: when the food becomes collectable, an ISO-8601 string WITH a
                timezone offset. Omit to mean "available now".
            donor_id: who is donating. Used only as a fallback: where the world can
                resolve ``offer_id``, the offer record's donor is used instead and
                this is ignored, so naming a well-regarded donor over the top of a
                real offer buys nothing. It matters when the offer cannot be
                resolved, which is the only case in which the policy has no other way
                to look up the donor's history - and a donor it cannot look up is
                treated as never having donated before.
        """
        if _pantry(pantry_id) is None:
            return {"error": f"unknown pantry {pantry_id}"}
        if _driver(driver_id) is None:
            return {"error": f"unknown driver {driver_id}"}
        try:
            TempClass(temp_class)
        except ValueError:
            return {"error": f"unknown temp_class {temp_class!r}; use chilled, frozen or ambient"}
        entry = {
            "kind": "commit_placement",
            "offer_id": offer_id,
            "pantry_id": pantry_id,
            "driver_id": driver_id,
            "donor_id": donor_id,
            "weight_kg": weight_kg,
            "eta": eta,
            "temp_class": temp_class,
            "dietary_flags": list(dietary_flags or []),
            "minutes_out_of_refrigeration": minutes_out_of_refrigeration,
            "ready_at_iso": ready_at_iso,
            "at": world.now().isoformat(),
        }
        ledger.append(entry)
        return {"status": "committed", **entry}

    @tool
    def dispatch_driver(driver_id: str, offer_id: str, pickup_address: str) -> dict:
        """Dispatch a driver to collect. Consequential: guarded by policy."""
        if _driver(driver_id) is None:
            return {"error": f"unknown driver {driver_id}"}
        entry = {
            "kind": "dispatch_driver",
            "driver_id": driver_id,
            "offer_id": offer_id,
            "pickup_address": pickup_address,
            "at": world.now().isoformat(),
        }
        ledger.append(entry)
        return {"status": "dispatched", **entry}

    @tool
    def notify_pantry(pantry_id: str, message: str, offer_id: str | None = None) -> dict:
        """Tell a pantry about a placement. Consequential: guarded by policy.

        Args:
            pantry_id: the pantry to message.
            message: what to tell them.
            offer_id: the placement this message is about. A message that names no
                committed placement is refused before it is sent - a pantry told to
                expect food that was never approved clears shelf space for nothing.
        """
        if _pantry(pantry_id) is None:
            return {"error": f"unknown pantry {pantry_id}"}
        entry = {"kind": "notify_pantry", "pantry_id": pantry_id, "message": message,
                 "offer_id": offer_id, "at": world.now().isoformat()}
        ledger.append(entry)
        return {"status": "sent", **entry}

    @tool
    def notify_donor(donor_id: str, message: str, offer_id: str | None = None) -> dict:
        """Tell a donor about a placement. Consequential: guarded by policy.

        Args:
            donor_id: the donor to message.
            message: what to tell them.
            offer_id: the placement this message is about. A message that names no
                committed placement is refused before it is sent - a donor told their
                donation is collected stops looking for another taker.
        """
        if _donor(donor_id) is None:
            return {"error": f"unknown donor {donor_id}"}
        entry = {"kind": "notify_donor", "donor_id": donor_id, "message": message,
                 "offer_id": offer_id, "at": world.now().isoformat()}
        ledger.append(entry)
        return {"status": "sent", **entry}

    return [
        list_pantries, check_capacity, find_pantries, find_driver, estimate_arrival,
        commit_placement, dispatch_driver, notify_pantry, notify_donor,
    ]
