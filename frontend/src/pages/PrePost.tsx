import { useState } from "react";
import { Link } from "react-router-dom";
import { api, Snapshot } from "../api";
import { Badge, Card, Empty, ErrorNote, PageHeader, Stat } from "../components/ui";
import { fmtTime, useApi } from "../hooks";
import { ViolationTable } from "./Checks";

const WORD: Record<string, string> = { pass: "Change verified", warning: "Verified with warnings", fail: "Verification failed" };

export default function PrePost() {
  const snaps = useApi<{ items: Snapshot[] }>("/snapshots", { limit: 200 });
  const usable = (snaps.data?.items || []).filter((s) => ["complete", "partial"].includes(s.status));
  const labels = [...new Set(usable.flatMap((s) => s.labels))].filter((l) => usable.filter((s) => s.labels.includes(l)).length >= 2);
  const [mode, setMode] = useState<"label" | "pick">("pick");
  const [label, setLabel] = useState("");
  const [pre, setPre] = useState("previous");
  const [post, setPost] = useState("latest");
  const [pct, setPct] = useState(2);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setErr(null); setBusy(true);
    try {
      const body = mode === "label" ? { label, route_drop_pct: pct } : { pre, post, route_drop_pct: pct };
      setRes(await api("/compare/prepost", { method: "POST", body: JSON.stringify(body) }));
    } catch (e: any) { setErr(e.message); setRes(null); } finally { setBusy(false); }
  }

  const opts = (extra: [string, string]) => (
    <>
      <option value={extra[0]}>{extra[1]}</option>
      {usable.map((s) => <option key={s.id} value={s.id}>{fmtTime(s.created_at)} {s.labels.join(" ")}</option>)}
    </>
  );
  const checks = res ? Object.entries(res.checks as Record<string, any>) : [];
  const failing = checks.filter(([, c]) => c.status !== "pass");

  return (
    <>
      <PageHeader title="Change verification" sub="Compare snapshots taken before and after a change window" />
      <Card>
        <div className="form">
          <div className="seg">
            <button className={mode === "pick" ? "tab active" : "tab"} onClick={() => setMode("pick")}>Pick snapshots</button>
            <button className={mode === "label" ? "tab active" : "tab"} onClick={() => setMode("label")}>By change label</button>
          </div>
          <div className="filters wrap" style={{ marginBottom: 0 }}>
            {mode === "pick" ? <>
              <label className="inline">Before<select value={pre} onChange={(e) => setPre(e.target.value)}>{opts(["previous", "Previous snapshot"])}</select></label>
              <label className="inline">After<select value={post} onChange={(e) => setPost(e.target.value)}>{opts(["latest", "Latest snapshot"])}</select></label>
            </> : (
              <label className="inline">Label
                <select value={label} onChange={(e) => setLabel(e.target.value)}>
                  <option value="">Choose…</option>{labels.map((l) => <option key={l}>{l}</option>)}
                </select>
                {labels.length === 0 && <span className="muted">Take snapshots with <code>--label change:CHG…</code> before and after.</span>}
              </label>
            )}
            <label className="inline">Route drop threshold
              <input type="number" min={0} max={100} step={0.5} value={pct} onChange={(e) => setPct(+e.target.value)} style={{ width: 80 }} /> %</label>
            <button className="btn primary" disabled={busy || (mode === "label" && !label)} onClick={run}>{busy ? "Verifying…" : "Verify"}</button>
          </div>
        </div>
      </Card>
      <ErrorNote error={err} />
      {!res && !err && <Empty>Verification fails when a BGP session, OSPF adjacency, interface or neighbor is lost, routes drop beyond the threshold, a device was not collected, or a state check newly fails.</Empty>}
      {res && (
        <>
          <div className={`verdict ${res.result}`}>
            <Badge status={res.result} />
            <strong>{WORD[res.result]}</strong>
            <span className="muted">
              <Link to={`/snapshots/${res.pre.id}`}>{fmtTime(res.pre.created_at)}</Link> → <Link to={`/snapshots/${res.post.id}`}>{fmtTime(res.post.created_at)}</Link>
            </span>
            <Link className="btn" style={{ marginLeft: "auto" }} to={`/compare?from=${res.pre.id}&to=${res.post.id}`}>Full diff</Link>
          </div>
          <div className="stats">
            <Stat label="Failing findings" value={res.fail} tone={res.fail ? "bad" : "good"} />
            <Stat label="Warnings" value={res.warning} tone={res.warning ? "warn" : undefined} />
            <Stat label="Changes" value={res.diff_summary.total} hint={`${res.diff_summary.devices_changed} device(s)`} />
            <Stat label="Not collected after" value={res.devices_not_collected_in_post.length} tone={res.devices_not_collected_in_post.length ? "bad" : "good"}
              hint={res.devices_not_collected_in_post.join(", ") || "all devices collected"} />
          </div>
          <Card title="Checks">
            <table className="tbl dense">
              <thead><tr><th>Result</th><th>Check</th><th className="num">Findings</th></tr></thead>
              <tbody>{checks.map(([id, c]) => (
                <tr key={id}><td><Badge status={c.status} /></td><td><strong>{c.name}</strong> <span className="mono muted">{id}</span>
                  {c.description && <div className="muted">{c.description}</div>}</td><td className="num">{c.violations.length}</td></tr>
              ))}</tbody>
            </table>
          </Card>
          {failing.map(([id, c]) => (
            <Card key={id} title={<><Badge status={c.status} /> {c.name}</>}>
              <ViolationTable rows={c.violations} />
            </Card>
          ))}
        </>
      )}
    </>
  );
}
