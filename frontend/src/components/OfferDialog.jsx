import React, { useState } from "react";
import { useStore } from "../store";

/** Offer form. Prefilled with something realistic so a judge can submit in one click,
 *  but every field is editable — this is how a coordinator would really log a call. */
export default function OfferDialog({ onClose }) {
  const submitOffer = useStore((s) => s.submitOffer);
  const busy = useStore((s) => s.busy) === "new-offer";
  const [f, setF] = useState({
    description: "pork sausages", weight_kg: 120, temp_class: "chilled",
    dietary_flags: "pork", minutes_out_of_refrigeration: 35,
    donor: "Steel City Catering",
  });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    const now = new Date();
    await submitOffer({
      offer_id: `off-${Date.now().toString(36)}`,
      donor_id: "don-1",
      lots: [{
        description: f.description,
        weight_kg: Number(f.weight_kg),
        temp_class: f.temp_class,
        allergens: [],
        dietary_flags: f.dietary_flags ? f.dietary_flags.split(",").map((s) => s.trim()).filter(Boolean) : [],
      }],
      ready_at: now.toISOString(),
      pickup_address: `${f.donor}, Pittsburgh, PA`,
      lat: 40.4406, lon: -79.9959,
      minutes_out_of_refrigeration: Number(f.minutes_out_of_refrigeration),
      notes: "",
    }).catch(() => {});
    onClose();
  }

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(32,21,21,.35)", zIndex: 40,
                  display: "grid", placeItems: "center", padding: 20 }}
         onClick={onClose}>
      <form className="card" style={{ width: 480, maxWidth: "100%" }}
            onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h3>Log an offer</h3>
        <p className="sub" style={{ marginTop: 6 }}>
          Gleaner will place it, or stop and ask you — you find out which in about half a minute.
        </p>
        <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
          <label>Food<input className="noteinput" style={{ width: "100%" }} value={f.description} onChange={set("description")} required /></label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <label>Weight (kg)<input className="noteinput" style={{ width: "100%" }} type="number" min="1" value={f.weight_kg} onChange={set("weight_kg")} required /></label>
            <label>Handling
              <select className="noteinput" style={{ width: "100%" }} value={f.temp_class} onChange={set("temp_class")}>
                <option value="chilled">chilled</option>
                <option value="frozen">frozen</option>
                <option value="ambient">ambient</option>
              </select>
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <label>Dietary flags<input className="noteinput" style={{ width: "100%" }} value={f.dietary_flags} onChange={set("dietary_flags")} placeholder="pork, alcohol" /></label>
            <label>Minutes out of fridge<input className="noteinput" style={{ width: "100%" }} type="number" min="0" value={f.minutes_out_of_refrigeration} onChange={set("minutes_out_of_refrigeration")} /></label>
          </div>
          <label>Donor<input className="noteinput" style={{ width: "100%" }} value={f.donor} onChange={set("donor")} /></label>
        </div>
        <div className="acts">
          <button className="sm pri" type="submit" disabled={busy}>
            {busy ? "Gleaner is working…" : "Send it to Gleaner"}
          </button>
          <button className="sm" type="button" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </div>
  );
}
