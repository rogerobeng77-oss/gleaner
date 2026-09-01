from strands.hooks import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookRegistry,
)

from gleaner.hooks.audit import AuditTrail, TimelineEntry


def test_registers_without_error():
    AuditTrail().register_hooks(HookRegistry())


def test_records_tool_calls_in_order():
    trail = AuditTrail()
    trail.on_before_tool(type("E", (), {"tool_use": {"name": "check_capacity", "input": {}}})())
    trail.on_before_tool(type("E", (), {"tool_use": {"name": "commit_placement", "input": {}}})())
    assert [e.label for e in trail.entries] == ["check_capacity", "commit_placement"]


def test_entries_are_timestamped_and_typed():
    trail = AuditTrail()
    trail.on_before_tool(type("E", (), {"tool_use": {"name": "x", "input": {"a": 1}}})())
    entry = trail.entries[0]
    assert isinstance(entry, TimelineEntry)
    assert entry.kind == "tool_call"
    assert entry.at.tzinfo is not None
    assert entry.detail == {"a": 1}


def test_serialises_for_the_api():
    trail = AuditTrail()
    trail.on_before_tool(type("E", (), {"tool_use": {"name": "x", "input": {}}})())
    payload = trail.as_dicts()
    assert payload[0]["kind"] == "tool_call"
    assert isinstance(payload[0]["at"], str)


def test_subscribes_to_the_events_we_rely_on():
    assert set(AuditTrail.SUBSCRIBED) >= {
        BeforeInvocationEvent,
        BeforeToolCallEvent,
        AfterToolCallEvent,
    }


def test_records_invocation_start_and_finish_in_order():
    trail = AuditTrail()
    trail.on_before_invocation(type("E", (), {})())
    trail.on_after_invocation(type("E", (), {})())
    assert [(e.kind, e.label) for e in trail.entries] == [
        ("invocation", "agent started"),
        ("invocation", "agent finished"),
    ]
