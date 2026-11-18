// Line icons drawn for Assurance on a 24px grid (stroke = currentColor).
const P: Record<string, string> = {
  dashboard: "M3 3h8v10H3zM13 3h8v6h-8zM13 11h8v10h-8zM3 15h8v6H3z",
  devices: "M3 5h18v6H3zM3 13h18v6H3zM7 8h.01M7 16h.01M11 8h6M11 16h6",
  discovery: "M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M12 12m-6 0a6 6 0 1 0 12 0a6 6 0 1 0-12 0M12 12m-10 0a10 10 0 1 0 20 0a10 10 0 1 0-20 0",
  snapshots: "M4 7h3l2-3h6l2 3h3v13H4zM12 17a4 4 0 1 0 0-8a4 4 0 0 0 0 8z",
  compare: "M7 4v13M7 17l-3-3M7 17l3-3M17 20V7M17 7l-3 3M17 7l3 3",
  checks: "M12 3l8 3v6c0 5-3.5 8-8 9c-4.5-1-8-4-8-9V6zM8.5 12l2.5 2.5L15.5 10",
  verify: "M9 11l3 3l8-8M20 12v7a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h9",
  topology: "M12 5m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M5 19m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M19 19m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M11 6.7L6 17.3M13 6.7l5 10.6M7 19h10",
  reports: "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7M9 9h2",
  settings: "M12 15a3 3 0 1 0 0-6a3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3a1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5a1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8a1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  search: "M11 18a7 7 0 1 0 0-14a7 7 0 0 0 0 14zM21 21l-5-5",
  interfaces: "M4 9h16v8H4zM7 9V6h10v3M8 13h.01M11 13h.01M14 13h.01M17 13h.01",
  routes: "M4 19c4 0 4-14 8-14s4 14 8 14M4 5h3M17 19h3",
  arp: "M4 6h16M4 12h16M4 18h10M17 16l3 2l-3 2",
  mac: "M5 4h14v16H5zM9 8h6M9 12h6M9 16h3",
  neighbors: "M6 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M18 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M9 12h6",
  bgp: "M5 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M19 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M12 18m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M7 6h10M6 8l5 8M18 8l-5 8",
  ospf: "M12 3l8 5v8l-8 5l-8-5V8zM12 3v18M4 8l16 8M20 8L4 16",
  vlans: "M3 6h18M3 12h18M3 18h18M8 4v4M14 10v4M10 16v4",
  vrfs: "M4 4h7v7H4zM13 13h7v7h-7zM13 4h7v7h-7zM4 13h7v7H4z",
  hardware: "M6 6h12v12H6zM9 9h6v6H9zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4",
  portchannels: "M3 8h6M3 12h6M3 16h6M15 8h6M15 12h6M15 16h6M9 6h6v12H9z",
  stp: "M12 3v6M12 9l-6 6M12 9l6 6M6 15v6M18 15v6M12 9m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  fhrp: "M12 4l7 4v8l-7 4l-7-4V8zM9 12l2 2l4-4",
  ntp: "M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18zM12 7v5l3 2",
  management: "M4 6h16v12H4zM8 10l2 2l-2 2M12 14h4",
  config: "M8 4L4 12l4 8M16 4l4 8l-4 8M13 4l-2 16",
  facts: "M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18zM12 11v6M12 7.5h.01",
  chevron: "M6 9l6 6l6-6",
  chevronRight: "M9 6l6 6l-6 6",
  menu: "M4 6h16M4 12h16M4 18h16",
  sun: "M12 17a5 5 0 1 0 0-10a5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  plus: "M12 5v14M5 12h14",
  download: "M12 4v12M7 11l5 5l5-5M5 20h14",
  upload: "M12 20V8M7 13l5-5l5 5M5 4h14",
  columns: "M4 4h16v16H4zM10 4v16M15 4v16",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  x: "M6 6l12 12M18 6L6 18",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  play: "M7 5l12 7l-12 7z",
  clock: "M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18zM12 7v5h4",
  logout: "M15 4h4v16h-4M10 8l-4 4l4 4M6 12h10",
  key: "M15 9m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0M11.5 12.5L4 20M7 17l2 2",
  user: "M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8zM4 21c1-4 4-6 8-6s7 2 8 6",
  filter: "M4 5h16l-6 8v6l-4-2v-4z",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  logo: "M12 2.5l8.5 4.9v9.2L12 21.5l-8.5-4.9V7.4zM12 7v10M7.5 9.6l9 4.8M16.5 9.6l-9 4.8",
};

export function Icon({ name, size = 16, className, strokeWidth = 1.75 }: { name: string; size?: number; className?: string; strokeWidth?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d={P[name] || P.facts} />
    </svg>
  );
}

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#4c8dff" /><stop offset="1" stopColor="#22c3a6" />
        </linearGradient>
      </defs>
      <path d="M12 1.8l9 5.2v10L12 22.2L3 17V7z" fill="url(#lg)" />
      <path d="M12 6.6v10.8M7.4 9.3l9.2 5.4M16.6 9.3l-9.2 5.4" stroke="#0d1524" strokeWidth="1.7" strokeLinecap="round" fill="none" opacity="0.85" />
      <circle cx="12" cy="12" r="2.1" fill="#0d1524" />
    </svg>
  );
}
