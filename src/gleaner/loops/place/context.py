# src/gleaner/loops/place/context.py
"""Deriving the placement context the escalation policy judges.

The policy asks one question - may the agent do this alone? - and it can only
answer honestly if the facts it is given are facts. The obvious mistake, and the
one this module exists to prevent, is to let the *model* supply them: an agent that
reports a comfortable capacity fraction gets a comfortable answer, which makes the
guard theatre.

So almost nothing here is taken on trust. The pantry is looked up in the world and
its real free capacity and real dietary restrictions are used. The deadline is
recomputed from the cold-chain arithmetic in ``perishability``. The donor's history
is read from the world, not from the tool call. What is unavoidably the model's
assertion - the mass, the temperature class, the promised ETA - is exactly the set
of claims the tool would act on anyway, and every one of them is used to *tighten*
the judgement, never to relax it.

**Fail closed.** Any input this module cannot make sense of - an unknown pantry, a
naive datetime, a missing temperature class, a non-positive weight - produces a
context that escalates. Silence is never read as consent. The rules that fire in
that case (``liability_uncertain`` and ``no_viable_match``) are unconditional, so an
unparseable call escalates at every autonomy level including ``trusted``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from gleaner.adapters.base import WorldAdapter
from gleaner.domain import perishability
from gleaner.domain.capacity import can_accept, utilisation_after
from gleaner.domain.models import DonationOffer, FoodLot, Pantry
from gleaner.domain.types import TempClass
from gleaner.policy.rules import PlacementContext

#: Ceiling on ``safety_confidence``. Nothing here inspects a temperature log or a
#: photograph of the load, so this module never claims certainty - only that the
#: cold-chain budget is, arithmetically, still intact.
MAX_SAFETY_CONFIDENCE: float = 0.99


def _escalating(weight_kg: float = 0.0) -> PlacementContext:
    """The context returned when the input cannot be trusted or understood.

    ``liability_ok=False`` and ``has_viable_match=False`` both fire rules that carry
    no threshold, so this escalates at every autonomy level. ``dietary_ok`` is left
    ``True`` deliberately: we do not know of a dietary conflict here, and claiming
    one would put a false sentence in front of the coordinator. The escalation is
    already guaranteed without it.
    """
    return PlacementContext(
        weight_kg=weight_kg,
        margin_minutes=0.0,
        safety_confidence=0.0,
        capacity_fraction=1.0,
        liability_ok=False,
        dietary_ok=True,
        donor_is_novel=True,
        has_viable_match=False,
    )


def _parse_aware(value: Any) -> datetime | None:
    """An ISO-8601 string with an offset, or ``None`` if it is anything else."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _safety_confidence(temp_class: TempClass, minutes_out: float) -> float:
    """How much of the cold-chain budget is left, as a confidence in [0, 0.99].

    ``MAX_SAFETY_CONFIDENCE * (1 - spent / budget)``: full marks (less the ceiling)
    for food straight out of refrigeration, decaying linearly to zero as the budget
    for time above safe holding temperature is used up. Ambient food has no cold
    chain to break, so its budget is infinite and it scores the ceiling.

    This is a stand-in with a stated formula, not a model's guess. The spec assigns
    real safety reasoning - temperature history, packaging, provenance - to a Safety
    node that does not exist yet; when it does, it replaces this function.
    """
    budget = perishability.DANGER_ZONE_BUDGET_MINUTES[temp_class]
    if budget == float("inf"):
        return MAX_SAFETY_CONFIDENCE
    spent_fraction = min(1.0, max(0.0, minutes_out) / budget)
    return round(MAX_SAFETY_CONFIDENCE * (1.0 - spent_fraction), 2)


def build_context_provider(
    world: WorldAdapter, *, donor_is_novel_threshold: int = 1
) -> Callable[[dict[str, Any]], PlacementContext]:
    """A context provider bound to a world, for ``EscalationPolicy``.

    The returned callable takes the guarded tool call's ``input`` dict - the
    arguments the model actually proposed - and answers with a ``PlacementContext``
    derived from the world wherever the world can answer.

    Args:
        world: the adapter every lookup goes through.
        donor_is_novel_threshold: a donor with fewer completed donations than this is
            treated as novel. The default of 1 means "has never donated before".

    Returns:
        ``(tool_input) -> PlacementContext``. Never raises: an input it cannot make
        sense of comes back as an escalating context.

    Notes on each field:

    * ``weight_kg`` and ``temp_class`` are the model's assertions, and irreducibly so:
      a placement may legitimately be part of an offer, of one handling class, so
      neither can be read off the offer record. They are validated for shape and then
      used to *tighten* the capacity and cold-chain arithmetic, never to relax it.
    * ``margin_minutes`` recomputes the real deadline with ``perishability.safe_until``
      - from the offer's ready time and the longer of the two cold-chain figures - and
      compares it against the ETA the model promised.
    * ``capacity_fraction`` is ``capacity.utilisation_after`` against the pantry's
      real current usage, not against anything the model said about capacity.
    * ``dietary_ok`` intersects the food's flags with the looked-up pantry's real
      restrictions. The flags are the union of what the agent declared and what the
      offer record says, so omitting a flag cannot buy permission. On an offer of
      several lots this can escalate a placement of the unflagged lot - deliberately:
      over-escalating a consent question is the right way to be wrong.
    * ``liability_ok`` is ``not perishability.is_already_unsafe(...)``. This is a
      **conservative proxy** for Bill Emerson Good Samaritan Act eligibility and
      **not a legal determination**: it says only that the food has not visibly blown
      its cold-chain budget. The spec assigns actual Emerson Act reasoning to a
      Safety node that has not been built. Until it exists, treat a ``True`` here as
      "no arithmetic reason to doubt it", never as "protection established".
    * ``safety_confidence`` follows ``_safety_confidence`` above.
    * ``donor_is_novel`` is read from the world when the donation offer can be
      resolved to a donor. When it cannot, it is ``True`` - unknown escalates.
    * ``has_viable_match`` is ``True`` only when the pantry exists and can physically
      take the whole lot right now.
    """
    def _offer(tool_input: dict[str, Any]) -> DonationOffer | None:
        """The donation offer behind this call, if the world can resolve it."""
        return world.offer(tool_input.get("offer_id"))

    def _pantry(pantry_id: Any) -> Pantry | None:
        if not isinstance(pantry_id, str):
            return None
        return next((p for p in world.pantries() if p.pantry_id == pantry_id), None)

    def _donor_is_novel(tool_input: dict[str, Any], offer: DonationOffer | None) -> bool:
        """Novel unless the world can positively say otherwise."""
        donor_id = offer.donor_id if offer is not None else tool_input.get("donor_id")
        if not isinstance(donor_id, str):
            return True
        donor = next((d for d in world.donors() if d.donor_id == donor_id), None)
        if donor is None:
            return True
        return donor.donation_count < donor_is_novel_threshold

    def provide(tool_input: dict[str, Any]) -> PlacementContext:
        if not isinstance(tool_input, dict):
            return _escalating()

        raw_weight = tool_input.get("weight_kg")
        weight_kg = float(raw_weight) if isinstance(raw_weight, (int, float)) else 0.0
        if weight_kg <= 0:
            return _escalating()

        try:
            temp_class = TempClass(tool_input.get("temp_class"))
        except ValueError:
            return _escalating(weight_kg)

        pantry = _pantry(tool_input.get("pantry_id"))
        if pantry is None:
            return _escalating(weight_kg)

        arrival = _parse_aware(tool_input.get("eta"))
        if arrival is None:
            return _escalating(weight_kg)

        ready_at = _parse_aware(tool_input.get("ready_at_iso")) or world.now()

        raw_minutes = tool_input.get("minutes_out_of_refrigeration", 0.0)
        minutes_out = float(raw_minutes) if isinstance(raw_minutes, (int, float)) else 0.0
        if minutes_out < 0:
            return _escalating(weight_kg)

        raw_flags = tool_input.get("dietary_flags") or []
        flags = [f for f in raw_flags if isinstance(f, str)] if isinstance(raw_flags, list) else []

        # Where the world knows the offer, its record beats the agent's description of
        # it. Both are combined in the direction that escalates: the union of dietary
        # flags, the longer time out of refrigeration, the offer's own ready time. An
        # agent that forgets to mention the pork therefore does not get waved through.
        offer = _offer(tool_input)
        if offer is not None:
            for offer_lot in offer.lots:
                flags.extend(f for f in offer_lot.dietary_flags if f not in flags)
            minutes_out = max(minutes_out, float(offer.minutes_out_of_refrigeration))
            ready_at = offer.ready_at

        lot = FoodLot(
            description="proposed placement",
            weight_kg=weight_kg,
            temp_class=temp_class,
            dietary_flags=flags,
        )
        deadline = perishability.safe_until(temp_class, ready_at, minutes_out)

        return PlacementContext(
            weight_kg=weight_kg,
            margin_minutes=perishability.margin_minutes(deadline, arrival),
            safety_confidence=_safety_confidence(temp_class, minutes_out),
            capacity_fraction=utilisation_after(pantry, lot),
            liability_ok=not perishability.is_already_unsafe(temp_class, minutes_out),
            dietary_ok=not (set(flags) & set(pantry.dietary_restrictions)),
            donor_is_novel=_donor_is_novel(tool_input, offer),
            has_viable_match=can_accept(pantry, lot),
        )

    return provide
