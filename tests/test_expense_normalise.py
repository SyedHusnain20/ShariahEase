"""Step 4b: the deterministic normalisers (amounts, dates, periods) and the amount cross-check. Offline."""
from datetime import date

import pytest

from app.services.expense_normalise import (
    DateError, amount_in_message, normalise_amount, parse_date_text, parse_period_text,
)
from app.services.money import MoneyError

TODAY = date(2026, 11, 20)


@pytest.mark.parametrize("text,paisa", [
    ("500", 50000), ("Rs 500", 50000), ("rs.500", 50000), ("PKR 1,250.50", 125050), ("1,00,000", 10000000),
    ("۸۵۰۰", 850000), ("5k", 500000), ("1.5k", 150000), ("2 hazar", 200000), ("2 Hazaar", 200000), ("do hazar", 200000),
    ("دو ہزار", 200000), ("1.5 lakh", 15000000), ("ڈیڑھ لاکھ", 15000000), ("2lakh", 20000000), ("dhai sau", 25000),
    ("paanch hazar", 500000), ("ek crore", 1000000000), ("500 rupay", 50000), ("ek", 100),
])
def test_amounts_that_are_understood(text, paisa):
    assert normalise_amount(text) == paisa


@pytest.mark.parametrize("text,code", [
    ("", "empty"), ("   ", "empty"), ("Rs", "empty"), ("0", "not_positive"), ("0 hazar", "not_positive"),
    ("-5", "format"), ("1e3", "format"), ("about 500", "format"), ("500 600", "format"), ("50 aur 100", "format"),
    ("1.234", "too_many_decimals"), ("1.234 lakh", "too_many_decimals"), ("5 crore crore", "format"),
    ("999999 crore", "too_large"), ("hazar", "format"), ("true", "format"), ("2 hazar 5", "format"), ("teen paanch", "format"),
])
def test_amounts_that_are_refused_never_guessed(text, code):
    with pytest.raises(MoneyError) as err:
        normalise_amount(text)
    assert err.value.code == code


@pytest.mark.parametrize("value", [None, 500, 5.5, True, ["500"], {"a": 1}])
def test_only_text_is_accepted(value):
    with pytest.raises(MoneyError):
        normalise_amount(value)


def test_plain_numbers_use_the_one_strict_money_gate():
    from app.services.money import parse_rupees_to_paisa
    for text in ("1,250", "99.5", "۱۲۵۰", "Rs 7,50,000"):
        assert normalise_amount(text) == parse_rupees_to_paisa(text)


def test_the_multiplication_is_exact_integers():
    assert normalise_amount("0.07 lakh") == 700000          # a float would give 7000.000000000001 somewhere
    assert normalise_amount("33.33 hazar") == 3333000


@pytest.mark.parametrize("amount,message,ok", [
    ("500", "chai 500 rupay", True), ("500", "chai 5000", False), ("50", "bill 50.5", False), ("500", "x 1500", False),
    ("500", "paid 1,500", False), ("1,250", "paid 1,250 for gas", True), ("Rs. 15,000", "Rs. 15,000 house rent paid", True),
    ("۸۵۰۰", "بجلی کا بل ۸۵۰۰ ادا کیا", True), ("8500", "بجلی کا بل ۸۵۰۰ ادا کیا", True), ("2 hazar", "petrol 2 hazar ka", True),
    ("5k", "5k rent ka diya", True), ("500", "chai 50", False), ("500", "", False), ("", "chai 500", False), ("500", "chai 500.", True),
    ("500", "rs.500 chai", True), ("500", "chai 5,500", False), ("100", "a 3.100 b", False),
])
def test_the_amount_must_really_be_in_the_message(amount, message, ok):
    assert amount_in_message(amount, message) is ok


@pytest.mark.parametrize("text,expected", [
    (None, TODAY), ("", TODAY), ("  ", TODAY), ("today", TODAY), ("Aaj", TODAY), ("آج", TODAY),
    ("kal", date(2026, 11, 19)), ("yesterday", date(2026, 11, 19)), ("کل", date(2026, 11, 19)),
    ("parson", date(2026, 11, 18)), ("پرسوں", date(2026, 11, 18)), ("3 din pehle", date(2026, 11, 17)), ("2 days ago", date(2026, 11, 18)),
    ("2026-11-05", date(2026, 11, 5)), ("05/11/2026", date(2026, 11, 5)), ("5-11", date(2026, 11, 5)), ("5/11/26", date(2026, 11, 5)),
    ("21/11", date(2026, 11, 21)),
])
def test_dates_that_are_understood(text, expected):
    assert parse_date_text(text, TODAY) == expected


@pytest.mark.parametrize("text,code", [
    ("monday", "unknown"), ("next week", "unknown"), ("40 din pehle", "unknown"), ("31/02", "invalid"), ("1999-12-31", "invalid"),
    ("2026-13-01", "invalid"), ("30/12", "future"), ("2027-01-01", "future"), ("22/11", "future"),
])
def test_dates_that_are_refused(text, code):
    with pytest.raises(DateError) as err:
        parse_date_text(text, TODAY)
    assert err.value.code == code


def test_the_day_ahead_limit_follows_the_service_rule():
    assert parse_date_text("2026-11-21", TODAY, max_future_days=1) == date(2026, 11, 21)
    with pytest.raises(DateError):
        parse_date_text("2026-11-21", TODAY, max_future_days=0)


@pytest.mark.parametrize("text,window", [
    ("is mahine", (date(2026, 11, 1), date(2026, 12, 1))), ("", (date(2026, 11, 1), date(2026, 12, 1))),
    ("this month", (date(2026, 11, 1), date(2026, 12, 1))), ("pichle mahine", (date(2026, 10, 1), date(2026, 11, 1))),
    ("اس مہینے", (date(2026, 11, 1), date(2026, 12, 1))), ("today", (TODAY, date(2026, 11, 21))),
    ("kal", (date(2026, 11, 19), TODAY)), ("this week", (date(2026, 11, 16), date(2026, 11, 23))),
    ("last week", (date(2026, 11, 9), date(2026, 11, 16))), ("this year", (date(2026, 1, 1), date(2027, 1, 1))),
    ("last year", (date(2025, 1, 1), date(2026, 1, 1))),
])
def test_periods_are_half_open_windows_in_local_dates(text, window):
    assert parse_period_text(text, TODAY) == window


def test_periods_cross_a_year_boundary_and_unknown_is_none():
    assert parse_period_text("last month", date(2026, 1, 15)) == (date(2025, 12, 1), date(2026, 1, 1))
    assert parse_period_text("whenever", TODAY) is None


def test_the_days_ago_limit_is_inclusive_at_31():
    assert parse_date_text("31 din pehle", TODAY) == date(2026, 10, 20)
    with pytest.raises(DateError):
        parse_date_text("32 din pehle", TODAY)
