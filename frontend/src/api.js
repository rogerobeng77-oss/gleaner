/**
 * The one place the console talks to the control plane.
 *
 * Every call goes through `req`, so a backend that is down, slow, or returning
 * an error surfaces the same way everywhere instead of each screen inventing its
 * own failure mode. The console is read-mostly: it reads state the agents
 * produce, and writes exactly two things — a human's decision, and a new offer.
 */

const BASE = "";

async function req(path, options = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (cause) {
    throw new Error(`Cannot reach the control plane. Is the API running on port 8000?`, { cause });
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await res.text().catch(() => "");
    }
    throw new Error(detail || `${options.method || "GET"} ${path} failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  health: () => req("/health"),
  world: () => req("/api/world"),
  inbox: () => req("/api/inbox"),
  ledger: () => req("/api/ledger"),
  impact: () => req("/api/impact"),
  timeline: () => req("/api/timeline"),
  autonomy: () => req("/api/autonomy"),

  setAutonomy: (level) =>
    req("/api/autonomy", { method: "PUT", body: JSON.stringify({ level }) }),

  answer: (decisionId, { outcome, by, role, note }) =>
    req(`/api/inbox/${encodeURIComponent(decisionId)}/answer`, {
      method: "POST",
      body: JSON.stringify({ outcome, by, role, note }),
    }),

  submitOffer: (offer) =>
    req("/api/offers", { method: "POST", body: JSON.stringify(offer) }),
};

/** Everything the console needs, in one round trip. */
export async function loadAll() {
  const [world, inbox, ledger, impact, timeline, autonomy] = await Promise.all([
    api.world(),
    api.inbox(),
    api.ledger(),
    api.impact(),
    api.timeline(),
    api.autonomy(),
  ]);
  return { world, inbox, ledger, impact, timeline, autonomy };
}
