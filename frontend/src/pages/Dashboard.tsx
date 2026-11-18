import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { TABLES } from "../api";
import { axisStyle, Chart } from "../components/Chart";
import { Badge, Card, Empty, ErrorNote, PageHeader, Stat } from "../components/ui";
import { ago, fmtTime, useApi } from "../hooks";

export default function Dashboard() {
  const { data, error } = useApi<any>("/stats/overview", { snapshots: 48 }, (d) => d.snapshots_running > 0);
  const nav = useNavigate();

  const trendOption = useMemo(() => {
    if (!data) return {};
    const t = data.trend as any[];
    const ax = axisStyle();
    return {
      tooltip: { trigger: "axis" },
      legend: { top: 0, textStyle: { color: ax.axisLabel.color } },
      xAxis: { type: "category", data: t.map((s) => fmtTime(s.created_at)), ...ax, splitLine: { show: false } },
      yAxis: { type: "value", minInterval: 1, ...ax },
      series: TABLES.map((tb) => ({
        name: tb, type: "bar", stack: "c", barMaxWidth: 28,
        data: t.map((s) => s.changes?.[tb] || 0),
      })),
    };
  }, [data]);

  const pie = (obj: Record<string, number>) => ({
    tooltip: { trigger: "item" },
    series: [{
      type: "pie", radius: ["55%", "80%"], avoidLabelOverlap: true, label: { formatter: "{b}\n{c}" },
      data: Object.entries(obj || {}).map(([name, value]) => ({ name, value })),
    }],
  });

  if (error) return <ErrorNote error={error} />;
  if (!data) return <div className="muted">Loading…</div>;
  const latest = data.latest_snapshot;
  const ch = latest?.summary?.changes;
  const trend = data.trend as any[];

  return (
    <>
      <PageHeader title="Dashboard" sub="Fleet state and what changed recently" />
      {data.devices.total === 0 && (
        <div className="alert info">No devices yet. <Link to="/devices">Add devices or import a CSV</Link> to start collecting.</div>
      )}
      <div className="stats">
        <Stat label="Devices" value={data.devices.total} hint={`${data.devices.by_status.active || 0} active`} />
        <Stat label="Latest snapshot" value={latest ? <Badge status={latest.status} /> : "—"}
          hint={latest ? <Link to={`/snapshots/${latest.id}`}>{ago(latest.created_at)}</Link> : "none yet"} />
        <Stat label="Changes vs previous" value={ch ? ch.total : "—"}
          hint={ch ? <Link to={`/compare?from=${ch.previous}&to=${latest.id}`}>{ch.devices_changed} device(s) · view diff</Link> : "need two snapshots"}
          tone={ch?.total ? "warn" : undefined} />
        <Stat label="Collection failures" value={data.devices.failing.length}
          hint={data.devices.failing.slice(0, 3).join(", ") || "all devices collected"} tone={data.devices.failing.length ? "bad" : "good"} />
        <Stat label="Snapshots" value={data.snapshot_count} hint={data.snapshots_running ? `${data.snapshots_running} running` : "stored"} />
      </div>

      <div className="grid-2">
        <Card title="Changes per snapshot" className="span-2">
          {trend.length < 2 ? <Empty>Changes appear here once there are two snapshots.</Empty> :
            <Chart option={trendOption} height={280}
              onClick={(p) => { const s = trend[p.dataIndex]; if (s) nav(`/compare?to=${s.id}`); }} />}
        </Card>
        <Card title="Platforms"><Chart option={pie(data.devices.by_platform)} height={220} /></Card>
        <Card title="OS versions">
          {Object.keys(data.devices.by_os_version).length ? <Chart option={pie(data.devices.by_os_version)} height={220} /> :
            <Empty>Versions are learned from the first collection.</Empty>}
        </Card>
        <Card title="Most changed devices (24 h)">
          {data.top_changed_devices_24h.length === 0 ? <Empty>No changes in the last 24 hours.</Empty> : (
            <table className="tbl">
              <tbody>
                {data.top_changed_devices_24h.map(([name, n]: [string, number]) => (
                  <tr key={name}><td><Link to={`/devices/${name}`}>{name}</Link></td><td className="num">{n}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Sites">
          <table className="tbl">
            <tbody>
              {Object.entries(data.devices.by_site).map(([s, n]) => (
                <tr key={s}><td>{s}</td><td className="num">{n as number}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}
