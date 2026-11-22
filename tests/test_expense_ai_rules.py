"""Step 4b: rules that are checked by reading the code and the text: privacy, ordering, templates, the cheap pre-check. Offline."""
import re
from pathlib import Path

import pytest

from app.services import expense_ai as ai
from app.services import pending_actions as pa

ROOT = Path(__file__).resolve().parents[1]
EXPENSE_AI_FILES = ["app/services/expense_ai.py", "app/services/expense_understand.py", "app/services/pending_actions.py",
                    "app/services/ai_limits.py", "app/services/expense_normalise.py", "app/routers/expense_ai.py"]


def source(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ── privacy by construction ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", EXPENSE_AI_FILES)
def test_the_expense_path_never_touches_the_traced_chat_graph_or_prints(rel):
    code = source(rel)
    assert not re.search(r"chat_graph|run_chat|traceable|screener_graph", code), rel
    assert not re.search(r"^\s*print\(", code, re.M), rel
    if rel != "app/services/expense_understand.py":
        assert "langsmith" not in code.lower(), rel


def test_only_the_understand_call_knows_langsmith_and_only_to_switch_it_off():
    code = source("app/services/expense_understand.py")
    assert code.count("langsmith") >= 1 and "tracing_context(enabled=False)" in code
    assert "enabled=True" not in code


def test_the_expense_path_does_not_use_the_text_to_speech_or_web_search_services():
    for rel in EXPENSE_AI_FILES:
        assert not re.search(r"tts_service|text_to_speech|web_search|whisper", source(rel)), rel


def test_log_calls_carry_codes_and_class_names_only():
    for rel in EXPENSE_AI_FILES:
        for call in re.findall(r"log(?:ger)?\.(?:debug|info|warning|error|exception)\((.*)\)", source(rel)):
            assert not re.search(r"\b(message|text|note|content|body|payload|amount_text|exc\b(?!\.__)|err\b)(?!_)", call.replace("type(exc).__name__", "")), call


def test_no_free_model_text_reaches_the_person():
    """The reply text is built from the fixed templates only: nothing from the model's answer is put into an `answer`."""
    code = source("app/services/expense_ai.py")
    assert "result.understood" in code and "content" not in code.replace("clean_message", "")
    assert "item.note" not in ai.items_text.__code__.co_names and "note" not in ai.items_text.__code__.co_names   # the note is never put in the text


# ── ordering in the chat route ──────────────────────────────────────────────────────────────────

def test_the_assistant_runs_before_the_history_is_saved_and_before_the_chat_graph():
    code = source("app/routers/chatbot.py")
    handle, saved, graph = (code.index(x) for x in ("expense_ai.handle(", "save_message(db, request.session_id, \"user\"", "await run_chat("))
    assert handle < saved < graph
    assert "uid is not None" in code[code.index("# AI expense assistant"):handle]            # logged-in people only


def test_the_assistant_return_happens_before_text_to_speech():
    code = source("app/routers/chatbot.py")
    assert code.index("card=handled.card") < code.index("text_to_speech_bytes(answer, lang)")


def test_the_router_is_included_in_the_app():
    assert "expense_ai.router" in source("main.py")


# ── the privacy page says what the code does ────────────────────────────────────────────────────

def test_the_privacy_page_numbers_match_the_card_rules():
    page = re.sub(r"<[^>]+>", " ", source("frontend/templates/pages/privacy.html"))
    page = re.sub(r"\s+", " ", page)
    assert f"valid for {int(pa.PENDING_TTL.total_seconds() // 60)} minutes" in page
    assert f"removed after {pa.KEEP_DAYS} days" in page
    assert "not saved in your chat history, not read aloud, and not sent to LangSmith" in page


# ── templates ───────────────────────────────────────────────────────────────────────────────────

def test_every_template_exists_in_all_three_languages_with_the_same_placeholders():
    for kind, langs in ai.T.items():
        assert set(langs) == {"en", "roman_ur", "ur"}, kind
        holders = {lang: set(re.findall(r"\{(\w+)\}", text)) for lang, text in langs.items()}
        assert holders["en"] == holders["roman_ur"] == holders["ur"], kind


def test_urdu_templates_are_in_urdu_script_and_the_others_are_not():
    for kind, langs in ai.T.items():
        assert re.search(r"[\u0600-\u06FF]", langs["ur"]), kind
        assert not re.search(r"[\u0600-\u06FF]", langs["en"] + langs["roman_ur"]), kind


def test_no_template_gives_a_verdict_or_ruling_or_uses_the_word_fatwa():
    for langs in ai.T.values():
        for text in langs.values():
            assert not re.search(r"fatwa|ruling|فتویٰ|فتوی", text, re.I)


def test_every_reply_key_the_code_uses_has_a_template():
    used = set(re.findall(r'_say\(\s*"(\w+)"', source("app/services/expense_ai.py")))
    used |= set(ai._CARD_ERROR_KEY.values()) | {"amount", "date", "category", "no_items", "too_many"}
    assert used <= set(ai.T)


def test_the_unavailable_and_refusal_texts_promise_nothing_false_about_the_allowance():
    for lang, text in ai.T["unavailable"].items():
        assert text and ("allowance" in text.lower() or "الاؤنس" in text)
    assert "not used" in ai.T["breaker"]["en"] and "not used" in ai.T["not_yet"]["en"]


@pytest.mark.parametrize("message,expected", [
    ("chai 50", True), ("petrol 3000", True), ("bijli ka bill 8500 diya", True), ("۸۵۰۰ ادا کیا", True), ("بجلی کا بل ادا کیا", True),
    ("petrol 2 hazar ka", True), ("is mahine kitna kharcha hua", True), ("pichli entry delete kar do", True), ("how much did I spend", True),
    ("What is riba?", False), ("assalam o alaikum", False), ("tell me about mudarabah", False), ("ek sawal hai", False),
    ("can you do that", False), ("", False), ("zakat kya hai", False), ("hello", False),
])
def test_the_cheap_precheck(message, expected):
    assert ai.looks_like_expense_talk(message) is expected


@pytest.mark.parametrize("message,expected", [
    ("yes", "yes"), ("Yes!", "yes"), ("haan", "yes"), ("  ہاں ", "yes"), ("جی ہاں", "yes"), ("confirm", "yes"), ("save", "yes"),
    ("no", "no"), ("nahi", "no"), ("نہیں", "no"), ("cancel", "no"), ("Cancel.", "no"),
    ("ok", None), ("okay", None), ("ji", None), ("sure", None), ("yes please save it", None), ("no 50", None), ("", None),
    ("yes and also 500 chai", None), ("thanks", None),
])
def test_only_an_explicit_yes_or_no_confirms_or_cancels(message, expected):
    assert ai.yes_or_no(message) == expected


def test_the_language_names_map_to_reply_languages():
    assert [ai._lang(x) for x in ("en", "ur", "roman_ur", "mixed", "zz")] == ["en", "ur", "roman_ur", "roman_ur", "en"]


@pytest.mark.parametrize("value,ok", [("abc-123_X", True), ("x" * 64, True), ("x" * 65, False), ("has space", False), ("", False),
                                      ("a/b", False), ("semi;colon", False), ("new\nline", False)])
def test_a_chat_message_id_is_a_short_plain_token(value, ok):
    assert (ai._MSG_ID.match(value) is not None) is ok
