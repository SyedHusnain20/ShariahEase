"""
Safe wording (hand-off decision 14): a reminder says "may involve riba, please verify with a scholar", never a verdict.
Every screen, script and PDF text of the expense tracker is checked, in English and Urdu.
"""
import re
from pathlib import Path

import pytest

from app.services import expense_service as es
from app.services import statement_pdf as sp

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "frontend/templates"
EXPENSE_PAGES = ["pages/expenses.html", "pages/settings.html", "pages/privacy.html", "offline.html"]
SCRIPTS = [ROOT / "frontend/static/js/expenses.js", ROOT / "frontend/static/js/settings.js", ROOT / "frontend/static/offline.html"]

# words that make a statement a religious verdict, or an absolute one
FORBIDDEN = re.compile(
    r"fatw|fatāw|\bruling|\brulings\b|فتو|فتاو|\bharam\b|حرام|ناجائز|\bforbidden\b|\bprohibited\b|\bimpermissible\b|\bsinful\b|گناہ",
    re.IGNORECASE)
VERDICT = re.compile(r"\b(is|are|was|were)\s+(riba|usury|haram)\b|یہ\s+سود\s+ہے|سود\s+ہے", re.IGNORECASE)


def _texts():
    for name in EXPENSE_PAGES:
        p = TEMPLATES / name
        if p.exists():
            yield f"template {name}", p.read_text(encoding="utf-8")
    for p in SCRIPTS:
        if p.exists():
            yield f"file {p.name}", p.read_text(encoding="utf-8")
    for lang in ("en", "ur"):
        flat = []
        for value in sp.T[lang].values():
            flat += [value] if isinstance(value, str) else (value if isinstance(value, list) else list(value.values()))
        yield f"pdf text ({lang})", "\n".join(flat)
    yield "charity type names", "\n".join(f"{en} {ur}" for _k, en, ur in es.CHARITY_TYPES)


@pytest.mark.parametrize("label,text", list(_texts()), ids=[l for l, _ in _texts()])
def test_no_verdict_words_anywhere_a_person_can_read(label, text):
    hit = FORBIDDEN.search(text) or VERDICT.search(text)
    assert not hit, (label, hit.group(0))


def _dictionary(lang):
    js = (ROOT / "frontend/static/js/expenses.js").read_text(encoding="utf-8")
    block = js[js.index(f"    {lang}: {{"):js.index("    ur: {" if lang == "en" else "  const pad")]
    return dict(re.findall(r"\b([a-z_0-9]+): '((?:[^'\\]|\\.)*)'", block))


def test_the_riba_label_says_may_involve_and_the_page_says_verify_with_a_scholar():
    en, ur = _dictionary("en"), _dictionary("ur")
    assert en["flag_riba"] == "May involve riba"
    assert "verify with a scholar" in en["flag_verify"].lower()
    assert en["flag_doubtful"] == "Doubtful"
    assert "ممکن ہے" in ur["flag_riba"] and "عالمِ دین" in ur["flag_verify"]            # UR: may involve ... ask a scholar
    assert "not a religious opinion" in en["flag_note"] and "دینی رائے نہیں" in ur["flag_note"]


def test_the_statement_says_it_is_not_a_certificate_or_an_opinion():
    en, ur = _dictionary("en"), _dictionary("ur")
    assert "not a Zakat certificate" in en["st_note"] and "not a religious opinion" in en["st_note"]
    assert "سرٹیفکیٹ نہیں" in ur["st_note"] and "نہ ہی کوئی دینی رائے" in ur["st_note"]
    assert any("not a Zakat certificate" in line for line in sp.T["en"]["disclaimer"])
    assert any("سرٹیفکیٹ نہیں" in line for line in sp.T["ur"]["disclaimer"])
    assert any("نہ ہی کوئی دینی رائے" in line for line in sp.T["ur"]["disclaimer"])


def test_the_checker_itself_catches_what_it_is_meant_to():
    for bad in ("This is a fatwa", "not a fatwa", "the ruling on riba", "Rulings", "this is haram", "یہ حرام ہے", "فتویٰ",
                "This is riba", "these are riba", "یہ سود ہے", "forbidden", "ناجائز"):
        assert FORBIDDEN.search(bad) or VERDICT.search(bad), bad
    for ok in ("May involve riba", "Please verify with a scholar.", "ممکن ہے اس میں سود ہو", "Zakat", "Doubtful", "the rule is simple",
               "mark-up", "ruled lines", "Haramain"):
        assert not (FORBIDDEN.search(ok) or VERDICT.search(ok)), ok


# ── the page offers what the hand-off asks for ───────────────────────────────

@pytest.fixture(scope="module")
def html():
    return (TEMPLATES / "pages/expenses.html").read_text(encoding="utf-8")


def test_the_page_has_a_dismiss_button_in_the_list_and_a_show_again_button_in_the_edit_sheet(html):
    assert 'data-testid="flag-dismiss"' in html and "setDismissed(x, true)" in html
    assert 'data-testid="flag-show"' in html and "setDismissed(editing, false)" in html
    strip = re.search(r'<div x-show="([^"]*)"[^>]*data-testid="flag-strip"', html)
    assert strip and strip.group(1) == "hasLiveFlag(x)"                                    # a dismissed reminder is not shown


def test_the_statement_links_to_the_zakat_calculator_and_offers_both_pdfs(html):
    section = html[html.index('data-testid="statement"'):]
    assert 'href="/zakat"' in section and 'data-testid="statement-zakat-link"' in section
    assert "downloadPdf('en')" in section and "downloadPdf('ur')" in section


def test_the_page_adds_up_no_money_for_the_statement():
    js = (ROOT / "frontend/static/js/expenses.js").read_text(encoding="utf-8")
    block = js[js.index("yearly Zakat and Sadaqah statement"):js.index("// ── toast")]
    assert "reduce(" not in block and "parseFloat" not in block and "Number(" not in block
    assert not re.search(r"paisa\s*[-+*/]", block)


def test_the_service_worker_cache_was_bumped_so_installed_apps_get_the_new_script():
    sw = (ROOT / "frontend/static/sw.js").read_text(encoding="utf-8")
    assert re.search(r"const VERSION = 'v([0-9]+)';", sw) and int(re.search(r"VERSION = 'v([0-9]+)'", sw).group(1)) >= 2
