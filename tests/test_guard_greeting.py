"""A greeting word must be matched as a word, never inside another word.

Before the fix the guards used `if greeting in text`, so "hi" unlocked "machine",
"this" and "which", and "hey" unlocked "they". Any off-topic question that happened
to contain such a word skipped the off-topic check.
"""

import pytest

from app.services import whatsapp_service
from app.services.greeting import contains_greeting
from app.services.llm_client import is_off_topic, should_refuse

# Off-topic questions that contain a greeting word inside a longer word.
HIDDEN_GREETING = [
    "Tell me about machine learning",         # mac-hi-ne
    "Explain this to me",                     # t-hi-s
    "Which team won the cricket match?",      # w-hi-ch
    "They won the cricket match",             # T-hey
    "Recommend a good hiking trail",          # hi-king
    "Give me the history of Rome",            # hi-story
    "Tell me about salamander habitats",      # salam-ander
    "He plays cricket in the evening",        # "he" is not "hey"
    "Hell is a hot place",                    # "hell" is not "hello"
]

# Real greetings, in the spellings people actually type.
REAL_GREETINGS = [
    "hi", "Hi!", "  HI  ", "hello", "Hello there", "hey", "Hey, how are you?",
    "salam", "Salam!", "assalam", "Assalamualaikum", "Assalamu alaikum",
    "Assalamo alaikum", "assalam o alaikum", "Walaikum assalam", "AOA", "aoa!",
    "good morning", "Good Evening!", "good night", "good afternoon",
    "hii", "heyy", "helloo",
    "السلام علیکم", "سلام", "ہیلو", "آداب", "جی",
]


@pytest.mark.parametrize("text", HIDDEN_GREETING)
def test_a_greeting_inside_another_word_is_not_a_greeting(text):
    assert is_off_topic(text) is True
    assert should_refuse(text, []) is True


@pytest.mark.parametrize("text", REAL_GREETINGS)
def test_real_greetings_still_get_through(text):
    assert is_off_topic(text) is False
    assert should_refuse(text, []) is False


@pytest.mark.parametrize("text", HIDDEN_GREETING)
def test_whatsapp_guard_also_ignores_greetings_inside_words(text):
    assert whatsapp_service._is_allowed(text) is False


@pytest.mark.parametrize("text", [
    "hi", "Hi!", "hello", "hey", "helo", "slm", "salam", "AOA", "aoa!",
    "assalam", "Assalamualaikum", "Assalamu alaikum", "assalam o alaikum",
    "Walaikum assalam", "walaikumassalam", "hii", "heyy",
])
def test_whatsapp_real_greetings_still_get_through(text):
    assert whatsapp_service._is_allowed(text) is True


def test_a_greeting_next_to_punctuation_or_a_question_still_counts():
    assert is_off_topic("hi, can you help me?") is False
    assert is_off_topic("hello?") is False
    assert is_off_topic("(salam)") is False


def test_the_off_topic_check_still_works_for_ordinary_questions():
    for text in ["Explain photosynthesis", "Write me a Python script", "Who is the president of Pakistan?"]:
        assert is_off_topic(text) is True


# ── the helper itself ───────────────────────────────────────────────────────────

def test_helper_ignores_case():
    assert contains_greeting("HELLO there", {"hello"}) is True


def test_helper_with_no_terms_matches_nothing():
    assert contains_greeting("hello", set()) is False
    assert contains_greeting("hello", set(), set()) is False


def test_a_stem_may_continue_but_a_word_may_not():
    assert contains_greeting("assalamualaikum", set(), {"assalam"}) is True
    assert contains_greeting("assalamualaikum", {"assalam"}) is False


def test_a_stem_must_start_a_word():
    assert contains_greeting("walaikumassalam", set(), {"assalam"}) is False
    assert contains_greeting("walaikumassalam", set(), {"walaikum"}) is True
