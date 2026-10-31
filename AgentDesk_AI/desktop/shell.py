"""AgentDesk desktop shell.

Two pywebview windows on top of the local backend:
- the panel (the full React app), hidden to the tray instead of closing;
- the orb: a small frameless, transparent, always-on-top window (?view=orb) that
  expands to the panel when pressed.
A tray icon (pystray) offers Open, Show orb, Start with Windows and Quit.

The JS bridge (`window.pywebview.api`) exposes only expand/collapse and the
autostart switch. It cannot run commands, read files or touch approvals: every
action still goes through the authenticated HTTP API and its approval rules.

Run: python desktop/shell.py [--minimized]   (Windows; needs pywebview + pystray)
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desktop import autostart  # noqa: E402

ORB_SIZE = 132
PANEL_SIZE = (1180, 820)


class DesktopApi:
    """Methods callable from the page as window.pywebview.api.<name>()."""

    def __init__(self, shell: "Shell"):
        self._shell = shell

    def expand(self) -> None:
        self._shell.show_panel()

    def collapse(self) -> None:
        self._shell.hide_panel()

    def get_autostart(self) -> bool:
        try:
            return autostart.is_enabled(self._shell.registry)
        except OSError:
            return False

    def set_autostart(self, enabled: bool) -> bool:
        return autostart.set_enabled(bool(enabled), self._shell.registry)


class Shell:
    def __init__(self, webview, url: str, token: str, *, tray_factory: Callable[["Shell"], Any] | None = None,
                 registry=None, screen: tuple[int, int] = (1920, 1080)):
        self.webview = webview
        self.url = url.rstrip("/")
        self.token = token
        self.tray_factory = tray_factory
        self.registry = registry
        self.screen = screen
        self.api = DesktopApi(self)
        self.panel = None
        self.orb = None
        self.tray = None
        self.quitting = False
        self.panel_visible = False

    def page(self, query: str = "") -> str:
        # The token travels in the URL fragment, which is never sent to the server or logged.
        return f"{self.url}/{query}#token={self.token}"

    def create_windows(self, start_minimized: bool) -> None:
        width, height = self.screen
        self.panel = self.webview.create_window(
            "Agent X", self.page(), width=PANEL_SIZE[0], height=PANEL_SIZE[1], min_size=(720, 560),
            hidden=start_minimized, js_api=self.api, text_select=True)
        self.orb = self.webview.create_window(
            "Agent X orb", self.page("?view=orb"), width=ORB_SIZE, height=ORB_SIZE,
            x=width - ORB_SIZE - 24, y=height - ORB_SIZE - 72, frameless=True, on_top=True,
            transparent=True, easy_drag=True, resizable=False, js_api=self.api)
        self.panel_visible = not start_minimized
        self.panel.events.closing += self._on_panel_closing

    def _on_panel_closing(self) -> bool:
        """Closing the panel hides it to the tray; only Quit ends the app."""
        if self.quitting:
            return True
        self.hide_panel()
        return False

    def show_panel(self) -> None:
        self.panel.show()
        self.panel.restore()
        self.panel_visible = True

    def hide_panel(self) -> None:
        self.panel.hide()
        self.panel_visible = False

    def run_in_panel(self, script: str) -> None:
        """Run a fixed script in the panel (used by the tray). Never builds script text from user input."""
        evaluate = getattr(self.panel, "evaluate_js", None)
        if evaluate is not None:
            evaluate(script)

    def toggle_listening(self) -> None:
        self.show_panel()
        self.run_in_panel("window.__agentx && window.__agentx.toggleVoice()")

    def open_settings(self) -> None:
        self.show_panel()
        self.run_in_panel("window.__agentx && window.__agentx.openView('settings')")

    def toggle_orb(self) -> None:
        if getattr(self.orb, "_agentdesk_hidden", False):
            self.orb.show()
            self.orb._agentdesk_hidden = False
        else:
            self.orb.hide()
            self.orb._agentdesk_hidden = True

    def quit(self) -> None:
        self.quitting = True
        if self.tray is not None:
            self.tray.stop()
        for window in (self.orb, self.panel):
            if window is not None:
                window.destroy()

    def start_tray(self) -> None:
        if self.tray_factory is not None:
            self.tray = self.tray_factory(self)
            threading.Thread(target=self.tray.run, name="agentdesk-tray", daemon=True).start()

    def run(self, start_minimized: bool = False) -> None:
        self.create_windows(start_minimized)
        self.webview.start(self.start_tray, private_mode=False)


def tray_icon(shell: Shell):
    """pystray icon with the shell's menu (Windows notification area)."""
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 60, 60), fill=(79, 70, 229, 255))
    return pystray.Icon("AgentDesk", image, "Agent X", menu=pystray.Menu(*menu_items(shell, pystray.MenuItem, pystray.Menu.SEPARATOR)))


def menu_items(shell: Shell, item, separator):
    """Tray menu, built from plain callables so it can be tested without a tray."""
    return [
        item("Open Agent X", lambda *_: shell.show_panel(), default=True),
        item("Start / stop listening", lambda *_: shell.toggle_listening()),
        item("Show / hide mini mode", lambda *_: shell.toggle_orb()),
        item("Settings", lambda *_: shell.open_settings()),
        item("Start with Windows", lambda *_: shell.api.set_autostart(not shell.api.get_autostart()),
             checked=lambda *_: shell.api.get_autostart()),
        separator,
        item("Quit", lambda *_: shell.quit()),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentDesk desktop shell")
    parser.add_argument("--minimized", action="store_true", help="start with only the orb and tray icon")
    args = parser.parse_args(argv)
    import launch

    if not launch.ensure_backend():
        print("Backend start nahi ho saka. logs/backend.log check karein.")
        return 1
    import webview

    screen = (1920, 1080)
    try:
        primary = webview.screens[0]
        screen = (primary.width, primary.height)
    except Exception:  # noqa: BLE001 - default position is fine
        pass
    Shell(webview, launch.URL, launch.api_token(), tray_factory=tray_icon, screen=screen).run(args.minimized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
