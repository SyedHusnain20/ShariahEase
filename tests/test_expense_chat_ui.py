"""
Path step 5b: rules the chat page's source must keep (read from the files, like tests/test_expense_ai_rules.py):
the person's words are never inserted as HTML, the card buttons are real buttons with a 44 px target, the page sends
`client_msg_id`, sends no money arithmetic, loads the module, is cached by the service worker, and never uses the
word "fatwa" or "ruling". Offline.
"""
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.routers import pages

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "frontend/static/js/expense_chat.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/templates/pages/chatbot.html").read_text(encoding="utf-8")
SW = (ROOT / "frontend/static/sw.js").read_text(encoding="utf-8")


def card_block():
    return HTML[HTML.index('data-testid="expense-card"'):HTML.index("<!-- Sources -->")]


def strip_block():
    return HTML[HTML.index('data-testid="expense-strip"'):HTML.index("<!-- Input Controls -->")]


@pytest.mark.parametrize("name", ["expense_chat.js", "chatbot.html"])        # names only: pytest puts parameters into the test id, and a whole file there breaks Windows (32767-character limit)
def test_nothing_the_person_wrote_is_ever_inserted_as_html(name):
    source = {"expense_chat.js": JS, "chatbot.html": HTML}[name]
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "x-html", "v-html", "document.write", "createContextualFragment", "eval("):
        assert bad not in source, f"{bad} in {name}"


def test_the_note_is_only_shown_with_x_text_and_with_automatic_direction():
    uses = re.findall(r"<[^>]*item\.note[^>]*>", card_block())
    assert uses, "the note is not shown at all"
    for tag in uses:
        assert "x-text=\"item.note\"" in tag or "x-show=\"item.note\"" in tag
    shown = [t for t in uses if "x-text=\"item.note\"" in t]
    assert len(shown) == 1 and 'dir="auto"' in shown[0] and "overflow-wrap:anywhere" in shown[0]


def test_every_message_has_automatic_text_direction_because_main_js_forces_the_page_to_ltr():
    assert re.search(r'<p [^>]*dir="auto"[^>]*x-text="msg\.content"', HTML)
    assert 'document.documentElement.setAttribute("dir", "ltr")' in (ROOT / "frontend/static/js/main.js").read_text(encoding="utf-8")


def test_card_and_panel_buttons_are_real_buttons_with_a_44px_target():
    for block in (card_block(), strip_block()):
        for tag in re.findall(r"<button\b[^>]*>", block):
            assert 'type="button"' in tag, tag
            assert "min-h-[44px]" in tag, tag


def test_the_picking_rows_work_by_keyboard_too():
    assert "@keydown.enter.prevent" in card_block() and "@keydown.space.prevent" in card_block() and 'tabindex' in card_block()


def test_the_send_includes_a_client_message_id_and_gives_the_reply_to_the_module():
    assert "client_msg_id: clientMsgId" in HTML and "this.xClientId(text)" in HTML
    assert "this.xAfterReply(data, this.formatTime())" in HTML and "this.xFailedSend = { text, id: clientMsgId }" in HTML
    assert "signal:  ctl.signal" in HTML                              # the send cannot wait for ever


def test_the_module_is_loaded_before_the_page_script_and_the_page_still_works_without_it():
    assert HTML.index('/static/js/expense_chat.js') < HTML.index("function chatApp()")
    assert "typeof window.expenseChat === 'function'" in HTML


def test_the_existing_chat_features_are_still_there():
    for needle in ("sendSuggested", "toggleRecording", "/voice/ask", "suggestedQuestions", "togglePlay", "clearChat", "msg.sources"):
        assert needle in HTML, needle


def test_the_page_does_no_money_arithmetic_and_stores_only_the_language_choice():
    for bad in ("parseFloat", "toFixed", "Number(", "parseInt(item"):
        assert bad not in JS, bad
    assert not re.search(r"paisa\w*\s*[-+*/%]|[-+*/%]\s*[\w.]*paisa", JS)          # no sum or conversion of any amount (the 60 * 60 * 1000 is time)
    assert re.findall(r"safeStorage\('set', (\w+)", JS) == ["LANG_KEY"]
    assert "se_token" in JS and "setItem('se_token'" not in JS


def test_no_verdict_words_appear_in_the_text():
    assert not re.search(r"fatwa|ruling", JS + card_block() + strip_block(), re.I)


def test_the_service_worker_caches_the_new_file_and_its_version_moved():
    assert "'/static/js/expense_chat.js'" in SW and "const VERSION = 'v3';" in SW
    assert (ROOT / "frontend/static/js/expense_chat.js").exists()


def test_the_chat_page_renders_and_the_script_is_served():
    app = FastAPI()
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=str(ROOT / "frontend/static")), name="static")
    c = TestClient(app)
    page = c.get("/chatbot")
    assert page.status_code == 200 and '/static/js/expense_chat.js' in page.text
    assert 'data-testid="expense-strip"' in page.text and 'data-testid="pro-button"' in page.text
    js = c.get("/static/js/expense_chat.js")
    assert js.status_code == 200 and "window.expenseChat" in js.text


def test_card_buttons_are_disabled_while_a_request_runs_and_confirm_also_until_an_entry_is_picked():
    tags = re.findall(r"<button\b[^>]*>", card_block())
    assert len(tags) >= 5
    for tag in tags:
        assert ':disabled="msg.busy' in tag, tag
    confirm = next(t for t in tags if 'data-testid="card-confirm"' in t)
    assert ':disabled="msg.busy || xNeedsPick(msg.card)"' in confirm


def test_the_confirm_and_cancel_row_shows_only_while_the_card_is_active_and_undo_only_within_its_hour():
    block = card_block()
    row = block[:block.index('data-testid="card-confirm"')]
    assert row.rstrip().endswith('<button type="button"') and 'x-show="xActive(msg)" class="flex gap-2"' in row[-200:]
    undo = re.search(r"<button[^>]*data-testid=\"card-undo\"[^>]*>", block).group(0)
    assert 'x-show="xCanUndo(msg)"' in undo
    assert 'x-show="msg.error"' in block and 'role="alert"' in block


def test_the_pro_button_cannot_be_pressed_twice_and_the_panel_shows_only_for_logged_in_people():
    tag = re.search(r"<button[^>]*data-testid=\"pro-button\"[^>]*>", strip_block()).group(0)
    assert ':disabled="xProBusy || xProDone"' in tag and '@click="xPro()"' in tag
    strip = re.search(r"<button[^>]*data-testid=\"pro-button-strip\"[^>]*>", strip_block()).group(0)
    assert ':disabled="xProBusy || xProDone"' in strip and '@click="xPro()"' in strip and "xState() === 'limit'" in strip
    assert 'x-show="xLoggedIn && xOpen"' in strip_block() and 'x-cloak' in strip_block()


def test_the_open_panel_scrolls_inside_a_height_cap_and_the_welcome_text_points_to_it():
    panel = re.search(r'<div x-cloak[^>]*data-testid="expense-panel"', HTML).group(0)
    assert "max-h-[50dvh]" in panel and "overflow-y-auto" in panel
    assert 'data-testid="expense-welcome-hint"' in HTML and "xt('w_hint')" in HTML


def test_the_status_strip_gets_out_of_the_way_when_the_keyboard_leaves_little_room():
    assert "[@media(max-height:480px)]:hidden" in HTML[HTML.index('data-testid="expense-strip"') - 200:HTML.index('data-testid="expense-strip"')]


def test_a_long_status_text_gets_most_of_the_row_and_the_urdu_valid_until_line_is_right_to_left():
    assert "basis-[65%]" in strip_block()
    assert "valid_until: 'یہ {time} تک درست ہے'," in JS          # begins with an Urdu word: dir="auto" then lays the line out right to left
