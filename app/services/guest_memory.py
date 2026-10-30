"""
Short-lived chat memory for GUESTS. Held in server memory only, never in the database.

A guest's messages are not stored. To keep follow-up questions working ("and for silver?"),
the last few turns are remembered in RAM for a short time, keyed by a random per-browser cookie
AND the page's session id, so:
  * refreshing the page (new session id) starts a fresh conversation,
  * another browser that guesses the session id sees nothing (different cookie),
  * a server restart forgets everything.
"""
import secrets
import threading
import time
from collections import OrderedDict

COOKIE_NAME = "guest_id"
MAX_STORED_CHARS = 4000


class GuestMemory:
    def __init__(self, ttl_seconds: int = 1800, max_turns: int = 10, max_sessions: int = 500, clock=time.monotonic):
        self.ttl, self.max_messages, self.max_sessions, self._clock = ttl_seconds, max_turns * 2, max_sessions, clock
        self._data: "OrderedDict[str, tuple[float, list[dict]]]" = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for key in [k for k, (seen, _) in self._data.items() if now - seen > self.ttl]:
            del self._data[key]

    def history(self, key: str) -> list[dict]:
        with self._lock:
            now = self._clock()
            self._purge(now)
            entry = self._data.get(key)
            return [dict(m) for m in entry[1]] if entry else []

    def add(self, key: str, role: str, content: str) -> None:
        with self._lock:
            now = self._clock()
            self._purge(now)
            messages = self._data.pop(key, (now, []))[1]
            messages.append({"role": role, "content": content[:MAX_STORED_CHARS]})
            self._data[key] = (now, messages[-self.max_messages:])
            while len(self._data) > self.max_sessions:
                self._data.popitem(last=False)          # drop the least recently used session

    def clear(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


guest_memory = GuestMemory()


def new_guest_id() -> str:
    return secrets.token_urlsafe(16)


def memory_key(guest_id: str, session_id: str) -> str:
    return f"{guest_id}:{session_id}"
