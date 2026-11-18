import { ReactNode, useEffect } from "react";
import { Link } from "react-router-dom";
import { Icon } from "./Icon";

export function Badge({ status, label }: { status: string | null | undefined; label?: string }) {
  const s = (status || "unknown").toString();
  return <span className={`badge badge-${s.toLowerCase()}`}>{label ?? s}</span>;
}

export function ChangeTag({ change }: { change: string }) {
  const sym = { added: "+", removed: "−", changed: "~" }[change] || "";
  return <span className={`chg chg-${change}`}>{sym} {change === "changed" ? "modified" : change}</span>;
}

export function Card({ title, actions, children, className, sub }: { title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className || ""}`}>
      {(title || actions) && (
        <header className="card-h">
          <h2>{title}{sub && <span className="muted">{sub}</span>}</h2>
          <div className="row gap">{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone, to, icon }: { label: string; value: ReactNode; hint?: ReactNode; tone?: string; to?: string; icon?: string }) {
  const body = (
    <>
      <div className="stat-label">{tone && <span className="dot" />}{icon && <Icon name={icon} size={13} />}{label}</div>
      <div className="stat-value">{value}</div>
      {hint !== undefined && <div className="stat-hint">{hint}</div>}
    </>
  );
  const cls = `stat ${tone ? `stat-${tone}` : ""}`;
  return to ? <Link className={cls} to={to}>{body}</Link> : <div className={cls}>{body}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function EmptyState({ icon = "facts", title, children, action }: { icon?: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <Icon name={icon} size={32} strokeWidth={1.4} />
      <h3>{title}</h3>
      {children && <div>{children}</div>}
      {action}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null | undefined }) {
  return error ? <div className="alert" role="alert">{error}</div> : null;
}

export function PageHeader({ title, sub, actions, crumbs }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumbs?: [string, string?][] }) {
  return (
    <div className="page-h">
      <div style={{ minWidth: 0 }}>
        {crumbs && (
          <div className="crumbs">
            {crumbs.map(([label, to], i) => (
              <span key={i} className="row gap" style={{ gap: 6 }}>
                {i > 0 && <Icon name="chevronRight" size={12} />}
                {to ? <Link to={to}>{label}</Link> : <span>{label}</span>}
              </span>
            ))}
          </div>
        )}
        <h1>{title}</h1>
        {sub && <p className="muted">{sub}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEscape(onClose);
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="card-h">
          <h2>{title}</h2>
          <button className="btn ghost icon" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children, actions }: { title: ReactNode; onClose: () => void; children: ReactNode; actions?: ReactNode }) {
  useEscape(onClose);
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <header className="card-h">
          <h2>{title}</h2>
          <div className="row gap">{actions}<button className="btn ghost icon" onClick={onClose} aria-label="Close"><Icon name="x" /></button></div>
        </header>
        <div className="body">{children}</div>
      </aside>
    </>
  );
}

export function UTabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode; n?: number | string }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="utabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={t.id === value} className={`utab ${t.id === value ? "active" : ""}`} onClick={() => onChange(t.id)}>
          {t.label}{t.n !== undefined && <span className="n">{t.n}</span>}
        </button>
      ))}
    </div>
  );
}

export function BarList({ items, max, onClick, color, limit = 10 }: { items: [string, number][]; max?: number; onClick?: (k: string) => void; color?: string; limit?: number }) {
  const m = max ?? Math.max(1, ...items.map(([, v]) => v));
  const shown = items.slice(0, limit);
  const rest = items.slice(limit);
  return (
    <div className="bar-list">
      {shown.map(([k, v]) => (
        <div className="bl-row" key={k}>
          <span className="truncate" title={k}>{onClick ? <a href="#" onClick={(e) => { e.preventDefault(); onClick(k); }}>{k}</a> : k}</span>
          <div className="bl-bar"><span style={{ width: `${(v / m) * 100}%`, background: color }} /></div>
          <span className="num">{v.toLocaleString()}</span>
        </div>
      ))}
      {rest.length > 0 && (
        <div className="bl-row muted">
          <span>{rest.length.toLocaleString()} more</span><span />
          <span className="num">{rest.reduce((a, [, v]) => a + v, 0).toLocaleString()}</span>
        </div>
      )}
    </div>
  );
}

export function Loading() {
  return <div className="muted" style={{ padding: 24 }}>Loading…</div>;
}
