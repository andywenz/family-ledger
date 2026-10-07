import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, refreshSession, setAccessToken, setUnauthenticatedHandler, type Me } from "../api/client";

type Ctx = {
  me: Me | null;
  ready: boolean;
  env: string;
  reloadMe: () => Promise<void>;
  signedIn: (accessToken: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthCtx = createContext<Ctx | null>(null);

function csrf(): string {
  return document.cookie.split("; ").find((c) => c.startsWith("ledger_csrf="))?.split("=")[1] ?? "";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [env, setEnv] = useState("");

  const reloadMe = useCallback(async () => {
    setMe(await api.get<Me>("/me"));
  }, []);

  useEffect(() => {
    setUnauthenticatedHandler(() => {
      setAccessToken(null);
      setMe(null);
    });
    (async () => {
      try {
        const h = await api.get<{ env: string }>("/health");
        setEnv(h.env);
      } catch {
        /* 健康检查失败不阻止登录页显示 */
      }
      try {
        if (await refreshSession()) await reloadMe();
      } finally {
        setReady(true);
      }
    })();
  }, [reloadMe]);

  const signedIn = useCallback(
    async (token: string) => {
      setAccessToken(token);
      await reloadMe();
    },
    [reloadMe],
  );

  const logout = useCallback(async () => {
    await fetch("/v1/auth/logout", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": csrf() } });
    setAccessToken(null);
    setMe(null);
  }, []);

  return <AuthCtx.Provider value={{ me, ready, env, reloadMe, signedIn, logout }}>{children}</AuthCtx.Provider>;
}

export function useAuth(): Ctx {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("AuthProvider missing");
  return ctx;
}
