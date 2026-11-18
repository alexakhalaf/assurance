import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Snapshot } from "../api";
import { Chart } from "../components/Chart";
import { Card, Empty, ErrorNote, PageHeader, Stat } from "../components/ui";
import { fmtTime, useApi } from "../hooks";

const LINK = { unchanged: "#8d96a8", added: "#14a38b", removed: "#d9534f" } as Record<string, string>;
const NODE = { ok: "#2f5bea", warning: "#e8a33d", fail: "#d9534f", external: "#94a3b8" } as Record<string, string>;

export default function Topology() {
  const [sp, setSp] = useSearchParams();
  const compare = sp.get("compare") || "";
  const site = sp.get("site") || "";
  const nav = useNavigate();
  const [showExternal, setShowExternal] = useState(true);
  const snaps = useApi<{ items: Snapshot[] }>("/snapshots", { limit: 100 });
  const { data, error } = useApi<any>("/topology", { compare: compare || undefined, site: site || undefined });
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n, { replace: true }); };
  const usable = (snaps.data?.items || []).filter((s) => ["complete", "partial"].includes(s.status)).slice(1);
  const sites = [...new Set((data?.nodes || []).map((n: any) => n.site).filter(Boolean))] as string[];

  const option = useMemo(() => {
    if (!data) return {};
    const nodes = (data.nodes as any[]).filter((n) => showExternal || n.managed);
    const ids = new Set(nodes.map((n) => n.id));
    const degree: Record<string, number> = {};
    for (const l of data.links) { degree[l.source] = (degree[l.source] || 0) + 1; degree[l.target] = (degree[l.target] || 0) + 1; }
    return {
      tooltip: {
        formatter: (p: any) => {
          if (p.dataType === "edge") {
            const l = p.data.raw;
            return `${l.source} ${l.source_interface || ""}<br/>↔ ${l.target} ${l.target_interface || ""}<br/>${l.protocols.join(", ")} · ${l.change}`;
          }
          const n = p.data.raw;
          return `<b>${n.id}</b><br/>${n.managed ? `${n.platform || ""} · ${n.site || ""} · ${n.role || ""}<br/>checks: ${n.checks.fail} fail, ${n.checks.warning} warn` : `discovered (${n.platform || "unknown"})`}${n.change && n.change !== "unchanged" ? `<br/>${n.change}` : ""}`;
        },
      },
      series: [{
        type: "graph", layout: "force", roam: true, draggable: true,
        force: { repulsion: 420, edgeLength: [90, 160], gravity: 0.08 },
        label: { show: true, position: "bottom", fontSize: 11 },
        edgeLabel: { show: false },
        data: nodes.map((n) => {
          const tone = !n.managed ? "external" : n.checks.fail ? "fail" : n.checks.warning ? "warning" : "ok";
          return {
            id: n.id, name: n.id, raw: n, symbol: n.managed ? "roundRect" : "circle",
            symbolSize: n.managed ? 26 + Math.min(degree[n.id] || 0, 8) * 2 : 14,
            itemStyle: { color: NODE[tone], opacity: n.change === "removed" ? 0.35 : 1,
              borderColor: n.change === "added" ? LINK.added : n.change === "removed" ? LINK.removed : "transparent", borderWidth: 3 },
          };
        }),
        links: (data.links as any[]).filter((l) => ids.has(l.source) && ids.has(l.target)).map((l) => ({
          source: l.source, target: l.target, raw: l,
          lineStyle: { color: LINK[l.change], width: l.change === "unchanged" ? 1.5 : 3, type: l.change === "removed" ? "dashed" : "solid", curveness: 0.08 },
        })),
      }],
    };
  }, [data, showExternal]);

  const changed = (data?.links || []).filter((l: any) => l.change !== "unchanged");
  return (
    <>
      <PageHeader title="Topology" sub="Built from CDP and LLDP neighbors in the latest snapshot" />
      <Card>
        <div className="filters wrap" style={{ marginBottom: 0 }}>
          <label className="inline">Highlight changes since
            <select value={compare} onChange={(e) => set("compare", e.target.value)}>
              <option value="">(no comparison)</option>
              <option value="previous">Previous snapshot</option>
              <option value="24h">24 hours ago</option>
              <option value="7d">7 days ago</option>
              {usable.map((s) => <option key={s.id} value={s.id}>{fmtTime(s.created_at)} {s.labels.join(" ")}</option>)}
            </select></label>
          <label className="inline">Site
            <select value={site} onChange={(e) => set("site", e.target.value)}>
              <option value="">All sites</option>
              {sites.map((s) => <option key={s}>{s}</option>)}
            </select></label>
          <label className="inline"><input type="checkbox" checked={showExternal} onChange={(e) => setShowExternal(e.target.checked)} /> Show discovered (unmanaged) neighbors</label>
        </div>
      </Card>
      <ErrorNote error={error} />
      {data && (
        <>
          <div className="stats">
            <Stat label="Managed devices" value={data.nodes.filter((n: any) => n.managed).length} />
            <Stat label="Discovered neighbors" value={data.nodes.filter((n: any) => !n.managed).length} />
            <Stat label="Links" value={data.links.filter((l: any) => l.change !== "removed").length} />
            {compare && <Stat label="Link changes" value={changed.length} tone={changed.length ? "warn" : "good"}
              hint={`${changed.filter((l: any) => l.change === "added").length} added, ${changed.filter((l: any) => l.change === "removed").length} removed`} />}
          </div>
          <Card title="Map" actions={<div className="legend">
            <span><i style={{ background: NODE.ok }} />healthy</span><span><i style={{ background: NODE.warning }} />warnings</span>
            <span><i style={{ background: NODE.fail }} />failing checks</span><span><i style={{ background: NODE.external }} />discovered</span>
            {compare && <><span><i style={{ background: LINK.added }} />link added</span><span><i style={{ background: LINK.removed }} />link removed</span></>}
          </div>}>
            {data.nodes.length === 0 ? <Empty>No CDP or LLDP neighbors in this snapshot.</Empty> :
              <Chart option={option} height={560} onClick={(p) => { if (p.dataType === "node" && p.data.raw.managed) nav(`/devices/${p.data.id}`); }} />}
          </Card>
          {changed.length > 0 && (
            <Card title="Link changes">
              <table className="tbl dense">
                <thead><tr><th>Change</th><th>A side</th><th>B side</th><th>Protocols</th></tr></thead>
                <tbody>{changed.map((l: any, i: number) => (
                  <tr key={i}><td><span className={`chg chg-${l.change}`}>{l.change}</span></td>
                    <td>{l.source} <span className="mono muted">{l.source_interface}</span></td>
                    <td>{l.target} <span className="mono muted">{l.target_interface}</span></td>
                    <td>{l.protocols.join(", ")}</td></tr>
                ))}</tbody>
              </table>
            </Card>
          )}
        </>
      )}
    </>
  );
}
