import os
import sys

import httpx
import pytest

# Make `import app...` work when pytest is run from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# llm_client builds a Groq client at import time; the tests never call it.
os.environ.setdefault("GROQ_API_KEY", "test-key-not-used")


@pytest.fixture(autouse=True)
def no_real_price_requests(monkeypatch):
    """
    No test may reach a real price API. Every price request fails instantly as
    if offline, and the quote cache starts empty. Tests that exercise the
    providers install their own transport (see tests/test_stock_price.py).
    """
    from app.services import stock_price

    def offline(request):
        raise httpx.ConnectError("offline (tests)", request=request)

    monkeypatch.setattr(
        stock_price, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(offline)),
    )
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    def reset():
        stock_price._cache.clear()
        stock_price._psx_token = None
        stock_price._psx_token_failed_at = None
        stock_price._psx_board = None

    reset()
    yield
    reset()
