"""
Runs the real frontend/static/js/expenses.js in Node (no browser) to check the page logic for the Shariah reminders and the
statement: which reminders show, what they say in each language, year limits, request URLs and headers.
Skipped when Node is not installed (the same rule as the service-worker test).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "frontend/static/js/expenses.js"

HARNESS = r"""
const fs = require('fs');
const store = {};
global.window = global;
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
global.document = { documentElement: {}, getElementById: () => null, createElement: () => ({ click() {}, remove() {} }), body: { appendChild() {} } };
global.navigator = {};
global.crypto = { randomUUID: () => '00000000-0000-4000-8000-000000000000', getRandomValues: (a) => a };
global.authHeaders = () => ({ 'Content-Type': 'application/json', Authorization: 'Bearer T' });
global.handleAuthError = (res) => res.status === 401;
global.Intl = Intl;
const calls = [];
global.fetch = async (url, opts) => {
  calls.push({ url: String(url), method: (opts && opts.method) || 'GET', headers: (opts && opts.headers) || {}, body: opts && opts.body });
  const next = global.__next || { status: 200, json: {} };
  return { ok: next.status < 400, status: next.status, headers: { get: (h) => (next.headers || {})[h] || null }, json: async () => next.json, blob: async () => 'BLOB' };
};
global.URL = Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} });
new Function(fs.readFileSync(process.argv[2], 'utf8'))();

const mk = (lang) => { const a = window.expenseApp(); a.lang = lang || 'en'; a.$nextTick = (f) => f(); a.$watch = () => {}; return a; };
(async () => {
  const out = {};
  const a = mk('en'), u = mk('ur');

  // which reminders show
  const x = (flag, dismissed) => ({ shariah_flag: flag, shariah_flag_dismissed: dismissed });
  out.live = [a.hasLiveFlag(x('riba', false)), a.hasLiveFlag(x('riba', true)), a.hasLiveFlag(x(null, false)), a.hasLiveFlag(x('doubtful', false)), a.hasLiveFlag(x(null, true))];
  out.text = [a.flagText(x('riba')), a.flagText(x('doubtful')), u.flagText(x('riba')), u.flagText(x('doubtful'))];

  // statement: year limits
  a.api = async (method, url) => { calls.push({ url, method }); return { ok: true, status: 200, data: { total_paisa: 0, count: 0, types: [], entries: [], zakat_check: null } }; };
  const year = a.thisYear;
  out.year0 = a.stYear === year;
  await a.shiftYear(1); out.afterFuture = [a.stYear, calls.filter((c) => c.url.includes('/statement')).length];
  await a.shiftYear(-1); out.afterPast = [a.stYear, calls[calls.length - 1].url];
  a.stYear = 2000; await a.shiftYear(-1); out.atFloor = a.stYear;

  // statement: check text in both languages
  a.stData = { zakat_check: { is_zakat_due: true, calculated_on: '2026-03-02' } };
  u.stData = { zakat_check: { is_zakat_due: false, calculated_on: '2026-03-02' } };
  out.check = [a.stCheckText(), u.stCheckText()];
  a.stData = { zakat_check: null }; out.checkNone = a.stCheckText();
  a.stData = { types: [] };

  // type names
  out.typeName = [a.stTypeName({ name_en: 'Zakat', name_ur: 'زکوٰۃ' }), u.stTypeName({ name_en: 'Zakat', name_ur: 'زکوٰۃ' })];

  // dismissal request: PATCH with the version and a real boolean, and the forgery header
  const d = mk('en');
  d.closeEdit = () => {}; d.reload = async () => {}; d.say = () => {};
  calls.length = 0;
  global.__next = { status: 200, json: {} };
  await d.setDismissed({ id: 7, version: 3 }, true);
  const p = calls.find((c) => c.method === 'PATCH');
  out.patch = { url: p.url, body: JSON.parse(p.body), header: p.headers['X-Requested-With'] };
  calls.length = 0;
  await d.setDismissed({ id: 7, version: 4 }, false);
  out.patchFalse = JSON.parse(calls.find((c) => c.method === 'PATCH').body);
  // a stale version: the page reloads and says so, it does not try again
  let reloads = 0, said = '';
  d.reload = async () => { reloads++; }; d.say = (t) => { said = t; };
  global.__next = { status: 409, json: { detail: { code: 'version_conflict' } } };
  calls.length = 0;
  await d.setDismissed({ id: 7, version: 1 }, true);
  out.conflict = { reloads, said, patches: calls.filter((c) => c.method === 'PATCH').length };

  // PDF download: URL, language, token header, saved under the server's file name
  const pdf = mk('en');
  let saved = null;
  document.createElement = () => ({ click() { saved = this.download; }, remove() {} });
  calls.length = 0;
  global.fetch = async (url, opts) => { calls.push({ url: String(url), headers: opts && opts.headers }); return { ok: true, status: 200, headers: { get: (h) => (h === 'Content-Disposition' ? 'attachment; filename="shariahease-zakat-sadaqah-2026-ur.pdf"' : null) }, blob: async () => 'B' }; };
  pdf.stYear = 2026;
  await pdf.downloadPdf('ur');
  out.pdf = { url: calls[0].url, auth: calls[0].headers.Authorization, saved, busy: pdf.stBusy };
  global.fetch = async () => ({ ok: false, status: 429, headers: { get: (h) => (h === 'Retry-After' ? '42' : null) } });
  await pdf.downloadPdf('en');
  out.pdf429 = pdf.stError;
  global.fetch = async () => { throw new Error('offline'); };
  await pdf.downloadPdf('en');
  out.pdfOffline = [pdf.stError, pdf.stBusy];
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def got(tmp_path_factory):
    if shutil.which("node") is None:
        pytest.skip("Node is not installed")
    h = tmp_path_factory.mktemp("js") / "h.js"
    h.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(h), str(JS)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_only_a_flagged_and_not_dismissed_expense_shows_the_reminder(got):
    assert got["live"] == [True, False, False, True, False]


def test_the_reminder_words_in_both_languages(got):
    assert got["text"][:2] == ["May involve riba", "Doubtful"]
    assert got["text"][2] == "ممکن ہے اس میں سود ہو" and got["text"][3] == "مشکوک"


def test_the_year_cannot_go_into_the_future_or_before_2000(got):
    assert got["year0"] is True
    assert got["afterFuture"] == [got["afterFuture"][0], 0] and got["afterFuture"][0] == got["afterPast"][0] + 1   # no request, year unchanged
    assert got["afterPast"][1].endswith(f"/api/expenses/statement?year={got['afterPast'][0]}")
    assert got["atFloor"] == 2000


def test_the_comparison_line_names_its_date_in_both_languages(got):
    assert got["check"][0] == "Zakat due by your calculation on 2026-03-02"
    assert got["check"][1] == "2026-03-02 کا حساب نصاب سے کم تھا: زکوٰۃ واجب نہیں"
    assert got["checkNone"] == ""


def test_type_names_come_from_the_server_in_the_chosen_language(got):
    assert got["typeName"] == ["Zakat", "زکوٰۃ"]


def test_dismissal_sends_the_version_a_real_boolean_and_the_forgery_header(got):
    assert got["patch"] == {"url": "/api/expenses/7", "body": {"version": 3, "shariah_flag_dismissed": True}, "header": "ShariahEase"}
    assert got["patchFalse"] == {"version": 4, "shariah_flag_dismissed": False}


def test_a_stale_dismissal_reloads_and_does_not_retry(got):
    assert got["conflict"] == {"reloads": 1, "said": "This expense was changed somewhere else. The latest version is shown.", "patches": 1}


def test_the_pdf_download_asks_for_the_chosen_year_and_language_and_uses_the_server_file_name(got):
    assert got["pdf"] == {"url": "/api/expenses/statement.pdf?year=2026&lang=ur", "auth": "Bearer T",
                          "saved": "shariahease-zakat-sadaqah-2026-ur.pdf", "busy": ""}


def test_download_failures_are_shown_honestly_and_unlock_the_buttons(got):
    assert got["pdf429"] == "Too many requests. Try again in 42 seconds."
    assert got["pdfOffline"] == ["The download did not work. Please try again.", ""]
