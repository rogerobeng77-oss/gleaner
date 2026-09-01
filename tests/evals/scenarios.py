# tests/evals/scenarios.py
"""Named scenarios a coordinator would recognise, used to hold the policy to account."""

from dataclasses import dataclass

from gleaner.policy.rules import PlacementContext


@dataclass(frozen=True)
class Scenario:
    name: str
    context: PlacementContext
    must_escalate: bool
    expect_rules: frozenset[str]


def _ctx(**kw) -> PlacementContext:
    base = dict(
        weight_kg=10.0, margin_minutes=240.0, safety_confidence=0.99,
        capacity_fraction=0.2, liability_ok=True, dietary_ok=True,
        donor_is_novel=False, has_viable_match=True,
    )
    return PlacementContext(**{**base, **kw})


SCENARIOS: list[Scenario] = [
    Scenario(
        "a crate of bread to a pantry down the road",
        _ctx(weight_kg=8.0),
        must_escalate=False,
        expect_rules=frozenset(),
    ),
    Scenario(
        "half a lorry of chilled produce",
        _ctx(weight_kg=800.0),
        must_escalate=True,
        expect_rules=frozenset({"over_value_threshold"}),
    ),
    Scenario(
        "chilled food that has been out of the van too long",
        _ctx(safety_confidence=0.3),
        must_escalate=True,
        expect_rules=frozenset({"safety_ambiguous"}),
    ),
    Scenario(
        "pork sausages offered to a halal pantry",
        _ctx(dietary_ok=False),
        must_escalate=True,
        expect_rules=frozenset({"dietary_conflict"}),
    ),
    Scenario(
        "donation whose Emerson Act cover is unclear",
        _ctx(liability_ok=False),
        must_escalate=True,
        expect_rules=frozenset({"liability_uncertain"}),
    ),
    Scenario(
        "arrives twenty minutes before it spoils",
        _ctx(margin_minutes=20.0),
        must_escalate=True,
        expect_rules=frozenset({"tight_window"}),
    ),
    Scenario(
        "would fill the last of the cold store",
        _ctx(capacity_fraction=0.99),
        must_escalate=True,
        expect_rules=frozenset({"capacity_overrun"}),
    ),
    Scenario(
        "nowhere will take it in time",
        _ctx(has_viable_match=False),
        must_escalate=True,
        expect_rules=frozenset({"no_viable_match"}),
    ),
    Scenario(
        "first offer from a caterer nobody has met",
        _ctx(donor_is_novel=True),
        must_escalate=True,
        expect_rules=frozenset({"novel_donor"}),
    ),
    Scenario(
        "everything wrong at once",
        _ctx(weight_kg=900.0, margin_minutes=5.0, safety_confidence=0.2,
             capacity_fraction=0.99, liability_ok=False, dietary_ok=False,
             donor_is_novel=True, has_viable_match=False),
        must_escalate=True,
        expect_rules=frozenset({
            "liability_uncertain", "dietary_conflict", "no_viable_match",
            "over_value_threshold", "tight_window", "safety_ambiguous",
            "capacity_overrun", "novel_donor",
        }),
    ),
]
