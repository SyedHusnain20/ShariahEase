"""Riba / doubtful reminders in the service: set on add, re-worked on edit, and a dismissal that stays dismissed."""
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.database.models import AuditLog
from app.services import expense_service as es
from tests.conftest import exp


@pytest.fixture
def uid(make_user, clock):
    clock["now"] = datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc)
    return make_user()


def add(db, uid, **kw):
    [r] = es.add_expenses(db, uid, [exp(**kw)])
    return r.expense


def edit(db, uid, e, **changes):
    return es.update_expense(db, uid, e.id, e.version, changes)


# ── a flag is set when the expense is added ───────────────────────────────────

def test_a_note_with_interest_raises_riba_on_an_ordinary_category(db_session, uid):
    e = add(db_session, uid, note="bank interest for March")
    assert e.subcategory_slug == "food.groceries" and e.shariah_flag == "riba"


def test_an_ordinary_note_raises_nothing(db_session, uid):
    assert add(db_session, uid, note="atta and daal").shariah_flag is None


def test_a_no_interest_note_raises_nothing(db_session, uid):
    assert add(db_session, uid, note="interest-free loan from a friend").shariah_flag is None


def test_the_subcategory_default_is_kept_when_the_note_is_innocent(db_session, uid):
    assert add(db_session, uid, sub="financial.markup_interest", note="monthly").shariah_flag == "riba"
    assert add(db_session, uid, sub="financial.markup_interest", note="bila sood").shariah_flag == "riba"
    assert add(db_session, uid, sub="financial.loan_installment", note=None).shariah_flag == "doubtful"


def test_a_stronger_note_raises_a_doubtful_default_to_riba(db_session, uid):
    assert add(db_session, uid, sub="financial.loan_installment", note="includes sood").shariah_flag == "riba"


def test_markup_in_a_note_is_doubtful_not_riba(db_session, uid):
    assert add(db_session, uid, note="shop markup").shariah_flag == "doubtful"


def test_urdu_script_and_roman_urdu_notes_are_read(db_session, uid):
    assert add(db_session, uid, note="بینک کا سود").shariah_flag == "riba"
    assert add(db_session, uid, note="Sood ki qist").shariah_flag == "riba"
    assert add(db_session, uid, note="سودا لیا").shariah_flag is None


def test_a_new_expense_starts_with_the_reminder_showing(db_session, uid):
    assert add(db_session, uid, note="interest").shariah_flag_dismissed is False


# ── a flag is re-worked when the note or the category changes ─────────────────

def test_editing_the_note_adds_a_flag(db_session, uid):
    e = add(db_session, uid, note="school")
    assert edit(db_session, uid, e, note="late interest").shariah_flag == "riba"


def test_editing_the_note_removes_a_flag_the_note_caused(db_session, uid):
    e = add(db_session, uid, note="interest")
    out = edit(db_session, uid, e, note="atta")
    assert out.shariah_flag is None and out.shariah_flag_dismissed is False


def test_clearing_the_note_removes_a_flag_the_note_caused(db_session, uid):
    e = add(db_session, uid, note="interest")
    assert edit(db_session, uid, e, note=None).shariah_flag is None


def test_editing_the_note_never_lowers_the_subcategory_default(db_session, uid):
    e = add(db_session, uid, sub="financial.markup_interest", note="interest")
    out = edit(db_session, uid, e, note="atta")
    assert out.shariah_flag == "riba"


def test_the_note_edit_uses_the_expenses_current_subcategory(db_session, uid):
    e = add(db_session, uid, sub="financial.loan_installment")
    assert edit(db_session, uid, e, note="atta").shariah_flag == "doubtful"
    e = es.get_expenses(db_session, uid, expense_ids=[e.id]).items[0]
    assert edit(db_session, uid, e, note="includes interest").shariah_flag == "riba"


def test_changing_the_category_recomputes_from_the_new_default_and_the_existing_note(db_session, uid):
    e = add(db_session, uid, note="sood")
    out = edit(db_session, uid, e, subcategory_slug="financial.loan_installment")
    assert out.shariah_flag == "riba"                                   # the note still says sood
    e2 = add(db_session, uid, note="atta")
    out2 = edit(db_session, uid, e2, subcategory_slug="financial.loan_installment")
    assert out2.shariah_flag == "doubtful"
    out3 = edit(db_session, uid, out2, subcategory_slug="food.groceries")
    assert out3.shariah_flag is None


def test_changing_the_category_and_the_note_together_uses_the_new_note(db_session, uid):
    e = add(db_session, uid, note="sood")
    out = edit(db_session, uid, e, subcategory_slug="food.groceries", note="atta")
    assert out.shariah_flag is None


# ── dismissal ────────────────────────────────────────────────────────────────

def test_dismissing_hides_the_reminder_but_keeps_the_flag(db_session, uid):
    e = add(db_session, uid, note="interest")
    out = edit(db_session, uid, e, shariah_flag_dismissed=True)
    assert out.shariah_flag == "riba" and out.shariah_flag_dismissed is True and out.version == e.version + 1


def test_a_dismissed_flag_stays_dismissed_after_other_edits(db_session, uid):
    e = add(db_session, uid, note="interest")
    e = edit(db_session, uid, e, shariah_flag_dismissed=True)
    e = edit(db_session, uid, e, amount_paisa=777)
    e = edit(db_session, uid, e, occurred_on=date(2026, 11, 1))
    e = edit(db_session, uid, e, payment_method="card")
    assert (e.shariah_flag, e.shariah_flag_dismissed) == ("riba", True)


def test_a_dismissed_flag_stays_dismissed_when_the_note_is_edited_and_the_flag_is_unchanged(db_session, uid):
    e = add(db_session, uid, note="interest")
    e = edit(db_session, uid, e, shariah_flag_dismissed=True)
    e = edit(db_session, uid, e, note="interest again, April")
    assert (e.shariah_flag, e.shariah_flag_dismissed) == ("riba", True)


def test_a_dismissed_flag_stays_dismissed_when_the_category_changes_to_the_same_default_via_the_same_category(db_session, uid):
    e = add(db_session, uid, sub="financial.markup_interest")
    e = edit(db_session, uid, e, shariah_flag_dismissed=True)
    e = edit(db_session, uid, e, amount_paisa=5)
    assert e.shariah_flag_dismissed is True


def test_a_different_flag_after_a_note_edit_shows_again(db_session, uid):
    e = add(db_session, uid, note="markup")                              # doubtful
    e = edit(db_session, uid, e, shariah_flag_dismissed=True)
    e = edit(db_session, uid, e, note="sood")                            # now riba: a new finding
    assert (e.shariah_flag, e.shariah_flag_dismissed) == ("riba", False)


def test_a_new_category_is_a_new_classification_so_the_reminder_shows_again(db_session, uid):
    e = add(db_session, uid, sub="financial.loan_installment")
    e = edit(db_session, uid, e, shariah_flag_dismissed=True)
    e = edit(db_session, uid, e, subcategory_slug="financial.markup_interest")
    assert (e.shariah_flag, e.shariah_flag_dismissed) == ("riba", False)


def test_a_dismissal_can_be_taken_back(db_session, uid):
    e = add(db_session, uid, note="interest")
    e = edit(db_session, uid, e, shariah_flag_dismissed=True)
    e = edit(db_session, uid, e, shariah_flag_dismissed=False)
    assert e.shariah_flag_dismissed is False


def test_dismissing_an_expense_with_no_flag_is_refused(db_session, uid):
    e = add(db_session, uid, note="atta")
    with pytest.raises(es.InvalidInput) as err:
        edit(db_session, uid, e, shariah_flag_dismissed=True)
    assert err.value.errors[0]["field"] == "shariah_flag_dismissed"
    assert es.get_expenses(db_session, uid, expense_ids=[e.id]).items[0].version == e.version      # nothing changed


def test_dismissal_must_be_a_real_boolean(db_session, uid):
    e = add(db_session, uid, note="interest")
    for bad in (1, "true", "yes", None):
        with pytest.raises(es.InvalidInput):
            edit(db_session, uid, e, shariah_flag_dismissed=bad)


def test_dismissing_with_a_stale_version_conflicts_and_changes_nothing(db_session, uid):
    e = add(db_session, uid, note="interest")
    edit(db_session, uid, e, amount_paisa=5)                             # the row is now at version 2
    with pytest.raises(es.VersionConflict) as err:
        es.update_expense(db_session, uid, e.id, e.version, {"shariah_flag_dismissed": True})
    assert err.value.current_version == 2
    assert es.get_expenses(db_session, uid, expense_ids=[e.id]).items[0].shariah_flag_dismissed is False


def test_a_note_edit_with_a_stale_version_conflicts_before_the_flag_is_worked_out(db_session, uid):
    e = add(db_session, uid, note="atta")
    edit(db_session, uid, e, amount_paisa=5)
    with pytest.raises(es.VersionConflict):
        es.update_expense(db_session, uid, e.id, e.version, {"note": "interest"})
    assert es.get_expenses(db_session, uid, expense_ids=[e.id]).items[0].shariah_flag is None


def test_nobody_can_dismiss_another_users_reminder(db_session, uid, make_user):
    other = make_user()
    e = add(db_session, uid, note="interest")
    with pytest.raises(es.NotFound):
        es.update_expense(db_session, other, e.id, e.version, {"shariah_flag_dismissed": True})
    assert es.get_expenses(db_session, uid, expense_ids=[e.id]).items[0].shariah_flag_dismissed is False


def test_a_deleted_expense_cannot_be_dismissed(db_session, uid):
    e = add(db_session, uid, note="interest")
    es.delete_expenses(db_session, uid, [e.id])
    with pytest.raises(es.NotFound):
        es.update_expense(db_session, uid, e.id, e.version + 1, {"shariah_flag_dismissed": True})


def test_dismissal_is_audited_with_field_names_only(db_session, uid):
    e = add(db_session, uid, note="SECRET-NOTE interest")
    edit(db_session, uid, e, shariah_flag_dismissed=True)
    rows = db_session.execute(select(AuditLog.action, AuditLog.detail).order_by(AuditLog.id)).all()
    assert [r[0] for r in rows] == ["expense.create", "expense.update"]
    assert rows[1][1] == {"fields": ["shariah_flag_dismissed"], "version": 2}
    assert "SECRET" not in str(rows)


def test_the_flag_is_also_worked_out_for_ai_and_whatsapp_sources(db_session, uid):
    [r] = es.add_expenses(db_session, uid, [exp(note="interest")], source="ai_chat")
    assert r.expense.shariah_flag == "riba" and r.expense.source == "ai_chat"


def test_the_export_does_not_carry_the_flag_or_the_note(db_session, uid):
    # The export is six plain fields (see privacy page and test_export.py). The flag, its dismissed state and the
    # note stay inside the app.
    e = add(db_session, uid, note="interest")
    edit(db_session, uid, e, shariah_flag_dismissed=True)
    [row] = list(es.export_expenses(db_session, uid))
    got = row.model_dump()
    assert set(got) == {"id", "occurred_on", "amount_paisa", "category", "sub_category", "source"}
    assert "interest" not in repr(got)
