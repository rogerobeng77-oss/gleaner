# Gleaner

**An autonomous food-rescue coordinator.** Surplus food and hungry people exist in the
same city at the same hour. What fails between them is logistics, not generosity.

Gleaner takes a donation offer, reasons about whether the food is still safe under a
cold-chain clock, finds a pantry that can genuinely receive it, routes a driver who can
beat the window, and either commits the placement itself or stops and asks a human — with
the autonomy boundary written as executable policy rather than prompt text.

Built with the [Strands Agents SDK](https://strandsagents.com) for the AWS **Agents for
Humans** hackathon, Good Neighbor Agents track.

---

## Why this problem

| | |
|---|---|
| Only **2.5%** of surplus food was donated in 2024; growers donated **1.6%** of theirs | [ReFED, *Strengthen Food Rescue*](https://refed.org/action-areas/strengthen-food-rescue/) |
| **47.9 million** Americans were food insecure in 2024 | USDA, cited by ReFED |
| "**Distribution and logistics challenges** … are the reason most food donations are of processed, shelf-stable items" | ReFED |
| Food banks are "**forced to turn away donations** of perfectly good food" for want of refrigeration | [Move For Hunger](https://moveforhunger.org/blog/cold-chain-keeping-good-food-going-waste) |

ReFED names the required intervention directly: *"**coordination and matching
technologies** … the capture and sharing of **real- or near-time data**."*

**Why the existing tools don't close it.** Food Rescue Hero, MEANS Database, Careit and
Copia are all *boards*: post a donation, wait for a human to claim it. None reasons about
whether the receiving pantry has cold storage free right now, whether a driver can beat
the perishability window, or whether the donation still qualifies for Bill Emerson Good
Samaritan Act protection. They are, precisely, another app someone has to open and
manage. Gleaner is the one that doesn't need opening.

---

## Quick start

Requires Python 3.12+ and Node 20+. AWS credentials with Amazon Bedrock access are
needed only to run a live agent; the test suite runs entirely offline.

```bash
git clone <this repo> && cd gleaner

python -m venv .venv && source .venv/bin/activate
pip install -e .                     # required — the API will not start without it
pytest -m "not integration"          # 306 offline tests, no network, no credentials

# terminal 1 — the control plane
uvicorn gleaner.api.app:create_app --factory --port 8000

# terminal 2 — the console
cd frontend && npm install && npm run dev     # http://localhost:5173
```

Then click **Log an offer**. The form is prefilled with a 120 kg pork donation; submit it
and watch Gleaner route it away from the one pantry that does not accept pork.

Set `GLEANER_API` if the control plane is not on port 8000:
`GLEANER_API=http://127.0.0.1:8010 npm run dev`.

### Without a browser

```bash
gleaner-demo                         # one seeded offer through the whole loop

curl -X POST localhost:8000/api/offers -H 'Content-Type: application/json' -d '{
  "offer_id":"off-1","donor_id":"don-1",
  "lots":[{"description":"pork sausages","weight_kg":400,"temp_class":"chilled","dietary_flags":["pork"]}],
  "ready_at":"2026-09-01T12:00:00+00:00","pickup_address":"Steel City Catering, Pittsburgh",
  "lat":40.4406,"lon":-79.9959,"minutes_out_of_refrigeration":35}'

curl localhost:8000/api/inbox        # the escalation, with the rule that fired
curl localhost:8000/api/timeline     # every model call and tool call, in order
```

### One container

```bash
docker build -t gleaner . && docker run -p 8080:8080 \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_REGION=us-east-1 gleaner
```

The image builds the console and serves it from the API, so there is a single artifact
and a single origin.

---

## How it works

```
offer ─► Intake ─► Safety ─► Match ─► Route ─► Commit ─► ledger
                                                  │
                              EscalationPolicy ───┤ held?
                                                  ▼
                                         decision inbox ─► human answers ─► run resumes
```

**The agent** is a Strands graph. **The maths is not the model's job** — perishability,
capacity, scoring and policy are pure functions the agent calls, so the safety-critical
logic is exhaustively testable without a language model in the loop.

**The autonomy boundary is code.** `EscalationPolicy` is a Strands `InterventionHandler`
evaluated before every consequential tool call. It returns `Proceed` or `Confirm`, and a
`Confirm` breaks the agent out of its loop so the run can be parked until a human answers.

**It judges the world, not itself.** The policy derives its facts from the offer record
and the pantry register rather than the agent's account of them — so an agent that
understates a load, or omits that the food is pork, is still caught.

**Unknown is never permissive.** A missing field, an unresolvable pantry or a malformed
time escalates. Failing closed is the default in every branch.

**Nothing acts on an unapproved placement.** A separate `PlacementDependencyGuard`
refuses `dispatch_driver`, `notify_pantry` and `notify_donor` unless the ledger already
records a committed placement for that offer. A held placement therefore cannot send a
volunteer driver on a wasted trip.

**It survives being put down.** A paused run snapshots its whole state and resumes hours
later, in a different process, from a structured human decision.

### The autonomy dial

| | conservative | balanced | trusted |
|---|---|---|---|
| Places on its own up to | 25 kg | 150 kg | 500 kg |
| Needs slack of at least | 120 min | 60 min | 30 min |
| Minimum safety confidence | 0.95 | 0.85 | 0.70 |
| Fills at most | 50% | 80% | 95% |

**Two rules ignore the dial entirely.** `liability_uncertain` and `dietary_conflict` fire
at every level and cannot be loosened — food safety and dietary consent are not
preferences. `tests/evals/` asserts this, and the assertion has been verified capable of
failing by deliberately breaking the policy.

> A value exactly *at* a threshold is treated as autonomous-safe: the rules escalate on
> strict inequality. 150.0 kg commits unattended at `balanced`; 150.1 kg does not.

### What the claim is, precisely

Gleaner cannot autonomously commit food that is unsafe, out of window, over capacity, or
in dietary conflict. Two honest qualifications:

1. **Weight and handling class remain the agent's word.** They cannot be read off the
   offer record without wrongly rejecting legitimate partial placements. Overstating
   either escalates, so the residual risk is one-directional.
2. **"Unsafe" means arithmetically unsafe, not legally protected.** `liability_ok` is a
   documented cold-chain proxy — *the budget has not been blown* — and is explicitly
   **not** a Bill Emerson Good Samaritan Act determination.

---

## Testing

```bash
pytest -m "not integration"   # 306 tests, offline
pytest -m integration         # 6 tests against live Bedrock — these are billed
```

`tests/evals/` is the safety argument: named scenarios a coordinator would recognise
("pork sausages offered to a halal pantry", "would fill the last of the cold store"),
asserting that no autonomy level can dial away safety or consent, and that raising
autonomy never *increases* escalations.

---

## Impact figures

Derived from published conversion factors, not invented:

- **0.544 kg per meal** — 1.2 lb, Feeding America's standard conversion, also used by
  ReFED when reporting meals from rescued food.
- **2.5 kg CO₂e per kg** — EPA WARM mid-range for mixed food waste diverted from landfill.

Meals round down; a partial meal is not a meal.

---

## Layout

```
src/gleaner/
  domain/      perishability clock, models, capacity, scoring, impact — pure functions
  policy/      rules.py (the thresholds), escalation.py (the Strands handler), dependency.py
  loops/place/ tools, the context provider, the runner
  adapters/    WorldAdapter protocol + a deterministic, seeded Pittsburgh
  hooks/       the audit trail
  persistence/ the decision inbox
  api/         FastAPI control plane
frontend/      React 18 + Vite console
tests/evals/   the safety argument
```

Not yet built, and named as such rather than implied: the FORECAST and SOURCE loops, real
geocoding and routing, and live email/Slack channels.

## Licence

Apache-2.0.
