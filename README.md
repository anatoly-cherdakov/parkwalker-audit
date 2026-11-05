# UK parkrun parkwalker historical audit

One-time audit of all discoverable **UK 5 km parkrun** events from **1 October 2022 through 4 November 2026**, inclusive. No monitoring of future events.

## Workflow

1. Verify the **1,909 original records** in `parkwalker.csv`, retaining existing verified results.
2. Load parkrun's official `https://images.parkrun.com/events.json` **once**, filtering UK (`countrycode=97`) 5 km (`seriesid=1`). Store the frozen list in `venues.json`.
3. For every discovered venue, read its official `results/eventhistory/` page, select only actual dated event numbers through 2026-11-04, and append any missing events to the CSV.
4. Verify each appended event's volunteer roster for the **parkwalker** role.
5. Save results, discovery progress and retry state to GitHub after each batch. All unavailable or CAPTCHA-blocked pages stay `НЕ ПРОВЕРЕНО` and are retried. After three attempts on each inaccessible original record, discovery can proceed without falsely marking those records complete.

The workflow runs on GitHub Actions. Logs contain `ROUND`, `VERIFIED`, `RETRY`, `DISCOVERED`, `DISCOVERY SUMMARY` and `FINAL SUMMARY`.

## Files
- `parkwalker.csv` — master audit data; semicolon delimited, UTF-8.
- `attempts.json` — attempts for event result pages.
- `venues.json` — frozen UK 5 km event list (created when the national phase begins).
- `discovery_state.json` — completion/retries per venue.
- `audit_complete.json` — written only once all discovered venues and rows have been checked.

## Coverage caveat

The current official `events.json` catalogue may omit **historic venues which closed before the snapshot**. These cannot be declared exhaustively covered until a separate historical venue inventory is reconciled. Likewise the role itself may have existed before the initial 2023 sampling, so the start date is October 2022.

GitHub Actions runs after completion do no new data collection: `audit_complete.json` makes them no-ops. Scheduled GitHub executions themselves may still occur until the workflow is disabled.

Do not interpret a green Actions checkmark as data completeness. Review `audit_complete.json` and zero `НЕ ПРОВЕРЕНО` rows.
