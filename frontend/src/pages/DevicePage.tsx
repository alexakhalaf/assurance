import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, Change, Collection, Device, download, Snapshot, toast } from "../api";
import { useAuth } from "../auth";
import { DataGrid, tableQuery } from "../components/DataGrid";
import { Icon } from "../components/Icon";
import { Badge, Card, EmptyState, ErrorNote, Loading, PageHeader, Stat, UTabs } from "../components/ui";
import { ago, duration, fmtTime, speed, useApi } from "../hooks";
import { useSnapId, useSnapshot } from "../snapshot";
import { TABLE_BY_ID, TABLE_DEFS } from "../tables";
import { ViolationTable } from "./Checks";
import { ChangeList } from "./Compare";
import { DeviceForm } from "./Devices";

const TECH = TABLE_DEFS.filter((t) => t.id !== "facts");

export default function DevicePage() {
  const { name = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "overview";
  const setTab = (t: string) => setSp(t === "overview" ? {} : { tab: t }, { replace: true });
  const { can } = useAuth();
  const nav = useNavigate();
  const snap = useSnapId();
  const { current } = useSnapshot();
  const dev = useApi<Device>(`/devices/${encodeURIComponent(name)}`);
  const snapDetail = useApi<Snapshot & { collections: Collection[] }>(`/snapshots/${snap}`);
  const col = snapDetail.data?.collections.find((c) => c.device_name === name);
  const ifaces = useApi<any>(`/snapshots/${snap}/tables/interfaces`, { device: [name], limit: 2000 });
  const bgp = useApi<any>(`/snapshots/${snap}/tables/bgp`, { device: [name], limit: 2000 });
  const checks = useApi<any>(`/snapshots/${snap}/checks`, { device: name });
  const [editing, setEditing] = useState(false);
  const d = dev.data;

  async function remove() {
    if (!confirm(`Remove ${name} from inventory? Its history in past snapshots is kept.`)) return;
    await api(`/devices/${encodeURIComponent(name)}`, { method: "DELETE" });
    toast(`${name} removed from inventory`);
    nav("/devices");
  }
  async function resetKey() {
    if (!confirm(`Forget the pinned SSH host key for ${name}? The next collection pins whatever key the device presents, so check the new fingerprint afterwards.`)) return;
    await api(`/devices/${encodeURIComponent(name)}/host-key`, { method: "DELETE" });
    dev.reload();
  }
  async function collectNow() {
    try {
      const s = await api<Snapshot>("/snapshots", { method: "POST", body: JSON.stringify({ scope: { devices: [name] }, labels: [] }) });
      toast(`Collecting ${name}…`);
      nav(`/snapshots/${s.id}`);
    } catch (e: any) { toast(e.message, "bad"); }
  }

  if (dev.error) return <div className="page"><ErrorNote error={dev.error} /></div>;
  if (!d) return <Loading />;
  const rows = col?.row_counts || {};
  const ifs: any[] = ifaces.data?.items || [];
  const up = ifs.filter((i) => i.oper_status === "up").length;
  const adminUpDown = ifs.filter((i) => i.admin_status === "up" && i.oper_status !== "up").length;
  const sessions: any[] = bgp.data?.items || [];
  const est = sessions.filter((s) => s.state === "established").length;
  const viol = checks.data?.violations || [];
  const fails = viol.filter((v: any) => v.severity === "fail").length;

  return (
    <div className="page">
      <PageHeader crumbs={[["Devices", "/devices"], [d.name]]}
        title={<span className="row gap">{d.name} <Badge status={d.status} /> {d.last_collection_status && <Badge status={d.last_collection_status} label={`collection ${d.last_collection_status}`} />}</span>}
        sub={<>{d.platform === "iosxe" ? "Cisco IOS-XE" : "Cisco NX-OS"} · {d.model || "model unknown"} · <span className="mono">{d.host}</span>{d.site ? ` · ${d.site}` : ""}{d.role ? ` · ${d.role}` : ""}</>}
        actions={<>
          {can("operator") && <button className="btn" onClick={collectNow}><Icon name="play" size={14} /> Collect now</button>}
          <Link className="btn" to={`/compare?device=${encodeURIComponent(name)}`}><Icon name="compare" size={14} /> Changes</Link>
          {can("admin") && <button className="btn" onClick={() => setEditing(true)}><Icon name="edit" size={14} /> Edit</button>}
          {can("admin") && <button className="btn danger" onClick={remove}><Icon name="trash" size={14} /> Remove</button>}
        </>} />

      {current && !col && <div className="alert warn">{name} was not part of the selected snapshot ({fmtTime(current.created_at)}). Pick another snapshot in the top bar.</div>}
      {col?.status === "failed" && <div className="alert">Collection failed in this snapshot: {col.error}</div>}

      <div className="stats">
        <Stat label="Interfaces up" value={ifs.length ? `${up} / ${ifs.length}` : "—"} hint={adminUpDown ? `${adminUpDown} enabled but down` : "none enabled and down"} tone={ifs.length ? (adminUpDown ? "warn" : "good") : undefined} />
        <Stat label="Routes" value={(rows.routes ?? 0).toLocaleString()} hint={`${rows.arp ?? 0} ARP · ${rows.mac ?? 0} MAC`} />
        <Stat label="BGP sessions" value={sessions.length ? `${est} / ${sessions.length}` : "—"} hint={sessions.length ? "established" : "no BGP"} tone={sessions.length ? (est < sessions.length ? "bad" : "good") : undefined} />
        <Stat label="Neighbors" value={rows.neighbors ?? 0} hint="CDP and LLDP" />
        <Stat label="Check violations" value={viol.length} tone={viol.length ? (fails ? "bad" : "warn") : "good"} hint={`${fails} fail · ${viol.length - fails} warn`} />
        <Stat label="Collected in" value={duration(col?.duration_ms)} hint={col?.warnings?.length ? `${col.warnings.length} parser warning(s)` : current ? fmtTime(current.created_at) : "—"} />
      </div>

      <section className="card">
        <UTabs value={tab} onChange={setTab} tabs={[
          { id: "overview", label: "Overview" },
          ...TECH.filter((t) => t.id !== "config").map((t) => ({ id: t.id, label: t.title.replace(" (CDP / LLDP)", "").replace("Routing table", "Routes").replace(" neighbors", ""), n: rows[t.id] ?? 0 })),
          { id: "history", label: "History" },
          { id: "config", label: "Configuration" },
        ]} />
        {tab === "overview" && <Overview d={d} col={col} viol={viol} onResetKey={can("admin") ? resetKey : undefined} ifs={ifs} />}
        {tab === "history" && <History name={name} />}
        {tab === "config" && <Config name={name} snap={snap} />}
        {TABLE_BY_ID[tab] && tab !== "config" && <DeviceTable table={tab} device={name} snap={snap} />}
      </section>
      {editing && <DeviceForm device={d} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); dev.reload(); }} />}
    </div>
  );
}

function Overview({ d, col, viol, ifs, onResetKey }: { d: Device; col?: Collection; viol: any[]; ifs: any[]; onResetKey?: () => void }) {
  const hist = useApi<any>(`/devices/${encodeURIComponent(d.name)}/history`, { since: "7d", limit: 20 });
  const down = ifs.filter((i) => i.admin_status === "up" && i.oper_status !== "up");
  return (
    <div className="grid-12" style={{ padding: 16 }}>
      <Card className="col-5" title="Device">
        <dl className="facts" style={{ padding: "4px 16px 8px" }}>
          <dt>Hostname</dt><dd>{d.hostname || "—"}</dd>
          <dt>Model</dt><dd>{d.model || "—"}</dd>
          <dt>Serial</dt><dd className="mono">{d.serial || "—"}</dd>
          <dt>Software</dt><dd className="mono">{d.os_version || "—"}</dd>
          <dt>Management</dt><dd className="mono">{d.host}:{d.port}</dd>
          <dt>Site / role</dt><dd>{d.site || "—"} / {d.role || "—"}</dd>
          <dt>Tags</dt><dd>{d.tags.length ? d.tags.map((t) => <span className="tag" key={t}>{t}</span>) : "—"}</dd>
          <dt>Credential</dt><dd>{d.credential || "inherited (site, then default)"}</dd>
          <dt>Last collection</dt><dd>{d.last_collection_status ? <><Badge status={d.last_collection_status} /> {ago(d.last_collected_at)}</> : "never"}</dd>
          <dt>SSH host key</dt><dd>{d.ssh_host_key_fingerprint
            ? <><span className="mono small">{d.ssh_host_key_type} {d.ssh_host_key_fingerprint}</span>
              <div className="muted small">pinned {fmtTime(d.ssh_host_key_pinned_at)}{onResetKey && <> · <a href="#" onClick={(e) => { e.preventDefault(); onResetKey(); }}>reset pin</a></>}</div></>
            : <span className="muted">not pinned yet (pinned on first live collection)</span>}</dd>
        </dl>
      </Card>
      <div className="col-7" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <Card title="Intent checks" sub={`${viol.length} violation(s)`} actions={<Link className="btn sm ghost" to="/checks">All rules</Link>}>
          {viol.length ? <ViolationTable rows={viol} hideDevice /> : <EmptyState icon="checks" title="All checks pass" />}
        </Card>
        <Card title="Enabled interfaces that are down" sub={`${down.length}`}>
          {down.length ? (
            <table className="tbl dense"><thead><tr><th>Interface</th><th>Description</th><th className="num">Speed</th></tr></thead>
              <tbody>{down.map((i) => <tr key={i.key}><td className="mono">{i.key}</td><td>{i.description || "—"}</td><td className="num">{speed(i.speed_mbps)}</td></tr>)}</tbody></table>
          ) : <EmptyState icon="interfaces" title="None" />}
        </Card>
      </div>
      <Card className="col-12" title="Changes in the last 7 days" actions={<span className="muted small">{hist.data ? `${hist.data.snapshots} snapshot(s)` : ""}</span>}>
        {hist.data?.events?.length ? (
          <ol className="timeline" style={{ padding: 16 }}>
            {hist.data.events.map((ev: any) => (
              <li key={ev.to.id}>
                <Link to={`/compare?from=${ev.from.id}&to=${ev.to.id}&device=${encodeURIComponent(d.name)}`}>{fmtTime(ev.to.created_at)}</Link>
                <span className="muted"> · {Object.entries(ev.summary).map(([t, c]: any) => `${t} ${c.added + c.removed + c.changed}`).join(" · ")}</span>
              </li>
            ))}
          </ol>
        ) : <EmptyState icon="compare" title="No changes">No changes to this device in the last 7 days.</EmptyState>}
      </Card>
      {col?.warnings?.length ? (
        <Card className="col-12" title="Parser warnings"><ul style={{ margin: 16 }}>{col.warnings.map((w, i) => <li key={i} className="mono small">{w}</li>)}</ul></Card>
      ) : null}
    </div>
  );
}

function DeviceTable({ table, device, snap }: { table: string; device: string; snap: string }) {
  const def = TABLE_BY_ID[table];
  const columns = useMemo(() => def.columns.filter((c) => c.key !== "device"), [def]);
  const source = useMemo(() => ({ path: `/snapshots/${snap}/tables/${table}`, params: { device: [device] }, map: (q: any) => tableQuery(columns, q) }), [snap, table, device, columns]);
  return (
    <DataGrid key={table} storageKey={`device.${table}`} columns={columns} source={source} rowKey={(r) => r.key} autoHeight pageSize={100}
      onExport={(q) => download(`/snapshots/${snap}/tables/${table}`, { ...tableQuery(columns, q), device: [device], format: "csv" })} />
  );
}

function History({ name }: { name: string }) {
  const [since, setSince] = useState("7d");
  const hist = useApi<any>(`/devices/${encodeURIComponent(name)}/history`, { since });
  const [sel, setSel] = useState(0);
  const ev = hist.data?.events?.[sel];
  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="row gap">
        <span className="muted">Window</span>
        <div className="seg">{[["24h", "24 h"], ["7d", "7 days"], ["30d", "30 days"], ["all", "All"]].map(([v, l]) => (
          <button key={v} className={`tab ${since === v ? "active" : ""}`} onClick={() => { setSince(v); setSel(0); }}>{l}</button>))}</div>
        <span className="muted small">{hist.data ? `${hist.data.events.length} change event(s) across ${hist.data.snapshots} snapshot(s)` : ""}</span>
      </div>
      {hist.data?.events?.length ? (
        <div className="compare-layout">
          <aside className="card" style={{ padding: 6 }}>
            {hist.data.events.map((e: any, i: number) => (
              <button key={e.to.id} className={`facet ${i === sel ? "on" : ""}`} onClick={() => setSel(i)}>
                <span>{fmtTime(e.to.created_at)}</span><span className="n">{e.changes.length}</span>
              </button>
            ))}
          </aside>
          <section className="card">
            <header className="card-h"><h2>{fmtTime(ev.from.created_at)} → {fmtTime(ev.to.created_at)}</h2>
              <Link className="btn sm" to={`/compare?from=${ev.from.id}&to=${ev.to.id}&device=${encodeURIComponent(name)}`}>Open in Changes</Link></header>
            <ChangeList changes={ev.changes as Change[]} hideDevice />
          </section>
        </div>
      ) : <EmptyState icon="compare" title="No changes in this window" />}
    </div>
  );
}

function Config({ name, snap }: { name: string; snap: string }) {
  const config = useApi<string>(`/devices/${encodeURIComponent(name)}/config`, { snapshot: snap });
  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="row gap between wrap">
        <span className="muted">Running configuration with secrets masked before storage.</span>
        <div className="row gap">
          <Link className="btn sm" to={`/compare?device=${encodeURIComponent(name)}&table=config`}>Config changes</Link>
          <button className="btn sm" onClick={() => { const b = new Blob([config.data || ""], { type: "text/plain" }); const u = URL.createObjectURL(b);
            const a = document.createElement("a"); a.href = u; a.download = `${name}-running-config.txt`; a.click(); URL.revokeObjectURL(u); }}>
            <Icon name="download" size={14} /> Download</button>
        </div>
      </div>
      {config.error ? <EmptyState icon="config" title="No configuration">{config.error}</EmptyState> : <pre className="cfg cfg-full">{config.data ?? "Loading…"}</pre>}
    </div>
  );
}
