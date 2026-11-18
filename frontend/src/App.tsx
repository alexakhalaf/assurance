import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Compare from "./pages/Compare";
import Dashboard from "./pages/Dashboard";
import DevicePage from "./pages/DevicePage";
import Devices from "./pages/Devices";
import Login from "./pages/Login";
import Settings from "./pages/Settings";
import SnapshotDetail from "./pages/SnapshotDetail";
import Snapshots from "./pages/Snapshots";

const NAV = [
  { to: "/", label: "Dashboard", icon: "◧" },
  { to: "/devices", label: "Devices", icon: "▤" },
  { to: "/snapshots", label: "Snapshots", icon: "◷" },
  { to: "/compare", label: "Compare", icon: "⇄" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

function Shell() {
  const { user, loading, logout } = useAuth();
  if (loading) return <div className="center muted">Loading…</div>;
  if (!user) return <Login />;
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={26} height={26} />
          <span>Assurance</span>
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => (isActive ? "nav active" : "nav")}>
              <span className="nav-i" aria-hidden>{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <div className="who">
            <strong>{user.username}</strong>
            <span className="muted">{user.role}</span>
          </div>
          <button className="btn ghost sm" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/devices/:name" element={<DevicePage />} />
          <Route path="/snapshots" element={<Snapshots />} />
          <Route path="/snapshots/:id" element={<SnapshotDetail />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
