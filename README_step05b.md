# ShariahEase: Path step 5b (chat UI for the expense assistant + "Notify me about Pro")

Built on the current `main` of your repo (it already contains 5a: commit "Chatbot can delete and update entries now…").
No migration. No change to the AI logic, the gold set or `scripts/gate_metrics.sql`.

## What is inside (12 files: 5 changed, 7 new)

| File | What |
|---|---|
| `frontend/static/js/expense_chat.js` (new) | All the new browser logic: confirmation cards, status line, help panel, Pro button, retry id. Mixed into `chatApp()`. |
| `frontend/templates/pages/chatbot.html` | Card markup, status strip, help panel, `client_msg_id` on every send, 70 s send timeout, `dir="auto"` on every message. The page still works if the new JS file fails to load. |
| `app/services/upgrade_interest.py` (new) | One `audit_log` row per person: action `upgrade_interest`, actor `user`, no entity, no detail (real SQL NULL). Locks the user's row so two taps at the same moment still give one row. |
| `app/routers/expense_ai.py` | `POST /api/expense-ai/upgrade-interest` (login + write header + the shared write limit); `GET /status` now also says `upgrade_interest: true/false` so the button remembers across reloads. |
| `frontend/templates/pages/privacy.html`, `app/routers/pages.py` | New "Pro interest" entry; date set to 26 November 2026 (change it to your deploy day if different). |
| `frontend/static/sw.js` | `VERSION` v2 → v3 and the new file precached (the service worker serves `/static/` from cache). |
| `tests/test_upgrade_interest.py`, `tests/test_upgrade_interest_pg.py`, `tests/test_expense_chat_js.py`, `tests/test_expense_chat_ui.py` (new) | 13 offline + 5 PostgreSQL + 47 Node + 19 source-rule tests. |
| `scripts/mutation_check_step05b.py` (new) | 93 mutations. |
| `urdu_review_5b.md` (new, in the zip root, not in the repo) | Every Urdu / Roman Urdu string for a native speaker. |

## How to apply and test (PowerShell, from the repo folder)

```powershell
# copy the files of the zip over the repo (same paths), then:
python -m pytest -q                                   # expect 2081 passed, 59 deselected (was 2002 + 54)
python -m pytest -q -m pg                             # with DATABASE_URL_TEST set: expect 58 passed (was 53)
python scripts/mutation_check_step05b.py --check      # 93 apply exactly once
python scripts/mutation_check_step05b.py              # expect 93 of 93 caught (the 11 "pg:"/pg-needing runs need DATABASE_URL_TEST)
```
Node must be on the PATH for the 47 JS tests (they skip without it, and then their mutations survive: the script says so).
After deploying, bump nothing else: the service worker version is already moved.

## What I verified, and how

- Offline suite 2081 passed; PostgreSQL 16 suite 58 passed (including: the gate query counts one person once however often they tap, leaves out admins and taps at/after `as_of`; six simultaneous taps leave one row; account deletion removes the row).
- Mutations: **93 of 93 caught** (first run 83 of 85; the two survivors got tests, then I added the layout/panel ones).
- Real browser (Chromium, 360×740, touch): 67 checks over 12 scenarios: log, confirm, double tap, undo, change category, Urdu card, query, delete with pick, update with old→new, typed digit, typed "yes", reload restore, expiry (server and device clock), AI off, daily limit, monthly limit, provider down, Pro button, guest, language switch, a 330 px-tall viewport. It ran the real pages and routers on an in-memory database with a scripted fake AI provider. I looked at the screenshots and fixed what they showed (below).

## Decisions I made (tell me if you disagree)

1. **The help panel is closed by default.** My first version opened it on an empty chat; the screenshot showed it covering the whole chat and, after sending, the new card. Now the welcome bubble says "Tap Expense help for examples", and the panel closes by itself when an answer arrives or an example is tapped.
2. **Examples fill the box, they do not send.** Sending would spend one of the 30 messages and "delete my last entry" could touch a real entry. All nine example sentences are gold-set messages.
3. **Only the newest card is clickable** (like the typed "yes", which acts on the newest). Older ones say "A newer card replaced this one".
4. **A typed "yes"/"no" is noticed**: the card then says "This card was answered in the chat. See the reply below." It does not claim saved or cancelled; the reply text says which.
5. **Honest network failures.** If Confirm or Undo gets no answer, the card says it cannot tell whether it went through and that tapping again can never happen twice (single-use cards). A request that hangs is cut at 20 s. After a failed send the text goes back into the box, and sending the same text again reuses the same `client_msg_id`, so the server gives back the same card and charges nothing.
6. **Expiry uses the server's clock** (taken from the `Date` header), so a phone with a wrong clock does not hide a valid card or offer an expired one. The server stays the judge: 409 `expired` is shown as expired.
7. **Undo works for one hour while the page stays open.** After a reload the chat does not remember it (chat messages are not stored). The card text says so; the Expenses page can still edit or delete.
8. **Voice messages do not go through the expense assistant** (they use `/voice/ask`). The panel says so in plain words rather than changing the voice path.
9. **Pro:** the button is in the panel, and also appears next to the "all 30 used" message. A tap cannot be withdrawn from the UI. No email, no payment fields.
10. **Static text only:** the person's own words (the note) are shown with `x-text` only, `dir="auto"`, wrapped; no `innerHTML` anywhere (tests enforce it).

## Please look at

- **Native-speaker review of the Urdu and Roman Urdu text**: `urdu_review_5b.md` (54 strings + 9 examples). All of it is marked UR/RU in the source and written by me.
- **One change outside the brief:** `privacy.html` still said "The expense tracker has no AI feature yet", which contradicted the AI card section below it. I replaced it with a true sentence (saved expenses are not sent to an AI; the message you type is). A test now fails if the old sentence comes back.
- **Not verified:** a real phone with a real keyboard (I used a 330 px-tall window and hid the status strip when the viewport is under 480 px tall; the chat area there is about 108 px, which is the existing page header + chat header, not new); the Noto Nastaliq font and Material icons (blocked in my sandbox, so icons showed as words in my screenshots); the real Groq model; the Tailwind CDN (I built the same config locally). Please open `/chatbot` on your phone once, in English and Urdu, before deploying.
- 5a is on GitHub `main`, not only on your local `step05a-query-edit` branch as the hand-off said. I built on `main`.
- My browser harness is sandbox-only (local Tailwind + Playwright); it is not in the zip.

## Not done on purpose
Quota for the Pro list, any payment, email capture, the optional "remove me" for Pro interest, a stored chat history for expenses, TTS for AI replies. None were in the brief.
