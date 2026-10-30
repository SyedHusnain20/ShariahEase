"""Guests: nothing in the database; short in-memory context that dies on refresh."""
import pytest

from app.database import models
from app.services import guest_memory as gm
from app.services.guest_memory import GuestMemory
from tests.conftest import signup


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


# ── the memory class ──────────────────────────────────────────────────────────

def test_remembers_in_order_and_returns_copies():
    m = GuestMemory()
    m.add("k", "user", "hi"); m.add("k", "assistant", "hello")
    h = m.history("k")
    assert [x["content"] for x in h] == ["hi", "hello"]
    h[0]["content"] = "tampered"
    assert m.history("k")[0]["content"] == "hi"


def test_keys_are_isolated():
    m = GuestMemory()
    m.add("a:S1", "user", "secret")
    assert m.history("b:S1") == [] and m.history("a:S2") == []


def test_expires_after_ttl():
    clock = Clock(); m = GuestMemory(ttl_seconds=60, clock=clock)
    m.add("k", "user", "x")
    clock.t += 60; assert len(m.history("k")) == 1          # exactly at the limit still alive
    clock.t += 1;  assert m.history("k") == []              # past it: gone


def test_activity_extends_the_session():
    clock = Clock(); m = GuestMemory(ttl_seconds=60, clock=clock)
    m.add("k", "user", "x"); clock.t += 50
    m.add("k", "user", "y"); clock.t += 50
    assert len(m.history("k")) == 2


def test_keeps_only_the_last_turns():
    m = GuestMemory(max_turns=2)
    for i in range(5):
        m.add("k", "user", f"q{i}"); m.add("k", "assistant", f"a{i}")
    assert [x["content"] for x in m.history("k")] == ["q3", "a3", "q4", "a4"]


def test_evicts_the_least_recently_used_session():
    m = GuestMemory(max_sessions=2)
    m.add("a", "user", "1"); m.add("b", "user", "2"); m.add("a", "user", "again")
    m.add("c", "user", "3")                                  # b is the oldest-used
    assert m.history("b") == [] and m.history("a") and m.history("c")


def test_long_messages_are_truncated_and_clear_works():
    m = GuestMemory()
    m.add("k", "user", "x" * (gm.MAX_STORED_CHARS + 500))
    assert len(m.history("k")[0]["content"]) == gm.MAX_STORED_CHARS
    m.clear("k"); assert m.history("k") == []


# ── through the HTTP route ────────────────────────────────────────────────────

@pytest.fixture
def chat(client, monkeypatch):
    gm.guest_memory._data.clear()
    seen = []

    async def fake_run_chat(message, history):
        seen.append([m["content"] for m in history])
        return {"answer": f"answer to {message}", "sources": []}

    async def no_tts(*a, **k):
        return b""

    monkeypatch.setattr("app.routers.chatbot.run_chat", fake_run_chat)
    monkeypatch.setattr("app.routers.chatbot.text_to_speech_bytes", no_tts)

    def send(message, session="S1"):
        r = client.post("/chat/message", json={"session_id": session, "message": message})
        assert r.status_code == 200, r.text
        return r
    send.seen = seen
    return send


def test_guest_chat_writes_nothing_to_the_database(client, chat):
    chat("my savings are 900000")
    chat("and my gold?")
    with client.session_factory() as db:
        assert db.query(models.ChatMessage).count() == 0


def test_guest_follow_up_has_context_in_the_same_page_session(client, chat):
    chat("what is nisab?")
    chat("and for silver?")
    assert chat.seen[0] == [] and chat.seen[1] == ["what is nisab?", "answer to what is nisab?"]


def test_refresh_means_new_session_id_and_no_context(client, chat):
    chat("what is nisab?", session="session_1")
    chat("and for silver?", session="session_2")             # the page generates a new id on every load
    assert chat.seen[1] == []


def test_another_browser_guessing_the_session_id_sees_nothing(client, chat):
    chat("my secret question", session="session_1")
    client.cookies.clear()                                   # a different browser: new guest cookie
    chat("what did the other person ask?", session="session_1")
    assert chat.seen[1] == []


def test_guest_cookie_is_httponly(client, chat):
    r = chat("hello")
    header = r.headers.get("set-cookie", "")
    assert gm.COOKIE_NAME in header and "HttpOnly" in header


def test_clear_history_forgets_guest_context(client, chat):
    chat("one", session="S9")
    assert client.delete("/chat/history/S9").json() == {"cleared": True}
    chat("two", session="S9")
    assert chat.seen[1] == []


def test_logged_in_users_are_still_stored_in_the_database(client, chat):
    signup(client)
    chat("my question"); chat("follow up")
    with client.session_factory() as db:
        rows = db.query(models.ChatMessage).order_by(models.ChatMessage.id).all()
        assert [r.role for r in rows] == ["user", "assistant", "user", "assistant"] and all(r.user_id for r in rows)
    assert chat.seen[1] != []                                # their saved history reaches the model
