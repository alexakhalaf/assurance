import { FormEvent, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Card, Empty, ErrorNote, PageHeader, UTabs } from "../components/ui";
import { Col, DataGrid } from "../components/DataGrid";
import { ago, fmtTime, useApi } from "../hooks";

const TABS = [
  { id: "credentials", label: "Device credentials", admin: true },
  { id: "schedules", label: "Collection schedules", admin: false },
  { id: "tokens", label: "API tokens", admin: false },
  { id: "users", label: "Users", admin: true },
  { id: "audit", label: "Audit log", admin: true },
];

export default function Settings() {
  const { can } = useAuth();
  const { tab: t } = useParams();
  const nav = useNavigate();
  const tabs = TABS.filter((x) => !x.admin || can("admin"));
  const tab = tabs.find((x) => x.id === t)?.id || tabs[0].id;
  return (
    <div className="page">
      <PageHeader title="Settings" crumbs={[["Administration"]]} sub="Credentials, schedules, access and the audit trail" />
      <section className="card">
        <UTabs value={tab} onChange={(v) => nav(`/settings/${v}`)} tabs={tabs.map((x) => ({ id: x.id, label: x.label }))} />
        <div className="settings-body">
          {tab === "credentials" && <Credentials />}
          {tab === "schedules" && <Schedules />}
          {tab === "tokens" && <Tokens />}
          {tab === "users" && <Users />}
          {tab === "audit" && <Audit />}
        </div>
      </section>
    </div>
  );
}

function useForm<T extends Record<string, any>>(init: T) {
  const [f, setF] = useState(init);
  const set = (k: keyof T) => (e: any) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return { f, set, reset: () => setF(init) };
}

function Credentials() {
  const list = useApi<any[]>("/credentials");
  const { f, set, reset } = useForm({ name: "default", username: "", password: "", enable_password: "", description: "" });
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/credentials", { method: "POST", body: JSON.stringify({ ...f, enable_password: f.enable_password || null }) });
      reset();
      setErr(null);
      list.reload();
    } catch (e: any) { setErr(e.message); }
  }
  async function remove(name: string) {
    if (confirm(`Delete credential profile ${name}?`)) { await api(`/credentials/${name}`, { method: "DELETE" }); list.reload(); }
  }
  return (
    <Card title="Device credentials">
      <p className="muted">Encrypted at rest with the server's master key and never shown again. Resolution order: device, then its site, then the profile named <code>default</code>. Use a read-only account limited to show commands.</p>
      {list.data?.length ? (
        <table className="tbl"><thead><tr><th>Name</th><th>Username</th><th>Enable secret</th><th>Updated</th><th></th></tr></thead>
          <tbody>{list.data.map((c) => <tr key={c.name}><td>{c.name}</td><td>{c.username}</td><td>{c.has_enable_password ? "set" : "—"}</td><td>{ago(c.updated_at)}</td>
            <td><button className="btn sm ghost" onClick={() => remove(c.name)}>Delete</button></td></tr>)}</tbody></table>
      ) : <Empty>No credential profiles. Live collection needs at least one.</Empty>}
      <form className="form-inline" onSubmit={submit}>
        <input placeholder="Profile name" value={f.name} onChange={set("name")} required />
        <input placeholder="Username" value={f.username} onChange={set("username")} required autoComplete="off" />
        <input type="password" placeholder="Password" value={f.password} onChange={set("password")} required autoComplete="new-password" />
        <input type="password" placeholder="Enable secret (optional)" value={f.enable_password} onChange={set("enable_password")} autoComplete="new-password" />
        <button className="btn primary">Add profile</button>
      </form>
      <ErrorNote error={err} />
    </Card>
  );
}

function Schedules() {
  const { can } = useAuth();
  const list = useApi<any[]>("/schedules");
  const { f, set, reset } = useForm({ name: "", cron: "0 * * * *", site: "", labels: "" });
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/schedules", { method: "POST", body: JSON.stringify({ name: f.name, cron: f.cron, scope: f.site ? { site: f.site } : {},
        labels: f.labels.split(",").map((x) => x.trim()).filter(Boolean) }) });
      reset(); setErr(null); list.reload();
    } catch (e: any) { setErr(e.message); }
  }
  async function toggle(s: any) { await api(`/schedules/${s.name}`, { method: "PATCH", body: JSON.stringify({ enabled: !s.enabled }) }); list.reload(); }
  async function remove(s: any) { if (confirm(`Delete schedule ${s.name}?`)) { await api(`/schedules/${s.name}`, { method: "DELETE" }); list.reload(); } }
  return (
    <Card title="Collection schedules">
      {list.data?.length ? (
        <table className="tbl"><thead><tr><th>Name</th><th>Cron (UTC)</th><th>Scope</th><th>Next run</th><th>Last run</th><th>Enabled</th><th></th></tr></thead>
          <tbody>{list.data.map((s) => <tr key={s.name}><td>{s.name}</td><td className="mono">{s.cron}</td><td>{Object.entries(s.scope).map(([k, v]) => `${k}=${v}`).join(" ") || "all devices"}</td>
            <td>{s.enabled ? fmtTime(s.next_run_at) : "—"}</td><td>{ago(s.last_run_at)}</td>
            <td>{can("admin") ? <input type="checkbox" checked={s.enabled} onChange={() => toggle(s)} /> : s.enabled ? "yes" : "no"}</td>
            <td>{can("admin") && <button className="btn sm ghost" onClick={() => remove(s)}>Delete</button>}</td></tr>)}</tbody></table>
      ) : <Empty>No schedules. Snapshots run only on demand.</Empty>}
      {can("admin") && (
        <form className="form-inline" onSubmit={submit}>
          <input placeholder="Name, e.g. hourly-fleet" value={f.name} onChange={set("name")} required />
          <input placeholder="Cron" value={f.cron} onChange={set("cron")} required className="mono" />
          <input placeholder="Site (optional)" value={f.site} onChange={set("site")} />
          <input placeholder="Labels (optional)" value={f.labels} onChange={set("labels")} />
          <button className="btn primary">Add schedule</button>
        </form>
      )}
      <ErrorNote error={err} />
    </Card>
  );
}

function Tokens() {
  const list = useApi<any[]>("/auth/tokens");
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const t = await api<any>("/auth/tokens", { method: "POST", body: JSON.stringify({ name, expires_in_days: 90 }) });
    setCreated(t.token); setName(""); list.reload();
  }
  async function revoke(id: number) { await api(`/auth/tokens/${id}`, { method: "DELETE" }); list.reload(); }
  return (
    <Card title="API tokens">
      <p className="muted">For scripts, pipelines and the CLI. Send as <code>Authorization: Bearer &lt;token&gt;</code>. API reference: <a href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a>.</p>
      {created && <div className="alert info">Copy this token now, it will not be shown again: <code className="select">{created}</code></div>}
      {list.data?.length ? (
        <table className="tbl"><thead><tr><th>Name</th><th>Prefix</th><th>Expires</th><th>Last used</th><th></th></tr></thead>
          <tbody>{list.data.map((t) => <tr key={t.id}><td>{t.name}</td><td className="mono">{t.prefix}…</td><td>{fmtTime(t.expires_at)}</td><td>{ago(t.last_used_at)}</td>
            <td><button className="btn sm ghost" onClick={() => revoke(t.id)}>Revoke</button></td></tr>)}</tbody></table>
      ) : <Empty>No API tokens.</Empty>}
      <form className="form-inline" onSubmit={submit}>
        <input placeholder="Token name, e.g. change-pipeline" value={name} onChange={(e) => setName(e.target.value)} required />
        <button className="btn primary">Create token</button>
      </form>
    </Card>
  );
}

function Users() {
  const list = useApi<any[]>("/users");
  const { f, set, reset } = useForm({ username: "", password: "", role: "viewer" });
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try { await api("/users", { method: "POST", body: JSON.stringify(f) }); reset(); setErr(null); list.reload(); } catch (e: any) { setErr(e.message); }
  }
  async function patch(u: any, body: any) {
    try { await api(`/users/${u.username}`, { method: "PATCH", body: JSON.stringify(body) }); list.reload(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <Card title="Users">
      <table className="tbl"><thead><tr><th>User</th><th>Role</th><th>Active</th><th>Last login</th></tr></thead>
        <tbody>{list.data?.map((u) => <tr key={u.id}><td>{u.username}</td>
          <td><select value={u.role} onChange={(e) => patch(u, { role: e.target.value })}><option>viewer</option><option>operator</option><option>admin</option></select></td>
          <td><input type="checkbox" checked={u.is_active} onChange={() => patch(u, { is_active: !u.is_active })} /></td><td>{ago(u.last_login_at)}</td></tr>)}</tbody></table>
      <form className="form-inline" onSubmit={submit}>
        <input placeholder="Username" value={f.username} onChange={set("username")} required />
        <input type="password" placeholder="Password (12+ characters)" value={f.password} onChange={set("password")} required minLength={12} autoComplete="new-password" />
        <select value={f.role} onChange={set("role")}><option>viewer</option><option>operator</option><option>admin</option></select>
        <button className="btn primary">Add user</button>
      </form>
      <ErrorNote error={err} />
    </Card>
  );
}

function Audit() {
  const { data, loading, error } = useApi<any>("/audit", { limit: 1000 });
  const columns: Col[] = [
    { key: "at", label: "When", render: (a) => fmtTime(a.at), text: (a) => fmtTime(a.at), sortValue: (a) => a.at },
    { key: "actor", label: "Who" },
    { key: "action", label: "Action", mono: true },
    { key: "target", label: "Target" },
    { key: "detail", label: "Detail", wrap: true, sortable: false, text: (a) => (a.detail ? JSON.stringify(a.detail) : ""), render: (a) => a.detail ? <span className="mono small">{JSON.stringify(a.detail)}</span> : <span className="faint">—</span> },
    { key: "ip", label: "From", mono: true },
  ];
  return (
    <Card title="Audit log" sub="latest 1,000 events">
      <DataGrid storageKey="audit" columns={columns} rows={data?.items || []} loading={loading} error={error} rowKey={(a) => String(a.id)} autoHeight />
    </Card>
  );
}
