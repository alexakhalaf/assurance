import { FormEvent, useEffect, useState } from "react";
import { useAuth } from "../auth";
import { Icon, Logo } from "../components/Icon";

interface Sso { enabled: boolean; name: string; local_login: boolean }

export default function Login() {
  const { login, ssoError } = useAuth();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState<Sso | null>(null);

  useEffect(() => {
    fetch("/api/v1/auth/oidc").then((r) => (r.ok ? r.json() : null)).then(setSso).catch(() => setSso(null));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await login(u, p);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const local = !sso || sso.local_login;
  const error = err || ssoError;
  return (
    <div className="login">
      <div className="login-art">
        <div className="brand-mark"><Logo size={28} /> Assurance</div>
        <div>
          <h2>Know exactly what changed in your network, and when.</h2>
          <p>Snapshots of every Cisco IOS-XE and NX-OS device, parsed with Genie, kept as history and compared row by row.</p>
          <ul>
            <li><Icon name="snapshots" /> Scheduled and on-demand snapshots</li>
            <li><Icon name="compare" /> Diffs between any two points in time</li>
            <li><Icon name="checks" /> Intent checks and pre/post change verification</li>
            <li><Icon name="topology" /> Topology, search, reports and a full REST API</li>
          </ul>
        </div>
        <span style={{ color: "#6f7c93", fontSize: 12 }}>Network assurance for Cisco IOS-XE and NX-OS</span>
      </div>
      <div className="login-side">
        <form className="login-card" onSubmit={submit}>
          <div>
            <h1>Sign in</h1>
            <p className="muted" style={{ margin: "4px 0 0" }}>Use your Assurance account{sso?.enabled ? " or single sign-on" : ""}.</p>
          </div>
          {sso?.enabled && <a className="btn primary sso" href="/api/v1/auth/oidc/login">Sign in with {sso.name}</a>}
          {sso?.enabled && local && <div className="divider"><span>or</span></div>}
          {local && <>
            <label>Username<input autoFocus={!sso?.enabled} value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" required /></label>
            <label>Password<input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required /></label>
          </>}
          {error && <div className="alert" role="alert">{error}</div>}
          {local && <button className={sso?.enabled ? "btn" : "btn primary"} disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>}
        </form>
      </div>
    </div>
  );
}
