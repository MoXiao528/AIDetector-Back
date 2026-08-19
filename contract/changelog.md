# Contract Changelog

Tracks breaking OpenAPI changes and compatibility boundaries.

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
