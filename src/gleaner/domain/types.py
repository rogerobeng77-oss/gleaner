# src/gleaner/domain/types.py
"""Core enumerations shared across the whole domain."""

from __future__ import annotations

from enum import StrEnum


class TempClass(StrEnum):
    """Temperature handling class for a lot of food."""

    AMBIENT = "ambient"
    CHILLED = "chilled"
    FROZEN = "frozen"


class AutonomyLevel(StrEnum):
    """How much authority the coordinator has delegated to the agent."""

    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    TRUSTED = "trusted"

    @classmethod
    def order(cls) -> list["AutonomyLevel"]:
        """Least to most permissive."""
        return [cls.CONSERVATIVE, cls.BALANCED, cls.TRUSTED]


class DecisionOutcome(StrEnum):
    """What a human decided when the agent escalated."""

    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


class PlacementStatus(StrEnum):
    """Lifecycle of a placement."""

    PROPOSED = "proposed"
    AWAITING_DECISION = "awaiting_decision"
    COMMITTED = "committed"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        return self in {PlacementStatus.DELIVERED, PlacementStatus.CANCELLED}
