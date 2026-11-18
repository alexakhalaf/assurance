import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { TABLES } from "../api";
import { useAuth } from "../auth";
import { axisStyle, Chart, TONE } from "../components/Chart";
import { Icon } from "../components/Icon";
import { Badge, BarList, Card, EmptyState, ErrorNote, Loading, PageHeader, Stat } from "../components/ui";
import { ago, duration, fmtShort, fmtTime, pct, useApi } from "../hooks";

/** One category per snapshot: bars stay readable whether there are 2 snapshots or 60. */
function snapAxis(items: any[]) {
  const ax = axisStyle();
  return { type: "category" as const, data: items.map((s) => fmtShort(s.created_at)), ...ax, splitLine: { show: false },
    axisLabel: { ...ax.axisLabel, hideOverlap: true } };
}

export default function Dashboard() {
  const { data, error } = useApi<any>("/stats/overview", { snapshots: 60 }, (d) => d.snapshots_running > 0);
  const checkTrend = useApi<any[]>("/checks/trend", { snapshots: 60 });
  const rules = useApi<{ rules: any[] }>("/checks/rules");
  const { can } = useAuth();
  const nav = useNavigate();

  const trend = (data?.trend || []) as any[];
  const changeOption = useMemo(() => {
    const ax = axisStyle();
    const used = TABLES.filter((t) => trend.some((s) => s.changes?.[t]));
    return {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { top: 0, left: 0 },
      xAxis: snapAxis(trend),
      yAxis: { type: "value", minInterval: 1, ...ax },
      series: used.map((tb) => ({
        name: tb, type: "bar", stack: "c", barMaxWidth: 28, emphasis: { focus: "series" },
        data: trend.map((s) => s.changes?.[tb] || 0),
      })),
    };
  }, [data]);

  const violationOption = useMemo(() => {
    const ax = axisStyle();
    const t = checkTrend.data || [];
    return {
      tooltip: { trigger: "axis" },
      legend: { top: 0, left: 0 },
      xAxis: snapAxis(t),
      yAxis: { type: "value", minInterval: 1, ...ax },
      series: [
        { name: "Failing", type: "line", showSymbol: t.length < 30, color: TONE.bad, areaStyle: { opacity: 0.12 }, data: t.map((s) => s.fail) },
        { name: "Warnings", type: "line", showSymbol: t.length < 30, color: TONE.warn, data: t.map((s) => s.warning) },
      ],
    };
  }, [checkTrend.data]);

  const collectionOption = useMemo(() => {
    const ax = axisStyle();
    return {
      tooltip: { trigger: "axis" },
      legend: { top: 0, left: 0 },
      xAxis: snapAxis(trend),
      yAxis: { type: "value", minInterval: 1, ...ax },
      series: [
        { name: "Collected", type: "bar", stack: "d", color: TONE.good, barMaxWidth: 28, data: trend.map((s) => s.devices_ok || 0) },
        { name: "Failed", type: "bar", stack: "d", color: TONE.bad, barMaxWidth: 28, data: trend.map((s) => s.devices_failed || 0) },
      ],
    };
  }, [data]);

  if (error) return <div className="page"><ErrorNote error={error} /></div>;
  if (!data) return <Loading />;

  const latest = data.latest_snapshot;
  const ch = latest?.summary?.changes;
  const cs = latest?.summary?.checks;
  const ruleStates = Object.entries(cs?.rules || {}) as [string, any][];
  const passing = ruleStates.filter(([, r]) => r.status === "pass").length;
  const names: Record<string, string> = Object.fromEntries((rules.data?.rules || []).map((r) => [r.id, r.name]));
  const failingRules = ruleStates.filter(([, r]) => r.violations > 0).sort((a, b) => b[1].violations - a[1].violations)
    .map(([id, r]) => [names[id] || id, r.violations] as [string, number]).slice(0, 8);
  const worst = Object.entries((cs?.devices || {}) as Record<string, any>)
    .sort((a, b) => b[1].fail - a[1].fail || b[1].warning - a[1].warning).slice(0, 8);
  const total = data.devices.total;
  const failing: string[] = data.devices.failing;
  const okDevices = latest ? latest.summary?.devices_ok ?? 0 : 0;
  const latestRows = Object.values(latest?.summary?.rows || {}).reduce((a: number, b: any) => a + b, 0) as number;
  const changeByTable = Object.entries(ch?.by_table || {}).filter(([, n]) => (n as number) > 0).sort((a, b) => (b[1] as number) - (a[1] as number)) as [string, number][];

  if (total === 0) {
    return (
      <div className="page">
        <PageHeader title="Dashboard" />
        <Card>
          <EmptyState icon="devices" title="No devices yet" action={can("admin") && <Link className="btn primary" to="/devices">Add or import devices</Link>}>
            Add devices (or import a CSV), add a credential profile under Settings, then take the first snapshot.
          </EmptyState>
        </Card>
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader title="Dashboard" sub={latest ? <>Latest snapshot {fmtTime(latest.created_at)} · {ago(latest.created_at)}</> : "No finished snapshot yet"}
        actions={<>
          {latest && <Link className="btn" to={`/snapshots/${latest.id}`}><Icon name="snapshots" size={14} /> Latest snapshot</Link>}
          {ch && <Link className="btn primary" to={`/compare?from=${ch.previous}&to=${latest.id}`}><Icon name="compare" size={14} /> What changed</Link>}
        </>} />

      <div className="stats">
        <Stat icon="devices" label="Devices" value={total.toLocaleString()} hint={`${data.devices.by_status.active || 0} active · ${Object.keys(data.devices.by_site).length} site(s)`} to="/devices" />
        <Stat icon="snapshots" label="Collection" value={latest ? pct(okDevices, latest.device_count) : "—"}
          tone={!latest ? undefined : failing.length ? "bad" : "good"}
          hint={latest ? `${okDevices} of ${latest.device_count} collected${failing.length ? ` · ${failing.length} failing` : ""}` : "no snapshot yet"}
          to={latest ? `/snapshots/${latest.id}` : "/snapshots"} />
        <Stat icon="compare" label="Changes" value={ch ? ch.total.toLocaleString() : "—"} tone={ch ? (ch.total ? "warn" : "good") : undefined}
          hint={ch ? `${ch.devices_changed} device(s) · +${ch.added} −${ch.removed} ~${ch.changed}` : "needs two snapshots"}
          to={ch ? `/compare?from=${ch.previous}&to=${latest.id}` : undefined} />
        <Stat icon="checks" label="Compliance" value={ruleStates.length ? pct(passing, ruleStates.length) : "—"}
          tone={cs ? (cs.fail ? "bad" : cs.warning ? "warn" : "good") : undefined}
          hint={cs ? `${passing}/${ruleStates.length} rules · ${cs.fail} fail · ${cs.warning} warn` : "graded after each snapshot"} to="/checks" />
        <Stat icon="facts" label="State rows" value={latestRows.toLocaleString()} hint={latest ? `latest snapshot · ${duration(latest.finished_at && latest.started_at ? +new Date(latest.finished_at) - +new Date(latest.started_at) : null)}` : "—"} />
        <Stat icon="clock" label="Snapshots" value={data.snapshot_count.toLocaleString()} hint={data.snapshots_running ? `${data.snapshots_running} running now` : "stored"} to="/snapshots" />
      </div>

      <div className="grid-12">
        <Card className="col-8" title="Changes per snapshot" sub="by table"
          actions={<Link className="btn sm ghost" to="/compare?from=24h&to=now">Last 24 h</Link>}>
          {trend.length < 2 ? <EmptyState icon="compare" title="Not enough history">Changes appear once there are two snapshots.</EmptyState> :
            <div style={{ padding: "8px 12px 12px" }}><Chart option={changeOption} height={280}
              onClick={(p) => { const s = trend[p.dataIndex]; if (s) nav(`/compare?to=${s.id}`); }} /></div>}
        </Card>
        <Card className="col-4" title="Latest changes" sub="by table">
          {changeByTable.length ? <div style={{ padding: 16 }}><BarList items={changeByTable} color={TONE.warn}
            onClick={(t) => nav(`/compare?from=${ch.previous}&to=${latest.id}&table=${t}`)} /></div>
            : <EmptyState icon="checks" title={ch ? "No changes" : "No comparison yet"}>{ch ? "Nothing changed between the last two snapshots." : "Take a second snapshot."}</EmptyState>}
        </Card>

        <Card className="col-6" title="Intent check violations" sub="over time" actions={<Link className="btn sm ghost" to="/checks">All rules</Link>}>
          {(checkTrend.data?.length || 0) < 2 ? <EmptyState icon="checks" title="Not enough history">Appears after two graded snapshots.</EmptyState> :
            <div style={{ padding: "8px 12px 12px" }}><Chart option={violationOption} height={220} /></div>}
        </Card>
        <Card className="col-6" title="Failing rules" sub="latest snapshot">
          {failingRules.length ? <div style={{ padding: 16 }}><BarList items={failingRules} color={TONE.bad} onClick={() => nav("/checks")} /></div>
            : <EmptyState icon="checks" title="All rules pass">{cs ? "No violations on the latest snapshot." : "Graded after each snapshot."}</EmptyState>}
        </Card>

        <Card className="col-4" title="Most changed devices" sub="24 h">
          {data.top_changed_devices_24h.length === 0 ? <EmptyState icon="devices" title="Quiet">No changes in the last 24 hours.</EmptyState> :
            <div style={{ padding: 16 }}><BarList items={data.top_changed_devices_24h} onClick={(d) => nav(`/devices/${encodeURIComponent(d)}`)} /></div>}
        </Card>
        <Card className="col-4" title="Devices with most violations">
          {worst.length === 0 ? <EmptyState icon="checks" title="No violations" /> : (
            <table className="tbl dense"><tbody>{worst.map(([name, c]) => (
              <tr key={name}><td><Link to={`/devices/${encodeURIComponent(name)}`}>{name}</Link></td>
                <td className="num">{c.fail > 0 && <span className="badge badge-fail">{c.fail} fail</span>} {c.warning > 0 && <span className="badge badge-warning">{c.warning} warn</span>}</td></tr>
            ))}</tbody></table>
          )}
        </Card>
        <Card className="col-4" title="Collection failures" sub="last attempt">
          {failing.length === 0 ? <EmptyState icon="snapshots" title="All devices collected" /> : (
            <table className="tbl dense"><tbody>{failing.slice(0, 10).map((n) => (
              <tr key={n}><td><Link to={`/devices/${encodeURIComponent(n)}`}>{n}</Link></td><td className="num"><Badge status="failed" /></td></tr>
            ))}</tbody></table>
          )}
          {failing.length > 10 && <p className="muted small" style={{ margin: "8px 16px" }}>and {failing.length - 10} more</p>}
        </Card>

        <Card className="col-6" title="Collection per snapshot">
          {trend.length < 1 ? <EmptyState icon="snapshots" title="No snapshots yet" /> :
            <div style={{ padding: "8px 12px 12px" }}><Chart option={collectionOption} height={200} /></div>}
        </Card>
        <Card className="col-3" title="Platforms">
          <div style={{ padding: 16 }}><BarList limit={8} items={Object.entries(data.devices.by_platform) as [string, number][]} /></div>
        </Card>
        <Card className="col-3" title="Sites">
          <div style={{ padding: 16 }}><BarList limit={8} items={Object.entries(data.devices.by_site) as [string, number][]} /></div>
        </Card>
        <Card className="col-6" title="Software versions">
          {Object.keys(data.devices.by_os_version).length ? <div style={{ padding: 16 }}><BarList items={(Object.entries(data.devices.by_os_version) as [string, number][])} color="#8e6bd8" /></div>
            : <EmptyState icon="facts" title="Not learned yet">Versions are learned from the first collection.</EmptyState>}
        </Card>
        <Card className="col-6" title="Recent snapshots" actions={<Link className="btn sm ghost" to="/snapshots">All</Link>}>
          <table className="tbl dense">
            <thead><tr><th>Taken</th><th>Status</th><th className="num">Devices</th><th className="num">Changes</th><th>Labels</th></tr></thead>
            <tbody>{[...trend].reverse().slice(0, 6).map((s) => (
              <tr key={s.id}><td><Link to={`/snapshots/${s.id}`}>{fmtTime(s.created_at)}</Link></td><td><Badge status={s.status} /></td>
                <td className="num">{s.devices_ok ?? "—"}{s.devices_failed ? <span className="bad"> / {s.devices_failed} failed</span> : ""}</td>
                <td className="num">{s.total_changes}</td><td>{(s.labels || []).slice(0, 2).map((l: string) => <span key={l} className="tag">{l}</span>)}</td></tr>
            ))}</tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
