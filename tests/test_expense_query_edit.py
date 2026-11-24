"""
Step 5a end to end through HTTP: a question is answered from SQL totals, a change or delete makes a card, "which one?" lists,
stale entries, undo, ownership, allowance and breaker. A fake provider stands in for Groq. Offline (SQLite).
The frozen clock is 2026-10-31 12:00 in Karachi (a Saturday).
"""
import uuid
from datetime import date

import pytest

from app.services import expense_service as es
from tests.ai_helpers import form, item, says
from tests.test_expense_ai_flow import H, say, saved, used, web  # noqa: F401  (web is a fixture)

QUERY = lambda period="", category="none", group="none": {"period_text": period, "category_slug": category, "group_by": group}
EDIT = lambda action, target="", amount="", category="none": {"action": action, "target_text": target, "new_amount_text": amount,
                                                              "new_category_slug": category}


def user_id(web, n=0):
    return web.rows("SELECT id FROM users ORDER BY id")[n][0]


def seed(web, uid, *entries):
    """entries: (amount_rupees, slug, day, note). Returns the expense ids in order."""
    ids = []
    with web.Session() as db:
        for rupees, slug, day, note in entries:
            out = es.add_expenses(db, uid, [{"client_id": uuid.uuid4(), "amount_paisa": rupees * 100, "occurred_on": day,
                                             "subcategory_slug": slug, "note": note}])
            ids.append(out[0].expense.id)
    return ids


def rows_of(web):
    return [tuple(r) for r in web.rows("SELECT id, amount_paisa, subcategory_id IS NOT NULL, deleted_at IS NOT NULL, version FROM expenses ORDER BY id")]


def ask(client, intent_form, message, monkeypatch, msg_id=None):
    says(intent_form).install(monkeypatch)
    return say(client, message, msg_id=msg_id)


OCT = [date(2026, 10, d) for d in range(1, 32)]


# ── questions ──────────────────────────────────────────────────────────────────────────────────

def test_a_question_is_answered_from_sql_totals_with_the_exact_numbers(web, monkeypatch):
    me = web.make()
    uid = user_id(web)
    seed(web, uid, (1250, "bills.electricity", OCT[4], None), (4000, "food.groceries", OCT[9], None), (50, "food.tea_snacks", OCT[29], None),
         (999, "food.tea_snacks", date(2026, 9, 30), None))                    # the last one is September
    r = ask(me, form("expense_query", query=QUERY("this month")), "is this month kitna kharcha hua 500?", monkeypatch)
    answer = r.json()["answer"]
    assert "Rs 5,300 · 3" in answer and "2026-10-01" in answer and "2026-10-31" in answer
    assert r.json()["card"] is None and web.chat_calls == [] and tts_none(web)
    assert used(web)[0][1] == 1                                                 # a served question costs one message


def tts_none(web):
    return web.tts == []


def test_an_empty_period_means_this_month_and_a_month_with_no_entries_says_so(web, monkeypatch):
    me = web.make()
    uid = user_id(web)
    seed(web, uid, (300, "food.groceries", date(2026, 9, 15), None))
    r = ask(me, form("expense_query", query=QUERY("")), "kitna kharcha hua 100", monkeypatch)
    assert r.json()["answer"].startswith("No entries from 2026-10-01 to 2026-10-31")
    r = ask(me, form("expense_query", query=QUERY("last month")), "last month total 100", monkeypatch)
    assert "Rs 300 · 1" in r.json()["answer"] and "2026-09-01" in r.json()["answer"]


def test_a_category_and_a_breakdown_use_sql_groups(web, monkeypatch):
    me = web.make()
    uid = user_id(web)
    seed(web, uid, (1000, "food.groceries", OCT[1], None), (500, "food.tea_snacks", OCT[2], None), (700, "transport.fuel", OCT[3], None))
    r = ask(me, form("expense_query", query=QUERY("this month", "none", "category")), "this month by category 1", monkeypatch)
    a = r.json()["answer"]
    assert "Total: Rs 2,200 · 3" in a and "• Food: Rs 1,500 · 2" in a and "• Transport: Rs 700 · 1" in a
    assert a.index("Food") < a.index("Transport")                              # SQL order: biggest first
    r = ask(me, form("expense_query", query=QUERY("this month", "food")), "food this month 1", monkeypatch)
    assert "Total: Rs 1,500 · 2" in r.json()["answer"] and "Category: Food" in r.json()["answer"]
    r = ask(me, form("expense_query", query=QUERY("this month", "food.tea_snacks")), "tea this month 1", monkeypatch)
    assert "Total: Rs 500 · 1" in r.json()["answer"] and "Tea & snacks" in r.json()["answer"]


def test_deleted_entries_and_other_peoples_entries_never_count(web, monkeypatch):
    mine, theirs = web.make(), web.make()
    me_id, they_id = user_id(web, 0), user_id(web, 1)
    gone = seed(web, me_id, (900, "food.groceries", OCT[1], None), (100, "food.groceries", OCT[2], None))[0]
    seed(web, they_id, (77777, "food.groceries", OCT[1], None))
    with web.Session() as db:
        es.delete_expenses(db, me_id, [gone])
    r = ask(mine, form("expense_query", query=QUERY("this month")), "this month 1", monkeypatch)
    assert "Total: Rs 100 · 1" in r.json()["answer"] and "77" not in r.json()["answer"]


def test_urdu_and_roman_urdu_answers_use_their_language_and_urdu_category_names(web, monkeypatch):
    me = web.make()
    uid = user_id(web)
    seed(web, uid, (1000, "food.groceries", OCT[1], None))
    r = ask(me, form("expense_query", language="ur", query=QUERY("اس مہینے", "food")), "اس مہینے خوراک کا خرچ کتنا 1", monkeypatch)
    assert "آپ کا خرچ" in r.json()["answer"] and "Rs 1,000" in r.json()["answer"] and "زمرہ" in r.json()["answer"]
    r = ask(me, form("expense_query", language="roman_ur", query=QUERY("is mahine")), "is mahine kitna kharcha 1", monkeypatch)
    assert "Aap ka kharcha" in r.json()["answer"] and "Kul: Rs 1,000" in r.json()["answer"]


def test_a_period_the_model_made_up_is_refused_and_the_allowance_is_given_back(web, monkeypatch):
    """The model's period words must literally be in the message (like the amount cross-check on a new expense)."""
    me = web.make()
    uid = user_id(web)
    seed(web, uid, (1000, "food.groceries", OCT[1], None))
    r = ask(me, form("expense_query", query=QUERY("last year")), "this month kitna kharcha hua 1", monkeypatch)
    assert "could not read the period" in r.json()["answer"] and "Rs" not in r.json()["answer"]
    assert used(web)[0][1] == 0


def test_an_unknown_period_word_is_refused(web, monkeypatch):
    me = web.make()
    r = ask(me, form("expense_query", query=QUERY("next decade")), "next decade kitna kharcha 1", monkeypatch)
    assert "could not read the period" in r.json()["answer"] and used(web)[0][1] == 0


def test_a_query_that_breaks_the_number_rule_is_never_shown(web, monkeypatch):
    """If an answer ever held a number the data did not give, it is dropped and the allowance is given back."""
    from app.services import expense_ai
    me = web.make()
    uid = user_id(web)
    seed(web, uid, (1000, "food.groceries", OCT[1], None))
    real = expense_ai.query_text
    monkeypatch.setattr(expense_ai, "query_text", lambda result, lang: real(result, lang) + " (about Rs 123456)")
    r = ask(me, form("expense_query", query=QUERY("this month")), "this month 1", monkeypatch)
    assert "123456" not in r.json()["answer"] and "could not answer that safely" in r.json()["answer"] and used(web)[0][1] == 0


# ── delete ─────────────────────────────────────────────────────────────────────────────────────

def test_delete_the_last_entry_makes_a_card_and_only_yes_deletes_it(web, monkeypatch):
    me = web.make()
    uid = user_id(web)
    first, second = seed(web, uid, (500, "food.groceries", OCT[10], "first"), (800, "transport.fuel", OCT[10], "second"))
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "pichli entry")), "pichli entry delete kar do 1", monkeypatch)
    card = r.json()["card"]
    assert card["kind"] == "delete" and card["needs_choice"] is False and card["chosen"] == 0
    assert [i["amount_paisa"] for i in card["items"]] == [80000] and "delete this entry" in r.json()["answer"]
    assert [x[3] for x in rows_of(web)] == [0, 0]                               # nothing yet
    done = say(me, "yes")
    assert done.json()["answer"].startswith("Deleted") and [x[3] for x in rows_of(web)] == [0, 1]
    assert web.rows("SELECT actor, action FROM audit_log WHERE action = 'expense.delete'")[0] == ("ai", "expense.delete")


def test_no_cancels_a_delete(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], None))
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "last entry")), "delete the last entry 1", monkeypatch)
    assert "Cancelled" in say(me, "no").json()["answer"] and [x[3] for x in rows_of(web)] == [0]


def test_undo_restores_a_deleted_entry_once(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], None))
    card = ask(me, form("expense_edit_delete", edit=EDIT("delete", "last entry")), "delete the last entry 1", monkeypatch).json()["card"]
    say(me, "yes")
    r = me.post(f"/api/expense-ai/{card['id']}/undo", headers=H)
    assert r.status_code == 200 and r.json()["removed"] == 1 and [x[3] for x in rows_of(web)] == [0]
    assert me.post(f"/api/expense-ai/{card['id']}/undo", headers=H).status_code == 409


def test_everything_at_once_is_refused(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], None), (600, "food.groceries", OCT[11], None))
    for message, target in (("delete all my entries 1", "all my entries"), ("sab entries delete kar do 1", "sab entries")):
        r = ask(me, form("expense_edit_delete", edit=EDIT("delete", target)), message, monkeypatch)
        assert "one entry at a time" in r.json()["answer"] and r.json()["card"] is None
    assert [x[3] for x in rows_of(web)] == [0, 0] and used(web)[0][1] == 0


def test_a_target_the_model_made_up_is_refused(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], "tea"))
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "the 9999 entry")), "delete that one please 1", monkeypatch)
    assert "could not tell which entry" in r.json()["answer"] and r.json()["card"] is None and used(web)[0][1] == 0


# ── update ─────────────────────────────────────────────────────────────────────────────────────

def test_change_the_amount_shows_before_and_after_and_yes_updates_once(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol pump"), (300, "food.groceries", OCT[10], None))
    r = ask(me, form("expense_edit_delete", edit=EDIT("update", "petrol 3000", "3500")), "petrol 3000 ko 3500 kar do", monkeypatch)
    a = r.json()["answer"]
    assert "Rs 3,000" in a and "Rs 3,500" in a and r.json()["card"]["changes"]["amount_paisa"] == 350000
    assert [x[1] for x in rows_of(web)] == [300000, 30000]
    done = say(me, "yes")
    assert done.json()["answer"].startswith("Changed") and [x[1] for x in rows_of(web)] == [350000, 30000]
    assert web.rows("SELECT actor FROM audit_log WHERE action = 'expense.update'")[0][0] == "ai"
    assert say(me, "yes").json()["answer"] == "NORMAL CHATBOT ANSWER"           # nothing waits any more: no second change


def test_change_the_category_keeps_the_amount(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.dining_out", OCT[10], None))
    r = ask(me, form("expense_edit_delete", edit=EDIT("update", "yesterday's 500", category="food.groceries")), "change 500 to groceries", monkeypatch)
    assert r.json()["card"] is None and "could not tell which entry" in r.json()["answer"]     # "yesterday" is not in the message: refused
    r = ask(me, form("expense_edit_delete", edit=EDIT("update", "500", category="food.groceries")), "change 500 to groceries", monkeypatch)
    assert "Groceries" in r.json()["answer"] and "Rs 500" in r.json()["answer"]
    say(me, "yes")
    assert web.rows("SELECT s.slug, e.amount_paisa FROM expenses e JOIN subcategories s ON s.id = e.subcategory_id")[0] == ("food.groceries", 50000)


def test_a_new_amount_the_model_made_up_is_refused(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    r = ask(me, form("expense_edit_delete", edit=EDIT("update", "petrol 3000", "9500")), "petrol 3000 ko 3500 kar do", monkeypatch)
    assert "could not read the amount" in r.json()["answer"] and r.json()["card"] is None and [x[1] for x in rows_of(web)] == [300000]
    assert used(web)[0][1] == 0


def test_undo_puts_an_updated_entry_back_but_not_over_a_later_change(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    card = ask(me, form("expense_edit_delete", edit=EDIT("update", "petrol 3000", "3500")), "petrol 3000 ko 3500 kar do", monkeypatch).json()["card"]
    say(me, "yes")
    me.patch("/api/expenses/1", json={"version": 2, "note": "edited by hand"}, headers=H)       # the person edits it on the page
    r = me.post(f"/api/expense-ai/{card['id']}/undo", headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale" and [x[1] for x in rows_of(web)] == [350000]


def test_undo_puts_an_untouched_update_back(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    card = ask(me, form("expense_edit_delete", edit=EDIT("update", "petrol 3000", "3500")), "petrol 3000 ko 3500 kar do", monkeypatch).json()["card"]
    say(me, "yes")
    r = me.post(f"/api/expense-ai/{card['id']}/undo", headers=H)
    assert r.status_code == 200 and [x[1] for x in rows_of(web)] == [300000]


def test_an_update_without_a_change_is_refused(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    r = ask(me, form("expense_edit_delete", edit=EDIT("update", "petrol 3000")), "petrol 3000 ko theek kar do", monkeypatch)
    assert "Please say what to do" in r.json()["answer"] and used(web)[0][1] == 0


# ── which one? ─────────────────────────────────────────────────────────────────────────────────

def two_petrols(web):
    return seed(web, user_id(web), (3000, "transport.fuel", OCT[5], "petrol first fill"), (3000, "transport.fuel", OCT[12], "petrol second fill"))


def test_two_matches_are_never_guessed_the_person_picks_by_number(web, monkeypatch):
    me = web.make()
    two_petrols(web)
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "petrol 3000")), "petrol 3000 delete kar do", monkeypatch)
    card = r.json()["card"]
    assert card["needs_choice"] is True and card["chosen"] is None and len(card["items"]) == 2
    assert "1. Rs 3,000" in r.json()["answer"] and "2. Rs 3,000" in r.json()["answer"] and "Which one" in r.json()["answer"]
    assert card["items"][0]["occurred_on"] == "2026-10-13"                       # newest first
    blocked = say(me, "yes")                                                     # "yes" before picking deletes nothing
    assert "pick which entry first" in blocked.json()["answer"] and [x[3] for x in rows_of(web)] == [0, 0]
    picked = say(me, "2")
    assert picked.json()["card"]["chosen"] == 1 and "delete this entry" in picked.json()["answer"] and "2026-10-06" in picked.json()["answer"]
    say(me, "yes")
    assert [x[3] for x in rows_of(web)] == [1, 0]                                # the OLDER one (number 2) is gone, only that one


def test_a_number_out_of_range_picks_nothing_and_a_number_with_no_list_goes_to_the_chatbot(web, monkeypatch):
    me = web.make()
    two_petrols(web)
    assert say(me, "3").json()["answer"] == "NORMAL CHATBOT ANSWER"             # no list is waiting
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "petrol 3000")), "petrol 3000 delete kar do", monkeypatch)
    r = say(me, "5")
    assert "from 1 to 2" in r.json()["answer"] and r.json()["card"]["chosen"] is None and [x[3] for x in rows_of(web)] == [0, 0]


def test_the_pick_route_and_the_confirm_route_follow_the_same_rules(web, monkeypatch):
    me = web.make()
    two_petrols(web)
    card = ask(me, form("expense_edit_delete", edit=EDIT("delete", "petrol 3000")), "petrol 3000 delete kar do", monkeypatch).json()["card"]
    url = f"/api/expense-ai/{card['id']}"
    assert me.post(url + "/confirm", headers=H).status_code == 409
    assert me.post(url + "/confirm", headers=H).json()["detail"]["code"] == "not_chosen"
    assert me.post(url + "/pick", json={"index": 7}, headers=H).status_code == 422
    assert me.post(url + "/pick", json={"index": True}, headers=H).status_code == 422
    assert me.post(url + "/pick", json={"index": 0, "user_id": 2}, headers=H).status_code == 422
    assert me.post(url + "/pick", json={"index": 0}).status_code == 403          # no write header
    ok = me.post(url + "/pick", json={"index": 0}, headers=H)
    assert ok.status_code == 200 and ok.json()["card"]["chosen"] == 0
    done = me.post(url + "/confirm", headers=H)
    assert done.status_code == 200 and done.json()["kind"] == "delete" and [x[3] for x in rows_of(web)] == [0, 1]
    assert me.post(url + "/confirm", headers=H).status_code == 409                # single use


def test_the_category_choose_route_refuses_an_edit_card(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], None))
    card = ask(me, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 one", monkeypatch).json()["card"]
    r = me.post(f"/api/expense-ai/{card['id']}/choose", json={"item": 0, "category_slug": "food.groceries"}, headers=H)
    assert r.status_code == 422


def test_more_than_five_matches_show_the_five_newest(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), *[(100, "food.tea_snacks", OCT[d], None) for d in range(8)])
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "chai 100")), "delete chai 100", monkeypatch)
    assert r.json()["card"]["needs_choice"] is True                               # "chai" fits no note or name: the amount alone found them, so the person picks
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "tea 100")), "delete tea 100", monkeypatch)
    card = r.json()["card"]
    assert len(card["items"]) == 5 and card["items"][0]["occurred_on"] == "2026-10-08" and "More entries fit" in r.json()["answer"]


def test_when_the_words_fit_nothing_but_the_amount_does_the_person_still_picks(web, monkeypatch):
    """A single entry is chosen for the person only if every word they gave fitted it; otherwise they confirm which."""
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "bijli 3000")), "bijli 3000 delete kar do", monkeypatch)
    assert r.json()["card"]["needs_choice"] is True and r.json()["card"]["chosen"] is None


def test_nothing_matching_says_so_and_costs_nothing(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "7777")), "delete the 7777 entry", monkeypatch)
    assert "could not find a matching entry" in r.json()["answer"] and r.json()["card"] is None and used(web)[0][1] == 0


# ── the entry changed after the card ───────────────────────────────────────────────────────────

def test_an_entry_changed_after_the_card_is_not_touched(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    ask(me, form("expense_edit_delete", edit=EDIT("update", "petrol 3000", "3500")), "petrol 3000 ko 3500 kar do", monkeypatch)
    me.patch("/api/expenses/1", json={"version": 1, "amount": "3100"}, headers=H)               # edited on the page meanwhile
    r = say(me, "yes")
    assert "has changed since I showed it" in r.json()["answer"] and [x[1] for x in rows_of(web)] == [310000]


def test_an_entry_deleted_after_the_card_is_not_touched(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "petrol 3000")), "petrol 3000 delete", monkeypatch)
    me.delete("/api/expenses/1", headers=H)
    r = say(me, "yes")
    assert "has changed since I showed it" in r.json()["answer"]
    assert web.rows("SELECT count(*) FROM audit_log WHERE action = 'expense.delete' AND actor = 'ai'")[0][0] == 0


def test_a_stale_card_is_used_up_and_can_not_be_retried(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    card = ask(me, form("expense_edit_delete", edit=EDIT("delete", "petrol 3000")), "petrol 3000 delete", monkeypatch).json()["card"]
    me.patch("/api/expenses/1", json={"version": 1, "amount": "3100"}, headers=H)
    r = me.post(f"/api/expense-ai/{card['id']}/confirm", headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale"
    assert [x[3] for x in rows_of(web)] == [0]
    again = me.post(f"/api/expense-ai/{card['id']}/confirm", headers=H)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "cancelled" and [x[3] for x in rows_of(web)] == [0]
    assert web.rows("SELECT status FROM pending_actions")[0][0] == "cancelled"
    assert say(me, "yes").json()["answer"] == "NORMAL CHATBOT ANSWER"             # nothing waits any more


# ── ownership ──────────────────────────────────────────────────────────────────────────────────

def test_a_message_naming_another_person_changes_only_my_own_entries(web, monkeypatch):
    mine, theirs = web.make(), web.make()
    me_id, they_id = user_id(web, 0), user_id(web, 1)
    seed(web, me_id, (500, "food.groceries", OCT[10], None))
    other = seed(web, they_id, (500, "food.groceries", OCT[10], None))[0]
    r = ask(mine, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry of user 2", monkeypatch)
    assert [i["amount_paisa"] for i in r.json()["card"]["items"]] == [50000]
    say(mine, "yes")
    live = web.rows("SELECT user_id FROM expenses WHERE deleted_at IS NULL")
    assert [x[0] for x in live] == [they_id] and other == 2


def test_another_persons_edit_card_is_not_found_on_every_route(web, monkeypatch):
    mine, theirs = web.make(), web.make()
    seed(web, user_id(web, 0), (500, "food.groceries", OCT[10], None))
    card = ask(mine, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry", monkeypatch).json()["card"]
    for path, body in (("confirm", None), ("cancel", None), ("pick", {"index": 0}), ("undo", None)):
        r = theirs.post(f"/api/expense-ai/{card['id']}/{path}", json=body, headers=H) if body else theirs.post(f"/api/expense-ai/{card['id']}/{path}", headers=H)
        assert r.status_code == 404, path
    assert [x[3] for x in rows_of(web)] == [0]


def test_a_plain_yes_from_another_person_never_confirms_my_edit_card(web, monkeypatch):
    mine, theirs = web.make(), web.make()
    seed(web, user_id(web, 0), (500, "food.groceries", OCT[10], None))
    ask(mine, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry", monkeypatch)
    assert say(theirs, "yes").json()["answer"] == "NORMAL CHATBOT ANSWER" and [x[3] for x in rows_of(web)] == [0]


# ── allowance, retries, kill switch ────────────────────────────────────────────────────────────

def test_the_same_chat_message_sent_twice_gives_the_same_edit_card_and_costs_one_message(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], None))
    first = ask(me, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry", monkeypatch, msg_id="m-1").json()
    again = say(me, "delete the 500 entry", msg_id="m-1").json()
    assert first["card"]["id"] == again["card"]["id"] and again["answer"] == first["answer"] and used(web)[0][1] == 1


def test_a_served_edit_and_a_served_question_each_cost_one_message_and_a_refused_one_costs_nothing(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], None))
    ask(me, form("expense_query", query=QUERY("this month")), "this month 1", monkeypatch)
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry", monkeypatch)
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "9999")), "delete the 9999 entry", monkeypatch)
    assert used(web)[0][1] == 2


def test_message_31_in_a_month_is_refused_for_questions_and_edits_too(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], None))
    monkeypatch.setenv("AI_FREE_LIMIT", "1")
    ask(me, form("expense_query", query=QUERY("this month")), "this month 1", monkeypatch)
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry", monkeypatch)
    assert "used all 1 free AI messages" in r.json()["answer"] and r.json()["card"] is None and [x[3] for x in rows_of(web)] == [0]


def test_with_the_ai_off_questions_and_edits_go_to_the_normal_chatbot_and_the_manual_form_still_works(web, monkeypatch):
    me = web.make()
    monkeypatch.setenv("EXPENSE_AI_ENABLED", "false")
    says(form("expense_query", query=QUERY("this month"))).install(monkeypatch)
    assert say(me, "this month kitna kharcha hua 1").json()["answer"] == "NORMAL CHATBOT ANSWER"
    r = me.post("/api/expenses/", json={"client_id": str(uuid.uuid4()), "amount": "250", "occurred_on": "2026-10-30",
                                         "category_slug": "food"}, headers=H)
    assert r.status_code == 201


def test_an_edit_that_crashes_gives_the_allowance_back_and_changes_nothing(web, monkeypatch):
    from app.services import expense_ai
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], None))

    def boom(*a, **k):
        raise RuntimeError("SECRETNOTE should never be logged")
    monkeypatch.setattr(expense_ai, "build_edit", boom)
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry", monkeypatch)
    assert "could not reach the assistant" in r.json()["answer"] and used(web)[0][1] == 0 and [x[3] for x in rows_of(web)] == [0]


# ── more rules found while checking the tests with mutations ──────────────────────────────────

def test_zero_and_numbers_beyond_the_list_pick_nothing_and_name_the_range(web, monkeypatch):
    me = web.make()
    two_petrols(web)
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "petrol 3000")), "petrol 3000 delete kar do", monkeypatch)
    for digit in ("0", "3", "9"):
        r = say(me, digit)
        assert "from 1 to 2" in r.json()["answer"] and r.json()["card"]["chosen"] is None
    assert [x[3] for x in rows_of(web)] == [0, 0] and web.chat_calls == []


def test_a_number_while_a_plain_expense_card_waits_goes_to_the_normal_chatbot_and_the_card_stays(web, monkeypatch):
    me = web.make()
    says(form("expense_log", [item("50", "food.tea_snacks")])).install(monkeypatch)
    card = say(me, "chai 50").json()["card"]
    assert say(me, "2").json()["answer"] == "NORMAL CHATBOT ANSWER"
    assert web.rows("SELECT status FROM pending_actions")[0][0] == "pending" and card["status"] == "pending"


def test_pick_on_a_plain_expense_card_is_refused(web, monkeypatch):
    me = web.make()
    says(form("expense_log", [item("50", "food.tea_snacks")])).install(monkeypatch)
    cid = say(me, "chai 50").json()["card"]["id"]
    assert me.post(f"/api/expense-ai/{cid}/pick", json={"index": 0}, headers=H).status_code == 422


def test_the_pick_prompt_card_json_says_a_choice_is_needed_when_yes_comes_too_early(web, monkeypatch):
    me = web.make()
    two_petrols(web)
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "petrol 3000")), "petrol 3000 delete kar do", monkeypatch)
    blocked = say(me, "yes").json()
    assert blocked["card"]["needs_choice"] is True and blocked["card"]["chosen"] is None and len(blocked["card"]["items"]) == 2


def test_with_no_words_at_all_a_single_entry_is_still_confirmed_by_the_person_not_chosen_for_them(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], "tea"))
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "")), "delete it please", monkeypatch)
    assert r.json()["card"]["needs_choice"] is True and r.json()["card"]["chosen"] is None and [x[3] for x in rows_of(web)] == [0]


def test_two_amounts_in_the_target_are_unclear_not_guessed(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (50, "food.tea_snacks", OCT[10], "chai"), (60, "food.tea_snacks", OCT[11], "chai"))
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "chai 50 60")), "delete chai 50 or 60", monkeypatch)
    assert "could not tell which entry" in r.json()["answer"] and r.json()["card"] is None and used(web)[0][1] == 0


def test_a_parallel_copy_of_an_edit_message_is_not_charged_twice(web, monkeypatch):
    """The early duplicate check is skipped here to reach the race path: create_card then reports created=False and the loser pays nothing."""
    from app.services import expense_ai
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], "tea"))
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry", monkeypatch, msg_id="same-1")
    assert used(web)[0][1] == 1
    real, calls = expense_ai.pa.get_by_message, []

    def skip_the_first_lookup(*a, **k):                           # only handle()'s early duplicate check; create_card's own lookup stays real
        calls.append(1)
        return None if len(calls) == 1 else real(*a, **k)
    monkeypatch.setattr(expense_ai.pa, "get_by_message", skip_the_first_lookup)
    r = ask(me, form("expense_edit_delete", edit=EDIT("delete", "500")), "delete the 500 entry", monkeypatch, msg_id="same-1")
    assert r.json()["card"] is not None and used(web)[0][1] == 1 and web.rows("SELECT count(*) FROM pending_actions")[0][0] == 1


def test_a_second_confirm_of_an_update_card_says_already_used_not_stale(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (3000, "transport.fuel", OCT[10], "petrol"))
    card = ask(me, form("expense_edit_delete", edit=EDIT("update", "petrol 3000", "3500")), "petrol 3000 ko 3500 kar do", monkeypatch).json()["card"]
    assert me.post(f"/api/expense-ai/{card['id']}/confirm", headers=H).status_code == 200
    again = me.post(f"/api/expense-ai/{card['id']}/confirm", headers=H)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_used" and [x[1] for x in rows_of(web)] == [350000]


def test_a_long_breakdown_shows_eight_lines_and_says_how_many_more(web, monkeypatch):
    me = web.make()
    slugs = ["food.groceries", "food.dining_out", "food.tea_snacks", "transport.fuel", "transport.public", "bills.gas", "bills.water",
             "bills.mobile", "health.doctor", "health.medicine"]
    seed(web, user_id(web), *[(100 * (i + 1), s, OCT[3], None) for i, s in enumerate(slugs)])
    r = ask(me, form("expense_query", query=QUERY("this month", "none", "subcategory")), "this month by subcategory 1", monkeypatch)
    a = r.json()["answer"]
    assert a.count("•") == 1 + 8 and "and 2 more" in a and "Total: Rs 5,500 · 10" in a


def test_the_query_answer_never_carries_a_note(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.groceries", OCT[10], "SECRETNOTE for ammi"))
    r = ask(me, form("expense_query", query=QUERY("this month", "none", "category")), "this month by category 1", monkeypatch)
    assert "SECRETNOTE" not in r.json()["answer"] and r.json()["card"] is None


def test_a_query_answer_is_exactly_the_template_with_the_sql_numbers_and_nothing_else(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (1250, "bills.electricity", OCT[4], "bijli"), (4000, "food.groceries", OCT[9], None))
    r = ask(me, form("expense_query", query=QUERY("this month")), "this month kitna kharcha hua 500?", monkeypatch)
    assert r.json()["answer"] == "Your spending from 2026-10-01 to 2026-10-31 (amount · number of entries):\n• Total: Rs 5,250 · 2"
    r = ask(me, form("expense_query", language="roman_ur", query=QUERY("is mahine")), "is mahine kitna kharcha 1", monkeypatch)
    assert r.json()["answer"] == "Aap ka kharcha 2026-10-01 se 2026-10-31 tak (raqam · entries ki tadaad):\n• Kul: Rs 5,250 · 2"


def test_a_two_digit_number_while_a_list_waits_is_not_a_pick(web, monkeypatch):
    me = web.make()
    two_petrols(web)
    ask(me, form("expense_edit_delete", edit=EDIT("delete", "petrol 3000")), "petrol 3000 delete kar do", monkeypatch)
    r = say(me, "12")
    assert "from 1 to" not in r.json()["answer"] and [x[3] for x in rows_of(web)] == [0, 0]


def test_undo_puts_the_old_category_back_not_only_the_old_amount(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.dining_out", OCT[10], "lunch"))
    card = ask(me, form("expense_edit_delete", edit=EDIT("update", "500", category="food.groceries")), "change 500 to groceries", monkeypatch).json()["card"]
    say(me, "yes")
    q = "SELECT s.slug, e.amount_paisa FROM expenses e JOIN subcategories s ON s.id = e.subcategory_id"
    assert web.rows(q)[0] == ("food.groceries", 50000)
    assert me.post(f"/api/expense-ai/{card['id']}/undo", headers=H).status_code == 200
    assert web.rows(q)[0] == ("food.dining_out", 50000)


def test_undo_of_a_combined_change_puts_back_both_the_amount_and_the_category(web, monkeypatch):
    me = web.make()
    seed(web, user_id(web), (500, "food.dining_out", OCT[10], "lunch"))
    card = ask(me, form("expense_edit_delete", edit=EDIT("update", "500", "700", "food.groceries")), "change 500 to 700 groceries", monkeypatch).json()["card"]
    say(me, "yes")
    q = "SELECT s.slug, e.amount_paisa FROM expenses e JOIN subcategories s ON s.id = e.subcategory_id"
    assert web.rows(q)[0] == ("food.groceries", 70000)
    me.post(f"/api/expense-ai/{card['id']}/undo", headers=H)
    assert web.rows(q)[0] == ("food.dining_out", 50000)
