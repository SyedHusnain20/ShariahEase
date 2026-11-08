"""
"We never store card numbers": nothing in the database may be able to hold one.

1. No column in any table (in the real migrated schema AND in the models) is named like a card, a card security code,
   an expiry, an IBAN or a bank account.
2. The payment method of an expense is a closed list of labels, never a free field.
3. Every table and column is listed in REVIEWED below. A new one fails the tests until someone reads it, decides it cannot
   hold payment data, and adds it (that deliberate step is the point).
Free-text columns (the expense note, chat content) can still receive whatever a user types; the privacy page says
"do not type card numbers into notes".
"""
import re

import pytest
from sqlalchemy import inspect

from app.database import models

FORBIDDEN = re.compile(
    r"(card|cvv|cvc|cvn|\bpan\b|iban|swift|routing|sort_?code|account_?(no|num)|acct|bank_?account|credit|debit|"
    r"expiry|expiration|exp_?(month|year|date)|ccnum|track_?data|magstripe|wallet_?(no|num|id))", re.I)

# Column names that look suspicious to a regex but are not payment data. Each needs a reason.
ALLOWED = {
    ("expenses", "payment_method"): "a label from a closed list (cash, card, bank_transfer, wallet, other), never a number",
}

# EVERY table and column, reviewed for payment data. A new table or column fails the tests below until someone reads it,
# decides it cannot hold payment credentials, and adds it here: that deliberate step is the point.
# (webhook_events.payload is the one to watch: a stored WhatsApp payload would hold phone numbers; it is kept empty.)
REVIEWED = {
    "users": {"id", "name", "email", "hashed_password", "language", "created_at", "timezone", "token_version", "is_admin",
              "email_verified_at", "privacy_accepted_at"},
    "refresh_tokens": {"id", "user_id", "family_id", "token_hash", "issued_at", "expires_at", "revoked_at", "replaced_by"},
    "zakat_records": {"id", "user_id", "user_name", "cash", "gold_value", "silver_value", "business_inventory", "receivables",
                      "debts", "immediate_expenses", "total_assets", "total_deductions", "zakatable_wealth", "nisab_threshold",
                      "nisab_rate_used", "zakat_due", "is_zakat_due", "calculated_at"},
    "screener_history": {"id", "user_id", "query", "verdict", "explanation", "screened_at"},
    "chat_messages": {"id", "user_id", "session_id", "role", "content", "language", "created_at"},
    "categories": {"id", "slug", "name_en", "name_ur", "is_charity", "sort_order"},
    "subcategories": {"id", "category_id", "slug", "name_en", "name_ur", "default_flag", "charity_type", "sort_order"},
    "expenses": {"id", "user_id", "client_id", "amount_paisa", "currency", "occurred_on", "category_id", "subcategory_id", "note",
                 "payment_method", "source", "shariah_flag", "shariah_flag_dismissed", "created_at", "updated_at", "deleted_at", "version"},
    "audit_log": {"id", "user_id", "actor", "action", "entity_type", "entity_id", "detail", "created_at"},
    "maintenance_runs": {"name", "last_run_at"},
    "webhook_events": {"id", "provider", "event_id", "event_type", "signature_valid", "status", "attempts", "error",
                       "payload", "received_at", "processed_at"},
}


def _model_columns():
    return {t.name: {c.name for c in t.columns} for t in models.Base.metadata.tables.values()}


@pytest.mark.parametrize("name", ["card_number", "cardno", "credit_card", "cvv", "cvc2", "pan", "iban", "bank_account", "account_number",
                                  "acct_no", "expiry_date", "exp_month", "exp_year", "routing_number", "sort_code", "debit_card", "ccnum"])
def test_the_forbidden_pattern_catches_the_obvious_names(name):
    assert FORBIDDEN.search(name), name


@pytest.mark.parametrize("name", ["payment_method", "amount_paisa", "note", "user_id", "token_hash", "occurred_on", "hashed_password"])
def test_the_forbidden_pattern_leaves_ordinary_names_alone(name):
    assert not FORBIDDEN.search(name) or (("expenses", name) in ALLOWED), name


def test_no_column_is_named_like_payment_data(db_engine):
    insp = inspect(db_engine)
    bad = []
    for table in insp.get_table_names():
        if table.startswith("alembic"):
            continue
        for col in insp.get_columns(table):
            if FORBIDDEN.search(col["name"]) and (table, col["name"]) not in ALLOWED:
                bad.append(f"{table}.{col['name']}")
    for table, cols in _model_columns().items():
        bad += [f"{table}.{c} (model)" for c in cols if FORBIDDEN.search(c) and (table, c) not in ALLOWED]
    assert not bad, f"a column that could hold payment data appeared: {bad}"


def test_no_table_is_named_like_payment_data(db_engine):
    assert not [t for t in inspect(db_engine).get_table_names() if re.search(r"card|payment|billing|invoice|wallet", t, re.I)], (
        "a payments table appeared: payments are Path step 6 and need their own privacy review (and an updated privacy page)")


def test_payment_method_is_a_closed_list_in_the_schema():
    checks = " ".join(str(c.sqltext) for c in models.Expense.__table__.constraints if c.__class__.__name__ == "CheckConstraint")
    for label in ("cash", "card", "bank_transfer", "wallet", "other"):
        assert f"'{label}'" in checks, label


def test_every_table_and_column_in_the_real_schema_has_been_reviewed(db_engine):
    insp = inspect(db_engine)
    tables = {t for t in insp.get_table_names() if not t.startswith("alembic")}
    assert tables == set(REVIEWED), f"table list changed: {tables ^ set(REVIEWED)}; review for payment data, then update REVIEWED"
    for table in tables:
        real = {c["name"] for c in insp.get_columns(table)}
        assert real == REVIEWED[table], f"{table}: columns changed {real ^ REVIEWED[table]}; review for payment data, then update REVIEWED"


def test_every_table_and_column_in_the_models_has_been_reviewed():
    model_cols = _model_columns()
    assert set(model_cols) == set(REVIEWED)
    for table, cols in model_cols.items():
        assert cols == REVIEWED[table], f"{table}: model columns changed {cols ^ REVIEWED[table]}"
