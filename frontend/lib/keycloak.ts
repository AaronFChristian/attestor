/**
 * Keycloak client singleton.
 *
 * Why a singleton and not "new Keycloak() inside the component": React
 * Strict Mode (enabled in next.config.js) double-invokes effects in dev,
 * and Next.js hot-reloads components frequently. Re-instantiating the
 * Keycloak adapter on every render/remount causes duplicate init() calls,
 * which the adapter itself throws on ("A 'Keycloak' instance can only be
 * initialized once"). A module-level singleton, created exactly once when
 * this module is first imported, avoids that entirely — this is the
 * pattern the official keycloak-js docs recommend for SPA frameworks with
 * hot reload / strict mode.
 *
 * Client: "attestor-frontend", a PUBLIC client (no secret) already
 * defined in keycloak/attestor-realm.json since Day 1. Public + PKCE is
 * the correct, secure flow for a browser app that can't safely hold a
 * client secret — never add a secret to this file.
 */
"use client";

import Keycloak from "keycloak-js";

const keycloakConfig = {
  url: process.env.NEXT_PUBLIC_KEYCLOAK_URL || "http://localhost:8080",
  realm: process.env.NEXT_PUBLIC_KEYCLOAK_REALM || "attestor",
  clientId: process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID || "attestor-frontend",
};

let keycloakInstance: Keycloak | null = null;

export function getKeycloak(): Keycloak {
  if (typeof window === "undefined") {
    // keycloak-js touches window/localStorage internally — this guard
    // exists so importing this module during Next.js's server-side
    // render pass doesn't crash. Actual init only ever happens client-side
    // (see auth-context.tsx, which is itself a "use client" component).
    throw new Error("getKeycloak() must only be called in a browser context.");
  }
  if (!keycloakInstance) {
    keycloakInstance = new Keycloak(keycloakConfig);
  }
  return keycloakInstance;
}

export type AttestorRole = "model_owner" | "validator" | "mrm_head" | "auditor";

const GOVERNANCE_ROLES: AttestorRole[] = [
  "model_owner",
  "validator",
  "mrm_head",
  "auditor",
];

/**
 * Extracts the single governance role from the token's realm_access.roles.
 * Mirrors app/core/auth.py::_extract_role on the backend exactly — if a
 * user somehow holds zero or multiple governance roles, that's a
 * segregation-of-duties problem the backend already refuses to serve, so
 * the frontend surfaces it as an explicit state rather than guessing.
 */
export function extractRole(keycloak: Keycloak): AttestorRole | null {
  const roles: string[] = keycloak.tokenParsed?.realm_access?.roles || [];
  const matched = GOVERNANCE_ROLES.filter((r) => roles.includes(r));
  if (matched.length !== 1) {
    return null;
  }
  return matched[0];
}
