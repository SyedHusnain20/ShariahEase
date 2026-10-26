"""The chat guard: stock-price questions are allowed, and a refusal can't unlock later questions."""

import pytest

from app.services.llm_client import is_off_topic, off_topic_refusal, should_refuse


# ── price questions about a recognisable company ───────────────────────────────

@pytest.mark.parametrize("text", [
    "What is the prize of Apple stock",          # the exact message that was refused
    "What is the price of Apple stock?",
    "Tesla share price",
    "what is $TSLA trading at",
    "What is the prize of lucky cement stock",
    "OGDC stock price today",
])
def test_price_questions_about_known_companies_are_allowed(text):
    assert is_off_topic(text) is False
    assert should_refuse(text, []) is False


@pytest.mark.parametrize("text", [
    "Who founded Apple?",                         # a company, but not a price question
    "Tell me about Tesla's cars",
    "What is the price of a pineapple?",          # price word, but no recognised company
    "What is the price of bread",
    "stock photos of cats",
])
def test_other_questions_are_still_refused(text):
    assert should_refuse(text, []) is True


# ── a refusal must not unlock the next question ────────────────────────────────

def refusal_history(lang="en"):
    return [
        {"role": "user", "content": "Who is the president of Pakistan?"},
        {"role": "assistant", "content": off_topic_refusal(lang)},
    ]


@pytest.mark.parametrize("lang", ["en", "ur", "roman"])
def test_off_topic_question_right_after_a_refusal_is_refused_again(lang):
    # Before the fix the refusal text itself ("...Zakat, halal investing...") counted
    # as Islamic-finance context, so this second question got through.
    assert should_refuse("Explain photosynthesis", refusal_history(lang)) is True


def test_a_model_written_refusal_is_recognised_too():
    history = [{"role": "assistant", "content":
                "I can only assist with Islamic finance questions — such as Zakat. Please ask within scope."}]
    assert should_refuse("Explain photosynthesis", history) is True


def test_error_messages_do_not_count_as_context():
    history = [{"role": "assistant", "content": "⚠️ Rate limit reached. Zakat halal riba Nisab."}]
    assert should_refuse("Explain photosynthesis", history) is True


def test_real_follow_ups_still_work_after_an_islamic_finance_answer():
    history = [
        {"role": "user", "content": "What is riba?"},
        {"role": "assistant", "content": "Riba is interest, which is prohibited in Islamic finance."},
    ]
    assert should_refuse("Who is the president of Pakistan?", history) is False


def test_a_real_answer_before_a_refusal_still_counts():
    history = [
        {"role": "assistant", "content": "Zakat is 2.5% of zakatable wealth above the Nisab."},
        {"role": "user", "content": "Who is the president of Pakistan?"},
        {"role": "assistant", "content": off_topic_refusal("en")},
    ]
    # the earlier answer is genuine context, so a follow-up is still allowed
    assert should_refuse("Who is the president of Pakistan?", history) is False
