# src/gleaner/domain/models.py
"""Entity models. Pydantic v2 throughout so agents can emit them as structured output."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from gleaner.domain.perishability import safe_until
from gleaner.domain.types import DecisionOutcome, PlacementStatus, TempClass


class FoodLot(BaseModel):
    """A homogeneous quantity of food sharing one handling class."""

    description: str
    weight_kg: float = Field(gt=0)
    temp_class: TempClass
    allergens: list[str] = Field(default_factory=list)
    dietary_flags: list[str] = Field(default_factory=list)


class Donor(BaseModel):
    donor_id: str
    name: str
    lat: float
    lon: float
    verified: bool = False
    donation_count: int = 0


class DonationOffer(BaseModel):
    offer_id: str
    donor_id: str
    lots: list[FoodLot] = Field(min_length=1)
    ready_at: datetime
    pickup_address: str
    lat: float
    lon: float
    minutes_out_of_refrigeration: float = Field(default=0.0, ge=0)
    stated_expiry: datetime | None = None
    notes: str = ""

    @property
    def total_weight_kg(self) -> float:
        return sum(lot.weight_kg for lot in self.lots)

    def deadline_for(self, lot: FoodLot) -> datetime:
        return safe_until(
            lot.temp_class,
            self.ready_at,
            self.minutes_out_of_refrigeration,
            self.stated_expiry,
        )

    def earliest_deadline(self) -> datetime:
        return min(self.deadline_for(lot) for lot in self.lots)


class Pantry(BaseModel):
    pantry_id: str
    name: str
    lat: float
    lon: float
    cold_capacity_kg: float = Field(ge=0)
    frozen_capacity_kg: float = Field(ge=0)
    dry_capacity_kg: float = Field(ge=0)
    cold_used_kg: float = Field(default=0.0, ge=0)
    frozen_used_kg: float = Field(default=0.0, ge=0)
    dry_used_kg: float = Field(default=0.0, ge=0)
    opens_at_hour: int = Field(ge=0, le=23)
    closes_at_hour: int = Field(ge=0, le=23)
    dietary_restrictions: list[str] = Field(default_factory=list)
    recent_fresh_kg_30d: float = 0.0

    def free_kg(self, temp_class: TempClass) -> float:
        capacity = {
            TempClass.CHILLED: self.cold_capacity_kg,
            TempClass.FROZEN: self.frozen_capacity_kg,
            TempClass.AMBIENT: self.dry_capacity_kg,
        }[temp_class]
        used = {
            TempClass.CHILLED: self.cold_used_kg,
            TempClass.FROZEN: self.frozen_used_kg,
            TempClass.AMBIENT: self.dry_used_kg,
        }[temp_class]
        return max(0.0, capacity - used)

    def is_open_at(self, moment: datetime) -> bool:
        return self.opens_at_hour <= moment.hour < self.closes_at_hour


class Driver(BaseModel):
    driver_id: str
    name: str
    lat: float
    lon: float
    refrigerated: bool = False
    available_from: datetime
    available_until: datetime

    def is_available_at(self, moment: datetime) -> bool:
        return self.available_from <= moment <= self.available_until


class Placement(BaseModel):
    placement_id: str
    offer_id: str
    pantry_id: str
    driver_id: str
    eta: datetime
    status: PlacementStatus = PlacementStatus.PROPOSED


class Decision(BaseModel):
    """An escalation and, once answered, the human's structured response."""

    decision_id: str
    offer_id: str
    rule: str
    prompt: str
    outcome: DecisionOutcome | None = None
    by: str | None = None
    role: str | None = None
    note: str | None = None
    at: datetime | None = None
    override_of: str | None = None

    def answer(
        self,
        outcome: DecisionOutcome,
        *,
        by: str,
        role: str,
        note: str = "",
    ) -> "Decision":
        """Return a copy recording the human's decision. Decisions are never mutated."""
        return self.model_copy(
            update={
                "outcome": outcome,
                "by": by,
                "role": role,
                "note": note,
                "at": datetime.now(timezone.utc),
                "override_of": self.rule,
            }
        )
