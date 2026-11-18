import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Change, Device, TABLES } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, Empty, ErrorNote, PageHeader } from "../components/ui";
import { fmtTime, useApi } from "../hooks";
import { ChangeList } from "./Compare";
import { DeviceForm } from "./Devices";
import { StateTable } from "./SnapshotDetail";

export default function DevicePage() {
  const { name = "" } = useParams();
  const { can } = useAuth();
  const nav = useNavigate();
  const dev = useApi<Device>(`/devices/${name}`);
  const [since, setSince] = useState("7d");
  const hist = useApi<any>(`/devices/${name}/history`, { since });
  const [tab, setTab] = useState<string>("interfaces");
  const [editing, setEditing] = useState(false);
  const d = dev.data;

  async function remove() {
    if (!confirm(`Remove ${name} from inventory? Its history in past snapshots is kept.`)) return;
    await api(`/devices/${name}`, { method: "DELETE" });
    nav("/devices");
  }

  if (dev.error) return <ErrorNote error={dev.error} />;
  if (!d) return <div className="muted">Loading…</div>;
  return (
    <>
      <PageHeader title={d.name} sub={<><Link to="/devices">Devices</Link> · {d.platform} · {d.host}</>}
        actions={can("admin") && <><button className="btn" onClick={() => setEditing(true)}>Edit</button><button className="btn danger" onClick={remove}>Remove</button></>} />
      <div className="grid-2">
        <Card title="Facts">
          <dl className="facts">
            <dt>Status</dt><dd><Badge status={d.status} /></dd>
            <dt>Site / role</dt><dd>{d.site || "—"} / {d.role || "—"}</dd>
            <dt>Model</dt><dd>{d.model || "—"}</dd>
            <dt>Serial</dt><dd className="mono">{d.serial || "—"}</dd>
            <dt>OS version</dt><dd className="mono">{d.os_version || "—"}</dd>
            <dt>Credential</dt><dd>{d.credential || "inherited"}</dd>
            <dt>Last collection</dt><dd>{d.last_collection_status ? <><Badge status={d.last_collection_status} /> {fmtTime(d.last_collected_at)}</> : "never"}</dd>
            <dt>Tags</dt><dd>{d.tags.map((t) => <span className="tag" key={t}>{t}</span>)}</dd>
          </dl>
        </Card>
        <Card title="Change history" actions={
          <select value={since} onChange={(e) => setSince(e.target.value)}>
            <option value="24h">24 hours</option><option value="7d">7 days</option><option value="30d">30 days</option><option value="all">All</option>
          </select>}>
          {hist.data?.events?.length ? (
            <ol className="timeline">
              {hist.data.events.map((ev: any) => (
                <li key={ev.to.id}>
                  <Link to={`/compare?from=${ev.from.id}&to=${ev.to.id}&device=${name}`}>{fmtTime(ev.to.created_at)}</Link>
                  <span className="muted"> · {Object.entries(ev.summary).map(([t, c]: any) => `${t} ${c.added + c.removed + c.changed}`).join(", ")}</span>
                </li>
              ))}
            </ol>
          ) : <Empty>No changes in this window{hist.data ? ` (${hist.data.snapshots} snapshot(s))` : ""}.</Empty>}
        </Card>
      </div>
      {hist.data?.events?.[0] && (
        <Card title={`Latest change · ${fmtTime(hist.data.events[0].to.created_at)}`}>
          <ChangeList changes={hist.data.events[0].changes as Change[]} hideDevice />
        </Card>
      )}
      <Card title="Current state" actions={<div className="tabs">{TABLES.map((t) => <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>{t}</button>)}</div>}>
        <StateTable snapshot="latest" table={tab} device={name} />
      </Card>
      {editing && <DeviceForm device={d} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); dev.reload(); }} />}
    </>
  );
}
