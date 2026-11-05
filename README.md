# parkwalker audit

Automatic audit of UK parkrun event volunteer rosters (2023–2026). This repository runs Python checks in GitHub Actions and checkpoints verified results in `parkwalker.csv`.

## Start
1. Upload `parkwalker.csv` from the previous conversation (the 1,909-row CSV) into this repository at the root, preserving the exact filename and semicolon delimiter.
2. Open **Actions → Audit parkwalker → Run workflow**. The workflow also runs automatically every 6 hours.
3. Review `parkwalker.csv` and `attempts.json` for progress.

Unknown/blocked pages remain `НЕ ПРОВЕРЕНО`. Confirmed results are never overwritten automatically. No paid API required.

**Important:** The GitHub-hosted runner's ability to access parkrun has not yet been validated. The workflow will log failures without inventing statuses.
