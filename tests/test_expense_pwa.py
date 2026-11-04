"""Pages, manifest, service worker and Urdu/English text for the Expenses page (offline; no browser needed)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "frontend" / "static"


@pytest.fixture
def site():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import pages
    app = FastAPI()
    app.include_router(pages.router)
    with TestClient(app) as c:
        yield c


# ── pages ────────────────────────────────────────────────────────────────────

def test_expenses_page_uses_the_pinned_local_chart_and_links_the_manifest(site):
    html = site.get("/expenses").text
    assert 'src="/static/vendor/chartjs/chart.umd.js"' in html
    assert "cdn.jsdelivr.net/npm/chart.js" not in html
    assert 'rel="manifest" href="/manifest.webmanifest"' in html
    assert 'name="theme-color"' in html and "/static/js/expenses.js" in html
    assert 'href="/expenses"' in html and ">Expenses<" in html          # the sidebar link


def test_other_pages_keep_their_chart_script(site):
    assert "cdn.jsdelivr.net/npm/chart.js" in site.get("/mudarabah").text


def test_vendored_chart_is_the_documented_version_and_licensed():
    js = (STATIC / "vendor/chartjs/chart.umd.js").read_text(encoding="utf-8")
    assert js.startswith("/*!\n * Chart.js v4.5.1")
    assert "MIT License" in (STATIC / "vendor/chartjs/LICENSE.md").read_text(encoding="utf-8")
    readme = (STATIC / "vendor/chartjs/README.md").read_text(encoding="utf-8")
    import hashlib
    assert hashlib.sha256(js.encode("utf-8")).hexdigest() in readme


def test_service_worker_is_served_from_the_root_and_never_cached_for_long(site):
    r = site.get("/sw.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")
    assert r.headers["service-worker-allowed"] == "/" and r.headers["cache-control"] == "no-cache"
    assert "se-shell-" in r.text


def test_manifest_is_installable_shaped(site):
    r = site.get("/manifest.webmanifest")
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["start_url"] == "/expenses" and m["scope"] == "/" and m["display"] == "standalone" and m["name"] and m["short_name"]
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes
    from PIL import Image
    for icon in m["icons"]:
        path = ROOT / "frontend" / icon["src"].lstrip("/")
        assert path.is_file(), icon
        assert Image.open(path).size == tuple(int(n) for n in icon["sizes"].split("x"))
    assert (ROOT / "frontend/static/icons/icon-180.png").is_file()                # apple-touch-icon


def test_offline_page_is_self_contained():
    html = (STATIC / "offline.html").read_text(encoding="utf-8")
    assert "<style>" in html and "http://" not in html and "https://" not in html and "<script" not in html.replace("onclick", "")


def test_every_precached_file_exists():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    block = re.search(r"const PRECACHE = \[(.*?)\];", sw, re.S).group(1)
    paths = re.findall(r"'(/[^']+)'", block)
    assert len(paths) >= 6
    for p in paths:
        assert (ROOT / "frontend" / p.lstrip("/")).is_file(), p


def test_main_registers_the_expenses_router():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert re.search(r"from app\.routers import .*\bexpenses\b", main)
    assert "app.include_router(expenses.router)" in main


# ── service worker behaviour (runs the real file under Node, if Node is installed) ──

NODE_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const listeners = {};
const cacheStore = new Map();
const mkCache = () => ({ addAll: async () => {}, put: async (r) => { putUrls.push(r.url); }, match: async (r) => (typeof r === 'string' ? ('CACHED:' + r) : undefined), keys: async () => [] });
const putUrls = [];
const self = {
  location: { origin: 'https://app.test' },
  addEventListener: (n, f) => { listeners[n] = f; },
  skipWaiting: () => {}, clients: { claim: () => {} },
};
const caches = { open: async () => mkCache(), match: async (r) => 'OFFLINE:' + r, keys: async () => [], delete: async () => true };
let netFails = false;
const fetchImpl = async (req) => { if (netFails) throw new Error('offline'); return { ok: !req.url.includes('broken'), clone() { return this; }, url: req.url }; };
new Function('self', 'caches', 'fetch', 'URL', src)(self, caches, fetchImpl, URL);

async function run(label, req, offline) {
  netFails = !!offline;
  let handled = false, answer;
  listeners.fetch({ request: req, respondWith: (p) => { handled = true; answer = p; } });
  if (handled) answer = await answer;
  return { label, handled, answer: handled ? (typeof answer === 'string' ? answer : 'response') : null };
}
const H = (o) => ({ has: (k) => Object.keys(o || {}).map((x) => x.toLowerCase()).includes(k.toLowerCase()) });
const rq = (method, url, extra) => Object.assign({ method, url: 'https://app.test' + url, mode: 'cors', headers: H() }, extra || {});
(async () => {
  const out = [];
  out.push(await run('post-api', rq('POST', '/api/expenses')));
  out.push(await run('patch-api', rq('PATCH', '/api/expenses/1')));
  out.push(await run('get-api', rq('GET', '/api/expenses?month=2026-11')));
  out.push(await run('get-auth', rq('GET', '/auth/refresh')));
  out.push(await run('get-bearer', rq('GET', '/static/js/main.js', { headers: H({ Authorization: 'x' }) })));
  out.push(await run('other-origin', { method: 'GET', url: 'https://cdn.example.com/static/lib.js', mode: 'no-cors', headers: H() }));
  out.push(await run('other-origin-page', { method: 'GET', url: 'https://other.example.com/expenses', mode: 'navigate', headers: H() }));
  out.push(await run('static', rq('GET', '/static/js/expenses.js')));
  out.push(await run('post-static', rq('POST', '/static/js/expenses.js')));
  out.push(await run('static-broken', rq('GET', '/static/js/broken.js')));
  await new Promise((r) => setTimeout(r, 20));
  out.push(await run('navigate-online', rq('GET', '/expenses', { mode: 'navigate' })));
  out.push(await run('navigate-offline', rq('GET', '/expenses', { mode: 'navigate' }), true));
  out.push(await run('page-not-navigate', rq('GET', '/zakat')));
  console.log(JSON.stringify({ out, putUrls }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_service_worker_only_touches_our_static_files(tmp_path):
    harness = tmp_path / "h.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(harness), str(STATIC / "sw.js")], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    got = {o["label"]: o for o in data["out"]}
    for label in ("post-api", "post-static", "patch-api", "get-api", "get-auth", "get-bearer", "other-origin", "other-origin-page", "page-not-navigate"):
        assert not got[label]["handled"], label                                   # the browser handles these itself
    assert got["static"]["handled"] and got["navigate-online"]["handled"]
    assert got["navigate-offline"]["answer"] == "OFFLINE:/static/offline.html"
    assert all("/api/" not in u and "/auth/" not in u for u in data["putUrls"])
    assert any(u.endswith("/static/js/expenses.js") for u in data["putUrls"])            # good answers are kept
    assert not any("broken" in u for u in data["putUrls"])                                 # failed answers are not


# ── Urdu / English text ──────────────────────────────────────────────────────

def _dictionaries():
    js = (STATIC / "js/expenses.js").read_text(encoding="utf-8")
    en = js[js.index("    en: {"):js.index("    ur: {")]
    ur = js[js.index("    ur: {"):js.index("  const pad")]
    keys = lambda block: dict(re.findall(r"\b([a-z_0-9]+): '((?:[^'\\]|\\.)*)'", block))
    return js, keys(en), keys(ur)


def test_urdu_and_english_have_the_same_keys_and_placeholders():
    _, en, ur = _dictionaries()
    assert len(en) > 50 and set(en) == set(ur)
    for k in en:
        assert re.findall(r"\{\w+\}", en[k]) == re.findall(r"\{\w+\}", ur[k]), k
        assert ur[k].strip() and en[k].strip()


def test_urdu_text_is_urdu_and_marked_for_review():
    js, en, ur = _dictionaries()
    assert "awaits native-speaker review" in js and "UR:" in js
    arabic = re.compile(r"[؀-ۿ]")
    for k, v in ur.items():
        assert arabic.search(v), (k, v)


def test_every_text_key_used_by_the_page_exists():
    js, en, _ = _dictionaries()
    html = (ROOT / "frontend/templates/pages/expenses.html").read_text(encoding="utf-8")
    used = set(re.findall(r"\bt\('([a-z_0-9]+)'[,)]", html + js))
    assert not used - set(en), used - set(en)
    for pm in ("cash", "bank_transfer", "card", "wallet", "other"):
        assert "pm_" + pm in en
    for code in ("empty", "format", "too_many_decimals", "not_positive", "too_large"):
        assert "err_amount_" + code in en                                         # one message per money error code


def test_the_page_never_sends_a_user_id_or_adds_money():
    js = (STATIC / "js/expenses.js").read_text(encoding="utf-8")
    assert "user_id" not in js
    assert "X-Requested-With" in js and "ShariahEase" in js
