"""Several Groq keys: a key that hits its limit hands over to the next one automatically."""
from __future__ import annotations

from app.ai.base import AIRateLimitError
from app.ai.groq_client import GroqClient
from app.ai.key_pool import KeyPool, parse_keys
from app.config import Settings
from tests.test_groq_client import Endpoint, FakeStatusError, RateLimitError, chat_response, fake_sdk

import pytest


def test_parse_keys_merges_dedupes_and_splits():
    assert parse_keys("a", "b, c;d\na") == ["b", "c", "d", "a"]
    assert parse_keys(None, "") == []


def test_pool_cooldown_and_recovery():
    now = [0.0]
    pool = KeyPool(["a", "b"], clock=lambda: now[0])
    assert pool.current() == "a"
    assert pool.penalize("a", 30) is True
    assert pool.current() == "b"
    assert pool.penalize("b", 10) is False          # nothing usable right now
    assert pool.current() == "b"                    # frees up first
    now[0] = 11
    assert pool.current() == "b"
    now[0] = 31
    assert pool.current() == "a"
    pool.drop("a")
    assert pool.current() == "b" and "gsk" not in repr(pool)


def make(keys, sdks):
    pool = KeyPool(keys)
    used: list[str] = []

    def factory(key):
        used.append(key)
        return sdks[key]

    return GroqClient(keys[0], key_pool=pool, client_factory=factory, sleep=lambda s: None), used, pool


def test_rate_limit_switches_key_without_waiting():
    sdks = {"k1": fake_sdk(chat=Endpoint(RateLimitError({"retry-after": "3600"}))),
            "k2": fake_sdk(chat=Endpoint(chat_response("hello")))}
    client, used, _ = make(["k1", "k2"], sdks)
    assert client.chat([{"role": "user", "content": "hi"}])["content"] == "hello"
    assert used == ["k1", "k2"]
    assert client.usage.snapshot()["chat"]["key_switches"] == 1


def test_skips_cooling_keys_on_next_call():
    sdks = {"k1": fake_sdk(chat=Endpoint(RateLimitError())),
            "k2": fake_sdk(chat=Endpoint(chat_response("a"), chat_response("b")))}
    client, used, _ = make(["k1", "k2"], sdks)
    client.chat([{"role": "user", "content": "1"}])
    client.chat([{"role": "user", "content": "2"}])
    assert used == ["k1", "k2"]  # k1 was not tried again


def test_rejected_key_is_dropped():
    sdks = {"k1": fake_sdk(chat=Endpoint(FakeStatusError(401))),
            "k2": fake_sdk(chat=Endpoint(chat_response("ok")))}
    client, used, pool = make(["k1", "k2"], sdks)
    assert client.chat([{"role": "user", "content": "x"}])["content"] == "ok"
    assert pool.current() == "k2"


def test_all_keys_limited_raises_rate_limit_error():
    sdks = {k: fake_sdk(chat=Endpoint(*[RateLimitError({"retry-after": "3600"})] * 5)) for k in ("k1", "k2")}
    client, _, _ = make(["k1", "k2"], sdks)
    with pytest.raises(AIRateLimitError):
        client.chat([{"role": "user", "content": "x"}])


def test_settings_resolve_keys_from_both_fields():
    s = Settings(groq_api_key="one", groq_api_keys="two,three")
    assert s.resolved_groq_keys() == ["two", "three", "one"]
    assert s.groq_configured and not Settings().groq_configured
