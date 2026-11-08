# ShariahEase expense tracker: release checklist

Tick every box in order. Do not launch with an empty box. Commands are PowerShell, run from the repository folder.
Nothing here contains a secret: keys go in `.env` (local) and in the Hugging Face Space **Secrets** (never in git).

## A. One-time, before the first public launch

- [ ] **Tests pass.** `python -m pytest -q` is green. With a throwaway Postgres: `$env:DATABASE_URL_TEST="postgresql://.../shariahease_test"; python -m pytest -q -m pg` is green (the database name must contain `test`).
- [ ] **Urdu reviewed** by a native speaker, signed off in writing: `frontend/static/js/expenses.js`, `frontend/static/js/settings.js`, `frontend/static/offline.html`, the category and subcategory names added by migration 0004. The privacy page is English only: decide whether that is acceptable for launch.
- [ ] **Real phone test** (Android Chrome, and an iPhone if you can): sign up, add an expense in Urdu and English, open the app offline (you should see the offline page), install it to the home screen, download the CSV and JSON from Settings and open them, delete the test account.
- [ ] **Secrets and settings** on the Space (Settings, "Variables and secrets"). Secrets: `DATABASE_URL`, `SECRET_KEY` (32+ characters; the app refuses to start without it in production), `GROQ_API_KEY`, `SUNNAH_API_KEY` (a newly issued key), `FINNHUB_API_KEY`, `METAL_API_KEY`, and the WhatsApp ones only if the bot is on. Variables: `ENV=production`, `PRIVACY_CONTACT_EMAIL` (an address you read; if empty the privacy page points to About). `LANGSMITH_TRACING` stays off unless you are debugging.
- [ ] **The previous sunnah.com key is revoked** and the new one is in use.
- [ ] **Database region known.** Open the Neon console and note where the database lives (the privacy page names Neon and Hugging Face; Pakistan's data-protection bill is still a draft, see `risk_register.md`).
- [ ] **Privacy page matches reality.** Read `/privacy` once, slowly. Change `PRIVACY_UPDATED` in `app/routers/pages.py` whenever you change its text.
- [ ] **Backups kept 30 days or less.** The privacy page says "30 days or less". `scripts\backup_db.ps1` keeps the newest 8 dumps by default, which is about 8 weeks if you run it weekly. Run it as `.\scripts\backup_db.ps1 -Keep 4` (about 4 weeks), or change the sentence on the privacy page.

## B. Every deploy

- [ ] **Backup first.** `.\scripts\backup_db.ps1 -Keep 4`; check that a file of a sensible size appeared.
- [ ] **Static file changed?** If you edited a file listed in `PRECACHE` in `frontend/static/sw.js`, bump `VERSION` there.
- [ ] **Deploy** (push to the Space). Wait until the Space says "Running". Migrations run at start unless `AUTO_MIGRATE=false`.
- [ ] **Smoke test** (each must print 200 except where noted):
  ```powershell
  $base = "https://<your-space>.hf.space"
  foreach ($p in "/health","/privacy","/settings","/expenses","/manifest.webmanifest","/sw.js") {
    "{0} {1}" -f (Invoke-WebRequest -UseBasicParsing "$base$p").StatusCode, $p }
  # /docs must NOT exist in production (expect 404):
  try { (Invoke-WebRequest -UseBasicParsing "$base/docs").StatusCode } catch { $_.Exception.Response.StatusCode.value__ }
  ```
- [ ] **One real run-through as a new user:** sign up, add 3 expenses (one with an Urdu note), download JSON and CSV (the CSV should open in Excel with the Urdu text intact), delete an expense and undo it, then delete the account (a wrong password must be refused, the right one must work, and logging in again must fail).

## C. Launch day

- [ ] **Write down the launch moment in UTC** (it becomes `--cohort-start` for the gate report) and keep it somewhere private:
  ```powershell
  (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  ```
- [ ] **Check the report can reach production.** This only proves the connection (the date is in the past on purpose; the counts will be whatever exists today):
  ```powershell
  python scripts\gate_report.py --mode c1 --cohort-start 2026-01-01T00:00:00Z
  ```
  It must print a decision and save a file under `gate_reports\` (git-ignored). It only reads.

## D. Every week while it runs

- [ ] `.\scripts\backup_db.ps1 -Keep 4`
- [ ] Neon console: compute hours used this month (free plan has a monthly limit), storage used.
- [ ] The Space is "Running" (free Spaces sleep after 48 hours without traffic).
- [ ] The purge is alive: the row named `purge_deleted_expenses` in table `maintenance_runs` has a `last_run_at` from the last day or two (it runs when people use the app).

## E. Checkpoint C1 (about 6 weeks after launch)

- [ ] `python scripts\gate_report.py --mode c1 --cohort-start <launch moment>`
- [ ] Proceed to the AI steps only if it says PROCEED (at least 30 starters, and at least 30% of the starters who are 7 or more days old are activated). If not, spend the time on getting users and on the first-run experience, not on building. Keep the saved file.

## F. Gate G (30 days after the AI steps went live)

- [ ] `python scripts\gate_report.py --mode gate --cohort-start <launch moment> --as-of <30 days after the AI steps went live, UTC>`
- [ ] Keep the saved JSON file with the others. INCONCLUSIVE means "not enough people yet", not "no".

## If something goes wrong

- Redeploy the previous commit. If data is damaged, restore a backup into an **empty** database: `pg_restore --no-owner --dbname=<target url> <file>` (the backup script prints this line). Do not restore over the live database.
- If someone asks for their data to be deleted and cannot log in: delete the user row from the console (`DELETE FROM users WHERE id = ...` removes everything linked to it, on PostgreSQL), and tell them it can stay in backups for up to the period the privacy page states.
