"use client";

import { useEffect, type ReactNode } from "react";
import { useAuth } from "@/lib/auth-context";
import { RoleBanner } from "@/components/RoleBanner";
import Link from "next/link";

const ROLE_FRAMING: Record<string, string> = {
  model_owner:
    "You're signed in as a Model Owner. You can register models and see findings raised against them — you cannot validate your own models or close a finding.",
  validator:
    "You're signed in as a Validator. Trigger validation runs and work the review queue: every AI-proposed finding needs your accept, reject, or amend, with a written rationale.",
  mrm_head:
    "You're signed in as MRM Head. Set materiality thresholds and sign off finalized reports — the human accountability anchor for every validation.",
  auditor:
    "You're signed in as an Auditor. Everything here is read-only for you, on purpose — including the hash-chained audit log.",
};

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const { initialized, authenticated, role, name, email, login, logout } = useAuth();

  useEffect(() => {
    if (initialized && !authenticated) {
      login();
    }
  }, [initialized, authenticated, login]);

  if (!initialized) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-400">
        Checking session…
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-400">
        Redirecting to sign in…
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/60">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Link href="/" className="font-medium tracking-wide text-slate-200">
            ATTESTOR
          </Link>
          <nav className="flex items-center gap-6 text-sm">
            <Link href="/dashboard" className="text-slate-300 hover:text-white">
              Models
            </Link>
            <span className="text-slate-600" title="Coming in the next build phase">
              Pipeline visualizer
            </span>
            <div className="flex items-center gap-3 border-l border-slate-800 pl-6">
              <span className="text-slate-400">
                {name} · <span className="text-slate-500">{email}</span>
              </span>
              <button
                onClick={logout}
                className="rounded-md bg-slate-800 px-3 py-1.5 text-slate-200 hover:bg-slate-700"
              >
                Sign out
              </button>
            </div>
          </nav>
        </div>
      </header>

      {role && (
        <RoleBanner role={role} text={ROLE_FRAMING[role]} />
      )}
      {!role && (
        <div className="bg-red-950 px-6 py-3 text-center text-sm text-red-200">
          Your account doesn&rsquo;t hold exactly one governance role. This is a
          segregation-of-duties problem — contact an MRM Head to fix your role
          assignment in Keycloak. No dashboard actions will work until this is
          resolved.
        </div>
      )}

      <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
    </div>
  );
}
