"""Process-wide state for the control plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gleaner.adapters.simulated import SimulatedCity
from gleaner.domain.types import AutonomyLevel
from gleaner.hooks.audit import AuditTrail
from gleaner.persistence.inbox import DecisionInbox


@dataclass
class GleanerState:
    world: SimulatedCity = field(default_factory=SimulatedCity)
    inbox: DecisionInbox = field(default_factory=DecisionInbox)
    trail: AuditTrail = field(default_factory=AuditTrail)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    impact: list[Any] = field(default_factory=list)
    autonomy: AutonomyLevel = AutonomyLevel.BALANCED
