# src/gleaner/adapters/base.py
"""The seam between Gleaner and the world.

One implementation exists today: ``SimulatedCity``, a deterministic Pittsburgh. The
protocol is narrow on purpose so that the two the design calls for - one backed by
real public geodata, one by live integrations with donors, pantries and drivers -
can be written later without the agent graph noticing which it is talking to.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from gleaner.domain.models import DonationOffer, Donor, Driver, Pantry


@runtime_checkable
class WorldAdapter(Protocol):
    def pantries(self) -> list[Pantry]: ...
    def drivers(self) -> list[Driver]: ...
    def donors(self) -> list[Donor]: ...

    def offer(self, offer_id: str | None) -> DonationOffer | None:
        """The donation offer with this id, or ``None`` if the world does not know it.

        Declared here rather than duck-typed because the escalation policy leans on
        it: the offer record is what lets the guard union the agent's dietary flags
        with the ones the donor actually declared, take the *longer* of the two
        cold-chain figures, and read the donor's real history. An adapter that
        silently lacked this method would lose that defence with no type error and
        no failing test, and the agent's own account of the food would become the
        only account. Return ``None`` for an id you cannot resolve - the caller
        treats "unknown" as a reason to escalate, never as a reason to relax.
        """
        ...

    def register_offer(self, offer: DonationOffer) -> None:
        """Record an offer so that ``offer()`` can resolve it afterwards.

        Offers arrive two ways - generated inside a simulated world, or posted in
        from outside through the API - and both must end up in the same registry, or
        the defence above holds on one path and not the other.
        """
        ...

    def distance_km(self, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float: ...
    def travel_minutes(self, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float: ...
    def now(self) -> datetime: ...
