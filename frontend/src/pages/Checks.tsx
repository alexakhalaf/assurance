import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, CheckSummary, Violation } from "../api";
import { useAuth } from "../auth";
import { axisStyle, Chart } from "../components/Chart";
import { Badge, Card, Empty, ErrorNote, Modal, PageHeader, Stat } from "../components/ui";
import { fmtTime, useApi } from "../hooks";

interface Rule {
  id: string; name: string; description: string | null; severity: string; kind: string; builtin: boolean;
  enabled: boolean; tables?: string[]; definition?: any; latest?: CheckSummary | null;
}

const EXAMPLE = `# Every NX-OS server port must run MTU 9216
kind: row
table: interfaces
where:
  - {field: description, op: regex, value: "^to .*esx"}
assert: {field: mtu, op: eq, value: 9216}
scope: {platform: nxos}
message: "{key} MTU is {mtu}, expected 9216"

# Other kinds:
#   kind: config_require   pattern: '^logging host '
#   kind: config_forbid    pattern: '^ip http server'
`;

export default function Checks() {
  const { can } = useAuth();
  const rules = useApi<{ snapshot: string | null; rules: Rule[] }>("/checks/rules");
  const trend = useApi<any[]>("/checks/trend", { snapshots: 30 });
  const [sel, setSel] = useState<string | null>(null);
  const [severity, setSeverity] = useState("");
  const [device, setDevice] = useState("");
  const params = useMemo(() => ({ rule: sel || undefined, severity: severity || undefined, device: device || undefined }), [sel, severity, device]);
  const viol = useApi<{ snapshot: string; created_at: string; summary: any; violations: Violation[] }>("/snapshots/latest/checks", params);
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggle(r: Rule) {
    await api(`/checks/rules/${encodeURIComponent(r.id)}`, { method: "PATCH", body: JSON.stringify({ enabled: !r.enabled }) });
    rules.reload();
  }
  async function regrade() {
    setBusy(true);
    try {
      await api("/checks/run", { method: "POST" });
      rules.reload(); viol.reload(); trend.reload();
    } finally {
      setBusy(false);
    }
  }

  const trendOption = useMemo(() => {
    const t = trend.data || [];
    const ax = axisStyle();
    return {
      tooltip: { trigger: "axis" },
      legend: { top: 0, textStyle: { color: ax.axisLabel.color } },
      xAxis: { type: "category", data: t.map((s) => fmtTime(s.created_at)), ...ax, splitLine: { show: false } },
      yAxis: { type: "value", minInterval: 1, ...ax },
      series: [
        { name: "Failing", type: "line", smooth: true, data: t.map((s) => s.fail), color: "#d9534f", areaStyle: { opacity: 0.08 } },
        { name: "Warnings", type: "line", smooth: true, data: t.map((s) => s.warning), color: "#e8a33d" },
      ],
    };
  }, [trend.data]);

  const list = rules.data?.rules || [];
  const sum = viol.data?.summary;
  const passing = list.filter((r) => r.enabled && r.latest?.status === "pass").length;
  const graded = list.filter((r) => r.enabled && r.latest).length;

  return (
    <>
      <PageHeader title="Intent checks" sub="Rules every snapshot is graded against"
        actions={can("operator") && <>
          <button className="btn" disabled={busy} onClick={regrade}>{busy ? "Grading…" : "Re-grade latest"}</button>
          {can("admin") && <button className="btn primary" onClick={() => setEditing("new")}>New rule</button>}
        </>} />
      <ErrorNote error={rules.error || viol.error} />
      <div className="stats">
        <Stat label="Compliance" value={graded ? `${Math.round((passing / graded) * 100)}%` : "—"} hint={`${passing} of ${graded} rules pass`}
          tone={graded && passing === graded ? "good" : "warn"} />
        <Stat label="Failing" value={sum?.fail ?? "—"} tone={sum?.fail ? "bad" : "good"} hint="violations, latest snapshot" />
        <Stat label="Warnings" value={sum?.warning ?? "—"} tone={sum?.warning ? "warn" : undefined} />
        <Stat label="Devices affected" value={sum ? Object.keys(sum.devices || {}).length : "—"} />
      </div>
      <div className="grid-2">
        <Card title="Rules" className="span-2">
          <div className="tbl-wrap">
            <table className="tbl dense">
              <thead><tr><th>Status</th><th>Rule</th><th>Severity</th><th>Source</th><th className="num">Violations</th><th></th></tr></thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id} className={`rule-row ${sel === r.id ? "sel" : ""}`} onClick={() => setSel(sel === r.id ? null : r.id)}>
                    <td>{!r.enabled ? <Badge status="disabled" /> : r.kind === "delta" ? <span className="muted">pre/post</span> : r.latest ? <Badge status={r.latest.status} /> : <span className="muted nowrap">not graded</span>}</td>
                    <td><strong>{r.name}</strong> <span className="muted mono">{r.id}</span>{r.description && <div className="muted">{r.description}</div>}</td>
                    <td>{r.severity}</td>
                    <td className="nowrap">{r.builtin ? "built-in" : "custom"}</td>
                    <td className="num">{r.latest?.violations ?? "—"}</td>
                    <td onClick={(e) => e.stopPropagation()}><div className="row gap end">
                      {can("admin") && <button className="btn sm ghost" onClick={() => toggle(r)}>{r.enabled ? "Disable" : "Enable"}</button>}
                      {can("admin") && !r.builtin && <button className="btn sm ghost" onClick={() => setEditing(r)}>Edit</button>}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="Violations over time" className="span-2">
          {(trend.data?.length || 0) < 2 ? <Empty>The trend appears after two graded snapshots.</Empty> : <Chart option={trendOption} height={220} />}
        </Card>
      </div>
      <Card title={<>Violations {sel && <span className="tag">{sel}</span>} {viol.data && <span className="muted">· snapshot {fmtTime(viol.data.created_at)}</span>}</>}
        actions={<>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">All severities</option><option value="fail">Fail</option><option value="warning">Warning</option>
          </select>
          <input placeholder="Device" value={device} onChange={(e) => setDevice(e.target.value)} />
          {sel && <button className="btn sm" onClick={() => setSel(null)}>Clear rule filter</button>}
        </>}>
        <ViolationTable rows={viol.data?.violations || []} />
      </Card>
      {editing && <RuleEditor rule={editing === "new" ? null : editing} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); regrade(); }} />}
    </>
  );
}

export function ViolationTable({ rows, hideDevice }: { rows: Violation[]; hideDevice?: boolean }) {
  if (!rows.length) return <Empty>No violations.</Empty>;
  return (
    <div className="tbl-wrap">
      <table className="tbl dense">
        <thead><tr>{rows[0].severity && <th>Severity</th>}{rows[0].rule && <th>Rule</th>}{!hideDevice && <th>Device</th>}<th>Object</th><th>Finding</th></tr></thead>
        <tbody>
          {rows.map((v, i) => (
            <tr key={`${v.rule}${v.device}${v.key}${i}`}>
              {v.severity && <td><Badge status={v.severity} /></td>}
              {v.rule && <td className="mono nowrap">{v.rule}</td>}
              {!hideDevice && <td className="nowrap"><Link to={`/devices/${v.device}`}>{v.device}</Link></td>}
              <td className="mono">{v.key}</td>
              <td>{v.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function toYaml(d: any): string {
  // The API accepts JSON too; JSON is valid YAML, so show existing rules as pretty JSON.
  return JSON.stringify(d, null, 2);
}

function RuleEditor({ rule, onClose, onSaved }: { rule: Rule | null; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(rule ? rule.id.replace(/^custom:/, "") : "");
  const [title, setTitle] = useState(rule?.name || "");
  const [description, setDescription] = useState(rule?.description || "");
  const [severity, setSeverity] = useState(rule?.severity || "fail");
  const [text, setText] = useState(rule ? toYaml(rule.definition) : EXAMPLE);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<any>(null);

  async function run() {
    setErr(null); setTest(null);
    try {
      setTest(await api("/checks/rules/test", { method: "POST", body: JSON.stringify({ definition_yaml: text, severity }) }));
    } catch (e: any) { setErr(e.message); }
  }
  async function save() {
    setErr(null);
    try {
      if (rule) {
        await api(`/checks/rules/${encodeURIComponent(rule.id)}`, { method: "PATCH",
          body: JSON.stringify({ title, description, severity, definition_yaml: text }) });
      } else {
        await api("/checks/rules", { method: "POST", body: JSON.stringify({ name, title: title || null, description: description || null, severity, definition_yaml: text }) });
      }
      onSaved();
    } catch (e: any) { setErr(e.message); }
  }
  async function remove() {
    if (!rule || !confirm(`Delete rule ${rule.id}?`)) return;
    await api(`/checks/rules/${encodeURIComponent(rule.id)}`, { method: "DELETE" });
    onSaved();
  }

  return (
    <Modal title={rule ? `Edit ${rule.id}` : "New custom rule"} onClose={onClose}>
      <div className="form">
        <div className="form-grid">
          <label>Name (id)<input value={name} disabled={!!rule} onChange={(e) => setName(e.target.value)} placeholder="server-port-mtu" /></label>
          <label>Severity<select value={severity} onChange={(e) => setSeverity(e.target.value)}><option value="fail">Fail</option><option value="warning">Warning</option></select></label>
          <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Server ports run jumbo MTU" /></label>
          <label>Description<input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        </div>
        <label>Definition (YAML or JSON)<textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} /></label>
        <ErrorNote error={err} />
        {test && (
          <div className={`verdict ${test.status === "pass" ? "pass" : test.status}`}>
            <Badge status={test.status} />
            <span>{test.violations.length} violation(s) on the latest snapshot{test.error ? ` · ${test.error}` : ""}</span>
          </div>
        )}
        {test?.violations?.length > 0 && <ViolationTable rows={test.violations.slice(0, 50)} />}
        <div className="row gap end">
          {rule && <button className="btn danger" onClick={remove}>Delete</button>}
          <button className="btn" onClick={run}>Test against latest</button>
          <button className="btn primary" disabled={!rule && !name} onClick={save}>Save</button>
        </div>
      </div>
    </Modal>
  );
}
