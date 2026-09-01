# tests/evals/test_policy_invariants.py
import pytest

from gleaner.domain.types import AutonomyLevel
from gleaner.policy.rules import NON_NEGOTIABLE_RULES, THRESHOLDS, evaluate_rules
from tests.evals.scenarios import SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_scenario_escalates_as_expected_at_balanced(scenario):
    hits = evaluate_rules(scenario.context, AutonomyLevel.BALANCED)
    assert bool(hits) == scenario.must_escalate
    assert scenario.expect_rules <= {h.rule for h in hits}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_non_negotiable_rules_survive_every_autonomy_level(scenario, level):
    """Safety and consent must never be dialled away. This is the core safety claim."""
    expected = scenario.expect_rules & NON_NEGOTIABLE_RULES
    if not expected:
        pytest.skip("scenario does not exercise a non-negotiable rule")
    assert expected <= {h.rule for h in evaluate_rules(scenario.context, level)}


@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_unsafe_food_never_auto_commits_at_any_level(level):
    """No autonomy level may allow a placement with unresolved liability."""
    for scenario in SCENARIOS:
        if scenario.context.liability_ok:
            continue
        assert evaluate_rules(scenario.context, level), (
            f"{scenario.name} was allowed to proceed unattended at {level}"
        )


@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_dietary_conflicts_never_auto_commit_at_any_level(level):
    for scenario in SCENARIOS:
        if scenario.context.dietary_ok:
            continue
        assert evaluate_rules(scenario.context, level)


def test_raising_autonomy_never_increases_escalations():
    """Monotonicity: a more permissive level must never escalate more than a stricter one."""
    for scenario in SCENARIOS:
        counts = [len(evaluate_rules(scenario.context, lvl)) for lvl in AutonomyLevel.order()]
        assert counts == sorted(counts, reverse=True), scenario.name


def test_every_rule_in_the_corpus_is_exercised_somewhere():
    covered = set()
    for scenario in SCENARIOS:
        covered |= {h.rule for h in evaluate_rules(scenario.context, AutonomyLevel.CONSERVATIVE)}
    assert covered == {
        "over_value_threshold", "tight_window", "safety_ambiguous", "capacity_overrun",
        "no_viable_match", "novel_donor", "liability_uncertain", "dietary_conflict",
    }


def test_conservative_is_stricter_than_trusted_on_every_threshold():
    strict, loose = THRESHOLDS[AutonomyLevel.CONSERVATIVE], THRESHOLDS[AutonomyLevel.TRUSTED]
    assert strict.max_autonomous_kg < loose.max_autonomous_kg
    assert strict.required_margin_minutes > loose.required_margin_minutes
    assert strict.min_safety_confidence > loose.min_safety_confidence
    assert strict.max_capacity_fraction < loose.max_capacity_fraction


def test_non_negotiable_rule_reported_first_when_mixed_with_ordinary_rules():
    """EscalationPolicy reports the escalation's reason as ``hits[0].rule`` (see
    src/gleaner/policy/escalation.py), which is only a correct attribution because
    evaluate_rules emits non-negotiable rules (liability_uncertain, dietary_conflict)
    before ordinary ones. This pins that ordering: for a context that violates both a
    non-negotiable rule and at least one ordinary rule, the first hit must be
    non-negotiable. If a future change to evaluate_rules reorders its checks, this
    test must fail — otherwise escalation reasons would be silently misattributed.
    """
    scenario = next(s for s in SCENARIOS if s.name == "everything wrong at once")
    hits = evaluate_rules(scenario.context, AutonomyLevel.BALANCED)
    assert len(hits) >= 2
    non_negotiable_rules = {h.rule for h in hits} & NON_NEGOTIABLE_RULES
    ordinary_rules = {h.rule for h in hits} - NON_NEGOTIABLE_RULES
    assert non_negotiable_rules and ordinary_rules, "scenario must exercise both kinds of rule"
    assert hits[0].rule in NON_NEGOTIABLE_RULES, (
        f"expected a non-negotiable rule first, got {hits[0].rule!r}"
    )
