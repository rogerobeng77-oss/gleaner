import React, { useEffect, useState } from "react";
import { useStore } from "./store";
import Icon from "./icons.jsx";
import * as V from "./views.jsx";
import OfferDialog from "./components/OfferDialog.jsx";

const NAV = [
  ["today", "Today", "today"],
  ["decisions", "Decisions", "decisions"],
  ["pantries", "Pantries", "pantries"],
  ["drivers", "Drivers", "drivers"],
  null,
  ["autonomy", "Autonomy", "autonomy"],
  ["timeline", "Timeline", "timeline"],
  ["ledger", "Ledger", "ledger"],
  ["impact", "Impact", "impact"],
];

const VIEWS = {
  today: V.Today, decisions: V.Decisions, pantries: V.Pantries, drivers: V.Drivers,
  autonomy: V.Autonomy, timeline: V.Timeline, ledger: V.Ledger, impact: V.Impact,
};

export default function App() {
  const { route, go, refresh, loading, error, inbox, impact, ledger, autonomy, toast, q, setQuery } =
    useStore();
  const [dialog, setDialog] = useState(false);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const t = setInterval(() => refresh({ quiet: true }), 8000);
    return () => clearInterval(t);
  }, [refresh]);
  useEffect(() => {
    const h = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        document.getElementById("q")?.focus();
      }
    };
    addEventListener("keydown", h);
    return () => removeEventListener("keydown", h);
  }, []);

  const held = inbox.length;
  const committed = ledger.filter((e) => e.kind === "commit_placement").length;
  const total = committed + held;
  const View = VIEWS[route] ?? V.Today;

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand"><span className="mark" />gleaner</div>
        <button className="cta" onClick={() => setDialog(true)}>
          <Icon name="plus" size={17} />Log an offer
        </button>
        <nav className="nav">
          {NAV.map((item, i) =>
            item === null ? <div className="divider" key={`d${i}`} /> : (
              <button key={item[0]} className={route === item[0] ? "on" : ""} onClick={() => go(item[0])}>
                <Icon name={item[2]} />{item[1]}
                {item[0] === "decisions" && (
                  <span className={`badge ${held ? "" : "zero"}`}>{held}</span>
                )}
              </button>
            ),
          )}
        </nav>
        <div className="foot">
          <div className="meter-row">
            <span>Placed without you</span>
            <span className="mono">{committed} / {total}</span>
          </div>
          <div className="meter"><i style={{ width: `${total ? (committed / total) * 100 : 0}%` }} /></div>
          <small>{held ? `${held} held for you at ${autonomy.level}` : "Nothing waiting on you"}</small>
        </div>
      </aside>

      <div>
        <header className="top">
          <div className="search">
            <span style={{ color: "var(--ink-3)", display: "flex" }}><Icon name="search" size={17} /></span>
            <input id="q" value={q} onChange={(e) => setQuery(e.target.value)}
                   placeholder="Search pantries and drivers" aria-label="Search" />
            <span className="kbd">Ctrl K</span>
          </div>
          <div className="tools">
            <span className="conn">
              <span className={`dot ${error ? "bad" : ""}`} />
              {error ? "offline" : "connected"}
            </span>
            <button className="btn-out" onClick={() => refresh()}>Refresh</button>
            <div className="avatar">RK</div>
          </div>
        </header>

        <main>
          <div className="wrap">
            {error && (
              <div className="banner">
                <span><b>{error}</b></span>
                <button className="sm" onClick={() => refresh()}>Try again</button>
              </div>
            )}
            {loading && !impact.placements && !held ? <V.Loading /> : <View />}
          </div>
        </main>
      </div>

      {dialog && <OfferDialog onClose={() => setDialog(false)} />}
      {toast && <div className="toast">{toast.message}</div>}
    </div>
  );
}
