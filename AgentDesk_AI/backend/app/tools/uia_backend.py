"""Windows UI Automation backend (layer 2 of the control stack): what is on screen, and bounded actions on it.

`UiaBackend` is the interface the tools use. `PywinautoBackend` implements it with pywinauto's UIA backend; tests use a fake one.
A snapshot is a compact list of at most 80 controls of one window: {id, name, type, value, enabled, rect, focused, ...}. Ids are
only valid for the snapshot they came from. Every action works on a control the snapshot DETECTED (invoke / toggle / select patterns,
or a click at the centre of that control's own rectangle): no coordinates are ever invented by a model.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_CONTROLS = 80
MAX_NODES = 900            # elements looked at per snapshot (a browser page has thousands)
SCAN_SECONDS = 1.5         # time budget for one snapshot
MAX_VALUE_CHARS = 200
CACHE_SECONDS = 2.0

INTERACTIVE = {"Button", "CheckBox", "RadioButton", "ComboBox", "Edit", "Hyperlink", "ListItem", "MenuItem", "MenuBar", "Menu",
               "TabItem", "TreeItem", "DataItem", "Slider", "Spinner", "SplitButton", "ToolBar", "Document", "Image", "ScrollBar",
               "List", "Tree", "Table", "DataGrid", "Pane", "Group", "Window", "Header", "HeaderItem", "ToggleButton"}
SKIP_WHEN_UNNAMED = {"Pane", "Group", "Image", "Window", "List", "Tree", "Table", "ToolBar", "Menu", "MenuBar", "Header", "ScrollBar"}


class UiaError(RuntimeError):
    """A UI action could not be done (control gone, pattern missing, window closed). The message is shown to the user."""


@dataclass
class WindowRef:
    title: str
    process: str = ""
    pid: int = 0
    handle: int = 0
    ref: Any = field(default=None, repr=False, compare=False)


@dataclass
class Control:
    id: int
    name: str
    type: str
    value: str | None = None
    enabled: bool = True
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    focused: bool = False
    password: bool = False
    toggled: bool | None = None
    selected: bool | None = None
    auto_id: str = ""
    ref: Any = field(default=None, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        """What the planner may see. A password field never shows its value, and no control shows more than 200 characters."""
        out: dict[str, Any] = {"id": self.id, "name": self.name[:80], "type": self.type, "enabled": self.enabled,
                               "box": list(self.rect)}
        if self.value is not None and not self.password:
            out["value"] = self.value[:MAX_VALUE_CHARS]
        if self.focused:
            out["focused"] = True
        if self.toggled is not None:
            out["checked"] = self.toggled
        if self.selected:
            out["selected"] = True
        if self.password:
            out["password"] = True
        return out


@dataclass
class Snapshot:
    window: WindowRef
    controls: list[Control]
    taken_at: float = field(default_factory=time.monotonic)
    truncated: bool = False
    generation: int = 0

    def digest(self) -> tuple:
        """What can change when something happened: title, and each control's name / value / state."""
        return (self.window.title, tuple((c.name, c.type, c.value if not c.password else None, c.enabled, c.toggled, c.selected)
                                         for c in self.controls))

    def find(self, control_id: int) -> Control | None:
        return next((c for c in self.controls if c.id == control_id), None)


class UiaBackend(Protocol):
    def foreground(self) -> WindowRef | None: ...
    def windows(self) -> list[WindowRef]: ...
    def snapshot(self, window: WindowRef, limit: int = MAX_CONTROLS) -> Snapshot: ...
    def click(self, window: WindowRef, control: Control) -> None: ...
    def set_text(self, window: WindowRef, control: Control, text: str) -> None: ...
    def select(self, window: WindowRef, control: Control, item: str) -> None: ...
    def scroll(self, window: WindowRef, control: Control | None, amount: int) -> None:
        info = control.ref if control is not None else window.ref
        w = self._wrapper(info)
        steps = abs(amount)
        before = self.scroll_position(window, control)
        try:
            for _ in range(steps):
                w.iface_scroll.Scroll(2, 3 if amount > 0 else 0)   # NoAmount horizontally; vertically one large step down / up
            after = self.scroll_position(window, control)
            if before is None or after is None or abs(after - before) > 0.001:
                return   # the pattern did it (or cannot say, and the caller's check will look at the screen)
        except Exception:  # noqa: BLE001
            pass
        # The control has no working scroll pattern (a web page, many Electron apps): turn the mouse wheel over it, or press Page Down / Up.
        self._focus(window)
        try:
            w.wheel_mouse_input(wheel_dist=-amount)
            return
        except Exception:  # noqa: BLE001
            pass
        try:
            from pywinauto.keyboard import send_keys
            send_keys(("{PGDN}" if amount > 0 else "{PGUP}") * steps, pause=0.05)
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"Could not scroll: {type(exc).__name__}") from exc

    def press_keys(self, window: WindowRef, combo: str) -> None: ...
    def read(self, window: WindowRef, control: Control) -> str: ...


# ----------------------------------------------------------------------------------------------------------- keys
_KEYS = {"enter": "{ENTER}", "return": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}", "escape": "{ESC}", "delete": "{DELETE}", "del": "{DELETE}",
         "backspace": "{BACKSPACE}", "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}", "home": "{HOME}",
         "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}", "space": "{SPACE}", "win": "{VK_LWIN}", "insert": "{INSERT}"}
_MODS = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}


def to_send_keys(combo: str) -> str:
    """"ctrl+shift+s" -> "^+s", "alt+f4" -> "%{F4}", "enter" -> "{ENTER}". Raises ValueError for anything it does not know."""
    parts = [p.strip().casefold() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key combination")
    prefix = ""
    for mod in parts[:-1]:
        if mod not in _MODS:
            raise ValueError(f"unknown modifier '{mod}'")
        prefix += _MODS[mod]
    key = parts[-1]
    if key in _KEYS:
        body = _KEYS[key]
    elif len(key) == 1 and key.isalnum():
        body = key
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        body = "{" + key.upper() + "}"
    else:
        raise ValueError(f"unknown key '{key}'")
    return prefix + body


def escape_for_send_keys(text: str) -> str:
    return "".join("{" + ch + "}" if ch in "{}()+^%~[]" else ch for ch in text)


# ----------------------------------------------------------------------------------------------------- pywinauto
class PywinautoBackend:
    """The real thing. Needs Windows and `pip install pywinauto`; imported lazily so servers on other systems never load it."""

    def __init__(self) -> None:
        import platform
        if platform.system() != "Windows":
            raise UiaError("UI Automation needs Windows.")
        from pywinauto.uia_element_info import UIAElementInfo
        self._Info = UIAElementInfo

    # -- windows
    def _ref(self, info) -> WindowRef:
        name = ""
        try:
            import psutil
            name = psutil.Process(info.process_id).name()
        except Exception:  # noqa: BLE001
            pass
        return WindowRef(title=info.name or "", process=name, pid=info.process_id or 0, handle=info.handle or 0, ref=info)

    def foreground(self) -> WindowRef | None:
        import ctypes
        hwnd = 0
        for _ in range(3):   # the answer is 0 for a moment while the focus moves between windows
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                break
            time.sleep(0.1)
        try:
            if hwnd:
                return self._ref(self._Info(handle=hwnd))
        except Exception:  # noqa: BLE001
            pass
        # No foreground answer (a background process, a locked desktop): the top-most real window is the best observed answer.
        for w in self.windows():
            if w.title and w.title not in ("Taskbar", "Program Manager"):
                return w
        return None

    def windows(self) -> list[WindowRef]:
        out = []
        for info in self._Info().children():
            try:
                if info.visible and info.name:
                    out.append(self._ref(info))
            except Exception:  # noqa: BLE001
                continue
        return out

    # -- reading
    @staticmethod
    def _wrapper(info):
        from pywinauto.controls.uiawrapper import UIAWrapper   # picks the typed wrapper (Button, Edit, ComboBox ...) itself
        return UIAWrapper(info)

    def snapshot(self, window: WindowRef, limit: int = MAX_CONTROLS) -> Snapshot:
        started = time.monotonic()
        root = window.ref
        found: list[tuple[Any, str]] = []
        queue = list(root.children())
        looked = 0
        truncated = False
        while queue:
            if looked >= MAX_NODES or time.monotonic() - started > SCAN_SECONDS:
                truncated = True
                break
            info = queue.pop(0)
            looked += 1
            try:
                ctype = info.control_type or ""
                visible = info.visible
            except Exception:  # noqa: BLE001
                continue
            if not visible:
                continue
            name = ""
            try:
                name = info.name or ""
            except Exception:  # noqa: BLE001
                pass
            if ctype in INTERACTIVE and not (ctype in SKIP_WHEN_UNNAMED and not name):
                found.append((info, ctype))
            elif ctype == "Text" and name and len(name) <= 60:
                found.append((info, ctype))
            try:
                queue.extend(info.children())
            except Exception:  # noqa: BLE001
                pass
        controls = [self._control(0, info, ctype) for info, ctype in found]
        controls = self._prioritise(controls, limit)
        for i, c in enumerate(controls, 1):
            c.id = i
        return Snapshot(window=window, controls=controls, truncated=truncated or len(found) > limit)

    def _control(self, cid: int, info, ctype: str) -> Control:
        try:
            r = info.rectangle
            rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:  # noqa: BLE001
            rect = (0, 0, 0, 0)
        focused = password = False
        try:
            focused = bool(info.element.CurrentHasKeyboardFocus)
            password = bool(info.element.CurrentIsPassword)
        except Exception:  # noqa: BLE001
            pass
        value = toggled = selected = None
        if not password:
            try:
                if ctype in ("Edit", "ComboBox", "Document", "Slider", "Spinner", "DataItem", "ListItem", "Hyperlink"):
                    from pywinauto.controls.uiawrapper import UIAWrapper
                    value = UIAWrapper(info).iface_value.CurrentValue
            except Exception:  # noqa: BLE001
                value = None
        try:
            if ctype in ("CheckBox", "RadioButton", "ToggleButton", "Button", "MenuItem"):
                from pywinauto.controls.uiawrapper import UIAWrapper
                state = UIAWrapper(info).iface_toggle.CurrentToggleState
                toggled = state == 1
        except Exception:  # noqa: BLE001
            toggled = None
        try:
            if ctype in ("ListItem", "TabItem", "TreeItem", "RadioButton", "DataItem"):
                from pywinauto.controls.uiawrapper import UIAWrapper
                selected = bool(UIAWrapper(info).iface_selection_item.CurrentIsSelected)
        except Exception:  # noqa: BLE001
            selected = None
        try:
            enabled = bool(info.enabled)
            auto_id = info.automation_id or ""
        except Exception:  # noqa: BLE001
            enabled, auto_id = True, ""
        return Control(cid, (info.name or "").strip(), ctype, value, enabled, rect, focused, password, toggled, selected, auto_id, info)

    @staticmethod
    def _prioritise(controls: list[Control], limit: int) -> list[Control]:
        """Focused first, then enabled interactive controls with a name, then the rest; kept in reading order (top to bottom)."""
        def rank(c: Control) -> tuple:
            return (not c.focused, c.type == "Text", not c.name, not c.enabled)
        keep = sorted(controls, key=rank)[:limit]
        return sorted(keep, key=lambda c: (c.rect[1] // 12, c.rect[0]))

    # -- acting
    def _live(self, control: Control):
        if control.ref is None:
            raise UiaError("That control is not available any more. Look at the window again.")
        return self._wrapper(control.ref)

    def _focus(self, window: WindowRef) -> None:
        try:
            self._wrapper(window.ref).set_focus()
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"Could not bring '{window.title}' to the front: {type(exc).__name__}") from exc

    def click(self, window: WindowRef, control: Control) -> None:
        w = self._live(control)
        for pattern in ("iface_invoke", "iface_toggle", "iface_selection_item"):
            try:
                iface = getattr(w, pattern)
                if pattern == "iface_invoke":
                    iface.Invoke()
                elif pattern == "iface_toggle":
                    iface.Toggle()
                else:
                    iface.Select()
                return
            except Exception:  # noqa: BLE001 - the control does not have this pattern; try the next
                continue
        self._focus(window)
        try:
            w.click_input()      # the centre of the rectangle UI Automation reported for THIS control
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"Could not click '{control.name}': {type(exc).__name__}") from exc

    def set_text(self, window: WindowRef, control: Control, text: str) -> None:
        w = self._live(control)
        try:
            w.iface_value.SetValue(text)
            return
        except Exception:  # noqa: BLE001
            pass
        self._focus(window)
        try:
            from pywinauto.keyboard import send_keys
            w.set_focus()
            send_keys("^a{DELETE}" + escape_for_send_keys(text), with_spaces=True, pause=0.01)
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"Could not type into '{control.name}': {type(exc).__name__}") from exc

    def select(self, window: WindowRef, control: Control, item: str) -> None:
        w = self._live(control)
        try:
            w.select(item)
            return
        except Exception:  # noqa: BLE001
            pass
        try:
            w.iface_expand_collapse.Expand()
        except Exception:  # noqa: BLE001
            pass
        wanted = item.casefold().strip()
        for child in control.ref.descendants():
            try:
                if (child.name or "").casefold().strip() == wanted and child.control_type in ("ListItem", "MenuItem", "TabItem", "TreeItem"):
                    cw = self._wrapper(child)
                    try:
                        cw.iface_selection_item.Select()
                    except Exception:  # noqa: BLE001
                        cw.iface_invoke.Invoke()
                    return
            except Exception:  # noqa: BLE001
                continue
        raise UiaError(f"'{control.name}' has no item called '{item}'.")

    def scroll_position(self, window: WindowRef, control: Control | None) -> float | None:
        info = control.ref if control is not None else window.ref
        try:
            return float(self._wrapper(info).iface_scroll.CurrentVerticalScrollPercent)
        except Exception:  # noqa: BLE001
            return None

    def scroll(self, window: WindowRef, control: Control | None, amount: int) -> None:
        info = control.ref if control is not None else window.ref
        w = self._wrapper(info)
        steps = abs(amount)
        try:
            for _ in range(steps):
                w.iface_scroll.Scroll(2, 3 if amount > 0 else 0)   # NoAmount horizontally; vertically one large step down / up
            return
        except Exception:  # noqa: BLE001
            pass
        self._focus(window)
        try:
            w.wheel_mouse_input(wheel_dist=-amount)
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"Could not scroll: {type(exc).__name__}") from exc

    def press_keys(self, window: WindowRef, combo: str) -> None:
        try:
            seq = to_send_keys(combo)
        except ValueError as exc:
            raise UiaError(str(exc)) from exc
        self._focus(window)
        from pywinauto.keyboard import send_keys
        send_keys(seq, pause=0.02)

    def read(self, window: WindowRef, control: Control) -> str:
        w = self._live(control)
        for getter in (lambda: w.iface_value.CurrentValue, lambda: w.iface_text.DocumentRange.GetText(2000), lambda: w.window_text()):
            try:
                text = getter()
                if text:
                    return str(text)[:2000]
            except Exception:  # noqa: BLE001
                continue
        return control.name


_backend: UiaBackend | None = None


def get_backend() -> UiaBackend:
    global _backend
    if _backend is None:
        _backend = PywinautoBackend()
    return _backend


def set_backend(backend: UiaBackend | None) -> None:
    global _backend
    _backend = backend
