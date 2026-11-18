import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getToken, setToken, User } from "./api";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (role: User["role"]) => boolean;
}

const Ctx = createContext<AuthState>(null as any);
const RANK = { viewer: 0, operator: 1, admin: 2 };

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api<User>("/auth/me").then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onLogout = () => setUser(null);
    window.addEventListener("assurance:logout", onLogout);
    return () => window.removeEventListener("assurance:logout", onLogout);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api<{ token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    setToken(r.token);
    setUser(r.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {
      /* already gone */
    }
    setToken(null);
    setUser(null);
  }, []);

  const can = useCallback((role: User["role"]) => !!user && RANK[user.role] >= RANK[role], [user]);
  return <Ctx.Provider value={{ user, loading, login, logout, can }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
