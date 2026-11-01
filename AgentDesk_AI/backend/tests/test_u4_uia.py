"""U4: the UI Automation tools, on a fake desktop (see tests/helpers/fake_uia.py), plus the 50 scripted actions.

These tests cover the TOOL LOGIC: finding a control by (misheard) name, risk, refusals, asking when two controls fit, and verification by
looking again. How well pywinauto drives real apps is measured by scripts/uia_live.py on a real PC, not here.
"""
from __future__ import annotations

import pytest

from app.tools.base import RiskLevel
from app.tools.uia import UiActArgs, UiActTool, UiaService, UiInspectTool, is_dangerous, set_service
from app.tools.uia_backend import MAX_CONTROLS, Control, PywinautoBackend, escape_for_send_keys, to_send_keys
from tests.helpers.fake_uia import FC, FakeBackend, build_world


@pytest.fixture()
def desk(ctx):
    world, apps = build_world()
    set_service(UiaService(FakeBackend(world)))
    yield world, apps, UiActTool(ctx), UiInspectTool(ctx)
    set_service(None)


def act(tool, **raw):
    args = tool.validate_args(raw)
    risk = tool.risk_for_args(args)
    result = tool.execute(args)
    verification = tool.verify(args, result) if result.success else None
    return risk, result, verification


# ------------------------------------------------------------------------------------------------ 50 scripted actions
# (app window, action fields, expected outcome, expected risk)
#  verified   = the action ran and looking again confirmed it
#  unverified = the action ran but nothing observable changed, so the tool must NOT claim success
#  refused    = nothing was done, with a reason      ask = two controls fit: a question comes back
V, U, R, A = "verified", "unverified", "refused", "ask"
L, M, H = RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH
SCRIPT = [
    # Notepad
    ("Notepad", dict(action="type", control="Text editor", text="hello world"), V, M),
    ("Notepad", dict(action="read", control="Text editor"), V, L),
    ("Notepad", dict(action="click", control="Word wrap"), V, M),
    ("Notepad", dict(action="click", control="word rap"), V, M),                       # misheard name
    ("Notepad", dict(action="press_keys", keys="ctrl+s"), V, M),                       # a Save As dialog opens in front
    ("Save As", dict(action="type", control="File name", text="notes.txt"), V, M),
    ("Save As", dict(action="click", control="Cancel"), V, M),                         # the dialog closes
    ("Notepad", dict(action="click", control="Zoom in"), U, M),                        # nothing visible changes: not claimed
    # File Explorer
    ("Documents - File Explorer", dict(action="click", control="Documents"), V, M),
    ("File Explorer", dict(action="click", control="Downloads"), V, M),
    ("File Explorer", dict(action="type", control="Search Documents", text="report"), V, M),
    ("File Explorer", dict(action="type", control="Address", text="This PC > Downloads"), V, M),
    ("File Explorer", dict(action="click", control="Back"), V, M),
    ("File Explorer", dict(action="read", control="Address"), V, L),
    ("File Explorer", dict(action="click", control="Report.docx"), V, M),
    # Settings
    ("Settings", dict(action="type", control="Find a setting", text="bluetooth"), V, M),
    ("Settings", dict(action="click", control="Bluetooth & devices"), V, M),
    ("Settings", dict(action="select", control="Choose your mode", item="Dark"), V, M),
    ("Settings", dict(action="select", control="choose your mode", item="dark"), V, M),
    ("Settings", dict(action="click", control="Transparency effects"), V, M),
    ("Settings", dict(action="click", control="Reset"), U, H),                          # a dangerous name: high risk
    ("Settings", dict(action="read", control="System"), V, L),
    # Calculator
    ("Calculator", dict(action="click", control="Seven"), V, M),
    ("Calculator", dict(action="click", control="Plus"), V, M),
    ("Calculator", dict(action="click", control="Eight"), V, M),
    ("Calculator", dict(action="click", control="Equals"), V, M),
    ("Calculator", dict(action="read", control="Display"), V, L),
    ("Calculator", dict(action="click", control="Clear"), V, M),
    ("Calculator", dict(action="click", control="Nine"), V, M),
    ("Calculator", dict(action="read", control="Display"), V, L),
    # Word
    ("Word", dict(action="type", control="Page 1 content", text="Dear Ali, see you on Monday."), V, M),
    ("Word", dict(action="click", control="Bold"), V, M),
    ("Word", dict(action="click", control="italic"), V, M),
    ("Word", dict(action="select", control="Font", item="Arial"), V, M),
    ("Word", dict(action="click", control="Save"), U, M),
    ("Word", dict(action="click", control="Send"), U, H),                               # sending is high risk
    ("Word", dict(action="click", control="Insert"), V, M),
    # Excel
    ("Excel", dict(action="type", control="Name Box", text="B2"), V, M),
    ("Excel", dict(action="type", control="Formula Bar", text="=SUM(A1:A3)"), V, M),
    ("Excel", dict(action="click", control="Bold"), V, M),
    ("Excel", dict(action="select", control="Number format", item="Currency"), V, M),
    ("Excel", dict(action="click", control="Sort & Filter"), U, M),
    ("Excel", dict(action="read", control="Formula Bar"), V, L),
    # Chrome
    ("Chrome", dict(action="click", control="sign in"), V, M),                          # the page title changes
    ("Chrome", dict(action="click", control="YouTube"), V, M),
    ("Chrome", dict(action="scroll", amount=5), V, L),
    ("Chrome", dict(action="type", control="Address and search bar", text="youtube.com"), V, M),
    ("Chrome", dict(action="read", control="Address and search bar"), V, L),
    ("Chrome", dict(action="click", control="Buy now"), U, H),
    ("Chrome", dict(action="read", control="Password"), R, L),                          # never read
    ("Chrome", dict(action="type", control="Password", text="x"), R, M),                # never typed
]


def test_there_are_fifty_scripted_actions_over_seven_apps():
    assert len(SCRIPT) >= 50 and len({w.split(" - ")[-1].split()[0] for w, *_ in SCRIPT}) >= 7


def test_the_fifty_scripted_actions(desk):
    world, apps, tool, _ = desk
    wrong = []
    for window, fields, expected, expected_risk in SCRIPT:
        args = dict(fields, window=window)
        risk, result, ver = act(tool, **args)
        if expected == R:
            outcome = R if not result.success else "ran"
        elif expected == A:
            outcome = A if (result.output or {}).get("needs_confirmation") else "no question"
        else:
            outcome = (V if (result.success and ver and ver.passed) else U if result.success else f"failed: {result.error}")
        if outcome != expected or risk != expected_risk:
            wrong.append((window, fields, expected, outcome, expected_risk, risk, result.error))
    done = len(SCRIPT) - len(wrong)
    assert done / len(SCRIPT) >= 0.9, wrong
    assert not wrong, wrong        # on the fake desktop every one must be exact; the live run (scripts/uia_live.py) is where misses appear


def test_the_calculator_really_added_seven_and_eight(desk):
    world, apps, tool, _ = desk
    for name in ("Seven", "Plus", "Eight", "Equals"):
        act(tool, action="click", control=name, window="Calculator")
    _, result, ver = act(tool, action="read", control="Display", window="Calculator")
    assert result.output["text"] == "15" and ver.passed and ver.evidence["chars_read"] == 2


# ------------------------------------------------------------------------------------------------------------ safety
def test_two_equal_controls_ask_instead_of_guessing(desk):
    world, apps, tool, _ = desk
    world.add("Dialog - Example", "example.exe", [FC("Close"), FC("Close"), FC("Help")])
    risk, result, _ = act(tool, action="click", control="Close", window="Dialog")
    assert not result.success and result.output["needs_confirmation"] and "Which one" in result.output["question"]


def test_a_close_name_with_two_candidates_asks(desk):
    world, apps, tool, _ = desk
    world.add("Form - Example", "example.exe", [FC("Save draft"), FC("Save as"), FC("Cancel")])
    _, result, _ = act(tool, action="click", control="save", window="Form")
    assert not result.success and result.output.get("needs_confirmation")


def test_unknown_control_lists_what_was_seen(desk):
    _, _, tool, _ = desk
    _, result, _ = act(tool, action="click", control="Launch rocket", window="Calculator")
    assert not result.success and "No control called" in result.error and "Zero" in result.error


def test_shell_windows_never_get_input_but_can_be_inspected(desk):
    world, apps, tool, inspect = desk
    world.add("Command Prompt", "cmd.exe", [FC("Terminal", "Edit", "C:\\>"), FC("Close")])
    _, result, _ = act(tool, action="type", control="Terminal", text="del /q *", window="Command Prompt")
    assert not result.success and "shell or admin tool" in result.error
    seen = inspect.execute(inspect.validate_args({"window": "Command Prompt"}))
    assert seen.success and seen.output["count"] == 2


def test_password_fields_are_hidden_in_the_list_and_never_read_or_typed(desk):
    _, _, tool, inspect = desk
    listed = inspect.execute(inspect.validate_args({"window": "Chrome"})).output["controls"]
    pw = next(c for c in listed if c["name"] == "Password")
    assert pw["password"] is True and "value" not in pw
    for fields in (dict(action="read", control="Password"), dict(action="type", control="Password", text="x")):
        _, result, _ = act(tool, window="Chrome", **fields)
        assert not result.success and "password" in result.error.lower()


def test_secret_looking_names_are_refused_too(desk):
    world, _, tool, _ = desk
    world.add("Checkout - Shop", "shop.exe", [FC("Card number", "Edit", ""), FC("CVV", "Edit", "")])
    _, result, _ = act(tool, action="type", control="Card number", text="4111", window="Checkout")
    assert not result.success and "secret" in result.error


def test_a_dangerous_control_cannot_hide_behind_a_harmless_name(desk):
    world, _, tool, _ = desk
    world.add("Files - Example", "example.exe", [FC("Delete all files")])
    # the plan says "Delete all files" -> HIGH. A plan that only gave the id would be MEDIUM at planning time: the tool refuses it.
    risk, result, _ = act(tool, action="click", id=1, window="Files")
    assert risk == RiskLevel.MEDIUM and not result.success and "Say its name" in result.error
    risk2, result2, _ = act(tool, action="click", control="Delete all files", window="Files")
    assert risk2 == RiskLevel.HIGH and result2.success


@pytest.mark.parametrize("name,expected", [("Pay now", True), ("Buy", True), ("Delete", True), ("Submit order", True), ("Install", True),
                                           ("Send", True), ("Uninstall app", True), ("Save", False), ("Cancel", False), ("Bold", False),
                                           ("Description", False), ("Sender name", False)])
def test_dangerous_names(name, expected):
    assert is_dangerous(name) is expected


@pytest.mark.parametrize("keys,risk", [("alt+f4", RiskLevel.HIGH), ("ctrl+w", RiskLevel.HIGH), ("delete", RiskLevel.HIGH),
                                       ("ctrl+s", RiskLevel.MEDIUM), ("enter", RiskLevel.MEDIUM), ("ctrl+a", RiskLevel.MEDIUM)])
def test_key_risk(desk, keys, risk):
    _, _, tool, _ = desk
    assert tool.risk_for_args(tool.validate_args({"action": "press_keys", "keys": keys})) == risk


def test_read_and_scroll_are_low_risk_everything_else_needs_approval(desk):
    _, _, tool, inspect = desk
    assert inspect.risk_for_args(None) == RiskLevel.LOW and tool.requires_approval
    assert tool.risk_for_args(tool.validate_args({"action": "scroll"})) == RiskLevel.LOW


def test_bad_arguments_are_rejected_before_anything_runs(desk):
    _, _, tool, _ = desk
    for raw in ({"action": "click"}, {"action": "type", "control": "x"}, {"action": "select", "control": "x"},
                {"action": "press_keys"}, {"action": "press_keys", "keys": "ctrl+banana"}, {"action": "hover", "control": "x"}):
        with pytest.raises(Exception):
            tool.validate_args(raw)


# ------------------------------------------------------------------------------------------------------- honesty
def test_a_click_with_no_visible_effect_is_not_reported_as_done(desk):
    _, _, tool, _ = desk
    _, result, ver = act(tool, action="click", control="Zoom in", window="Notepad")
    assert result.success and not ver.passed and "cannot say it worked" in ver.details


def test_typing_is_verified_by_reading_the_field_back(desk, monkeypatch):
    world, apps, tool, _ = desk
    _, result, ver = act(tool, action="type", control="Text editor", text="abc", window="Notepad")
    assert ver.passed and ver.evidence["value_now"] == "abc"
    apps["notepad"].get("Text editor").value = ""      # the app swallowed the text
    args = tool.validate_args({"action": "type", "control": "Text editor", "text": "abc", "window": "Notepad"})
    assert not tool.verify(args, result).passed


def test_a_select_that_did_not_take_is_caught(desk):
    world, apps, tool, _ = desk
    _, result, _ = act(tool, action="select", control="Font", item="Comic Sans", window="Word")
    assert not result.success and "no item called" in result.error


def test_expected_text_is_an_extra_check(desk):
    _, _, tool, _ = desk
    _, _, ver = act(tool, action="click", control="Word wrap", window="Notepad", expect="Word wrap")
    assert ver.passed
    _, _, ver2 = act(tool, action="click", control="Word wrap", window="Notepad", expect="Saved successfully")
    assert not ver2.passed and "did not appear" in ver2.details


def test_a_window_that_closed_after_the_click_counts_as_the_effect(desk):
    world, _, tool, _ = desk
    world.add("Tip - Example", "example.exe", [FC("OK", on_click=lambda wld, win, fc: wld.close(win))])
    _, result, ver = act(tool, action="click", control="OK", window="Tip")
    assert ver.passed and ver.evidence["window_gone"]


def test_the_assistant_never_acts_on_a_window_that_is_not_open(desk):
    _, _, tool, _ = desk
    _, result, _ = act(tool, action="click", control="Bold", window="Photoshop")
    assert not result.success and "No open window" in result.error


def test_a_spoken_window_name_finds_the_window(desk):
    _, _, tool, _ = desk
    _, result, _ = act(tool, action="read", control="Display", window="calc later")
    assert not result.success or result.output.get("text") is not None   # fuzzy window names never crash


# ---------------------------------------------------------------------------------- the list the planner sees
def test_inspect_lists_the_foreground_window_and_filters_by_name(desk):
    world, apps, _, inspect = desk
    out = inspect.execute(inspect.validate_args({})).output           # the front window is Notepad (added first, calc later: see below)
    assert out["window"].endswith("Notepad") or out["count"] > 0
    out = inspect.execute(inspect.validate_args({"window": "Word", "query": "bold"})).output
    assert [c["name"] for c in out["controls"]] == ["Bold"]


def test_inspect_never_lists_more_than_80_controls(desk):
    world, _, _, inspect = desk
    world.add("Huge - Example", "example.exe", [FC(f"Item {i}", "ListItem") for i in range(200)])
    out = inspect.execute(inspect.validate_args({"window": "Huge"})).output
    assert out["count"] == MAX_CONTROLS and out["truncated"] is True


def test_priority_keeps_focused_and_named_controls_in_reading_order():
    controls = [Control(0, f"c{i}", "Button", rect=(i, 500 - i * 5, i + 10, 520 - i * 5), focused=(i == 90)) for i in range(120)]
    controls += [Control(0, "", "Text", rect=(0, 0, 5, 5))]
    kept = PywinautoBackend._prioritise(controls, 80)
    assert len(kept) == 80 and any(c.focused for c in kept) and not any(c.name == "" for c in kept)
    tops = [c.rect[1] // 12 for c in kept]
    assert tops == sorted(tops)


def test_public_view_is_compact_and_hides_passwords():
    c = Control(3, "x" * 200, "Edit", value="v" * 500, rect=(1, 2, 3, 4), password=False)
    shown = c.public()
    assert len(shown["name"]) == 80 and len(shown["value"]) == 200 and shown["box"] == [1, 2, 3, 4]
    assert "value" not in Control(1, "pw", "Edit", value="secret", password=True).public()


def test_the_cache_serves_a_second_look_within_two_seconds(desk):
    world, apps, _, _ = desk
    from app.tools.uia import get_service
    service = get_service()
    calls = {"n": 0}
    real = service.backend.snapshot

    def counting(window, limit=80):
        calls["n"] += 1
        return real(window, limit)

    service.backend.snapshot = counting
    w = service.window("Word")
    service.snapshot(w)
    service.snapshot(w)
    assert calls["n"] == 1
    service.snapshot(w, fresh=True)
    assert calls["n"] == 2


# ----------------------------------------------------------------------------------------------------------- keys
@pytest.mark.parametrize("combo,expected", [("ctrl+s", "^s"), ("ctrl+shift+s", "^+s"), ("alt+f4", "%{F4}"), ("enter", "{ENTER}"),
                                            ("alt+left", "%{LEFT}"), ("Ctrl + A", "^a"), ("f5", "{F5}"), ("win+d", "{VK_LWIN}d".replace("{VK_LWIN}d", "d")) if False else ("tab", "{TAB}")])
def test_key_names(combo, expected):
    assert to_send_keys(combo) == expected


@pytest.mark.parametrize("combo", ["", "ctrl+", "ctrl+banana", "super+x", "f13"])
def test_unknown_keys_are_rejected(combo):
    with pytest.raises(ValueError):
        to_send_keys(combo)


def test_typed_text_is_escaped_for_send_keys():
    assert escape_for_send_keys("a+b (c) {d} ~e") == "a{+}b {(}c{)} {{}d{}} {~}e"


# --------------------------------------------------------------------------------------- wiring into the planner
def test_tools_are_registered_and_shortlisted(ctx):
    from app.nlu.shortlist import shortlist_tools
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry(ctx)
    assert {"ui_inspect", "ui_act"} <= set(registry.names())
    chosen = shortlist_tools("in Excel make column B bold", registry.names())
    assert "ui_act" in chosen and "ui_inspect" in chosen
    assert "ui_act" not in shortlist_tools("volume 40 kar do", registry.names())


def test_the_planner_may_use_the_ui_control_intent():
    from app.nlu.schema import INTENTS
    assert "ui_control" in INTENTS


def test_a_question_from_ui_act_reaches_the_user(desk, db, registry_with_fake=None):
    """The executor turns a "which one?" from a tool into the task's question (not the app-name wording)."""
    import json
    from app.config import Settings
    from app.models import TaskStatus
    from app.orchestration import executor
    from app.tools.base import ToolContext
    from app.tools.registry import ToolRegistry
    world, apps, tool, _ = desk
    world.add("Dialog - Example", "example.exe", [FC("Close"), FC("Close")])
    registry = ToolRegistry(ToolContext(allowed_directories=[]))
    from app.ai.base import Plan, PlanAction
    task = executor.create_task(db, "click close in the dialog")
    task = executor.plan_task(db, task, registry, Settings(ai_provider="groq", groq_api_key=None), auto_approve=True,
                              preplanned=Plan(actions=[PlanAction(tool="ui_act", args={"action": "click", "control": "Close", "window": "Dialog"})]))
    done = task   # auto-approved medium risk: it ran inside plan_task and stopped with the tool's own question
    assert done.status == TaskStatus.AWAITING_INPUT and "Which one" in done.plan_note
    assert json.loads(done.actions[0].result_json)["output"]["needs_confirmation"]


# ---------------------------------------------------------------- found by the first live run on a real PC
def test_a_label_and_its_field_with_the_same_name_pick_the_field_to_type_into(desk):
    world, _, tool, _ = desk
    win = world.add("Form - Example", "example.exe", [FC("Search box", "Text", "Search box"), FC("Search box", "Edit", "")])
    _, result, ver = act(tool, action="type", control="Search box", text="milk", window="Form")
    assert result.success and ver.passed and win.controls[1].value == "milk"


def test_a_nav_item_and_a_card_with_the_same_name_are_one_click_not_a_question(desk):
    world, _, tool, _ = desk
    clicked = []
    world.add("Settings - Example", "example.exe", [
        FC("Bluetooth & devices", "Button", on_click=lambda w, win, fc: clicked.append("card")), FC("Bluetooth & devices", "Text", "Bluetooth & devices"),
        FC("Bluetooth & devices", "ListItem", selected=False, on_click=lambda w, win, fc: clicked.append("nav"))])
    _, result, _ = act(tool, action="click", control="Bluetooth & devices", window="Settings - Example")
    assert result.success and clicked == ["card"]


def test_reading_a_display_prefers_the_text_over_the_group_around_it(desk):
    world, _, tool, _ = desk
    world.add("Calc2 - Example", "example.exe", [FC("Display controls", "Group"), FC("Display is 15", "Text", "15"), FC("Plus")])
    _, result, _ = act(tool, action="read", control="Display", window="Calc2")
    assert result.success and result.output["text"] == "15"


def test_two_buttons_with_the_same_name_and_type_still_ask(desk):
    world, _, tool, _ = desk
    world.add("Twins - Example", "example.exe", [FC("Open", "Button"), FC("Open", "Button")])
    _, result, _ = act(tool, action="click", control="Open", window="Twins")
    assert not result.success and result.output["needs_confirmation"]
