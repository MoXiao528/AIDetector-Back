# Contract Changelog

Tracks OpenAPI changes and compatibility boundaries.

## Unreleased
- Added optional server-owned `evidence` to canonical detection responses and history records. Evidence never changes the main score, threshold or AI/Human label; compatibility scan responses keep their existing shape.
- Evidence defaults to off. Shadow stores validated snapshots but omits public `evidence`; serve exposes valid snapshots, including degraded results. Missing or invalid snapshots are omitted rather than returned as `evidence: null`.
- Detection results, quota and idempotency completion share one transaction with the Evidence snapshot. First responses, history and replay use that snapshot without recomputation or requiring the current Bundle SHA.
- Corrected `EvidenceSignal.notice` to include `null` in its nullable enum, matching existing runtime responses.
- These changes are locally verified and have not been released; the current API version remains `4.0.0`.

## 4.0.0 - 2026-08-25
- **Breaking public-contract change:** detection labels and summary percentages now expose only `ai` and `human`; segment types additionally retain `too_short` for non-classified input.
- Legacy persisted `mixed` labels, sentence types, and summary percentages remain readable and are projected to `human` at response time. Stored history is not migrated or rewritten.
- Compatibility scan responses and scan examples now return binary AI/Human results. Admin `label=human` filtering includes legacy persisted `mixed` rows.

## 3.0.0 - 2026-08-23
- **Breaking hard removal:** removed `POST /api/v1/detections/parse-files` and the `ParseFilesResponse` / `ParsedFileResult` schemas. The removed path has no compatibility endpoint and returns `404`.
- Raw PDF, DOCX, and TXT files are no longer accepted or parsed by the backend. Browser clients extract document text locally and submit only the resulting text through the existing detection API.
- Direct API consumers and load-test profiles must remove the legacy multipart request before upgrading. There is no grace period or server-side file parsing fallback.

## 2.0.0 - 2026-08-23
- **Breaking hard cut:** all five detection entry points (`/api/v1/detect`, `/api/v1/scan/detect`, `/api/v1/scan`, `/api/scan/detect`, and `/api/scan`) require an `Idempotency-Key` header containing a UUID. Missing or malformed keys return `422`; the server does not generate a fallback key.
- One logical request owns one key. A transport retry must reuse the original key, while a new user-initiated detection must use a new UUID.
- Reusing the same actor/key for a different canonical request returns `409` without starting inference or consuming quota. An already-processing request also returns `409` and supplies `Retry-After` in whole seconds.
- If a completed idempotency record can no longer replay its result, the server returns `410`; that key never starts inference again.
- Clients and scripts must be upgraded before deploying this contract. There is no compatibility grace period for callers that omit the header.

## 1.3.0 - 2026-08-22
- Added `POST /api/v1/auth/logout` to revoke the supplied current user access credential and clear its access cookie; an absent or already-revoked credential remains idempotent.
- Revocation applies only to the credential presented to logout; access tokens issued by another login remain valid.
- Existing JWTs without `iss`, `aud`, `iat`, and `jti` are rejected after this change. Users must log in again; guests with a valid recovery cookie can obtain a replacement access token.
- Refresh tokens, refresh endpoints, and all-device logout are not part of this contract version.

## 1.2.0 - 2026-08-20
- Added `GET /api/v1/auth/guest` to preview only guest-session activity and the unclaimed server-history count, including recovery-cookie-only sessions, without returning detection contents or creating a new guest session.
- Added idempotent `DELETE /api/v1/auth/guest` so clients can revoke a guest capability and clear its HttpOnly recovery cookie before discarding browser-local guest data.
- A valid guest Bearer token is required to revoke an active server-side guest session. Cookie-only and anonymous discard requests only clear `aid_guest_refresh`; invalid, expired, malformed, and non-guest Bearer credentials fail closed with 401 and preserve the cookie for a safe retry.

## 1.1.0 - 2026-08-19
- Added `ApiKeyAuth` using the `X-API-Key` header.
- Restricted API keys to the canonical `/api/v1/detect` and `/api/v1/quota` automation paths; both paths now accept either Bearer authentication or API key authentication.
- Changed `/api/v1/keys/self-test` to API key authentication only, while API key creation, listing, and deactivation remain Bearer-session operations.
- Added fixed API key scopes (`detect:write`, `quota:read`) and exposed `scopes`, `expiresAt`, and `revokedAt` in API key responses.
- API keys no longer represent interactive user or administrator sessions; profile, history, report, team, upload, key-management, and admin routes remain Bearer-only.
- Requests that mix Bearer/cookie credentials with `X-API-Key` now fail closed with `AMBIGUOUS_CREDENTIALS`.

## 1.0.0 - 2026-04-01
- Rebuilt the active contract baseline and unified active routes under `/api/v1/*`.
- Removed legacy `/api/*` path definitions to match the real backend mount prefix; `/api/scan` is not an active public contract path.
- Replaced the obsolete `AuthResponse` with explicit `TokenResponse` and `UserResponse` models.
- Renamed user fields to `systemRole` and `profile.jobRole` to separate system role from occupational role.
- Froze `UserResponse` as `id/email/name/systemRole/isActive/planTier/creditsRemaining/createdAt/profile`.
- Froze `DetectResponse` as `detectionId/historyId/label/score/modelName/rawScore/threshold/currentCredits`.
- Unified history APIs under `/api/v1/history`, including CRUD, batch delete, and clear-all operations.
- Standardized paginated responses to `items/page/pageSize/total`.
- Added phase-1 admin APIs: `/api/v1/admin/status`, `/overview`, `/users`, `/users/{userId}`, `/users/{userId}/credits`, `/detections`, `/detections/{detectionId}`.
- Split credit adjustment into `/admin/users/{userId}/credits` instead of mixing it into generic user patch operations.
- Reworked `/api/v1/admin/overview` to use `preset=today|week|month|quarter|year` plus automatic granularity and period/series response fields.
- Removed billing, config, and contact paths from the active contract because they are not part of phase 1.
