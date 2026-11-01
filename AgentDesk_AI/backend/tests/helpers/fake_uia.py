"""A small fake desktop for the UI Automation tests: seven apps with controls that react like the real ones (typing sets a value, a
toggle flips, a calculator adds, a dialog opens in front). It implements `UiaBackend`, so the real tools run unchanged on top of it.

It tests the TOOL LOGIC (finding controls by name, risk, refusals, verification by looking again). It says nothing about how well
pywinauto drives the real apps: `scripts/uia_live.py` does that on a real PC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.tools.uia_backend import Control, Snapshot, UiaError, WindowRef, to_send_keys


@dataclass
class FC:
    name: str
    type: str = "Button"
    value: str | None = None
    enabled: bool = True
    toggled: bool | None = None
    selected: bool | None = None
    password: bool = False
    items: list[str] = field(default_factory=list)
    scroll_pos: float | None = None
    on_click: Callable[["FakeWorld", "FakeWindow", "FC"], None] | None = None
    x: int = 0
    y: int = 0


@dataclass
class FakeWindow:
    title: str
    process: str
    handle: int
    controls: list[FC] = field(default_factory=list)
    on_keys: Callable[["FakeWorld", "FakeWindow", str], None] | None = None

    def get(self, name: str) -> FC:
        return next(c for c in self.controls if c.name == name)


class FakeWorld:
    def __init__(self) -> None:
        self.windows: list[FakeWindow] = []
        self.front: int = 0
        self.log: list[str] = []
        self._next = 100

    def add(self, title: str, process: str, controls: list[FC], on_keys=None) -> FakeWindow:
        self._next += 1
        for i, c in enumerate(controls):
            c.x, c.y = 20 + (i % 4) * 90, 40 + (i // 4) * 30
        w = FakeWindow(title, process, self._next, controls, on_keys)
        self.windows.append(w)
        self.front = w.handle
        return w

    def close(self, window: FakeWindow) -> None:
        self.windows = [w for w in self.windows if w is not window]
        self.front = self.windows[-1].handle if self.windows else 0


class FakeBackend:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    # -- UiaBackend
    def foreground(self) -> WindowRef | None:
        w = next((w for w in self.world.windows if w.handle == self.world.front), None)
        return WindowRef(w.title, w.process, 0, w.handle, w) if w else None

    def windows(self) -> list[WindowRef]:
        return [WindowRef(w.title, w.process, 0, w.handle, w) for w in self.world.windows]

    def snapshot(self, window: WindowRef, limit: int = 80) -> Snapshot:
        w: FakeWindow = next((x for x in self.world.windows if x.handle == window.handle), None)
        if w is None:
            raise UiaError(f"'{window.title}' is not open.")
        controls = [Control(i, c.name, c.type, c.value, c.enabled, (c.x, c.y, c.x + 80, c.y + 24), False, c.password, c.toggled,
                            c.selected, "", c) for i, c in enumerate(w.controls[:limit], 1)]
        return Snapshot(window=WindowRef(w.title, w.process, 0, w.handle, w), controls=controls, truncated=len(w.controls) > limit)

    def _fc(self, control: Control) -> FC:
        return control.ref

    def _win(self, window: WindowRef) -> FakeWindow:
        return window.ref

    def click(self, window, control):
        fc = self._fc(control)
        self.world.log.append(f"click {fc.name}")
        if fc.on_click:
            fc.on_click(self.world, self._win(window), fc)
        elif fc.toggled is not None:
            fc.toggled = not fc.toggled
        elif fc.selected is not None:
            for other in self._win(window).controls:
                if other.type == fc.type and other.selected is not None:
                    other.selected = other is fc

    def set_text(self, window, control, text):
        fc = self._fc(control)
        if fc.type not in ("Edit", "Document", "ComboBox"):
            raise UiaError(f"'{fc.name}' does not take text.")
        fc.value = text
        self.world.log.append(f"type {fc.name}")

    def select(self, window, control, item):
        fc = self._fc(control)
        match = next((i for i in fc.items if i.casefold() == item.casefold()), None)
        if match is None:
            raise UiaError(f"'{fc.name}' has no item called '{item}'.")
        fc.value = match
        self.world.log.append(f"select {match}")

    def scroll_position(self, window, control):
        fc = self._fc(control) if control else next((c for c in self._win(window).controls if c.scroll_pos is not None), None)
        return fc.scroll_pos if fc else None

    def scroll(self, window, control, amount):
        fc = self._fc(control) if control else next((c for c in self._win(window).controls if c.scroll_pos is not None), None)
        if fc is None or fc.scroll_pos is None:
            raise UiaError("Nothing scrolls here.")
        fc.scroll_pos = max(0.0, min(1.0, fc.scroll_pos + amount * 0.1))

    def press_keys(self, window, combo):
        to_send_keys(combo)
        self.world.log.append(f"keys {combo}")
        w = self._win(window)
        if w.on_keys:
            w.on_keys(self.world, w, combo.replace(" ", "").casefold())

    def read(self, window, control):
        fc = self._fc(control)
        return fc.value if fc.value is not None else fc.name


# ------------------------------------------------------------------------------------------------- the seven apps
def build_world() -> tuple[FakeWorld, dict[str, FakeWindow]]:
    world = FakeWorld()
    apps: dict[str, FakeWindow] = {}

    # Notepad: ctrl+s opens a Save dialog in front
    def notepad_keys(wld, win, combo):
        if combo == "ctrl+s":
            def save(w2, dlg, fc):
                w2.close(dlg)
            wld.add("Save As", "notepad.exe", [FC("File name", "Edit", ""), FC("Save", on_click=save), FC("Cancel", on_click=save)])
        elif combo == "ctrl+a":
            win.get("Text editor").value = win.get("Text editor").value  # selection only: nothing visible changes

    apps["notepad"] = world.add("Untitled - Notepad", "notepad.exe", [
        FC("Text editor", "Document", ""), FC("File", "MenuItem"), FC("Edit", "MenuItem"), FC("View", "MenuItem"),
        FC("Word wrap", "CheckBox", toggled=True), FC("Status bar", "CheckBox", toggled=True), FC("Zoom in", "Button")], notepad_keys)

    # File Explorer
    def back(wld, win, fc):
        win.get("Address").value = "This PC"
    apps["explorer"] = world.add("Documents - File Explorer", "explorer.exe", [
        FC("Address", "Edit", "This PC > Documents"), FC("Search Documents", "Edit", ""), FC("Back", on_click=back),
        FC("Forward"), FC("Documents", "ListItem", selected=False), FC("Downloads", "ListItem", selected=False),
        FC("Report.docx", "ListItem", selected=False), FC("Sort", "Button"), FC("New folder", "Button")])

    # Settings
    apps["settings"] = world.add("Settings", "systemsettings.exe", [
        FC("Find a setting", "Edit", ""), FC("System", "ListItem", selected=True), FC("Bluetooth & devices", "ListItem", selected=False),
        FC("Network & internet", "ListItem", selected=False), FC("Personalization", "ListItem", selected=False),
        FC("Choose your mode", "ComboBox", "Light", items=["Light", "Dark", "Custom"]), FC("Transparency effects", "CheckBox", toggled=True),
        FC("Reset", "Button")])

    # Calculator
    def digit(d):
        def click(wld, win, fc):
            disp = win.get("Display")
            disp.value = d if disp.value in ("0", "") else disp.value + d
        return click

    def op(symbol):
        def click(wld, win, fc):
            disp = win.get("Display")
            win.controls.append(FC("_acc", "Text", disp.value))
            win.controls.append(FC("_op", "Text", symbol))
            disp.value = "0"
        return click

    def equals(wld, win, fc):
        disp = win.get("Display")
        acc = int(win.get("_acc").value)
        sym = win.get("_op").value
        disp.value = str(acc + int(disp.value) if sym == "+" else acc * int(disp.value))

    def clear(wld, win, fc):
        win.get("Display").value = "0"
    keys = [FC("Display", "Text", "0")] + [FC(n, on_click=digit(str(i))) for i, n in enumerate(
        ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"])]
    keys += [FC("Plus", on_click=op("+")), FC("Multiply by", on_click=op("*")), FC("Equals", on_click=equals), FC("Clear", on_click=clear)]
    apps["calculator"] = world.add("Calculator", "calculatorapp.exe", keys)

    # Word
    apps["word"] = world.add("Document1 - Word", "winword.exe", [
        FC("Page 1 content", "Document", ""), FC("Bold", "Button", toggled=False), FC("Italic", "Button", toggled=False),
        FC("Font", "ComboBox", "Calibri", items=["Calibri", "Arial", "Times New Roman"]), FC("Save", "Button"),
        FC("Share", "Button"), FC("Send", "Button"), FC("Home", "TabItem", selected=True), FC("Insert", "TabItem", selected=False)])

    # Excel
    apps["excel"] = world.add("Book1 - Excel", "excel.exe", [
        FC("Name Box", "Edit", "A1"), FC("Formula Bar", "Edit", ""), FC("Sheet grid", "Document", ""),
        FC("Bold", "Button", toggled=False), FC("Number format", "ComboBox", "General", items=["General", "Number", "Currency", "Percentage"]),
        FC("Sort & Filter", "Button"), FC("Sheet1", "TabItem", selected=True), FC("New sheet", "Button")])

    # Chrome
    def sign_in(wld, win, fc):
        win.title = "Sign in - Google Accounts - Google Chrome"
    def tab(name):
        def click(wld, win, fc):
            for t in win.controls:
                if t.type == "TabItem":
                    t.selected = t is fc
            win.title = f"{name} - Google Chrome"
        return click
    apps["chrome"] = world.add("Gmail - Google Chrome", "chrome.exe", [
        FC("Address and search bar", "Edit", "mail.google.com"), FC("Gmail", "TabItem", selected=True, on_click=tab("Gmail")),
        FC("YouTube", "TabItem", selected=False, on_click=tab("YouTube")), FC("Reload", "Button"), FC("Sign in", "Hyperlink", on_click=sign_in),
        FC("Page", "Document", "Inbox", scroll_pos=0.0), FC("Buy now", "Button"), FC("Password", "Edit", "hunter2", password=True)])

    world.front = apps["notepad"].handle
    return world, apps
