"""User A can never read, change, delete or restore user B's expenses (offline)."""
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.database.models import AuditLog, Expense
from app.services import expense_service as es
from tests.conftest import exp

MONTH = dict(date_from=date(2026, 10, 1), date_to=date(2026, 11, 1))


@pytest.fixture
def two(db_session, make_user, clock):
    a, b = make_user(), make_user()
    ea = es.add_expenses(db_session, a, [exp(1000, note="a-private")])[0].expense
    eb = es.add_expenses(db_session, b, [exp(7, note="b-private")])[0].expense
    return a, b, ea, eb


def _row(db, expense_id):
    db.expire_all()
    return db.execute(select(Expense).where(Expense.id == expense_id)).scalar_one()


def test_lists_and_lookups_are_scoped(db_session, two):
    a, b, ea, eb = two
    assert [e.id for e in es.get_expenses(db_session, b).items] == [eb.id]
    assert es.get_expenses(db_session, b, expense_ids=[ea.id]).items == []
    assert es.get_expenses(db_session, a, expense_ids=[eb.id]).items == []


def test_a_paging_cursor_from_one_user_does_not_open_the_other_users_rows(db_session, two):
    a, b, ea, eb = two
    es.add_expenses(db_session, a, [exp(1, date(2026, 10, 29)), exp(2, date(2026, 10, 28))])
    page = es.get_expenses(db_session, a, limit=1)
    assert page.next_before is not None
    assert [e.id for e in es.get_expenses(db_session, b, limit=50, before=page.next_before).items] == []


def test_totals_are_scoped(db_session, two):
    a, b, *_ = two
    assert es.summarize(db_session, a, **MONTH).total_paisa == 1000
    assert es.summarize(db_session, b, **MONTH).total_paisa == 7
    assert es.summarize(db_session, b, group_by="category", **MONTH).groups[0].total_paisa == 7


def test_another_user_cannot_edit(db_session, two):
    a, b, ea, eb = two
    with pytest.raises(es.NotFound):
        es.update_expense(db_session, b, ea.id, 1, {"amount_paisa": 1})
    row = _row(db_session, ea.id)
    assert (row.amount_paisa, row.version, row.note) == (1000, 1, "a-private")


def test_editing_with_a_stale_version_does_not_reveal_that_the_row_exists(db_session, two):
    a, b, ea, eb = two
    es.update_expense(db_session, a, ea.id, 1, {"amount_paisa": 5})                  # A's row is now version 2
    with pytest.raises(es.NotFound):                                                  # NOT VersionConflict: that would leak "exists, version 2"
        es.update_expense(db_session, b, ea.id, 1, {"amount_paisa": 1})


def test_another_user_cannot_delete(db_session, two):
    a, b, ea, eb = two
    res = es.delete_expenses(db_session, b, [ea.id])
    assert (res.changed, res.not_found) == ([], [ea.id])
    assert _row(db_session, ea.id).deleted_at is None
    assert [e.id for e in es.get_expenses(db_session, a).items] == [ea.id]


def test_another_user_cannot_restore(db_session, two):
    a, b, ea, eb = two
    es.delete_expenses(db_session, a, [ea.id])
    res = es.restore_expenses(db_session, b, [ea.id])
    assert (res.changed, res.not_found) == ([], [ea.id])
    assert _row(db_session, ea.id).deleted_at is not None


def test_a_mixed_id_list_only_touches_the_callers_rows(db_session, two):
    a, b, ea, eb = two
    res = es.delete_expenses(db_session, a, [ea.id, eb.id])
    assert (res.changed, res.not_found) == ([ea.id], [eb.id])
    assert _row(db_session, eb.id).deleted_at is None


def test_the_same_client_id_for_two_users_makes_two_independent_rows(db_session, make_user, clock):
    a, b = make_user(), make_user()
    cid = uuid.uuid4()
    ra = es.add_expenses(db_session, a, [exp(111, client_id=cid)])[0]
    rb = es.add_expenses(db_session, b, [exp(222, client_id=cid)])[0]
    assert ra.created and rb.created and ra.expense.id != rb.expense.id
    assert (ra.expense.amount_paisa, rb.expense.amount_paisa) == (111, 222)
    again = es.add_expenses(db_session, b, [exp(333, client_id=cid)])[0]            # B's retry returns B's row, never A's
    assert (again.created, again.expense.id) == (False, rb.expense.id)


def test_a_retry_after_the_other_user_deleted_theirs_is_unaffected(db_session, make_user, clock):
    a, b = make_user(), make_user()
    cid = uuid.uuid4()
    ra = es.add_expenses(db_session, a, [exp(1, client_id=cid)])[0].expense
    es.delete_expenses(db_session, a, [ra.id])
    rb = es.add_expenses(db_session, b, [exp(2, client_id=cid)])[0]
    assert rb.created and not rb.already_deleted


def test_audit_rows_belong_to_the_acting_user(db_session, two):
    a, b, ea, eb = two
    rows = db_session.execute(select(AuditLog.user_id, AuditLog.entity_id)).all()
    assert sorted(rows) == sorted([(a, str(ea.id)), (b, str(eb.id))])


def test_a_caller_cannot_choose_a_user_through_the_input(db_session, two):
    a, b, ea, eb = two
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, a, [exp(1, user_id=b)])
    with pytest.raises(es.InvalidInput):
        es.update_expense(db_session, a, ea.id, 1, {"amount_paisa": 5, "user_id": b})
    assert _row(db_session, ea.id).user_id == a
