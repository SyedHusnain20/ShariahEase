"""Notes are personal data: they must not appear in errors, logs or the audit trail (offline)."""
import logging
from datetime import date

import pytest

from app.services import expense_service as es
from tests.conftest import exp

SECRET = "SECRETNOTE-gift-for-ammi-rs-5000"


@pytest.fixture
def uid(make_user, clock):
    return make_user()


def test_validation_errors_do_not_echo_the_note(db_session, uid):
    cases = [
        exp(0, note=SECRET),                                        # bad amount, valid note
        exp(100, note=SECRET + "x" * 600),                          # note too long
        exp(100, note=SECRET, subcategory_slug="food.nope"),        # unknown category
        exp(100, note=SECRET, on="not a date"),
        exp(100, note=SECRET, user_id=3),
    ]
    for item in cases:
        with pytest.raises(es.InvalidInput) as err:
            es.add_expenses(db_session, uid, [item])
        assert "SECRETNOTE" not in str(err.value) and "SECRETNOTE" not in repr(err.value.errors)


def test_update_errors_do_not_echo_the_note(db_session, uid):
    e = es.add_expenses(db_session, uid, [exp(100)])[0].expense
    with pytest.raises(es.InvalidInput) as err:
        es.update_expense(db_session, uid, e.id, 1, {"note": SECRET, "amount_paisa": 0})
    assert "SECRETNOTE" not in str(err.value)
    with pytest.raises(es.VersionConflict) as err:
        es.update_expense(db_session, uid, e.id, 5, {"note": SECRET})
    assert "SECRETNOTE" not in str(err.value)


def test_nothing_is_logged_or_printed_with_a_note(db_session, uid, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    e = es.add_expenses(db_session, uid, [exp(100, note=SECRET)], source="whatsapp")[0].expense
    es.update_expense(db_session, uid, e.id, 1, {"note": SECRET + "2"})
    es.get_expenses(db_session, uid)
    es.summarize(db_session, uid, date_from=date(2026, 10, 1), date_to=date(2026, 11, 1), group_by="day")
    es.delete_expenses(db_session, uid, [e.id])
    es.restore_expenses(db_session, uid, [e.id])
    with pytest.raises(es.InvalidInput):
        es.add_expenses(db_session, uid, [exp(0, note=SECRET)])
    out = capsys.readouterr()
    assert "SECRETNOTE" not in caplog.text and "SECRETNOTE" not in out.out + out.err


def test_a_bad_timezone_warning_does_not_contain_the_timezone_text_or_user_data(db_session, make_user, clock, caplog):
    uid = make_user("Not/AZone")
    caplog.set_level(logging.WARNING)
    es.add_expenses(db_session, uid, [exp(100, note=SECRET)])
    assert "Not/AZone" not in caplog.text and "SECRETNOTE" not in caplog.text
