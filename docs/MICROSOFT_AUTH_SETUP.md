# Microsoft Entra authentication setup

LogSentinel uses two Entra app registrations. The browser SPA obtains a delegated access token for the LogSentinel API with MSAL's authorization-code flow with PKCE. The backend validates that token and exchanges it for the existing internal LogSentinel JWT. The Microsoft token is never an application session token.

## 1. Choose the tenant policy

Choose one supported account population and use it consistently for both app registrations, the SPA authority, and the backend:

| Account population | Entra supported account type | Authority tenant segment | `AZURE_TENANT_ID` |
| --- | --- | --- | --- |
| One organization | Accounts in this organizational directory only | `<tenant-guid>` | The same tenant GUID |
| Any Entra organization | Accounts in any organizational directory | `organizations` | `organizations` |
| Organizations and personal accounts | Accounts in any organizational directory and personal Microsoft accounts | `common` | `common` |
| Personal Microsoft accounts only | Personal Microsoft accounts | `consumers` | `consumers` |

Use **One organization** unless there is an explicit product requirement for broader access. LogSentinel has no implicit tenant default: omitting `AZURE_TENANT_ID` disables Microsoft authentication instead of silently selecting `common`.

For a multitenant registration, set `AZURE_ALLOWED_TENANTS` to a comma-separated list of tenant GUIDs when LogSentinel should accept only selected directories. The allow-list is applied in addition to `AZURE_TENANT_ID`; incompatible settings can reject every tenant. A specific `AZURE_TENANT_ID` is always enforced. The backend checks the signed `tid` and `iss` claims; the authority URL alone is not treated as tenant authorization.

## 2. Register the LogSentinel API

1. In Microsoft Entra admin center, open **Identity > Applications > App registrations > New registration**.
2. Name it **LogSentinel API - Local Development** (or another clearly API-specific name).
3. Select the supported account type chosen above. Do not add an SPA redirect URI to this API registration.
4. Record its **Application (client) ID** as `<API_APP_CLIENT_ID>` and its **Directory (tenant) ID** as `<TENANT_ID>`. These are public configuration, not secrets.
5. Under **Expose an API**, set the Application ID URI to:

   ```text
   api://<API_APP_CLIENT_ID>
   ```

6. Add a delegated scope with:

   | Field | Value |
   | --- | --- |
   | Scope name | `access_as_user` |
   | Full scope | `api://<API_APP_CLIENT_ID>/access_as_user` |
   | Who can consent | Admins and users, or the organization-approved policy |
   | Admin consent display name | `Access LogSentinel as the signed-in user` |
   | Admin consent description | `Allows the application to authenticate the signed-in user to the LogSentinel API.` |
   | User consent display name | `Access LogSentinel` |
   | User consent description | `Allows LogSentinel to authenticate you to its API.` |
   | State | Enabled |

7. Do not add Microsoft Graph permissions for this login exchange. The browser requests only the custom scope, and the backend requires `scp` to include `access_as_user` and `aud` to equal `<API_APP_CLIENT_ID>`.

No client secret or certificate is needed for access-token validation. The backend uses Microsoft's public signing keys.

## 3. Register the browser SPA

1. Create a second app registration named **LogSentinel SPA - Local Development** using the same supported account type.
2. Record its **Application (client) ID** as `<SPA_APP_CLIENT_ID>`.
3. Under **Authentication**, add the **Single-page application** platform. Do not configure a Web platform for these callback URLs.
4. Register the exact local redirect URI under the SPA platform:

   ```text
   http://localhost:5173/redirect.html
   ```

   Add the Docker URI only when Docker browser validation is required:

   ```text
   http://localhost:8080/redirect.html
   ```

   Microsoft treats localhost redirect URIs that differ only by port as equivalent during matching. If both local modes must remain registered and exact-port return behavior is unreliable, use separate development SPA registrations rather than changing the callback path or weakening validation.

5. Add `https://<production-domain>/redirect.html` only after the real production domain is known. Do not register a guessed domain.
6. Under **Implicit grant and hybrid flows**, leave both legacy token options disabled. MSAL uses authorization code flow with PKCE.
7. Under **API permissions**, add **My APIs > LogSentinel API > Delegated permissions > `access_as_user`**.
8. Grant administrator consent only when required by the scope or tenant policy.
9. Verify that no Microsoft Graph permission is needed by this application.

The SPA is a public client: never create, embed, or expose a client secret in Vite variables, JavaScript, Docker build arguments, or `redirect.html`.

The redirect URI is a dedicated MSAL bridge, not a React route. It must remain same-origin with the application and exactly match a registered SPA redirect URI. The production server must serve `redirect.html` as a static file and must not apply a `Cross-Origin-Opener-Policy` header that severs the popup from its opener.

## 4. Environment contract

| Entra or application value | Backend variable | Frontend variable | Secret |
| --- | --- | --- | --- |
| API Application (client) ID / expected `aud` | `AZURE_CLIENT_ID` | Embedded in `VITE_MICROSOFT_API_SCOPE` | No |
| SPA Application (client) ID | — | `VITE_MICROSOFT_SPA_CLIENT_ID` | No |
| Tenant mode or tenant GUID | `AZURE_TENANT_ID` | Embedded in `VITE_MICROSOFT_AUTHORITY` | No |
| Optional accepted tenant GUID list | `AZURE_ALLOWED_TENANTS` | — | No |
| Delegated scope name | `AZURE_REQUIRED_SCOPE=access_as_user` | — | No |
| Full delegated API scope | — | `VITE_MICROSOFT_API_SCOPE=api://<API_APP_CLIENT_ID>/access_as_user` | No |
| Microsoft login feature switch | — | `VITE_MICROSOFT_AUTH_ENABLED=true` | No |
| SPA redirect bridge URI | — | `VITE_MICROSOFT_REDIRECT_URI` | No |
| Same-origin post-logout URI | — | `VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI` | No |
| Browser-visible LogSentinel API origin | — | `VITE_API_URL` | No |
| Exact browser application origin / backend CORS allow-origin | `FRONTEND_URL` | — | No |
| JWKS request timeout in seconds | `AZURE_JWKS_TIMEOUT` | — | No |
| JWKS cache TTL in seconds | `AZURE_JWKS_CACHE_TTL` | — | No |
| Entra client secret | Not used | Not used | Must not exist |
| Internal LogSentinel JWT signing key | `JWT_SECRET_KEY` | — | **Yes** |

`VITE_*` values are compiled into the browser bundle and are never secrets. `JWT_SECRET_KEY` is unrelated to Entra registration and must be a strong, private, randomly generated value shared only with the backend.

The repository ignores `.env` and `.env.local`, and Docker build context excludes both. Keep real deployment identifiers and the local JWT key only in one of those ignored files or in process environment variables. A root `.env.local` is loaded by Vite, but a directly launched backend does not automatically load it; inject the backend variables into its process before startup. Never print the file contents during validation.

### Vite development (`http://localhost:5173`)

Set the backend values in the backend terminal:

```powershell
$env:JWT_SECRET_KEY="<strong-random-local-value>"
$env:AZURE_CLIENT_ID="<API_APP_CLIENT_ID>"
$env:AZURE_TENANT_ID="<tenant-guid-or-supported-mode>"
$env:AZURE_REQUIRED_SCOPE="access_as_user"
$env:AZURE_ALLOWED_TENANTS="<optional-comma-separated-tenant-guids>"
$env:AZURE_JWKS_TIMEOUT="5.0"
$env:AZURE_JWKS_CACHE_TTL="3600"
$env:FRONTEND_URL="http://localhost:5173"
py -3.13 -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Set the browser values before starting Vite in a separate terminal:

```powershell
$env:VITE_API_URL="http://localhost:8000"
$env:VITE_MICROSOFT_AUTH_ENABLED="true"
$env:VITE_MICROSOFT_SPA_CLIENT_ID="<SPA_APP_CLIENT_ID>"
$env:VITE_MICROSOFT_AUTHORITY="https://login.microsoftonline.com/<tenant-guid-or-supported-mode>"
$env:VITE_MICROSOFT_API_SCOPE="api://<API_APP_CLIENT_ID>/access_as_user"
$env:VITE_MICROSOFT_REDIRECT_URI="http://localhost:5173/redirect.html"
$env:VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI="http://localhost:5173/login"
corepack pnpm dev
```

If Microsoft authentication is disabled, missing, partial, malformed, cross-origin, or uses a non-LogSentinel scope, the application deliberately does not construct MSAL. Email/password and configured Google login remain available.

### Docker (`http://localhost:8080`)

Place non-secret IDs and the secret JWT key in an uncommitted local `.env` file or inject them from a secret manager:

```dotenv
JWT_SECRET_KEY=<strong-random-secret>
AZURE_CLIENT_ID=<API_APP_CLIENT_ID>
AZURE_TENANT_ID=<tenant-guid-or-supported-mode>
AZURE_REQUIRED_SCOPE=access_as_user
AZURE_ALLOWED_TENANTS=
VITE_MICROSOFT_AUTH_ENABLED=true
VITE_MICROSOFT_SPA_CLIENT_ID=<SPA_APP_CLIENT_ID>
VITE_MICROSOFT_AUTHORITY=https://login.microsoftonline.com/<tenant-guid-or-supported-mode>
VITE_MICROSOFT_API_SCOPE=api://<API_APP_CLIENT_ID>/access_as_user
VITE_MICROSOFT_REDIRECT_URI=http://localhost:8080/redirect.html
VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI=http://localhost:8080/login
VITE_API_URL=http://localhost:8080
FRONTEND_URL=http://localhost:8080
```

Then rebuild because Vite variables are build-time values:

```powershell
docker compose up --build
```

For production, use `https://<production-domain>/redirect.html` and `https://<production-domain>/login` for the two frontend URI values, set `FRONTEND_URL=https://<production-domain>` for the backend CORS origin, and set `VITE_API_URL` to the browser-visible HTTPS API origin (the same origin when Nginx proxies `/api`). Register the exact redirect URI in Entra before building the production image.

## 5. Runtime and token contract

1. MSAL handles the authorization code and PKCE verifier internally through the dedicated bridge.
2. The browser requests only `api://<API_APP_CLIENT_ID>/access_as_user`.
3. The hook tries `acquireTokenSilent` for an existing account. Only an interaction-required result opens `acquireTokenPopup`. With no account, the direct click invokes `loginPopup` with `prompt: select_account`.
4. The browser sends only `{ "access_token": "<microsoft-access-token>" }` to `POST /api/auth/microsoft`.
5. The backend validates signature, algorithm, key ID, expiry/not-before, audience, issuer, tenant, and `scp=access_as_user`.
6. Existing identities are resolved by provider + issuer + subject. Matching email alone never links accounts.
7. The backend returns an internal LogSentinel JWT. Only that internal JWT is manually stored under `authToken`.

The Microsoft access token and MSAL refresh state stay under MSAL control. Application code does not persist, log, display, or place Microsoft tokens in URLs. Remember Me selects `localStorage` for the internal JWT when checked and `sessionStorage` when unchecked.

## 6. Live validation checklist

Use browser developer tools while keeping token values redacted.

1. Start PostgreSQL and the backend with the backend variables above; confirm `/api/auth/microsoft` is enabled without sending a real token.
2. Start Vite or rebuild/start Docker with the matching frontend values.
3. Open `/login`; confirm email/password and Google regressions remain usable as configured.
4. Confirm `redirect.html` returns HTTP 200 as a static document and loads only its compiled redirect-bridge asset.
5. Select Remember Me, choose **Continue with Microsoft**, and confirm the popup opens.
6. Select an allowed account and grant the custom `access_as_user` permission if prompted. Do not accept a Graph permission as a substitute.
7. Confirm the popup closes, one `POST /api/auth/microsoft` succeeds, and the response is an internal LogSentinel JWT.
8. Confirm the dashboard opens only after the exchange succeeds.
9. With Remember Me checked, confirm `authToken` is in `localStorage`, survives reload, and survives a browser restart until its 60-minute expiry. Confirm no Microsoft access token is stored there.
10. With Remember Me unchecked, confirm `authToken` is in `sessionStorage`, survives a reload, and disappears when that browser session closes.
11. Refresh the dashboard; then replace the internal token with an expired/malformed value and confirm the route guard clears both stores and returns to `/login`.
12. Log out and confirm both internal-token stores and the local MSAL cache are cleared. A remaining Microsoft identity-provider session must not grant dashboard access without a valid internal JWT.
13. Re-run email/password login with Remember Me both checked and unchecked.
14. Re-run Google login with Remember Me both checked and unchecked when Google is configured.
15. Exercise popup cancellation and popup blocking; cancellation should be neutral, while blocking should show a sanitized actionable message.
16. Where a test account shares an email with an existing local/Google account, confirm the backend returns `account_linking_required` and creates no automatic link.

## 7. Failure interpretation

| Backend code | Meaning / portal action |
| --- | --- |
| `microsoft_auth_disabled` | Backend Entra configuration is absent or invalid. |
| `invalid_microsoft_token` | Token is malformed, expired, incorrectly signed, or for the wrong audience. Check API app ID alignment. |
| `invalid_microsoft_tenant` | The signed tenant is outside the configured tenant mode or allow-list. |
| `missing_required_scope` | Add/grant the API's delegated `access_as_user` permission to the SPA. |
| `account_linking_required` | An email match exists; use an explicit future linking flow rather than automatic linking. |
| `microsoft_identity_conflict` | The external identity mapping conflicts or provisioning lost a uniqueness race. |
| `microsoft_onboarding_required` | The verified token did not contain a usable account email for the current user schema. |
| `microsoft_jwks_unavailable` | Microsoft signing keys could not be retrieved; retry after service/network recovery. |

Do not expose raw exception text, tenant identifiers, JWKS details, or token contents to the browser when troubleshooting.

## References

- [Microsoft identity platform access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens)
- [MSAL.js login and redirect bridge guidance](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/login-user)
- [Redirect URI restrictions](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url)
- [Configure a client application to access a web API](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-access-web-apis)
- [Authorization code flow with PKCE](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
