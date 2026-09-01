import { create } from "zustand";
import { api, loadAll } from "./api";

/**
 * One store for the whole console.
 *
 * The backend is the source of truth for everything: which offers were placed,
 * what the agent decided, what is waiting on a human. The store never computes
 * a placement or evaluates a rule itself — it refetches and renders. That
 * matters, because a console that reimplements the policy would be able to
 * disagree with the agent, and the whole point is that it cannot.
 */
export const useStore = create((set, get) => ({
  route: "today",
  q: "",
  loading: true,
  error: null,
  busy: null, // id of the decision currently being answered
  toast: null,

  world: { pantries: [], drivers: [], donors: [], now: null },
  inbox: [],
  ledger: [],
  impact: { placements: 0, kg_rescued: 0, meals: 0, co2e_kg: 0, counterfactual_kg_wasted: 0 },
  timeline: [],
  autonomy: { level: "balanced", thresholds: {} },

  go: (route) => set({ route }),
  setQuery: (q) => set({ q }),

  notify(message) {
    set({ toast: { message, at: Date.now() } });
    setTimeout(() => {
      if (Date.now() - (get().toast?.at ?? 0) >= 2600) set({ toast: null });
    }, 2700);
  },

  async refresh({ quiet = false } = {}) {
    if (!quiet) set({ loading: true });
    try {
      const data = await loadAll();
      set({ ...data, loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: e.message });
    }
  },

  async setAutonomy(level) {
    const previous = get().autonomy;
    set({ autonomy: { ...previous, level } }); // optimistic; the dial should feel instant
    try {
      const autonomy = await api.setAutonomy(level);
      set({ autonomy });
      get().notify(`Autonomy set to ${level}`);
      await get().refresh({ quiet: true });
    } catch (e) {
      set({ autonomy: previous, error: e.message });
    }
  },

  async answer(decisionId, outcome, note) {
    set({ busy: decisionId });
    try {
      await api.answer(decisionId, {
        outcome,
        by: "Roger K",
        role: "coordinator",
        note: note || "",
      });
      get().notify(outcome === "approve" ? "Placement approved" : "Decision recorded");
      await get().refresh({ quiet: true });
    } catch (e) {
      set({ error: e.message });
    } finally {
      set({ busy: null });
    }
  },

  async submitOffer(offer) {
    set({ busy: "new-offer" });
    try {
      const result = await api.submitOffer(offer);
      const held = result.status === "awaiting_decision";
      // Three outcomes, not two. A run can finish having committed nothing —
      // no pantry could take the food in time — and reporting that as "placed"
      // is the one lie this console must never tell, because the counters
      // right next to the toast say zero.
      get().notify(
        held
          ? `Offer held for you — ${result.rule || "policy stopped it"}`
          : result.status === "placed"
            ? "Offer placed without interrupting you"
            : "Nowhere could take it in time — nothing was placed",
      );
      await get().refresh({ quiet: true });
      if (held) set({ route: "decisions" });
      return result;
    } catch (e) {
      set({ error: e.message });
      throw e;
    } finally {
      set({ busy: null });
    }
  },
}));
