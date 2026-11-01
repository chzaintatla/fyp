"""Exact browser tab matching and idempotent YouTube pause/resume."""
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, model_validator
from ..browser_bridge import bridge
from ..verification import failed_result, verdict
from .base import BaseTool, RiskLevel, ToolResult, Verification

SITES = {
    "youtube": {"youtube.com"}, "google drive": {"drive.google.com"}, "drive": {"drive.google.com"},
    "gmail": {"mail.google.com"}, "whatsapp": {"web.whatsapp.com"},
    "openai": {"openai.com"}, "chatgpt": {"chatgpt.com"},
}


def select_tabs(tabs, target):
    target = target.strip().casefold()
    if target in SITES:
        domains = SITES[target]
        return [t for t in tabs if any((urlsplit(t.get("url", "")).hostname or "").casefold() == host or (urlsplit(t.get("url", "")).hostname or "").casefold().endswith("." + host) for host in domains)]
    # Exact title or URL only for unfamiliar names; never arbitrary substring closing.
    return [t for t in tabs if target and target in {t.get("title", "").casefold(), t.get("url", "").casefold()}]


def _clean_tabs(raw) -> list[dict]:
    """The extension is outside this process; accept only well-formed tab records."""
    tabs = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool):
            tabs.append({
                "id": item["id"],
                "title": str(item.get("title") or "")[:300],
                "url": str(item.get("url") or "")[:2000],
                "active": bool(item.get("active")),
                "audible": bool(item.get("audible")),
            })
    return tabs


def public_tab(tab: dict) -> dict:
    """Tab info that may leave the backend (UI, AI prompts): URLs lose query and fragment,
    which often carry session tokens or private search text."""
    parts = urlsplit(tab.get("url", ""))
    shown = f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme and parts.netloc else ""
    return {**tab, "url": shown}


# What each new action needs, and what the page must show afterwards (the extension reads the page again and sends what it saw).
PAGE_ACTIONS = {"seek", "speed", "volume", "mute", "unmute", "fullscreen", "next", "results", "click_result", "scroll"}
YOUTUBE_ONLY = PAGE_ACTIONS | {"pause", "resume"}


class BrowserControlArgs(BaseModel):
    action: Literal["inspect", "close", "pause", "resume", "switch", "read", "seek", "speed", "volume", "mute", "unmute", "fullscreen", "next",
                    "results", "click_result", "scroll"]
    target: str = Field(default="youtube", max_length=500)
    tab_id: int | None = Field(default=None, ge=0)
    seconds: int | None = Field(default=None, ge=-3600, le=3600, description="seek: seconds forward (+) or back (-)")
    position: int | None = Field(default=None, ge=0, le=86400, description="seek: go to this second of the video")
    rate: float | None = Field(default=None, ge=0.25, le=4, description="speed: playback speed, 1 = normal")
    level: int | None = Field(default=None, ge=0, le=100, description="volume: the video player's volume, 0-100")
    index: int | None = Field(default=None, ge=1, le=20, description="click_result: which search result, counting from 1")
    amount: int | None = Field(default=None, ge=-10, le=10, description="scroll: pages down (+) or up (-)")
    exit: bool = Field(default=False, description="fullscreen: true leaves full screen")

    @model_validator(mode="after")
    def _needs(self):
        need = {"seek": (self.seconds is not None) or (self.position is not None), "speed": self.rate is not None, "volume": self.level is not None,
                "click_result": self.index is not None, "scroll": bool(self.amount)}
        if self.action in need and not need[self.action]:
            raise ValueError(f"{self.action} needs " + {"seek": "seconds or position", "speed": "rate", "volume": "level", "click_result": "index",
                                                        "scroll": "a non-zero amount"}[self.action])
        return self


def _spoken(a: BrowserControlArgs, out: dict) -> str:
    """One short sentence to say aloud. Only what the page showed, never what was merely asked."""
    title = str(out.get("title") or "")[:70]
    if a.action == "seek":
        return f"At {int(out.get('time') or 0) // 60}:{int(out.get('time') or 0) % 60:02d}."
    if a.action == "speed":
        return f"Speed is {out.get('rate'):g}x."
    if a.action == "volume":
        return f"Video volume is {out.get('volume')} percent."
    if a.action in {"mute", "unmute"}:
        return "The video is muted." if out.get("muted") else "The video is unmuted."
    if a.action == "fullscreen":
        return "Full screen." if out.get("fullscreen") else "Back to normal size."
    if a.action == "next":
        return f"Playing: {title}."
    if a.action == "click_result":
        return f"Playing: {str(out.get('clicked_title') or title)[:70]}."
    if a.action == "results":
        names = [str(x)[:40] for x in (out.get("results") or [])[:3]]
        return "Top results: " + "; ".join(f"{i}. {n}" for i, n in enumerate(names, 1)) + "." if names else "No results found."
    if a.action == "scroll":
        return "Scrolled."
    if a.action == "switch":
        return f"Switched to {title}."
    if a.action == "read":
        return f"The page is called {title}."
    return ""


class BrowserControlTool(BaseTool):
    name = "control_browser"
    description = ("Inspect actual browser tabs, close or switch to one exact site/title/tab ID, read a tab's title, or control the YouTube tab: pause, "
                   "resume, seek (skip seconds), speed, volume, mute, fullscreen, next video, list search results, click result N, scroll. "
                   "Requires the Agent X Browser Bridge extension. Multiple matches require clarification. Never substitutes an unrelated tab.")
    args_schema = BrowserControlArgs
    risk_level = RiskLevel.LOW
    isolation = "thread"  # the extension socket lives in the server process

    def execute(self, args):
        a = BrowserControlArgs.model_validate(args.model_dump())
        observed = bridge.request("inspect")
        if not observed.get("success"):
            return ToolResult(success=False, error=observed.get("error", "Browser inspect failed"))
        tabs = _clean_tabs(observed.get("tabs"))
        if a.action == "inspect":
            return ToolResult(success=True, output={"tabs": [public_tab(t) for t in tabs]})
        if a.action in PAGE_ACTIONS and not bridge.supports(a.action):
            return ToolResult(success=False, error="The browser extension is older than 1.2: reload it at chrome://extensions (it updates itself "
                                                   "when connected), then repeat.")
        if a.action == "read" and a.tab_id is None and a.target.strip().casefold() in ("", "active", "this", "current"):
            matches = [t for t in tabs if t.get("active")]
        else:
            matches = [t for t in tabs if t["id"] == a.tab_id] if a.tab_id is not None else select_tabs(tabs, a.target)
        if a.action in YOUTUBE_ONLY:
            matches = select_tabs(matches, "youtube")
            # For media only, an unambiguous audible (or the one active) YouTube tab is a meaningful target.
            audible = [t for t in matches if t.get("audible")]
            active = [t for t in matches if t.get("active")]
            if len(matches) > 1 and len(audible) == 1:
                matches = audible
            elif len(matches) > 1 and len(active) == 1:
                matches = active
        if len(matches) != 1:
            shown = "; ".join(f"tab {t['id']}: {t.get('title', '')}" for t in matches)
            return ToolResult(success=False, output={"candidates": [public_tab(t) for t in matches], "needs_confirmation": True}, error="Matching tab nahi mila; unrelated tab ko touch nahi kiya." if not matches else "Multiple matching tabs. Exact title ya tab ID batayein: " + shown)
        tab = matches[0]
        extra = {k: v for k, v in a.model_dump().items() if k in {"seconds", "position", "rate", "level", "index", "amount", "exit"} and v is not None and v is not False}
        result = bridge.request(a.action, tab_id=tab["id"], expected_url=tab.get("url", ""), **extra)
        if not result.get("success"):
            return ToolResult(success=False, error=result.get("error", "Browser action failed"))
        if a.action in {"close", "pause", "resume"}:
            verified = result.get("closed") is True if a.action == "close" else result.get("paused") is (a.action == "pause")
            if not verified:
                return ToolResult(success=False, error="Browser ne requested tab/video state verify nahi ki.")
        keep = {"success", "closed", "paused", "error", "title", "url", "active", "time", "duration", "rate", "volume", "muted", "fullscreen", "video_id",
                "previous_video_id", "changed", "target", "results", "clicked_title", "wanted_video_id", "moved", "scroll_y", "scroll_max"}
        output = {"action": a.action, "tab_id": tab["id"], "title": tab.get("title"), **{k: v for k, v in result.items() if k in keep}}
        output["spoken"] = _spoken(a, output)
        return ToolResult(success=True, output=output)

    def verify(self, args, result: ToolResult) -> Verification:
        """Evidence comes from the browser extension (a separate process that reads the real tabs)."""
        if not result.success:
            return failed_result(result)
        a = BrowserControlArgs.model_validate(args.model_dump())
        if a.action == "inspect":
            tabs = result.output.get("tabs", [])
            return verdict([("extension returned the tab list", isinstance(tabs, list))], {"tabs": len(tabs)})
        observed = bridge.request("inspect")
        if not observed.get("success"):
            return Verification(passed=False, checks=["could not re-inspect tabs"], details=observed.get("error"))
        fresh = _clean_tabs(observed.get("tabs"))
        ids = {t["id"] for t in fresh}
        if a.action in PAGE_ACTIONS | {"switch", "read"}:
            return self._verify_page(a, result.output, fresh, ids)
        tab_id = result.output.get("tab_id")
        evidence = {"tab_id": tab_id, "tabs_now": len(ids), "extension_reply": {k: result.output.get(k) for k in ("closed", "paused")}}
        if a.action == "close":
            return verdict([("extension confirmed close", result.output.get("closed") is True), ("tab is gone", tab_id not in ids)], evidence)
        return verdict([("extension confirmed video state", result.output.get("paused") is (a.action == "pause")),
                        ("tab still open", tab_id in ids)], evidence)

    @staticmethod
    def _verify_page(a: BrowserControlArgs, out: dict, tabs: list[dict], ids: set) -> Verification:
        """What the page showed AFTER the action (read by the extension from the DOM) must match what was asked; the tab must still be there."""
        tab = next((t for t in tabs if t["id"] == out.get("tab_id")), None)
        evidence = {k: out.get(k) for k in ("tab_id", "title", "time", "rate", "volume", "muted", "fullscreen", "video_id", "scroll_y", "active") if out.get(k) is not None}
        checks = [("tab still open", tab is not None)]
        if a.action == "seek":
            want = out.get("target")
            checks.append(("the video is at the requested time", want is not None and out.get("time") is not None and abs(out["time"] - want) <= 2))
        elif a.action == "speed":
            checks.append(("the player runs at the requested speed", out.get("rate") is not None and abs(out["rate"] - a.rate) < 0.01))
        elif a.action == "volume":
            checks.append(("the player volume is the requested level", out.get("volume") is not None and abs(out["volume"] - a.level) <= 1 and out.get("muted") is False))
        elif a.action in {"mute", "unmute"}:
            checks.append(("the player is " + ("muted" if a.action == "mute" else "unmuted"), out.get("muted") is (a.action == "mute")))
        elif a.action == "fullscreen":
            checks.append(("the video is " + ("full screen" if not a.exit else "back to normal"), out.get("fullscreen") is (not a.exit)))
        elif a.action == "next":
            checks.append(("a different video is playing", bool(out.get("video_id")) and out.get("video_id") != out.get("previous_video_id")))
        elif a.action == "click_result":
            checks.append(("the chosen result's video opened", bool(out.get("wanted_video_id")) and out.get("video_id") == out.get("wanted_video_id")))
        elif a.action == "scroll":
            checks.append(("the page moved", out.get("moved") is True))
        elif a.action == "results":
            checks.append(("results were read from the page", bool(out.get("results"))))
        elif a.action == "switch":
            checks.append(("the tab is now the active one", tab is not None and tab.get("active") is True))
        elif a.action == "read":
            checks.append(("the tab's title was read", bool(out.get("title"))))
        return verdict(checks, evidence)


