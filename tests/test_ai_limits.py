"""Step 4b: kill switch, monthly allowance, daily breaker. Offline (SQLite). The parallel proof is in test_ai_quota_pg.py."""
from datetime import datetime, timezone

import pytest

from app.services import ai_limits as lim

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value,expected", [(None, True), ("true", True), ("1", True), ("", True), ("yes", True),
                                            ("false", False), ("FALSE", False), ("0", False), ("off", False), ("no", False)])
def test_kill_switch(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("EXPENSE_AI_ENABLED", value)
    assert lim.ai_enabled() is expected


@pytest.mark.parametrize("value,expected", [(None, 30), ("", 30), ("5", 5), ("0", 0), ("-3", 0), ("abc", 30), ("2.5", 30)])
def test_free_limit_setting(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("AI_FREE_LIMIT", value)
    assert lim.free_limit() == expected


@pytest.mark.parametrize("value,expected", [(None, 120000), ("50000", 50000), ("x", 120000), ("-1", 0)])
def test_daily_cap_setting(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("LLM_DAILY_TOKEN_CAP", value)
    assert lim.daily_token_cap() == expected


def test_the_default_allowance_is_thirty_and_the_thirty_first_is_refused(db_session, make_user):
    uid = make_user()
    for n in range(1, 31):
        assert lim.try_consume(db_session, uid, "2026-11") == n
    assert lim.try_consume(db_session, uid, "2026-11") is None
    assert lim.used_this_period(db_session, uid, "2026-11") == 30          # a refusal does not count


def test_a_limit_of_zero_refuses_everything_and_writes_nothing(db_session, make_user):
    uid = make_user()
    assert lim.try_consume(db_session, uid, "2026-11", limit=0) is None
    assert lim.used_this_period(db_session, uid, "2026-11") == 0


def test_allowance_is_per_person_and_per_month(db_session, make_user):
    a, b = make_user(), make_user()
    assert lim.try_consume(db_session, a, "2026-11", limit=1) == 1
    assert lim.try_consume(db_session, a, "2026-11", limit=1) is None
    assert lim.try_consume(db_session, b, "2026-11", limit=1) == 1
    assert lim.try_consume(db_session, a, "2026-12", limit=1) == 1


def test_release_gives_one_back_and_never_goes_below_zero(db_session, make_user):
    uid = make_user()
    lim.release(db_session, uid, "2026-11")
    assert lim.used_this_period(db_session, uid, "2026-11") == 0
    lim.try_consume(db_session, uid, "2026-11", limit=2)
    lim.try_consume(db_session, uid, "2026-11", limit=2)
    assert lim.try_consume(db_session, uid, "2026-11", limit=2) is None
    lim.release(db_session, uid, "2026-11")
    assert lim.used_this_period(db_session, uid, "2026-11") == 1
    assert lim.try_consume(db_session, uid, "2026-11", limit=2) == 2


def test_release_touches_only_that_person_and_month(db_session, make_user):
    a, b = make_user(), make_user()
    lim.try_consume(db_session, a, "2026-11"), lim.try_consume(db_session, b, "2026-11"), lim.try_consume(db_session, a, "2026-10")
    lim.release(db_session, a, "2026-11")
    assert (lim.used_this_period(db_session, a, "2026-11"), lim.used_this_period(db_session, b, "2026-11"),
            lim.used_this_period(db_session, a, "2026-10")) == (0, 1, 1)


def test_the_month_is_the_persons_own_month(db_session, make_user):
    karachi, utc = make_user("Asia/Karachi"), make_user("UTC")
    late = datetime(2026, 11, 30, 20, 30, tzinfo=timezone.utc)             # 01:30 on 1 December in Karachi
    assert lim.period_for(db_session, karachi, late) == "2026-12"
    assert lim.period_for(db_session, utc, late) == "2026-11"


def test_a_bad_time_zone_falls_back_to_the_default(db_session, make_user):
    assert lim.period_for(db_session, make_user("Not/AZone"), NOW) == "2026-11"


def test_the_breaker_opens_exactly_at_the_cap_and_closes_on_the_next_utc_day(db_session, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_TOKEN_CAP", "1000")
    assert not lim.breaker_open(db_session, NOW)
    lim.add_tokens(db_session, 999, NOW)
    assert not lim.breaker_open(db_session, NOW)
    lim.add_tokens(db_session, 1, NOW)
    assert lim.tokens_today(db_session, NOW) == 1000 and lim.breaker_open(db_session, NOW)
    assert not lim.breaker_open(db_session, datetime(2026, 11, 21, 0, 0, 1, tzinfo=timezone.utc))


def test_tokens_add_up_and_a_negative_or_junk_amount_adds_nothing(db_session):
    lim.add_tokens(db_session, 300, NOW)
    lim.add_tokens(db_session, 200, NOW)
    lim.add_tokens(db_session, -50, NOW)
    lim.add_tokens(db_session, None, NOW)
    assert lim.tokens_today(db_session, NOW) == 500


def test_the_daily_row_holds_no_user_id(db_engine):
    from sqlalchemy import inspect
    assert "user_id" not in {c["name"] for c in inspect(db_engine).get_columns("llm_usage_daily")}


def test_a_second_release_at_zero_changes_nothing_and_raises_nothing(db_session, make_user):
    uid = make_user()
    lim.try_consume(db_session, uid, "2026-11")
    lim.release(db_session, uid, "2026-11")
    lim.release(db_session, uid, "2026-11")                            # the row exists at 0: the database rule would refuse -1
    assert lim.used_this_period(db_session, uid, "2026-11") == 0


def test_the_breaker_day_is_the_utc_day_whatever_zone_the_clock_value_is_in(db_session):
    from datetime import timedelta
    karachi_morning = datetime(2026, 11, 21, 2, 0, tzinfo=timezone(timedelta(hours=5)))     # 21:00 UTC on the 20th
    lim.add_tokens(db_session, 700, karachi_morning)
    assert lim.tokens_today(db_session, datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)) == 700
    assert lim.tokens_today(db_session, datetime(2026, 11, 21, 0, 30, tzinfo=timezone.utc)) == 0
