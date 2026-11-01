"""get_expenses, summarize, list_categories (offline). Totals are checked against an independent Python sum."""
import random
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import expense_service as es
from tests.conftest import exp


@pytest.fixture
def uid(make_user, clock):
    clock["now"] = datetime(2027, 3, 1, 7, 0, tzinfo=timezone.utc)       # so generated dates up to the end of 2026 are not "in the future"
    return make_user()


def _fill(db, uid, rng, n, start=date(2026, 9, 20), days=100, big=False):
    """Adds n random expenses and returns the plain-Python record of what was added (id, date, amount, sub, category)."""
    cats = es.list_categories(db)
    subs = [(c.slug, s.slug) for c in cats for s in c.subcategories]
    added = []
    for _ in range(n):
        cat, sub = rng.choice(subs)
        category_only = rng.random() < 0.15
        amount = rng.choice([1, 99, 100, 12345, 10 ** 9, 10 ** 12]) if big else rng.randint(1, 500000)
        on = start + timedelta(days=rng.randrange(days))
        item = exp(amount, on, sub=None if category_only else sub, **({"category_slug": cat} if category_only else {}))
        out = es.add_expenses(db, uid, [item])[0].expense
        added.append(dict(id=out.id, on=on, amount=amount, category=cat, sub=None if category_only else sub))
    return added


# ── get_expenses ──────────────────────────────────────────────────────────────

def test_newest_first_and_ties_broken_by_id(db_session, uid):
    a = es.add_expenses(db_session, uid, [exp(1, date(2026, 10, 1))])[0].expense
    b = es.add_expenses(db_session, uid, [exp(2, date(2026, 10, 3))])[0].expense
    c = es.add_expenses(db_session, uid, [exp(3, date(2026, 10, 3))])[0].expense
    assert [e.id for e in es.get_expenses(db_session, uid).items] == [c.id, b.id, a.id]


def test_paging_visits_every_row_once(db_session, uid):
    added = _fill(db_session, uid, random.Random(1), 57, days=6)          # few dates, so many ties
    seen, before = [], None
    for _ in range(12):                                   # 57 rows at 10 per page is 6 pages; the bound stops a broken cursor looping forever
        page = es.get_expenses(db_session, uid, limit=10, before=before)
        seen += [e.id for e in page.items]
        before = page.next_before
        if before is None:
            break
    assert before is None, "paging did not finish"
    assert len(seen) == len(set(seen)) == 57 and set(seen) == {r["id"] for r in added}
    assert seen == [r["id"] for r in sorted(added, key=lambda r: (r["on"], r["id"]), reverse=True)]


def test_the_last_full_page_has_no_next_page(db_session, uid):
    _fill(db_session, uid, random.Random(2), 10)
    page = es.get_expenses(db_session, uid, limit=10)
    assert len(page.items) == 10 and page.next_before is None


def test_window_is_start_inclusive_and_end_exclusive(db_session, uid):
    for d in (date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 31), date(2026, 11, 1)):
        es.add_expenses(db_session, uid, [exp(1, d)])
    got = {e.occurred_on for e in es.get_expenses(db_session, uid, date_from=date(2026, 10, 1), date_to=date(2026, 11, 1)).items}
    assert got == {date(2026, 10, 1), date(2026, 10, 31)}


def test_filters(db_session, uid):
    es.add_expenses(db_session, uid, [exp(1, sub="food.groceries"), exp(2, sub="food.dining_out"), exp(3, sub="transport.fuel"),
                                       exp(4, sub=None, category_slug="food")])
    amounts = lambda **kw: sorted(e.amount_paisa for e in es.get_expenses(db_session, uid, **kw).items)
    assert amounts(category_slug="food") == [1, 2, 4]
    assert amounts(subcategory_slug="food.groceries") == [1]
    assert amounts(subcategory_slug="transport.fuel") == [3]
    with pytest.raises(es.InvalidInput):
        es.get_expenses(db_session, uid, category_slug="nope")
    with pytest.raises(es.InvalidInput):
        es.get_expenses(db_session, uid, category_slug="transport", subcategory_slug="food.groceries")


def test_filter_by_ids(db_session, uid):
    a = es.add_expenses(db_session, uid, [exp(1)])[0].expense
    es.add_expenses(db_session, uid, [exp(2)])
    assert [e.id for e in es.get_expenses(db_session, uid, expense_ids=[a.id]).items] == [a.id]


def test_deleted_rows_are_not_listed(db_session, uid):
    a = es.add_expenses(db_session, uid, [exp(1)])[0].expense
    es.add_expenses(db_session, uid, [exp(2)])
    es.delete_expenses(db_session, uid, [a.id])
    assert [e.amount_paisa for e in es.get_expenses(db_session, uid).items] == [2]
    assert es.get_expenses(db_session, uid, expense_ids=[a.id]).items == []


@pytest.mark.parametrize("kw", [
    dict(limit=0), dict(limit=es.MAX_PAGE_SIZE + 1), dict(limit="5"), dict(limit=True),
    dict(date_from="2026-10-01"), dict(date_to=20261001),
    dict(date_from=date(2026, 11, 1), date_to=date(2026, 10, 1)),
    dict(before="x"), dict(before=(date(2026, 10, 1), "1")), dict(before=("2026-10-01", 1)),
    dict(expense_ids=[]),
])
def test_bad_arguments_are_rejected(db_session, uid, kw):
    with pytest.raises(es.InvalidInput):
        es.get_expenses(db_session, uid, **kw)


# ── summarize ─────────────────────────────────────────────────────────────────

def _expected(added, d_from, d_to):
    rows = [r for r in added if d_from <= r["on"] < d_to]
    by = lambda key: _sum_by(rows, key)
    return dict(total=sum(r["amount"] for r in rows), count=len(rows), rows=rows, by=by)


def _sum_by(rows, key):
    out = defaultdict(lambda: [0, 0])
    for r in rows:
        k = key(r)
        out[k][0] += r["amount"]
        out[k][1] += 1
    return {k: tuple(v) for k, v in out.items()}


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_totals_equal_an_independent_python_sum(db_session, uid, make_user, seed):
    rng = random.Random(seed)
    added = _fill(db_session, uid, rng, 150)
    other = make_user()
    _fill(db_session, other, random.Random(seed + 100), 60)                               # another user's data must not leak in
    es.delete_expenses(db_session, uid, [r["id"] for r in rng.sample(added, 25)])         # deleted rows must not count
    deleted = set(_deleted_ids(db_session, uid))
    live = [r for r in added if r["id"] not in deleted]
    assert len(deleted) == 25 and len(live) == 125

    for d_from, d_to in [(date(2026, 10, 1), date(2026, 11, 1)), (date(2026, 9, 20), date(2026, 12, 31)),
                         (date(2026, 10, 15), date(2026, 10, 16)), (date(2026, 12, 25), date(2027, 1, 1))]:
        want = _expected(live, d_from, d_to)
        got = es.summarize(db_session, uid, date_from=d_from, date_to=d_to)
        assert (got.total_paisa, got.count, got.groups) == (want["total"], want["count"], [])

        g = es.summarize(db_session, uid, date_from=d_from, date_to=d_to, group_by="category")
        assert {x.key: (x.total_paisa, x.count) for x in g.groups} == want["by"](lambda r: r["category"])
        assert g.total_paisa == sum(x.total_paisa for x in g.groups) == want["total"]
        assert [x.total_paisa for x in g.groups] == sorted((x.total_paisa for x in g.groups), reverse=True)

        g = es.summarize(db_session, uid, date_from=d_from, date_to=d_to, group_by="subcategory")
        assert {x.key: (x.total_paisa, x.count) for x in g.groups} == want["by"](lambda r: r["sub"] or r["category"] + ".unspecified")
        assert g.total_paisa == sum(x.total_paisa for x in g.groups)

        g = es.summarize(db_session, uid, date_from=d_from, date_to=d_to, group_by="day")
        assert {x.key: (x.total_paisa, x.count) for x in g.groups} == want["by"](lambda r: r["on"].isoformat())
        assert [x.key for x in g.groups] == sorted(x.key for x in g.groups)

        g = es.summarize(db_session, uid, date_from=d_from, date_to=d_to, group_by="month")
        assert {x.key: (x.total_paisa, x.count) for x in g.groups} == want["by"](lambda r: f"{r['on'].year:04d}-{r['on'].month:02d}")
        assert g.total_paisa == sum(x.total_paisa for x in g.groups)

        for cat in ("food", "charity", "financial"):
            only = [r for r in want["rows"] if r["category"] == cat]
            got = es.summarize(db_session, uid, date_from=d_from, date_to=d_to, category_slug=cat)
            assert (got.total_paisa, got.count) == (sum(r["amount"] for r in only), len(only))


def _deleted_ids(db, uid):
    from sqlalchemy import select
    from app.database.models import Expense
    return [i for (i,) in db.execute(select(Expense.id).where(Expense.user_id == uid, Expense.deleted_at.is_not(None)))]


def test_sums_larger_than_32_bits_do_not_overflow(db_session, uid):
    es.add_expenses(db_session, uid, [exp(10 ** 12, date(2026, 10, 1)) for _ in range(5)])
    got = es.summarize(db_session, uid, date_from=date(2026, 10, 1), date_to=date(2026, 11, 1), group_by="category")
    assert got.total_paisa == 5 * 10 ** 12 and got.groups[0].total_paisa == 5 * 10 ** 12 and isinstance(got.total_paisa, int)


def test_summary_window_edges(db_session, uid):
    for d, amt in ((date(2026, 9, 30), 1), (date(2026, 10, 1), 10), (date(2026, 10, 31), 100), (date(2026, 11, 1), 1000)):
        es.add_expenses(db_session, uid, [exp(amt, d)])
    got = es.summarize(db_session, uid, date_from=date(2026, 10, 1), date_to=date(2026, 11, 1))
    assert (got.total_paisa, got.count) == (110, 2)                                  # first day in, end day out


def test_an_empty_window_is_zero_not_none(db_session, uid):
    got = es.summarize(db_session, uid, date_from=date(2026, 1, 1), date_to=date(2026, 2, 1), group_by="category")
    assert (got.total_paisa, got.count, got.groups) == (0, 0, [])
    same = es.summarize(db_session, uid, date_from=date(2026, 1, 1), date_to=date(2026, 1, 1))
    assert same.total_paisa == 0


def test_months_are_not_mixed_across_the_new_year(db_session, uid, clock):
    for d in (date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 2)):
        es.add_expenses(db_session, uid, [exp(10, d)])
    g = es.summarize(db_session, uid, date_from=date(2026, 12, 1), date_to=date(2027, 2, 1), group_by="month")
    assert [(x.key, x.total_paisa) for x in g.groups] == [("2026-12", 10), ("2027-01", 20)]


def test_restoring_brings_the_amount_back_into_the_total(db_session, uid):
    a = es.add_expenses(db_session, uid, [exp(100, date(2026, 10, 5))])[0].expense
    window = dict(date_from=date(2026, 10, 1), date_to=date(2026, 11, 1))
    es.delete_expenses(db_session, uid, [a.id])
    assert es.summarize(db_session, uid, **window).total_paisa == 0
    es.restore_expenses(db_session, uid, [a.id])
    assert es.summarize(db_session, uid, **window).total_paisa == 100


@pytest.mark.parametrize("kw", [
    dict(date_from="2026-10-01", date_to=date(2026, 11, 1)), dict(date_from=date(2026, 10, 1), date_to=None),
    dict(date_from=date(2026, 11, 1), date_to=date(2026, 10, 1)), dict(group_by="year"), dict(category_slug="nope"),
])
def test_summarize_rejects_bad_arguments(db_session, uid, kw):
    args = dict(date_from=date(2026, 10, 1), date_to=date(2026, 11, 1))
    args.update(kw)
    with pytest.raises(es.InvalidInput):
        es.summarize(db_session, uid, **args)


# ── list_categories ───────────────────────────────────────────────────────────

def test_list_categories(db_session):
    cats = es.list_categories(db_session)
    assert [c.slug for c in cats] == ["food", "transport", "bills", "subscriptions", "education", "health", "charity",
                                       "household", "clothing", "family", "entertainment", "financial", "other"]
    assert sum(len(c.subcategories) for c in cats) == 40
    charity = next(c for c in cats if c.slug == "charity")
    assert charity.is_charity and [s.slug for s in charity.subcategories] == ["charity.zakat", "charity.sadaqah", "charity.fitrana", "charity.other"]
    assert all(c.name_ur and all(s.name_ur for s in c.subcategories) for c in cats)
