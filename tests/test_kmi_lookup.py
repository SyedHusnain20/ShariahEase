import pytest

from app.services.web_search import lookup_shariah_status


def status(text):
    """(ticker, status) of the matched entry, or (None, None)."""
    _, data = lookup_shariah_status(text)
    return (data["ticker"], data["status"]) if data else (None, None)


@pytest.mark.parametrize("text, ticker, expected", [
    ("Lucky Cement", "LUCK", "halal"),
    ("is lucky cement halal?", "LUCK", "halal"),
    ("LUCK", "LUCK", "halal"),                      # by ticker
    ("OGDC", "OGDC", "halal"),
    ("HBL", "HBL", "haram"),                        # conventional bank
    ("Habib Bank", "HBL", "haram"),
    ("Meezan Bank", "MEBL", "halal"),
    ("Pakistan Tobacco", "PAKT", "haram"),
    ("k-electric", "KEL", "doubtful"),
    ("PPL", "PPL", "halal"),
])
def test_known_companies_match(text, ticker, expected):
    assert status(text) == (ticker, expected)


@pytest.mark.parametrize("text", [
    "reliable energy stocks",       # "abl" inside "reliable" used to match Allied Bank
    "stable dividend stocks",       # "abl" inside "stable"
    "Is Apple stock halal?",        # "ppl" inside "apple" used to match PPL
    "Bitcoin",
    "hello",
])
def test_substrings_inside_other_words_do_not_match(text):
    assert status(text) == (None, None)


@pytest.mark.parametrize("text", [
    "MCB Islamic Fund",             # a fund, not MCB Bank
    "HBL Islamic Money Market Fund",
    "UBL Islamic Savings Account",
    "Jubilee Life takaful",
])
def test_issuer_names_inside_product_queries_do_not_return_the_issuer_verdict(text):
    assert status(text) == (None, None)


def test_islamic_bank_is_not_skipped_for_product_words():
    # Meezan is an Islamic bank, not an issuer-sector entry.
    assert status("Meezan Bank Islamic account") == ("MEBL", "halal")


def test_longest_match_wins():
    # "bank al habib" must not be answered as "habib bank"-style entries.
    name, _ = lookup_shariah_status("Bank Al Habib share price")
    assert name == "bank al habib"
