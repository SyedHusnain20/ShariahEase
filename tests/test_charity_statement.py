"""The yearly Zakat / Sadaqah / Fitrana statement (service layer, offline)."""
import math
from datetime import date, datetime, timezone

import pytest

from app.database.models import MAX_AMOUNT_PAISA, ZakatRecord
from app.services import expense_service as es
from tests.conftest import exp

Y = dict(date_from=date(2026, 1, 1), date_to=date(2027, 1, 1))


@pytest.fixture
def uid(make_user, clock):
    clock["now"] = datetime(2027, 3, 1, 7, 0, tzinfo=timezone.utc)
    return make_user()


def give(db, uid, sub, amount, on, **kw):
    [r] = es.add_expenses(db, uid, [exp(amount, on=on, sub=sub, **kw)])
    return r.expense


def stmt(db, uid, **kw):
    return es.charity_statement(db, uid, **{**Y, **kw})


def by_key(st):
    return {t.key: (t.total_paisa, t.count) for t in st.types}


def save_calc(db, uid, due, is_due=True, at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)):
    db.add(ZakatRecord(user_id=uid, user_name="U", total_assets=1, total_deductions=0, zakatable_wealth=1,
                       nisab_threshold=1, zakat_due=due, is_zakat_due=is_due, calculated_at=at))
    db.commit()


# ── what counts ──────────────────────────────────────────────────────────────

def test_an_empty_statement_is_zero_not_an_error(db_session, uid):
    st = stmt(db_session, uid)
    assert (st.total_paisa, st.count, st.entries, st.entries_truncated, st.zakat_check) == (0, 0, [], False, None)
    assert [t.key for t in st.types] == ["zakat", "sadaqah", "fitrana", "other"]          # the four main lines are always shown
    assert all(t.total_paisa == 0 and t.count == 0 for t in st.types)


def test_totals_by_type(db_session, uid):
    give(db_session, uid, "charity.zakat", 2_500_000, date(2026, 3, 10))
    give(db_session, uid, "charity.zakat", 500_000, date(2026, 4, 10))
    give(db_session, uid, "charity.sadaqah", 12_505, date(2026, 5, 1))
    give(db_session, uid, "charity.fitrana", 30_000, date(2026, 3, 20))
    give(db_session, uid, "charity.other", 7_000, date(2026, 8, 1))
    st = stmt(db_session, uid)
    assert by_key(st) == {"zakat": (3_000_000, 2), "sadaqah": (12_505, 1), "fitrana": (30_000, 1), "other": (7_000, 1)}
    assert (st.total_paisa, st.count) == (3_049_505, 5)


def test_other_categories_never_count(db_session, uid):
    give(db_session, uid, "food.groceries", 99_999, date(2026, 3, 1))
    give(db_session, uid, "bills.electricity", 88_888, date(2026, 3, 2))
    give(db_session, uid, "charity.zakat", 100, date(2026, 3, 3))
    st = stmt(db_session, uid)
    assert (st.total_paisa, st.count) == (100, 1)


def test_a_charity_expense_with_no_type_is_listed_apart_and_never_counted_as_zakat(db_session, uid):
    [r] = es.add_expenses(db_session, uid, [exp(40_000, on=date(2026, 3, 1), sub=None, category_slug="charity")])
    st = stmt(db_session, uid)
    assert by_key(st)["zakat"] == (0, 0)
    assert by_key(st)["unspecified"] == (40_000, 1)
    assert (st.total_paisa, st.count) == (40_000, 1)
    assert st.entries[0].type == "unspecified" and st.entries[0].id == r.expense.id


def test_the_unspecified_line_is_hidden_when_empty(db_session, uid):
    give(db_session, uid, "charity.zakat", 100, date(2026, 3, 1))
    assert "unspecified" not in by_key(stmt(db_session, uid))


# ── the window: start inclusive, end exclusive ───────────────────────────────

def test_year_boundaries_start_included_end_excluded(db_session, uid):
    give(db_session, uid, "charity.zakat", 1, date(2025, 12, 31))             # the day before: out
    give(db_session, uid, "charity.zakat", 10, date(2026, 1, 1))              # first day: in
    give(db_session, uid, "charity.zakat", 100, date(2026, 12, 31))           # last day: in
    give(db_session, uid, "charity.zakat", 1000, date(2027, 1, 1))            # the day after: out
    st = stmt(db_session, uid)
    assert (st.total_paisa, st.count) == (110, 2)
    assert [e.occurred_on for e in st.entries] == [date(2026, 1, 1), date(2026, 12, 31)]


def test_the_boundaries_are_parameters(db_session, uid):
    for d, a in ((date(2026, 3, 1), 1), (date(2026, 3, 15), 10), (date(2026, 3, 31), 100), (date(2026, 4, 1), 1000)):
        give(db_session, uid, "charity.sadaqah", a, d)
    st = stmt(db_session, uid, date_from=date(2026, 3, 15), date_to=date(2026, 4, 1))
    assert (st.total_paisa, st.count) == (110, 2) and st.date_from == date(2026, 3, 15) and st.date_to == date(2026, 4, 1)


def test_a_one_day_window_holds_that_day_only(db_session, uid):
    give(db_session, uid, "charity.zakat", 5, date(2026, 5, 5))
    give(db_session, uid, "charity.zakat", 7, date(2026, 5, 6))
    assert stmt(db_session, uid, date_from=date(2026, 5, 5), date_to=date(2026, 5, 6)).total_paisa == 5


def test_an_empty_window_is_allowed_and_a_backwards_one_is_not(db_session, uid):
    assert stmt(db_session, uid, date_from=date(2026, 5, 5), date_to=date(2026, 5, 5)).count == 0
    with pytest.raises(es.InvalidInput):
        stmt(db_session, uid, date_from=date(2026, 5, 6), date_to=date(2026, 5, 5))


@pytest.mark.parametrize("kw", [
    dict(date_from=date(1999, 12, 31)), dict(date_to=date(2200, 1, 2)),
    dict(date_from="2026-01-01"), dict(date_to=None), dict(date_from=datetime(2026, 1, 1)),
])
def test_bad_dates_are_refused(db_session, uid, kw):
    with pytest.raises(es.InvalidInput):
        stmt(db_session, uid, **kw)


@pytest.mark.parametrize("limit", [0, -1, 10001, True, 1.5, "5", None])
def test_a_bad_entry_limit_is_refused(db_session, uid, limit):
    with pytest.raises(es.InvalidInput):
        stmt(db_session, uid, entry_limit=limit)


# ── deleted rows and other people's rows ─────────────────────────────────────

def test_deleted_expenses_never_appear_and_come_back_when_restored(db_session, uid):
    keep = give(db_session, uid, "charity.zakat", 100, date(2026, 3, 1))
    gone = give(db_session, uid, "charity.zakat", 1000, date(2026, 3, 2))
    es.delete_expenses(db_session, uid, [gone.id])
    st = stmt(db_session, uid)
    assert (st.total_paisa, st.count) == (100, 1) and [e.id for e in st.entries] == [keep.id]
    assert by_key(st)["zakat"] == (100, 1)
    es.restore_expenses(db_session, uid, [gone.id])
    assert stmt(db_session, uid).total_paisa == 1100


def test_another_users_giving_is_never_counted(db_session, uid, make_user):
    other = make_user()
    give(db_session, other, "charity.zakat", 555_555, date(2026, 3, 1))
    give(db_session, uid, "charity.zakat", 100, date(2026, 3, 1))
    mine, theirs = stmt(db_session, uid), stmt(db_session, other)
    assert (mine.total_paisa, mine.count, len(mine.entries)) == (100, 1, 1)
    assert (theirs.total_paisa, theirs.count) == (555_555, 1)
    assert stmt(db_session, make_user()).count == 0


def test_the_user_id_must_be_a_real_integer(db_session, uid):
    for bad in (None, "1", 1.0, True, 0, -1):
        with pytest.raises(Exception):
            stmt(db_session, bad)


# ── entries ──────────────────────────────────────────────────────────────────

def test_entries_are_oldest_first_and_carry_names_and_notes(db_session, uid):
    b = give(db_session, uid, "charity.sadaqah", 200, date(2026, 6, 1), note="masjid", payment_method="wallet")
    a = give(db_session, uid, "charity.zakat", 100, date(2026, 2, 1))
    c = give(db_session, uid, "charity.fitrana", 300, date(2026, 6, 1))
    st = stmt(db_session, uid)
    assert [e.id for e in st.entries] == [a.id, b.id, c.id]                     # by date, then by id
    first, second = st.entries[0], st.entries[1]
    assert (first.type, first.name_en, first.name_ur, first.amount_paisa) == ("zakat", "Zakat", "زکوٰۃ", 100)
    assert (second.note, second.payment_method) == ("masjid", "wallet")


def test_a_long_list_is_cut_but_the_totals_still_cover_every_row(db_session, uid):
    for i in range(7):
        give(db_session, uid, "charity.sadaqah", 10, date(2026, 3, 1 + i))
    st = stmt(db_session, uid, entry_limit=3)
    assert len(st.entries) == 3 and st.entries_truncated is True
    assert (st.total_paisa, st.count) == (70, 7) and by_key(st)["sadaqah"] == (70, 7)
    assert [e.occurred_on.day for e in st.entries] == [1, 2, 3]
    exact = stmt(db_session, uid, entry_limit=7)
    assert len(exact.entries) == 7 and exact.entries_truncated is False


def test_totals_use_whole_paisa_without_rounding(db_session, uid):
    give(db_session, uid, "charity.sadaqah", 1, date(2026, 3, 1))
    give(db_session, uid, "charity.sadaqah", 2, date(2026, 3, 2))
    assert stmt(db_session, uid).total_paisa == 3
    give(db_session, uid, "charity.sadaqah", MAX_AMOUNT_PAISA, date(2026, 3, 3))
    give(db_session, uid, "charity.sadaqah", MAX_AMOUNT_PAISA, date(2026, 3, 4))
    assert stmt(db_session, uid).total_paisa == 2 * MAX_AMOUNT_PAISA + 3


# ── the Zakat calculator comparison ──────────────────────────────────────────

def test_no_saved_calculation_means_no_comparison(db_session, uid):
    give(db_session, uid, "charity.zakat", 100, date(2026, 3, 1))
    assert stmt(db_session, uid).zakat_check is None


def test_due_paid_and_remaining(db_session, uid):
    give(db_session, uid, "charity.zakat", 2_500_000, date(2026, 3, 1))
    give(db_session, uid, "charity.sadaqah", 9_999_999, date(2026, 3, 2))        # Sadaqah is never counted as Zakat paid
    save_calc(db_session, uid, 30000.0)
    c = stmt(db_session, uid).zakat_check
    assert (c.zakat_due_paisa, c.zakat_paid_paisa, c.remaining_paisa, c.overpaid_paisa) == (3_000_000, 2_500_000, 500_000, 0)
    assert c.is_zakat_due is True and c.calculated_on == date(2026, 6, 1)


def test_overpaid(db_session, uid):
    give(db_session, uid, "charity.zakat", 4_000_000, date(2026, 3, 1))
    save_calc(db_session, uid, 30000.0)
    c = stmt(db_session, uid).zakat_check
    assert (c.remaining_paisa, c.overpaid_paisa) == (0, 1_000_000)


def test_exactly_paid(db_session, uid):
    give(db_session, uid, "charity.zakat", 3_000_000, date(2026, 3, 1))
    save_calc(db_session, uid, 30000.0)
    c = stmt(db_session, uid).zakat_check
    assert (c.remaining_paisa, c.overpaid_paisa) == (0, 0)


def test_below_nisab_is_reported_as_not_due(db_session, uid):
    save_calc(db_session, uid, 0.0, is_due=False)
    c = stmt(db_session, uid).zakat_check
    assert c.is_zakat_due is False and c.zakat_due_paisa == 0 and c.remaining_paisa == 0


def test_the_latest_calculation_in_the_window_is_the_one_used(db_session, uid):
    save_calc(db_session, uid, 100.0, at=datetime(2026, 2, 1, 10, tzinfo=timezone.utc))
    save_calc(db_session, uid, 200.0, at=datetime(2026, 9, 1, 10, tzinfo=timezone.utc))
    save_calc(db_session, uid, 300.0, at=datetime(2025, 12, 1, 10, tzinfo=timezone.utc))      # outside the window
    save_calc(db_session, uid, 400.0, at=datetime(2027, 2, 1, 10, tzinfo=timezone.utc))       # outside the window
    c = stmt(db_session, uid).zakat_check
    assert (c.zakat_due_paisa, c.calculated_on) == (20_000, date(2026, 9, 1))


def test_calculations_outside_the_window_do_not_count(db_session, uid):
    save_calc(db_session, uid, 100.0, at=datetime(2025, 6, 1, 10, tzinfo=timezone.utc))
    assert stmt(db_session, uid).zakat_check is None


def test_the_calculations_date_is_the_users_local_date(db_session, make_user, clock):
    clock["now"] = datetime(2027, 3, 1, 7, 0, tzinfo=timezone.utc)
    karachi = make_user("Asia/Karachi")
    save_calc(db_session, karachi, 100.0, at=datetime(2026, 12, 31, 20, 0, tzinfo=timezone.utc))     # 01:00 on 1 Jan in Karachi
    assert stmt(db_session, karachi).zakat_check is None                                              # so it is NOT in 2026
    next_year = stmt(db_session, karachi, date_from=date(2027, 1, 1), date_to=date(2028, 1, 1))
    assert next_year.zakat_check is not None and next_year.zakat_check.calculated_on == date(2027, 1, 1)
    utc_user = make_user("UTC")
    save_calc(db_session, utc_user, 100.0, at=datetime(2026, 12, 31, 20, 0, tzinfo=timezone.utc))
    assert stmt(db_session, utc_user).zakat_check.calculated_on == date(2026, 12, 31)


def test_a_calculation_made_late_on_the_last_day_west_of_greenwich_still_belongs_to_that_year(db_session, make_user, clock):
    clock["now"] = datetime(2027, 3, 1, 7, 0, tzinfo=timezone.utc)
    new_york = make_user("America/New_York")
    save_calc(db_session, new_york, 100.0, at=datetime(2027, 1, 1, 3, 0, tzinfo=timezone.utc))        # 22:00 on 31 Dec in New York
    check = stmt(db_session, new_york).zakat_check
    assert check is not None and check.calculated_on == date(2026, 12, 31)
    assert stmt(db_session, new_york, date_from=date(2027, 1, 1), date_to=date(2028, 1, 1)).zakat_check is None


def test_another_users_calculation_is_never_used(db_session, uid, make_user):
    other = make_user()
    save_calc(db_session, other, 99999.0)
    assert stmt(db_session, uid).zakat_check is None


def test_a_calculation_saved_with_no_user_is_never_used(db_session, uid):
    db_session.add(ZakatRecord(user_id=None, user_name="Guest", total_assets=1, total_deductions=0, zakatable_wealth=1,
                               nisab_threshold=1, zakat_due=500.0, is_zakat_due=True,
                               calculated_at=datetime(2026, 6, 1, 10, tzinfo=timezone.utc)))
    db_session.commit()
    assert stmt(db_session, uid).zakat_check is None


def test_an_unusable_saved_amount_is_left_out_not_guessed(db_session, uid, caplog):
    save_calc(db_session, uid, 1e30)                                           # far above the money cap
    assert stmt(db_session, uid).zakat_check is None
    save_calc(db_session, uid, -5.0, at=datetime(2026, 7, 1, 10, tzinfo=timezone.utc))
    assert stmt(db_session, uid).zakat_check is None


# ── float to paisa: the one place a float becomes money ───────────────────────

@pytest.mark.parametrize("value,paisa", [
    (0, 0), (0.0, 0), (1, 100), (1234.57, 123457), (0.1, 10), (0.29, 29), (19.99, 1999), (1.005, 101),
    (2.675, 268), (0.005, 1), (0.004, 0), (30000.0, 3_000_000), (1234567.89, 123456789), (10 ** 10, 10 ** 12),
])
def test_floats_become_paisa_through_decimal(value, paisa):
    assert es.float_rupees_to_paisa(value) == paisa


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -0.01, -1, 10 ** 10 + 1, 1e30,
                                 True, False, None, "12.5", b"1", [1], (1,), complex(1, 0)])
def test_unusable_amounts_are_refused(bad):
    with pytest.raises(ValueError):
        es.float_rupees_to_paisa(bad)


def test_the_conversion_never_goes_through_float_multiplication():
    assert 1.005 * 100 != 100.5                                      # the trap: this is 100.49999999999999
    assert es.float_rupees_to_paisa(1.005) == 101
    assert math.isfinite(1.0)
