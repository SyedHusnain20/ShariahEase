# Step 4B: AI expense backend (intent router, understand call, confirmation cards, limits)

## What will change
New: migration `0005`, 6 service/router files (`expense_normalise`, `expense_understand`, `ai_limits`, `pending_actions`,
`expense_ai`, router `expense_ai`), 7 test files, `scripts/mutation_check_step04b.py`.
Changed (whole files in this zip): `app/database/models.py`, `app/models/schemas.py`, `app/routers/chatbot.py`,
`app/routers/pages.py`, `main.py`, `frontend/templates/pages/privacy.html`, and 5 existing tests
(`conftest`, `test_account_deletion`, `test_migrations`, `test_no_card_numbers`, `test_privacy_and_settings`).
No new dependency (the `groq` package is not used by this path; plain `httpx`).

## Plan that was followed
1. DB: `pending_actions`, `usage_counters`, `llm_usage_daily` (no user id in the daily table). Privacy page + deletion seeders updated.
2. Normalisers (amounts via `money.py`, dates, periods) + amount cross-check.
3. Understand call: one strict-JSON Groq request, fixed prefix, optional fallback provider, tracing off, nothing logged.
4. Limits: atomic monthly allowance, daily token breaker, kill switch.
5. Cards: create / confirm / choose / cancel / undo, single use, 15-minute life, retry safe.
6. Router before the guard in `/chat/message`; templates in English, Urdu, Roman Urdu; card routes `/api/expense-ai/*`.

## How a message flows (logged-in users only)
kill switch -> bare yes/no for a waiting card -> cheap pre-check (no digit / amount word / expense word = normal chatbot, no model)
-> same message id again = same card -> breaker -> allowance -> model -> intent. Other topics go to the normal chatbot (allowance
given back). Query / edit-delete answer "coming soon" (Step 5; allowance given back). Expense: every amount cross-checked and
normalised by code; ONE problem = NO card (allowance given back); otherwise a card. Only "yes" (or the confirm route) writes.
Expense messages are NOT saved to chat history, NOT read aloud, NOT traced, NOT logged.

## Settings (all optional)
| Variable | Default | Meaning |
|---|---|---|
| `EXPENSE_AI_ENABLED` | true | `false` = assistant off; manual form unaffected |
| `AI_FREE_LIMIT` | 30 | AI messages per person per month (person's own time zone) |
| `LLM_DAILY_TOKEN_CAP` | 120000 | tokens per UTC day for everybody; keep below Groq's 200K/day (shared with the website chatbot) |
| `EXPENSE_MODEL` | openai/gpt-oss-120b | model name |
| `EXPENSE_FALLBACK_BASE_URL` / `_API_KEY` / `_MODEL` | unset | optional OpenAI-compatible fallback (all three; URL must be https) |

Deploy: migration 0005 runs with your normal startup migration. Rolling back = `EXPENSE_AI_ENABLED=false` (no redeploy of code needed).

## Run the checks
    python -m pytest -q                                   # offline: 1707 passed, 49 deselected (was 1354 / 45)
    set DATABASE_URL_TEST=postgresql+psycopg://...        # a THROWAWAY database
    python -m pytest -q -m pg                             # 49 passed, incl. 60-parallel quota, 30-parallel confirm
    python scripts/mutation_check_step04b.py              # 107/107 caught (needs DATABASE_URL_TEST for the "pg:" one)

## Try it by hand (while logged in, e.g. browser console on the site)
    fetch('/chat/message',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_id:'t1',message:'chai 50 aaj',client_msg_id:crypto.randomUUID()})}).then(r=>r.json()).then(console.log)
You get `answer` text and a `card`. Type "yes" (or "haan") in the existing chat to save, or POST
`/api/expense-ai/{card.id}/confirm` with header `X-Requested-With: ShariahEase`. Needs a real `GROQ_API_KEY`.

## Decisions and things to know
- The 4A spike results were not in the repo (gitignored). I assumed S1/S2 passed and used the "no nulls" form (sentinels), which
  Groq's strict mode accepts. Please verify against your `spike_report.md`.
- "kal" means yesterday in an expense message; weeks run Monday to Sunday; day-first dates (05/11/2026).
- A bare "ok" / "ji" never confirms; only yes / haan / confirm / save / ہاں / جی ہاں do.
- The card's `note` is returned in card JSON only (the app must escape it); it is never put in the reply text.
- Free Groq tier has 8K tokens/minute: an uncached call is ~2-3K tokens, so a burst can hit 429. That is handled honestly
  (fallback, then "could not reach the assistant", allowance given back). There is no local per-minute pacing yet.
- Not in this step (Step 5): chat UI buttons, query path, edit/delete path with "which one?". `pending_actions.kind` already allows them.
- Urdu templates and Urdu number words need a native-speaker review.
- Privacy page text and date changed (21 November 2026) because two new per-user tables exist.
