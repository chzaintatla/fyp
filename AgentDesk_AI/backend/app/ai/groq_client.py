"""One Groq client for chat, speech-to-text and text-to-speech.

Every Groq call in the app should go through `GroqClient` so that timeouts,
rate-limit handling, usage counting and logging behave the same everywhere.

- Timeouts: each call passes an explicit per-request timeout to the SDK.
- Retries: HTTP 429, 5xx and connection errors are retried with exponential
  backoff (honouring Retry-After), at most `max_retries` times (default 2).
  A 429 whose Retry-After exceeds `max_backoff_seconds` is not waited out:
  it raises AIRateLimitError at once so the task can pause honestly.
  Timeouts are not retried: they already cost the full budget.
- Usage: a thread-safe counter per kind (chat/stt/tts): calls, failures,
  retries, rate limits, tokens, audio seconds and TTS characters.
- Logging: one structured event per attempt with kind, model, status,
  HTTP status and duration. Prompts, transcripts, audio and spoken text are
  never logged; error text passes through `redact`.

The API key is held privately and never logged, returned or put in errors.
"""
from __future__ import annotations

import io
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Callable

from ..logging_setup import log_event, redact
from .base import AIConfigError, AIError, AIRateLimitError

DEFAULT_CHAT_MODEL = "openai/gpt-oss-120b"
DEFAULT_STT_MODELS = ("whisper-large-v3-turbo", "whisper-large-v3")
DEFAULT_TTS_MODEL = "canopylabs/orpheus-v1-english"
DEFAULT_TTS_VOICE = "autumn"

KINDS = ("chat", "stt", "tts")


@dataclass
class KindUsage:
    calls: int = 0
    succeeded: int = 0
    failed: int = 0
    retries: int = 0
    rate_limited: int = 0
    key_switches: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    audio_seconds: float = 0.0
    tts_characters: int = 0
    total_ms: float = 0.0


@dataclass
class UsageCounter:
    """Thread-safe in-memory usage totals. Holds counts only, never content."""

    _by_kind: dict[str, KindUsage] = field(default_factory=lambda: {k: KindUsage() for k in KINDS})
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, kind: str, **deltas: float) -> None:
        with self._lock:
            usage = self._by_kind[kind]
            for name, value in deltas.items():
                setattr(usage, name, getattr(usage, name) + value)

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {kind: dict(vars(usage)) for kind, usage in self._by_kind.items()}

    def reset(self) -> None:
        with self._lock:
            self._by_kind = {k: KindUsage() for k in KINDS}


class _Retryable(Exception):
    def __init__(self, cause: Exception, delay: float | None, rate_limited: bool):
        super().__init__(str(cause))
        self.cause = cause
        self.delay = delay
        self.rate_limited = rate_limited


def _retry_after_seconds(exc: Exception) -> float | None:
    """Read Retry-After (seconds or HTTP date) from an SDK status error, if any."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw = None
    for name in ("retry-after", "Retry-After", "x-ratelimit-reset-requests"):
        try:
            raw = headers.get(name)
        except AttributeError:
            raw = None
        if raw:
            break
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        return max(0.0, float(raw.rstrip("s")))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _status_code(exc: Exception) -> int | None:
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


KEEPALIVE_SECONDS = 90   # httpx closes an idle connection after 5 s by default; a new TLS handshake costs 150-400 ms


def _keepalive_http_client(timeout: float):
    """The SDK's own client with a long keep-alive, so the next command reuses the open HTTPS connection."""
    from groq import DefaultHttpxClient
    import httpx
    return DefaultHttpxClient(limits=httpx.Limits(max_connections=20, max_keepalive_connections=8, keepalive_expiry=KEEPALIVE_SECONDS),
                              timeout=httpx.Timeout(timeout, connect=10.0))


class GroqClient:
    """Shared Groq access for chat completions, transcription and speech."""

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 2,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 15.0,
        chat_model: str = DEFAULT_CHAT_MODEL,
        stt_models: tuple[str, ...] | list[str] = DEFAULT_STT_MODELS,
        tts_model: str = DEFAULT_TTS_MODEL,
        tts_voice: str = DEFAULT_TTS_VOICE,
        client: Any | None = None,
        key_pool: Any | None = None,
        client_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        usage: UsageCounter | None = None,
    ) -> None:
        if not api_key and client is None and not key_pool:
            raise AIConfigError(
                "GROQ_API_KEY is not set. Add it to backend/.env (see .env.example); "
                "get a key at https://console.groq.com/keys."
            )
        if max_retries < 0 or max_retries > 2:
            raise ValueError("max_retries must be between 0 and 2")
        self._api_key = api_key
        self._pool = key_pool
        self._factory = client_factory
        self._sdk_key: str | None = None
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.chat_model = chat_model
        self.stt_models = tuple(stt_models)
        self.tts_model = tts_model
        self.tts_voice = tts_voice
        self._client = client
        self.last_used = 0.0   # time.monotonic() of the last request; the warm-up loop stops pinging when idle
        self._sleep = sleep
        self.usage = usage or UsageCounter()

    def __repr__(self) -> str:  # never show the key
        return f"GroqClient(chat_model={self.chat_model!r}, timeout={self.timeout_seconds:g}s)"

    @classmethod
    def from_settings(cls, settings=None, **overrides) -> "GroqClient":
        if settings is None:
            from ..config import get_settings

            settings = get_settings()
        kwargs = dict(
            timeout_seconds=settings.ai_timeout_seconds,
            max_retries=min(2, max(0, settings.ai_max_retries)),
            chat_model=settings.ai_model,
            stt_models=tuple(m for m in (settings.groq_stt_model, settings.groq_stt_fallback_model) if m),
            tts_model=settings.groq_tts_model,
            tts_voice=settings.groq_tts_voice,
        )
        kwargs.update(overrides)
        from .key_pool import parse_keys, pool_for

        keys = parse_keys(settings.groq_api_key, getattr(settings, "groq_api_keys", ""))
        if len(keys) > 1:
            kwargs.setdefault("key_pool", pool_for(keys))
        return cls(keys[0] if keys else None, **kwargs)

    # ------------------------------------------------------------ plumbing
    def _sdk(self):
        if self._pool is not None and self._client is not None and self._sdk_key is not None:
            current = self._pool.current()
            if current != self._sdk_key:
                self._client = None  # the pool moved to another key
        if self._client is None:
            try:
                from groq import Groq
            except ImportError as exc:
                raise AIConfigError("The 'groq' package is not installed. Run: pip install groq") from exc
            # SDK retries are disabled: this class owns the retry policy.
            key = (self._pool.current() if self._pool is not None else None) or self._api_key
            self._sdk_key = key if self._pool is not None else None
            self._client = self._factory(key) if self._factory else Groq(
                api_key=key, timeout=self.timeout_seconds, max_retries=0, http_client=_keepalive_http_client(self.timeout_seconds))
        return self._client

    def warm(self) -> float | None:
        """Open (or refresh) the HTTPS connection with a request that costs no tokens. Returns its time in ms, None if it failed."""
        started = time.perf_counter()
        for _ in range(3):   # a key Groq rejects is dropped from the pool (as for a real request) and the next key is tried
            try:
                self._sdk().models.list()
                return (time.perf_counter() - started) * 1000
            except Exception as exc:  # noqa: BLE001 - warming is a courtesy; a real request reports real errors
                if not (_status_code(exc) in (401, 403) and self._rotate("chat", None, rejected=True)):
                    return None
        return None

    def _rotate(self, kind: str, delay: float | None, rejected: bool = False) -> bool:
        """Switch to the next key after a limit (or rejection). True when another key can be used right now."""
        if self._pool is None or self._sdk_key is None:
            return False
        key = self._sdk_key
        usable = self._pool.drop(key) if rejected else self._pool.penalize(key, delay)
        if usable:
            self._client = None
            self.usage.add(kind, key_switches=1)
            log_event("groq_key_switch", level=logging.WARNING, tool=f"groq.{kind}",
                      status="rejected" if rejected else "rate_limited", count=self._pool.available())
        return usable

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        delay = self.base_backoff_seconds * (2 ** attempt)
        return delay + random.uniform(0, delay * 0.1)

    def _classify(self, exc: Exception, kind: str, model: str) -> Exception:
        """Map an SDK exception to a final AI error or a _Retryable marker."""
        try:
            import groq
        except ImportError:  # injected test client without the SDK
            groq = None  # type: ignore[assignment]
        status = _status_code(exc)
        name = type(exc).__name__
        if (groq is not None and isinstance(exc, groq.APITimeoutError)) or name == "APITimeoutError":
            return AIError(f"Groq {kind} request timed out after {self.timeout_seconds:g} s.")
        if status == 429 or name == "RateLimitError":
            return _Retryable(exc, _retry_after_seconds(exc), rate_limited=True)
        if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
            return AIConfigError("Groq rejected the API key or access. Check GROQ_API_KEY in backend/.env.")
        if status == 404 or name == "NotFoundError":
            return AIConfigError(f"Groq model '{model}' is not available. See https://console.groq.com/docs/models.")
        if (status is not None and status >= 500) or name in ("APIConnectionError", "InternalServerError"):
            return _Retryable(exc, None, rate_limited=False)
        return AIError(f"Groq {kind} request failed: {redact(str(exc), 200)}")

    def _call(self, kind: str, model: str, fn: Callable[[], Any]) -> Any:
        last: _Retryable | None = None
        attempt = -1
        switches = 0
        while True:
            attempt += 1
            if attempt > self.max_retries:
                break
            started = time.perf_counter()
            self.last_used = time.monotonic()
            self.usage.add(kind, calls=1)
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - classified below
                elapsed = (time.perf_counter() - started) * 1000
                self.usage.add(kind, total_ms=elapsed)
                mapped = self._classify(exc, kind, model)
                log_event(
                    "groq_call", level=logging.WARNING, tool=f"groq.{kind}", detail=model,
                    status="retryable" if isinstance(mapped, _Retryable) else "error",
                    http_status=_status_code(exc), duration_ms=round(elapsed), count=attempt + 1,
                )
                limit = len(self._pool) if self._pool is not None else 0
                if isinstance(mapped, AIConfigError) and _status_code(exc) in (401, 403) and switches < limit:
                    if self._rotate(kind, None, rejected=True):
                        switches += 1
                        attempt -= 1
                        continue
                if not isinstance(mapped, _Retryable):
                    self.usage.add(kind, failed=1)
                    raise mapped from exc
                last = mapped
                if mapped.rate_limited:
                    self.usage.add(kind, rate_limited=1)
                    if switches < limit and self._rotate(kind, mapped.delay):
                        switches += 1
                        attempt -= 1  # a fresh key: no backoff, and this does not use up a retry
                        continue
                delay = self._backoff(attempt, mapped.delay)
                if attempt >= self.max_retries or delay > self.max_backoff_seconds:
                    break
                self.usage.add(kind, retries=1)
                self._sleep(delay)
                continue
            elapsed = (time.perf_counter() - started) * 1000
            self.usage.add(kind, succeeded=1, total_ms=elapsed)
            log_event("groq_call", tool=f"groq.{kind}", detail=model, status="ok",
                      duration_ms=round(elapsed), count=attempt + 1)
            return result
        assert last is not None
        self.usage.add(kind, failed=1)
        if last.rate_limited:
            retry_after = last.delay if last.delay is not None else self._backoff(self.max_retries, None)
            raise AIRateLimitError(
                f"Groq rate limit reached; retry in about {retry_after:.0f} s. "
                "Limits: https://console.groq.com/settings/limits.",
                retry_after,
            ) from last.cause
        raise AIError(f"Groq {kind} service unavailable after retries: {redact(str(last.cause), 200)}") from last.cause

    # ------------------------------------------------------------ chat
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0,
        max_tokens: int = 1200,
        **extra: Any,
    ) -> dict[str, Any]:
        """One chat completion. Returns {'content', 'model', 'usage', 'finish_reason'}."""
        model = model or self.chat_model
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature,
                                  "max_tokens": max_tokens, "timeout": self.timeout_seconds, **extra}
        if response_format is not None:
            kwargs["response_format"] = response_format
        response = self._call("chat", model, lambda: self._sdk().chat.completions.create(**kwargs))
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        self.usage.add("chat", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return {
            "content": getattr(choice.message, "content", None),
            "model": getattr(response, "model", model),
            "finish_reason": getattr(choice, "finish_reason", None),
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }

    # ------------------------------------------------------------ speech-to-text
    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "speech.wav",
        language: str | None = None,
        prompt: str | None = None,
        models: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Transcribe audio. Tries each STT model in order (turbo, then large-v3).

        Configuration errors (bad key) stop immediately; a failure of one model
        (unavailable, rate-limited, server error) falls through to the next.
        Returns {'text', 'language', 'duration', 'model'}.
        """
        if not audio:
            raise AIError("No audio to transcribe.")
        last_error: Exception | None = None
        for model in models or self.stt_models:
            kwargs: dict[str, Any] = {"model": model, "file": (filename, io.BytesIO(audio)),
                                      "response_format": "verbose_json", "temperature": 0,
                                      "timeout": self.timeout_seconds}
            if language:
                kwargs["language"] = language
            if prompt:
                kwargs["prompt"] = prompt[:800]
            try:
                result = self._call("stt", model, lambda kw=kwargs: self._sdk().audio.transcriptions.create(**kw))
            except AIConfigError as exc:
                if "API key" in str(exc):
                    raise
                last_error = exc
                continue
            except AIError as exc:
                last_error = exc
                continue
            data = result.model_dump() if hasattr(result, "model_dump") else dict(vars(result)) if hasattr(result, "__dict__") else {}
            text = (getattr(result, "text", None) or data.get("text") or "").strip()
            duration = float(data.get("duration") or getattr(result, "duration", 0) or 0)
            self.usage.add("stt", audio_seconds=duration)
            return {"text": text, "language": data.get("language") or language,
                    "duration": duration, "model": model}
        raise last_error or AIError("No speech-to-text model is configured.")

    # ------------------------------------------------------------ text-to-speech
    def speak(
        self,
        text: str,
        *,
        voice: str | None = None,
        model: str | None = None,
        response_format: str = "wav",
    ) -> bytes:
        """Synthesize speech. Returns audio bytes (WAV by default)."""
        text = (text or "").strip()
        if not text:
            raise AIError("No text to speak.")
        model = model or self.tts_model
        kwargs = {"model": model, "voice": voice or self.tts_voice, "input": text,
                  "response_format": response_format, "timeout": self.timeout_seconds}
        response = self._call("tts", model, lambda: self._sdk().audio.speech.create(**kwargs))
        if isinstance(response, (bytes, bytearray)):
            audio = bytes(response)
        elif hasattr(response, "read"):
            audio = response.read()
        else:
            audio = getattr(response, "content", b"")
        if not audio:
            raise AIError("Groq returned no audio.")
        self.usage.add("tts", tts_characters=len(text))
        return audio


_shared: GroqClient | None = None
_shared_lock = threading.Lock()


def get_groq_client() -> GroqClient:
    """Process-wide client built from settings (one usage counter for the app)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = GroqClient.from_settings()
        return _shared
