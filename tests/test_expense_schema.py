"""Migration 0004: the tables, the seed and the database-level rules (offline, SQLite with foreign keys ON)."""
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app.database.migrate import upgrade_to_head, _config

NEW_TABLES = {"categories", "subcategories", "expenses", "audit_log"}


def _ins(db, **kw):
    """Raw insert into expenses so the database constraints (not the service) are what is tested."""
    row = dict(user_id=1, client_id=str(uuid.uuid4()), amount_paisa=100, occurred_on="2026-10-30",
               category_id=1, subcategory_id=None, note=None)
    row.update(kw)
    cols = ", ".join(row)
    db.execute(text(f"INSERT INTO expenses ({cols}) VALUES ({', '.join(':' + k for k in row)})"), row)
    db.commit()


@pytest.fixture
def user1(db_session, make_user):
    return make_user()


def test_tables_exist(db_engine):
    assert NEW_TABLES <= set(inspect(db_engine).get_table_names())


def test_seed_has_13_categories_and_40_subcategories(db_session):
    assert db_session.execute(text("SELECT count(*) FROM categories")).scalar() == 13
    assert db_session.execute(text("SELECT count(*) FROM subcategories")).scalar() == 40
    # every subcategory slug starts with its category slug
    bad = db_session.execute(text(
        "SELECT s.slug FROM subcategories s JOIN categories c ON c.id = s.category_id "
        "WHERE s.slug NOT LIKE c.slug || '.%'")).all()
    assert bad == []


def test_only_the_four_conservative_default_flags_exist(db_session):
    rows = dict(db_session.execute(text("SELECT slug, default_flag FROM subcategories WHERE default_flag IS NOT NULL")).all())
    assert rows == {
        "financial.loan_installment": "doubtful",
        "financial.insurance": "doubtful",
        "financial.markup_interest": "riba",
        "financial.late_charge": "riba",
    }
    # bank fees and Takaful must NOT flag
    assert db_session.execute(text(
        "SELECT count(*) FROM subcategories WHERE slug IN ('financial.bank_fees','financial.takaful') AND default_flag IS NOT NULL")).scalar() == 0


def test_charity_has_the_four_statement_types(db_session):
    rows = dict(db_session.execute(text("SELECT slug, charity_type FROM subcategories WHERE charity_type IS NOT NULL")).all())
    assert rows == {"charity.zakat": "zakat", "charity.sadaqah": "sadaqah", "charity.fitrana": "fitrana", "charity.other": "other"}
    assert db_session.execute(text("SELECT slug FROM categories WHERE is_charity")).scalars().all() == ["charity"]


def test_urdu_labels_are_present_for_every_row(db_session):
    assert db_session.execute(text("SELECT count(*) FROM categories WHERE name_ur IS NULL OR trim(name_ur) = ''")).scalar() == 0
    assert db_session.execute(text("SELECT count(*) FROM subcategories WHERE name_ur IS NULL OR trim(name_ur) = ''")).scalar() == 0


# ── constraints ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("amount", [0, -1, 10 ** 12 + 1])
def test_amount_outside_range_is_refused(db_session, user1, amount):
    with pytest.raises(IntegrityError):
        _ins(db_session, amount_paisa=amount)


@pytest.mark.parametrize("amount", [1, 10 ** 12])
def test_amount_boundaries_are_accepted(db_session, user1, amount):
    _ins(db_session, amount_paisa=amount)


def test_other_currencies_are_refused(db_session, user1):
    with pytest.raises(IntegrityError):
        _ins(db_session, currency="USD")


def test_dates_before_2000_are_refused(db_session, user1):
    with pytest.raises(IntegrityError):
        _ins(db_session, occurred_on="1999-12-31")
    _ins(db_session, occurred_on="2000-01-01")


def test_note_length_is_limited_in_characters_not_bytes(db_session, user1):
    _ins(db_session, note="ا" * 500)                    # 500 Urdu letters = 1000 bytes: allowed
    with pytest.raises(IntegrityError):
        _ins(db_session, note="a" * 501)


@pytest.mark.parametrize("column,value", [("payment_method", "cheque"), ("source", "telegram"), ("shariah_flag", "haram")])
def test_value_lists_are_enforced(db_session, user1, column, value):
    with pytest.raises(IntegrityError):
        _ins(db_session, **{column: value})


def test_a_subcategory_must_belong_to_the_category(db_session, user1):
    ids = dict(db_session.execute(text("SELECT slug, id FROM categories")).all())
    groceries = db_session.execute(text("SELECT id FROM subcategories WHERE slug='food.groceries'")).scalar()
    _ins(db_session, category_id=ids["food"], subcategory_id=groceries)
    with pytest.raises(IntegrityError):
        _ins(db_session, category_id=ids["transport"], subcategory_id=groceries)


def test_same_client_id_twice_for_one_user_is_refused_but_other_users_may_reuse_it(db_session, make_user):
    a, b = make_user(), make_user()
    cid = str(uuid.uuid4())
    _ins(db_session, user_id=a, client_id=cid)
    with pytest.raises(IntegrityError):
        _ins(db_session, user_id=a, client_id=cid)
    _ins(db_session, user_id=b, client_id=cid)


def test_deleting_a_user_deletes_their_expenses_and_audit_rows(db_session, make_user):
    a, b = make_user(), make_user()
    _ins(db_session, user_id=a)
    _ins(db_session, user_id=b)
    db_session.execute(text("INSERT INTO audit_log (user_id, actor, action) VALUES (:u, 'user', 'x')"), {"u": a})
    db_session.commit()
    db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": a})
    db_session.commit()
    assert db_session.execute(text("SELECT count(*) FROM expenses")).scalar() == 1
    assert db_session.execute(text("SELECT count(*) FROM audit_log")).scalar() == 0


def test_audit_actor_is_restricted(db_session, user1):
    with pytest.raises(IntegrityError):
        db_session.execute(text("INSERT INTO audit_log (user_id, actor, action) VALUES (1, 'robot', 'x')"))
        db_session.commit()


def test_no_column_could_hold_a_card_number(db_engine):
    """Privacy rule: no card number or bank credential column may ever appear in the expense tables."""
    forbidden = ("card_number", "pan", "cvv", "cvc", "iban", "account_number", "pin", "expiry", "password", "secret")
    insp = inspect(db_engine)
    for table in NEW_TABLES:
        for col in insp.get_columns(table):
            assert not any(word == col["name"] or col["name"].startswith(word + "_") for word in forbidden), (table, col["name"])


def test_downgrade_removes_the_tables_and_upgrade_restores_them():
    from alembic import command
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    upgrade_to_head(engine)
    with engine.begin() as c:
        command.downgrade(_config(c), "0003")
    assert not (NEW_TABLES & set(inspect(engine).get_table_names()))
    upgrade_to_head(engine)
    assert NEW_TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM subcategories")).scalar() == 40
