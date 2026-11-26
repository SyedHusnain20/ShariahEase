"""
Path step 5b: runs the real frontend/static/js/expense_chat.js in Node (no browser, no Alpine) with a scripted fake `fetch`,
and checks the logic of the chat page's expense part: which buttons a card offers, what every error code says, that a
double tap sends one request, that the write header is on every POST, expiry by the server's clock, the status strip,
the Pro button and the retry id. Skipped when Node is not installed (the same rule as tests/test_expense_flags_js.py).
Every subprocess read uses encoding="utf-8" (a Windows code page broke the older test).
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "frontend/static/js/expense_chat.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")

HARNESS = r"""
const fs = require('fs');
const store = { se_token: 'T' };
global.window = global;
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
global.document = { querySelector: () => null };
global.authHeaders = () => ({ 'Content-Type': 'application/json', Authorization: 'Bearer T' });
let authErrors = 0;
global.handleAuthError = (res) => { if (res.status === 401) { authErrors++; return true; } return false; };

// timers are captured, so the test decides when "20 seconds" have passed
const timers = [];
global.setTimeout = (f, ms) => { timers.push({ f, ms }); return timers.length; };
global.clearTimeout = () => {};
global.setInterval = () => 1;
global.clearInterval = () => {};

// a scripted fake fetch: each call takes the next scripted answer ({status, json, headers} | {net:true} | {hang:true})
const calls = [];
let queue = [];
global.fetch = (url, opts) => {
  opts = opts || {};
  calls.push({ url: String(url), method: opts.method || 'GET', headers: opts.headers || {}, body: opts.body === undefined ? undefined : JSON.parse(opts.body) });
  const next = queue.length ? queue.shift() : { status: 200, json: {} };
  if (next.net) return Promise.reject(new TypeError('network'));
  if (next.hang) return new Promise((_, rej) => { opts.signal.addEventListener('abort', () => rej(new Error('aborted'))); });
  return Promise.resolve({ ok: next.status < 400, status: next.status, headers: { get: (h) => (next.headers || {})[h] || null }, json: async () => next.json });
};
global.AbortController = class { constructor() { this.signal = { _l: [], addEventListener(t, f) { this._l.push(f); } }; } abort() { this.signal._l.forEach((f) => f()); } };

new Function(fs.readFileSync(process.argv[2], 'utf8'))();
const EC = window.expenseChat;

const soon = (min) => new Date(Date.now() + min * 60000).toISOString();
const item = (o) => Object.assign({ index: 0, amount_paisa: 5000, amount_display: 'Rs 50', category_slug: 'food.tea', category_name_en: 'Tea', category_name_ur: 'چائے', occurred_on: '2026-11-25', note: 'chai', payment_method: 'cash', alternatives: [{ slug: 'food.groceries', name_en: 'Groceries', name_ur: 'راشن' }] }, o || {});
const card = (o) => Object.assign({ id: 'c1', kind: 'create', status: 'pending', lang: 'en', expires_at: soon(10), items: [item()] }, o || {});
const mk = () => { const a = EC(); a.messages = []; a.inputText = ''; a.formatTime = () => '10:00'; a.autoResize = () => {}; a.xTick = Date.now(); a.xLoggedIn = true; return a; };
const cmsg = (a, c) => { const m = a.xCardMessage(c, 'text', '10:00'); a.messages.push(m); return m; };
const script = (...answers) => { queue = answers; calls.length = 0; };
const last = () => calls[calls.length - 1];

(async () => {
  const out = {};

  // ── text ──────────────────────────────────────────────────────────────────
  const T = EC.TEXT;
  out.keys = { en: Object.keys(T.en).sort(), ur: Object.keys(T.ur).sort(), ro: Object.keys(T.ro).sort() };
  const arabic = /[\u0600-\u06FF]/;
  out.scripts = { urAll: Object.values(T.ur).every((s) => arabic.test(s)), roNone: Object.values(T.ro).every((s) => !arabic.test(s)), enNone: Object.values(T.en).every((s) => !arabic.test(s)) };
  out.examples = EC.EXAMPLES;

  // ── confirm, undo, double tap, headers ─────────────────────────────────────
  {
    const a = mk(); const m = cmsg(a, card());
    out.activeAtStart = [a.xActive(m), a.xShowExpired(m), a.xCanUndo(m)];
    script({ status: 200, json: { status: 'confirmed', kind: 'create', expenses: [] } });
    const p1 = a.xConfirm(m), p2 = a.xConfirm(m);                  // a double tap: the second must do nothing
    out.busyWhileWaiting = m.busy;
    await Promise.all([p1, p2]);
    out.confirmCalls = calls.length;
    out.confirmReq = { url: last().url, method: last().method, hdr: last().headers['X-Requested-With'], auth: last().headers.Authorization };
    out.afterConfirm = { outcome: m.outcome, status: m.card.status, busy: m.busy, active: a.xActive(m), canUndo: a.xCanUndo(m), undoMin: Math.round((m.undoUntil - Date.now()) / 60000) };
    out.textAfterConfirm = a.ct(m.card, m.outcome);
    script({ status: 200, json: { status: 'undone', removed: 1 } });
    await a.xUndo(m);
    out.afterUndo = { url: last().url, outcome: m.outcome, canUndo: a.xCanUndo(m) };
    // undo is not offered when there is nothing to undo
    script(); await a.xUndo(m); out.undoTwiceCalls = calls.length;
  }

  // delete and update cards say what they did
  {
    const a = mk();
    const d = cmsg(a, card({ kind: 'delete', id: 'd1', chosen: 0, needs_choice: false, changes: null }));
    script({ status: 200, json: { status: 'confirmed', kind: 'delete', expenses: [] } }); await a.xConfirm(d);
    const u = cmsg(a, card({ kind: 'update', id: 'u1', chosen: 0, needs_choice: false, changes: { amount_paisa: 6000, amount_display: 'Rs 60', category_slug: null, category_name_en: null, category_name_ur: null } }));
    script({ status: 200, json: { status: 'confirmed', kind: 'update', expenses: [] } }); await a.xConfirm(u);
    out.kindOutcomes = [d.outcome, u.outcome];
    const c = cmsg(a, card({ id: 'x2' }));
    script({ status: 200, json: { status: 'cancelled' } }); await a.xCancel(c);
    out.cancel = { url: last().url, outcome: c.outcome, status: c.card.status, active: a.xActive(c) };
  }

  // ── every error code ──────────────────────────────────────────────────────
  {
    const cases = {
      already_used: { status: 409, json: { detail: { code: 'already_used' } } },
      cancelled: { status: 409, json: { detail: { code: 'cancelled' } } },
      expired: { status: 409, json: { detail: { code: 'expired' } } },
      stale: { status: 409, json: { detail: { code: 'stale' } } },
      not_found: { status: 404, json: { detail: { code: 'not_found' } } },
      conflict: { status: 409, json: { detail: { code: 'conflict' } } },
      not_chosen: { status: 409, json: { detail: { code: 'not_chosen' } } },
      bad_choice: { status: 422, json: { detail: { code: 'bad_choice' } } },
      invalid_input: { status: 422, json: { detail: { code: 'invalid_input', errors: [] } } },
      rate: { status: 429, json: { detail: { code: 'rate_limited', retry_after: 7 } }, headers: { 'Retry-After': '12' } },
      server_confirm: { status: 500, json: {} },
      forbidden: { status: 403, json: { detail: { code: 'missing_header' } } },
      validation_list: { status: 422, json: { detail: [{ loc: ['body'], msg: 'x' }] } },
    };
    out.errors = {};
    for (const [name, answer] of Object.entries(cases)) {
      const a = mk(); const m = cmsg(a, card());
      script(answer); await a.xConfirm(m);
      out.errors[name] = { outcome: m.outcome, error: m.error, status: m.card.status, active: a.xActive(m), busy: m.busy };
    }
    // a lost connection: Confirm and Undo cannot tell, the others know nothing happened
    const a = mk(); const m = cmsg(a, card());
    script({ net: true }); await a.xConfirm(m);
    out.netConfirm = { error: m.error, active: a.xActive(m), outcome: m.outcome, busy: m.busy };
    script({ status: 200, json: { status: 'confirmed', kind: 'create' } }); await a.xConfirm(m);     // pressing again works
    out.retryAfterNet = m.outcome;
    script({ net: true }); await a.xUndo(m);
    out.netUndo = { error: m.error, canUndo: a.xCanUndo(m) };
    const b = mk(); const n = cmsg(b, card({ kind: 'update', chosen: null, needs_choice: true }));
    script({ net: true }); await b.xPick(n, 0);
    out.netPick = n.error;
    // undo window over (server says so)
    const c = mk(); const o = cmsg(c, card());
    script({ status: 200, json: { status: 'confirmed', kind: 'create' } }); await c.xConfirm(o);
    script({ status: 409, json: { detail: { code: 'undo_window_over' } } }); await c.xUndo(o);
    out.undoOver = { error: o.error, canUndo: c.xCanUndo(o), outcome: o.outcome };
    // 401: the shared handler runs and the card is left alone
    const d = mk(); const p = cmsg(d, card());
    script({ status: 401, json: { detail: 'x' } }); await d.xConfirm(p);
    out.unauth = { authErrors, outcome: p.outcome, error: p.error };
    // a request that never answers is cut off (never an endless spinner)
    const e = mk(); const q = cmsg(e, card());
    script({ hang: true });
    const waiting = e.xConfirm(q);
    out.hangBusy = q.busy;
    const t = timers[timers.length - 1];
    t.f();
    await waiting;
    out.hang = { ms: t.ms, busy: q.busy, error: q.error };
  }

  // ── choose and pick ────────────────────────────────────────────────────────
  {
    const a = mk(); const m = cmsg(a, card());
    script({ status: 200, json: { status: 'pending', card: card({ items: [item({ category_slug: 'food.groceries', category_name_en: 'Groceries' })] }) } });
    await a.xToggleAlt(m, 0); out.altOpen = m.altOpen;
    await a.xChoose(m, 0, 'food.groceries');
    out.choose = { url: last().url, body: last().body, hdr: last().headers['X-Requested-With'], cat: m.card.items[0].category_slug, altOpen: m.altOpen };

    const ed = card({ id: 'e1', kind: 'update', chosen: null, needs_choice: true, changes: { amount_paisa: 6000, amount_display: 'Rs 60', category_slug: 'food.groceries', category_name_en: 'Groceries', category_name_ur: 'راشن' }, items: [item({ index: 0, amount_display: 'Rs 50' }), item({ index: 1, amount_display: 'Rs 70', note: 'second' })] });
    const b = mk(); const n = cmsg(b, ed);
    out.pickState = { needsPick: b.xNeedsPick(n.card), showCandidates: b.xShowCandidates(n.card), pickMode: b.xPickMode(n), rows: b.xChangeRows(n.card).length, visible: b.xVisibleItems(n).length };
    script({ status: 200, json: { status: 'pending', card: Object.assign({}, ed, { chosen: 1, needs_choice: false }) } });
    await b.xPick(n, 1);
    out.picked = { url: last().url, body: last().body, hdr: last().headers['X-Requested-With'], chosen: n.card.chosen, needsPick: b.xNeedsPick(n.card), rows: b.xChangeRows(n.card) };
    out.pickBodyIsInteger = Number.isInteger(last().body.index);
    // when the card is settled only the entry it was about stays
    n.outcome = 'o_changed'; out.visibleAfter = b.xVisibleItems(n).map((i) => i.index);
    // a delete card has no change rows
    out.deleteRows = b.xChangeRows(card({ kind: 'delete', chosen: 0, changes: null }));
    // category names follow the card language
    out.catNames = [b.xCatName(item(), { lang: 'en' }), b.xCatName(item(), { lang: 'ur' }), b.xCatName(item(), { lang: 'roman_ur' })];
  }

  // ── expiry, by the server's clock ──────────────────────────────────────────
  {
    const a = mk();
    const old = cmsg(a, card({ expires_at: soon(-1) }));
    out.expiredLocal = [a.xActive(old), a.xShowExpired(old)];
    script(); await a.xConfirm(old); out.expiredNoRequest = calls.length;
    const server = cmsg(a, card({ id: 'z', status: 'expired' }));
    out.expiredFromServer = [a.xActive(server), a.xShowExpired(server)];
    // the device clock is 10 minutes ahead of the server: a card with 5 minutes left on the server is still live
    const b = mk(); const m = cmsg(b, card({ expires_at: soon(-5) }));     // by the device clock it ended 5 minutes ago
    b.xNoteDate({ headers: { get: () => new Date(Date.now() - 10 * 60000).toUTCString() } });
    b.xTick = Date.now();
    out.skewBehind = b.xActive(m);
    const c = mk(); const m2 = cmsg(c, card({ expires_at: soon(5) }));      // by the device clock it has 5 minutes left
    c.xNoteDate({ headers: { get: () => new Date(Date.now() + 10 * 60000).toUTCString() } });
    c.xTick = Date.now();
    out.skewAhead = c.xActive(m2);
    const d = mk(); d.xNoteDate({ headers: { get: () => null } }); out.noDateHeader = d.xOffset;
  }

  // ── what comes back from /chat/message ─────────────────────────────────────
  {
    const a = mk();
    const first = cmsg(a, card({ id: 'old' }));
    script({ status: 200, json: { status: 'ok' } });
    a.xAfterReply({ answer: 'please confirm', card: card({ id: 'new' }), sources: [] }, '10:01');
    const z = mk(); script();
    z.xOpen = true; z.xAfterReply({ answer: 'again', sources: [] }, '10:01'); out.panelClosesOnReply = z.xOpen;
    z.xOpen = true; z.xUseExample('tea 50'); out.panelClosesOnExample = z.xOpen;
    script({ status: 200, json: { status: 'ok' } });
    out.afterCardReply = { n: a.messages.length, oldSuperseded: first.superseded, newHasCard: a.messages[1].card.id === 'new', newActive: a.xActive(a.messages[1]), oldActive: a.xActive(first) };

    // a reply without a card while a card is shown: the card may have been answered by a typed "yes"
    const b = mk(); const shown = cmsg(b, card({ id: 's1' }));
    script({ status: 200, json: { card: null } }, { status: 200, json: { enabled: true, used: 1, limit: 30, breaker_open: false } });
    b.xAfterReply({ answer: 'Saved', sources: [] }, '10:02'); await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
    out.answeredInChat = { outcome: shown.outcome, hasCardKey: 'card' in b.messages[1], urls: calls.map((c) => c.url) };
    const c = mk(); const same = cmsg(c, card({ id: 's2' }));
    script({ status: 200, json: { card: card({ id: 's2' }) } }, { status: 200, json: { enabled: true, used: 1, limit: 30, breaker_open: false } });
    c.xAfterReply({ answer: 'something else', sources: [] }, '10:03'); await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
    out.stillPending = same.outcome;
    const d = mk(); const other = cmsg(d, card({ id: 's3' }));
    script({ status: 200, json: { card: card({ id: 'different' }) } }, { status: 200, json: {} });
    d.xAfterReply({ answer: 'x', sources: [] }, '10:04'); await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
    out.differentPending = other.outcome;
    // a failed check says nothing: the card stays as it was
    const e = mk(); const kept = cmsg(e, card({ id: 's4' }));
    script({ status: 500, json: {} }); await e.xReconcile();
    script({ net: true }); await e.xReconcile();
    script({ status: 200, json: null }); await e.xReconcile();
    out.failedCheck = [kept.outcome, e.xActive(kept)];
    // a guest never calls the expense routes
    const g = mk(); g.xLoggedIn = false; script();
    g.xAfterReply({ answer: 'hello', sources: [] }, '10:05'); await new Promise((r) => setImmediate(r));
    out.guestCalls = calls.length;
  }

  // ── page load: a waiting card comes back; the status; login state ───────────
  {
    const a = mk();
    script({ status: 200, json: { card: card({ id: 'wait', lang: 'ur' }) } });
    await a.xLoadPending();
    out.restored = { n: a.messages.length, id: a.messages[0].card.id, text: a.messages[0].content, active: a.xActive(a.messages[0]), url: last().url, method: last().method };
    script({ status: 200, json: { card: card({ id: 'wait' }) } }); await a.xLoadPending(); out.restoredOnce = a.messages.length;
    script({ status: 200, json: { card: null } }); await a.xLoadPending(); out.noPending = a.messages.length;
    script({ status: 200, json: { card: card({ id: 'gone', status: 'expired' }) } }); await a.xLoadPending(); out.expiredNotRestored = a.messages.length;
    script({ status: 500, json: {} }); await a.xLoadPending(); out.failedLoad = a.messages.length;
    script({ status: 401, json: {} }); await a.xLoadPending(); out.pending401 = [a.xLoggedIn, authErrors];

    const states = {};
    const s = mk();
    states.loading = [s.xState(), s.xStatusText()];
    script({ net: true }); await s.xRefreshStatus(); states.failed = [s.xState(), s.xStatusText(), s.xShowManualLink()];
    script({ status: 200, json: { enabled: true, period: '2026-11', limit: 30, used: 3, breaker_open: false, upgrade_interest: false } }); await s.xRefreshStatus(); states.ok = [s.xState(), s.xStatusText(), s.xShowManualLink(), s.xProDone];
    script({ status: 200, json: { enabled: true, period: '2026-11', limit: 30, used: 30, breaker_open: false, upgrade_interest: false } }); await s.xRefreshStatus(); states.limit = [s.xState(), s.xStatusText(), s.xShowManualLink()];
    script({ status: 200, json: { enabled: true, period: '2026-11', limit: 30, used: 5, breaker_open: true, upgrade_interest: false } }); await s.xRefreshStatus(); states.breaker = [s.xState(), s.xStatusText(), s.xShowManualLink()];
    script({ status: 200, json: { enabled: false, period: '2026-11', limit: 30, used: 5, breaker_open: false, upgrade_interest: true } }); await s.xRefreshStatus(); states.off = [s.xState(), s.xStatusText(), s.xShowManualLink(), s.xProDone];
    const g = mk(); g.xLoggedIn = false; states.guest = [g.xState(), g.xStatusText()];
    s.xSetLang('ur'); states.ur = s.xStatusText(); s.xSetLang('ro'); states.ro = s.xStatusText(); s.xSetLang('fr'); states.badLang = s.xLang;
    out.states = states; out.savedLang = store.se_xlang;
    const q = mk(); script({ status: 401, json: {} }); await q.xRefreshStatus(); out.status401 = q.xLoggedIn;
  }

  // ── the Pro button ─────────────────────────────────────────────────────────
  {
    const a = mk();
    script({ status: 200, json: { status: 'recorded' } });
    const p1 = a.xPro(), p2 = a.xPro();
    out.proBusy = a.xProBusy;
    await Promise.all([p1, p2]);
    out.pro1 = { calls: calls.length, url: last().url, method: last().method, hdr: last().headers['X-Requested-With'], body: last().body, done: a.xProDone, busy: a.xProBusy };
    script(); await a.xPro(); out.proAgain = calls.length;
    const b = mk(); script({ status: 429, json: { detail: { code: 'rate_limited' } }, headers: { 'Retry-After': '9' } }); await b.xPro(); out.pro429 = [b.xProDone, b.xProError];
    const c = mk(); script({ net: true }); await c.xPro(); out.proNet = [c.xProDone, c.xProError];
    const d = mk(); script({ status: 500, json: {} }); await d.xPro(); out.pro500 = [d.xProDone, d.xProError];
    script({ status: 200, json: { status: 'already_recorded' } }); await d.xPro(); out.proRetry = [d.xProDone, d.xProError];
    const g = mk(); g.xLoggedIn = false; script(); await g.xPro(); out.proGuest = calls.length;
  }

  // ── the retry id, examples ──────────────────────────────────────────────────
  {
    const a = mk();
    const id1 = a.xClientId('chai 50');
    const id2 = a.xClientId('chai 50');
    a.xFailedSend = { text: 'chai 50', id: id1 };
    out.ids = { fresh: id1 !== id2, reuse: a.xClientId('chai 50') === id1, other: a.xClientId('chai 60') !== id1, len: id1.length, uuid: /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id1) };
    a.xUseExample('tea 50'); out.example = a.inputText;
    const cryptoBefore = global.crypto;
    Object.defineProperty(global, 'crypto', { value: { getRandomValues: (b) => { for (let i = 0; i < b.length; i++) b[i] = 7; return b; } }, configurable: true });
    const fb = a.xClientId('zzz'); out.fallbackId = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(fb);
    Object.defineProperty(global, 'crypto', { value: cryptoBefore, configurable: true });
    out.dates = [a.xDate('2026-11-25', 'en'), a.xDate('2026-11-25', 'ur'), a.xDate('garbage', 'en')];
  }

  // ── the undo hour ends, Roman Urdu cards, the state read at load ──────────────
  {
    const a = mk(); const m = cmsg(a, card());
    m.outcome = 'o_saved'; m.undoUntil = Date.now() - 1000; a.xTick = Date.now();
    out.undoExpired = a.xCanUndo(m);
    m.undoUntil = Date.now() + 5000; out.undoLive = a.xCanUndo(m);
    out.cardText = [a.ct({ lang: 'roman_ur' }, 'confirm'), a.ct({ lang: 'ur' }, 'confirm'), a.ct({ lang: 'en' }, 'confirm'), a.ct(undefined, 'confirm')];
    store.se_xlang = 'xx'; delete store.se_token;
    const b = EC(); b.messages = []; script(); await b.xInit();
    out.initGuest = [b.xLoggedIn, b.xLang, calls.length, b.xOpen];
    store.se_token = 'T'; store.se_xlang = 'ur';
    const c = EC(); c.messages = [{}]; c.formatTime = () => '';
    script({ status: 200, json: { enabled: true, limit: 30, used: 0, breaker_open: false } }, { status: 200, json: { card: null } });
    await c.xInit();
    out.initUser = [c.xLoggedIn, c.xLang, calls.map((x) => x.url).sort(), c.xOpen];
  }

  // ── every POST of the whole run carried the header ─────────────────────────
  out.allCalls = { posts: calls.length };
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def R(tmp_path_factory):
    harness = tmp_path_factory.mktemp("js") / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    env = {**os.environ, "TZ": "America/Los_Angeles"}                  # a zone behind UTC: a date that is shifted by the zone shows up
    res = subprocess.run(["node", str(harness), str(JS)], capture_output=True, encoding="utf-8", errors="replace", timeout=60, env=env)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# ── text and examples ───────────────────────────────────────────────────────────

def test_every_string_exists_in_all_three_languages_in_the_right_script(R):
    assert R["keys"]["en"] == R["keys"]["ur"] == R["keys"]["ro"]
    assert R["scripts"] == {"urAll": True, "roNone": True, "enNone": True}


def test_urdu_strings_are_marked_for_review_in_the_source():
    src = JS.read_text(encoding="utf-8")
    assert "ur: {   // UR" in src and "ro: {   // RU" in src and "URDU TEXT:" in src


def test_every_example_is_a_message_from_the_gold_set(R):
    from tests.gold_set import GOLD
    messages = {c.message for c in GOLD}
    assert set(R["examples"]) == {"en", "ur", "ro"}
    for lang, rows in R["examples"].items():
        assert [r["kind"] for r in rows] == ["log", "query", "delete"], lang
        for r in rows:
            assert r["text"] in messages, (lang, r["text"])
    assert all(re.search(r"[\u0600-\u06FF]", r["text"]) for r in R["examples"]["ur"])
    assert not any(re.search(r"[\u0600-\u06FF]", r["text"]) for r in R["examples"]["ro"] + R["examples"]["en"])


def test_an_example_fills_the_box_and_does_not_send(R):
    assert R["example"] == "tea 50"


# ── confirm, undo, cancel ───────────────────────────────────────────────────────

def test_a_fresh_card_offers_buttons_and_no_undo(R):
    assert R["activeAtStart"] == [True, False, False]


def test_confirm_posts_once_with_the_write_header_and_a_double_tap_does_nothing(R):
    assert R["busyWhileWaiting"] is True
    assert R["confirmCalls"] == 1
    assert R["confirmReq"] == {"url": "/api/expense-ai/c1/confirm", "method": "POST", "hdr": "ShariahEase", "auth": "Bearer T"}


def test_after_confirm_the_buttons_go_and_undo_appears_for_one_hour(R):
    a = R["afterConfirm"]
    assert a["outcome"] == "o_saved" and a["status"] == "confirmed" and a["busy"] is False
    assert a["active"] is False and a["canUndo"] is True and a["undoMin"] in (59, 60)
    assert "Saved" in R["textAfterConfirm"] and "1 hour" in R["textAfterConfirm"]


def test_undo_posts_and_then_cannot_be_used_again(R):
    assert R["afterUndo"] == {"url": "/api/expense-ai/c1/undo", "outcome": "o_undone", "canUndo": False}
    assert R["undoTwiceCalls"] == 0


def test_delete_and_update_cards_say_changed_or_deleted_and_cancel_closes_the_card(R):
    assert R["kindOutcomes"] == ["o_deleted", "o_changed"]
    assert R["cancel"] == {"url": "/api/expense-ai/x2/cancel", "outcome": "o_cancelled", "status": "cancelled", "active": False}


# ── errors ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,outcome,status,active", [
    ("already_used", "e_used", "pending", False),
    ("cancelled", "e_cancelled", "cancelled", False),
    ("expired", "o_expired", "expired", False),
    ("stale", "o_stale", "cancelled", False),
    ("not_found", "e_not_found", "pending", False),
    ("conflict", "e_conflict", "pending", False),
])
def test_a_finished_card_says_so_and_offers_nothing_to_click(R, name, outcome, status, active):
    e = R["errors"][name]
    assert (e["outcome"], e["status"], e["active"], e["busy"]) == (outcome, status, active, False)
    assert e["error"] == ""


@pytest.mark.parametrize("name,words", [
    ("not_chosen", "Choose which entry"), ("bad_choice", "not available"), ("invalid_input", "Nothing was saved"),
    ("rate", "12 seconds"), ("server_confirm", "cannot tell whether"), ("forbidden", "Something went wrong"),
    ("validation_list", "Something went wrong"),
])
def test_a_problem_that_leaves_the_card_open_says_what_happened(R, name, words):
    e = R["errors"][name]
    assert words in e["error"] and e["outcome"] == "" and e["active"] is True and e["busy"] is False


def test_a_lost_connection_on_confirm_does_not_claim_anything_and_allows_a_safe_second_tap(R):
    n = R["netConfirm"]
    assert "cannot tell whether" in n["error"] and "never happen twice" in n["error"]
    assert n["active"] is True and n["outcome"] == "" and n["busy"] is False
    assert R["retryAfterNet"] == "o_saved"
    assert "cannot tell whether" in R["netUndo"]["error"] and R["netUndo"]["canUndo"] is True
    assert "Nothing was changed" in R["netPick"]                       # a pick changes nothing, so this one can say so


def test_when_the_undo_hour_is_over_the_undo_button_goes(R):
    assert R["undoOver"]["canUndo"] is False and "1 hour for undo is over" in R["undoOver"]["error"] and R["undoOver"]["outcome"] == "o_saved"


def test_a_401_goes_to_the_shared_login_handler_and_leaves_the_card_alone(R):
    assert R["unauth"] == {"authErrors": R["unauth"]["authErrors"], "outcome": "", "error": ""} and R["unauth"]["authErrors"] >= 1


def test_a_request_that_never_answers_is_cut_off_after_20_seconds(R):
    assert R["hangBusy"] is True
    assert R["hang"]["ms"] == 20000 and R["hang"]["busy"] is False and "cannot tell whether" in R["hang"]["error"]


# ── choose and pick ─────────────────────────────────────────────────────────────

def test_choosing_another_category_posts_item_and_slug_and_shows_the_new_card(R):
    assert R["altOpen"] == 0
    c = R["choose"]
    assert c["url"] == "/api/expense-ai/c1/choose" and c["body"] == {"item": 0, "category_slug": "food.groceries"}
    assert c["hdr"] == "ShariahEase" and c["cat"] == "food.groceries" and c["altOpen"] == -1


def test_an_edit_card_must_be_picked_before_it_can_be_confirmed(R):
    s = R["pickState"]
    assert s == {"needsPick": True, "showCandidates": True, "pickMode": True, "rows": 0, "visible": 2}


def test_picking_posts_a_real_integer_index_and_then_shows_the_old_and_new_values(R):
    p = R["picked"]
    assert p["url"] == "/api/expense-ai/e1/pick" and p["body"] == {"index": 1} and p["hdr"] == "ShariahEase"
    assert R["pickBodyIsInteger"] is True
    assert p["chosen"] == 1 and p["needsPick"] is False
    assert p["rows"] == [{"label": "Amount", "from": "Rs 70", "to": "Rs 60"}, {"label": "Category", "from": "Tea", "to": "Groceries"}]
    assert R["visibleAfter"] == [1]
    assert R["deleteRows"] == []


def test_category_names_follow_the_language_of_the_card(R):
    assert R["catNames"] == ["Tea", "چائے", "Tea"]


# ── expiry ────────────────────────────────────────────────────────────────────────

def test_an_expired_card_says_so_and_sends_no_request_when_tapped(R):
    assert R["expiredLocal"] == [False, True]
    assert R["expiredNoRequest"] == 0
    assert R["expiredFromServer"] == [False, True]


def test_expiry_follows_the_servers_clock_not_a_wrong_phone_clock(R):
    assert R["skewBehind"] is True          # phone ahead by 10 minutes: the card still has time on the server's clock
    assert R["skewAhead"] is False          # phone behind by 10 minutes: the card is over on the server's clock
    assert R["noDateHeader"] == 0


# ── replies, reload ───────────────────────────────────────────────────────────────

def test_a_new_card_replaces_the_older_one_as_the_only_one_offered(R):
    a = R["afterCardReply"]
    assert a == {"n": 2, "oldSuperseded": True, "newHasCard": True, "newActive": True, "oldActive": False}


def test_a_typed_answer_that_closed_the_card_is_noticed_without_claiming_the_result(R):
    assert R["answeredInChat"]["outcome"] == "o_answered" and R["answeredInChat"]["hasCardKey"] is False
    assert R["answeredInChat"]["urls"][0] == "/api/expense-ai/pending"
    assert R["stillPending"] == "" and R["differentPending"] == "o_answered"


def test_a_guest_never_touches_the_expense_routes(R):
    assert R["guestCalls"] == 0


def test_a_waiting_card_is_restored_once_after_a_reload(R):
    r = R["restored"]
    assert r["n"] == 1 and r["id"] == "wait" and r["active"] is True and r["url"] == "/api/expense-ai/pending" and r["method"] == "GET"
    assert re.search(r"[\u0600-\u06FF]", r["text"])                    # the card is Urdu, so is its note
    assert R["restoredOnce"] == 1 and R["noPending"] == 1
    assert R["pending401"][0] is False


# ── the status strip ─────────────────────────────────────────────────────────────

def test_each_honest_state_has_its_own_text(R):
    s = R["states"]
    assert s["loading"] == ["loading", ""]
    assert s["failed"][0] == "failed" and "Could not check" in s["failed"][1] and s["failed"][2] is False
    assert s["ok"][:3] == ["ok", "3 of 30 AI messages used this month", False] and s["ok"][3] is False
    assert s["limit"][0] == "limit" and "all 30 AI messages" in s["limit"][1] and "free and unlimited" in s["limit"][1] and s["limit"][2] is True
    assert s["breaker"][0] == "breaker" and "tomorrow" in s["breaker"][1] and s["breaker"][2] is True
    assert s["off"][0] == "off" and "switched off" in s["off"][1] and "by hand" in s["off"][1] and s["off"][2] is True
    assert s["off"][3] is True                                         # this person already tapped Pro: the button says thanks
    assert s["guest"][0] == "guest" and "Log in" in s["guest"][1]


def test_the_panel_language_can_be_switched_and_is_remembered(R):
    s = R["states"]
    assert re.search(r"[\u0600-\u06FF]", s["ur"]) and "abhi band hai" in s["ro"] and R["savedLang"] == "ro"
    assert s["badLang"] == "ro"                                        # an unknown language changes nothing
    assert R["status401"] is False


# ── the Pro button ───────────────────────────────────────────────────────────────

def test_the_pro_button_posts_once_with_the_header_and_no_body_and_a_double_tap_does_nothing(R):
    assert R["proBusy"] is True
    assert R["pro1"] == {"calls": 1, "url": "/api/expense-ai/upgrade-interest", "method": "POST", "hdr": "ShariahEase", "done": True, "busy": False}     # no body at all
    assert R["proAgain"] == 0


def test_the_pro_button_reports_problems_and_can_be_tapped_again(R):
    assert R["pro429"][0] is False and "9 seconds" in R["pro429"][1]
    assert R["proNet"][0] is False and "No connection" in R["proNet"][1]
    assert R["pro500"][0] is False and "Something went wrong" in R["pro500"][1]
    assert R["proRetry"] == [True, ""]
    assert R["proGuest"] == 0


# ── the retry id ─────────────────────────────────────────────────────────────────

def test_a_new_message_gets_a_new_id_and_a_retry_of_a_failed_one_keeps_it(R):
    i = R["ids"]
    assert i["fresh"] and i["reuse"] and i["other"] and i["uuid"] and i["len"] <= 64
    assert R["fallbackId"] is True                                     # no crypto.randomUUID (an old browser or plain http)


def test_dates_are_shown_as_calendar_dates(R):
    en, ur, bad = R["dates"]
    assert en == "25 Nov 2026" and bad == "garbage" and ur != en


def test_every_post_in_the_source_goes_through_the_one_function_that_adds_the_header():
    src = JS.read_text(encoding="utf-8")
    assert src.count("fetch(") == 1                                    # one fetch call: xApi
    assert "method === 'GET' ? {} : WRITE_HEADER" in src and "const WRITE_HEADER = { 'X-Requested-With': 'ShariahEase' };" in src


def test_the_undo_hour_really_ends_and_roman_urdu_cards_use_roman_urdu(R):
    assert R["undoExpired"] is False and R["undoLive"] is True
    assert R["cardText"] == ["Confirm karein", "تصدیق کریں", "Confirm", "Confirm"]


def test_a_stored_token_means_logged_in_and_a_bad_saved_language_is_ignored(R):
    assert R["initGuest"] == [False, "en", 0, False]                    # a guest: no request at all; the panel starts closed
    logged, lang, urls, is_open = R["initUser"]
    assert logged is True and lang == "ur" and urls == ["/api/expense-ai/pending", "/api/expense-ai/status"] and is_open is False


def test_a_failed_check_does_not_close_the_card_and_a_card_that_is_not_pending_is_not_restored(R):
    assert R["failedCheck"] == ["", True]
    assert R["expiredNotRestored"] == 1 and R["failedLoad"] == 1


def test_the_panel_never_covers_the_chat_it_closes_on_a_reply_and_on_an_example(R):
    assert R["panelClosesOnReply"] is False and R["panelClosesOnExample"] is False
