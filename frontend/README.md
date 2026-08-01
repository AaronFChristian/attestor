# Attestor Frontend — Day 4

Next.js 16 + React 19 + TypeScript + Tailwind 4, with real Keycloak OIDC
login via the official `keycloak-js` adapter (not hand-rolled).

## What exists right now

- **Landing page** (`/`) — the pitch, for a first-time viewer, before login
- **Real Keycloak login** — public client (`attestor-frontend`, PKCE, S256),
  already defined in `keycloak/attestor-realm.json` since Day 1
- **Role-aware authenticated shell** (`/dashboard`) — persona-specific
  framing pulled from the actual token's realm role
- **Live governed-models list** — real `GET /models` call, proving the
  Keycloak-issued token flows through to the FastAPI backend correctly

## What's NOT here yet (Day 5)

- Findings review workspace (accept/reject/amend UI)
- Live LangGraph pipeline visualizer
- Guided demo mode

## Setup

```bash
cd frontend
npm install
cp .env.local.example .env.local
```

The defaults in `.env.local.example` assume the backend stack from Days
1-3 is already running via `docker compose up` in the parent directory —
Keycloak on `:8080`, the API on `:8000`.

```bash
npm run dev
```

Open `http://localhost:3000`.

## Verification I could NOT do from a sandbox

Everything below was verified: `npm install` succeeds, `npm run build`
produces a clean production build with all three routes compiling
(`/`, `/dashboard`, `/_not-found`), TypeScript passes strict mode with zero
errors.

What I could **not** verify without your live Keycloak instance: the actual
login redirect round-trip, token refresh behavior, and the real API call
succeeding with a real bearer token. That first end-to-end login is
genuinely untested code touching your infrastructure for the first time —
same situation as every other new piece this build has shipped. Expect to
paste me whatever the browser console or network tab shows if it doesn't
work first try; that's been the actual pattern all session, not a sign
something's unusually wrong.

## A known, deliberate gap: npm audit

`npm audit` reports 3 high-severity findings — all inside Next.js's own
bundled build tooling (`postcss` CSS processing, `sharp` image
optimization), not in any code this app calls directly. npm's suggested
fix (`npm audit fix --force`) would downgrade Next.js from 16 to 9 to
"solve" this, which is a materially worse trade than the vulnerabilities
themselves for a local dev/demo build. Noted here rather than either
silently ignored or blindly "fixed."

## Design notes worth remembering for an interview

- **`keycloak-js` singleton pattern** (`lib/keycloak.ts`): React Strict
  Mode double-invokes effects in dev, and the adapter throws if
  initialized twice. A module-level singleton, not a per-component
  instance, is what the official docs recommend for exactly this reason.
- **`check-sso`, not `login-required`**: lets the landing page render for
  an unauthenticated visitor instead of force-redirecting before anyone's
  seen what the product is. This was a deliberate UX decision, not a
  default left unconfigured.
- **Proactive token refresh** (`auth-context.tsx`): refreshes 60s before
  expiry on a 30s poll, rather than waiting for a 401. Losing an in-progress
  finding review to a silent auth failure would be a genuinely bad demo
  moment, so this is designed around that failure mode specifically.
