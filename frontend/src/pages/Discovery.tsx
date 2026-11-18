import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Card, Empty, ErrorNote, PageHeader, Stat } from "../components/ui";
import { fmtTime, useApi } from "../hooks";

export default function Discovery() {
  const { can } = useAuth();
  const [showIgnored, setShowIgnored] = useState(false);
  const c = useApi<any>("/discovery/candidates", { include_ignored: showIgnored });
  const [sel, setSel] = useState<Record<string, { platform: string }>>({});
  const [site, setSite] = useState("");
  const [role, setRole] = useState("");
  const [collect, setCollect] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (x: any) => setSel((s) => {
    const n = { ...s };
    if (n[x.name]) delete n[x.name]; else n[x.name] = { platform: x.platform || "" };
    return n;
  });

  async function approve() {
    setErr(null); setMsg(null);
    try {
      const r = await api<any>("/discovery/approve", { method: "POST", body: JSON.stringify({
        candidates: Object.entries(sel).map(([name, v]) => ({ name, platform: v.platform || null })),
        site: site || null, role: role || null, collect, snapshot: c.data?.snapshot,
      }) });
      setMsg(`Added ${r.created.length} device(s)${r.snapshot ? "; collecting them now, their neighbors appear here next" : ""}.` +
        (r.errors.length ? ` Not added: ${r.errors.map((e: any) => `${e.name} (${e.error})`).join("; ")}` : ""));
      setSel({}); c.reload();
    } catch (e: any) { setErr(e.message); }
  }
  async function ignore(names: string[]) {
    await api("/discovery/ignore", { method: "POST", body: JSON.stringify({ names, reason: "ignored in UI" }) });
    setSel({}); c.reload();
  }
  async function unignore(name: string) {
    await api(`/discovery/ignore/${encodeURIComponent(name)}`, { method: "DELETE" });
    c.reload();
  }

  const items: any[] = c.data?.items || [];
  const chosen = Object.keys(sel);
  return (
    <>
      <PageHeader title="Discovery" sub="CDP and LLDP neighbors that are not in inventory yet. Nothing is added without approval." />
      <ErrorNote error={c.error || err} />
      {msg && <div className="alert info">{msg}</div>}
      {c.data && (
        <div className="stats">
          <Stat label="Candidates" value={c.data.total} hint={`from snapshot ${fmtTime(c.data.created_at)}`} />
          <Stat label="Ready to add" value={c.data.ready} hint="supported platform and a management IP" tone={c.data.ready ? "warn" : "good"} />
        </div>
      )}
      <Card title="Candidates" actions={<>
        <label className="inline"><input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} /> Show ignored</label>
      </>}>
        {items.length === 0 ? <Empty>No new neighbors. Every device seen over CDP/LLDP is in inventory or ignored.</Empty> : (
          <div className="tbl-wrap">
            <table className="tbl dense">
              <thead><tr><th></th><th>Name</th><th>Management IP</th><th>Platform</th><th>Seen by</th><th></th></tr></thead>
              <tbody>{items.map((x) => (
                <tr key={x.name} className={sel[x.name] ? "sel" : ""}>
                  <td>{can("admin") && !x.ignored && x.supported && <input type="checkbox" aria-label={`select ${x.name}`} checked={!!sel[x.name]} onChange={() => toggle(x)} />}</td>
                  <td><strong>{x.name}</strong>{x.ignored && <span className="tag">ignored</span>}</td>
                  <td className="mono">{x.host || <span className="muted">not advertised</span>}</td>
                  <td>
                    {sel[x.name] ? (
                      <select value={sel[x.name].platform} onChange={(e) => setSel((s) => ({ ...s, [x.name]: { platform: e.target.value } }))}>
                        <option value="">choose…</option><option value="iosxe">iosxe</option><option value="nxos">nxos</option>
                      </select>
                    ) : x.platform || <span className="muted">{x.supported ? "unknown" : "not supported"}</span>}
                    <div className="muted">{x.platform_text}</div>
                  </td>
                  <td>{x.seen_by.map((s: any, i: number) => <div key={i}><Link to={`/devices/${s.device}`}>{s.device}</Link> <span className="mono muted">{s.interface}</span> <span className="muted">{s.protocol}</span></div>)}</td>
                  <td>{can("admin") && (x.ignored
                    ? <button className="btn sm ghost" onClick={() => unignore(x.name)}>Un-ignore</button>
                    : <button className="btn sm ghost" onClick={() => ignore([x.name])}>Ignore</button>)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {can("admin") && chosen.length > 0 && (
          <div className="form-inline">
            <input placeholder="Site (optional)" value={site} onChange={(e) => setSite(e.target.value)} />
            <input placeholder="Role (optional)" value={role} onChange={(e) => setRole(e.target.value)} />
            <label className="inline"><input type="checkbox" checked={collect} onChange={(e) => setCollect(e.target.checked)} /> Collect them now (next crawl step)</label>
            <button className="btn primary" onClick={approve}>Add {chosen.length} to inventory</button>
            <button className="btn" onClick={() => ignore(chosen)}>Ignore {chosen.length}</button>
          </div>
        )}
      </Card>
    </>
  );
}
