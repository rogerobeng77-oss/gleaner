"""The escalation policy must judge the world, not the agent's description of it.

Every test here builds the tool input an agent could plausibly emit and asserts that
the derived context contradicts it wherever the world disagrees. No model is called:
the provider is pure arithmetic over the adapter.
"""

from datetime import datetime, timedelta, timezone

from gleaner.adapters.simulated import SimulatedCity
from gleaner.domain.models import DonationOffer, FoodLot
from gleaner.domain.types import AutonomyLevel, TempClass
from gleaner.loops.place.context import build_context_provider
from gleaner.policy.rules import evaluate_rules

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _world() -> SimulatedCity:
    """The default seed, because that is what the demo and a judge will see."""
    return SimulatedCity(now=NOW)


def _pantry_with_restriction(world: SimulatedCity, restriction: str) -> str:
    """Find, rather than assume, which seeded pantry carries a restriction."""
    matches = [p for p in world.pantries() if restriction in p.dietary_restrictions]
    assert matches, f"the default seed has no pantry restricting {restriction!r}"
    return matches[0].pantry_id


def _open_unrestricted_pantry(world: SimulatedCity) -> str:
    matches = [p for p in world.pantries() if not p.dietary_restrictions]
    assert matches
    return matches[0].pantry_id


def _input(world: SimulatedCity, **overrides) -> dict:
    base = dict(
        offer_id="off-1",
        pantry_id=_open_unrestricted_pantry(world),
        driver_id="drv-1",
        weight_kg=10.0,
        eta=(NOW + timedelta(minutes=20)).isoformat(),
        temp_class="chilled",
        dietary_flags=[],
        minutes_out_of_refrigeration=0.0,
        ready_at_iso=NOW.isoformat(),
    )
    return {**base, **overrides}


def _escalates(ctx) -> bool:
    """Escalation at the most permissive level is the strongest form of the claim."""
    return bool(evaluate_rules(ctx, AutonomyLevel.TRUSTED))


# --- the world overrules the model ----------------------------------------------


def test_a_modest_claimed_weight_cannot_hide_a_capacity_overrun():
    """The model says 10 kg; the pantry's real free space says that fills it."""
    world = _world()
    # A pantry with no freezer at all: any frozen weight overruns it.
    no_freezer = [p for p in world.pantries() if p.frozen_capacity_kg == 0.0]
    assert no_freezer, "the default seed has no pantry without a freezer"
    ctx = build_context_provider(world)(
        _input(world, pantry_id=no_freezer[0].pantry_id, temp_class="frozen", weight_kg=10.0)
    )
    assert ctx.weight_kg == 10.0            # the claim is carried through untouched
    assert ctx.capacity_fraction == 1.0     # and contradicted by the lookup
    assert not ctx.has_viable_match
    assert _escalates(ctx)


def test_capacity_fraction_comes_from_the_pantry_not_the_claim():
    world = _world()
    pantry = next(p for p in world.pantries() if not p.dietary_restrictions)
    half_the_headroom = pantry.free_kg("chilled") / 2
    ctx = build_context_provider(world)(
        _input(world, pantry_id=pantry.pantry_id, weight_kg=half_the_headroom)
    )
    expected = (pantry.cold_used_kg + half_the_headroom) / pantry.cold_capacity_kg
    assert abs(ctx.capacity_fraction - expected) < 1e-9
    assert ctx.has_viable_match


def test_pork_against_a_pork_restricted_pantry_is_a_dietary_conflict():
    world = _world()
    ctx = build_context_provider(world)(
        _input(world, pantry_id=_pantry_with_restriction(world, "pork"), dietary_flags=["pork"])
    )
    assert ctx.dietary_ok is False
    assert "dietary_conflict" in {hit.rule for hit in evaluate_rules(ctx, AutonomyLevel.TRUSTED)}


def test_the_same_food_at_an_unrestricted_pantry_is_fine():
    world = _world()
    ctx = build_context_provider(world)(_input(world, dietary_flags=["pork"]))
    assert ctx.dietary_ok is True


def test_food_past_its_cold_chain_budget_is_not_liability_ok():
    world = _world()
    ctx = build_context_provider(world)(
        _input(world, minutes_out_of_refrigeration=400.0)   # chilled budget is 240
    )
    assert ctx.liability_ok is False
    assert ctx.safety_confidence == 0.0
    assert "liability_uncertain" in {hit.rule for hit in evaluate_rules(ctx, AutonomyLevel.TRUSTED)}


def test_safety_confidence_decays_with_the_spent_cold_chain_budget():
    world = _world()
    provide = build_context_provider(world)
    fresh = provide(_input(world, minutes_out_of_refrigeration=0.0))
    half_spent = provide(_input(world, minutes_out_of_refrigeration=120.0))
    assert fresh.safety_confidence == 0.99          # the ceiling, never certainty
    assert half_spent.safety_confidence == 0.49     # half the 240-minute budget spent
    assert fresh.safety_confidence > half_spent.safety_confidence


def test_margin_is_measured_against_the_recomputed_deadline():
    world = _world()
    ctx = build_context_provider(world)(
        _input(
            world,
            minutes_out_of_refrigeration=180.0,          # 60 minutes of budget left
            eta=(NOW + timedelta(minutes=20)).isoformat(),
        )
    )
    assert ctx.margin_minutes == 40.0                     # 60 remaining minus 20 travelled


def test_an_eta_past_the_deadline_yields_a_negative_margin():
    world = _world()
    ctx = build_context_provider(world)(
        _input(
            world,
            minutes_out_of_refrigeration=180.0,
            eta=(NOW + timedelta(minutes=90)).isoformat(),
        )
    )
    assert ctx.margin_minutes == -30.0
    assert _escalates(ctx)


# --- donor novelty ---------------------------------------------------------------


def test_a_donor_that_cannot_be_resolved_is_treated_as_novel():
    world = _world()
    ctx = build_context_provider(world)(_input(world, offer_id="off-never-seen"))
    assert ctx.donor_is_novel is True


def test_a_known_offer_resolves_to_the_real_donor_history():
    world = _world()
    offer = world.generate_offer()
    donor = next(d for d in world.donors() if d.donor_id == offer.donor_id)
    ctx = build_context_provider(world)(_input(world, offer_id=offer.offer_id))
    assert ctx.donor_is_novel == (donor.donation_count < 1)


def test_the_threshold_decides_what_counts_as_novel():
    world = _world()
    offer = world.generate_offer()
    donor = next(d for d in world.donors() if d.donor_id == offer.donor_id)
    strict = build_context_provider(world, donor_is_novel_threshold=donor.donation_count + 1)
    assert strict(_input(world, offer_id=offer.offer_id)).donor_is_novel is True


# --- fail closed ------------------------------------------------------------------


def test_an_unknown_pantry_escalates_rather_than_waving_through():
    world = _world()
    ctx = build_context_provider(world)(_input(world, pantry_id="pan-does-not-exist"))
    assert ctx.has_viable_match is False
    assert ctx.liability_ok is False
    assert _escalates(ctx)


def test_a_missing_temp_class_escalates():
    world = _world()
    tool_input = _input(world)
    del tool_input["temp_class"]
    ctx = build_context_provider(world)(tool_input)
    assert _escalates(ctx)


def test_an_unparseable_eta_escalates():
    world = _world()
    ctx = build_context_provider(world)(_input(world, eta="tomorrow lunchtime"))
    assert _escalates(ctx)


def test_a_naive_eta_escalates_rather_than_guessing_a_timezone():
    world = _world()
    ctx = build_context_provider(world)(_input(world, eta="2026-09-01T12:20:00"))
    assert _escalates(ctx)


def test_a_missing_weight_escalates():
    world = _world()
    tool_input = _input(world)
    del tool_input["weight_kg"]
    assert _escalates(build_context_provider(world)(tool_input))


def test_a_non_numeric_weight_escalates():
    world = _world()
    assert _escalates(build_context_provider(world)(_input(world, weight_kg="loads")))


def test_an_empty_input_escalates():
    world = _world()
    assert _escalates(build_context_provider(world)({}))


def test_the_provider_never_raises_on_rubbish():
    """A tool call the agent malformed must not crash the guard - it must close it."""
    world = _world()
    provide = build_context_provider(world)
    for rubbish in ({"pantry_id": None}, {"weight_kg": -5}, {"dietary_flags": "pork"}, {}):
        assert _escalates(provide(rubbish))


# --- the default wiring actually uses this ----------------------------------------


def test_build_default_policy_pairs_the_level_with_a_world_derived_provider():
    from gleaner.loops.place.runner import build_default_policy

    world = _world()
    policy = build_default_policy(world, AutonomyLevel.TRUSTED)
    assert policy.level is AutonomyLevel.TRUSTED
    ctx = policy.context_provider(_input(world, pantry_id="pan-does-not-exist"))
    assert ctx.has_viable_match is False


# --- the headline claim, end to end -----------------------------------------------
#
# "The agent cannot autonomously commit food that is unsafe, out of window, over
# capacity, or in dietary conflict." These drive the real EscalationPolicy, built the
# real way, with the real tool input shape, at the MOST permissive autonomy level -
# so passing here means passing at every level.


def _guard(world, level=AutonomyLevel.TRUSTED):
    from gleaner.loops.place.runner import build_default_policy

    return build_default_policy(world, level)


def _commit_event(tool_input: dict):
    from types import SimpleNamespace

    return SimpleNamespace(tool_use={"name": "commit_placement", "input": tool_input})


def _stopped(world, level=AutonomyLevel.TRUSTED, **overrides) -> bool:
    from strands.interventions import Confirm

    action = _guard(world, level).before_tool_call(_commit_event(_input(world, **overrides)))
    return isinstance(action, Confirm)


def test_unsafe_food_cannot_be_committed_alone_at_any_level():
    world = _world()
    assert _stopped(world, minutes_out_of_refrigeration=400.0)          # budget is 240


def test_out_of_window_food_cannot_be_committed_alone_at_any_level():
    world = _world()
    assert _stopped(
        world,
        minutes_out_of_refrigeration=180.0,
        eta=(NOW + timedelta(minutes=90)).isoformat(),                  # arrives 30 min late
    )


def test_over_capacity_placement_cannot_be_committed_alone_at_any_level():
    world = _world()
    no_freezer = next(p for p in world.pantries() if p.frozen_capacity_kg == 0.0)
    assert _stopped(world, pantry_id=no_freezer.pantry_id, temp_class="frozen")


def test_dietary_conflict_cannot_be_committed_alone_at_any_level():
    world = _world()
    assert _stopped(
        world,
        pantry_id=_pantry_with_restriction(world, "pork"),
        dietary_flags=["pork"],
    )


def _clean_offer(world):
    """A seeded offer with no dietary flags, a fresh cold chain and a known donor.

    Found by search rather than asserted blind - the seed decides, and the search
    fails loudly if the default city stops producing one.
    """
    donations = {d.donor_id: d.donation_count for d in world.donors()}
    for seed in range(1, 200):
        offer = world.generate_offer(rng_seed=seed)
        flagged = any(flag for lot in offer.lots for flag in lot.dietary_flags)
        if not flagged and offer.minutes_out_of_refrigeration == 0 and donations[offer.donor_id]:
            return offer
    raise AssertionError("the default city produced no clean offer in 200 seeds")


def test_a_clean_placement_is_still_allowed_through():
    """The guard has to be capable of saying yes, or the tests above prove nothing."""
    from strands.interventions import Proceed

    world = _world()
    offer = _clean_offer(world)
    pantry = next(p for p in world.pantries() if not p.dietary_restrictions)
    action = _guard(world).before_tool_call(
        _commit_event(
            _input(
                world,
                pantry_id=pantry.pantry_id,
                offer_id=offer.offer_id,
                weight_kg=5.0,
                temp_class="chilled",
                minutes_out_of_refrigeration=0.0,
                eta=(offer.ready_at + timedelta(minutes=20)).isoformat(),
            )
        )
    )
    assert isinstance(action, Proceed)


def test_understating_the_cold_chain_does_not_buy_permission():
    """The agent says the food is fresh; the offer record says 200 minutes out."""
    world = _world()
    stale = world.generate_offer()                     # the default seed's offer
    assert stale.minutes_out_of_refrigeration > 0, "this seed no longer proves the point"
    ctx = build_context_provider(world)(
        _input(world, offer_id=stale.offer_id, minutes_out_of_refrigeration=0.0)
    )
    assert ctx.safety_confidence < 0.99
    assert _escalates(ctx)


def test_omitting_a_dietary_flag_does_not_buy_permission():
    """Consent is non-negotiable, so the offer's flags are used even if unmentioned."""
    world = _world()
    porky = next(
        world.generate_offer(rng_seed=seed)
        for seed in range(1, 200)
        if any("pork" in lot.dietary_flags for lot in world.generate_offer(rng_seed=seed).lots)
    )
    ctx = build_context_provider(world)(
        _input(
            world,
            offer_id=porky.offer_id,
            pantry_id=_pantry_with_restriction(world, "pork"),
            dietary_flags=[],                          # the agent simply does not say
        )
    )
    assert ctx.dietary_ok is False
    assert "dietary_conflict" in {h.rule for h in evaluate_rules(ctx, AutonomyLevel.TRUSTED)}


# --- an offer that arrived from outside is defended exactly like a generated one ---
#
# The regression guard for the whole offer-record defence. Before ``register_offer``
# existed, an offer submitted through POST /api/offers was never in the world's
# registry, ``world.offer(...)`` returned None for it, and every fact the record was
# supposed to contribute - the dietary flags, the cold-chain figure, the ready time -
# quietly reverted to being whatever the agent said. At ``trusted`` that let pork into
# a pork-restricted pantry unattended.


def _pork_offer(offer_id: str = "off-submitted") -> DonationOffer:
    """The offer shape POST /api/offers parses off the wire, not one this city made."""
    return DonationOffer(
        offer_id=offer_id,
        donor_id="don-1",
        lots=[
            FoodLot(
                description="pork sausages",
                weight_kg=12.0,
                temp_class=TempClass.CHILLED,
                dietary_flags=["pork"],
            )
        ],
        ready_at=NOW,
        pickup_address="Somewhere, Pittsburgh, PA",
        lat=40.44,
        lon=-79.99,
    )


def test_a_registered_offer_still_catches_an_omitted_dietary_flag():
    """The one that matters. Register, then let the agent simply not mention the pork."""
    world = _world()
    world.register_offer(_pork_offer())
    ctx = build_context_provider(world)(
        _input(
            world,
            offer_id="off-submitted",
            pantry_id=_pantry_with_restriction(world, "pork"),
            dietary_flags=[],                       # the agent says nothing about pork
        )
    )
    assert ctx.dietary_ok is False
    assert "dietary_conflict" in {h.rule for h in evaluate_rules(ctx, AutonomyLevel.TRUSTED)}


def test_a_registered_offer_stops_the_commit_at_every_level_including_trusted():
    """The same call through the real policy, not just the context. Trusted included."""
    world = _world()
    world.register_offer(_pork_offer())
    for level in AutonomyLevel.order():
        assert _stopped(
            world,
            level,
            offer_id="off-submitted",
            pantry_id=_pantry_with_restriction(world, "pork"),
            dietary_flags=[],
        ), f"{level} let unmentioned pork into a pork-restricted pantry"


def test_without_registration_the_same_call_is_undefended():
    """Names the failure precisely, so a future regression reads as this test flipping.

    Not an endorsement of the unregistered path - a proof that registration is what
    does the work, and therefore that dropping the ``register_offer`` call in
    ``POST /api/offers`` would be a real loss and not a no-op.
    """
    world = _world()                                # deliberately NOT registered
    ctx = build_context_provider(world)(
        _input(
            world,
            offer_id="off-submitted",
            pantry_id=_pantry_with_restriction(world, "pork"),
            dietary_flags=[],
        )
    )
    assert ctx.dietary_ok is True                   # nothing knows about the pork


def test_a_registered_offer_overrides_an_understated_cold_chain():
    """Registration carries the whole record across, not only the dietary flags."""
    world = _world()
    stale = _pork_offer("off-stale")
    world.register_offer(stale.model_copy(update={"minutes_out_of_refrigeration": 300.0}))
    ctx = build_context_provider(world)(
        _input(world, offer_id="off-stale", minutes_out_of_refrigeration=0.0)
    )
    assert ctx.liability_ok is False                # 300 minutes against a 240 budget
    assert ctx.safety_confidence == 0.0


def test_every_one_of_the_four_is_stopped_at_the_strictest_level_too():
    world = _world()
    no_freezer = next(p for p in world.pantries() if p.frozen_capacity_kg == 0.0)
    cases = [
        dict(minutes_out_of_refrigeration=400.0),
        dict(minutes_out_of_refrigeration=180.0, eta=(NOW + timedelta(minutes=90)).isoformat()),
        dict(pantry_id=no_freezer.pantry_id, temp_class="frozen"),
        dict(pantry_id=_pantry_with_restriction(world, "pork"), dietary_flags=["pork"]),
    ]
    for level in AutonomyLevel.order():
        for case in cases:
            assert _stopped(world, level, **case), f"{level} let {case} through"
