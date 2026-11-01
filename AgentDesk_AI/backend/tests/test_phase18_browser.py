"""Extension version handshake, self-reload of outdated extensions, open_url fallback for old ones."""
from __future__ import annotations

import pytest

from app.browser_bridge import bridge, expected_version
from app.tools import apps
from app.tools.apps import OpenUrlTool, _same_url


def _connect(client, hello):
    socket = client.websocket_connect("/browser/ws", headers={"Origin": "chrome-extension://x"})
    ws = socket.__enter__()
    ws.send_json({"token": bridge.token, **hello})
    assert ws.receive_json() == {"ready": True}
    return socket, ws


def test_expected_version_comes_from_the_shipped_manifest():
    assert expected_version() == "1.2.0"


def test_current_extension_is_not_asked_to_reload(client):
    socket, ws = _connect(client, {"version": "1.2.0", "capabilities": ["inspect", "open", "reload"]})
    try:
        status = client.get("/browser/status").json()
        assert status["connected"] and status["version"] == "1.2.0" and status["outdated"] is False
        assert bridge.supports("open")
    finally:
        socket.__exit__(None, None, None)


def test_outdated_extension_with_reload_support_is_asked_to_reload(client, monkeypatch):
    monkeypatch.setattr("app.browser_bridge.expected_version", lambda: "1.2.0")
    socket, ws = _connect(client, {"version": "1.1.0", "capabilities": ["inspect", "open", "reload"]})   # one version behind the shipped files
    try:
        message = ws.receive_json()
        assert message["action"] == "reload" and message["id"]
        assert client.get("/browser/status").json()["outdated"] is True
    finally:
        socket.__exit__(None, None, None)


def test_legacy_extension_has_no_open_capability(client):
    socket, ws = _connect(client, {})  # 1.0.0 sends only the token
    try:
        info = bridge.client_info()
        assert info["version"] == "1.0.0" and "open" not in info["capabilities"]
        assert not bridge.supports("open")
    finally:
        socket.__exit__(None, None, None)


@pytest.mark.parametrize("a,b,same", [("https://mail.google.com", "https://mail.google.com/", True),
                                      ("https://WWW.youtube.com/watch?v=1", "https://www.youtube.com/watch?v=1", True),
                                      ("https://mail.google.com", "https://mail.google.com.evil.io/", False)])
def test_url_comparison_matches_browser_normalisation(a, b, same):
    assert _same_url(a, b) is same


def test_open_in_chrome_with_old_extension_launches_and_confirms_via_inspect(ctx, monkeypatch):
    launched = []
    monkeypatch.setattr(bridge, "clients", {"c": None})
    monkeypatch.setattr(bridge, "info", {"c": {"version": "1.0.0", "capabilities": ["inspect", "close", "pause", "resume"]}})
    calls = []

    def request(action, **kw):
        calls.append(action)
        assert action == "inspect", "an old extension must never be sent 'open'"
        tabs = [{"id": 7, "url": "https://mail.google.com/"}] if launched else []
        return {"success": True, "tabs": tabs}
    monkeypatch.setattr(bridge, "request", request)
    monkeypatch.setattr(apps.OpenApplicationTool, "_find_executable", lambda self, name: "chrome.exe")
    monkeypatch.setattr(apps._platform, "launch_program", lambda exe, args=None: launched.append((exe, args)))
    tool = OpenUrlTool(ctx)
    result = tool.execute(tool.validate_args({"url": "https://mail.google.com", "browser": "chrome"}))
    assert result.success and result.output["tab_id"] == 7 and "inspect" in result.output["via"]
    assert launched == [("chrome.exe", ["https://mail.google.com"])]


def test_old_extension_fallback_fails_honestly_when_tab_never_appears(ctx, monkeypatch):
    monkeypatch.setattr(bridge, "clients", {"c": None})
    monkeypatch.setattr(bridge, "info", {"c": {"version": "1.0.0", "capabilities": ["inspect"]}})
    monkeypatch.setattr(bridge, "request", lambda action, **kw: {"success": True, "tabs": []})
    monkeypatch.setattr(apps.OpenApplicationTool, "_find_executable", lambda self, name: "chrome.exe")
    monkeypatch.setattr(apps._platform, "launch_program", lambda exe, args=None: None)
    tool = OpenUrlTool(ctx)
    result = tool._open_and_find_tab("https://mail.google.com", wait_seconds=0.5)
    assert not result.success and "Reload" in result.error


def test_new_extension_uses_open_command(ctx, monkeypatch):
    monkeypatch.setattr(bridge, "clients", {"c": None})
    monkeypatch.setattr(bridge, "info", {"c": {"version": "1.2.0", "capabilities": ["inspect", "open"]}})
    monkeypatch.setattr(bridge, "request", lambda action, **kw: {"success": True, "tab_id": 3, "url": kw.get("url")} if action == "open" else pytest.fail(action))
    tool = OpenUrlTool(ctx)
    result = tool.execute(tool.validate_args({"url": "https://mail.google.com", "browser": "chrome"}))
    assert result.success and result.output["tab_id"] == 3
