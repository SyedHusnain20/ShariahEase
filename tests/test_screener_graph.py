import asyncio

import pytest

from app.services import screener_graph as sg
from app.services.web_search import KMI_SHARIAH_DB


def llm_result(verdict="DOUBTFUL", confidence="LOW", reason="llm reason", rec="llm rec"):
    return {"verdict": verdict, "confidence": confidence,
            "reason": reason, "recommendation": rec}


# ── enforce_kmi_verdict (pure function) ────────────────────────────────────────

def test_halal_entry_overrides_llm_doubt():
    out = sg.enforce_kmi_verdict(
        llm_result("DOUBTFUL"), "lucky cement", KMI_SHARIAH_DB["lucky cement"]
    )
    assert out["verdict"] == "HALAL"
    assert out["confidence"] == "HIGH"
    assert "KMI" in out["reason"]
    assert "llm reason" not in out["reason"]          # no contradicting explanation


def test_haram_entry_overrides_llm_and_keeps_database_reason():
    out = sg.enforce_kmi_verdict(llm_result("HALAL"), "hbl", KMI_SHARIAH_DB["hbl"])
    assert out["verdict"] == "HARAM"
    assert "riba" in out["reason"]


def test_agreeing_llm_output_is_kept():
    out = sg.enforce_kmi_verdict(
        llm_result("HALAL", "MEDIUM", "good explanation"),
        "lucky cement", KMI_SHARIAH_DB["lucky cement"],
    )
    assert out["reason"] == "good explanation"        # not replaced
    assert out["confidence"] == "HIGH"


@pytest.mark.parametrize("entry", ["hascol", None])
def test_doubtful_or_missing_entry_leaves_llm_output_alone(entry):
    data = KMI_SHARIAH_DB[entry] if entry else None
    parsed = llm_result("HARAM", "MEDIUM")
    assert sg.enforce_kmi_verdict(parsed, entry, data) == parsed


# ── graph routing, with the slow/external parts replaced ───────────────────────

@pytest.fixture
def calls(monkeypatch):
    log = {"rag": 0, "web": 0, "llm": []}

    def fake_rag(query):
        log["rag"] += 1
        return "RAG CONTEXT"

    async def fake_web(query):
        log["web"] += 1
        return "WEB CONTEXT"

    def fake_llm(query, context, has_kmi_data):
        log["llm"].append({"context": context, "has_kmi_data": has_kmi_data})
        return llm_result("DOUBTFUL")

    monkeypatch.setattr(sg, "_build_rag_context", fake_rag)
    monkeypatch.setattr(sg, "_get_web_context", fake_web)
    monkeypatch.setattr(sg, "_call_llm", fake_llm)
    monkeypatch.setattr(sg, "_graph", None)
    return log


def run(query):
    return asyncio.run(sg.run_screener(query))


def test_definite_entry_skips_web_search_and_returns_kmi_verdict(calls):
    out = run("Lucky Cement")
    assert out["verdict"] == "HALAL"
    assert calls["web"] == 0
    assert calls["rag"] == 1
    assert calls["llm"][0]["has_kmi_data"] is True
    assert "SHARIAHEASE SHARIAH COMPLIANCE DATABASE" in calls["llm"][0]["context"]


def test_unknown_query_runs_rag_and_web_then_trusts_llm(calls):
    out = run("Bitcoin")
    assert out["verdict"] == "DOUBTFUL"
    assert calls["rag"] == 1 and calls["web"] == 1
    assert calls["llm"][0]["has_kmi_data"] is False
    ctx = calls["llm"][0]["context"]
    assert "WEB CONTEXT" in ctx and "RAG CONTEXT" in ctx


def test_doubtful_entry_still_searches_the_web(calls):
    run("Hascol")
    assert calls["web"] == 1


def test_llm_failure_for_a_definite_entry_still_returns_the_kmi_verdict(calls, monkeypatch):
    monkeypatch.setattr(sg, "_call_llm", lambda q, c, h: {
        "verdict": "DOUBTFUL", "confidence": "LOW",
        "reason": "Analysis could not be completed due to a service error.",
        "recommendation": "Please try again.",
    })
    out = run("HBL")
    assert out["verdict"] == "HARAM"
    assert "service error" not in out["reason"]


def test_web_search_failure_does_not_break_screening(calls, monkeypatch):
    async def boom(query):
        raise RuntimeError("network down")
    monkeypatch.setattr(sg, "_get_web_context", boom)
    out = run("Bitcoin")
    assert out["verdict"] == "DOUBTFUL"
    assert "RAG CONTEXT" in calls["llm"][0]["context"]


def test_product_query_with_bank_name_is_not_given_the_banks_verdict(calls):
    out = run("MCB Islamic Fund")
    assert out["verdict"] == "DOUBTFUL"          # LLM result, no KMI override
    assert calls["llm"][0]["has_kmi_data"] is False
