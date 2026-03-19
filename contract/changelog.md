# Contract Changelog

Tracks breaking OpenAPI changes and compatibility boundaries.

## Unreleased
- Rebuilt the active contract baseline and unified active routes under `/api/v1/*`.
- Removed legacy `/api/*` path definitions to match the real backend mount prefix.
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
