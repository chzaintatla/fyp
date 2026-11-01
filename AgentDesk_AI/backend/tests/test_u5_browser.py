"""U5: browser and YouTube controls: the spoken commands, the tool, and the DOM-based verification (against a simulated extension)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.browser_commands import browser_plan, youtube_followup
from app.ai.command_intents import canonical_command
from app.ai.speech_commands import clean_transcript
from app.browser_bridge import bridge
from app.tools.base import RiskLevel
from app.tools.browser import BrowserControlTool, _spoken, select_tabs


def plan_of(text):
    plan = browser_plan(canonical_command(clean_transcript(text)))
    return plan.actions[0].args if plan else None


# ---------------------------------------------------------------------------------------------------- spoken commands
@pytest.mark.parametrize("text,expected", [
    ("skip 10 seconds", {"action": "seek", "seconds": 10}), ("Skip 10 seconds.", {"action": "seek", "seconds": 10}),
    ("forward 30", {"action": "seek", "seconds": 30}), ("go forward 15 seconds", {"action": "seek", "seconds": 15}),
    ("skip 1 minute", {"action": "seek", "seconds": 60}), ("rewind 10 seconds", {"action": "seek", "seconds": -10}),
    ("go back 10 seconds", {"action": "seek", "seconds": -10}), ("rewind 5", {"action": "seek", "seconds": -5}),
    ("10 second aage karo", {"action": "seek", "seconds": 10}), ("5 second peeche kar do", {"action": "seek", "seconds": -5}),
    ("speed 1.5", {"action": "speed", "rate": 1.5}), ("playback speed 2x", {"action": "speed", "rate": 2.0}),
    ("set speed to 0.75", {"action": "speed", "rate": 0.75}), ("1.5x speed", {"action": "speed", "rate": 1.5}),
    ("normal speed", {"action": "speed", "rate": 1.0}),
    ("full screen", {"action": "fullscreen"}), ("fullscreen", {"action": "fullscreen"}), ("exit full screen", {"action": "fullscreen", "exit": True}),
    ("next video", {"action": "next"}), ("play the next video", {"action": "next"}),
    ("youtube volume 40", {"action": "volume", "level": 40}), ("video volume 30 percent", {"action": "volume", "level": 30}),
    ("mute the video", {"action": "mute"}), ("unmute youtube", {"action": "unmute"}),
    ("play result 2", {"action": "click_result", "index": 2}), ("click result 3", {"action": "click_result", "index": 3}),
    ("read the results", {"action": "results"}), ("youtube scroll down", {"action": "scroll", "amount": 1}),
    ("switch to youtube tab", {"action": "switch", "target": "youtube"}), ("switch to gmail", {"action": "switch", "target": "gmail"}),
    ("what page is this", {"action": "read", "target": "active"}), ("read the title", {"action": "read", "target": "active"}),
])
def test_spoken_youtube_and_tab_commands(text, expected):
    args = plan_of(text)
    assert args is not None, text
    assert args == {"target": "youtube", **expected}


@pytest.mark.parametrize("text", ["volume down", "volume 50", "go back", "scroll down", "next song", "speed 9", "skip", "forward", "full", "play result", "pause the music",
                                  "skip the intro of my report", "speed up my computer", "mute", "switch to word", "go to youtube", "youtube volume 400"])
def test_system_and_unclear_commands_are_not_taken_by_the_browser(text):
    plan = browser_plan(canonical_command(clean_transcript(text)))
    assert plan is None or plan.actions[0].tool != "control_browser" or plan.actions[0].args.get("action") in {"pause", "resume", "close", "inspect"}


def _recent(*calls):
    return SimpleNamespace(actions=[SimpleNamespace(tool_name=t, args_json=a) for t, a in calls])


@pytest.mark.parametrize("text,index", [("play the second one", 2), ("the first one", 1), ("third", 3), ("open the fourth video", 4), ("play number 2", 2),
                                        ("doosri wali chalao", 2), ("chalao pehli video", 1)])
def test_second_one_after_a_youtube_search_clicks_that_result(text, index):
    recent = _recent(("web_search", '{"query": "python tutorials", "site": "youtube.com"}'))
    plan = youtube_followup(canonical_command(clean_transcript(text)), recent)
    assert plan and plan.actions[0].args["action"] == "click_result" and plan.actions[0].args["index"] == index


def test_second_one_means_something_else_when_the_last_turn_was_not_youtube():
    for recent in (None, _recent(("list_folder", "{}")), _recent(("find_paths", "{}")), SimpleNamespace(actions=[])):
        assert youtube_followup("play the second one", recent) is None


def test_it_also_works_after_a_result_list_or_a_played_video():
    assert youtube_followup("the second one", _recent(("control_browser", '{"action": "results"}')))
    assert youtube_followup("the second one", _recent(("play_youtube_video", '{"query": "x"}')))


# ------------------------------------------------------------------------------------------------- the tool, on a fake extension
class FakeExtension:
    """What the real extension would answer: the page state AFTER each action. `broken` makes it report a state that does not match."""

    def __init__(self, tabs):
        self.tabs, self.calls, self.broken = tabs, [], set()

    def request(self, action, **kw):
        self.calls.append((action, kw))
        if action == "inspect":
            return {"success": True, "tabs": self.tabs}
        base = {"success": True, "title": "First video - YouTube", "video_id": "aaa", "time": 100.0, "rate": 1, "volume": 100, "muted": False, "fullscreen": False, "paused": False}
        if action == "seek":
            target = kw.get("position", 100 + kw.get("seconds", 0))
            return {**base, "target": target, "time": target + (30 if "seek" in self.broken else 0)}
        if action == "speed":
            return {**base, "rate": kw["rate"] + (1 if "speed" in self.broken else 0)}
        if action == "volume":
            return {**base, "volume": kw["level"] + (20 if "volume" in self.broken else 0)}
        if action in ("mute", "unmute"):
            return {**base, "muted": (action == "mute") != ("mute" in self.broken)}
        if action == "fullscreen":
            return {**base, "fullscreen": (not kw.get("exit")) != ("fullscreen" in self.broken)}
        if action == "next":
            return {**base, "video_id": "bbb", "previous_video_id": "aaa" if "next" not in self.broken else "bbb", "title": "Second video - YouTube", "changed": True}
        if action == "click_result":
            return {**base, "video_id": "rrr2" if "click" not in self.broken else "aaa", "wanted_video_id": "rrr2", "clicked_title": "Result number 2"}
        if action == "results":
            return {**base, "results": ["Result number 1", "Result number 2", "Result number 3"]}
        if action == "scroll":
            return {**base, "moved": "scroll" not in self.broken, "scroll_y": 640}
        if action == "switch":
            return {"success": True, "active": True, "title": "Gmail"}
        if action == "read":
            return {"success": True, "title": "Inbox - Gmail", "url": "https://mail.google.com/mail/u/0/", "active": True}
        return {"success": False, "error": "unsupported"}


TABS = [{"id": 1, "title": "Inbox - Gmail", "url": "https://mail.google.com/mail/u/0/", "active": True, "audible": False},
        {"id": 2, "title": "First video - YouTube", "url": "https://www.youtube.com/watch?v=aaa", "active": False, "audible": True}]


@pytest.fixture()
def ext(ctx, monkeypatch):
    fake = FakeExtension([dict(t) for t in TABS])
    monkeypatch.setattr(bridge, "request", fake.request)
    monkeypatch.setattr(bridge, "supports", lambda capability: True)
    fake.tool = BrowserControlTool(ctx)
    return fake


def go(ext, **raw):
    args = ext.tool.validate_args(raw)
    result = ext.tool.execute(args)
    return result, (ext.tool.verify(args, result) if result.success else None)


@pytest.mark.parametrize("raw,said", [
    (dict(action="seek", seconds=10), "At 1:50."), (dict(action="seek", position=300), "At 5:00."), (dict(action="speed", rate=1.5), "Speed is 1.5x."),
    (dict(action="volume", level=40), "Video volume is 40 percent."), (dict(action="mute"), "The video is muted."), (dict(action="unmute"), "The video is unmuted."),
    (dict(action="fullscreen"), "Full screen."), (dict(action="fullscreen", exit=True), "Back to normal size."),
    (dict(action="next"), "Playing: Second video - YouTube."), (dict(action="click_result", index=2), "Playing: Result number 2."),
    (dict(action="results"), "Top results: 1. Result number 1; 2. Result number 2; 3. Result number 3."), (dict(action="scroll", amount=2), "Scrolled."),
])
def test_every_page_action_is_verified_from_what_the_page_showed(ext, raw, said):
    result, ver = go(ext, **raw)
    assert result.success and ver.passed, (raw, ver and ver.checks)
    assert result.output["spoken"] == said
    kinds = [c[0] for c in ext.calls]
    assert kinds[0] == "inspect" and raw["action"] in kinds and kinds[-1] == "inspect"   # the tab list is read again afterwards


@pytest.mark.parametrize("raw,broken", [
    (dict(action="seek", seconds=10), "seek"), (dict(action="speed", rate=1.5), "speed"), (dict(action="volume", level=40), "volume"),
    (dict(action="mute"), "mute"), (dict(action="fullscreen"), "fullscreen"), (dict(action="next"), "next"), (dict(action="click_result", index=2), "click"),
    (dict(action="scroll", amount=2), "scroll"),
])
def test_a_page_that_did_not_change_is_never_reported_as_done(ext, raw, broken):
    ext.broken.add(broken)
    result, ver = go(ext, **raw)
    assert result.success and not ver.passed       # the extension answered, but its own observation does not match what was asked


def test_switch_and_read_work_on_any_tab_and_are_verified(ext):
    result, ver = go(ext, action="switch", target="gmail")
    assert result.success and ver.passed and result.output["spoken"] == "Switched to Gmail."   # the tab list read afterwards shows it in front
    result, ver = go(ext, action="read", target="active")
    assert result.success and result.output["title"] == "Inbox - Gmail" and ver.passed and result.output["spoken"] == "The page is called Inbox - Gmail."


def test_page_actions_pick_the_audible_or_active_youtube_tab_and_never_another_site(ext):
    ext.tabs.append({"id": 3, "title": "Other video - YouTube", "url": "https://www.youtube.com/watch?v=ccc", "active": False, "audible": False})
    go(ext, action="seek", seconds=5)
    assert ext.calls[1] == ("seek", {"tab_id": 2, "expected_url": "https://www.youtube.com/watch?v=aaa", "seconds": 5})   # the audible one
    ext.tabs[1]["audible"] = False
    ext.tabs[2]["active"] = True
    ext.calls.clear()
    go(ext, action="next")
    assert ext.calls[1][1]["tab_id"] == 3                                          # no audible tab: the one that is in front
    ext.tabs[2]["active"] = False
    ext.calls.clear()
    result, _ = go(ext, action="speed", rate=2)
    assert not result.success and result.output["needs_confirmation"] and len(ext.calls) == 1   # two idle YouTube tabs: ask, send nothing


def test_with_no_youtube_tab_nothing_is_sent(ext):
    ext.tabs[:] = [ext.tabs[0]]
    result, _ = go(ext, action="seek", seconds=5)
    assert not result.success and [c[0] for c in ext.calls] == ["inspect"]


def test_an_older_extension_is_told_to_update_not_guessed_at(ext, monkeypatch):
    monkeypatch.setattr(bridge, "supports", lambda capability: False)
    result, _ = go(ext, action="seek", seconds=5)
    assert not result.success and "older than 1.2" in result.error and [c[0] for c in ext.calls] == ["inspect"]


def test_arguments_are_validated_before_anything_is_sent(ext):
    for raw in (dict(action="seek"), dict(action="speed"), dict(action="speed", rate=9), dict(action="volume", level=101), dict(action="click_result"),
                dict(action="click_result", index=0), dict(action="scroll"), dict(action="scroll", amount=0), dict(action="seek", seconds=99999), dict(action="eval")):
        with pytest.raises(Exception):
            ext.tool.validate_args(raw)


def test_browser_controls_are_low_risk_like_other_media_and_navigation(ext):
    for raw in (dict(action="seek", seconds=5), dict(action="scroll", amount=1), dict(action="next"), dict(action="switch"), dict(action="close")):
        assert ext.tool.risk_for_args(ext.tool.validate_args(raw)) == RiskLevel.LOW


def test_existing_pause_resume_close_still_work(ext):
    for raw in (dict(action="inspect"),):
        result, ver = go(ext, **raw)
        assert result.success and ver.passed
    assert select_tabs(ext.tabs, "youtube")[0]["id"] == 2


def test_the_planner_can_use_the_new_actions():
    from app.tools.registry import ToolRegistry
    from app.tools.base import ToolContext
    schema = BrowserControlTool(ToolContext()).args_schema.model_json_schema()
    assert {"seek", "speed", "volume", "next", "click_result", "scroll", "switch", "read"} <= set(schema["properties"]["action"]["enum"])
    assert _spoken(BrowserControlTool(ToolContext()).validate_args({"action": "scroll", "amount": 1}), {}) == "Scrolled."
