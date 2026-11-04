"""Rupees text to paisa, and the sliding-window limiter (offline)."""
import random

import pytest

from app.services.money import MoneyError, parse_rupees_to_paisa as p
from app.services.rate_limit import SlidingWindowLimiter


@pytest.mark.parametrize("text,paisa", [
    ("1", 100), ("0.01", 1), ("0.1", 10), ("1.5", 150), ("1.50", 150), ("125.5", 12550), ("1250", 125000),
    ("1,250", 125000), ("1,250.50", 125050), ("1,00,000", 10000000), ("12,34", 123400), ("100000", 10000000),
    ("  99  ", 9900), ("Rs 500", 50000), ("rs.500", 50000), ("PKR 1,000", 100000), ("₨250", 25000), ("00012", 1200),
    ("۱۲۵۰", 125000), ("۱۲۵۰٫۵۰", 125050), ("١٢٥٠", 125000), ("١٬٢٥٠٫٥", 125050),                # Urdu / Arabic digits
    ("１２５", 12500),                                                                          # full-width digits (NFKC)
    ("10000000000", 10 ** 12),                                                                # exactly the cap
    (500, 50000), (1, 100),
])
def test_valid_amounts(text, paisa):
    assert p(text) == paisa


@pytest.mark.parametrize("text,code", [
    ("", "empty"), ("   ", "empty"), ("Rs", "empty"),
    ("abc", "format"), ("12abc", "format"), ("1e3", "format"), ("0x10", "format"), ("12.", "format"), (".5", "format"),
    ("1.2.3", "format"), ("1,2", "format"), ("1,0000", "format"), (",100", "format"), ("100,", "format"), ("-5", "format"),
    ("+5", "format"), ("5 0", "format"), ("1_000", "format"), ("١٢٣abc", "format"), ("NaN", "format"), ("Infinity", "format"), ("१२३", "format"), ("১২৩", "format"),
    ("1.234", "too_many_decimals"), ("0.001", "too_many_decimals"), ("5.999", "too_many_decimals"),
    ("0", "not_positive"), ("0.00", "not_positive"), ("000", "not_positive"), (0, "not_positive"), (-5, "not_positive"),
    ("10000000000.01", "too_large"), ("10000000001", "too_large"), ("99999999999999999999", "too_large"), (10 ** 10 + 1, "too_large"),
])
def test_invalid_amounts(text, code):
    with pytest.raises(MoneyError) as err:
        p(text)
    assert err.value.code == code


@pytest.mark.parametrize("bad", [True, False, 1.5, 100.0, None, [], {}, b"100"])
def test_wrong_types_are_refused(bad):
    with pytest.raises(MoneyError) as err:
        p(bad)
    assert err.value.code == "format"


def test_no_floating_point_error():
    # 0.1 + 0.2 style traps: 19.99 * 100 is 1998.9999999999998 in floats
    assert p("19.99") == 1999 and p("0.29") == 29 and p("1.15") == 115 and p("4.35") == 435 and p("8.2") == 820


def test_round_trip_for_random_amounts():
    rng = random.Random(5)
    for _ in range(2000):
        paisa = rng.choice([rng.randint(1, 10 ** 12), rng.randint(1, 100000), rng.randint(1, 99)])
        rupees, rest = divmod(paisa, 100)
        assert p(f"{rupees}.{rest:02d}") == paisa
        assert p(f"{rupees:,}.{rest:02d}") == paisa                       # western grouping
        assert p(f"{rupees}.{rest:02d}".translate({ord(str(i)): "۰۱۲۳۴۵۶۷۸۹"[i] for i in range(10)})) == paisa   # Urdu digits


# ── limiter ───────────────────────────────────────────────────────────────────

def test_limiter_blocks_after_the_limit_and_recovers():
    now = [100.0]
    lim = SlidingWindowLimiter(3, 60, clock=lambda: now[0])
    assert [lim.check("u") for _ in range(3)] == [0, 0, 0]
    wait = lim.check("u")
    assert 59 < wait <= 60
    now[0] += 30
    assert 29 < lim.check("u") <= 30
    now[0] += 30.5                                                       # the first hit is now older than the window
    assert lim.check("u") == 0


def test_limiter_keys_are_independent():
    lim = SlidingWindowLimiter(1, 60, clock=lambda: 0.0)
    assert lim.check(1) == 0 and lim.check(1) > 0 and lim.check(2) == 0


def test_blocked_attempts_do_not_extend_the_block():
    now = [0.0]
    lim = SlidingWindowLimiter(2, 10, clock=lambda: now[0])
    lim.check("u"); lim.check("u")
    for _ in range(50):
        now[0] += 0.1
        assert lim.check("u") > 0
    now[0] = 10.0                                                        # exactly one window after the first hit
    assert lim.check("u") == 0


def test_limiter_memory_is_bounded():
    now = [0.0]
    lim = SlidingWindowLimiter(1, 1, clock=lambda: now[0], max_keys=50)
    for i in range(500):
        now[0] += 2
        lim.check(i)
    assert len(lim._hits) <= 51


def test_limiter_rejects_nonsense_settings():
    with pytest.raises(ValueError):
        SlidingWindowLimiter(0, 60)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(5, 0)
