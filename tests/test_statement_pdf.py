"""The statement PDF builder: fonts, Urdu shaping, number direction, layout of long and empty statements (offline)."""
from datetime import date

import pytest
from reportlab.pdfbase.ttfonts import TTFontFile

from app.services import expense_service as es
from app.services import statement_pdf as sp

FONTS = sp.FONT_DIR
ARABIC_TTF = FONTS / "NotoNaskhArabic-Regular.ttf"
LATIN_TTF = FONTS / "NotoNaskhArabic-Latin-Regular.ttf"


def make_statement(n_entries=3, truncated=False, check=None, empty=False):
    types = [es.StatementType(key=k, name_en=en, name_ur=ur, total_paisa=0 if empty else 1000 * (i + 1), count=0 if empty else i + 1)
             for i, (k, en, ur) in enumerate(es.CHARITY_TYPES[:4])]
    methods = ["cash", "bank_transfer", "card", "wallet", "other", "mystery"]
    entries = [] if empty else [
        es.StatementEntry(id=i, occurred_on=date(2026, 1, 1 + i % 28), type="sadaqah", name_en="Sadaqah", name_ur="صدقہ",
                          amount_paisa=100 * i + 5, payment_method=methods[i % 6], note="NOTE-NEVER-IN-PDF") for i in range(n_entries)]
    return es.CharityStatement(date_from=date(2026, 1, 1), date_to=date(2027, 1, 1), total_paisa=0 if empty else 10_000,
                               count=0 if empty else 10, types=types, entries=entries, entries_truncated=truncated, zakat_check=check)


def check(due=3_000_000, paid=2_500_000, is_due=True):
    return es.ZakatCheck(calculated_on=date(2026, 3, 2), is_zakat_due=is_due, zakat_due_paisa=due, zakat_paid_paisa=paid,
                         remaining_paisa=max(0, due - paid), overpaid_paisa=max(0, paid - due))


def build(st, lang="en", **kw):
    return sp.build_statement_pdf(st, lang=lang, issued_to=kw.pop("issued_to", "Amina Khan"), generated_on=date(2026, 11, 14), **kw)


# ── fonts ────────────────────────────────────────────────────────────────────

def test_the_font_files_and_their_licence_ship_with_the_app():
    for f in (ARABIC_TTF, LATIN_TTF, FONTS / "OFL.txt"):
        assert f.is_file() and f.stat().st_size > 1000, f
    assert "SIL OPEN FONT LICENSE" in (FONTS / "OFL.txt").read_text(encoding="utf-8")


def test_the_two_font_files_have_different_internal_names():
    # Same internal name = reportlab merges them into one PDF font and every Latin letter and digit goes blank.
    assert TTFontFile(str(ARABIC_TTF)).name != TTFontFile(str(LATIN_TTF)).name


def _urdu_strings():
    t = sp.T["ur"]
    for key, value in t.items():
        if isinstance(value, str):
            yield key, value
        elif isinstance(value, list):
            for v in value:
                yield key, v
        elif isinstance(value, dict):
            for k, v in value.items():
                yield f"{key}.{k}", v
    for key, _en, ur in es.CHARITY_TYPES:
        yield f"type.{key}", ur
    yield "name", "امینہ خان"


def test_every_urdu_string_can_be_drawn_with_the_bundled_fonts():
    arabic, latin = TTFontFile(str(ARABIC_TTF)).charToGlyph, TTFontFile(str(LATIN_TTF)).charToGlyph
    missing = []
    for key, text in _urdu_strings():
        shaped = sp._RESHAPER.reshape(text)
        for ch in shaped:
            if ch in "\n\r\t ‌‍":
                continue
            if ord(ch) not in arabic and ord(ch) not in latin:
                missing.append((key, hex(ord(ch))))
    assert not missing, missing


def test_the_urdu_strings_really_are_urdu_and_english_has_none():
    arabic = sp._ARABIC
    for key, text in _urdu_strings():
        assert arabic.search(text) or key in ("currency",), key
    for key, value in sp.T["en"].items():
        flat = value if isinstance(value, str) else " ".join(value if isinstance(value, list) else value.values())
        assert not arabic.search(flat), key


def test_both_languages_have_the_same_keys_and_placeholders():
    import re
    assert set(sp.T["en"]) == set(sp.T["ur"])
    for k in sp.T["en"]:
        e, u = sp.T["en"][k], sp.T["ur"][k]
        assert type(e) is type(u), k
        if isinstance(e, str):
            assert re.findall(r"\{\w+\}", e) == re.findall(r"\{\w+\}", u), k
        else:
            assert len(e) == len(u), k
    assert "awaits native-speaker review" in (sp.__doc__ or "") and "UR" in (sp.__doc__ or "")


# ── text direction ───────────────────────────────────────────────────────────

def test_numbers_and_dates_stay_left_to_right_inside_urdu_lines():
    out = sp.visual("2026-01-01 تا 2026-12-31", "ur")
    assert "2026-12-31" in out and "2026-01-01" in out
    assert out.index("2026-12-31") < out.index("2026-01-01")          # right-to-left line: the first date ends up on the right
    assert "1,250.50" in sp.visual("رقم 1,250.50 روپے", "ur")


def test_the_direction_marks_never_reach_the_page():
    out = sp.visual("رقم 1,250.50", "ur")
    assert not any(ch in out for ch in ("‪", "‬", "‎", "‏"))


def test_plain_latin_text_is_not_changed():
    for text in ("Zakat 25,000.00", "2026-12-31", ""):
        assert sp.visual(text, "en") == text and sp.visual(text, "ur") == text


def test_a_latin_name_with_brackets_is_not_scrambled_in_an_urdu_document():
    # Run through the right-to-left algorithm, "Amina Khan (2026)" would come out as "(Amina Khan (2026".
    for text in ("Amina Khan (2026)", "[Ali] - Rs 5", "(Zakat)"):
        assert sp.visual(text, "ur") == text


def test_money_is_formatted_with_integer_arithmetic():
    assert [sp.money(p) for p in (0, 5, 100, 12_345, 123_456_789, 10 ** 12)] == \
        ["0.00", "0.05", "1.00", "123.45", "1,234,567.89", "10,000,000,000.00"]


def test_markup_escapes_markup_characters():
    assert "<b>" not in sp.markup("a <b>bold</b> & more", "en")
    assert "&amp;" in sp.markup("a & b", "en") and "&lt;" in sp.markup("a < b", "ur")


# ── whole documents ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["en", "ur"])
@pytest.mark.parametrize("st", [
    make_statement(), make_statement(empty=True), make_statement(truncated=True), make_statement(check=check()),
    make_statement(check=check(due=0, paid=0, is_due=False)), make_statement(check=check(due=100, paid=900)),
    make_statement(check=check(due=1000, paid=1000)), make_statement(n_entries=300, truncated=True, check=check()),
], ids=["plain", "empty", "truncated", "due", "below-nisab", "overpaid", "exact", "300-entries"])
def test_every_kind_of_statement_builds_in_both_languages(st, lang):
    pdf = build(st, lang)
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 2000


def test_a_long_list_runs_onto_more_pages():
    short, long = build(make_statement(3)), build(make_statement(300, truncated=True))
    assert short.count(b"/Type /Page\n") + short.count(b"/Type /Page ") >= 1
    assert long.count(b"/Type /Page") > short.count(b"/Type /Page")


def test_a_name_with_markup_or_urdu_does_not_break_the_document():
    for lang in ("en", "ur"):
        for name in ("A & <b>B</b> < C", "امینہ خان", "Amina امینہ", ""):
            assert build(make_statement(), lang, issued_to=name).startswith(b"%PDF-")


def test_notes_are_never_written_into_the_pdf(monkeypatch):
    import reportlab.rl_config as rl
    monkeypatch.setattr(rl, "pageCompression", 0)
    for lang in ("en", "ur"):
        assert b"NOTE-NEVER-IN-PDF" not in build(make_statement(), lang)


def test_the_english_pdf_says_what_it_is_and_what_it_is_not(monkeypatch):
    import reportlab.rl_config as rl
    monkeypatch.setattr(rl, "pageCompression", 0)
    pdf = build(make_statement(check=check()), "en")
    for needle in (b"Zakat and Sadaqah Statement", b"not a Zakat certificate", b"not a religious opinion",
                   b"01 Jan 2026 to 31 Dec 2026", b"Zakat calculator check", b"Left to pay"):
        assert needle in pdf, needle


def test_the_period_shows_the_last_day_not_the_exclusive_end(monkeypatch):
    import reportlab.rl_config as rl
    monkeypatch.setattr(rl, "pageCompression", 0)
    pdf = build(make_statement(), "en")
    assert b"31 Dec 2026" in pdf and b"01 Jan 2027" not in pdf


def test_an_unknown_language_is_refused():
    for lang in ("fr", "", "EN", None):
        with pytest.raises(ValueError):
            build(make_statement(), lang)


def test_the_font_registration_survives_being_called_many_times():
    for _ in range(3):
        assert build(make_statement(), "ur").startswith(b"%PDF-")
