import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useDebounced, useLocal } from "../hooks";
import { Icon } from "./Icon";

export interface Col<R = any> {
  key: string;
  label: string;
  render?: (row: R) => ReactNode;
  /** Plain text of the cell, for client-side filter/sort and the tooltip. */
  text?: (row: R) => string;
  /** Value to sort by on the client (defaults to the raw number, else the text). */
  sortValue?: (row: R) => any;
  mono?: boolean;
  num?: boolean;
  wrap?: boolean;
  /** "text" (contains), "select" (exact, with options), or false (no filter). Default text. */
  filter?: "text" | "select" | false;
  options?: string[];
  sortable?: boolean;
  /** Hidden until enabled in the column chooser. */
  hidden?: boolean;
  width?: number | string;
}

interface Common<R> {
  columns: Col<R>[];
  rowKey: (row: R, i: number) => string;
  onRowClick?: (row: R) => void;
  toolbar?: ReactNode;
  /** Remembers visible columns and page size per grid. */
  storageKey: string;
  pageSize?: number;
  emptyText?: ReactNode;
  onExport?: (state: GridQuery) => void;
  searchPlaceholder?: string;
  autoHeight?: boolean;
}

export interface GridQuery {
  q: string;
  filters: Record<string, string>;
  sort: string | null;
  order: "asc" | "desc";
  limit: number;
  offset: number;
}

type Props<R> = Common<R> & (
  | { rows: R[]; loading?: boolean; error?: string | null; source?: undefined }
  | { source: { path: string; params?: Record<string, any>; map: (q: GridQuery) => Record<string, any> }; rows?: undefined; loading?: undefined; error?: undefined }
);

export function DataGrid<R = any>(props: Props<R>) {
  const { columns, rowKey, onRowClick, toolbar, storageKey, emptyText, onExport, searchPlaceholder, autoHeight } = props;
  const [hiddenCols, setHiddenCols] = useLocal<string[]>(`grid.${storageKey}.hidden`, columns.filter((c) => c.hidden).map((c) => c.key));
  const [limit, setLimit] = useLocal<number>(`grid.${storageKey}.limit`, props.pageSize || 50);
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<string | null>(null);
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [offset, setOffset] = useState(0);
  const [showCols, setShowCols] = useState(false);
  const [showFilters, setShowFilters] = useState(true);
  const dq = useDebounced(q, 300);
  const dfilters = useDebounced(filters, 300);
  const visible = columns.filter((c) => !hiddenCols.includes(c.key));
  const query: GridQuery = { q: dq, filters: dfilters, sort, order, limit, offset };

  useEffect(() => { setOffset(0); }, [dq, JSON.stringify(dfilters), sort, order, limit, props.source?.path, JSON.stringify(props.source?.params)]);

  // ---- server source
  const [srv, setSrv] = useState<{ items: R[]; total: number } | null>(null);
  const [srvErr, setSrvErr] = useState<string | null>(null);
  const [srvLoading, setSrvLoading] = useState(false);
  const seq = useRef(0);
  const srcKey = props.source ? props.source.path + JSON.stringify(props.source.params || {}) + JSON.stringify(query) : "";
  useEffect(() => {
    if (!props.source) return;
    const my = ++seq.current;
    setSrvLoading(true);
    api<{ items: R[]; total: number }>(props.source.path, { params: { ...(props.source.params || {}), ...props.source.map(query) } })
      .then((d) => { if (my === seq.current) { setSrv(d); setSrvErr(null); } })
      .catch((e) => { if (my === seq.current) setSrvErr(e.message); })
      .finally(() => { if (my === seq.current) setSrvLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [srcKey]);

  // ---- client source
  const local = useMemo(() => {
    if (!props.rows) return null;
    const txt = (c: Col<R>, r: R) => (c.text ? c.text(r) : String((r as any)[c.key] ?? ""));
    let rows = props.rows;
    const needle = dq.toLowerCase();
    if (needle) rows = rows.filter((r) => columns.some((c) => txt(c, r).toLowerCase().includes(needle)));
    for (const [k, v] of Object.entries(dfilters)) {
      if (!v) continue;
      const c = columns.find((x) => x.key === k);
      if (!c) continue;
      const vv = v.toLowerCase();
      rows = rows.filter((r) => (c.filter === "select" ? txt(c, r).toLowerCase() === vv : txt(c, r).toLowerCase().includes(vv)));
    }
    if (sort) {
      const c = columns.find((x) => x.key === sort);
      if (c) {
        const val = (r: R) => {
          if (c.sortValue) return c.sortValue(r) ?? "";
          const raw = (r as any)[c.key];
          return typeof raw === "number" ? raw : txt(c, r).toLowerCase();
        };
        rows = [...rows].sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * (order === "asc" ? 1 : -1); });
      }
    }
    return { total: rows.length, items: rows.slice(offset, offset + limit) };
  }, [props.rows, dq, JSON.stringify(dfilters), sort, order, offset, limit, columns]);

  const data = local || srv;
  const loading = props.rows ? props.loading : srvLoading;
  const error = props.rows ? props.error : srvErr;
  const total = data?.total ?? 0;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));

  function clickSort(c: Col<R>) {
    if (c.sortable === false) return;
    if (sort !== c.key) { setSort(c.key); setOrder("asc"); }
    else if (order === "asc") setOrder("desc");
    else { setSort(null); setOrder("asc"); }
  }
  const activeFilters = Object.values(filters).filter(Boolean).length + (q ? 1 : 0);

  return (
    <div className="grid">
      <div className="grid-toolbar">
        <span className="count">{data ? `${total.toLocaleString()} row${total === 1 ? "" : "s"}` : loading ? "Loading…" : ""}</span>
        {toolbar}
        <div className="input-icon"><Icon name="search" size={14} />
          <input placeholder={searchPlaceholder || "Search all columns"} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search table" style={{ width: 220 }} />
        </div>
        {activeFilters > 0 && <button className="btn sm ghost" onClick={() => { setQ(""); setFilters({}); }}>Clear filters ({activeFilters})</button>}
        <button className={`btn sm ${showFilters ? "" : "ghost"}`} onClick={() => setShowFilters(!showFilters)} title="Column filters"><Icon name="filter" size={14} /></button>
        <div style={{ position: "relative" }}>
          <button className="btn sm" onClick={() => setShowCols(!showCols)} title="Columns"><Icon name="columns" size={14} /> Columns</button>
          {showCols && (
            <div className="col-menu" onMouseLeave={() => setShowCols(false)}>
              {columns.map((c) => (
                <label key={c.key}><input type="checkbox" checked={!hiddenCols.includes(c.key)}
                  onChange={() => setHiddenCols(hiddenCols.includes(c.key) ? hiddenCols.filter((x) => x !== c.key) : [...hiddenCols, c.key])} />{c.label}</label>
              ))}
            </div>
          )}
        </div>
        {onExport && <button className="btn sm" onClick={() => onExport(query)}><Icon name="download" size={14} /> CSV</button>}
      </div>
      {loading ? <div className="loading-bar" /> : <div style={{ height: 2 }} />}
      {error && <div className="alert" style={{ margin: 12 }}>{error}</div>}
      <div className={`grid-scroll ${autoHeight ? "auto" : ""}`}>
        <table>
          <thead>
            <tr>
              {visible.map((c) => (
                <th key={c.key} className={c.sortable === false ? "nosort" : ""} style={{ width: c.width }} aria-sort={sort === c.key ? (order === "asc" ? "ascending" : "descending") : undefined}>
                  <div className={`th ${sort === c.key ? "sorted" : ""}`} onClick={() => clickSort(c)} style={{ justifyContent: c.num ? "flex-end" : undefined }}>
                    {c.label}
                    {sort === c.key && <Icon name="chevron" size={12} className={order === "asc" ? "" : "flip"} />}
                  </div>
                </th>
              ))}
            </tr>
            {showFilters && (
              <tr className="filters-row">
                {visible.map((c) => (
                  <th key={c.key}>
                    {c.filter === false ? null : c.filter === "select" ? (
                      <select value={filters[c.key] || ""} onChange={(e) => setFilters({ ...filters, [c.key]: e.target.value })} aria-label={`Filter ${c.label}`}>
                        <option value="">All</option>
                        {(c.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    ) : (
                      <input value={filters[c.key] || ""} placeholder="Filter" onChange={(e) => setFilters({ ...filters, [c.key]: e.target.value })} aria-label={`Filter ${c.label}`} />
                    )}
                  </th>
                ))}
              </tr>
            )}
          </thead>
          <tbody>
            {data && data.items.length === 0 && (
              <tr><td className="empty-cell" colSpan={visible.length}>{emptyText || "No rows match."}</td></tr>
            )}
            {data?.items.map((r, i) => (
              <tr key={rowKey(r, i)} className={onRowClick ? "clickable" : ""} onClick={onRowClick ? () => onRowClick(r) : undefined}>
                {visible.map((c) => (
                  <td key={c.key} className={[c.mono ? "mono" : "", c.num ? "num" : "", c.wrap ? "wrap-cell" : ""].join(" ")}
                    title={c.text ? c.text(r) : c.render ? undefined : plain((r as any)[c.key])}>
                    {c.render ? c.render(r) : fmtCell((r as any)[c.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid-foot">
        <span>Rows per page</span>
        <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="Rows per page">
          {[25, 50, 100, 250, 500].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <div className="pager">
          <span>{total ? `${(offset + 1).toLocaleString()}–${Math.min(offset + limit, total).toLocaleString()} of ${total.toLocaleString()}` : ""}</span>
          <button className="btn sm icon" disabled={page <= 1} onClick={() => setOffset(0)} aria-label="First page">«</button>
          <button className="btn sm icon" disabled={page <= 1} onClick={() => setOffset(Math.max(0, offset - limit))} aria-label="Previous page">‹</button>
          <span>Page {page} of {pages}</span>
          <button className="btn sm icon" disabled={page >= pages} onClick={() => setOffset(offset + limit)} aria-label="Next page">›</button>
          <button className="btn sm icon" disabled={page >= pages} onClick={() => setOffset((pages - 1) * limit)} aria-label="Last page">»</button>
        </div>
      </div>
    </div>
  );
}

function plain(v: any): string | undefined {
  if (v === null || v === undefined || v === "") return undefined;
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export function fmtCell(v: any): ReactNode {
  if (v === null || v === undefined || v === "") return <span className="faint">—</span>;
  if (typeof v === "boolean") return <span className={v ? "good" : "bad"}>{v ? "yes" : "no"}</span>;
  if (Array.isArray(v)) {
    if (!v.length) return <span className="faint">—</span>;
    if (typeof v[0] === "object") return <div className="list-cell">{v.map((x, i) => <span key={i}>{Object.values(x).filter((y) => y !== null && y !== undefined).join(" via ")}</span>)}</div>;
    return v.join(", ");
  }
  if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(", ");
  return String(v);
}

/** Map grid state to the state-table API: column filters become ~contains (or exact for selects). */
export function tableQuery(columns: Col[], q: GridQuery): Record<string, any> {
  const p: Record<string, any> = { q: q.q || undefined, sort: q.sort || undefined, order: q.order, limit: q.limit, offset: q.offset };
  for (const [k, v] of Object.entries(q.filters)) {
    if (!v) continue;
    const c = columns.find((x) => x.key === k);
    const exact = c?.filter === "select";
    if (k === "device") p.device = exact ? v : `~${v}`;
    else p[k] = exact ? v : `~${v}`;
  }
  return p;
}
