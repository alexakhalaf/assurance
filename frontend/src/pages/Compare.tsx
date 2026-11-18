import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Change, Diff, download, Snapshot, TABLES } from "../api";
import { Card, ChangeTag, Empty, ErrorNote, PageHeader, Stat } from "../components/ui";
import { fmtTime, short, useApi } from "../hooks";

const PRESETS = [
  { label: "Previous → latest", from: "previous", to: "latest" },
  { label: "Last 24 hours", from: "24h", to: "now" },
  { label: "Last 7 days", from: "7d", to: "now" },
];

export default function Compare() {
  const [sp, setSp] = useSearchParams();
  const from = sp.get("from") || "previous";
  const to = sp.get("to") || "latest";
  const tables = sp.getAll("table");
  const device = sp.get("device") || "";
  const [change, setChange] = useState("");
  const [q, setQ] = useState("");
  const snaps = useApi<{ items: Snapshot[] }>("/snapshots", { limit: 200, status: undefined });
  const params = useMemo(() => ({ from, to, table: tables, device: device ? [device] : undefined }), [from, to, tables.join(), device]);
  const { data, error, loading } = useApi<Diff>("/diffs", params);

  const update = (k: string, v: string | string[] | null) => {
    const n = new URLSearchParams(sp);
    n.delete(k);
    if (Array.isArray(v)) v.forEach((x) => n.append(k, x));
    else if (v) n.set(k, v);
    setSp(n, { replace: true });
  };
  const usable = snaps.data?.items.filter((s) => ["complete", "partial"].includes(s.status)) || [];

  const shown = useMemo(() => {
    const needle = q.toLowerCase();
    return (data?.changes || []).filter((c) => (!change || c.change === change) &&
      (!needle || `${c.device} ${c.key} ${JSON.stringify(c.after || c.before)}`.toLowerCase().includes(needle)));
  }, [data, change, q]);

  const s = data?.summary;
  return (
    <>
      <PageHeader title="Compare" sub="What changed between two points in time"
        actions={data && <button className="btn" onClick={() => download("/diffs", { ...params, format: "csv" })}>Export CSV</button>} />
      <Card>
        <div className="filters wrap">
          <div className="seg">{PRESETS.map((p) => (
            <button key={p.label} className={from === p.from && to === p.to ? "tab active" : "tab"}
              onClick={() => { update("from", p.from); update("to", p.to); }}>{p.label}</button>
          ))}</div>
          <label className="inline">From
            <select value={from} onChange={(e) => update("from", e.target.value)}>
              <option value={from}>{labelFor(from, usable)}</option>
              {usable.map((x) => <option key={x.id} value={x.id}>{fmtTime(x.created_at)} {x.labels.join(" ")}</option>)}
            </select></label>
          <label className="inline">To
            <select value={to} onChange={(e) => update("to", e.target.value)}>
              <option value={to}>{labelFor(to, usable)}</option>
              {usable.map((x) => <option key={x.id} value={x.id}>{fmtTime(x.created_at)} {x.labels.join(" ")}</option>)}
            </select></label>
        </div>
        <div className="filters wrap">
          <div className="seg">{TABLES.map((t) => (
            <button key={t} className={tables.includes(t) ? "tab active" : "tab"}
              onClick={() => update("table", tables.includes(t) ? tables.filter((x) => x !== t) : [...tables, t])}>{t}</button>
          ))}</div>
          <input placeholder="Device" value={device} onChange={(e) => update("device", e.target.value)} />
          <select value={change} onChange={(e) => setChange(e.target.value)}>
            <option value="">All changes</option><option value="added">Added</option><option value="removed">Removed</option><option value="changed">Changed</option>
          </select>
          <input placeholder="Search key or value" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </Card>
      <ErrorNote error={error} />
      {data && s && (
        <>
          <p className="muted">
            <Link to={`/snapshots/${data.from.id}`}>{fmtTime(data.from.created_at)}</Link> → <Link to={`/snapshots/${data.to.id}`}>{fmtTime(data.to.created_at)}</Link>
            {loading && " · updating…"}
          </p>
          <div className="stats">
            <Stat label="Total changes" value={s.total} tone={s.total ? "warn" : "good"} hint={`${s.devices_changed} device(s)`} />
            <Stat label="Added" value={s.added} tone={s.added ? "good" : undefined} />
            <Stat label="Removed" value={s.removed} tone={s.removed ? "bad" : undefined} />
            <Stat label="Modified" value={s.changed} tone={s.changed ? "warn" : undefined} />
          </div>
          {s.devices_unknown.length > 0 && <div className="alert">Not compared (collection failed in one snapshot): {s.devices_unknown.join(", ")}</div>}
          {(s.devices_only_in_to.length > 0 || s.devices_only_in_from.length > 0) && (
            <div className="alert info">
              {s.devices_only_in_to.length > 0 && <>Only in the later snapshot: {s.devices_only_in_to.join(", ")}. </>}
              {s.devices_only_in_from.length > 0 && <>Only in the earlier snapshot: {s.devices_only_in_from.join(", ")}.</>}
            </div>
          )}
          <Card title={`Changes${shown.length !== data.changes.length ? ` (${shown.length} of ${data.changes.length})` : ""}`}>
            {shown.length ? <ChangeList changes={shown} /> : <Empty>No differences.</Empty>}
          </Card>
        </>
      )}
    </>
  );
}

function labelFor(ref: string, snaps: Snapshot[]) {
  const s = snaps.find((x) => x.id === ref);
  if (s) return fmtTime(s.created_at);
  return { previous: "Previous snapshot", latest: "Latest snapshot", now: "Now" }[ref] || (/^\d+[mhdw]$/.test(ref) ? `${ref} ago` : ref);
}

export function ChangeList({ changes, hideDevice }: { changes: Change[]; hideDevice?: boolean }) {
  const groups = useMemo(() => {
    const g = new Map<string, Change[]>();
    for (const c of changes) {
      const k = hideDevice ? c.table : `${c.device}\u0000${c.table}`;
      g.set(k, [...(g.get(k) || []), c]);
    }
    return [...g.entries()];
  }, [changes, hideDevice]);
  return (
    <div className="changes">
      {groups.map(([k, list]) => {
        const [dev, table] = hideDevice ? ["", k] : k.split("\u0000");
        return (
          <div key={k} className="chg-group">
            <h3>{!hideDevice && <><Link to={`/devices/${dev}`}>{dev}</Link> · </>}<span className="muted">{table}</span> <span className="muted">({list.length})</span></h3>
            <table className="tbl dense">
              <tbody>
                {list.map((c) => (
                  <tr key={c.key}>
                    <td><ChangeTag change={c.change} /></td>
                    <td className="mono">{c.key}</td>
                    <td>{c.table === "config" ? <ConfigLines change={c} /> : c.change === "changed" ? (
                      <ul className="fields">{c.fields_changed.map((f) => (
                        <li key={f}><span className="muted">{f}</span> <del>{short(c.before?.[f])}</del> → <ins>{short(c.after?.[f])}</ins></li>
                      ))}</ul>
                    ) : <span className="muted">{brief(c.table, c.after || c.before || {})}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

function brief(table: string, v: Record<string, any>) {
  const keep: Record<string, string[]> = {
    facts: ["model", "os_version"], interfaces: ["oper_status", "description", "ipv4"],
    routes: ["protocol", "next_hops"], arp: ["mac", "interface"], neighbors: ["remote_device", "remote_interface"],
    bgp: ["remote_as", "state", "prefixes_received"], ospf: ["address", "state"], mac: ["interfaces"],
    vlans: ["name", "state"], vrfs: ["rd", "state"], hardware: ["pid", "serial"],
    portchannels: ["oper_status", "members_bundled"], stp: ["root_address", "root_port"],
    fhrp: ["state", "virtual_ip"], ntp: ["mode", "stratum"],
  };
  return (keep[table] || []).map((k) => `${k.replace(/_/g, " ")}: ${short(v[k])}`).join(" · ");
}

/** Line-level view of a changed config section: removed lines red, added lines green, the rest as context. */
export function ConfigLines({ change }: { change: Change }) {
  const before: string[] = change.before?.lines || [];
  const after: string[] = change.after?.lines || [];
  const gone = new Set(before.filter((l) => !after.includes(l)));
  const added = new Set(after.filter((l) => !before.includes(l)));
  const rows = [...before.filter((l) => gone.has(l)).map((l) => ["-", l]), ...after.map((l) => [added.has(l) ? "+" : " ", l])];
  if (change.change === "removed") rows.splice(0, rows.length, ...before.map((l) => ["-", l]));
  if (!rows.length) {
    const sym = change.change === "removed" ? "-" : "+";
    return <pre className="cfg"><span className={sym === "+" ? "cfg-add" : "cfg-del"}>{sym} {change.key.replace(/#\d+$/, "")}</span></pre>;
  }
  return (
    <pre className="cfg">{rows.map(([s, l], i) => (
      <span key={i} className={s === "+" ? "cfg-add" : s === "-" ? "cfg-del" : "muted"}>{s} {l}{"\n"}</span>
    ))}</pre>
  );
}
