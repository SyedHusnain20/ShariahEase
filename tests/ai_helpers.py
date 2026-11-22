"""Shared helpers for the step 4b tests: a fake AI provider that speaks the Groq chat format (no network)."""
import json
from dataclasses import dataclass, field

import httpx

EMPTY_QUERY = {"period_text": "", "category_slug": "none", "group_by": "none"}
EMPTY_EDIT = {"action": "none", "target_text": "", "new_amount_text": "", "new_category_slug": "none"}


def item(amount_text, category="food.tea_snacks", note="", date_text="", payment="unknown"):
    return {"amount_text": amount_text, "category_slug": category, "note": note, "date_text": date_text, "payment_method": payment}


def form(intent="expense_log", items=(), language="en", query=None, edit=None):
    return {"intent": intent, "language": language, "items": list(items), "query": query or EMPTY_QUERY, "edit": edit or EMPTY_EDIT}


def groq_body(content, total=900, cached=0):
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {"choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": total - 100, "completion_tokens": 100, "total_tokens": total,
                      "prompt_tokens_details": {"cached_tokens": cached}}}


@dataclass
class FakeProvider:
    """Install with install(monkeypatch). `replies` is a list of (status, body) used in order; the last one repeats."""
    replies: list = field(default_factory=list)
    requests: list = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"url": str(request.url), "headers": dict(request.headers), "json": json.loads(request.content)})
        status, body = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        return httpx.Response(status, json=body)

    def install(self, monkeypatch):
        from app.services import expense_understand
        monkeypatch.setattr(expense_understand, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))
        monkeypatch.setenv("GROQ_API_KEY", "test-key-not-used")
        return self


def says(provider_form, **kw):
    """A provider that answers every call with this form."""
    return FakeProvider([(200, groq_body(provider_form, **kw))])
