import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Change, Diff, download, Snapshot } from "../api";
import { Icon } from "../components/Icon";
import { ChangeTag, EmptyState, ErrorNote, PageHeader, Stat } from "../components/ui";
import { TABLE_BY_ID } from "../tables";
import { fmtTime, short, useApi } from "../hooks";

const PRESETS = [
  { label: "Previous → latest", from: "previous", to: "latest" },
  { label: "Last 24 hours", from: "24h", to: "now" },
  { label: "Last 7 days", from: "7d", to: "now" },
];

const PAGE = 1000;

export default function Compare() {
  const [sp, setSp] = useSearchParams();
  const from = sp.get("from") || "previous";
  const to = sp.get("to") || "latest";
  const tables = sp.getAll("table");
  const device = sp.get("device") || "";
  const change = sp.get("change") || "";
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const snaps = useApi<{ items: Snapshot[] }>("/snapshots", { limit: 200 });
  const params = useMemo(() => ({ from, to, table: tables, device: device ? [device] : undefined, change: change || undefined, limit }),
    [from, to, tables.join(), device, change, limit]);
  const { data, error, loading } = useApi<Diff>("/diffs", params);
  // Facet counts come from the unfiltered (by table/device/change) summary of the same window.
  const whole = useApi<Diff>("/diffs", { from, to, limit: 1 });

  const update = (k: string, v: string | string[] | null) => {
    const n = new URLSearchParams(sp);
    n.delete(k);
    if (Array.isArray(v)) v.forEach((x) => n.append(k, x));
    else if (v) n.set(k, v);
    setSp(n, { replace: true });
    setLimit(PAGE);
  };
  const usable = snaps.data?.items.filter((s) => ["complete", "partial"].includes(s.status)) || [];

  const shown = useMemo(() => {
    const needle = q.toLowerCase();
    return (data?.changes || []).filter((c) => !needle || `${c.device} ${c.key} ${JSON.stringify(c.after || c.before)}`.toLowerCase().includes(needle));
  }, [data, q]);

  const s = data?.summary;
  const all = whole.data?.summary;
  const byTable = Object.entries(all?.by_table || {}).map(([t, v]: any) => [t, v.added + v.removed + v.changed] as [string, number]).sort((a, b) => b[1] - a[1]);
  const byDevice = Object.entries(all?.by_device || {}).map(([d, v]: any) => [d, v.added + v.removed + v.changed] as [string, number]).sort((a, b) => b[1] - a[1]);
  const [devFilter, setDevFilter] = useState("");
  const devShown = byDevice.filter(([d]) => !devFilter || d.toLowerCase().includes(devFilter.toLowerCase())).slice(0, 50);

  return (
    <div className="page">
      <PageHeader title="Changes" crumbs={[["Analysis"]]} sub="What changed between two points in time, row by row"
        actions={data && <button className="btn" onClick={() => download("/diffs", { ...params, limit: undefined, format: "csv" })}><Icon name="download" size={14} /> Export CSV</button>} />
      <section className="card">
        <div className="grid-toolbar" style={{ borderBottom: 0 }}>
          <div className="seg">{PRESETS.map((p) => (
            <button key={p.label} className={from === p.from && to === p.to ? "tab active" : "tab"}
              onClick={() => { const n = new URLSearchParams(sp); n.set("from", p.from); n.set("to", p.to); setSp(n, { replace: true }); }}>{p.label}</button>
          ))}</div>
          <label className="inline">From
            <select value={from} onChange={(e) => update("from", e.target.value)} aria-label="From snapshot">
              <option value={from}>{labelFor(from, usable)}</option>
              {usable.filter((x) => x.id !== from).map((x) => <option key={x.id} value={x.id}>{fmtTime(x.created_at)} {x.labels.join(" ")}</option>)}
            </select></label>
          <Icon name="chevronRight" size={14} />
          <label className="inline">To
            <select value={to} onChange={(e) => update("to", e.target.value)} aria-label="To snapshot">
              <option value={to}>{labelFor(to, usable)}</option>
              {usable.filter((x) => x.id !== to).map((x) => <option key={x.id} value={x.id}>{fmtTime(x.created_at)} {x.labels.join(" ")}</option>)}
            </select></label>
          {data && <span className="muted small">
            <Link to={`/snapshots/${data.from.id}`}>{fmtTime(data.from.created_at)}</Link> → <Link to={`/snapshots/${data.to.id}`}>{fmtTime(data.to.created_at)}</Link>
            {loading && " · updating…"}</span>}
        </div>
      </section>
      <ErrorNote error={error} />
      {all && (
        <div className="stats">
          <Stat label="Total changes" value={all.total.toLocaleString()} tone={all.total ? "warn" : "good"} hint={`${all.devices_changed} device(s) changed`} />
          <Stat label="Added" value={all.added.toLocaleString()} hint="new rows" />
          <Stat label="Removed" value={all.removed.toLocaleString()} hint="rows gone" tone={all.removed ? "bad" : undefined} />
          <Stat label="Modified" value={all.changed.toLocaleString()} hint="fields changed" />
          <Stat label="Not compared" value={all.devices_unknown.length} hint={all.devices_unknown.length ? "collection failed in one snapshot" : "every device compared"} tone={all.devices_unknown.length ? "warn" : undefined} />
        </div>
      )}
      {all && all.devices_unknown.length > 0 && <div className="alert warn">Not compared because collection failed in one of the snapshots: {all.devices_unknown.slice(0, 20).join(", ")}{all.devices_unknown.length > 20 ? "…" : ""}</div>}
      {all && (all.devices_only_in_to.length > 0 || all.devices_only_in_from.length > 0) && (
        <div className="alert info">
          {all.devices_only_in_to.length > 0 && <>Only in the later snapshot: {all.devices_only_in_to.slice(0, 20).join(", ")}. </>}
          {all.devices_only_in_from.length > 0 && <>Only in the earlier snapshot: {all.devices_only_in_from.slice(0, 20).join(", ")}.</>}
        </div>
      )}
      {data && s && (
        <div className="compare-layout">
          <aside className="card" style={{ padding: 6 }}>
            <div className="facet-h">Change type</div>
            {[["", "All", all?.total], ["added", "Added", all?.added], ["removed", "Removed", all?.removed], ["changed", "Modified", all?.changed]].map(([v, l, n]) => (
              <button key={String(v)} className={`facet ${change === v ? "on" : ""}`} onClick={() => update("change", (v as string) || null)}><span>{l}</span><span className="n">{(n as number)?.toLocaleString()}</span></button>
            ))}
            <div className="facet-h">Table</div>
            <button className={`facet ${!tables.length ? "on" : ""}`} onClick={() => update("table", null)}><span>All tables</span><span className="n">{all?.total.toLocaleString()}</span></button>
            {byTable.map(([t, n]) => (
              <button key={t} className={`facet ${tables.includes(t) ? "on" : ""}`} onClick={() => update("table", tables.includes(t) ? tables.filter((x) => x !== t) : [...tables, t])}>
                <span>{TABLE_BY_ID[t]?.title || t}</span><span className="n">{n.toLocaleString()}</span></button>
            ))}
            <div className="facet-h">Device</div>
            {byDevice.length > 8 && <div style={{ padding: "0 6px 6px" }}><input placeholder="Filter devices" value={devFilter} onChange={(e) => setDevFilter(e.target.value)} style={{ width: "100%", height: 28 }} /></div>}
            <button className={`facet ${!device ? "on" : ""}`} onClick={() => update("device", null)}><span>All devices</span><span className="n">{byDevice.length}</span></button>
            {devShown.map(([d, n]) => (
              <button key={d} className={`facet ${device === d ? "on" : ""}`} onClick={() => update("device", device === d ? null : d)}><span className="truncate">{d}</span><span className="n">{n.toLocaleString()}</span></button>
            ))}
          </aside>
          <section className="card">
            <div className="grid-toolbar">
              <span className="count">{data.total.toLocaleString()} change(s){shown.length !== data.changes.length ? `, ${shown.length} match the search` : ""}</span>
              <div className="input-icon"><Icon name="search" size={14} /><input placeholder="Search key or value" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} /></div>
            </div>
            {shown.length ? <ChangeList changes={shown} /> : <EmptyState icon="checks" title="No differences">{all?.total ? "Nothing matches these filters." : "These two snapshots are identical."}</EmptyState>}
            {data.total > data.changes.length && (
              <div className="grid-foot">Showing {data.changes.length.toLocaleString()} of {data.total.toLocaleString()}.
                <button className="btn sm" onClick={() => setLimit(limit + PAGE * 4)}>Load more</button>
                <span>Narrow by table or device, or export CSV for everything.</span></div>
            )}
          </section>
        </div>
      )}
    </div>
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
    fhrp: ["state", "virtual_ip"], ntp: ["mode", "stratum"], snmp: ["kind", "name", "version"],
    aaa: ["kind", "name", "methods"],
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
