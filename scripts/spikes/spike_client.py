"""
Step 4a spikes: the Groq caller.

Plain HTTPS (httpx, already pinned in requirements.txt), so the spikes do NOT need the groq package upgraded.
It never reads or prints the API key beyond sending it in the Authorization header, spends from a token budget,
slows itself down to stay inside the free limits, and stores every answer so an interrupted run can resume.
"""
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_BUDGET_TOKENS = 120_000        # of the 200,000 a day on the free plan (the live chatbot shares them)
DEFAULT_ESTIMATE_TOKENS = 3_000        # what one call is assumed to cost before its real usage is known
FREE_RPM = 30


class BudgetExceeded(RuntimeError):
    pass


class MissingKey(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------------
# Usage
# --------------------------------------------------------------------------------------------------
@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    total: int = 0
    cached: int = 0
    reasoning: Optional[int] = None        # None = the API did not report it
    reasoning_chars: int = 0               # length of the returned reasoning text, when it is returned


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def extract_usage(body: dict) -> Usage:
    usage = (body or {}).get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    reasoning = completion_details.get("reasoning_tokens")
    message = (((body or {}).get("choices") or [{}])[0] or {}).get("message") or {}
    text = message.get("reasoning") or message.get("reasoning_content") or ""
    prompt, completion = _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))
    return Usage(
        prompt=prompt,
        completion=completion,
        total=_int(usage.get("total_tokens")) or prompt + completion,
        cached=_int(prompt_details.get("cached_tokens")),
        reasoning=reasoning if isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning >= 0 else None,
        reasoning_chars=len(text) if isinstance(text, str) else 0,
    )


# --------------------------------------------------------------------------------------------------
# Budget and pacing
# --------------------------------------------------------------------------------------------------
class Budget:
    """Tokens this run may spend. Checked BEFORE a call with an estimate, corrected AFTER with the real usage."""

    def __init__(self, limit: int = DEFAULT_BUDGET_TOKENS):
        self.limit = limit
        self.used = 0

    def check(self, estimate: int = DEFAULT_ESTIMATE_TOKENS) -> None:
        if self.used + estimate > self.limit:
            raise BudgetExceeded(f"token budget reached: used {self.used}, next call about {estimate}, limit {self.limit}")

    def spend(self, tokens: int) -> None:
        self.used += max(0, int(tokens))

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)


_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def parse_duration(text) -> float:
    """Groq reset headers look like '7.66s', '1m30.5s', '2m59.56s', '120ms'. Unknown text gives 0."""
    if not isinstance(text, str):
        return 0.0
    try:                                           # the retry-after header is plain seconds: "12"
        return max(0.0, float(text.strip()))
    except ValueError:
        pass
    match = _DURATION.match(text.strip())
    if not match or not text.strip():
        return 0.0
    h, m, s, ms = (float(g) if g else 0.0 for g in match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def wait_seconds(headers: dict, need_tokens: int) -> float:
    """How long to sleep before a call that needs about need_tokens, from the last response's headers."""
    try:
        remaining = int(headers.get("x-ratelimit-remaining-tokens", ""))
    except (TypeError, ValueError):
        return 0.0
    if remaining >= need_tokens:
        return 0.0
    return min(90.0, parse_duration(headers.get("x-ratelimit-reset-tokens")) + 0.5)


class Store:
    """One JSON line per finished call, so an interrupted or rate-limited run continues where it stopped."""

    def __init__(self, path):
        self.path = Path(path)
        self.records = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        rec = json.loads(line)
                        self.records[rec["key"]] = rec
                    except (ValueError, KeyError):
                        continue

    def get(self, key):
        return self.records.get(key)

    def put(self, rec: dict) -> None:
        self.records[rec["key"]] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------------------------------
def redact(text: str, key: str) -> str:
    return text.replace(key, "[key]") if key and isinstance(text, str) else text


def load_key() -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parents[2] / ".env")
            key = os.getenv("GROQ_API_KEY", "").strip()
        except ImportError:
            pass
    if not key:
        raise MissingKey("GROQ_API_KEY is not set (put it in .env or run: $env:GROQ_API_KEY = '...')")
    return key


@dataclass
class Result:
    key: str
    ok: bool
    status: int
    content: Optional[str]
    finish_reason: Optional[str]
    usage: Usage
    latency_s: float
    error: Optional[str] = None
    from_store: bool = False
    headers: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        return {
            "key": self.key, "ok": self.ok, "status": self.status, "content": self.content,
            "finish_reason": self.finish_reason, "usage": self.usage.__dict__, "latency_s": self.latency_s,
            "error": self.error, "headers": self.headers,
        }

    @classmethod
    def from_record(cls, rec: dict) -> "Result":
        return cls(rec["key"], rec["ok"], rec["status"], rec.get("content"), rec.get("finish_reason"),
                   Usage(**rec["usage"]), rec.get("latency_s", 0.0), rec.get("error"), True, rec.get("headers", {}))


_KEPT_HEADERS = ("x-ratelimit-remaining-tokens", "x-ratelimit-remaining-requests", "x-ratelimit-reset-tokens",
                 "x-ratelimit-reset-requests", "x-ratelimit-limit-tokens", "x-ratelimit-limit-requests")


class GroqCaller:
    def __init__(self, api_key: str, budget: Budget, store: Store, client: Optional[httpx.Client] = None,
                 sleep=None, clock=None, log=print):
        self._key = api_key
        self.budget, self.store, self.log = budget, store, log
        self.sleep = sleep or (lambda seconds: time.sleep(seconds))      # looked up at call time, so tests can replace time.sleep
        self.clock = clock or (lambda: time.monotonic())
        self.http = client or httpx.Client(timeout=90.0)
        self.last_headers: dict = {}
        self.last_call_at = 0.0
        self.calls_made = 0

    def call(self, key: str, model: str, messages: list, *, response_format=None, reasoning_effort="low",
             max_completion_tokens=1500, include_reasoning=None, estimate=DEFAULT_ESTIMATE_TOKENS) -> Result:
        body = {"model": model, "messages": messages, "temperature": 0, "max_completion_tokens": max_completion_tokens,
                "reasoning_effort": reasoning_effort}
        if include_reasoning is not None:                 # the API default (reasoning returned) is left alone unless asked
            body["include_reasoning"] = include_reasoning
        if response_format is not None:
            body["response_format"] = response_format

        # A saved answer is reused only for the IDENTICAL request: change the prompt, the schema or any setting and the
        # digest changes, so an answer to an older version of the request is never mistaken for this one.
        digest = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
        key = f"{key}|{digest}"
        saved = self.store.get(key)
        if saved is not None:
            return Result.from_record(saved)
        self.budget.check(estimate)

        # stay inside the free limits: at most FREE_RPM calls a minute, and wait for token room
        gap = 60.0 / FREE_RPM - (self.clock() - self.last_call_at)
        pause = max(gap, wait_seconds(self.last_headers, estimate), 0.0)
        if pause > 0:
            self.sleep(pause)

        result = None
        for attempt in range(4):
            started = self.clock()
            try:
                resp = self.http.post(API_URL, json=body, headers={"Authorization": f"Bearer {self._key}"})
            except httpx.HTTPError as exc:
                result = Result(key, False, 0, None, None, Usage(), self.clock() - started,
                                f"network error: {type(exc).__name__}")
                self.sleep(2.0 * (attempt + 1))
                continue
            self.last_call_at = self.clock()
            headers = {k: v for k, v in resp.headers.items() if k.lower() in _KEPT_HEADERS}
            self.last_headers = {k.lower(): v for k, v in headers.items()}
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = parse_duration(resp.headers.get("retry-after", "")) or 5.0 * (attempt + 1)
                result = Result(key, False, resp.status_code, None, None, Usage(), self.last_call_at - started,
                                redact(self._error_text(data, resp), self._key), headers=self.last_headers)
                self.log(f"   waiting {wait:.0f}s (HTTP {resp.status_code})")
                self.sleep(min(wait, 120.0))
                continue
            usage = extract_usage(data)
            choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
            content = (choice.get("message") or {}).get("content")
            ok = resp.status_code == 200 and content is not None
            result = Result(key, ok, resp.status_code, content, choice.get("finish_reason"), usage,
                            self.last_call_at - started,
                            None if ok else redact(self._error_text(data, resp), self._key), headers=self.last_headers)
            break

        self.calls_made += 1
        self.budget.spend(result.usage.total - result.usage.cached if result.usage.total else estimate // 4)
        if result.status in (200, 400, 401, 403, 404, 413, 422):     # final answers are kept; 429/5xx/network are retried next run
            self.store.put(result.to_record())
        return result

    @staticmethod
    def _error_text(data, resp) -> str:
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            text = str(err.get("message", ""))
            if err.get("failed_generation"):
                text += " | failed_generation: " + str(err["failed_generation"])[:200]
            return text[:600] or f"HTTP {resp.status_code}"
        return f"HTTP {resp.status_code}"
