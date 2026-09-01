import React, { useState } from "react";
import { useStore } from "./store";
import Icon from "./icons.jsx";

/** Safety and consent are not preferences — these two escalate at every level. */
const NON_NEGOTIABLE = new Set(["liability_uncertain", "dietary_conflict"]);

const RULE_TEXT = {
  liability_uncertain: "The cold-chain budget looks blown, so Good Samaritan protection cannot be assumed.",
  dietary_conflict: "The food clashes with what this pantry's community will accept.",
  no_viable_match: "Nowhere can take it in time. Someone needs to ring round.",
  over_value_threshold: "The load is heavier than Gleaner may commit unattended.",
  tight_window: "The driver would arrive with less slack than your level allows.",
  safety_ambiguous: "Too little is known about how the food has been held.",
  capacity_overrun: "The placement would fill more of the cold store than you permit.",
  novel_donor: "First offer from this donor, and nobody has met them yet.",
};

const LEVELS = ["conservative", "balanced", "trusted"];
const LEVEL_NOTE = { conservative: "strict", balanced: "", trusted: "loose" };

const kg = (n) => `${Math.round(Number(n) || 0)} kg`;
const when = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(+d) ? "—" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

export function Loading() {
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div className="skel" style={{ height: 44, width: "60%", margin: "0 auto" }} />
      <div className="skel" style={{ height: 140, maxWidth: 940, margin: "12px auto 0", width: "100%" }} />
      <div className="tiles" style={{ marginTop: 26 }}>
        {[0, 1, 2].map((i) => <div key={i} className="skel" style={{ height: 118 }} />)}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- Today */

export function Today() {
  const { inbox, ledger, impact, autonomy, go } = useStore();
  const held = inbox.length;
  const committed = ledger.filter((e) => e.kind === "commit_placement").length;

  return (
    <>
      <section className="hero">
        <h1>
          {committed === 0
            ? "Nothing placed yet, Roger."
            : committed === 1
              ? "One offer placed itself, Roger."
              : `${committed} offers placed themselves, Roger.`}
        </h1>
        <p>{held ? `${held} need${held > 1 ? "" : "s"} you. Everything else is already moving.` : "Nothing is waiting on you."}</p>
      </section>

      <section className="console">
        <div className="chd"><span className="dot" />PLACE loop — running at {autonomy.level}</div>
        <div className="stats">
          <div><div className="k">Awaiting you</div>
            <div className="v" style={{ color: held ? "var(--crit)" : "var(--ok)" }}>{held}</div></div>
          <div><div className="k">Placed unattended</div><div className="v">{committed}</div></div>
          <div><div className="k">Rescued</div><div className="v">{impact.kg_rescued}<small> kg</small></div></div>
          <div><div className="k">Meals</div><div className="v">{impact.meals.toLocaleString()}</div></div>
        </div>
        <p className="caption">Gleaner acts on its own inside the limits you set, and stops the moment a call is genuinely yours.</p>
      </section>

      <h2 className="sec">The three loops</h2>
      <p className="lede">Three background loops, one decision inbox — so a coordinator has a single place to look.</p>
      <div className="tiles">
        <Tile icon="clock" title="Place" pill="ship" onClick={() => go("timeline")}
          body="A donation arrives. Gleaner checks the food is still safe, finds a pantry that can genuinely receive it, routes a driver who beats the clock, and commits." />
        <Tile icon="trend" title="Forecast" pill="next" tone="v2"
          body="Predicts each pantry's demand and free cold storage a week out, so placement becomes pre-emptive rather than reactive." />
        <Tile icon="mail" title="Source" pill="next" tone="g"
          body="Notices surplus nobody offered — a grocer who always donates on Tuesday and hasn't — and drafts the ask." />
      </div>

      {held > 0 && (
        <>
          <h2 className="sec">Waiting on you</h2>
          <p className="lede">Ranked with the non-negotiable calls first.</p>
          {inbox.slice(0, 2).map((d) => <DecisionCard key={d.decision_id} d={d} />)}
          {held > 2 && <button className="sm" onClick={() => go("decisions")}>See all {held} decisions</button>}
        </>
      )}
    </>
  );
}

function Tile({ icon, title, body, pill, tone = "", onClick }) {
  return (
    <button className="tile" onClick={onClick} type="button">
      <div className={`ico ${tone}`}><Icon name={icon} size={20} /></div>
      <div>
        <h3>{title}</h3>
        <p>{body}</p>
        <span className={`pill ${pill}`}>{pill === "ship" ? "Shipped" : "Next"}</span>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------ Decisions */

function DecisionCard({ d }) {
  const answer = useStore((s) => s.answer);
  const busy = useStore((s) => s.busy) === d.decision_id;
  const [note, setNote] = useState("");

  const hits = d.rule_hits?.length ? d.rule_hits : d.rule && d.rule !== "unknown" ? [{ rule: d.rule, detail: "" }] : [];
  const hard = hits.some((h) => NON_NEGOTIABLE.has(h.rule));
  const prose = hits.map((h) => h.detail || RULE_TEXT[h.rule]).filter(Boolean).join(" ");

  return (
    <article className={`dcard ${hard ? "hard" : ""}`}>
      <div className={`eyebrow ${hard ? "hard" : "soft"}`}>
        {hard ? "Held — non-negotiable rule" : "Held — outside your limits"}
        {hits.length ? ` · ${hits.length} rule${hits.length > 1 ? "s" : ""}` : ""}
      </div>
      <h3>Offer {d.offer_id}</h3>
      <p className="why">{prose || d.prompt}</p>
      {hits.length > 0 && (
        <div className="chips">
          {hits.map((h) => (
            <span key={h.rule} className={`chip ${NON_NEGOTIABLE.has(h.rule) ? "nn" : ""}`}>{h.rule}</span>
          ))}
        </div>
      )}
      <div className="acts">
        <button className="sm pri" disabled={busy}
                onClick={() => answer(d.decision_id, "approve", note)}>
          {busy ? "Sending…" : "Approve placement"}
        </button>
        <button className="sm danger" disabled={busy}
                onClick={() => answer(d.decision_id, "reject", note)}>
          Decline
        </button>
        <input className="noteinput" value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="Why? (recorded against your name)" aria-label="Decision note" />
      </div>
    </article>
  );
}

export function Decisions() {
  const { inbox, autonomy, go } = useStore();
  return (
    <>
      <div className="phead">
        <h1>Decisions</h1>
        <p>
          Gleaner stopped on these. Everything else it handled. Every answer is recorded against
          your name and resumes the paused run exactly where it left off. Change the{" "}
          <button className="sm" style={{ height: 26, padding: "0 9px" }} onClick={() => go("autonomy")}>
            autonomy level
          </button>{" "}
          to shift where that line sits.
        </p>
      </div>
      {inbox.length
        ? inbox.map((d) => <DecisionCard key={d.decision_id} d={d} />)
        : (
          <div className="empty">
            <strong>Nothing is waiting on you</strong>
            Every offer so far sits inside your {autonomy.level} limits.
          </div>
        )}
    </>
  );
}

/* ------------------------------------------------------------- Autonomy */

export function Autonomy() {
  const { autonomy, setAutonomy, inbox } = useStore();
  const T = autonomy.thresholds || {};
  const firing = {};
  inbox.forEach((d) => (d.rule_hits || []).forEach((h) => { firing[h.rule] = (firing[h.rule] || 0) + 1; }));

  return (
    <>
      <div className="phead">
        <h1>Autonomy</h1>
        <p>
          You decide how much authority Gleaner has. Moving the dial shifts four thresholds at
          once and takes effect on the very next offer.
        </p>
      </div>

      <div className="dial">
        {LEVELS.map((l) => {
          const on = autonomy.level === l;
          return (
            <button key={l} className={`lvl ${on ? "on" : ""}`} onClick={() => setAutonomy(l)}>
              <header>
                {l[0].toUpperCase() + l.slice(1)}
                <span className="state">{on ? "in use" : LEVEL_NOTE[l]}</span>
              </header>
              {on ? (
                <dl>
                  <dt>Places on its own up to</dt><dd>{T.max_autonomous_kg} kg</dd>
                  <dt>Needs slack of at least</dt><dd>{T.required_margin_minutes} min</dd>
                  <dt>Minimum safety confidence</dt><dd>{T.min_safety_confidence}</dd>
                  <dt>Fills at most</dt><dd>{Math.round((T.max_capacity_fraction || 0) * 100)}% of a store</dd>
                </dl>
              ) : (
                <dl><dt>Select to see this level's limits</dt><dd>&nbsp;</dd></dl>
              )}
            </button>
          );
        })}
      </div>

      <div className="note">
        <b>Two rules ignore the dial entirely.</b> Food-safety liability and dietary consent are not
        preferences, so they escalate at every level and cannot be loosened. A test suite asserts
        this, and the assertion has been proven capable of failing.
      </div>

      <h2 className="sec">Every rule that can stop the agent</h2>
      <p className="lede">Highlighted rules are firing on the decisions waiting for you now.</p>
      <div className="rules">
        {Object.entries(RULE_TEXT).map(([r, text]) => {
          const nn = NON_NEGOTIABLE.has(r);
          const n = firing[r] || 0;
          return (
            <div key={r} className={`rule-i ${n ? "firing" : ""}`}>
              <span className={`lock ${nn ? "hard" : ""}`}>{nn ? "!" : "◐"}</span>
              <div><div className="rn">{r}</div><div className="rd">{text}</div></div>
              {n > 0 && <span className="cnt">×{n}</span>}
            </div>
          );
        })}
      </div>
    </>
  );
}

/* ---------------------------------------------------- Pantries, Drivers */

export function Pantries() {
  const { world, q } = useStore();
  const list = world.pantries.filter((p) => !q || p.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <div className="phead">
        <h1>Pantry network</h1>
        <p>
          The <b>recent fresh</b> column drives the equity term — sites that have received least get
          boosted, so produce doesn't simply flow to whoever is nearest the depot.
        </p>
      </div>
      <div className="rows">
        <div className="row pantry rh"><div>Site</div><div>Cold storage</div><div>Utilisation</div><div>Recent fresh</div></div>
        {list.map((p) => {
          const free = Math.max(0, p.cold_capacity_kg - p.cold_used_kg);
          const pct = p.cold_capacity_kg ? Math.round((p.cold_used_kg / p.cold_capacity_kg) * 100) : 100;
          return (
            <div className="row pantry" key={p.pantry_id}>
              <div>
                <div className="nm">{p.name}</div>
                <div className="sub">
                  {String(p.opens_at_hour).padStart(2, "0")}:00–{String(p.closes_at_hour).padStart(2, "0")}:00
                  {p.dietary_restrictions?.length ? ` · does not accept ${p.dietary_restrictions.join(", ")}` : ""}
                </div>
              </div>
              <div><div className="mono" style={{ fontSize: 13.5 }}>{kg(free)} free</div>
                   <div className="sub">of {kg(p.cold_capacity_kg)} chilled</div></div>
              <div><div className="bar"><i className={pct > 85 ? "crit" : pct > 65 ? "warn" : ""} style={{ width: `${pct}%` }} /></div>
                   <div className="sub">{pct}% full</div></div>
              <div><div className="mono" style={{ fontSize: 13.5 }}>{kg(p.recent_fresh_kg_30d)}</div>
                   <div className="sub">30 days</div></div>
            </div>
          );
        })}
        {!list.length && <div className="row"><div style={{ color: "var(--ink-3)" }}>No pantry matches.</div></div>}
      </div>
    </>
  );
}

export function Drivers() {
  const { world, q } = useStore();
  const list = world.drivers.filter((d) => !q || d.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <div className="phead">
        <h1>Drivers</h1>
        <p>Only refrigerated vehicles are offered chilled or frozen loads, and availability windows are checked before a dispatch is ever proposed.</p>
      </div>
      <div className="rows">
        <div className="row driver rh"><div>Driver</div><div>Vehicle</div><div>Available</div><div>State</div></div>
        {list.map((d) => (
          <div className="row driver" key={d.driver_id}>
            <div><div className="nm">{d.name}</div><div className="sub">{d.driver_id}</div></div>
            <div>{d.refrigerated ? <span className="tag cold">refrigerated</span> : <span className="tag amb">ambient only</span>}</div>
            <div className="mono" style={{ fontSize: 13 }}>{when(d.available_from)} – {when(d.available_until)}</div>
            <div><span className="tag auto">on the roster</span></div>
          </div>
        ))}
      </div>
    </>
  );
}

/* ------------------------------------------- Timeline, Ledger, Impact */

export function Timeline() {
  const { timeline } = useStore();
  const events = [...timeline].reverse();
  return (
    <>
      <div className="phead">
        <h1>Timeline</h1>
        <p>Every model call, tool call and escalation, produced by hooks on the agent loop — so the agent never has to report on itself.</p>
      </div>
      {events.length ? (
        <div className="tl">
          {events.map((e, i) => (
            <div className={`ev ${e.kind}`} key={i}>
              <div className="t">{when(e.at)} · {e.kind}</div>
              <div className="m"><b>{e.label}</b></div>
              {e.detail && Object.keys(e.detail).length > 0 && (
                <div className="d">{JSON.stringify(e.detail)}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="empty"><strong>Nothing recorded yet</strong>Log an offer and the agent's reasoning appears here.</div>
      )}
    </>
  );
}

export function Ledger() {
  const { ledger } = useStore();
  return (
    <>
      <div className="phead">
        <h1>Ledger</h1>
        <p>The append-only record of what actually happened in the world — not what the agent believes happened. A held placement leaves no entry.</p>
      </div>
      <div className="rows">
        <div className="row ledger rh"><div>Action</div><div>Detail</div><div>At</div></div>
        {ledger.length ? ledger.map((e, i) => (
          <div className="row ledger" key={i}>
            <div><span className={`tag ${e.kind === "commit_placement" ? "auto" : "cold"}`}>{e.kind}</span></div>
            <div className="sub" style={{ marginTop: 0 }}>
              {[e.offer_id, e.pantry_id, e.driver_id, e.weight_kg && kg(e.weight_kg), e.message]
                .filter(Boolean).join(" · ")}
            </div>
            <div className="mono" style={{ fontSize: 13 }}>{when(e.at)}</div>
          </div>
        )) : <div className="row"><div style={{ color: "var(--ink-3)" }}>Nothing committed yet.</div></div>}
      </div>
    </>
  );
}

export function Impact() {
  const { impact, ledger } = useStore();
  const commits = ledger.filter((e) => e.kind === "commit_placement");
  return (
    <>
      <div className="phead">
        <h1>Impact</h1>
        <p>
          Computed from published conversion factors, not invented — 0.544 kg per meal
          (Feeding America / ReFED) and 2.5 kg CO₂e avoided per kg diverted from landfill (EPA WARM).
        </p>
      </div>
      <div className="stats" style={{ borderRadius: "var(--r)" }}>
        <div><div className="k">Placements</div><div className="v">{impact.placements}</div></div>
        <div><div className="k">Food rescued</div><div className="v">{impact.kg_rescued}<small> kg</small></div></div>
        <div><div className="k">Meals</div><div className="v">{impact.meals.toLocaleString()}</div></div>
        <div><div className="k">CO₂e avoided</div><div className="v">{impact.co2e_kg}<small> kg</small></div></div>
      </div>
      <h2 className="sec">What was placed</h2>
      <div className="cards">
        <div className="card">
          <h3>Committed to a pantry</h3>
          <ul>
            {commits.length
              ? commits.map((c, i) => <li key={i}><b>{kg(c.weight_kg)}</b> → {c.pantry_id}</li>)
              : <li>Nothing yet.</li>}
          </ul>
        </div>
        <div className="card">
          <h3>How the figures are derived</h3>
          <ul>
            <li><b>Meals</b> — rescued mass ÷ 0.544 kg, rounded down. A partial meal is not a meal.</li>
            <li><b>CO₂e</b> — rescued mass × 2.5 kg, the EPA WARM mid-range for food diverted from landfill.</li>
            <li><b>Counterfactual</b> — {impact.counterfactual_kg_wasted} kg, what would have been wasted without the agent.</li>
          </ul>
        </div>
      </div>
    </>
  );
}
