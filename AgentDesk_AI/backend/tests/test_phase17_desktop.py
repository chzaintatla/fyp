"""Desktop shell (desktop/shell.py) and autostart, with fake pywebview / registry / tray."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from desktop import autostart  # noqa: E402
from desktop.shell import DesktopApi, Shell, menu_items  # noqa: E402


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        return [h() for h in self.handlers]


class FakeWindow:
    def __init__(self, title, url, **kw):
        self.title, self.url, self.kw = title, url, kw
        self.calls = []
        self.events = type("Events", (), {"closing": Event()})()

    def __getattr__(self, name):
        if name in {"show", "hide", "restore", "destroy"}:
            return lambda: self.calls.append(name)
        raise AttributeError(name)


class FakeWebview:
    def __init__(self):
        self.windows = []
        self.started = None

    def create_window(self, title, url, **kw):
        window = FakeWindow(title, url, **kw)
        self.windows.append(window)
        return window

    def start(self, func, **kw):
        self.started = kw
        func()


class FakeRegistry:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1

    def __init__(self):
        self.values = {}

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, path, reserved, access):
        assert (root, path) == ("HKCU", autostart.RUN_KEY)
        return self._Key()

    CreateKeyEx = OpenKey

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def shell():
    s = Shell(FakeWebview(), "http://127.0.0.1:8000/", "TOKEN123", registry=FakeRegistry(), screen=(1920, 1080))
    s.create_windows(start_minimized=False)
    return s


def test_two_windows_panel_and_always_on_top_orb(shell):
    panel, orb = shell.webview.windows
    assert panel.url == "http://127.0.0.1:8000/#token=TOKEN123"
    assert orb.url == "http://127.0.0.1:8000/?view=orb#token=TOKEN123"
    assert orb.kw["on_top"] and orb.kw["frameless"] and orb.kw["transparent"] and not orb.kw["resizable"]
    assert orb.kw["x"] + orb.kw["width"] <= 1920 and orb.kw["y"] + orb.kw["height"] <= 1080
    assert panel.kw["js_api"] is orb.kw["js_api"] is shell.api


def test_js_bridge_exposes_only_window_and_autostart_controls(shell):
    public = {name for name in dir(DesktopApi) if not name.startswith("_")}
    assert public == {"expand", "collapse", "get_autostart", "set_autostart"}


def test_orb_expands_panel_and_closing_panel_hides_to_tray(shell):
    panel = shell.panel
    shell.api.collapse()
    assert panel.calls[-1] == "hide" and not shell.panel_visible
    shell.api.expand()
    assert panel.calls[-2:] == ["show", "restore"] and shell.panel_visible
    assert panel.events.closing.fire() == [False]  # close button hides instead of quitting
    assert panel.calls[-1] == "hide"
    shell.quitting = True
    assert panel.events.closing.fire() == [True]


def test_minimized_start_shows_only_the_orb():
    s = Shell(FakeWebview(), "http://127.0.0.1:8000", "T", registry=FakeRegistry())
    s.create_windows(start_minimized=True)
    assert s.panel.kw["hidden"] is True and not s.panel_visible


def test_autostart_writes_only_one_run_value_and_removes_it(shell):
    reg = shell.registry
    assert shell.api.get_autostart() is False
    assert shell.api.set_autostart(True) is True
    assert list(reg.values) == ["AgentDesk"]
    assert "shell.py" in reg.values["AgentDesk"] and "--minimized" in reg.values["AgentDesk"]
    assert shell.api.set_autostart(False) is False and reg.values == {}
    assert shell.api.set_autostart(False) is False  # idempotent


def test_autostart_is_refused_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(OSError):
        autostart.is_enabled()


def test_tray_menu_actions(shell):
    made = []

    def item(text, action, **kw):
        made.append((text, action, kw))
        return text
    items = menu_items(shell, item, "SEP")
    assert items == ["Open Agent X", "Start / stop listening", "Show / hide mini mode", "Settings", "Start with Windows", "SEP", "Quit"]
    actions = {text: action for text, action, _ in made}
    shell.hide_panel()
    actions["Open Agent X"]()
    assert shell.panel_visible
    actions["Show / hide mini mode"]()
    assert shell.orb.calls[-1] == "hide"
    actions["Start with Windows"]()
    assert shell.api.get_autostart() is True
    checked = dict((t, kw.get("checked")) for t, _, kw in made)["Start with Windows"]
    assert checked() is True
    stopped = []
    shell.tray = type("Tray", (), {"stop": lambda self: stopped.append(1)})()
    actions["Quit"]()
    assert stopped and shell.quitting and shell.panel.calls[-1] == "destroy" and shell.orb.calls[-1] == "destroy"


def test_run_starts_webview_and_tray(shell):
    started = []
    s = Shell(FakeWebview(), "http://127.0.0.1:8000", "T", registry=FakeRegistry(),
              tray_factory=lambda sh: type("Tray", (), {"run": lambda self: started.append("tray")})())
    s.run(start_minimized=True)
    import time
    time.sleep(0.1)
    assert started == ["tray"] and s.webview.started == {"private_mode": False}
