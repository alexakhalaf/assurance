import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Collection, download, Snapshot, toast } from "../api";
import { Col, DataGrid, tableQuery } from "../components/DataGrid";
import { Icon } from "../components/Icon";
import { Badge, Empty, ErrorNote, Loading, Modal, PageHeader, Stat, UTabs } from "../components/ui";
import { duration, fmtTime, useApi } from "../hooks";
import { useSnapshot } from "../snapshot";
import { TABLE_BY_ID, TABLE_DEFS } from "../tables";

export default function SnapshotDetail() {
  const { id = "" } = useParams();
  const { data: s, error } = useApi<Snapshot & { collections: Collection[] }>(`/snapshots/${id}`, undefined,
    (d) => ["queued", "running", "finalizing"].includes(d.status));
  const [tab, setTab] = useState("devices");
  const [raw, setRaw] = useState<string | null>(null);
  const snapCtx = useSnapshot();

  if (error) return <div className="page"><ErrorNote error={error} /></div>;
  if (!s) return <Loading />;
  const done = s.collections.filter((c) => !["pending", "running"].includes(c.status)).length;
  const failed = s.collections.filter((c) => c.status === "failed").length;
  const ch = s.summary?.changes;
  const cs = s.summary?.checks;
  const diffable = ["complete", "partial"].includes(s.status);
  const rows = Object.values(s.summary?.rows || {}).reduce((a: number, b: any) => a + b, 0) as number;
  const running = ["queued", "running", "finalizing"].includes(s.status);
  const columns: Col<Collection>[] = [
    { key: "device_name", label: "Device", render: (c) => <Link to={`/devices/${encodeURIComponent(c.device_name)}`}>{c.device_name}</Link> },
    { key: "platform", label: "Platform", filter: "select", options: ["iosxe", "nxos"], text: (c) => c.device_meta.platform || "" },
    { key: "site", label: "Site", text: (c) => c.device_meta.site || "" },
    { key: "status", label: "Status", filter: "select", options: ["ok", "partial", "failed", "running", "pending"], render: (c) => <Badge status={c.status} /> },
    { key: "duration_ms", label: "Time", num: true, filter: false, render: (c) => duration(c.duration_ms) },
    { key: "rows", label: "Rows", num: true, filter: false, sortValue: (c) => Object.values(c.row_counts).reduce((a, b) => a + b, 0),
      render: (c) => (Object.values(c.row_counts).reduce((a, b) => a + b, 0) || "—").toLocaleString() },
    { key: "notes", label: "Notes", wrap: true, text: (c) => c.error || c.warnings.join(" "),
      render: (c) => c.error ? <span className="bad">{c.error}</span> : c.warnings.length ? <span className="warn">{c.warnings.length} parser warning(s): {c.warnings[0]}</span> : <span className="faint">—</span> },
    { key: "raw", label: "", filter: false, sortable: false, render: (c) => diffable && c.status !== "failed" &&
      <button className="btn sm" onClick={(e) => { e.stopPropagation(); setRaw(c.device_name); }}>Raw output</button> },
  ];

  return (
    <div className="page">
      <PageHeader crumbs={[["Snapshots", "/snapshots"], [fmtTime(s.created_at)]]}
        title={<span className="row gap">Snapshot {fmtTime(s.created_at)} <Badge status={s.status} /></span>}
        sub={<><span className="mono">{s.id}</span> · {s.trigger}{s.requested_by ? ` by ${s.requested_by}` : ""}{s.labels.length ? <> · {s.labels.map((l) => <span className="tag" key={l}>{l}</span>)}</> : null}</>}
        actions={<>
          {diffable && <button className="btn" onClick={() => { snapCtx.setRef(s.id); toast(`Showing data from ${fmtTime(s.created_at)} everywhere`); }}><Icon name="snapshots" size={14} /> Use as current</button>}
          {ch && <Link className="btn primary" to={`/compare?from=${ch.previous}&to=${s.id}`}><Icon name="compare" size={14} /> Diff vs previous</Link>}
        </>} />
      <div className="stats">
        <Stat label="Devices" value={`${done} / ${s.device_count}`} hint={running ? "collecting…" : failed ? `${failed} failed` : "all collected"} tone={running ? "warn" : failed ? "bad" : "good"} />
        <Stat label="Duration" value={s.finished_at && s.started_at ? duration(+new Date(s.finished_at) - +new Date(s.started_at)) : "…"} hint={`started ${fmtTime(s.started_at)}`} />
        <Stat label="State rows" value={rows.toLocaleString()} hint={`${Object.keys(s.summary?.rows || {}).length} tables`} />
        <Stat label="Changes vs previous" value={ch?.total?.toLocaleString() ?? "—"} tone={ch ? (ch.total ? "warn" : "good") : undefined} hint={ch ? `${ch.devices_changed} device(s)` : "first snapshot"} />
        <Stat label="Check violations" value={cs ? cs.fail + cs.warning : "—"} tone={cs ? (cs.fail ? "bad" : cs.warning ? "warn" : "good") : undefined} hint={cs ? `${cs.fail} fail · ${cs.warning} warn` : "—"} />
      </div>
      {running && <div className="loading-bar" />}
      <section className="card">
        <UTabs value={tab} onChange={setTab} tabs={[
          { id: "devices", label: "Devices", n: s.device_count },
          ...(diffable ? TABLE_DEFS.map((t) => ({ id: t.id, label: t.title.replace(" (CDP / LLDP)", "").replace("Routing table", "Routes"), n: s.summary?.rows?.[t.id] ?? 0 })) : []),
        ]} />
        {tab === "devices" ? (
          <DataGrid storageKey="snapshot.devices" columns={columns} rows={s.collections} rowKey={(c) => c.device_name} autoHeight pageSize={100} />
        ) : <SnapTable snapshot={s.id} table={tab} />}
      </section>
      {raw && <RawViewer snapshot={s.id} device={raw} onClose={() => setRaw(null)} />}
    </div>
  );
}

function SnapTable({ snapshot, table }: { snapshot: string; table: string }) {
  const def = TABLE_BY_ID[table];
  const source = useMemo(() => ({ path: `/snapshots/${snapshot}/tables/${table}`, map: (q: any) => tableQuery(def.columns, q) }), [snapshot, table, def]);
  return <DataGrid key={table} storageKey={`tech.${table}`} columns={def.columns} source={source} rowKey={(r) => r.device + "\u0000" + r.key} autoHeight
    onExport={(q) => download(`/snapshots/${snapshot}/tables/${table}`, { ...tableQuery(def.columns, q), format: "csv" })} />;
}

/** Kept for other pages that embed a state table for one device. */
export function StateTable({ snapshot, table, device }: { snapshot: string; table: string; device?: string }) {
  const def = TABLE_BY_ID[table];
  const columns = useMemo(() => def.columns.filter((c) => !device || c.key !== "device"), [def, device]);
  const source = useMemo(() => ({ path: `/snapshots/${snapshot}/tables/${table}`, params: device ? { device: [device] } : {}, map: (q: any) => tableQuery(columns, q) }),
    [snapshot, table, device, columns]);
  return <DataGrid key={table} storageKey={`tech.${table}${device ? ".dev" : ""}`} columns={columns} source={source} rowKey={(r) => r.device + "\u0000" + r.key} autoHeight />;
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
