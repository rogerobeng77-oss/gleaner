import pytest

from gleaner.domain.types import AutonomyLevel
from gleaner.policy.rules import (
    NON_NEGOTIABLE_RULES,
    THRESHOLDS,
    PlacementContext,
    evaluate_rules,
)


def _ctx(**kw):
    base = dict(
        weight_kg=10.0, margin_minutes=180.0, safety_confidence=0.99,
        capacity_fraction=0.2, liability_ok=True, dietary_ok=True,
        donor_is_novel=False, has_viable_match=True,
    )
    return PlacementContext(**{**base, **kw})


def _rules(ctx, level=AutonomyLevel.BALANCED):
    return {hit.rule for hit in evaluate_rules(ctx, level)}


def test_routine_placement_escalates_nothing():
    assert _rules(_ctx()) == set()


def test_thresholds_defined_for_every_level():
    assert set(THRESHOLDS) == set(AutonomyLevel)


def test_thresholds_get_more_permissive_as_autonomy_rises():
    levels = AutonomyLevel.order()
    weights = [THRESHOLDS[lvl].max_autonomous_kg for lvl in levels]
    margins = [THRESHOLDS[lvl].required_margin_minutes for lvl in levels]
    assert weights == sorted(weights)
    assert margins == sorted(margins, reverse=True)


def test_heavy_load_escalates_at_balanced_but_not_trusted():
    ctx = _ctx(weight_kg=300.0)
    assert "over_value_threshold" in _rules(ctx, AutonomyLevel.BALANCED)
    assert "over_value_threshold" not in _rules(ctx, AutonomyLevel.TRUSTED)


def test_tight_window_escalates():
    assert "tight_window" in _rules(_ctx(margin_minutes=5.0))


def test_low_safety_confidence_escalates():
    assert "safety_ambiguous" in _rules(_ctx(safety_confidence=0.4))


def test_capacity_overrun_escalates():
    assert "capacity_overrun" in _rules(_ctx(capacity_fraction=0.99))


def test_no_viable_match_escalates():
    assert "no_viable_match" in _rules(_ctx(has_viable_match=False))


def test_novel_donor_escalates_below_trusted():
    ctx = _ctx(donor_is_novel=True)
    assert "novel_donor" in _rules(ctx, AutonomyLevel.CONSERVATIVE)
    assert "novel_donor" in _rules(ctx, AutonomyLevel.BALANCED)
    assert "novel_donor" not in _rules(ctx, AutonomyLevel.TRUSTED)


@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_liability_uncertainty_escalates_at_every_level(level):
    assert "liability_uncertain" in _rules(_ctx(liability_ok=False), level)


@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_dietary_conflict_escalates_at_every_level(level):
    assert "dietary_conflict" in _rules(_ctx(dietary_ok=False), level)


def test_non_negotiable_rules_are_exactly_safety_and_consent():
    assert NON_NEGOTIABLE_RULES == frozenset({"liability_uncertain", "dietary_conflict"})


def test_every_hit_carries_a_human_readable_detail():
    hits = evaluate_rules(_ctx(weight_kg=999.0, margin_minutes=1.0), AutonomyLevel.BALANCED)
    assert hits
    assert all(hit.detail.strip() for hit in hits)


def test_multiple_rules_can_fire_at_once():
    assert len(_rules(_ctx(weight_kg=999.0, margin_minutes=1.0, liability_ok=False))) >= 3
