import { FormEvent, lazy, Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import { AuthProvider, useAuth } from "./auth";
import { Icon, Logo } from "./components/Icon";
import { Badge, ErrorNote, Loading, Modal } from "./components/ui";
import { ago, fmtTime, useLocal } from "./hooks";
import Login from "./pages/Login";
import { SnapshotProvider, useSnapshot } from "./snapshot";
import { TABLE_DEFS, TECH_GROUPS } from "./tables";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const Devices = lazy(() => import("./pages/Devices"));
const DevicePage = lazy(() => import("./pages/DevicePage"));
const Discovery = lazy(() => import("./pages/Discovery"));
const Snapshots = lazy(() => import("./pages/Snapshots"));
const SnapshotDetail = lazy(() => import("./pages/SnapshotDetail"));
const Compare = lazy(() => import("./pages/Compare"));
const Checks = lazy(() => import("./pages/Checks"));
const PrePost = lazy(() => import("./pages/PrePost"));
const Topology = lazy(() => import("./pages/Topology"));
const Reports = lazy(() => import("./pages/Reports"));
const Search = lazy(() => import("./pages/Search"));
const Settings = lazy(() => import("./pages/Settings"));
const TechTable = lazy(() => import("./pages/TechTable"));

interface NavItem { to: string; label: string; icon: string; end?: boolean }
const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Overview", items: [
    { to: "/", label: "Dashboard", icon: "dashboard", end: true },
    { to: "/snapshots", label: "Snapshots", icon: "snapshots" },
  ] },
  { group: "Inventory", items: [
    { to: "/devices", label: "Devices", icon: "devices" },
    { to: "/tables/facts", label: "Device facts", icon: "facts" },
    { to: "/tables/hardware", label: "Hardware", icon: "hardware" },
    { to: "/discovery", label: "Discovery", icon: "discovery" },
  ] },
  ...TECH_GROUPS.map((g) => ({
    group: g,
    items: TABLE_DEFS.filter((t) => t.group === g).map((t) => ({ to: `/tables/${t.id}`, label: t.title.replace(" (CDP / LLDP)", ""), icon: t.icon })),
  })),
  { group: "Analysis", items: [
    { to: "/compare", label: "Changes", icon: "compare" },
    { to: "/checks", label: "Intent checks", icon: "checks" },
    { to: "/verify", label: "Change verification", icon: "verify" },
    { to: "/topology", label: "Topology", icon: "topology" },
    { to: "/search", label: "Search", icon: "search" },
    { to: "/reports", label: "Reports", icon: "reports" },
  ] },
  { group: "Administration", items: [{ to: "/settings", label: "Settings", icon: "settings" }] },
];

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useLocal<string>("assurance.theme",
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")];
}

function SnapshotPicker() {
  const { ref, setRef, current, list, running } = useSnapshot();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  return (
    <div className="snap-picker" ref={box}>
      <button className="snap-btn" onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open} title="Snapshot shown in tables, device pages and topology">
        <Icon name="snapshots" size={15} />
        <span className="lbl">Snapshot</span>
        <span className="when">{current ? fmtTime(current.created_at) : "none yet"}</span>
        {ref === "latest" && current && <span className="badge badge-ok" style={{ height: 18 }}>latest</span>}
        {running > 0 && <span className="badge badge-running" style={{ height: 18 }}>{running} running</span>}
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className="snap-menu" role="listbox">
          <div className="head">Show data from</div>
          <div className={`item ${ref === "latest" ? "on" : ""}`} onClick={() => { setRef("latest"); setOpen(false); }}>
            <Icon name="refresh" size={14} />
            <div><strong>Always the latest</strong><div className="sub">Follows new snapshots as they finish</div></div><span />
          </div>
          {list.map((s) => (
            <div key={s.id} className={`item ${ref === s.id ? "on" : ""}`} onClick={() => { setRef(s.id); setOpen(false); }}>
              <Badge status={s.status} label="" />
              <div style={{ minWidth: 0 }}>
                <div>{fmtTime(s.created_at)} <span className="muted">· {ago(s.created_at)}</span></div>
                <div className="sub truncate">{s.device_count} devices{s.labels.length ? ` · ${s.labels.join(", ")}` : ""}</div>
              </div>
              <span className="muted small">{s.summary?.changes ? `${s.summary.changes.total} chg` : ""}</span>
            </div>
          ))}
          {list.length === 0 && <div className="item"><span /><span className="muted">No finished snapshots yet.</span><span /></div>}
        </div>
      )}
    </div>
  );
}

function TopSearch() {
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement).tagName)) { e.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  return (
    <form className="top-search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) { nav(`/search?q=${encodeURIComponent(q.trim())}`); setQ(""); input.current?.blur(); } }}>
      <Icon name="search" size={15} />
      <input ref={input} placeholder="Search IP, MAC, prefix, hostname, serial…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Global search" />
      <kbd>/</kbd>
    </form>
  );
}

function UserMenu({ onPassword }: { onPassword: () => void }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  if (!user) return null;
  return (
    <div className="user-menu" ref={box}>
      <button className="avatar" onClick={() => setOpen(!open)} aria-label="Account menu">{user.username.slice(0, 2).toUpperCase()}</button>
      {open && (
        <div className="menu">
          <div className="who"><strong>{user.username}</strong><div className="muted small">Role: {user.role}</div></div>
          <button onClick={() => { setOpen(false); onPassword(); }}><Icon name="key" size={14} /> Change password</button>
          <a href="/api/docs" target="_blank" rel="noreferrer"><Icon name="external" size={14} /> API reference</a>
          <button onClick={logout}><Icon name="logout" size={14} /> Sign out</button>
        </div>
      )}
    </div>
  );
}

function PasswordDialog({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState({ current: "", next: "", again: "" });
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (f.next !== f.again) { setErr("The new passwords don't match."); return; }
    try {
      await api("/auth/password", { method: "POST", body: JSON.stringify({ current_password: f.current, new_password: f.next }) });
      setDone(true);
    } catch (e: any) { setErr(e.message); }
  }
  return (
    <Modal title="Change password" onClose={onClose}>
      {done ? <div className="form"><div className="alert info">Password changed. Your other sessions were signed out.</div>
        <div className="row end"><button className="btn primary" onClick={onClose}>Done</button></div></div> : (
        <form className="form" onSubmit={submit}>
          <label>Current password<input type="password" autoComplete="current-password" value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} required /></label>
          <label>New password (12+ characters)<input type="password" autoComplete="new-password" minLength={12} value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} required /></label>
          <label>Repeat new password<input type="password" autoComplete="new-password" minLength={12} value={f.again} onChange={(e) => setF({ ...f, again: e.target.value })} required /></label>
          <ErrorNote error={err} />
          <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Change password</button></div>
        </form>
      )}
    </Modal>
  );
}

function Toasts() {
  const [items, setItems] = useState<{ id: number; message: string; tone: string }[]>([]);
  useEffect(() => {
    const h = (e: Event) => {
      const d = (e as CustomEvent).detail;
      const id = Date.now() + Math.random();
      setItems((x) => [...x, { id, ...d }]);
      window.setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 6000);
    };
    window.addEventListener("assurance:toast", h);
    return () => window.removeEventListener("assurance:toast", h);
  }, []);
  return (
    <div style={{ position: "fixed", right: 16, bottom: 16, display: "flex", flexDirection: "column", gap: 8, zIndex: 200 }}>
      {items.map((t) => <div key={t.id} className={`alert ${t.tone === "bad" ? "" : "info"}`} style={{ boxShadow: "var(--shadow-lg)", minWidth: 280 }}>{t.message}</div>)}
    </div>
  );
}

function Shell() {
  const { user, loading } = useAuth();
  const [collapsed, setCollapsed] = useLocal<boolean>("assurance.nav.collapsed", false);
  const [closed, setClosed] = useLocal<string[]>("assurance.nav.closed", []);
  const [navOpen, setNavOpen] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const [pw, setPw] = useState(false);
  const loc = useLocation();
  useEffect(() => { setNavOpen(false); }, [loc.pathname]);
  if (loading) return <div className="center muted">Loading…</div>;
  if (!user) return <Login />;
  const toggleGroup = (g: string) => setClosed(closed.includes(g) ? closed.filter((x) => x !== g) : [...closed, g]);
  return (
    <SnapshotProvider>
      <div className={`shell ${collapsed ? "collapsed" : ""} ${navOpen ? "nav-open" : ""}`}>
        <header className="topbar">
          <div className="brand">
            <button className="icon-btn" onClick={() => (window.innerWidth < 860 ? setNavOpen(!navOpen) : setCollapsed(!collapsed))} aria-label="Toggle navigation"><Icon name="menu" size={18} /></button>
            <Logo />
            <span>Assurance</span>
          </div>
          <SnapshotPicker />
          <div className="top-spacer" />
          <TopSearch />
          <button className="icon-btn" onClick={toggleTheme} aria-label="Toggle dark mode" title={theme === "dark" ? "Light mode" : "Dark mode"}>
            <Icon name={theme === "dark" ? "sun" : "moon"} size={17} />
          </button>
          <UserMenu onPassword={() => setPw(true)} />
        </header>
        <nav className="sidenav" aria-label="Main">
          {NAV.map((g) => {
            const isClosed = closed.includes(g.group) && !collapsed;
            return (
              <div className="nav-group" key={g.group}>
                <button className={`nav-group-h ${isClosed ? "closed" : ""}`} onClick={() => toggleGroup(g.group)} aria-expanded={!isClosed}>
                  {g.group}<Icon name="chevron" size={12} />
                </button>
                {!isClosed && g.items.map((n) => (
                  <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "nav active" : "nav")} title={n.label}>
                    <Icon name={n.icon} size={16} /><span>{n.label}</span>
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <main className="main">
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/devices" element={<Devices />} />
              <Route path="/devices/:name" element={<DevicePage />} />
              <Route path="/tables/:table" element={<TechTable />} />
              <Route path="/snapshots" element={<Snapshots />} />
              <Route path="/snapshots/:id" element={<SnapshotDetail />} />
              <Route path="/compare" element={<Compare />} />
              <Route path="/checks" element={<Checks />} />
              <Route path="/verify" element={<PrePost />} />
              <Route path="/topology" element={<Topology />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/search" element={<Search />} />
              <Route path="/discovery" element={<Discovery />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/settings/:tab" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </main>
        {pw && <PasswordDialog onClose={() => setPw(false)} />}
        <Toasts />
      </div>
    </SnapshotProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
