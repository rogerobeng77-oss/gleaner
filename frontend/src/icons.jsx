import React from "react";

const P = {
  today: <path d="M3 10.5 12 3l9 7.5V21H3z" />,
  decisions: (<><path d="M4 5h16v14H4z" /><path d="M3 12h5l2 3h4l2-3h5" /></>),
  pantries: (<><path d="M4 20V9l8-5 8 5v11z" /><path d="M9 20v-6h6v6" /></>),
  drivers: (<><path d="M3 13h18l-2-5H5z" /><circle cx="7" cy="17" r="2" /><circle cx="17" cy="17" r="2" /></>),
  autonomy: (<><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /></>),
  timeline: <path d="M4 6h16M4 12h16M4 18h10" />,
  ledger: (<><path d="M5 4h14v16H5z" /><path d="M9 9h6M9 13h6" /></>),
  impact: <path d="M4 19V9M10 19V5M16 19v-7M22 19H2" />,
  clock: (<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  trend: <path d="M3 17l5-6 4 3 5-7 4 4" />,
  mail: (<><path d="M4 6h16v12H4z" /><path d="m4 7 8 6 8-6" /></>),
  search: (<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>),
  plus: <path d="M12 5v14M5 12h14" />,
};

export default function Icon({ name, size = 19 }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
         width={size} height={size} aria-hidden="true">
      {P[name] ?? null}
    </svg>
  );
}
