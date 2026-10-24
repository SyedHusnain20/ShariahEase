"""
ShariahEase — Chatbot as a LangGraph graph.

    START
      │
      ▼
   prepare ──(KB not loaded / off-topic)──────────────────────────► END
      │
      ├─► retrieve_rag ─┐
      ├─► web_search  ──┼─► assemble ─► generate ─► verify ──(problem, 1st try)──┐
      └─► live_nisab ───┘                  ▲           │                          │
          (Nisab questions only)           └───────────┼──────────────────────────┘
                                                       └─(ok / 2nd try)──► END

  prepare       language, Nisab detection, off-topic guard. A refused or
                unanswerable message stops here: no retrieval, no web search,
                no LLM call (previously all of those ran before the refusal).
  retrieve_rag  one FAISS search; gives both the context and the source names
                (previously the index was searched twice per message).
  web_search    existing get_web_context(); decides for itself whether the
                question needs live data.
  live_nisab    live gold/silver Nisab block.
  assemble      priority: live Nisab > web > knowledge base.
  generate      the existing get_chat_response() (Groq, retries, Urdu filter).
  verify        two cheap checks, at most one regeneration:
                  - a Nisab question must contain a live Nisab figure
                  - the answer must be in the question's script (English/Urdu)

The router still owns saving messages, history and TTS.
"""

import asyncio
import logging
import re
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from app.services.llm_client import (
    _error_message,
    detect_language,
    get_chat_response,
    off_topic_refusal,
    should_refuse,
)
from app.services.nisab_context import get_live_nisab_block, is_nisab_question
from app.services.rag_service import rag_service
from app.services.web_search import get_web_context

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2   # first answer + at most one regeneration
KB_NOT_LOADED = "Knowledge base not loaded. Please run: python knowledge_base/build_index.py"


class ChatState(TypedDict, total=False):
    message: str
    history: list
    lang: str
    wants_nisab: bool
    finished: bool
    rag_chunks: list
    web_context: str
    nisab_block: str
    nisab_figures: list
    context: str
    sources: list
    answer: str
    attempts: int
    correction: str


# ── Thin wrappers around the existing services (replaceable in tests) ──────────

def _kb_ready() -> bool:
    return rag_service.is_ready


def _rag_search(message: str) -> list:
    return rag_service.search(message, top_k=5)


def _format_rag(chunks: list) -> str:
    return rag_service.format_context(chunks)


async def _web_context(message: str) -> str:
    context, _ = await get_web_context(message)
    return context


async def _live_nisab() -> tuple[str, dict]:
    return await get_live_nisab_block()


def _generate(message, context, history, wants_nisab, correction) -> str:
    return get_chat_response(
        user_message     = message,
        context          = context,
        chat_history     = history,
        is_nisab_related = wants_nisab,
        correction       = correction,
    )


# ── Verification helpers (pure functions) ──────────────────────────────────────

_EASTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ASKS_ABOUT_NISAB = re.compile(r"nisab|nisaab|نصاب")
# The user explicitly asked for a language or a translation, so an answer in
# the other script is correct and must not be "fixed".
_LANGUAGE_REQUEST_WORDS = ("urdu", "english", "translat", "ترجم", "اردو", "انگریزی")


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text.translate(_EASTERN_DIGITS))


def find_problem(state: ChatState) -> str:
    """Return a correction instruction for the model, or "" if the answer is fine."""
    answer = state.get("answer", "")
    if not answer or answer.startswith("⚠️"):
        return ""   # nothing to check, or a service-error message

    message = state.get("message", "")
    lang = state.get("lang", "en")

    # 1. Live Nisab figures must actually be stated.
    figures = state.get("nisab_figures") or []
    if figures and _ASKS_ABOUT_NISAB.search(message.lower()):
        digits = _digits_only(answer)
        if not any(str(f) in digits for f in figures):
            gold, silver = figures[0], figures[1]
            return (
                "Your previous answer did not state the live Nisab figures from the context. "
                f"State them exactly: gold Nisab PKR {gold:,} and silver Nisab PKR {silver:,}."
            )

    # 2. Answer script must match the question (English <-> Urdu only; Roman
    #    Urdu is too close to English to detect reliably).
    if not any(w in message.lower() for w in _LANGUAGE_REQUEST_WORDS):
        answer_lang = detect_language(answer)
        if lang == "en" and answer_lang == "ur":
            return "Your previous answer was in Urdu script. Reply in English only."
        if lang == "ur" and answer_lang != "ur":
            return "Your previous answer was not in Urdu script. Reply in Urdu script only."

    return ""


# ── Nodes ──────────────────────────────────────────────────────────────────────

async def prepare(state: ChatState) -> dict:
    message = state["message"]
    lang = detect_language(message)

    if not _kb_ready():
        return {"lang": lang, "finished": True, "answer": KB_NOT_LOADED, "sources": []}

    if should_refuse(message, state.get("history", [])):
        return {"lang": lang, "finished": True,
                "answer": off_topic_refusal(lang), "sources": []}

    return {"lang": lang, "finished": False, "wants_nisab": is_nisab_question(message)}


def route_after_prepare(state: ChatState) -> list[str]:
    if state.get("finished"):
        return [END]
    targets = ["retrieve_rag", "web_search"]
    if state.get("wants_nisab"):
        targets.append("live_nisab")
    return targets   # several names run in parallel


async def retrieve_rag(state: ChatState) -> dict:
    try:
        chunks = await asyncio.to_thread(_rag_search, state["message"])
    except Exception as exc:
        logger.error("RAG search failed: %s", exc)
        chunks = []
    return {"rag_chunks": chunks}


async def web_search(state: ChatState) -> dict:
    try:
        context = await _web_context(state["message"])
    except Exception as exc:   # live search is best-effort
        logger.warning("Chat web search failed: %s", exc)
        context = ""
    return {"web_context": context}


async def live_nisab(state: ChatState) -> dict:
    block, data = await _live_nisab()
    figures = []
    if data:
        figures = [int(data["gold_nisab_pkr"]), int(data["silver_nisab_pkr"])]
    return {"nisab_block": block, "nisab_figures": figures}


async def assemble(state: ChatState) -> dict:
    chunks = state.get("rag_chunks", [])
    parts = [state.get("nisab_block", ""), state.get("web_context", ""), _format_rag(chunks)]
    context = "\n\n".join(p for p in parts if p)

    # Same naming as before: the top 3 chunks' source files, de-duplicated.
    names = [
        c["source"].replace("_", " ").replace(".txt", "").title()
        for c in chunks[:3]
    ]
    return {"context": context, "sources": list(dict.fromkeys(names))}


async def generate(state: ChatState) -> dict:
    attempts = state.get("attempts", 0) + 1
    try:
        answer = await asyncio.to_thread(
            _generate,
            state["message"],
            state.get("context", ""),
            state.get("history", []),
            state.get("wants_nisab", False),
            state.get("correction", ""),
        )
    except Exception as exc:
        logger.error("Chat generation failed: %s", exc)
        answer = _error_message(exc, state.get("lang", "en"))
    return {"answer": answer, "attempts": attempts, "correction": ""}


async def verify(state: ChatState) -> dict:
    problem = find_problem(state)
    if problem:
        logger.warning("Chat answer check failed (attempt %s): %s",
                       state.get("attempts"), problem)
    # Only ask for a regeneration while attempts remain.
    return {"correction": problem if state.get("attempts", 1) < MAX_ATTEMPTS else ""}


def route_after_verify(state: ChatState) -> str:
    return "generate" if state.get("correction") else END


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(ChatState)
    g.add_node("prepare", prepare)
    g.add_node("retrieve_rag", retrieve_rag)
    g.add_node("web_search", web_search)
    g.add_node("live_nisab", live_nisab)
    g.add_node("assemble", assemble)
    g.add_node("generate", generate)
    g.add_node("verify", verify)

    g.add_edge(START, "prepare")
    g.add_conditional_edges(
        "prepare", route_after_prepare,
        ["retrieve_rag", "web_search", "live_nisab", END],
    )
    # Separate edges (not a list): "live_nisab" only runs for Nisab questions,
    # and "assemble" must still run when it didn't.
    g.add_edge("retrieve_rag", "assemble")
    g.add_edge("web_search", "assemble")
    g.add_edge("live_nisab", "assemble")
    g.add_edge("assemble", "generate")
    g.add_edge("generate", "verify")
    g.add_conditional_edges("verify", route_after_verify, ["generate", END])
    return g.compile()


_graph = None


async def run_chat(message: str, history: list, channel: str = "web") -> dict:
    """
    Answer one chat message.
    Returns {"answer": str, "sources": [str], "lang": "en" | "ur" | "roman"}.
    """
    global _graph
    if _graph is None:
        _graph = build_graph()
    final = await _graph.ainvoke(
        {"message": message, "history": history, "attempts": 0},
        config={"run_name": "chat", "tags": ["chat", channel],
                "metadata": {"channel": channel}},
    )
    return {
        "answer":  final["answer"],
        "sources": final.get("sources", []),
        "lang":    final.get("lang", "en"),
    }
