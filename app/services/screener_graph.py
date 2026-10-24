"""
ShariahEase — Investment screener as a LangGraph graph.

    START
      │
      ▼
  kmi_lookup ──(HALAL / HARAM entry)──────────► rag ───┬─ quote ─┐
      │                                                  │         │
      └─(no entry, or DOUBTFUL)──┬─► rag ───────────────►├─ quote ─┼─► classify ─► enforce ─► END
                                 └─► web ───────────────►┘         │

  kmi_lookup  local KMI database, instant, no network
  rag         FAISS knowledge-base context
  web         live web search (skipped when the KMI database already gives a
              definite HALAL/HARAM answer — the verdict can't change, and the
              search only added latency)
  quote       live share price from the price APIs (PSX or foreign stocks).
              Runs every time; it never affects the verdict, it is only shown
              next to it.
  classify    one LLM call that writes the verdict, confidence and reason
  enforce     deterministic guard: a definite KMI entry always wins over the LLM

The router still owns authentication, input validation and persistence.
"""

import asyncio
import logging
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from app.services.stock_price import format_price_line, get_quotes_for_query
from app.services.web_search import lookup_shariah_status, format_shariah_context

logger = logging.getLogger(__name__)

DEFINITE_STATUSES = {"halal", "haram"}


class ScreenerState(TypedDict, total=False):
    query: str
    kmi_name: str | None
    kmi_data: dict | None
    kmi_context: str
    rag_context: str
    web_context: str
    quotes: list
    parsed: dict


# ── Thin wrappers around the existing services ─────────────────────────────────
# Imported lazily and kept as module-level functions so tests can replace them
# without loading FAISS, sentence-transformers or a Groq client.

def _build_rag_context(query: str) -> str:
    from app.services.rag_service import rag_service
    return rag_service.build_context(f"halal haram shariah screening {query}", top_k=5)


async def _get_web_context(query: str) -> str:
    from app.services.web_search import get_web_context
    # include_quotes=False: the price is added by the "quote" node instead.
    context, _ = await get_web_context(
        f"{query} halal shariah compliant investment", include_quotes=False
    )
    return context


def _call_llm(query: str, context: str, has_kmi_data: bool) -> dict:
    from app.services.llm_client import screen_investment
    return screen_investment(query, context, has_kmi_data=has_kmi_data)


# ── Deterministic KMI guard ────────────────────────────────────────────────────

def is_definite(kmi_data: dict | None) -> bool:
    return bool(kmi_data) and kmi_data["status"] in DEFINITE_STATUSES


def _kmi_reason(name: str | None, data: dict) -> tuple[str, str]:
    """Reason and recommendation text written from the KMI entry alone."""
    label = f"{(name or data['ticker']).title()} ({data['ticker']})"
    if data["status"] == "halal":
        reason = (
            f"{label} is listed on the PSX KMI All-Share Shariah Index "
            f"({data['sector']}), which makes it permissible for Muslim investors."
        )
        recommendation = (
            "Check the latest KMI list before investing, as the index is revised periodically."
        )
    else:
        why = data.get("reason", "It is not Shariah-compliant.")
        reason = f"{label} is not permissible. {why}."
        recommendation = (
            "Consider a Shariah-compliant alternative, such as an Islamic fund or a KMI-listed stock."
        )
    return reason, recommendation


def enforce_kmi_verdict(parsed: dict, kmi_name: str | None, kmi_data: dict | None) -> dict:
    """
    For a definite KMI entry the verdict is whatever the database says.

    If the LLM disagreed, or the LLM call failed, the verdict, confidence,
    reason and recommendation are all replaced with text written from the KMI
    entry, so the explanation never contradicts the verdict.
    """
    if not is_definite(kmi_data):
        return parsed

    expected = kmi_data["status"].upper()
    result = dict(parsed)

    if result.get("verdict") != expected:
        logger.warning(
            "KMI override: llm=%s kmi=%s query_match=%s",
            result.get("verdict"), expected, kmi_name,
        )
        result["verdict"] = expected
        result["reason"], result["recommendation"] = _kmi_reason(kmi_name, kmi_data)

    result["confidence"] = "HIGH"
    return result


# ── Nodes ──────────────────────────────────────────────────────────────────────

async def kmi_lookup(state: ScreenerState) -> dict:
    name, data = lookup_shariah_status(state["query"])
    context = format_shariah_context(name, data) if data else ""
    return {"kmi_name": name, "kmi_data": data, "kmi_context": context}


def route_after_kmi(state: ScreenerState) -> list[str]:
    # Returning two node names runs both of them in parallel.
    return ["rag", "quote"] if is_definite(state.get("kmi_data")) else ["rag", "web", "quote"]


async def rag(state: ScreenerState) -> dict:
    context = await asyncio.to_thread(_build_rag_context, state["query"])
    return {"rag_context": context}


async def web(state: ScreenerState) -> dict:
    try:
        context = await _get_web_context(state["query"])
    except Exception as exc:  # live search is best-effort
        logger.warning("Screener web search failed: %s", exc)
        context = ""
    return {"web_context": context}


async def quote(state: ScreenerState) -> dict:
    try:
        quotes = await get_quotes_for_query(state["query"])
    except Exception as exc:  # a price is a nice-to-have, never a reason to fail
        logger.warning("Screener quote lookup failed: %s", exc)
        quotes = []
    return {"quotes": quotes}


async def classify(state: ScreenerState) -> dict:
    kmi_context = state.get("kmi_context", "")
    web_context = state.get("web_context", "")
    # get_web_context() already prepends the KMI block for a matched company,
    # so don't hand the model the same block twice.
    if kmi_context and kmi_context in web_context:
        kmi_context = ""

    parts = [p for p in (kmi_context, web_context, state.get("rag_context", "")) if p]
    context = "\n\n".join(parts)

    parsed = await asyncio.to_thread(
        _call_llm, state["query"], context, bool(state.get("kmi_data"))
    )
    return {"parsed": parsed}


async def enforce(state: ScreenerState) -> dict:
    parsed = enforce_kmi_verdict(
        state["parsed"], state.get("kmi_name"), state.get("kmi_data")
    )
    quotes = state.get("quotes") or []
    parsed = dict(parsed, price_line=format_price_line(quotes[0]) if quotes else "")
    return {"parsed": parsed}


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(ScreenerState)
    g.add_node("kmi_lookup", kmi_lookup)
    g.add_node("rag", rag)
    g.add_node("web", web)
    g.add_node("quote", quote)
    g.add_node("classify", classify)
    g.add_node("enforce", enforce)

    g.add_edge(START, "kmi_lookup")
    g.add_conditional_edges("kmi_lookup", route_after_kmi, ["rag", "web", "quote"])
    # Two separate edges (not a list): "classify" must also run when only
    # "rag" ran, which is the case for a definite KMI entry.
    g.add_edge("rag", "classify")
    g.add_edge("web", "classify")
    g.add_edge("quote", "classify")
    g.add_edge("classify", "enforce")
    g.add_edge("enforce", END)
    return g.compile()


_graph = None


async def run_screener(query: str) -> dict:
    """Screen one investment query. Returns verdict, confidence, reason, recommendation."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    final = await _graph.ainvoke(
        {"query": query},
        config={"run_name": "screener", "tags": ["screener"]},
    )
    return final["parsed"]
