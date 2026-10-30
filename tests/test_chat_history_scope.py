"""Chat history must never cross between users, or between guests and users (the guest-leak bug)."""
import pytest

from app.database import models
from app.database.crud import get_chat_history, save_message
from tests.conftest import signup


@pytest.fixture
def two_users(db_session):
    a = models.User(name="A", email="a@x.com", hashed_password="h")
    b = models.User(name="B", email="b@x.com", hashed_password="h")
    db_session.add_all([a, b])
    db_session.commit()
    return a, b


def test_guest_cannot_read_a_logged_in_users_messages(db_session, two_users):
    a, _ = two_users
    save_message(db_session, "shared-session", "user", "my zakat is 50,000", user_id=a.id)
    assert get_chat_history(db_session, "shared-session", user_id=None) == []


def test_user_cannot_read_another_users_messages(db_session, two_users):
    a, b = two_users
    save_message(db_session, "s", "user", "a text", user_id=a.id)
    assert get_chat_history(db_session, "s", user_id=b.id) == []
    assert [m.content for m in get_chat_history(db_session, "s", user_id=a.id)] == ["a text"]


def test_guest_messages_are_never_written(db_session):
    assert save_message(db_session, "s", "user", "guest text", user_id=None) is None
    assert db_session.query(models.ChatMessage).count() == 0


def test_old_guest_rows_are_never_returned(db_session):
    """Even if a guest row exists (written by an old version), a guest cannot read it."""
    db_session.add(models.ChatMessage(user_id=None, session_id="s", role="user", content="legacy guest row"))
    db_session.commit()
    assert get_chat_history(db_session, "s", user_id=None) == []


def test_history_endpoint_for_guests_is_empty(client):
    assert client.get("/chat/history/any-session").json() == []


def test_chat_message_as_guest_never_feeds_user_history_to_the_model(client, monkeypatch):
    """End to end: the history handed to the LLM for a guest must not contain a logged-in user's messages."""
    signup(client)
    seen = []

    async def fake_run_chat(message, history):
        seen.append(list(history))
        return {"answer": "ok", "sources": []}

    async def no_tts(*a, **k):
        return b""

    monkeypatch.setattr("app.routers.chatbot.run_chat", fake_run_chat)
    monkeypatch.setattr("app.routers.chatbot.text_to_speech_bytes", no_tts)

    r = client.post("/chat/message", json={"session_id": "S1", "message": "my savings are 900000 rupees"})
    assert r.status_code == 200
    client.cookies.clear()                                   # now the same session id as a guest
    r = client.post("/chat/message", json={"session_id": "S1", "message": "what did I say before?"})
    assert r.status_code == 200
    assert seen[-1] == []                                    # nothing from the logged-in user leaked in
    with client.session_factory() as db:
        assert db.query(models.ChatMessage).filter(models.ChatMessage.user_id.is_(None)).count() == 0
