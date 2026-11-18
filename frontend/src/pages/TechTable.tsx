import { useMemo, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { download } from "../api";
import { DataGrid, tableQuery } from "../components/DataGrid";
import { Drawer, PageHeader } from "../components/ui";
import { fmtTime } from "../hooks";
import { useSnapId, useSnapshot } from "../snapshot";
import { TABLE_BY_ID } from "../tables";

export default function TechTable() {
  const { table = "" } = useParams();
  const def = TABLE_BY_ID[table];
  const snap = useSnapId();
  const { current } = useSnapshot();
  const [row, setRow] = useState<any>(null);
  const source = useMemo(() => def && ({
    path: `/snapshots/${snap}/tables/${def.id}`,
    map: (q: any) => tableQuery(def.columns, q),
  }), [def, snap]);
  if (!def) return <Navigate to="/" replace />;
  return (
    <div className="page">
      <PageHeader title={def.title} crumbs={[["Technology"], [def.group]]}
        sub={<>{def.blurb} {current && <span>Snapshot {fmtTime(current.created_at)}.</span>}</>} />
      <section className="card">
        <DataGrid key={def.id} storageKey={`tech.${def.id}`} columns={def.columns} source={source!}
          rowKey={(r) => r.device + "\u0000" + r.key} onRowClick={setRow}
          onExport={(q) => download(`/snapshots/${snap}/tables/${def.id}`, { ...tableQuery(def.columns, q), format: "csv", limit: undefined, offset: undefined })} />
      </section>
      {row && (
        <Drawer title={<>{row.device} <span className="muted mono">{row.key}</span></>} onClose={() => setRow(null)}>
          <div className="section-title">Row data</div>
          <pre className="json">{JSON.stringify(Object.fromEntries(Object.entries(row).filter(([k]) => !["device", "key"].includes(k))), null, 2)}</pre>
        </Drawer>
      )}
    </div>
  );
}
