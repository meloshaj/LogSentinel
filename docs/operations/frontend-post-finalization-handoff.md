# Post-finalization frontend handoff

**Date:** 2026-09-29  
**Production site:** <https://logsentinel.pages.dev>  
**Cloudflare Pages project:** `logsentinel`

## Production blocker and resolution

The application root in `src/App.tsx` mounted `GoogleOAuthProvider` on every
route with `VITE_GOOGLE_CLIENT_ID || ""`. Login and registration also called
`useGoogleLogin` unconditionally. Since the production Pages environment has
no Google client ID, the Google Identity Services script attempted
`google.accounts.oauth2.initTokenClient` without `client_id` and threw during
ordinary page load.

The root provider was removed. Google setup now lives inside a guarded Google
button component and requires a trimmed, non-empty client ID with the feature
enabled. With the actual Pages contract, Google is disabled and its buttons
are hidden. A local error boundary contains integration rendering failures.
No placeholder ID or credential was added.

The production Pages project supplies its existing public `VITE_AZURE_*`
authentication variables. The frontend now accepts those aliases for MSAL,
so Microsoft auth remains enabled in this deployment. The registered
`/auth/callback` is routed to the built MSAL redirect bridge. Password sign-in
remains available.

## Verification

- `pnpm typecheck`: passed.
- Full `pnpm test`: 15 files, 105 tests passed.
- Targeted auth regression tests: 3 files, 37 tests passed; missing Google ID
  regression also rerun directly and passed.
- Production Vite build: passed using the Cloudflare Pages production public
  environment contract, in an isolated Vite env directory to exclude local
  `.env` values.
- Live browser smoke at `/`, `/login`, `/register`, and `/forgot-password`:
  all routes returned HTTP 200 and rendered. `/` redirected to the normal login
  screen. Login showed password and enabled Microsoft sign-in; registration
  showed enabled Microsoft sign-in.
- Live browser captured no Google GSI requests, no Google `client_id` errors,
  no uncaught page errors, and no React Router application error.
- Microsoft redirect bridge route returned HTTP 200 after Cloudflare's clean
  URL redirect; its query string was preserved.

## Deployment

The corrected production build was deployed to Cloudflare Pages project
`logsentinel`, production branch `main`.

**Deployment ID:** `86a33cc1-7a30-4028-aa2d-1db456e5c8ed`  
**Deployment URL:** <https://86a33cc1.logsentinel.pages.dev>

No backend, database, object storage, backup architecture, WAL configuration,
or production container changes were made for this fix.
