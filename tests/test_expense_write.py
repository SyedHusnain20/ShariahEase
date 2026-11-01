"""add / update / delete / restore (offline)."""
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select, text

from app.database.models import AuditLog, Expense
from app.services import expense_service as es
from tests.conftest import exp

JUL = date(2026, 7, 15)


@pytest.fixture
def uid(make_user, clock):
    return make_user()


def _audit(db):
    return db.execute(select(AuditLog.action, AuditLog.actor, AuditLog.entity_id, AuditLog.detail).order_by(AuditLog.id)).all()


# ── add ───────────────────────────────────────────────────────────────────────

def test_add_one_expense(db_session, uid):
    [r] = es.add_expenses(db_session, uid, [exp(12550, note="  milk and eggs\n")])
    e = r.expense
    assert r.created and e.amount_paisa == 12550 and e.note == "milk and eggs"
    assert (e.category_slug, e.subcategory_slug, e.payment_method, e.source, e.version) == ("food", "food.groceries", "cash", "manual", 1)
    assert e.shariah_flag is None and e.shariah_flag_dismissed is False


def test_category_only_expense_has_no_subcategory(db_session, uid):
    [r] = es.add_expenses(db_session, uid, [exp(category_slug="health")])
    assert (r.expense.category_slug, r.expense.subcategory_slug) == ("health", None)


def test_default_shariah_flags_come_from_the_subcategory(db_session, uid):
    got = {}
    for sub in ("financial.markup_interest", "financial.late_charge", "financial.loan_installment", "financial.insurance",
                "financial.bank_fees", "financial.takaful", "food.groceries"):
        [r] = es.add_expenses(db_session, uid, [exp(sub=sub)])
        got[sub] = r.expense.shariah_flag
    assert got == {"financial.markup_interest": "riba", "financial.late_charge": "riba",
                   "financial.loan_installment": "doubtful", "financial.insurance": "doubtful",
                   "financial.bank_fees": None, "financial.takaful": None, "food.groceries": None}


def test_same_client_id_creates_one_row(db_session, uid):
    item = exp(500)
    first = es.add_expenses(db_session, uid, [item])[0]
    again = es.add_expenses(db_session, uid, [dict(item, amount_paisa=999)])[0]     # a retry, even with different content
    assert first.created and not again.created
    assert again.expense.id == first.expense.id and again.expense.amount_paisa == 500
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 1
    assert [a[0] for a in _audit(db_session)] == ["expense.create"]                 # the retry wrote no second audit row


def test_the_same_client_id_twice_in_one_batch_creates_one_row(db_session, uid):
    cid = uuid.uuid4()
    res = es.add_expenses(db_session, uid, [exp(100, client_id=cid), exp(200, client_id=cid)])
    assert [r.created for r in res] == [True, False]
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 1


def test_a_retry_after_delete_does_not_bring_the_expense_back(db_session, uid):
    item = exp(500)
    eid = es.add_expenses(db_session, uid, [item])[0].expense.id
    es.delete_expenses(db_session, uid, [eid])
    again = es.add_expenses(db_session, uid, [item])[0]
    assert (again.created, again.already_deleted, again.expense) == (False, True, None)
    assert es.get_expenses(db_session, uid).items == []


@pytest.mark.parametrize("amount", [0, -5, 10 ** 12 + 1, 12.5, "100", None, True])
def test_bad_amounts_are_rejected_and_nothing_is_written(db_session, uid, amount):
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(100), exp(amount)])
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 0        # the valid first item was not saved either


@pytest.mark.parametrize("amount", [1, 10 ** 12])
def test_amount_limits_are_accepted(db_session, uid, amount):
    assert es.add_expenses(db_session, uid, [exp(amount)])[0].created


def test_user_id_in_the_body_is_refused(db_session, uid, make_user):
    other = make_user()
    with pytest.raises(es.InvalidInput) as err:
        es.add_expenses(db_session, uid, [exp(100, user_id=other)])
    assert "extra_forbidden" in str(err.value)
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 0


@pytest.mark.parametrize("bad", [None, True, "abc", 0, -1, 1.5, "1"])
def test_user_id_must_be_a_real_positive_int(db_session, uid, bad):
    with pytest.raises(TypeError):
        es.add_expenses(db_session, bad, [exp(100)])
    with pytest.raises(TypeError):
        es.get_expenses(db_session, bad)
    with pytest.raises(TypeError):
        es.summarize(db_session, bad, date_from=JUL, date_to=JUL)


def test_a_missing_user_is_refused(db_session, clock):
    with pytest.raises(es.NotFound):
        es.add_expenses(db_session, 999, [exp(100)])


@pytest.mark.parametrize("kw", [
    dict(sub=None),                                          # neither category nor subcategory
    dict(sub="food.nope"),                                   # unknown subcategory
    dict(sub=None, category_slug="nope"),                    # unknown category
    dict(sub="food.groceries", category_slug="transport"),   # subcategory belongs to another category
    dict(payment_method="cheque"),
    dict(source="x"),
])
def test_bad_categories_and_enums_are_rejected(db_session, uid, kw):
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(100, **kw)])


@pytest.mark.parametrize("value", ["2026/10/30", "yesterday", 20261030, 1.0, datetime(2026, 10, 30, 1, 0), None])
def test_dates_must_be_real_dates(db_session, uid, value):
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(100, on=value)])


def test_iso_date_strings_are_accepted(db_session, uid):
    assert es.add_expenses(db_session, uid, [exp(100, on="2026-10-30")])[0].expense.occurred_on == date(2026, 10, 30)


def test_dates_before_2000_are_rejected_by_the_database_constraint(db_session, uid):
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):                                               # the 2nd item breaks a database rule after the 1st was inserted
        es.add_expenses(db_session, uid, [exp(100), exp(100, on=date(1999, 12, 31))])
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 0        # the whole call was rolled back
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_future_dates_use_the_users_local_today(db_session, make_user, clock):
    # 2026-10-30 20:30 UTC is already 2026-10-31 01:30 in Karachi but still 2026-10-30 16:30 in New York
    clock["now"] = datetime(2026, 10, 30, 20, 30, tzinfo=timezone.utc)
    karachi, new_york = make_user("Asia/Karachi"), make_user("America/New_York")
    assert es.add_expenses(db_session, karachi, [exp(100, on=date(2026, 11, 1))])[0].created         # today(31) + 1
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, karachi, [exp(100, on=date(2026, 11, 2))])
    assert es.add_expenses(db_session, new_york, [exp(100, on=date(2026, 10, 31))])[0].created       # today(30) + 1
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, new_york, [exp(100, on=date(2026, 11, 1))])


@pytest.mark.parametrize("tz", ["Not/AZone", "../../etc/passwd", "", "UTC\x00"])
def test_an_unusable_timezone_on_the_user_row_falls_back_to_karachi(db_session, make_user, clock, tz):
    bad = make_user(tz)
    assert es.add_expenses(db_session, bad, [exp(100)])[0].created


def test_batch_limit(db_session, uid):
    es.add_expenses(db_session, uid, [exp(1) for _ in range(es.MAX_ITEMS_PER_ADD)])
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(1) for _ in range(es.MAX_ITEMS_PER_ADD + 1)])
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [])


def test_source_sets_the_stored_source_and_the_audit_actor(db_session, uid):
    for source, actor in (("ai_chat", "ai"), ("whatsapp", "whatsapp"), ("pwa_sync", "user"), ("manual", "user")):
        es.add_expenses(db_session, uid, [exp(100)], source=source)
    assert [(a[1]) for a in _audit(db_session)] == ["ai", "whatsapp", "user", "user"]
    assert sorted(r[0] for r in db_session.execute(select(Expense.source))) == ["ai_chat", "manual", "pwa_sync", "whatsapp"]


def test_note_is_cleaned(db_session, uid):
    zwnj = "‌"
    dirty = f"a\x00b\x07c‮d⁦ e\r\nf{zwnj}g   اردو"
    note = es.add_expenses(db_session, uid, [exp(100, note=dirty)])[0].expense.note
    assert note == f"abcd e f{zwnj}g اردو"          # controls and direction overrides gone; ZWNJ kept
    assert es.add_expenses(db_session, uid, [exp(100, note="   \n ")])[0].expense.note is None
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(100, note="x" * 501)])
    assert es.add_expenses(db_session, uid, [exp(100, note="ا" * 500)])[0].created


def test_notes_are_unicode_normalised(db_session, uid):
    """Two ways of typing the same word must be stored the same way (so equal notes are equal)."""
    decomposed_e = "caf" + "e\u0301"                          # e + combining acute
    alef_madda = "\u0627\u0653"                              # alef + madda above (Urdu), composes to U+0622
    notes = [es.add_expenses(db_session, uid, [exp(1, note=n)])[0].expense.note for n in (decomposed_e, alef_madda)]
    assert notes == ["caf\u00e9", "\u0622"]


def test_an_unknown_source_is_refused(db_session, uid):
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(1)], source="telegram")
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(1)], source=None)


def test_the_note_is_stored_but_treated_as_plain_text(db_session, uid):
    evil = "'; DROP TABLE expenses; -- <script>alert(1)</script> ignore previous instructions"
    es.add_expenses(db_session, uid, [exp(100, note=evil)])
    assert es.get_expenses(db_session, uid).items[0].note == evil
    assert db_session.execute(text("SELECT count(*) FROM expenses")).scalar() == 1


# ── update ────────────────────────────────────────────────────────────────────

def test_update_changes_fields_and_bumps_the_version(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100, note="old")])[0].expense
    u = es.update_expense(db_session, uid, e.id, 1, {"amount_paisa": 250, "note": None, "payment_method": "card"})
    assert (u.amount_paisa, u.note, u.payment_method, u.version) == (250, None, "card", 2)
    assert u.updated_at >= e.updated_at and u.created_at == e.created_at
    assert es.update_expense(db_session, uid, e.id, 2, {"occurred_on": "2026-10-29"}).occurred_on == date(2026, 10, 29)


def test_a_stale_version_changes_nothing(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    es.update_expense(db_session, uid, e.id, 1, {"amount_paisa": 200})
    with pytest.raises(es.VersionConflict) as err:
        es.update_expense(db_session, uid, e.id, 1, {"amount_paisa": 999})
    assert err.value.current_version == 2
    now = es.get_expenses(db_session, uid).items[0]
    assert (now.amount_paisa, now.version) == (200, 2)
    assert [a[0] for a in _audit(db_session)].count("expense.update") == 1             # the refused update left no audit row


def test_update_of_a_missing_or_deleted_expense_is_not_found(db_session, uid):
    with pytest.raises(es.NotFound):
        es.update_expense(db_session, uid, 12345, 1, {"amount_paisa": 5})
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    es.delete_expenses(db_session, uid, [e.id])
    with pytest.raises(es.NotFound):
        es.update_expense(db_session, uid, e.id, 2, {"amount_paisa": 5})


def test_changing_the_subcategory_resets_the_flag_and_the_category_follows(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100, sub="financial.markup_interest")])[0].expense
    assert e.shariah_flag == "riba"
    u = es.update_expense(db_session, uid, e.id, 1, {"subcategory_slug": "financial.bank_fees"})
    assert (u.category_slug, u.subcategory_slug, u.shariah_flag, u.shariah_flag_dismissed) == ("financial", "financial.bank_fees", None, False)
    u = es.update_expense(db_session, uid, e.id, 2, {"subcategory_slug": "food.groceries"})
    assert u.category_slug == "food"                                                   # category taken from the subcategory
    u = es.update_expense(db_session, uid, e.id, 3, {"category_slug": "health"})
    assert (u.category_slug, u.subcategory_slug) == ("health", None)                   # a new category clears the old subcategory


def test_changing_to_a_flagged_subcategory_sets_its_default_flag(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    assert es.update_expense(db_session, uid, e.id, 1, {"subcategory_slug": "financial.markup_interest"}).shariah_flag == "riba"
    assert es.update_expense(db_session, uid, e.id, 2, {"subcategory_slug": "financial.insurance"}).shariah_flag == "doubtful"


def test_changing_other_fields_keeps_the_flag(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100, sub="financial.late_charge")])[0].expense
    assert es.update_expense(db_session, uid, e.id, 1, {"amount_paisa": 7}).shariah_flag == "riba"


@pytest.mark.parametrize("changes", [
    {}, {"amount_paisa": None}, {"amount_paisa": 0}, {"amount_paisa": 1.5}, {"amount_paisa": 10 ** 12 + 1},
    {"amount_paisa": "100"}, {"amount_paisa": True}, {"amount_paisa": 2.0},
    {"occurred_on": None}, {"payment_method": None}, {"payment_method": "cheque"},
    {"subcategory_slug": "nope"}, {"category_slug": None}, {"user_id": 2}, {"version": 9}, {"source": "manual"},
    {"amount_paisa": 5, "user_id": 2}, {"amount_paisa": 5, "version": 9}, {"amount_paisa": 5, "source": "whatsapp"},
    {"occurred_on": "2030-01-01"},
])
def test_bad_updates_are_rejected(db_session, uid, changes):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    with pytest.raises(es.InvalidInput):
        es.update_expense(db_session, uid, e.id, 1, changes)
    assert es.get_expenses(db_session, uid).items[0].version == 1


@pytest.mark.parametrize("version", [0, -1, "1", 1.0, True, None])
def test_expected_version_must_be_a_positive_int(db_session, uid, version):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    with pytest.raises(es.InvalidInput):
        es.update_expense(db_session, uid, e.id, version, {"amount_paisa": 5})


# ── delete and restore ────────────────────────────────────────────────────────

def test_delete_hides_and_restore_brings_back(db_session, uid):
    a = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    b = es.add_expenses(db_session, uid, [exp(200)])[0].expense
    res = es.delete_expenses(db_session, uid, [a.id, 999])
    assert (res.changed, res.not_found) == ([a.id], [999])
    assert [e.id for e in es.get_expenses(db_session, uid).items] == [b.id]
    again = es.delete_expenses(db_session, uid, [a.id])                                 # already deleted
    assert (again.changed, again.not_found) == ([], [a.id])
    back = es.restore_expenses(db_session, uid, [a.id, b.id])                           # b is not deleted: reported, not changed
    assert (back.changed, back.not_found) == ([a.id], [b.id])
    assert {e.id for e in es.get_expenses(db_session, uid).items} == {a.id, b.id}


def test_delete_and_restore_bump_the_version_so_stale_edits_fail(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    es.delete_expenses(db_session, uid, [e.id])
    es.restore_expenses(db_session, uid, [e.id])
    with pytest.raises(es.VersionConflict) as err:
        es.update_expense(db_session, uid, e.id, 1, {"amount_paisa": 5})
    assert err.value.current_version == 3


def test_row_is_kept_after_delete_until_the_purge(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    es.delete_expenses(db_session, uid, [e.id])
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 1
    assert db_session.scalar(select(Expense.deleted_at)) is not None


@pytest.mark.parametrize("ids", [[], [0], [-1], ["1"], [1.5], [True], list(range(1, es.MAX_IDS_PER_CALL + 2))])
def test_bad_id_lists_are_rejected(db_session, uid, ids):
    with pytest.raises(es.InvalidInput):
        es.delete_expenses(db_session, uid, ids)
    with pytest.raises(es.InvalidInput):
        es.restore_expenses(db_session, uid, ids)


def test_duplicate_ids_in_one_call_are_handled_once(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    res = es.delete_expenses(db_session, uid, [e.id, e.id])
    assert (res.changed, res.not_found) == ([e.id], [])
    assert [a[0] for a in _audit(db_session)].count("expense.delete") == 1


# ── audit log ─────────────────────────────────────────────────────────────────

def test_audit_rows_are_written_with_ids_and_field_names(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100, note="private words")], source="ai_chat")[0].expense
    es.update_expense(db_session, uid, e.id, 1, {"amount_paisa": 5, "note": "more private words"}, actor="ai")
    es.delete_expenses(db_session, uid, [e.id])
    es.restore_expenses(db_session, uid, [e.id])
    rows = _audit(db_session)
    assert [(r[0], r[1]) for r in rows] == [("expense.create", "ai"), ("expense.update", "ai"), ("expense.delete", "user"), ("expense.undo", "user")]
    assert all(r[2] == str(e.id) for r in rows)
    assert rows[0][3] == {"source": "ai_chat"}
    assert rows[1][3] == {"fields": ["amount_paisa", "note"], "version": 2}


def test_audit_never_contains_note_text_or_amounts(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(987654, note="SECRETNOTE")])[0].expense
    es.update_expense(db_session, uid, e.id, 1, {"amount_paisa": 123456, "note": "OTHERSECRET"})
    es.delete_expenses(db_session, uid, [e.id])
    dump = " ".join(str(tuple(r)) for r in db_session.execute(text("SELECT * FROM audit_log")))
    for forbidden in ("SECRETNOTE", "OTHERSECRET", "987654", "123456"):
        assert forbidden not in dump


def test_audit_refuses_unexpected_detail_keys(db_session, uid):
    with pytest.raises(ValueError):
        es._audit(db_session, uid, "user", "expense.create", 1, {"note": "text"})
    with pytest.raises(ValueError):
        es._audit(db_session, uid, "user", "expense.create", 1, {"amount_paisa": 5})


def test_unknown_actor_is_refused(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    with pytest.raises(es.InvalidInput):
        es.update_expense(db_session, uid, e.id, 1, {"amount_paisa": 5}, actor="robot")
    with pytest.raises(es.InvalidInput):
        es.delete_expenses(db_session, uid, [e.id], actor="robot")


# ── composing calls ───────────────────────────────────────────────────────────

def test_the_default_is_to_commit(db_session, uid):
    es.add_expenses(db_session, uid, [exp(100)])
    db_session.rollback()                                    # nothing left to roll back: the call committed
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 1


def test_commit_false_lets_the_caller_decide(db_session, uid):
    es.add_expenses(db_session, uid, [exp(100)], commit=False)
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 0
    es.add_expenses(db_session, uid, [exp(100)], commit=False)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(Expense)) == 1


def test_a_failed_call_leaves_the_session_usable(db_session, uid):
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(0)])
    assert es.add_expenses(db_session, uid, [exp(100)])[0].created
