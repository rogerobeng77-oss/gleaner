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
        "commit_placement",
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
        "commit_placement",
        _input(world, pantry_id=pantry.pantry_id, weight_kg=half_the_headroom)
    )
    expected = (pantry.cold_used_kg + half_the_headroom) / pantry.cold_capacity_kg
    assert abs(ctx.capacity_fraction - expected) < 1e-9
    assert ctx.has_viable_match


def test_pork_against_a_pork_restricted_pantry_is_a_dietary_conflict():
    world = _world()
    ctx = build_context_provider(world)(
        "commit_placement",
        _input(world, pantry_id=_pantry_with_restriction(world, "pork"), dietary_flags=["pork"])
    )
    assert ctx.dietary_ok is False
    assert "dietary_conflict" in {hit.rule for hit in evaluate_rules(ctx, AutonomyLevel.TRUSTED)}


def test_the_same_food_at_an_unrestricted_pantry_is_fine():
    world = _world()
    ctx = build_context_provider(world)("commit_placement", _input(world, dietary_flags=["pork"]))
    assert ctx.dietary_ok is True


def test_food_past_its_cold_chain_budget_is_not_liability_ok():
    world = _world()
    ctx = build_context_provider(world)(
        "commit_placement",
        _input(world, minutes_out_of_refrigeration=400.0)   # chilled budget is 240
    )
    assert ctx.liability_ok is False
    assert ctx.safety_confidence == 0.0
    assert "liability_uncertain" in {hit.rule for hit in evaluate_rules(ctx, AutonomyLevel.TRUSTED)}


def test_safety_confidence_decays_with_the_spent_cold_chain_budget():
    world = _world()
    provide = build_context_provider(world)
    fresh = provide("commit_placement", _input(world, minutes_out_of_refrigeration=0.0))
    half_spent = provide("commit_placement", _input(world, minutes_out_of_refrigeration=120.0))
    assert fresh.safety_confidence == 0.99          # the ceiling, never certainty
    assert half_spent.safety_confidence == 0.49     # half the 240-minute budget spent
    assert fresh.safety_confidence > half_spent.safety_confidence


def test_margin_is_measured_against_the_recomputed_deadline():
    world = _world()
    ctx = build_context_provider(world)(
        "commit_placement",
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
        "commit_placement",
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
    ctx = build_context_provider(world)(
        "commit_placement", _input(world, offer_id="off-never-seen")
    )
    assert ctx.donor_is_novel is True


def test_a_known_offer_resolves_to_the_real_donor_history():
    world = _world()
    offer = world.generate_offer()
    donor = next(d for d in world.donors() if d.donor_id == offer.donor_id)
    ctx = build_context_provider(world)("commit_placement", _input(world, offer_id=offer.offer_id))
    assert ctx.donor_is_novel == (donor.donation_count < 1)


def test_the_threshold_decides_what_counts_as_novel():
    world = _world()
    offer = world.generate_offer()
    donor = next(d for d in world.donors() if d.donor_id == offer.donor_id)
    strict = build_context_provider(world, donor_is_novel_threshold=donor.donation_count + 1)
    ctx = strict("commit_placement", _input(world, offer_id=offer.offer_id))
    assert ctx.donor_is_novel is True


def test_the_call_s_own_donor_id_is_used_when_the_offer_cannot_be_resolved():
    """The fallback has to be reachable, or "unknown means novel" is decoration.

    Before commit_placement carried a donor_id there was nothing to fall back TO, so
    an unresolvable offer meant donor_is_novel was a constant True.
    """
    world = _world()
    known = next(d for d in world.donors() if d.donation_count >= 1)
    ctx = build_context_provider(world)(
        "commit_placement",
        _input(world, offer_id="off-never-seen", donor_id=known.donor_id),
    )
    assert ctx.donor_is_novel is False
    assert "novel_donor" not in {h.rule for h in evaluate_rules(ctx, AutonomyLevel.CONSERVATIVE)}


def test_an_unknown_donor_id_is_still_novel():
    world = _world()
    ctx = build_context_provider(world)(
        "commit_placement", _input(world, offer_id="off-never-seen", donor_id="don-nobody")
    )
    assert ctx.donor_is_novel is True


def test_a_donor_with_no_history_is_novel_even_though_the_world_knows_them():
    world = _world()
    fresh = next((d for d in world.donors() if d.donation_count == 0), None)
    assert fresh is not None, "the default seed has no donor without history"
    ctx = build_context_provider(world)(
        "commit_placement", _input(world, offer_id="off-never-seen", donor_id=fresh.donor_id)
    )
    assert ctx.donor_is_novel is True


def test_the_offer_record_outranks_the_call_s_donor_id():
    """Naming a well-regarded donor over the top of a real offer must buy nothing."""
    world = _world()
    established = next(d for d in world.donors() if d.donation_count >= 1)
    _register(world, "off-shady", donor_id="don-nobody")
    ctx = build_context_provider(world)(
        "commit_placement",
        _input(world, offer_id="off-shady", donor_id=established.donor_id),
    )
    assert ctx.donor_is_novel is True


# --- fail closed ------------------------------------------------------------------


def test_an_unknown_pantry_escalates_rather_than_waving_through():
    world = _world()
    ctx = build_context_provider(world)(
        "commit_placement", _input(world, pantry_id="pan-does-not-exist")
    )
    assert ctx.has_viable_match is False
    assert ctx.liability_ok is False
    assert _escalates(ctx)


def test_a_missing_temp_class_escalates():
    world = _world()
    tool_input = _input(world)
    del tool_input["temp_class"]
    ctx = build_context_provider(world)("commit_placement", tool_input)
    assert _escalates(ctx)


def test_an_unparseable_eta_escalates():
    world = _world()
    ctx = build_context_provider(world)("commit_placement", _input(world, eta="tomorrow lunchtime"))
    assert _escalates(ctx)


def test_a_naive_eta_escalates_rather_than_guessing_a_timezone():
    world = _world()
    ctx = build_context_provider(world)(
        "commit_placement", _input(world, eta="2026-09-01T12:20:00")
    )
    assert _escalates(ctx)


def test_a_missing_weight_escalates():
    world = _world()
    tool_input = _input(world)
    del tool_input["weight_kg"]
    assert _escalates(build_context_provider(world)("commit_placement", tool_input))


def test_a_non_numeric_weight_escalates():
    world = _world()
    provide = build_context_provider(world)
    assert _escalates(provide("commit_placement", _input(world, weight_kg="loads")))


def test_an_empty_input_escalates():
    world = _world()
    assert _escalates(build_context_provider(world)("commit_placement", {}))


def test_the_provider_never_raises_on_rubbish():
    """A tool call the agent malformed must not crash the guard - it must close it."""
    world = _world()
    provide = build_context_provider(world)
    for rubbish in ({"pantry_id": None}, {"weight_kg": -5}, {"dietary_flags": "pork"}, {}):
        assert _escalates(provide("commit_placement", rubbish))


# --- the default wiring actually uses this ----------------------------------------


def test_build_default_policy_pairs_the_level_with_a_world_derived_provider():
    from gleaner.loops.place.runner import build_default_policy

    world = _world()
    policy = build_default_policy(world, AutonomyLevel.TRUSTED)
    assert policy.level is AutonomyLevel.TRUSTED
    ctx = policy.context_provider(
        "commit_placement", _input(world, pantry_id="pan-does-not-exist")
    )
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
        "commit_placement",
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
        "commit_placement",
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
        "commit_placement",
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
        "commit_placement",
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
        "commit_placement",
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


# --- the four guarded tools that place nothing -------------------------------------
#
# ``build_context_provider`` was written for ``commit_placement``'s argument shape.
# The other guarded tools have no ``weight_kg``, so every one of them fell into the
# ``weight_kg <= 0`` branch and escalated at every autonomy level - which broke the
# loop (the agent is told to commit and then dispatch, so every run paused on the
# dispatch) and, worse, told the coordinator five things about a driver dispatch that
# were not true of it, starting with "Bill Emerson Act protection is not clearly
# established for this donation".
#
# The rules genuinely applicable to each tool. Nothing outside these sets may ever be
# reported for that tool, at any autonomy level, on any input - true or false, a rule
# about pantry shelves is not a statement about a text message.
APPLICABLE_RULES = {
    "dispatch_driver": {"liability_uncertain", "novel_donor"},
    "notify_pantry": set(),
    "notify_donor": {"novel_donor"},
}


def _established_donor(world: SimulatedCity) -> str:
    match = next((d for d in world.donors() if d.donation_count >= 1), None)
    assert match is not None, "the default seed has no donor with any donation history"
    return match.donor_id


def _register(world: SimulatedCity, offer_id: str, **overrides) -> DonationOffer:
    """A registered offer, fresh and from a known donor unless overridden."""
    base = dict(
        offer_id=offer_id,
        donor_id=_established_donor(world),
        lots=[
            FoodLot(
                description="bagged carrots",
                weight_kg=15.0,
                temp_class=TempClass.CHILLED,
            )
        ],
        ready_at=NOW,
        pickup_address="Strip District, Pittsburgh, PA",
        lat=40.44,
        lon=-79.99,
        minutes_out_of_refrigeration=0.0,
    )
    offer = DonationOffer(**{**base, **overrides})
    world.register_offer(offer)
    return offer


def _call(world, tool_name, tool_input, level=AutonomyLevel.TRUSTED):
    """Drive the real policy for one guarded call; return the action and what fired."""
    from types import SimpleNamespace

    from gleaner.loops.place.runner import build_default_policy

    policy = build_default_policy(world, level)
    action = policy.before_tool_call(
        SimpleNamespace(tool_use={"name": tool_name, "input": tool_input})
    )
    return action, {hit.rule for hit in policy.last_hits}


def _dispatch_input(offer_id: str = "off-routine") -> dict:
    return {
        "driver_id": "drv-1",
        "offer_id": offer_id,
        "pickup_address": "Strip District, Pittsburgh, PA",
    }


# -- the loop can finish: routine calls are not stopped -----------------------------


def test_a_routine_dispatch_proceeds_at_trusted():
    """The functional half of the bug: every run paused on the dispatch after commit."""
    from strands.interventions import Proceed

    world = _world()
    offer = _register(world, "off-routine")
    action, fired = _call(world, "dispatch_driver", _dispatch_input(offer.offer_id))
    assert isinstance(action, Proceed), fired
    assert fired == set()


def test_a_routine_notify_pantry_proceeds_at_trusted():
    from strands.interventions import Proceed

    world = _world()
    pantry = _open_unrestricted_pantry(world)
    action, fired = _call(world, "notify_pantry", {"pantry_id": pantry, "message": "on its way"})
    assert isinstance(action, Proceed), fired
    assert fired == set()


def test_a_routine_notify_donor_proceeds_at_trusted():
    from strands.interventions import Proceed

    world = _world()
    action, fired = _call(
        world, "notify_donor", {"donor_id": _established_donor(world), "message": "thank you"}
    )
    assert isinstance(action, Proceed), fired
    assert fired == set()


def test_commit_then_dispatch_both_clear_the_guard_in_one_run():
    """The PLACE loop's actual sequence. It has to be able to run to the end."""
    from strands.interventions import Proceed

    world = _world()
    offer = _register(world, "off-routine")
    pantry = _open_unrestricted_pantry(world)
    commit, _ = _call(
        world,
        "commit_placement",
        _input(world, offer_id=offer.offer_id, pantry_id=pantry, weight_kg=15.0),
    )
    dispatch, _ = _call(world, "dispatch_driver", _dispatch_input(offer.offer_id))
    assert isinstance(commit, Proceed)
    assert isinstance(dispatch, Proceed)


# -- honesty: only applicable rules, ever -------------------------------------------


def test_a_dispatch_never_reports_a_dietary_or_capacity_rule():
    """A dispatch moves a person. It puts nothing on a shelf and feeds nobody."""
    world = _world()
    _register(world, "off-routine")
    _register(world, "off-pork", minutes_out_of_refrigeration=300.0, donor_id="don-nobody",
              lots=[FoodLot(description="pork sausages", weight_kg=12.0,
                            temp_class=TempClass.CHILLED, dietary_flags=["pork"])])
    inputs = [
        _dispatch_input("off-routine"),
        _dispatch_input("off-pork"),
        _dispatch_input("off-never-seen"),
        {},
        {"offer_id": None},
        {"offer_id": 42, "driver_id": None},
    ]
    for level in AutonomyLevel.order():
        for tool_input in inputs:
            _, fired = _call(world, "dispatch_driver", tool_input, level)
            assert "dietary_conflict" not in fired, (level, tool_input)
            assert "capacity_overrun" not in fired, (level, tool_input)


def test_no_guarded_tool_reports_a_rule_that_is_not_about_it():
    world = _world()
    _register(world, "off-routine")
    pantry = _open_unrestricted_pantry(world)
    calls = {
        "dispatch_driver": [
            _dispatch_input("off-routine"), _dispatch_input("off-never-seen"), {},
        ],
        "notify_pantry": [
            {"pantry_id": pantry, "message": "hi"}, {"pantry_id": "pan-nope"}, {},
        ],
        "notify_donor": [
            {"donor_id": _established_donor(world), "message": "hi"},
            {"donor_id": "don-nobody"}, {},
        ],
    }
    for level in AutonomyLevel.order():
        for tool_name, inputs in calls.items():
            for tool_input in inputs:
                _, fired = _call(world, tool_name, tool_input, level)
                assert fired <= APPLICABLE_RULES[tool_name], (level, tool_name, tool_input, fired)


def test_the_dispatch_escalation_prompt_says_nothing_about_pantries_or_food_fit():
    """A coordinator must read only sentences that are true of the dispatch."""
    world = _world()
    action, _ = _call(world, "dispatch_driver", _dispatch_input("off-never-seen"))
    prompt = action.prompt.lower()
    for placement_word in ("pantry", "dietary", "capacity", "slack", "store", "kg exceeds"):
        assert placement_word not in prompt, prompt
    assert prompt.endswith("approve this dispatch driver?")


# -- fail closed: neutral is never permissive about something real -------------------


def test_a_dispatch_for_an_unidentifiable_donation_escalates_at_every_level():
    """We cannot say whose food this is, so we can vouch for neither it nor the donor."""
    from strands.interventions import Confirm

    world = _world()
    for level in AutonomyLevel.order():
        action, fired = _call(world, "dispatch_driver", _dispatch_input("off-never-seen"), level)
        assert isinstance(action, Confirm), level
        assert "liability_uncertain" in fired, level


def test_a_dispatch_for_food_already_past_its_cold_chain_escalates_at_every_level():
    """Do not send a volunteer across the city for something already destined for the bin."""
    from strands.interventions import Confirm

    world = _world()
    _register(world, "off-spoiled", minutes_out_of_refrigeration=300.0)   # budget is 240
    for level in AutonomyLevel.order():
        action, fired = _call(world, "dispatch_driver", _dispatch_input("off-spoiled"), level)
        assert isinstance(action, Confirm), level
        assert fired == {"liability_uncertain"}, level


def test_an_unknown_donor_is_still_novel_on_a_notification():
    from strands.interventions import Confirm

    world = _world()
    action, fired = _call(
        world, "notify_donor", {"donor_id": "don-nobody"}, AutonomyLevel.BALANCED
    )
    assert isinstance(action, Confirm)
    assert fired == {"novel_donor"}


# -- a guarded tool nobody has taught the provider about ----------------------------


def test_the_provider_declines_to_describe_a_tool_it_does_not_know():
    provide = build_context_provider(_world())
    assert provide("send_outreach", {}) is None
    assert provide("some_future_tool", {"anything": 1}) is None
    assert provide("commit_placement", _input(_world())) is not None


def test_send_outreach_escalates_at_every_level_with_a_reason_true_of_it():
    """Guarded by name, no tool behind it yet. Undescribable is not the same as safe."""
    from strands.interventions import Confirm

    from gleaner.policy.escalation import UNASSESSABLE_RULE

    world = _world()
    for level in AutonomyLevel.order():
        action, fired = _call(world, "send_outreach", {"business": "Penn Ave Bakery"}, level)
        assert isinstance(action, Confirm), level
        assert action.reason == UNASSESSABLE_RULE
        assert fired == {UNASSESSABLE_RULE}
        assert "no way to judge the risk" in action.prompt
        assert "Emerson" not in action.prompt
