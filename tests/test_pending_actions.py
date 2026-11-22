"""Step 4b: confirmation cards: single use, expiry, ownership, retry safety, choose, undo. Offline (SQLite)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.database.models import AuditLog, Expense, PendingAction
from app.services import expense_service as es
from app.services import pending_actions as pa
from app.services.expense_understand import taxonomy


def payload(*amounts, slug="food.tea_snacks", lang="en", rows=None, note=None):
    alts = pa.alternatives_for(slug, rows) if rows else [slug]
    return pa.CardPayload(lang=lang, items=[pa.CardItem(amount_paisa=a, category_slug=slug, occurred_on=date(2026, 10, 31), note=note,
                                                        alternatives=alts) for a in amounts])


@pytest.fixture
def uid(make_user, clock):
    return make_user()


@pytest.fixture
def rows(db_session):
    return taxonomy(db_session)


def card(db, uid, *amounts, msg="m1", **kw):
    row, created = pa.create_card(db, uid, client_msg_id=msg, payload=payload(*amounts, **kw))
    return row


def expenses(db, uid):
    return db.execute(select(Expense).where(Expense.user_id == uid, Expense.deleted_at.is_(None))).scalars().all()


def move(clock, **delta):
    clock["now"] = clock["now"] + timedelta(**delta)


# ── creating ────────────────────────────────────────────────────────────────────────────────────

def test_a_new_card_is_pending_for_fifteen_minutes_and_saves_nothing(db_session, uid, clock):
    row = card(db_session, uid, 50000)
    assert row.status == "pending" and pa.PENDING_TTL == timedelta(minutes=15)
    assert es._aware(row.expires_at) - clock["now"] == timedelta(minutes=15)
    assert expenses(db_session, uid) == []


def test_the_same_chat_message_gives_back_the_same_card(db_session, uid):
    first, created1 = pa.create_card(db_session, uid, client_msg_id="same", payload=payload(100))
    again, created2 = pa.create_card(db_session, uid, client_msg_id="same", payload=payload(999))
    assert (created1, created2) == (True, False) and again.id == first.id
    assert pa.CardPayload.model_validate(again.payload).items[0].amount_paisa == 100          # the first card is kept untouched


def test_the_same_message_id_from_two_people_is_two_cards(db_session, make_user, clock):
    a, b = make_user(), make_user()
    ca, cb = card(db_session, a, 100, msg="x"), card(db_session, b, 100, msg="x")
    assert ca.id != cb.id and pa.get_by_message(db_session, a, "x").id == ca.id


def test_a_card_holds_only_validated_values():
    for bad in (dict(amount_paisa=0), dict(amount_paisa=10 ** 12 + 1), dict(amount_paisa=12.5), dict(amount_paisa="500"),
                dict(amount_paisa=True), dict(amount_paisa=5, user_id=2), dict(amount_paisa=5, note="x" * 201)):
        with pytest.raises(Exception):
            pa.CardItem(category_slug="food.groceries", occurred_on=date(2026, 10, 31), **bad)
    with pytest.raises(Exception):
        pa.CardPayload(items=[])
    with pytest.raises(Exception):
        pa.CardPayload(items=[pa.CardItem(amount_paisa=1, category_slug="a", occurred_on=date(2026, 1, 1))] * (pa.MAX_ITEMS + 1))


def test_cards_older_than_seven_days_are_removed_for_that_person_only(db_session, make_user, clock):
    a, b = make_user(), make_user()
    card(db_session, a, 1, msg="old-a"), card(db_session, b, 1, msg="old-b")
    move(clock, days=pa.KEEP_DAYS, minutes=1)
    card(db_session, a, 2, msg="new-a")
    assert pa.get_by_message(db_session, a, "old-a") is None and pa.get_by_message(db_session, a, "new-a") is not None
    assert pa.get_by_message(db_session, b, "old-b") is not None                                # not b's business


# ── confirming ──────────────────────────────────────────────────────────────────────────────────

def test_confirm_saves_the_expenses_as_ai_chat_and_audits_the_ai(db_session, uid):
    row = card(db_session, uid, 50000, 12000)
    saved = pa.confirm(db_session, uid, row.id)
    assert [e.amount_paisa for e in saved] == [50000, 12000] and {e.source for e in saved} == {"ai_chat"}
    assert len(expenses(db_session, uid)) == 2
    actors = {a.actor for a in db_session.execute(select(AuditLog).where(AuditLog.user_id == uid, AuditLog.action == "expense.create")).scalars()}
    assert actors == {"ai"}
    db_session.refresh(row)
    assert row.status == "confirmed" and row.result == {"expense_ids": [e.id for e in saved]}


def test_a_card_can_be_confirmed_only_once(db_session, uid):
    row = card(db_session, uid, 50000)
    pa.confirm(db_session, uid, row.id)
    for _ in range(2):
        with pytest.raises(pa.CardError) as err:
            pa.confirm(db_session, uid, row.id)
        assert err.value.code == "already_used"
    assert len(expenses(db_session, uid)) == 1


def test_a_card_cannot_be_confirmed_after_fifteen_minutes(db_session, uid, clock):
    row = card(db_session, uid, 50000)
    move(clock, minutes=14, seconds=59)
    ok_row = card(db_session, uid, 70000, msg="m2")
    pa.confirm(db_session, uid, ok_row.id)                                                       # still fine at 14:59
    move(clock, seconds=1)
    with pytest.raises(pa.CardError) as err:
        pa.confirm(db_session, uid, row.id)
    assert err.value.code == "expired"
    assert [e.amount_paisa for e in expenses(db_session, uid)] == [70000]
    db_session.refresh(row)
    assert row.status == "expired"


def test_the_expiry_boundary_is_exclusive(db_session, uid, clock):
    row = card(db_session, uid, 100)
    clock["now"] = es._aware(row.expires_at)                                                     # exactly at expires_at
    with pytest.raises(pa.CardError) as err:
        pa.confirm(db_session, uid, row.id)
    assert err.value.code == "expired"


def test_a_cancelled_card_cannot_be_confirmed(db_session, uid):
    row = card(db_session, uid, 100)
    pa.cancel(db_session, uid, row.id)
    with pytest.raises(pa.CardError) as err:
        pa.confirm(db_session, uid, row.id)
    assert err.value.code == "cancelled" and expenses(db_session, uid) == []


def test_another_persons_card_is_not_found_and_stays_untouched(db_session, make_user, clock):
    mine, theirs = make_user(), make_user()
    row = card(db_session, mine, 100)
    for action in (lambda: pa.confirm(db_session, theirs, row.id), lambda: pa.cancel(db_session, theirs, row.id),
                   lambda: pa.undo(db_session, theirs, row.id), lambda: pa.choose(db_session, theirs, row.id, 0, "food.tea_snacks")):
        with pytest.raises(pa.CardError) as err:
            action()
        assert err.value.code == "not_found"
    db_session.refresh(row)
    assert row.status == "pending" and expenses(db_session, theirs) == [] and expenses(db_session, mine) == []
    assert pa.get_card(db_session, theirs, row.id) is None and pa.latest_pending(db_session, theirs) is None


@pytest.mark.parametrize("bad_id", ["", "nope", "123", None, 5, "../../etc", str(uuid.uuid4())])
def test_a_bad_card_id_is_not_found(db_session, uid, bad_id):
    with pytest.raises(pa.CardError) as err:
        pa.confirm(db_session, uid, bad_id)
    assert err.value.code == "not_found"


def test_the_user_id_must_be_a_real_id(db_session):
    for bad in (0, -1, True, "1", None):
        with pytest.raises(TypeError):
            pa.confirm(db_session, bad, uuid.uuid4())


def test_a_refused_expense_rolls_the_claim_back_so_the_card_is_still_there(db_session, uid, clock):
    row = card(db_session, uid, 100)
    row.payload = {**row.payload, "items": [{**row.payload["items"][0], "occurred_on": "2030-01-01"}]}      # in the future: the service refuses
    db_session.commit()
    with pytest.raises(es.InvalidInput):
        pa.confirm(db_session, uid, row.id)
    db_session.refresh(row)
    assert row.status == "pending" and expenses(db_session, uid) == []
    pa.cancel(db_session, uid, row.id)


def test_expense_ids_come_from_the_card_so_a_replay_cannot_add_twice(db_session, uid):
    row = card(db_session, uid, 100, 200)
    saved = pa.confirm(db_session, uid, row.id)
    ids = [pa.derived_client_id(row.id, i) for i in range(2)]
    assert [e.client_id for e in saved] == ids
    again = es.add_expenses(db_session, uid, [dict(client_id=ids[0], amount_paisa=100, occurred_on=date(2026, 10, 31),
                                                   category_slug="food")], source="ai_chat")
    assert again[0].created is False and len(expenses(db_session, uid)) == 2


def test_latest_pending_is_the_newest_live_card(db_session, uid, clock):
    older = card(db_session, uid, 1, msg="a")
    move(clock, minutes=1)
    newer = card(db_session, uid, 2, msg="b")
    assert pa.latest_pending(db_session, uid).id == newer.id
    pa.cancel(db_session, uid, newer.id)
    assert pa.latest_pending(db_session, uid).id == older.id
    move(clock, minutes=20)
    assert pa.latest_pending(db_session, uid) is None


# ── choose ──────────────────────────────────────────────────────────────────────────────────────

def test_the_alternatives_are_the_neighbours_plus_other(rows):
    alts = pa.alternatives_for("food.tea_snacks", rows)
    assert alts[0] == "food.tea_snacks" and "food.groceries" in alts and "other.other" in alts and len(alts) <= pa.MAX_ALTERNATIVES
    assert all(a.split(".")[0] in ("food", "other") for a in alts)
    assert "other.other" in pa.alternatives_for("bills", rows) and pa.alternatives_for("other.other", rows).count("other.other") == 1


def test_choose_switches_to_an_offered_category_and_keeps_the_rest(db_session, uid, rows):
    row = card(db_session, uid, 100, 200, rows=rows)
    updated = pa.choose(db_session, uid, row.id, 1, "food.groceries")
    items = pa.CardPayload.model_validate(updated.payload).items
    assert [i.category_slug for i in items] == ["food.tea_snacks", "food.groceries"] and [i.amount_paisa for i in items] == [100, 200]
    assert updated.rev == 1
    pa.choose(db_session, uid, row.id, 1, "food.tea_snacks")                                         # and back again
    saved = pa.confirm(db_session, uid, row.id)
    assert [e.subcategory_slug for e in saved] == ["food.tea_snacks"] * 2


@pytest.mark.parametrize("index,slug", [(0, "charity.zakat"), (0, "food"), (0, ""), (0, None), (0, 5), (1, "food.groceries"), (-1, "food.groceries"),
                                        (True, "food.groceries"), ("0", "food.groceries"), (None, "food.groceries")])
def test_choose_refuses_anything_that_was_not_offered_and_changes_nothing(db_session, uid, rows, index, slug):
    row = card(db_session, uid, 100, rows=rows)
    before = dict(row.payload)
    with pytest.raises(pa.CardError) as err:
        pa.choose(db_session, uid, row.id, index, slug)
    assert err.value.code == "bad_choice"
    db_session.refresh(row)
    assert row.payload == before and row.rev == 0


def test_choose_cannot_edit_a_used_or_expired_card(db_session, uid, clock, rows):
    done, lapsed = card(db_session, uid, 1, msg="a", rows=rows), card(db_session, uid, 2, msg="b", rows=rows)
    pa.confirm(db_session, uid, done.id)
    with pytest.raises(pa.CardError) as err:
        pa.choose(db_session, uid, done.id, 0, "food.groceries")
    assert err.value.code == "already_used"
    move(clock, minutes=16)
    with pytest.raises(pa.CardError) as err:
        pa.choose(db_session, uid, lapsed.id, 0, "food.groceries")
    assert err.value.code == "expired"


# ── cancel and undo ─────────────────────────────────────────────────────────────────────────────

def test_cancel_is_single_use_and_saves_nothing(db_session, uid):
    row = card(db_session, uid, 100)
    pa.cancel(db_session, uid, row.id)
    with pytest.raises(pa.CardError) as err:
        pa.cancel(db_session, uid, row.id)
    assert err.value.code == "cancelled" and expenses(db_session, uid) == []


def test_a_confirmed_card_cannot_be_cancelled(db_session, uid):
    row = card(db_session, uid, 100)
    pa.confirm(db_session, uid, row.id)
    with pytest.raises(pa.CardError) as err:
        pa.cancel(db_session, uid, row.id)
    assert err.value.code == "already_used" and len(expenses(db_session, uid)) == 1


def test_undo_removes_what_the_card_saved_once_and_softly(db_session, uid):
    row = card(db_session, uid, 100, 200)
    pa.confirm(db_session, uid, row.id)
    assert pa.undo(db_session, uid, row.id) == 2
    assert expenses(db_session, uid) == []
    assert db_session.execute(text("SELECT count(*) FROM expenses WHERE user_id=:u AND deleted_at IS NOT NULL"), {"u": uid}).scalar() == 2
    with pytest.raises(pa.CardError) as err:
        pa.undo(db_session, uid, row.id)
    assert err.value.code == "already_used"


def test_undo_needs_a_confirmed_card(db_session, uid):
    row = card(db_session, uid, 100)
    with pytest.raises(pa.CardError) as err:
        pa.undo(db_session, uid, row.id)
    assert err.value.code == "not_confirmed"


def test_undo_window_is_one_hour(db_session, uid, clock):
    early, late = card(db_session, uid, 1, msg="a"), card(db_session, uid, 2, msg="b")
    pa.confirm(db_session, uid, early.id), pa.confirm(db_session, uid, late.id)
    move(clock, minutes=59)
    assert pa.undo(db_session, uid, early.id) == 1
    move(clock, minutes=2)
    with pytest.raises(pa.CardError) as err:
        pa.undo(db_session, uid, late.id)
    assert err.value.code == "undo_window_over" and len(expenses(db_session, uid)) == 1


def test_undo_deletes_only_what_that_card_saved(db_session, uid):
    es.add_expenses(db_session, uid, [dict(client_id=uuid.uuid4(), amount_paisa=999, occurred_on=date(2026, 10, 30), category_slug="food")])
    row = card(db_session, uid, 100)
    pa.confirm(db_session, uid, row.id)
    pa.undo(db_session, uid, row.id)
    assert [e.amount_paisa for e in expenses(db_session, uid)] == [999]


# ── display ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("paisa,shown", [(50000, "Rs 500"), (125050, "Rs 1,250.50"), (5, "Rs 0.05"), (150000000, "Rs 1,500,000"), (100, "Rs 1")])
def test_amounts_are_shown_in_rupees(paisa, shown):
    assert pa.format_rupees(paisa) == shown


def test_the_rendered_card_has_names_and_choices_but_never_a_user_id(db_session, uid, rows):
    row = card(db_session, uid, 50000, rows=rows, note="chai")
    view = pa.render(row, rows)
    assert view["status"] == "pending" and view["kind"] == "create" and set(view) == {"id", "kind", "status", "lang", "expires_at", "items"}
    first = view["items"][0]
    assert first["amount_display"] == "Rs 500" and first["category_name_en"] == "Tea & snacks" and first["note"] == "chai"
    assert {a["slug"] for a in first["alternatives"]} >= {"food.groceries", "other.other"} and "food.tea_snacks" not in {a["slug"] for a in first["alternatives"]}
    assert "user_id" not in str(view)


def test_a_lapsed_card_renders_as_expired(db_session, uid, clock, rows):
    row = card(db_session, uid, 100)
    move(clock, minutes=16)
    assert pa.render(row, rows)["status"] == "expired"


def test_the_card_tables_follow_the_data_rules(db_engine):
    from sqlalchemy import inspect
    insp = inspect(db_engine)
    fk = [f for f in insp.get_foreign_keys("pending_actions") if f["referred_table"] == "users"][0]
    assert fk["options"].get("ondelete", "").upper() == "CASCADE"
    assert any(u["column_names"] == ["user_id", "client_msg_id"] for u in insp.get_unique_constraints("pending_actions"))
    idx = {i["name"]: i for i in insp.get_indexes("pending_actions")}
    assert "ix_pending_user_live" in idx


def test_an_expired_card_cannot_be_cancelled_it_is_just_expired(db_session, uid, clock):
    row = card(db_session, uid, 100)
    move(clock, minutes=16)
    with pytest.raises(pa.CardError) as err:
        pa.cancel(db_session, uid, row.id)
    assert err.value.code == "expired"
    db_session.refresh(row)
    assert row.status == "expired"


@pytest.mark.parametrize("action", ["confirm", "cancel", "undo"])
def test_the_claim_itself_checks_the_owner_even_if_the_lookup_before_it_did_not(db_session, make_user, clock, monkeypatch, action):
    """Defence in depth: with the ownership lookup switched off, another person's claim must still change nothing."""
    mine, theirs = make_user(), make_user()
    row = card(db_session, mine, 100)
    if action == "undo":
        pa.confirm(db_session, mine, row.id)
    monkeypatch.setattr(pa, "get_card", lambda db, user_id, card_id: db.get(PendingAction, row.id))
    with pytest.raises(pa.CardError):
        getattr(pa, action)(db_session, theirs, row.id)
    db_session.expire_all()
    db_session.refresh(row)
    assert row.status == ("confirmed" if action == "undo" else "pending") and row.undone_at is None
    assert len(expenses(db_session, mine)) == (1 if action == "undo" else 0)


@pytest.mark.parametrize("what", ["rev_moved", "already_confirmed", "other_owner"])
def test_choose_update_is_guarded_against_what_changed_since_it_was_read(db_session, make_user, clock, monkeypatch, rows, what):
    """The race window: choose read the card, then something changed it. The UPDATE's own guards must refuse; nothing is overwritten."""
    from types import SimpleNamespace
    mine, theirs = make_user(), make_user()
    row = card(db_session, mine, 100, rows=rows)
    stale = SimpleNamespace(id=row.id, user_id=mine, status="pending", expires_at=row.expires_at, payload=row.payload, rev=0)
    before = dict(row.payload)
    caller = mine
    if what == "rev_moved":
        db_session.execute(text("UPDATE pending_actions SET rev = 1 WHERE id = :i"), {"i": str(row.id).replace("-", "")})
    elif what == "already_confirmed":
        db_session.execute(text("UPDATE pending_actions SET status = 'confirmed' WHERE id = :i"), {"i": str(row.id).replace("-", "")})
    else:
        caller = theirs
    db_session.commit()
    monkeypatch.setattr(pa, "get_card", lambda db, user_id, card_id: stale)
    with pytest.raises(pa.CardError) as err:
        pa.choose(db_session, caller, row.id, 0, "food.groceries")
    assert err.value.code == "conflict"
    db_session.expire_all()
    assert db_session.get(PendingAction, row.id).payload == before


def test_undo_refuses_a_cancelled_card_too(db_session, uid):
    row = card(db_session, uid, 100)
    pa.cancel(db_session, uid, row.id)
    with pytest.raises(pa.CardError) as err:
        pa.undo(db_session, uid, row.id)
    assert err.value.code == "not_confirmed"


@pytest.mark.parametrize("sql", [
    "INSERT INTO pending_actions (id, user_id, kind, status, client_msg_id, payload, expires_at) VALUES ('a'||:u, :u, 'create', 'x', 'm', '{}', '2030-01-01')",
    "INSERT INTO pending_actions (id, user_id, kind, status, client_msg_id, payload, expires_at) VALUES ('b'||:u, :u, 'zzz', 'pending', 'm', '{}', '2030-01-01')",
    "INSERT INTO usage_counters (user_id, period, used) VALUES (:u, '2026-11', -1)",
    "INSERT INTO llm_usage_daily (day, tokens, calls) VALUES ('2026-11-20', -5, 1)",
])
def test_the_database_itself_refuses_out_of_range_values(db_session, uid, sql):
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        db_session.execute(text(sql), {"u": uid})
        db_session.commit()
    db_session.rollback()
