"""A replayable record of everything the agent did.

Hooks give us this essentially for free, and it is what turns "the agent decided" into
"here is exactly how the agent decided" - for the coordinator, for the timeline view,
and for the demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from strands.hooks import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
)


@dataclass(frozen=True)
class TimelineEntry:
    kind: str
    label: str
    at: datetime
    detail: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditTrail(HookProvider):
    """Collects an ordered timeline of agent activity."""

    SUBSCRIBED = (
        BeforeInvocationEvent,
        AfterInvocationEvent,
        BeforeModelCallEvent,
        BeforeToolCallEvent,
        AfterToolCallEvent,
    )

    def __init__(self) -> None:
        self.entries: list[TimelineEntry] = []

    def register_hooks(self, registry: Any, **_kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self.on_before_invocation)
        registry.add_callback(BeforeModelCallEvent, self.on_before_model)
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool)
        registry.add_callback(AfterToolCallEvent, self.on_after_tool)
        registry.add_callback(AfterInvocationEvent, self.on_after_invocation)

    def _add(self, kind: str, label: str, detail: dict[str, Any] | None = None) -> None:
        self.entries.append(TimelineEntry(kind, label, _now(), detail or {}))

    def on_before_invocation(self, _event: Any) -> None:
        self._add("invocation", "agent started")

    def on_before_model(self, _event: Any) -> None:
        self._add("model_call", "reasoning")

    def on_before_tool(self, event: Any) -> None:
        # Shallow copy is deliberate: the SDK only mutates this dict in place during
        # streaming parse, which happens before BeforeToolCallEvent fires, so by the
        # time we see it a shallow copy is sufficient to snapshot the input.
        self._add("tool_call", event.tool_use["name"], dict(event.tool_use.get("input", {})))

    def on_after_tool(self, event: Any) -> None:
        self._add("tool_result", event.tool_use["name"])

    def on_after_invocation(self, _event: Any) -> None:
        self._add("invocation", "agent finished")

    def as_dicts(self) -> list[dict[str, Any]]:
        return [
            {"kind": e.kind, "label": e.label, "at": e.at.isoformat(), "detail": e.detail}
            for e in self.entries
        ]
