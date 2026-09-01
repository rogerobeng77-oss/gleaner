from gleaner.domain.impact import (
    CO2E_KG_PER_KG_FOOD,
    KG_PER_MEAL,
    ImpactEntry,
    co2e_avoided_kg,
    meals_from_kg,
    summarise,
)


def test_meals_uses_the_feeding_america_convention():
    assert KG_PER_MEAL == 0.544
    assert meals_from_kg(54.4) == 100


def test_meals_round_down_because_partial_meals_are_not_meals():
    assert meals_from_kg(1.0) == 1
    assert meals_from_kg(0.5) == 0


def test_zero_kg_is_zero_meals():
    assert meals_from_kg(0.0) == 0


def test_co2e_scales_linearly():
    assert co2e_avoided_kg(10.0) == 10.0 * CO2E_KG_PER_KG_FOOD


def test_summary_totals_across_entries():
    entries = [
        ImpactEntry("off-1", 100.0, meals_from_kg(100.0), co2e_avoided_kg(100.0), 100.0),
        ImpactEntry("off-2", 50.0, meals_from_kg(50.0), co2e_avoided_kg(50.0), 0.0),
    ]
    summary = summarise(entries)
    assert summary["placements"] == 2
    assert summary["kg_rescued"] == 150.0
    assert summary["meals"] == meals_from_kg(100.0) + meals_from_kg(50.0)
    assert summary["counterfactual_kg_wasted"] == 100.0


def test_summary_of_nothing_is_all_zeroes():
    summary = summarise([])
    assert summary == {
        "placements": 0, "kg_rescued": 0.0, "meals": 0,
        "co2e_kg": 0.0, "counterfactual_kg_wasted": 0.0,
    }
