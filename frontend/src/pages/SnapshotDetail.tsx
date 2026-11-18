import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Collection, download, Snapshot, TABLES } from "../api";
import { Badge, Card, Empty, ErrorNote, Modal, PageHeader, Stat } from "../components/ui";
import { fmtTime, short, useApi } from "../hooks";

export default function SnapshotDetail() {
  const { id = "" } = useParams();
  const { data: s, error } = useApi<Snapshot & { collections: Collection[] }>(`/snapshots/${id}`, undefined,
    (d) => ["queued", "running", "finalizing"].includes(d.status));
  const [tab, setTab] = useState("interfaces");
  const [raw, setRaw] = useState<string | null>(null);

  if (error) return <ErrorNote error={error} />;
  if (!s) return <div className="muted">Loading…</div>;
  const done = s.collections.filter((c) => !["pending", "running"].includes(c.status)).length;
  const ch = s.summary?.changes;
  const diffable = ["complete", "partial"].includes(s.status);

  return (
    <>
      <PageHeader title={`Snapshot ${fmtTime(s.created_at)}`}
        sub={<><Link to="/snapshots">Snapshots</Link> · <span className="mono">{s.id}</span> · {s.trigger}{s.requested_by ? ` by ${s.requested_by}` : ""}</>}
        actions={ch && <Link className="btn" to={`/compare?from=${ch.previous}&to=${s.id}`}>Diff vs previous</Link>} />
      <div className="stats">
        <Stat label="Status" value={<Badge status={s.status} />} hint={`${done} / ${s.device_count} devices finished`} />
        <Stat label="Duration" value={s.finished_at && s.started_at ? `${((+new Date(s.finished_at) - +new Date(s.started_at)) / 1000).toFixed(1)} s` : "…"} />
        <Stat label="Rows collected" value={Object.values(s.summary?.rows || {}).reduce((a: number, b: any) => a + b, 0)} />
        <Stat label="Changes vs previous" value={ch?.total ?? "—"} tone={ch?.total ? "warn" : undefined} />
      </div>
      {s.labels.length > 0 && <div className="row gap">{s.labels.map((l) => <span className="tag" key={l}>{l}</span>)}</div>}

      <Card title="Devices">
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Device</th><th>Platform</th><th>Status</th><th>Time</th><th>Rows</th><th>Notes</th><th></th></tr></thead>
            <tbody>
              {s.collections.map((c) => (
                <tr key={c.device_name}>
                  <td><Link to={`/devices/${c.device_name}`}>{c.device_name}</Link></td>
                  <td>{c.device_meta.platform}</td>
                  <td><Badge status={c.status} /></td>
                  <td>{c.duration_ms != null ? `${(c.duration_ms / 1000).toFixed(1)} s` : "…"}</td>
                  <td>{Object.values(c.row_counts).reduce((a, b) => a + b, 0) || "—"}</td>
                  <td className={c.error ? "bad" : "muted"}>{c.error || (c.warnings.length ? `${c.warnings.length} parser warning(s): ${c.warnings[0]}` : "")}</td>
                  <td>{diffable && c.status !== "failed" && <button className="btn sm ghost" onClick={() => setRaw(c.device_name)}>Raw output</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {diffable && (
        <Card title="State tables" actions={<div className="tabs">{TABLES.map((t) => <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>{t} <span className="muted">{s.summary?.rows?.[t] ?? 0}</span></button>)}</div>}>
          <StateTable snapshot={s.id} table={tab} />
        </Card>
      )}
      {raw && <RawViewer snapshot={s.id} device={raw} onClose={() => setRaw(null)} />}
    </>
  );
}

const COLS: Record<string, string[]> = {
  facts: ["hostname", "model", "serial", "os_version", "image"],
  interfaces: ["admin_status", "oper_status", "description", "ipv4", "speed", "duplex", "mtu"],
  routes: ["vrf", "prefix", "protocol", "distance", "metric", "next_hops"],
  arp: ["vrf", "ip", "mac", "interface"],
  neighbors: ["protocol", "local_interface", "remote_device", "remote_interface", "remote_platform", "mgmt_ip"],
  bgp: ["vrf", "neighbor", "address_family", "remote_as", "state", "prefixes_received"],
  ospf: ["vrf", "interface", "neighbor_id", "address", "state"],
  mac: ["vlan", "mac", "type", "interfaces"],
  vlans: ["vlan_id", "name", "state", "interfaces"],
  vrfs: ["name", "rd", "state", "interfaces"],
  hardware: ["name", "description", "pid", "serial"],
  portchannels: ["protocol", "oper_status", "layer", "members_bundled", "members_down"],
  stp: ["mode", "is_root", "root_address", "root_priority", "root_port", "bridge_priority", "ports"],
  fhrp: ["interface", "protocol", "group", "state", "priority", "virtual_ip", "active_router", "standby_router"],
  ntp: ["vrf", "stratum", "mode", "synchronized"],
  config: ["lines"],
};

export function StateTable({ snapshot, table, device }: { snapshot: string; table: string; device?: string }) {
  const [q, setQ] = useState("");
  const params = useMemo(() => ({ q, device: device ? [device] : undefined, limit: 2000 }), [q, device]);
  const { data, error } = useApi<any>(`/snapshots/${snapshot}/tables/${table}`, params);
  const cols = COLS[table] || [];
  return (
    <>
      <div className="filters">
        <input placeholder={`Search ${table}`} value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="muted">{data ? `${data.total} row(s)` : ""}</span>
        <button className="btn sm" onClick={() => download(`/snapshots/${snapshot}/tables/${table}`, { format: "csv", q, device: device ? [device] : undefined })}>CSV</button>
      </div>
      <ErrorNote error={error} />
      {data && data.items.length === 0 ? <Empty>No rows.</Empty> : (
        <div className="tbl-wrap">
          <table className="tbl dense">
            <thead><tr>{!device && <th>Device</th>}<th>Key</th>{cols.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
            <tbody>
              {data?.items.map((r: any) => (
                <tr key={r.device + r.key}>
                  {!device && <td>{r.device}</td>}
                  <td className="mono">{r.key}</td>
                  {cols.map((c) => c === "lines"
                    ? <td key={c}><pre className="cfg">{(r[c] || []).join("\n") || "—"}</pre></td>
                    : c === "ports" ? <td key={c} className="mono">{Object.entries(r[c] || {}).map(([i, role]) => `${i} ${role}`).join(", ") || "—"}</td>
                    : <td key={c} className={c.includes("status") || c === "state" ? `st-${r[c]}` : ""}>{short(r[c])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {data && data.total > data.items.length && <p className="muted">Showing the first {data.items.length} rows. Refine the search or export CSV.</p>}
        </div>
      )}
    </>
  );
}

function RawViewer({ snapshot, device, onClose }: { snapshot: string; device: string; onClose: () => void }) {
  const cmds = useApi<any[]>(`/snapshots/${snapshot}/raw/${device}`);
  const [cmd, setCmd] = useState<string | null>(null);
  const [kind, setKind] = useState("raw");
  const out = useApi<string | object>(cmd ? `/snapshots/${snapshot}/raw/${device}/${encodeURIComponent(cmd.replace(/ /g, "_"))}` : null, { kind });
  return (
    <Modal title={`${device} · command output`} onClose={onClose}>
      <div className="row gap wrap">
        {cmds.data?.map((c) => <button key={c.command} className={c.command === cmd ? "tab active" : "tab"} onClick={() => setCmd(c.command)}>{c.command}</button>)}
      </div>
      {cmd && <div className="row gap"><label className="row gap"><input type="radio" checked={kind === "raw"} onChange={() => setKind("raw")} /> Raw CLI</label>
        <label className="row gap"><input type="radio" checked={kind === "genie"} onChange={() => setKind("genie")} /> Genie parsed</label></div>}
      {cmd ? <pre className="raw">{typeof out.data === "string" ? out.data || "(empty)" : JSON.stringify(out.data, null, 2)}</pre> : <Empty>Pick a command.</Empty>}
    </Modal>
  );
}
