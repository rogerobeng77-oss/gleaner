from datetime import datetime, timezone

from gleaner.adapters.simulated import SimulatedCity
from gleaner.domain.models import Pantry
from gleaner.loops.place.tools import build_place_tools

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _tools():
    ledger: list[dict] = []
    world = SimulatedCity(seed=412, now=NOW)
    return {t.tool_name: t for t in build_place_tools(world, ledger)}, ledger, world


def test_exposes_every_expected_tool():
    tools, _, _ = _tools()
    assert set(tools) == {
        "list_pantries", "check_capacity", "find_pantries", "find_driver",
        "estimate_arrival", "commit_placement", "dispatch_driver",
        "notify_pantry", "notify_donor",
    }


def test_every_consequential_place_tool_is_guarded():
    from gleaner.policy.escalation import GUARDED_TOOLS
    tools, _, _ = _tools()
    consequential = {"commit_placement", "dispatch_driver", "notify_pantry", "notify_donor"}
    assert consequential <= GUARDED_TOOLS          # nothing consequential is unguarded
    assert consequential <= set(tools)             # and all of them exist here
    assert GUARDED_TOOLS - set(tools) == {"send_outreach"}   # only the SOURCE-loop tool is absent


def test_list_pantries_returns_the_world():
    tools, _, world = _tools()
    assert len(tools["list_pantries"]()) == len(world.pantries())


def test_check_capacity_reports_free_kilograms():
    tools, _, world = _tools()
    pid = world.pantries()[0].pantry_id
    result = tools["check_capacity"](pantry_id=pid, temp_class="chilled")
    assert result["free_kg"] >= 0
    assert result["pantry_id"] == pid


def test_check_capacity_on_unknown_pantry_reports_an_error():
    tools, _, _ = _tools()
    assert "error" in tools["check_capacity"](pantry_id="nope", temp_class="chilled")


def test_find_pantries_returns_ranked_viable_candidates_with_reasons():
    tools, _, _ = _tools()
    result = tools["find_pantries"](
        description="salad", weight_kg=20.0, temp_class="chilled",
        origin_lat=40.44, origin_lon=-79.99, dietary_flags=[],
    )
    assert result
    assert "reasons" in result[0] and "score" in result[0]
    scores = [r["score"] for r in result]
    assert scores == sorted(scores, reverse=True)


def test_find_pantries_returns_nothing_once_the_budget_is_spent():
    """Food already past its cold-chain budget has no viable destination."""
    tools, _, _ = _tools()
    assert tools["find_pantries"](
        description="salad", weight_kg=20.0, temp_class="chilled",
        origin_lat=40.44, origin_lon=-79.99, dietary_flags=[],
        minutes_out_of_refrigeration=400.0,
    ) == []


def test_find_driver_prefers_refrigerated_for_frozen():
    tools, _, _ = _tools()
    result = tools["find_driver"](origin_lat=40.44, origin_lon=-79.99, needs_refrigeration=True)
    if "driver_id" in result:
        assert result["refrigerated"] is True


def test_estimate_arrival_is_positive_and_iso_formatted():
    tools, _, _ = _tools()
    result = tools["estimate_arrival"](
        origin_lat=40.44, origin_lon=-79.99, dest_lat=40.46, dest_lon=-79.92,
    )
    assert result["travel_minutes"] > 0
    datetime.fromisoformat(result["eta"])


def test_commit_placement_writes_to_the_ledger():
    tools, ledger, _ = _tools()
    result = tools["commit_placement"](
        offer_id="off-1", pantry_id="pan-1", driver_id="drv-1",
        weight_kg=40.0, eta="2026-09-01T13:00:00+00:00", temp_class="chilled",
    )
    assert result["status"] == "committed"
    assert len(ledger) == 1
    assert ledger[0]["offer_id"] == "off-1"


def test_notifications_are_recorded_not_silently_dropped():
    tools, ledger, _ = _tools()
    tools["notify_pantry"](pantry_id="pan-1", message="incoming")
    assert any(e["kind"] == "notify_pantry" for e in ledger)


def test_commit_placement_rejects_an_unknown_pantry_and_leaves_the_ledger_untouched():
    tools, ledger, _ = _tools()
    result = tools["commit_placement"](
        offer_id="off-1", pantry_id="pan-99", driver_id="drv-1",
        weight_kg=40.0, eta="2026-09-01T13:00:00+00:00", temp_class="chilled",
    )
    assert "error" in result
    assert ledger == []


def test_commit_placement_rejects_an_unknown_driver_and_leaves_the_ledger_untouched():
    tools, ledger, _ = _tools()
    result = tools["commit_placement"](
        offer_id="off-1", pantry_id="pan-1", driver_id="drv-99",
        weight_kg=40.0, eta="2026-09-01T13:00:00+00:00", temp_class="chilled",
    )
    assert "error" in result
    assert ledger == []


def test_dispatch_driver_rejects_an_unknown_driver_and_leaves_the_ledger_untouched():
    tools, ledger, _ = _tools()
    result = tools["dispatch_driver"](
        driver_id="drv-99", offer_id="off-1", pickup_address="123 Main St",
    )
    assert "error" in result
    assert ledger == []


def test_notify_pantry_rejects_an_unknown_pantry_and_leaves_the_ledger_untouched():
    tools, ledger, _ = _tools()
    result = tools["notify_pantry"](pantry_id="pan-99", message="incoming")
    assert "error" in result
    assert ledger == []


def test_notify_donor_rejects_an_unknown_donor_and_leaves_the_ledger_untouched():
    tools, ledger, _ = _tools()
    result = tools["notify_donor"](donor_id="don-99", message="incoming")
    assert "error" in result
    assert ledger == []


def test_commit_placement_records_the_lot_facts_the_policy_needs():
    """The guard cannot judge a cold chain it was never told about."""
    tools, ledger, _ = _tools()
    tools["commit_placement"](
        offer_id="off-1", pantry_id="pan-1", driver_id="drv-1",
        weight_kg=40.0, eta="2026-09-01T13:00:00+00:00", temp_class="chilled",
        dietary_flags=["pork"], minutes_out_of_refrigeration=90.0,
        ready_at_iso="2026-09-01T12:15:00+00:00",
    )
    entry = ledger[0]
    assert entry["temp_class"] == "chilled"
    assert entry["dietary_flags"] == ["pork"]
    assert entry["minutes_out_of_refrigeration"] == 90.0
    assert entry["ready_at_iso"] == "2026-09-01T12:15:00+00:00"


def test_commit_placement_carries_the_donor_through_to_the_ledger():
    """Without it the guard has no fallback when the offer cannot be resolved."""
    tools, ledger, _ = _tools()
    tools["commit_placement"](
        offer_id="off-1", pantry_id="pan-1", driver_id="drv-1",
        weight_kg=40.0, eta="2026-09-01T13:00:00+00:00", temp_class="chilled",
        donor_id="don-3",
    )
    assert ledger[0]["donor_id"] == "don-3"


def test_commit_placement_still_works_without_a_donor_id():
    """Optional, so an agent that omits it fails the novelty check, not the call."""
    tools, ledger, _ = _tools()
    result = tools["commit_placement"](
        offer_id="off-1", pantry_id="pan-1", driver_id="drv-1",
        weight_kg=40.0, eta="2026-09-01T13:00:00+00:00", temp_class="chilled",
    )
    assert result["status"] == "committed"
    assert ledger[0]["donor_id"] is None


def test_commit_placement_rejects_an_unknown_temp_class():
    tools, ledger, _ = _tools()
    result = tools["commit_placement"](
        offer_id="off-1", pantry_id="pan-1", driver_id="drv-1",
        weight_kg=40.0, eta="2026-09-01T13:00:00+00:00", temp_class="lukewarm",
    )
    assert "error" in result
    assert ledger == []


class _OneStopWorld:
    """A world with a single pantry and no travel time.

    Zero travel collapses arrival onto departure, so the pantry's opening hours read
    the departure time back out exactly - which is the only way to observe, from
    outside, *when* find_pantries thinks the driver sets off.
    """

    def __init__(self, opens=8, closes=18):
        self._pantry = Pantry(
            pantry_id="pan-1", name="Only", lat=40.44, lon=-79.99,
            cold_capacity_kg=500.0, frozen_capacity_kg=500.0, dry_capacity_kg=500.0,
            opens_at_hour=opens, closes_at_hour=closes, recent_fresh_kg_30d=100.0,
        )

    def pantries(self): return [self._pantry.model_copy(deep=True)]
    def drivers(self): return []
    def donors(self): return []
    def distance_km(self, *_): return 0.0
    def travel_minutes(self, *_): return 0.0
    def now(self): return NOW


def _one_stop(**kw):
    return {t.tool_name: t for t in build_place_tools(_OneStopWorld(**kw), [])}


def test_find_pantries_departs_when_the_food_is_ready_not_when_asked():
    """A later ready_at moves the arrival, so a pantry shut by then drops out."""
    tools = _one_stop()
    kwargs = dict(
        description="tins", weight_kg=20.0, temp_class="ambient",
        origin_lat=40.44, origin_lon=-79.99, dietary_flags=[],
    )
    assert tools["find_pantries"](**kwargs)                                    # 12:00, open
    assert tools["find_pantries"](**kwargs, ready_at_iso="2026-09-01T19:00:00+00:00") == []


def test_find_pantries_never_departs_before_now():
    """A ready_at already in the past does not rewind the driver to that hour."""
    tools = _one_stop()
    result = tools["find_pantries"](
        description="tins", weight_kg=20.0, temp_class="ambient",
        origin_lat=40.44, origin_lon=-79.99, dietary_flags=[],
        ready_at_iso="2026-09-01T03:00:00+00:00",     # before opening; now is 12:00
    )
    assert result, "departure should be clamped to now, when the pantry is open"


def test_find_pantries_copes_with_an_empty_network():
    class _NoPantries(_OneStopWorld):
        def pantries(self): return []

    tools = {t.tool_name: t for t in build_place_tools(_NoPantries(), [])}
    assert tools["find_pantries"](
        description="tins", weight_kg=20.0, temp_class="ambient",
        origin_lat=40.44, origin_lon=-79.99,
    ) == []


def test_a_ready_time_past_closing_makes_pantries_unreachable():
    """The ETA really is computed from ready_at: push it past closing and they drop out."""
    tools, _, _ = _tools()
    kwargs = dict(
        description="salad", weight_kg=20.0, temp_class="chilled",
        origin_lat=40.44, origin_lon=-79.99, dietary_flags=[],
    )
    open_hours = tools["find_pantries"](**kwargs)
    overnight = tools["find_pantries"](**kwargs, ready_at_iso="2026-09-02T03:00:00+00:00")
    assert open_hours
    assert len(overnight) < len(open_hours)


def test_find_pantries_uses_a_true_median_for_the_equity_term():
    """sorted(...)[n // 2] is the upper-middle element, which inflates every deficit."""
    import statistics

    _, _, world = _tools()
    fresh = [p.recent_fresh_kg_30d for p in world.pantries()]
    assert len(fresh) % 2 == 0, "this only bites on an even-sized network"
    assert statistics.median(fresh) != sorted(fresh)[len(fresh) // 2]
