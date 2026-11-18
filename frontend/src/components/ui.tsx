import { ReactNode } from "react";

export function Badge({ status }: { status: string | null | undefined }) {
  const s = status || "unknown";
  return <span className={`badge badge-${s}`}>{s}</span>;
}

export function ChangeTag({ change }: { change: string }) {
  const sym = { added: "+", removed: "−", changed: "~" }[change] || "";
  return <span className={`chg chg-${change}`}>{sym} {change}</span>;
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className || ""}`}>
      {(title || actions) && (
        <header className="card-h">
          <h2>{title}</h2>
          <div className="row gap">{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: string }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="alert">{error}</div> : null;
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-h">
      <div>
        <h1>{title}</h1>
        {sub && <p className="muted">{sub}</p>}
      </div>
      <div className="row gap">{actions}</div>
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label={title}>
        <header className="card-h">
          <h2>{title}</h2>
          <button className="btn ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>
        {children}
      </div>
    </div>
  );
}
