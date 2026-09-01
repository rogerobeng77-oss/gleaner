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

from gleaner.domain.models import Donor, Driver, Pantry


@runtime_checkable
class WorldAdapter(Protocol):
    def pantries(self) -> list[Pantry]: ...
    def drivers(self) -> list[Driver]: ...
    def donors(self) -> list[Donor]: ...
    def distance_km(self, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float: ...
    def travel_minutes(self, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float: ...
    def now(self) -> datetime: ...
