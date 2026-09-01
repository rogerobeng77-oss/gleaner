# tests/domain/test_types.py
from gleaner.domain.types import TempClass, AutonomyLevel, DecisionOutcome, PlacementStatus


def test_temp_classes_are_string_valued():
    assert TempClass.CHILLED == "chilled"
    assert set(TempClass) == {TempClass.AMBIENT, TempClass.CHILLED, TempClass.FROZEN}


def test_autonomy_levels_ordered_least_to_most_permissive():
    assert AutonomyLevel.order() == [
        AutonomyLevel.CONSERVATIVE,
        AutonomyLevel.BALANCED,
        AutonomyLevel.TRUSTED,
    ]


def test_decision_outcomes():
    assert set(DecisionOutcome) == {
        DecisionOutcome.APPROVE,
        DecisionOutcome.REJECT,
        DecisionOutcome.MODIFY,
    }


def test_placement_status_terminal_states():
    assert PlacementStatus.DELIVERED.is_terminal()
    assert PlacementStatus.CANCELLED.is_terminal()
    assert not PlacementStatus.PROPOSED.is_terminal()
