import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Card, Empty, ErrorNote, PageHeader, Stat } from "../components/ui";
import { fmtTime, short, useApi } from "../hooks";

const KIND: Record<string, string> = { ip: "IP address", prefix: "Prefix", mac: "MAC address", text: "Text" };

export default function Search() {
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") || "";
  const mode = sp.get("mode") || "now";
  const [input, setInput] = useState(q);
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n, { replace: true }); };
  const valid = q.trim().length >= 2;
  const now = useApi<any>(valid && mode === "now" ? "/search" : null, { q });
  const hist = useApi<any>(valid && mode === "history" ? "/search/history" : null, { q, since: sp.get("since") || "30d" });
  const cfg = useApi<any>(valid && mode === "config" ? "/search/config" : null, { pattern: q, regex: sp.get("regex") === "1" });

  return (
    <div className="page">
      <PageHeader title="Search" sub="Find an IP, MAC, hostname, prefix, serial or any text, now or in past snapshots" />
      <Card>
        <form className="filters wrap" style={{ marginBottom: 0 }} onSubmit={(e) => { e.preventDefault(); set("q", input.trim()); }}>
          <input autoFocus placeholder="10.20.100.22, 0050.5601.0f99, 172.16.0.0/16, FDO2231…" value={input} onChange={(e) => setInput(e.target.value)} style={{ minWidth: 360 }} />
          <button className="btn primary" type="submit">Search</button>
          <div className="seg">
            {[["now", "Latest snapshot"], ["history", "History"], ["config", "Configs"]].map(([m, l]) => (
              <button type="button" key={m} className={mode === m ? "tab active" : "tab"} onClick={() => set("mode", m === "now" ? "" : m)}>{l}</button>
            ))}
          </div>
          {mode === "history" && <select value={sp.get("since") || "30d"} onChange={(e) => set("since", e.target.value)}>
            <option value="24h">24 hours</option><option value="7d">7 days</option><option value="30d">30 days</option><option value="90d">90 days</option></select>}
          {mode === "config" && <label className="inline"><input type="checkbox" checked={sp.get("regex") === "1"} onChange={(e) => set("regex", e.target.checked ? "1" : "")} /> Regular expression</label>}
        </form>
      </Card>
      {!valid && <Empty>Type at least two characters.</Empty>}
      {mode === "now" && <NowResults r={now.data} error={now.error} />}
      {mode === "history" && <HistoryResults r={hist.data} error={hist.error} />}
      {mode === "config" && <ConfigResults r={cfg.data} error={cfg.error} />}
    </div>
  );
}

function NowResults({ r, error }: { r: any; error: string | null }) {
  if (error) return <ErrorNote error={error} />;
  if (!r) return null;
  return (
    <>
      <div className="stats">
        <Stat label="Looks like" value={KIND[r.kind]} hint={r.normalized !== r.query ? `searched as ${r.normalized}` : undefined} />
        <Stat label="Matches" value={r.total} hint={`snapshot ${fmtTime(r.snapshot.created_at)}`} />
        <Stat label="Devices" value={new Set(r.hits.map((h: any) => h.device)).size} />
        {r.routes && <Stat label={r.kind === "ip" ? "Covering routes" : "Related routes"} value={r.routes.length} />}
      </div>
      {r.routes?.length > 0 && (
        <Card title={r.kind === "ip" ? "Route used to reach it (longest prefix match)" : "Routes for this prefix"}>
          <table className="tbl dense">
            <thead><tr><th>Device</th><th>VRF</th><th>Prefix</th>{r.kind === "prefix" && <th>Relation</th>}<th>Protocol</th><th>Next hops</th></tr></thead>
            <tbody>{r.routes.map((x: any, i: number) => (
              <tr key={i}><td><Link to={`/devices/${x.device}`}>{x.device}</Link></td><td>{x.vrf}</td><td className="mono">{x.prefix}</td>
                {r.kind === "prefix" && <td>{x.relation}</td>}<td>{x.protocol}</td><td>{short(x.next_hops)}</td></tr>
            ))}</tbody>
          </table>
        </Card>
      )}
      <Card title="Where it appears">
        {r.hits.length === 0 ? <Empty>Not found in this snapshot. Try History.</Empty> : (
          <table className="tbl dense">
            <thead><tr><th>Device</th><th>Table</th><th>Object</th><th>Matched field</th></tr></thead>
            <tbody>{r.hits.map((h: any, i: number) => (
              <tr key={i}><td><Link to={`/devices/${h.device}`}>{h.device}</Link></td><td>{h.table}</td>
                <td className="mono">{h.key}</td><td className="muted">{h.fields.join(", ")}</td></tr>
            ))}</tbody>
          </table>
        )}
        {r.truncated && <p className="muted">Showing the first {r.hits.length} of {r.total}.</p>}
      </Card>
    </>
  );
}

function HistoryResults({ r, error }: { r: any; error: string | null }) {
  if (error) return <ErrorNote error={error} />;
  if (!r) return null;
  const snaps = [...r.snapshots].reverse();
  return (
    <Card title={`Seen in ${r.seen_in} of ${r.snapshots.length} snapshots`}>
      {snaps.length === 0 ? <Empty>No snapshots in this window.</Empty> : (
        <table className="tbl dense">
          <thead><tr><th>Snapshot</th><th>Where</th><th></th></tr></thead>
          <tbody>{snaps.map((e: any) => (
            <tr key={e.id} className={e.changed ? "sel" : ""}>
              <td className="nowrap"><Link to={`/snapshots/${e.id}`}>{fmtTime(e.created_at)}</Link></td>
              <td>{e.places.length ? e.places.map((p: any, i: number) => <div key={i}><Link to={`/devices/${p.device}`}>{p.device}</Link> <span className="muted">{p.table}</span> <span className="mono">{p.key}</span></div>) : <span className="muted">not seen</span>}</td>
              <td>{e.changed && <span className="chg chg-changed">moved</span>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Card>
  );
}

function ConfigResults({ r, error }: { r: any; error: string | null }) {
  if (error) return <ErrorNote error={error} />;
  if (!r) return null;
  return (
    <Card title={`${r.total} matching line(s) on ${r.devices.length} device(s)`}>
      {r.matches.length === 0 ? <Empty>No config line matches.</Empty> : (
        <table className="tbl dense">
          <thead><tr><th>Device</th><th>Section</th><th>Line</th></tr></thead>
          <tbody>{r.matches.map((m: any, i: number) => (
            <tr key={i}><td><Link to={`/devices/${m.device}`}>{m.device}</Link></td><td className="mono">{m.section}</td><td className="mono">{m.line}</td></tr>
          ))}</tbody>
        </table>
      )}
    </Card>
  );
}
