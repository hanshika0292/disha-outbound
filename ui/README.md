# Disha UI

Next.js 16 (App Router) frontend for the Disha API. See the root `README.md` for the full setup.

Pages: dashboard (`/`), leads, drafts, inbox, sequence, LinkedIn queue, reports, settings, admin console, login, change password, and a public landing page with a waitlist form (`/welcome`).

## Run locally

```bash
cp .env.example .env.local   # sets NEXT_PUBLIC_API_BASE (default http://localhost:8000)
npm install
npm run dev                  # http://localhost:3000
```

The API must be running. Log in with a user created through the API (see the root README).

## Auth

`POST /api/v1/auth/login` on the API returns a signed per-user token, which the UI keeps in `localStorage` and sends as a bearer token. A `disha_session` cookie, set by `app/api/auth/login`, only gates page navigation in `middleware.ts`; the API token is the real security boundary.

## Build

`npm run build` produces a standalone server (`output: "standalone"`). `ui/Dockerfile` builds that image; `NEXT_PUBLIC_API_BASE` is baked in at build time.
