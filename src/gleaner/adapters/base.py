# src/gleaner/adapters/base.py
"""The seam between Gleaner and the world.

Three implementations sit behind this: a deterministic simulation, real public
geodata, and live integrations. The agent graph never knows which one it is talking to.
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
