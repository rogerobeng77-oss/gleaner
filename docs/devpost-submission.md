# Gleaner — Devpost submission copy

Paste-ready. Track: **Good Neighbor Agents**.

---

## Elevator pitch (200 char limit)

> Only 2.5% of surplus food gets donated. The problem isn't generosity, it's logistics. Gleaner places donations before they spoil, and only wakes a human when the call is genuinely theirs.

---

## Inspiration

A food bank director in Florida said something this year that I haven't been able to shake. Grocery stores had food ready for him to collect. He had to say no. Not because he didn't want it, not because his shelves were full. He didn't have a refrigerated van free.

That's the whole problem in one sentence. The food existed. The hunger existed. They were in the same city on the same afternoon. What failed was a van.

Once I started looking, the numbers were worse than I expected. In 2024, **2.5% of surplus food in the US was donated**. Growers donated **1.6%** of theirs. Meanwhile 47.9 million Americans were food insecure. ReFED, who track this properly, are blunt about the cause: distribution and logistics, not supply. It's also why most donated food is shelf-stable. Tins travel. Salad doesn't. So food banks end up buying the fresh produce they most need, while fresh produce a few miles away goes into a skip.

Here's the part that bothered me most. There is already software for this. Food Rescue Hero, MEANS Database, Careit, Copia. I looked at all of them. They're notice boards. A grocer posts a donation and then everybody waits for a human to notice, decide, and claim it. The deciding is the slow part, and the deciding is the part nobody automated.

So that's who I built it for. The one or two people at a small rescue org running the whole operation off a phone, a spreadsheet and a WhatsApp group, losing an afternoon to placing a pallet of lettuce that has four hours left in it.

## What it does

Gleaner takes a donation offer and gets it to a pantry before it spoils.

It works out how long the food has left, using published cold-chain budgets rather than a guess. Chilled food gets 240 minutes above 5°C, and if the crates already sat on a loading dock for 90 of those, it only has 150 left. It finds pantries that can genuinely take the food, which means cold storage free right now, open when the driver would actually arrive, and no dietary conflict. It finds a driver with a refrigerated vehicle if the food needs one, checks they beat the clock, and commits.

Then it stops and tells you, only if the call is genuinely yours.

That last bit is the actual product. Everything else is table stakes.

Eight named rules can stop it. The load is heavier than you've authorised. The window is too tight. Too little is known about how the food was held. It would fill more of the cold store than you allow. Nowhere can take it in time. The donor is someone nobody has met. The cold chain looks blown. Or the food conflicts with what that pantry's community will accept.

You set how much rope it gets. Conservative places up to 25kg alone. Trusted goes to 500kg. Two rules ignore that dial completely. Food safety and dietary consent are not preferences, so `liability_uncertain` and `dietary_conflict` fire at every level and there is no setting that loosens them. There's a test suite that asserts this, and I checked the assertion can actually fail by deliberately breaking the policy and watching the right tests go red.

When it stops, you get the rule that fired in plain English, not a rule code. "400 kg exceeds the balanced limit of 150 kg. Placement would fill 96% of the store; balanced allows 80%." You answer. The paused run picks up exactly where it left off, possibly hours later, on a different machine.

## How I built it

Strands Agents on Amazon Bedrock, Claude Sonnet 4.6, deployed on App Runner.

The design decision I'd defend hardest: **the maths is not the model's job**. Perishability, capacity, scoring and the escalation rules are all pure Python functions with no model anywhere near them. The agent orchestrates them. It never does the arithmetic itself. That's why I can test the safety-critical logic exhaustively, and why 312 of the tests run offline in under two seconds with no credentials.

The autonomy boundary is a Strands `InterventionHandler` that runs before every consequential tool call. It returns `Proceed` or `Confirm`. A `Confirm` breaks the agent out of its loop, and the run's whole state gets snapshotted so a human can answer whenever they get to it. I found `interventions` and `interrupt` by reading the SDK rather than the docs, and they turned out to be a much closer fit for "runs in the background, surfaces only for real decisions" than anything I'd have built by hand.

The other thing I'd point at: the policy checks the world, not the agent. It reads the donor's own offer record for the dietary flags and the time out of refrigeration. So an agent that quietly omits that the food is pork still gets caught, because the offer record says pork even when the model doesn't.

The demo world is a seeded Pittsburgh with eight real neighbourhoods, cold-storage limits that differ per site, and one pantry that doesn't accept pork. Pittsburgh on purpose: it's where 412 Food Rescue and Food Rescue Hero were built. If I'm going to claim the incumbent can't do something, I should demonstrate it in their city.

## Challenges I ran into

**A driver got dispatched for a placement that was never approved.** This one still bothers me. The policy correctly held a commit, and then the agent called `dispatch_driver` anyway, and that went through, because the guard treated each tool call as its own separate decision. In the real world that sends a volunteer across town to collect food with nowhere to take it. None of my 254 tests caught it, because they all stubbed the agent runner. I only found it by actually running the thing against live Bedrock and reading the ledger afterwards. The fix consults the ledger, which records what actually happened, rather than the agent's account of what it did.

**The escalation reason didn't survive the trip.** The API returned `"rule": "unknown"` with an empty list, while the interrupt itself was carrying "would fill 96% of the store" the whole time. So the coordinator was being shown an escalation the system couldn't name, which quietly undermines the entire "it explains itself" claim.

**`checkpointing=True` silently breaks the human-in-the-loop path.** It looks like the setting you want. It isn't. The run stops with `stop_reason="checkpoint"` before it ever reaches the tool call, and `result.interrupts` comes back `None`. Different feature, similar name, no error to tell you.

**`claude-sonnet-5` is listed by the Bedrock API and not callable.** `list-foundation-models` returns it happily. Invoking it returns `AccessDeniedException`. I now probe with a real call before trusting any model ID.

## Accomplishments that I'm proud of

The escalation policy is executable, versioned code, not a paragraph in a prompt. You can read it, test it, and diff it. I think that's the right shape for agent autonomy and I'd build it this way again.

The eval suite is honest. I made five targeted breaks to the policy, one at a time, and confirmed the right tests failed each time before reverting. An eval suite that passes whatever the policy does is worse than no suite at all. It just tells you what you want to hear.

And a small one that matters to me: when a pantry can't take pork, the console offers **reroute**, not override. The system respects consent instead of asking a tired coordinator to click past it at 6pm.

## What I learned

Running the thing found bugs that 254 passing tests didn't. Every serious defect in this project surfaced in a live run, not a test, because the tests stubbed the expensive part and the expensive part was where the bugs lived.

I also learned to be more careful about what I claim. At one point I'd written that the agent "cannot autonomously commit food in dietary conflict", and it wasn't true on the API path, because offers submitted over HTTP were never registered with the world, so the guard fell back to trusting the model. The claim was one line of code away from being true, but it wasn't true when I wrote it. I'd rather ship a narrow claim I can defend than a broad one that falls apart when a judge pokes it.

The liability check is a good example of the same instinct. It's a cold-chain proxy. It says the budget hasn't been blown. It is not a Bill Emerson Good Samaritan Act determination, and the code says so in the docstring, at the point of use, where someone reading it will actually see it.

## What's next for Gleaner

Two more loops, already designed. **Forecast** predicts each pantry's demand and free cold storage a week out, including the benefit-cycle timing that visibly drives pantry footfall, so placement gets ahead of need instead of chasing it. **Source** spots donors whose pattern says food is going spare, the grocer who always donates on Tuesday and hasn't this week, and drafts the ask. Messaging a real business always needs a person to approve it.

After that: real geocoding and road routing in place of straight-line estimates, and inbound email so a grocer can donate by replying to a thread instead of learning another app.

The honest gap in the current routing is that the ETA only covers pickup to pantry. It doesn't count the driver getting to the pickup. The margin it reports is therefore optimistic, and that's written into the docstring rather than hidden.

## Testing instructions

No login, no credentials, nothing to install. Open the live console and click **Log an offer**.

**1. Watch it place one on its own.** The form is prefilled with 120 kg of chilled pork sausages. Submit it. The agent takes 10 to 30 seconds, because it is a real Bedrock call and not a canned response. It routes the pork away from Hazelwood, the one pantry in the network that does not accept pork, and places it at Squirrel Hill instead. The home screen updates: one placement, 220 meals.

**2. Now watch it stop.** Log another offer, same food, but change the weight to 400 kg. This time it refuses to act alone and lands in **Decisions**, naming both rules that fired: over the 150 kg limit for the current autonomy level, and it would fill 96% of the pantry's cold store. Approve or decline it, and the paused run resumes from where it stopped.

**Worth a look while you are there:** **Autonomy** shows the three levels and the two rules that cannot be loosened at any of them. **Timeline** shows every model call and tool call the agent made. **Ledger** records only what actually happened, so a held placement leaves no entry.

To run it locally instead, see Quick start in the README. The test suite needs no AWS credentials: `pytest -m "not integration"`.

---

## Built with

python · fastapi · strands-agents · amazon-bedrock · claude · aws-app-runner · amazon-ecr · react · vite · zustand · pydantic · pytest · docker

## Try it out

- Live console: https://m6pane4ysw.us-west-2.awsapprunner.com
- Source: https://github.com/rogerobeng77-oss/gleaner

Click **Log an offer**. It's prefilled with 120kg of pork sausages. Watch it route around the one pantry that doesn't accept pork. Then change the weight to 400kg and watch it stop and ask you instead.
