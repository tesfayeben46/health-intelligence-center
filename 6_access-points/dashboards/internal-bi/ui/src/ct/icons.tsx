/** Inline stroke icons (feather-style) used across the control tower. */
type P = { className?: string };
const S = (props: any) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
       strokeLinecap="round" strokeLinejoin="round" {...props} />
);

export const IconChart = (p: P) => (
  <S {...p}><line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" /></S>
);
export const IconCheck = (p: P) => (
  <S {...p}><path d="M20 6 9 17l-5-5" /></S>
);
export const IconCheckCircle = (p: P) => (
  <S {...p}><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><path d="M22 4 12 14.01l-3-3" /></S>
);
export const IconFlow = (p: P) => (
  <S {...p}><circle cx="5" cy="6" r="2" /><circle cx="5" cy="18" r="2" /><circle cx="19" cy="12" r="2" /><path d="M7 6h5a4 4 0 0 1 4 4v0M7 18h5a4 4 0 0 0 4-4v0" /></S>
);
export const IconFocus = (p: P) => (
  <S {...p}><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" /></S>
);
export const IconArrowRight = (p: P) => (
  <S {...p}><path d="M5 12h14" /><path d="m12 5 7 7-7 7" /></S>
);
export const IconTruck = (p: P) => (
  <S {...p}><path d="M14 18V6a1 1 0 0 0-1-1H2a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h1" /><path d="M14 9h4l3 3v5a1 1 0 0 1-1 1h-1" /><circle cx="7" cy="18" r="2" /><circle cx="17" cy="18" r="2" /></S>
);
export const IconRoute = (p: P) => (
  <S {...p}><circle cx="6" cy="19" r="3" /><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" /><circle cx="18" cy="5" r="3" /></S>
);
export const IconPackage = (p: P) => (
  <S {...p}><path d="m7.5 4.27 9 5.15" /><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" /><path d="M3.3 7 12 12l8.7-5" /><path d="M12 22V12" /></S>
);
export const IconShieldCheck = (p: P) => (
  <S {...p}><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" /><path d="m9 12 2 2 4-4" /></S>
);
export const IconGauge = (p: P) => (
  <S {...p}><path d="m12 14 4-4" /><path d="M3.34 19a10 10 0 1 1 17.32 0" /></S>
);
export const IconList = (p: P) => (
  <S {...p}><line x1="8" x2="21" y1="6" y2="6" /><line x1="8" x2="21" y1="12" y2="12" /><line x1="8" x2="21" y1="18" y2="18" /><line x1="3" x2="3.01" y1="6" y2="6" /><line x1="3" x2="3.01" y1="12" y2="12" /><line x1="3" x2="3.01" y1="18" y2="18" /></S>
);
export const IconPin = (p: P) => (
  <S {...p}><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" /><circle cx="12" cy="10" r="3" /></S>
);
export const IconGrid = (p: P) => (
  <S {...p}><rect width="7" height="7" x="3" y="3" rx="1" /><rect width="7" height="7" x="14" y="3" rx="1" /><rect width="7" height="7" x="14" y="14" rx="1" /><rect width="7" height="7" x="3" y="14" rx="1" /></S>
);
export const IconChartLine = (p: P) => (
  <S {...p}><path d="M3 3v16a2 2 0 0 0 2 2h16" /><path d="m19 9-5 5-4-4-3 3" /></S>
);
export const IconAlertTriangle = (p: P) => (
  <S {...p}><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" /><path d="M12 9v4" /><path d="M12 17h.01" /></S>
);
export const IconUpload = (p: P) => (
  <S {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" x2="12" y1="3" y2="15" /></S>
);
export const IconBell = (p: P) => (
  <S {...p}><path d="M10.268 21a2 2 0 0 0 3.464 0" /><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326" /></S>
);
export const IconRefresh = (p: P) => (
  <S {...p}><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M8 16H3v5" /></S>
);
export const IconCalendar = (p: P) => (
  <S {...p}><path d="M8 2v4" /><path d="M16 2v4" /><rect width="18" height="18" x="3" y="4" rx="2" /><path d="M3 10h18" /></S>
);
export const IconChevronD = (p: P) => (
  <S {...p}><path d="m6 9 6 6 6-6" /></S>
);

/* Domain glyphs. Each depicts the thing it labels rather than borrowing a
   generic symbol: a store bin with a fill level, a delivery entering that bin,
   a cover runway, and a store radiating to the facilities it supplies. */

/** Shortfall: a stock bin whose contents sit below the required line. */
export const IconStockShort = (p: P) => (
  <S {...p}>
    <path d="M4 7h16" strokeDasharray="2.5 2.5" />
    <path d="M5 11h14v9H5z" />
    <path d="M5 15h14" />
  </S>
);
/** Inbound: units dropping into the bin. */
export const IconInbound = (p: P) => (
  <S {...p}>
    <path d="M12 2v7" /><path d="m8.5 6 3.5 3.5L15.5 6" />
    <path d="M4 12h16v8H4z" />
  </S>
);
/** Run-out: stock draining out of the bin. */
export const IconRunout = (p: P) => (
  <S {...p}>
    <path d="M5 4h14v9H5z" /><path d="M9 8h6" />
    <path d="M12 14v6" /><path d="m9 17.5 3 3 3-3" />
  </S>
);
/** Cover: how far along the month runway the stock reaches. */
export const IconCover = (p: P) => (
  <S {...p}>
    <path d="M3 12h18" /><path d="M3 5v14" /><path d="M21 5v14" />
    <path d="M9 8.5v7" /><path d="M15 8.5v7" />
    <circle cx="12" cy="12" r="3.2" />
  </S>
);
/** Shortages covered: a cover bar filled against a target tick. */
export const IconCoverBar = (p: P) => (
  <S {...p}>
    <rect x="2.5" y="7" width="19" height="10" rx="5" />
    <path d="M3.5 12h9" strokeWidth="7" strokeLinecap="butt" />
    <path d="M16 3.5v17" />
  </S>
);
/** Units to ship: cartons stacked on a pallet, in elevation. */
export const IconPallet = (p: P) => (
  <S {...p}>
    <path d="M8.5 4h7v5h-7z" />
    <path d="M4 9h7v6H4z" /><path d="M13 9h7v6h-7z" />
    <path d="M3 18h18" /><path d="M6 18v2.5" /><path d="M18 18v2.5" />
  </S>
);
/** Vehicle runs: several runs leaving one depot. */
export const IconDepotFan = (p: P) => (
  <S {...p}>
    <path d="M2.5 5.5h4.5v13H2.5z" />
    <path d="M7 8h7" /><path d="M7 12h11.5" /><path d="M7 16h7" />
    <circle cx="16" cy="8" r="1.9" /><circle cx="20.5" cy="12" r="1.9" /><circle cx="16" cy="16" r="1.9" />
  </S>
);
/** Avg vehicle fill: a truck body with a load line at the fill level. */
export const IconTruckFill = (p: P) => (
  <S {...p}>
    <path d="M3 6h13v11H3z" /><path d="M16 9.5h3l2 3v4.5h-5z" />
    <path d="M3 11.5h13" strokeDasharray="2.5 2" />
    <path d="M3 14.5h13" strokeWidth="4" strokeLinecap="butt" opacity="0.45" />
  </S>
);
/* Operators for the shipment-sizing steps: each marker states what the row does
   to the running balance. */
export const IconArrowDown = (p: P) => (
  <S {...p}><path d="M12 4v15" /><path d="m5.5 12.5 6.5 6.5 6.5-6.5" /></S>
);
export const IconEquals = (p: P) => (
  <S {...p}><path d="M4 9.5h16" /><path d="M4 15.5h16" /></S>
);
export const IconPlus = (p: P) => (
  <S {...p}><path d="M12 4.5v15" /><path d="M4.5 12h15" /></S>
);
/** Brand: a control tower in elevation, signal arcs from the cab. */
export const IconTower = (p: P) => (
  <S {...p}>
    <path d="M9.5 21h5l-1-9h-3z" /><path d="M7.5 8.5h9v3.5h-9z" />
    <path d="M6 6.2A7 7 0 0 1 18 6.2" /><path d="M8.6 3.6a10.5 10.5 0 0 1 6.8 0" />
  </S>
);
/** Overview: a summary page, not a grid of equal squares. */
export const IconSummary = (p: P) => (
  <S {...p}>
    <path d="M3 4h18v4H3z" /><path d="M3 11h7v9H3z" /><path d="M13 11h8v4h-8z" /><path d="M13 17h8v3h-8z" />
  </S>
);
/** Movement planning: a store shipping to a health facility. */
export const IconMovement = (p: P) => (
  <S {...p}>
    <path d="M2 9.5 5.5 6 9 9.5V19H2z" />
    <path d="M11 12.5h7" /><path d="m15.5 10 2.5 2.5-2.5 2.5" />
    <path d="M20.5 9v10" /><path d="M18.5 14h4" />
  </S>
);
/** Resource management: stock sitting on warehouse racking. */
export const IconRacking = (p: P) => (
  <S {...p}>
    <path d="M4 3v18" /><path d="M20 3v18" />
    <path d="M4 9h16" /><path d="M4 15h16" />
    <path d="M6.5 5.5h4v3.5h-4z" /><path d="M13 11.5h4.5V15H13z" />
  </S>
);
/** Service performance: results against a standard. */
export const IconAgainstTarget = (p: P) => (
  <S {...p}>
    <path d="M3 12h18" strokeDasharray="3 2.5" />
    <path d="M6.5 7v10" /><path d="M11 9.5v9" /><path d="M15.5 5.5v9" /><path d="M20 10v7" />
  </S>
);
/** Disease surveillance: case-finding within a population. */
export const IconCaseFinding = (p: P) => (
  <S {...p}>
    <circle cx="10.5" cy="10.5" r="6.5" /><path d="m21 21-5.6-5.6" />
    <circle cx="8" cy="9" r="0.9" /><circle cx="12.5" cy="8" r="0.9" /><circle cx="11" cy="12.5" r="1.7" />
  </S>
);
/** Data export: rows leaving the system. */
export const IconExportRows = (p: P) => (
  <S {...p}>
    <path d="M3 5h11v14H3z" /><path d="M3 9.5h11" /><path d="M3 14h11" />
    <path d="M16.5 12H22" /><path d="m19.5 9 2.5 3-2.5 3" />
  </S>
);
/** Distribution network: one store radiating to the facilities it supplies. */
export const IconHubSpoke = (p: P) => (
  <S {...p}>
    <circle cx="12" cy="12" r="2.6" />
    <path d="m10 10-4.5-3.5M14 10l4.5-3.5M10.5 14.2 7 18.5M13.5 14.2 17 18.5" />
    <circle cx="4.6" cy="5.6" r="1.5" /><circle cx="19.4" cy="5.6" r="1.5" />
    <circle cx="6.2" cy="19.4" r="1.5" /><circle cx="17.8" cy="19.4" r="1.5" />
  </S>
);
/** Flow volumes: two bands of different thickness crossing between two columns. */
export const IconSankey = (p: P) => (
  <S {...p}>
    <path d="M3 4v16" /><path d="M21 4v16" />
    <path d="M3 8h5c5 0 5 8 10 8h3" />
    <path d="M3 16h4c5 0 4-9 9-9h5" />
  </S>
);
