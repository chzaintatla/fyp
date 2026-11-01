"""Tests for the Groq provider with a mocked SDK client (no network, no cost)."""
from __future__ import annotations

import json

import groq as groq_sdk
import httpx
import pytest

from app.ai.base import AIConfigError, AIError
from app.ai.groq_provider import SYSTEM_PROMPT, GroqProvider
from app.ai.planner import get_provider
from app.config import Settings
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry


# ------------------------------------------------------------------ fakes
class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeResponse:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, script):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(script)})()


def _sdk_error(cls, code, headers=None):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return cls(
        "test error",
        response=httpx.Response(code, request=req, headers=headers),
        body=None,
    )


def _provider(script, **kwargs) -> GroqProvider:
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("model", "openai/gpt-oss-120b")
    return GroqProvider(client=_FakeClient(script), **kwargs)


# ------------------------------------------------------------------ tests
def test_tool_calls_are_parsed():
    tc = _FakeToolCall("call_1", "list_folder", json.dumps({"path": "C:\\Temp"}))
    p = _provider([_FakeResponse(_FakeMessage(tool_calls=[tc]))])
    out = p.complete([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert out["tool_calls"][0]["name"] == "list_folder"
    assert out["tool_calls"][0]["arguments"] == {"path": "C:\\Temp"}
    assert out["tool_calls"][0]["id"] == "call_1"


def test_plain_text_answer_has_no_tool_calls():
    p = _provider([_FakeResponse(_FakeMessage(content="Which folder?"))])
    out = p.complete([{"role": "user", "content": "hi"}])
    assert out["tool_calls"] == []
    assert out["content"] == "Which folder?"


def test_auth_error_becomes_config_error():
    p = _provider([_sdk_error(groq_sdk.AuthenticationError, 401)])
    with pytest.raises(AIConfigError, match="API key"):
        p.complete([{"role": "user", "content": "hi"}])


def test_unknown_model_becomes_config_error():
    p = _provider([_sdk_error(groq_sdk.NotFoundError, 404)], model="nope-9000")
    with pytest.raises(AIConfigError, match="not available"):
        p.complete([{"role": "user", "content": "hi"}])


def test_rate_limit_retries_then_fails_loudly():
    script = [_sdk_error(groq_sdk.RateLimitError, 429)] * 3
    p = _provider(script, max_retries=2)
    with pytest.raises(AIError, match="rate limit"):
        p.complete([{"role": "user", "content": "hi"}])
    assert len(p._client.chat.completions.calls) == 3  # 1 + 2 retries, then stop


def test_rate_limit_honors_short_retry_after(monkeypatch):
    pauses = []
    monkeypatch.setattr("app.ai.groq_provider.time.sleep", pauses.append)
    limited = _sdk_error(groq_sdk.RateLimitError, 429, {"Retry-After": "0.25"})
    p = _provider([limited, _FakeResponse(_FakeMessage(content="done"))], max_retries=1)

    result = p.complete([{"role": "user", "content": "hi"}])

    assert result["content"] == "done"
    assert pauses == [0.25]
    assert len(p._client.chat.completions.calls) == 2


def test_rate_limit_does_not_retry_when_reset_is_long():
    limited = _sdk_error(groq_sdk.RateLimitError, 429, {"Retry-After": "60"})
    p = _provider([limited, limited], max_retries=1)

    with pytest.raises(AIError, match="retry in 60 seconds; no more requests were sent"):
        p.complete([{"role": "user", "content": "hi"}])

    assert len(p._client.chat.completions.calls) == 1


def test_invalid_tool_json_becomes_empty_arguments():
    tc = _FakeToolCall("call_1", "list_folder", "{not valid json")
    p = _provider([_FakeResponse(_FakeMessage(tool_calls=[tc]))])
    out = p.complete([{"role": "user", "content": "hi"}])
    assert out["tool_calls"][0]["arguments"] == {}


def test_null_assistant_content_and_missing_tool_result_are_repaired():
    p = _provider([_FakeResponse(_FakeMessage(content="Which file?"))])
    p.complete(
        [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "control_desktop", "arguments": "{}"}},
            ]},
            {"role": "user", "content": "Close that Chrome tab"},
        ]
    )
    sent = p._client.chat.completions.calls[0]["messages"]
    assert sent[0]["content"] == ""
    assert sent[1]["role"] == "tool"
    assert sent[1]["tool_call_id"] == "c1"
    assert sent[2]["role"] == "user"


def test_tool_specs_are_groq_safe(tmp_path):
    ctx = ToolContext(allowed_directories=[tmp_path], approved_applications=[], test_dir=tmp_path)
    specs = GroqProvider.build_tool_specs(ToolRegistry(ctx).metadata())
    desktop = next(s for s in specs if s["function"]["name"] == "control_desktop")
    schema = desktop["function"]["parameters"]
    dumped = json.dumps(schema)
    assert "$defs" not in dumped
    assert "$ref" not in dumped


def test_desktop_prompt_requires_exact_inspected_window():
    assert "use a title from that inspection exactly" in SYSTEM_PROMPT
    assert "do not substitute another window" in SYSTEM_PROMPT


def test_missing_key_never_falls_back_to_mock():
    with pytest.raises(AIConfigError, match="GROQ_API_KEY"):
        get_provider(Settings(ai_provider="groq", groq_api_key=None))
    with pytest.raises(AIConfigError, match="GROQ_API_KEY"):
        GroqProvider(api_key="", model="x")


def test_unknown_provider_name_rejected():
    with pytest.raises(AIConfigError, match="Unknown AI_PROVIDER"):
        get_provider(Settings(ai_provider="watson"))


def test_tool_specs_cover_registry(tmp_path):
    ctx = ToolContext(allowed_directories=[tmp_path], approved_applications=[], test_dir=tmp_path)
    specs = GroqProvider.build_tool_specs(ToolRegistry(ctx).metadata())
    names = {s["function"]["name"] for s in specs}
    assert names == {
        "application_package", "find_paths", "control_browser",
        "compose_email", "compress_folder", "control_desktop", "copy_file",
        "create_folder", "delete_file",
        "download_web_content", "extract_file", "list_folder", "list_trash",
        "manage_system_setting", "move_file", "open_application", "open_file", "open_folder",
        "open_url", "open_windows_settings", "play_youtube_video", "read_text_file", "rename_file", "restore_file",
        "take_screenshot", "web_search", "write_text_file",
        "find_files", "edit_text_file", "media_control", "whatsapp_open_chat", "winget_install",
        "memory_remember", "memory_recall", "memory_forget", "web_answer", "summarise_page", "summarise_file",
        "ui_inspect", "ui_act", "open_terminal", "run_terminal_command",
    }
    for s in specs:
        assert s["type"] == "function"
        assert s["function"]["description"]
        assert isinstance(s["function"]["parameters"], dict)
