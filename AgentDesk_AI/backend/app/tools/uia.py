"""UI Automation tools: look at a window's controls (`ui_inspect`) and act on one of them (`ui_act`), verified by looking again.

This is layer 2 of the control stack (native tools first, then UI Automation, then vision). The model sees a compact list of the
controls the OS reports, names one of them, and the server does the rest:
  * the control is found by NAME (the same sound-alike matcher as app and file names; "sign in" finds "Sign in"), optionally with its
    type or the id from the latest list; two controls that fit equally make the tool ask which one, it never guesses;
  * risk is set here, not by the model: reading and scrolling are low, typing / selecting / clicking are medium, clicking Pay / Buy /
    Delete / Submit / Install / Send / Uninstall ... and closing keys (Alt+F4, Ctrl+W, Delete) are high;
  * a shell, console or admin tool never receives input (same rule as `control_desktop`); a password field is never read or typed into;
  * text found on the screen is DATA: a control's name can never change what the tool does;
  * after the action the window is read again; "done" needs evidence (a value read back, a state that changed, a window that moved on).
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..nlu import entity_match
from .base import BaseTool, RiskLevel, ToolResult, Verification
from .desktop import is_shell_target
from .uia_backend import CACHE_SECONDS, MAX_CONTROLS, Control, Snapshot, UiaError, WindowRef, get_backend, to_send_keys

# Controls whose activation is hard to take back or spends / sends / removes something.
_DANGEROUS = re.compile(
    r"\b(?:pay|payment|buy|purchase|order|checkout|check out|subscribe|donate|delete|remove|erase|wipe|format|uninstall|install|"
    r"submit|send|publish|post|transfer|withdraw|confirm|sign out|log ?out|empty|discard|reset|factory|"
    r"agree|accept|allow|grant|yes, ?delete)\b", re.I)
_SECRET = re.compile(r"password|passcode|pin\b|cvv|cvc|card number|security code|otp|one[- ]time|secret|token|ssn", re.I)
_CLOSING_KEYS = {"alt+f4", "ctrl+w", "delete", "del", "ctrl+shift+delete", "ctrl+shift+w", "win+l", "alt+f4 "}
_SETTLE_SECONDS = 1.6


def is_dangerous(name: str) -> bool:
    return bool(_DANGEROUS.search(name or ""))


class UiInspectArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    window: str | None = Field(default=None, max_length=160, description="Part of the window title or the app name; empty = the window in front")
    query: str | None = Field(default=None, max_length=80, description="Only controls whose name is like this")
    limit: int = Field(default=MAX_CONTROLS, ge=1, le=MAX_CONTROLS)


class UiActArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["click", "type", "select", "scroll", "press_keys", "read"]
    window: str | None = Field(default=None, max_length=160, description="Part of the window title or the app name; empty = the window in front")
    control: str | None = Field(default=None, max_length=120, description="Name of the control as shown on screen")
    control_type: str | None = Field(default=None, max_length=30, description="Button, Edit, CheckBox, ComboBox, ListItem, MenuItem, TabItem, Hyperlink ...")
    id: int | None = Field(default=None, ge=1, le=MAX_CONTROLS, description="Id from the latest ui_inspect list (only if the window did not change)")
    text: str | None = Field(default=None, max_length=2000, description="Text to type")
    item: str | None = Field(default=None, max_length=120, description="Item to select")
    amount: int = Field(default=3, ge=-20, le=20, description="Scroll steps; positive = down, negative = up")
    keys: str | None = Field(default=None, max_length=40, description="Key combination such as ctrl+s, enter, alt+left")
    expect: str | None = Field(default=None, max_length=200, description="Text that must be visible afterwards (optional extra check)")

    @model_validator(mode="after")
    def _needs(self):
        a = self.action
        if a in ("click", "type", "select", "read") and not (self.control or self.id):
            raise ValueError(f"{a} needs the control's name (or its id)")
        if a == "type" and self.text is None:
            raise ValueError("type needs the text")
        if a == "select" and not self.item:
            raise ValueError("select needs the item")
        if a == "press_keys":
            if not self.keys:
                raise ValueError("press_keys needs the keys")
            to_send_keys(self.keys)      # unknown keys are rejected here, before anything runs
        return self


@dataclass
class _Entry:
    name: str
    kind: str = "control"
    path: str = ""
    source: str = ""


class UiaService:
    """The backend plus a 2 s snapshot cache (for looking; acting always reads fresh)."""

    def __init__(self, backend=None):
        self._backend = backend
        self._cache: dict[int, Snapshot] = {}
        self._generation = 0

    @property
    def backend(self):
        return self._backend or get_backend()

    def window(self, query: str | None) -> WindowRef:
        b = self.backend
        if not query or not query.strip():
            w = b.foreground()
            if w is None:
                raise UiaError("No window is in front.")
            return w
        wanted = query.casefold().strip()
        windows = b.windows()
        hits = [w for w in windows if wanted in w.title.casefold() or wanted in w.process.casefold().removesuffix(".exe")]
        if not hits:   # a spoken name: "note pad" -> "Untitled - Notepad"
            scored = sorted(((entity_match.score(query, w.title), w) for w in windows), key=lambda p: -p[0])
            hits = [w for s, w in scored if s >= 0.7][:1]
        if not hits:
            raise UiaError(f"No open window matches '{query}'.")
        return hits[0]

    def snapshot(self, window: WindowRef, fresh: bool = False, limit: int = MAX_CONTROLS) -> Snapshot:
        cached = self._cache.get(window.handle)
        if cached and not fresh and time.monotonic() - cached.taken_at < CACHE_SECONDS:
            return cached
        snap = self.backend.snapshot(window, limit)
        self._generation += 1
        snap.generation = self._generation
        self._cache[window.handle] = snap
        return snap


_service = UiaService()


def get_service() -> UiaService:
    return _service


def set_service(service: UiaService | None) -> None:
    global _service
    _service = service or UiaService()


def _digest(snap: Snapshot) -> str:
    return hashlib.sha1(repr(snap.digest()).encode("utf-8", "replace")).hexdigest()[:16]


# A real window repeats names: a label "Search box" next to the field "Search box", a nav item and a card both called "Bluetooth".
# The action says which kind of control is meant, so the pool is narrowed to those types first.
_PREFERRED_TYPES = {
    "click": {"Button", "ListItem", "MenuItem", "TabItem", "CheckBox", "RadioButton", "Hyperlink", "SplitButton", "ToggleButton",
              "TreeItem", "DataItem", "ComboBox"},
    "type": {"Edit", "Document", "ComboBox", "Spinner", "Slider"},
    "select": {"ComboBox", "ListItem", "MenuItem", "TabItem", "TreeItem", "RadioButton", "List"},
    "read": {"Edit", "Document", "Text", "ComboBox", "ListItem", "DataItem", "Button", "Hyperlink", "CheckBox"},
}
# Controls of these types all do "the same click": two of them with one name are one choice, not a question.
_ACTIVATE_ALIKE = {"Button", "ListItem", "MenuItem", "TabItem", "Hyperlink", "SplitButton", "TreeItem", "ToggleButton"}


def resolve_control(snap: Snapshot, args: UiActArgs) -> Control:
    """The one control the user meant, or UiaError (with a question when several fit)."""
    pool = [c for c in snap.controls if not args.control_type or c.type.casefold() == args.control_type.casefold()]
    wanted_types = _PREFERRED_TYPES.get(args.action)
    if wanted_types and not args.control_type:
        narrowed = [c for c in pool if c.type in wanted_types]
        pool = narrowed or pool
    if args.control:
        entries = [_Entry(c.name) for c in pool if c.name]
        decision = entity_match.resolve(args.control, entries)
        if decision.action in ("act", "act_say"):
            same = [c for c in pool if c.name == decision.chosen.name]
            if args.id and any(c.id == args.id for c in same):
                return next(c for c in same if c.id == args.id)
            if len(same) == 1:
                return same[0]
            if args.action == "click" and {c.type for c in same} <= _ACTIVATE_ALIKE and len({c.type for c in same}) > 1:
                return same[0]   # a nav item and a card with the same name lead to the same place: the first in reading order
            raise _ask(args.control, [f"{c.type} #{c.id} at {c.rect[0]},{c.rect[1]}" for c in same], same)
        if decision.action == "ask":
            raise _ask(args.control, [c.name for c in decision.candidates[:3]], [])
        if args.id and snap.find(args.id):
            found = snap.find(args.id)
            if entity_match.score(args.control, found.name) >= 0.6:
                return found
        raise UiaError(f"No control called '{args.control}' in '{snap.window.title}'. Controls seen: "
                       f"{', '.join(c.name for c in pool[:8] if c.name) or 'none'}.")
    found = snap.find(args.id or 0)
    if found is None:
        raise UiaError(f"Control id {args.id} is not in the current list of '{snap.window.title}'. Look at the window again.")
    return found


def _scroll_target(snap: Snapshot) -> Control | None:
    """The biggest document, list or table: that is what "scroll down" means when no control is named."""
    big = [c for c in snap.controls if c.type in ("Document", "List", "Table", "DataGrid", "Tree") and c.enabled]
    return max(big, key=lambda c: (c.rect[2] - c.rect[0]) * (c.rect[3] - c.rect[1]), default=None)


def _ask(wanted: str, options: list[str], controls: list[Control]) -> UiaError:
    err = UiaError(f"More than one control fits '{wanted}': {'; '.join(options)}.")
    err.question = f"Which one do you mean by \"{wanted}\": {' or '.join(options)}?"   # type: ignore[attr-defined]
    err.options = options                                                             # type: ignore[attr-defined]
    return err


class UiInspectTool(BaseTool):
    name = "ui_inspect"
    description = ("List the controls (buttons, fields, menus, tabs, links ...) of a window: name, type, value, enabled, position. At most 80, "
                   "focused first. Use it to see what can be clicked or typed before ui_act; it changes nothing.")
    risk_level = RiskLevel.LOW
    args_schema = UiInspectArgs
    interruptible = True

    def execute(self, args: BaseModel) -> ToolResult:
        a = UiInspectArgs.model_validate(args.model_dump())
        try:
            service = get_service()
            window = service.window(a.window)
            snap = service.snapshot(window, limit=a.limit)
        except UiaError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Could not read the window: {type(exc).__name__}: {str(exc)[:120]}")
        controls = snap.controls
        if a.query:
            ranked = {c.name for c in controls if entity_match.score(a.query, c.name) >= 0.6}
            controls = [c for c in controls if c.name in ranked]
        return ToolResult(success=True, output={
            "window": window.title, "process": window.process, "count": len(controls), "truncated": snap.truncated,
            "controls": [c.public() for c in controls], "digest": _digest(snap)})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return Verification(passed=False, checks=["window read"], details=result.error)
        out = result.output
        ok = bool(out.get("window")) and out.get("count", 0) >= 0
        return Verification(passed=ok, checks=["the window's controls were read"],
                            evidence={"window": out.get("window"), "controls": out.get("count"), "digest": out.get("digest")})


class UiActTool(BaseTool):
    name = "ui_act"
    description = ("Do one thing in a window the OS can describe: click / type / select / scroll / press_keys / read, naming the control as it is "
                   "shown on screen. Native tools (open_application, files, browser) come first; use this for any other app. "
                   "Clicking Pay, Buy, Delete, Submit, Install or Send, and keys like Alt+F4, need explicit approval.")
    risk_level = RiskLevel.MEDIUM
    args_schema = UiActArgs
    timeout_seconds = 25

    def risk_for_args(self, args: BaseModel | None) -> RiskLevel:
        if args is None:
            return RiskLevel.MEDIUM
        a = UiActArgs.model_validate(args.model_dump())
        if a.action in ("read", "scroll"):
            return RiskLevel.LOW
        if a.action == "press_keys":
            return RiskLevel.HIGH if (a.keys or "").replace(" ", "").casefold() in _CLOSING_KEYS else RiskLevel.MEDIUM
        if a.action == "click" and (is_dangerous(a.control or "") or is_dangerous(a.expect or "")):
            return RiskLevel.HIGH
        if is_shell_target(a.window):
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM

    # -- execute
    def execute(self, args: BaseModel) -> ToolResult:
        a = UiActArgs.model_validate(args.model_dump())
        service = get_service()
        backend = service.backend
        try:
            window = service.window(a.window)
            if a.action != "read" and is_shell_target(window.title, window.process):
                return ToolResult(success=False, error=f"'{window.title}' is a shell or admin tool: nothing is typed or clicked there.")
            if a.action == "press_keys":
                before = service.snapshot(window, fresh=True)
                front = backend.foreground()
                backend.press_keys(window, a.keys or "")
                return ToolResult(success=True, output={"action": a.action, "window": window.title, "handle": window.handle, "keys": a.keys,
                                                        "front_before": front.handle if front else None,
                                                        "digest_before": _digest(before), "title_before": before.window.title})
            before = service.snapshot(window, fresh=True)
            control = None
            if a.action == "scroll" and not (a.control or a.id):
                control = _scroll_target(before)   # the page / list that fills the window; None = the window itself
            else:
                control = resolve_control(before, a)
                refusal = self._refuse(a, control)
                if refusal:
                    return ToolResult(success=False, error=refusal)
            front = backend.foreground()
            out: dict[str, Any] = {"action": a.action, "window": window.title, "handle": window.handle, "digest_before": _digest(before),
                                   "front_before": front.handle if front else None,
                                   "title_before": before.window.title}
            if control is not None:
                out["control"] = {"name": control.name, "type": control.type, "id": control.id}
            if a.action == "click":
                backend.click(window, control)
            elif a.action == "type":
                backend.set_text(window, control, a.text or "")
            elif a.action == "select":
                backend.select(window, control, a.item or "")
            elif a.action == "scroll":
                out["scroll_before"] = backend.scroll_position(window, control)
                backend.scroll(window, control, a.amount)
            elif a.action == "read":
                text = backend.read(window, control)
                out["text"] = text
            return ToolResult(success=True, output=out)
        except UiaError as exc:
            question = getattr(exc, "question", None)
            return ToolResult(success=False, error=str(exc), output={"needs_confirmation": True, "question": question,
                                                                     "candidates": getattr(exc, "options", [])} if question else {})
        except Exception as exc:  # noqa: BLE001 - COM errors are many; the message stays short and honest
            return ToolResult(success=False, error=f"The window did not accept that: {type(exc).__name__}: {str(exc)[:120]}")

    @staticmethod
    def _refuse(a: UiActArgs, control: Control) -> str | None:
        if control.password and a.action in ("type", "read"):
            return "That is a password field: it is never read or filled in by the assistant. Type it yourself."
        if _SECRET.search(control.name or "") and a.action in ("type", "read"):
            return f"'{control.name}' looks like a secret (password, card, PIN, code): the assistant does not read or type those."
        if a.action == "click" and is_dangerous(control.name) and not is_dangerous(a.control or ""):
            return (f"'{control.name}' is a control that sends, deletes, pays or installs. Say its name in the command, so it is "
                    "approved as such.")
        if a.action == "type" and not control.enabled:
            return f"'{control.name}' is disabled."
        if a.action in ("click", "select") and not control.enabled:
            return f"'{control.name}' is disabled right now."
        return None

    # -- verify: look again
    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        a = UiActArgs.model_validate(args.model_dump())
        if not result.success:
            return Verification(passed=False, checks=["action done"], details=result.error)
        out = result.output
        service = get_service()
        try:
            time.sleep(0.15)
            backend = service.backend
            window = next((w for w in backend.windows() if w.handle == out.get("handle")), None)
            if window is None:
                if a.action in ("click", "press_keys"):   # the window closed: that is the observed effect
                    return Verification(passed=True, checks=["the window closed"], evidence={"window_gone": True, "was": out.get("window")})
                return Verification(passed=False, checks=["window read again"], details=f"'{out.get('window')}' is not open any more.")
            deadline = time.monotonic() + _SETTLE_SECONDS
            after = service.snapshot(window, fresh=True)
            if a.action in ("click", "press_keys", "scroll"):
                while (_digest(after) == out.get("digest_before") and not self._new_window(backend, out.get("front_before"))
                       and time.monotonic() < deadline):
                    time.sleep(0.2)
                    after = service.snapshot(window, fresh=True)
            return self._judge(a, out, window, after, service)
        except UiaError as exc:
            return Verification(passed=False, checks=["window read again"], details=str(exc))
        except Exception as exc:  # noqa: BLE001
            return Verification(passed=False, checks=["window read again"], details=f"{type(exc).__name__}: {str(exc)[:120]}")

    @staticmethod
    def _new_window(backend, front_before: int | None) -> str | None:
        """The title of a window that took the focus since the action started (a dialog, a menu, a new window), or None."""
        fg = backend.foreground()
        return fg.title if fg is not None and front_before is not None and fg.handle != front_before else None

    def _judge(self, a: UiActArgs, out: dict, window: WindowRef, after: Snapshot, service: UiaService) -> Verification:
        opened = self._new_window(service.backend, out.get("front_before"))
        changed = _digest(after) != out.get("digest_before") or after.window.title != out.get("title_before") or bool(opened)
        ev: dict[str, Any] = {"window": after.window.title, "changed": changed, "controls_seen": len(after.controls)}
        if opened:
            ev["new_window_in_front"] = opened
        name = (out.get("control") or {}).get("name") or a.control or ""
        if a.expect:
            seen = any(a.expect.casefold() in (f"{c.name} {c.value or ''}").casefold() for c in after.controls)
            ev["expected_text_seen"] = seen
            if not seen:
                return Verification(passed=False, checks=[f"'{a.expect}' is visible"], evidence=ev,
                                    details=f"'{a.expect}' did not appear in '{after.window.title}'.")
        if a.action == "read":
            text = out.get("text") or ""
            ev["chars_read"] = len(text)
            return Verification(passed=bool(text), checks=["the control's text was read"], evidence=ev,
                                details=None if text else "The control has no text.")
        if a.action == "type":
            control = self._same_control(after, a, name, (out.get("control") or {}).get("type"))
            if control is None:
                return Verification(passed=False, checks=["the field still exists"], evidence=ev, details=f"'{name}' is not in the window any more.")
            now = service.backend.read(window, control)
            want = re.sub(r"\s+", " ", (a.text or "")).strip().casefold()
            got = re.sub(r"\s+", " ", now).strip().casefold()
            ev["value_now"] = now[:80]
            ok = want in got
            return Verification(passed=ok, checks=[f"'{name}' now contains the text"], evidence=ev,
                                details=None if ok else f"'{name}' shows '{now[:60]}' instead.")
        if a.action == "select":
            control = self._same_control(after, a, name, (out.get("control") or {}).get("type"))
            seen = bool(control and ((control.value or "").casefold().find((a.item or "").casefold()) >= 0 or control.selected))
            if control is None:   # the list collapsed: look for the item as the selected one anywhere
                seen = any(c.selected and (a.item or "").casefold() in c.name.casefold() for c in after.controls)
            ev["selected_seen"] = seen
            return Verification(passed=seen, checks=[f"'{a.item}' is selected"], evidence=ev,
                                details=None if seen else f"'{a.item}' is not shown as selected.")
        if a.action == "scroll":
            control = self._same_control(after, a, name, (out.get("control") or {}).get("type")) if name else None
            now = service.backend.scroll_position(window, control)
            before = out.get("scroll_before")
            moved = changed or (now is not None and before is not None and abs(now - before) > 0.01)
            ev.update({"scroll_before": before, "scroll_now": now})
            return Verification(passed=bool(moved), checks=["the view moved"], evidence=ev,
                                details=None if moved else "The view did not move (already at the end?).")
        # click / press_keys
        if a.action == "click":
            ev["control"] = name
        return Verification(passed=changed, checks=["the window reacted"], evidence=ev,
                            details=None if changed else "Nothing on screen changed after that, so I cannot say it worked.")

    @staticmethod
    def _same_control(snap: Snapshot, a: UiActArgs, name: str, ctype: str | None = None) -> Control | None:
        """The control that was acted on, found again by name AND the type it had (a label and its field share one name)."""
        want = (a.control_type or ctype or "").casefold()
        pool = [c for c in snap.controls if not want or c.type.casefold() == want]
        exact = [c for c in pool if c.name == name]
        if len(exact) == 1:
            return exact[0]
        entries = [_Entry(c.name) for c in pool if c.name]
        d = entity_match.resolve(name, entries) if name else None
        if d and d.chosen:
            same = [c for c in pool if c.name == d.chosen.name]
            return same[0] if same else None
        return None


def uia_tools(ctx=None) -> list[BaseTool]:
    return [UiInspectTool(ctx), UiActTool(ctx)]
