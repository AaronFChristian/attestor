/**
 * Auth context. Wraps the entire app (see providers.tsx). Responsibilities:
 *
 * 1. Initialize Keycloak exactly once on mount, using check-sso (not
 *    login-required) — this is what lets the landing page render for an
 *    unauthenticated visitor instead of force-redirecting to login before
 *    they've seen what Attestor even is. The explainer screen depends on
 *    this choice.
 * 2. Keep the access token fresh — refreshes proactively before expiry
 *    rather than waiting for a 401, since a 401 mid-review-action would
 *    lose whatever the user was doing.
 * 3. Expose { authenticated, role, user, token, login, logout } to every
 *    component via useAuth().
 */
"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { getKeycloak, extractRole, type AttestorRole } from "./keycloak";

interface AuthState {
  initialized: boolean;
  authenticated: boolean;
  role: AttestorRole | null;
  email: string | null;
  name: string | null;
  token: string | null;
  login: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [initialized, setInitialized] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [role, setRole] = useState<AttestorRole | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);

  // Guards against React Strict Mode's double-mount in dev running init()
  // twice on the same Keycloak singleton, which throws.
  const initStarted = useRef(false);

  useEffect(() => {
    if (initStarted.current) return;
    initStarted.current = true;

    const keycloak = getKeycloak();

    keycloak
      .init({
        onLoad: "check-sso",
        pkceMethod: "S256",
        silentCheckSsoRedirectUri:
          typeof window !== "undefined"
            ? `${window.location.origin}/silent-check-sso.html`
            : undefined,
      })
      .then((isAuthenticated) => {
        setAuthenticated(isAuthenticated);
        if (isAuthenticated) {
          setRole(extractRole(keycloak));
          setEmail(keycloak.tokenParsed?.email ?? null);
          setName(keycloak.tokenParsed?.name ?? null);
          setToken(keycloak.token ?? null);
        }
        setInitialized(true);
      })
      .catch((err) => {
        console.error("Keycloak init failed:", err);
        setInitialized(true);
      });

    // Proactive refresh: fires every 30s, only actually refreshes if the
    // token is within 60s of expiry. Losing a review action mid-edit to a
    // silent 401 would be a genuinely bad demo moment.
    const refreshInterval = setInterval(() => {
      keycloak
        .updateToken(60)
        .then((refreshed) => {
          if (refreshed) {
            setToken(keycloak.token ?? null);
          }
        })
        .catch(() => {
          setAuthenticated(false);
        });
    }, 30000);

    return () => clearInterval(refreshInterval);
  }, []);

  const login = () => getKeycloak().login();
  const logout = () => getKeycloak().logout({ redirectUri: window.location.origin });

  return (
    <AuthContext.Provider
      value={{ initialized, authenticated, role, email, name, token, login, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth() must be used within <AuthProvider>.");
  }
  return ctx;
}
