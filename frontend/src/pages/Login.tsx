import { FormEvent, useEffect, useState } from "react";
import { useAuth } from "../auth";

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
      <form className="card login-card" onSubmit={submit}>
        <div className="brand big">
          <img src="/favicon.svg" alt="" width={34} height={34} />
          <span>Assurance</span>
        </div>
        <p className="muted">Network snapshot and change assurance</p>
        {sso?.enabled && (
          <a className="btn primary sso" href="/api/v1/auth/oidc/login">Sign in with {sso.name}</a>
        )}
        {sso?.enabled && local && <div className="divider"><span>or</span></div>}
        {local && <>
          <label>Username<input autoFocus={!sso?.enabled} value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" required /></label>
          <label>Password<input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required /></label>
        </>}
        {error && <div className="alert">{error}</div>}
        {local && <button className={sso?.enabled ? "btn" : "btn primary"} disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>}
      </form>
    </div>
  );
}
