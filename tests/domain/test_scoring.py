# tests/domain/test_scoring.py
from datetime import datetime, timedelta, timezone

from gleaner.domain.models import FoodLot, Pantry
from gleaner.domain.scoring import ScoredPantry, rank_pantries, score_pantry
from gleaner.domain.types import TempClass

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
DEADLINE = NOW + timedelta(hours=3)


def _pantry(pid="pan-1", **kw):
    base = dict(
        pantry_id=pid, name=pid, lat=40.46, lon=-79.92,
        cold_capacity_kg=500.0, frozen_capacity_kg=100.0, dry_capacity_kg=500.0,
        opens_at_hour=9, closes_at_hour=18, recent_fresh_kg_30d=100.0,
    )
    return Pantry(**{**base, **kw})


def _lot(weight=50.0, temp=TempClass.CHILLED, **kw):
    return FoodLot(description="romaine", weight_kg=weight, temp_class=temp, **kw)


def _score(pantry, lot=None, *, eta=None, distance_km=5.0, median=100.0):
    return score_pantry(
        pantry, lot or _lot(),
        eta=eta or NOW + timedelta(minutes=30),
        deadline=DEADLINE, distance_km=distance_km, median_fresh_kg_30d=median,
    )


def test_viable_pantry_scores_positively_and_explains_itself():
    result = _score(_pantry())
    assert result.is_viable
    assert result.score > 0
    assert result.reasons


def test_closed_on_arrival_is_a_blocker():
    result = _score(_pantry(opens_at_hour=9, closes_at_hour=12), eta=NOW + timedelta(hours=5))
    assert not result.is_viable
    assert any("closed" in b for b in result.blockers)


def test_insufficient_capacity_is_a_blocker():
    result = _score(_pantry(cold_capacity_kg=10.0), _lot(weight=50.0))
    assert not result.is_viable
    assert any("capacity" in b for b in result.blockers)


def test_arrival_after_deadline_is_a_blocker():
    result = _score(_pantry(), eta=DEADLINE + timedelta(minutes=1))
    assert not result.is_viable
    assert any("deadline" in b for b in result.blockers)


def test_dietary_conflict_is_a_blocker():
    pantry = _pantry(dietary_restrictions=["pork"])
    result = _score(pantry, _lot(dietary_flags=["pork"]))
    assert not result.is_viable
    assert any("dietary" in b for b in result.blockers)


def test_underserved_pantry_outscores_well_served_peer():
    underserved = _score(_pantry("under", recent_fresh_kg_30d=10.0), median=100.0)
    well_served = _score(_pantry("over", recent_fresh_kg_30d=400.0), median=100.0)
    assert underserved.score > well_served.score
    assert any("equity" in r for r in underserved.reasons)


def test_closer_pantry_outscores_further_one():
    near = _score(_pantry("near"), distance_km=2.0)
    far = _score(_pantry("far"), distance_km=40.0)
    assert near.score > far.score


def test_ranking_returns_viable_only_best_first():
    good = _pantry("good", recent_fresh_kg_30d=10.0)
    blocked = _pantry("blocked", cold_capacity_kg=1.0)
    ranked = rank_pantries(
        [blocked, good], _lot(),
        etas={"good": NOW + timedelta(minutes=30), "blocked": NOW + timedelta(minutes=30)},
        deadline=DEADLINE,
        distances_km={"good": 3.0, "blocked": 1.0}, median_fresh_kg_30d=100.0,
    )
    assert [r.pantry_id for r in ranked] == ["good"]


def test_scored_pantry_is_not_viable_when_blocked():
    assert not ScoredPantry("p", 10.0, [], ["closed on arrival"], 0.0, 0.0).is_viable


def test_score_exposes_the_margin_and_capacity_as_numbers_not_prose():
    """The escalation policy has to decide on these, so they cannot only be English."""
    result = _score(_pantry(), _lot(weight=250.0), eta=NOW + timedelta(minutes=30))
    assert result.margin_minutes == 150.0          # 3h deadline minus 30min arrival
    assert result.capacity_fraction == 0.5         # 250 kg into 500 kg of chilled


def test_blocked_pantry_still_reports_how_badly_it_missed():
    result = _score(_pantry(), eta=DEADLINE + timedelta(minutes=45))
    assert not result.is_viable
    assert result.margin_minutes == -45.0
    assert result.capacity_fraction == 0.1         # 50 kg into 500 kg


def test_ranking_excludes_pantry_with_no_measured_distance():
    reachable = _pantry("reachable", recent_fresh_kg_30d=10.0)
    unmeasured = _pantry("unmeasured", recent_fresh_kg_30d=10.0)
    ranked = rank_pantries(
        [reachable, unmeasured], _lot(),
        etas={
            "reachable": NOW + timedelta(minutes=30),
            "unmeasured": NOW + timedelta(minutes=30),
        },
        deadline=DEADLINE,
        distances_km={"reachable": 3.0},
        median_fresh_kg_30d=100.0,
    )
    assert [r.pantry_id for r in ranked] == ["reachable"]
