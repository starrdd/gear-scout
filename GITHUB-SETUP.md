# Run Gear Scout on GitHub

The workflow runs once per hour at minute 23, plus on code updates and manual runs. It runs a single check and exits; do not use `--watch` in Actions. **Demo mode is the default and makes no eBay requests.** Scheduled jobs may be delayed by GitHub.

## First demo run

1. Create a **private** GitHub repository named `gear-scout`.
2. Upload this folder's source files to the repository root, including `.github/workflows/gear-scout.yml`. The `.github` folder may be hidden in Finder; Command+Shift+Period toggles hidden files. Do not upload the ZIP as a single file, credentials, or generated data.
3. Open **Actions → Gear Scout**. The initial source upload triggers a run; you can also choose **Run workflow** on the default branch.
4. A successful run has a green check and a summary labeled **DEMO**. Under **Artifacts**, download `gear-scout-report`, unzip it, and open `report.html`. The report includes cross-market search links and a listing calculator even in demo mode.
5. Leave the hourly schedule running if you want to verify unattended operation. Your Mac can be switched off. To pause, open the workflow's menu and choose **Disable workflow**.

## After eBay approval

In the repository's **Settings → Secrets and variables → Actions**:

- Add repository secrets `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` using your production credentials.
- Under **Variables**, add `GEAR_SCOUT_MODE` with value `live`.
- Choose **Run workflow** and confirm it succeeds. Account approval alone may not grant production Browse API access; 403 errors mean access needs checking.

Set `GEAR_SCOUT_MODE` to `demo` to return to examples. Live mode fails clearly if secrets are missing; it will not pretend demo results are real.

The workflow's hourly schedule is set in `.github/workflows/gear-scout.yml`. `interval_minutes` in config.json affects local `--watch` only. Other watchlist and scoring settings apply in both environments.

## State, results, and notifications

The latest report is published as a GitHub Pages website and also saved as a downloadable Actions artifact. Each artifact expires after seven days. Live alert deduplication is saved separately in `cloud-alert-state.json` in the repository, so it survives runner shutdown and artifact expiration. Updates use GitHub's temporary built-in token and optimistic version checks; no personal access token is required. The workflow has repository contents-write permission for this one state file. Do not run simultaneous external writers against that state file.

Demo runs do not write live alert state or contact notification services. For now, review deals in Actions or the downloaded report. **Phone/email deal delivery is not yet configured.** GitHub's workflow failure notifications follow your account's notification settings and are not deal notifications.

## Cost controls

No paid services or billing changes are configured. Hourly runs mean about 720–744 runs/month. If a run uses one billed minute, that is about 744 minutes; at two minutes, about 1,488. Actual usage depends on execution time and other workflows sharing your account's allowance. A five-minute timeout bounds each run but does not guarantee zero monthly charges. Review GitHub Settings → Billing usage and set an Actions budget that stops usage if you want to prevent overages. Reports are small and expire after seven days to limit storage.

The tests use fixtures. Only a successful Actions run verifies the actual hosted workflow; live eBay retrieval still requires approved credentials.
