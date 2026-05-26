# Load Test Scripts

## Files

- `run_loadtest.py`: burst load test runner.
- `scenarios.example.json`: example config for the online site.
- `fixtures/parse-sample.txt`: sample file for `/api/v1/detections/parse-files`.

## What it outputs

The runner writes a JSON report to `scripts/loadtest/results/loadtest-result-YYYYMMDD-HHMMSS.json`.

The JSON contains:

- `meta`: run metadata.
- `global_summary`: overall totals, status distribution, latency summary.
- `scenario_results[*].runs[*]`: per-scenario, per-concurrency, per-round details.
- `request_results`: per-request status code, latency, and error details.

## Example

```powershell
$env:LOADTEST_MEMBER_TOKEN = "your-member-jwt"
$env:LOADTEST_MEMBER_TOKEN_1 = "detect-member-jwt-1"
$env:LOADTEST_MEMBER_TOKEN_2 = "detect-member-jwt-2"
$env:LOADTEST_MEMBER_TOKEN_3 = "detect-member-jwt-3"
$env:LOADTEST_ADMIN_TOKEN = "your-admin-jwt"
$env:LOADTEST_MEMBER_API_KEY = "your-api-key"
$env:LOADTEST_GUEST_TOKEN_1 = "guest-token-1"
$env:LOADTEST_GUEST_TOKEN_2 = "guest-token-2"
$env:LOADTEST_GUEST_TOKEN_3 = "guest-token-3"
$env:LOADTEST_GUEST_TOKEN_4 = "guest-token-4"
$env:LOADTEST_GUEST_TOKEN_5 = "guest-token-5"

D:\Anaconda\envs\lab\python.exe .\scripts\loadtest\run_loadtest.py `
  --config .\scripts\loadtest\scenarios.example.json
```

## Useful commands

Only run a few scenarios:

```powershell
D:\Anaconda\envs\lab\python.exe .\scripts\loadtest\run_loadtest.py `
  --config .\scripts\loadtest\scenarios.example.json `
  --only health,detect_member,report_pdf_member
```

Override concurrency and rounds:

```powershell
D:\Anaconda\envs\lab\python.exe .\scripts\loadtest\run_loadtest.py `
  --config .\scripts\loadtest\scenarios.example.json `
  --concurrency 50,100 `
  --rounds 1
```

Skip TLS verification if needed:

```powershell
D:\Anaconda\envs\lab\python.exe .\scripts\loadtest\run_loadtest.py `
  --config .\scripts\loadtest\scenarios.example.json `
  --insecure
```

## Notes

- `detect_member` defaults to `rounds=1`, and should use `member_bearer_tokens` so detect traffic is spread across multiple member accounts.
- `detect_guest_pool` is disabled by default. If you enable it, use multiple guest tokens.
- Auth endpoints are included only as rate-limit verification scenarios, not as true performance tests.
- For `history_get_member`, `report_pdf_member`, `admin_user_detail`, `admin_detection_detail`, and `team_stats`, replace the example IDs with real IDs.
