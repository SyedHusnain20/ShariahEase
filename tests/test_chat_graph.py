import asyncio

import pytest

from app.services import chat_graph as cg
from app.services.llm_client import off_topic_refusal, should_refuse

GOLD, SILVER = 1823456, 98765
EN_OK = "Zakat is 2.5% of zakatable wealth above the Nisab."
UR_OK = "زکوٰۃ قابلِ زکوٰۃ مال کا ڈھائی فیصد ہے۔"
NISAB_OK = f"Gold Nisab is PKR {GOLD:,} and silver Nisab is PKR {SILVER:,} today."

CHUNKS = [
    {"text": "t1", "source": "zakat_rules.txt", "score": 0.1},
    {"text": "t2", "source": "shariah_screening.txt", "score": 0.2},
    {"text": "t3", "source": "zakat_rules.txt", "score": 0.3},
    {"text": "t4", "source": "islamic_finance_concepts.txt", "score": 0.4},
]


@pytest.fixture
def env(monkeypatch):
    """Replace RAG, web search, live Nisab and the LLM with recording stubs."""
    log = {"rag": 0, "web": 0, "nisab": 0, "gen": [], "replies": []}

    def rag(message):
        log["rag"] += 1
        return CHUNKS

    async def web(message):
        log["web"] += 1
        return "WEB CONTEXT"

    async def nisab():
        log["nisab"] += 1
        return "NISAB BLOCK", {"gold_nisab_pkr": GOLD, "silver_nisab_pkr": SILVER}

    def gen(message, context, history, wants_nisab, correction):
        log["gen"].append({"context": context, "wants_nisab": wants_nisab,
                           "correction": correction})
        return log["replies"].pop(0) if log["replies"] else EN_OK

    monkeypatch.setattr(cg, "_kb_ready", lambda: True)
    monkeypatch.setattr(cg, "_rag_search", rag)
    monkeypatch.setattr(cg, "_web_context", web)
    monkeypatch.setattr(cg, "_live_nisab", nisab)
    monkeypatch.setattr(cg, "_generate", gen)
    monkeypatch.setattr(cg, "_graph", None)
    return log


def chat(message, history=None):
    return asyncio.run(cg.run_chat(message, history or []))


# ── normal path ────────────────────────────────────────────────────────────────

def test_normal_question_retrieves_once_and_generates_once(env):
    out = chat("What is zakat?")
    assert out["answer"] == EN_OK
    assert (env["rag"], env["web"], env["nisab"], len(env["gen"])) == (1, 1, 0, 1)
    ctx = env["gen"][0]["context"]
    assert "WEB CONTEXT" in ctx and "[Source 1: zakat rules]" in ctx
    assert env["gen"][0]["wants_nisab"] is False
    assert out["sources"] == ["Zakat Rules", "Shariah Screening"]   # top 3, de-duplicated


def test_context_priority_is_nisab_then_web_then_rag(env):
    env["replies"] = [NISAB_OK]
    chat("What is the gold nisab?")
    ctx = env["gen"][0]["context"]
    assert ctx.index("NISAB BLOCK") < ctx.index("WEB CONTEXT") < ctx.index("[Source 1")


# ── early exits skip all retrieval and generation ──────────────────────────────

def test_off_topic_question_is_refused_without_retrieval_or_llm(env):
    out = chat("Who is the president of Pakistan?")
    assert out["answer"] == off_topic_refusal("en")
    assert out["sources"] == []
    assert (env["rag"], env["web"], env["nisab"], len(env["gen"])) == (0, 0, 0, 0)


def test_knowledge_base_not_loaded(env, monkeypatch):
    monkeypatch.setattr(cg, "_kb_ready", lambda: False)
    out = chat("What is zakat?")
    assert out["answer"] == cg.KB_NOT_LOADED
    assert len(env["gen"]) == 0


# ── verify: Nisab figures ──────────────────────────────────────────────────────

def test_nisab_answer_with_live_figures_is_accepted_first_time(env):
    env["replies"] = [NISAB_OK]
    out = chat("What is the gold nisab today?")
    assert out["answer"] == NISAB_OK
    assert env["nisab"] == 1 and len(env["gen"]) == 1


def test_nisab_answer_without_figures_is_regenerated_once_with_the_figures(env):
    env["replies"] = ["Nisab is the minimum wealth threshold.", NISAB_OK]
    out = chat("What is the gold nisab today?")
    assert out["answer"] == NISAB_OK
    assert len(env["gen"]) == 2
    assert env["gen"][0]["correction"] == ""
    assert f"{GOLD:,}" in env["gen"][1]["correction"]
    assert f"{SILVER:,}" in env["gen"][1]["correction"]


def test_regeneration_is_capped_at_one_retry(env):
    env["replies"] = ["no figures", "still no figures", "never reached"]
    out = chat("What is the gold nisab today?")
    assert out["answer"] == "still no figures"
    assert len(env["gen"]) == 2


def test_eastern_arabic_digits_count_as_the_figure(env):
    urdu_digits = "سونے کا نصاب ۱۸۲۳۴۵۶ روپے ہے۔"
    env["replies"] = [urdu_digits]
    chat("سونے کا نصاب کتنا ہے؟")
    assert len(env["gen"]) == 1


def test_non_nisab_question_is_never_checked_for_figures(env):
    chat("What is zakat?")
    assert len(env["gen"]) == 1


# ── verify: language ───────────────────────────────────────────────────────────

def test_urdu_answer_to_english_question_is_regenerated(env):
    env["replies"] = [UR_OK, EN_OK]
    out = chat("What is zakat?")
    assert out["answer"] == EN_OK
    assert "English" in env["gen"][1]["correction"]


def test_english_answer_to_urdu_question_is_regenerated(env):
    env["replies"] = [EN_OK, UR_OK]
    out = chat("زکوٰۃ کیا ہے؟")
    assert out["answer"] == UR_OK
    assert "Urdu" in env["gen"][1]["correction"]


def test_explicit_translation_request_is_not_second_guessed(env):
    env["replies"] = [UR_OK]
    out = chat("Explain zakat in Urdu")
    assert out["answer"] == UR_OK
    assert len(env["gen"]) == 1


# ── failures degrade gracefully ────────────────────────────────────────────────

def test_service_error_message_is_not_retried(env):
    env["replies"] = ["⚠️ Rate limit reached. Please wait 30 seconds and try again."]
    out = chat("What is zakat?")
    assert out["answer"].startswith("⚠️")
    assert len(env["gen"]) == 1


def test_generation_exception_becomes_an_error_message(env, monkeypatch):
    def boom(*args):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(cg, "_generate", boom)
    out = chat("What is zakat?")
    assert out["answer"].startswith("⚠️")


def test_web_search_failure_does_not_stop_the_answer(env, monkeypatch):
    async def boom(message):
        raise RuntimeError("network down")
    monkeypatch.setattr(cg, "_web_context", boom)
    out = chat("What is zakat?")
    assert out["answer"] == EN_OK
    assert "WEB CONTEXT" not in env["gen"][0]["context"]


def test_rag_failure_does_not_stop_the_answer(env, monkeypatch):
    def boom(message):
        raise RuntimeError("index corrupted")
    monkeypatch.setattr(cg, "_rag_search", boom)
    out = chat("What is zakat?")
    assert out["answer"] == EN_OK
    assert out["sources"] == []


# ── the extracted off-topic guard ──────────────────────────────────────────────

def test_should_refuse_basic_cases():
    assert should_refuse("Who is the president of Pakistan?", []) is True
    assert should_refuse("What is riba?", []) is False


def test_off_topic_wording_is_allowed_after_islamic_finance_answers():
    history = [{"role": "assistant", "content": "Zakat is 2.5% of wealth above the Nisab."}]
    assert should_refuse("Who is the president of Pakistan?", history) is False
