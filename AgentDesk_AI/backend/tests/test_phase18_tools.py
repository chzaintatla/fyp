"""Phase 18 tools: media_control, play_youtube_video playback check, whatsapp_open_chat, winget_install, email draft-only."""
from __future__ import annotations

import inspect
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from app.models import MemoryItem
from app.tools import apps, media, messaging, packages
from app.tools.base import RiskLevel, ToolResult
from app.tools.registry import ToolRegistry
from app.verification import WindowInfo, use_probe
from tests.helpers.fake_probe import ScriptedProbe


@pytest.fixture
def registry(ctx):
    return ToolRegistry(ctx)


def session(title, status):
    return {"app": "Spotify.exe", "title": title, "artist": "x", "status": status}


# ------------------------------------------------------------------ media_control
def test_media_keys_are_the_windows_virtual_keys(registry, monkeypatch):
    sent = []
    monkeypatch.setattr(media, "press_media_key", sent.append)
    tool = registry.get("media_control")
    with use_probe(ScriptedProbe(media=[session("Song A", "paused")])):
        for action in ("play_pause", "next", "previous", "stop"):
            tool.execute(tool.validate_args({"action": action}))
    assert sent == [0xB3, 0xB0, 0xB1, 0xB2]
    assert tool.risk_level == RiskLevel.LOW


def test_play_when_already_playing_sends_no_key(registry, monkeypatch):
    sent = []
    monkeypatch.setattr(media, "press_media_key", sent.append)
    tool = registry.get("media_control")
    with use_probe(ScriptedProbe(media=[session("Song A", "playing")])):
        result = tool.execute(tool.validate_args({"action": "play"}))
        assert result.output["key_sent"] is False and sent == []
        assert tool.verify(tool.validate_args({"action": "play"}), result).passed


@pytest.mark.parametrize("action,before,after,passed", [
    ("next", session("Song A", "playing"), session("Song B", "playing"), True),
    ("next", session("Song A", "playing"), session("Song A", "playing"), False),
    ("pause", session("Song A", "playing"), session("Song A", "paused"), True),
    ("pause", session("Song A", "playing"), session("Song A", "playing"), False),
    ("play_pause", session("Song A", "paused"), session("Song A", "playing"), True),
    ("next", None, None, False),
])
def test_media_verifier_reads_the_real_session(registry, monkeypatch, action, before, after, passed):
    monkeypatch.setattr(media, "press_media_key", lambda vk: None)
    tool = registry.get("media_control")
    readings = [r for r in (before, after) if r is not None]
    with use_probe(ScriptedProbe(media=readings)):
        args = tool.validate_args({"action": action})
        result = tool.execute(args)
        v = tool.verify(args, result)
    assert v.passed is passed
    if passed:
        assert v.evidence["after"]["title"]


@pytest.mark.skipif(sys.platform == "win32", reason="asserts the non-Windows error path")
def test_media_key_off_windows_fails_honestly(registry):
    tool = registry.get("media_control")
    with use_probe(ScriptedProbe()):
        result = tool.execute(tool.validate_args({"action": "next"}))
    assert not result.success and "Windows" in result.error


# ------------------------------------------------------------------ YouTube playback
@pytest.mark.parametrize("state,passed", [(session("Lofi beats - YouTube", "playing"), True),
                                          (session("Lofi beats - YouTube", "paused"), False), (None, False)])
def test_chosen_youtube_video_counts_only_when_playing(registry, state, passed):
    tool = registry.get("play_youtube_video")
    result = ToolResult(success=True, output={"video_selected": True, "title": "Lofi beats to study", "url": "u"})
    with use_probe(ScriptedProbe(media=[state] if state else [])):
        v = tool.verify(tool.validate_args({"query": "lofi beats"}), result)
    assert v.passed is passed
    if not passed:
        assert "autoplay" in (v.details or "")


def test_youtube_search_results_need_only_the_window(registry):
    tool = registry.get("play_youtube_video")
    result = ToolResult(success=True, output={"video_selected": False, "results_url": "u"})
    window = WindowInfo(1, "lofi - YouTube - Google Chrome", 2, "chrome.exe")
    with use_probe(ScriptedProbe(windows=[window])):
        assert tool.verify(tool.validate_args({"query": "lofi"}), result).passed


# ------------------------------------------------------------------ whatsapp_open_chat
@pytest.mark.parametrize("raw,digits", [("+92 300 1234567", "923001234567"), ("0092-300-1234567", "923001234567"),
                                        ("+1 (415) 555-0100", "14155550100")])
def test_phone_normalisation(raw, digits):
    assert messaging.normalize_phone(raw) == digits


@pytest.mark.parametrize("raw", ["03001234567", "+12", "+92abc1234567", "+1234567890123456"])
def test_ambiguous_or_invalid_numbers_are_refused(raw):
    with pytest.raises(ValueError):
        messaging.normalize_phone(raw)


def test_target_must_be_phone_or_contact():
    with pytest.raises(ValidationError):
        messaging.WhatsAppOpenChatArgs(text="hi")
    with pytest.raises(ValidationError):
        messaging.WhatsAppOpenChatArgs(phone="+923001234567", contact="Ahmed")


def test_contact_from_memory_opens_prefilled_chat_and_never_sends(registry, db, monkeypatch):
    db.add(MemoryItem(kind="alias", text="Ahmed: +92 300 1234567"))
    db.commit()
    opened = []
    monkeypatch.setattr(messaging, "whatsapp_installed", lambda: True)
    monkeypatch.setattr(messaging._platform, "open_path_with_os", opened.append)
    tool = registry.get("whatsapp_open_chat")
    assert tool.risk_level == RiskLevel.MEDIUM
    result = tool.execute(tool.validate_args({"contact": "ahmed", "text": "main 10 minute late hoon & sorry"}))
    assert result.success and result.output["sent"] is False and result.output["to"] == "+92******4567"
    url = urlsplit(opened[0])
    assert url.scheme == "whatsapp" and url.netloc == "send"
    assert parse_qs(url.query) == {"phone": ["923001234567"], "text": ["main 10 minute late hoon & sorry"]}
    assert "keyboard" not in inspect.getsource(messaging) and "pyautogui" not in inspect.getsource(messaging)


def test_unknown_contact_or_missing_app_asks_instead_of_guessing(registry, db, monkeypatch):
    monkeypatch.setattr(messaging._platform, "open_path_with_os", lambda uri: pytest.fail("opened"))
    tool = registry.get("whatsapp_open_chat")
    result = tool.execute(tool.validate_args({"contact": "Bilal", "text": "hi"}))
    assert not result.success and result.output["needs_confirmation"] and "Memory" in result.error
    monkeypatch.setattr(messaging, "whatsapp_installed", lambda: False)
    result = tool.execute(tool.validate_args({"phone": "+923001234567", "text": "hi"}))
    assert not result.success and "not installed" in result.error


@pytest.mark.parametrize("windows,texts,passed", [
    ([WindowInfo(5, "WhatsApp", 77, "WhatsApp.exe")], ["Type a message", "main late hoon"], True),
    ([WindowInfo(5, "WhatsApp", 77, "WhatsApp.exe")], ["Type a message"], False),
    ([WindowInfo(5, "WhatsApp", 77, "WhatsApp.exe")], [], True),   # text box not readable: window is the evidence
    ([], [], False),
])
def test_whatsapp_verifier(registry, windows, texts, passed):
    tool = registry.get("whatsapp_open_chat")
    args = tool.validate_args({"phone": "+923001234567", "text": "main late hoon"})
    result = ToolResult(success=True, output={"to": "+92******4567", "sent": False})
    with use_probe(ScriptedProbe(windows=windows, dialogs={77: texts})):
        v = tool.verify(args, result)
    assert v.passed is passed and v.evidence["sent"] is False


# ------------------------------------------------------------------ winget_install
class Winget:
    def __init__(self, installed=False, install_code=0, installs_ok=True):
        self.installed, self.install_code, self.installs_ok = installed, install_code, installs_ok
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(args[0])
        if args[0] == "list":
            ok = self.installed
            return SimpleNamespace(returncode=0 if ok else 1, stdout=f"Name Id\nVLC {args[2]}\n" if ok else "No installed package found", stderr="")
        if self.install_code == 0 and self.installs_ok:
            self.installed = True
        return SimpleNamespace(returncode=self.install_code, stdout="Successfully installed", stderr="")


def test_install_is_high_risk_exact_id_and_never_interrupted(registry):
    tool = registry.get("winget_install")
    assert tool.risk_level == RiskLevel.HIGH and tool.interruptible is False and tool.timeout_seconds >= 600
    for bad in ("vlc media player", "VLC && calc", "-h", "x"):
        with pytest.raises(ValidationError):
            tool.validate_args({"package_id": bad})


def test_install_then_verify_with_winget_list(registry, monkeypatch):
    fake = Winget()
    monkeypatch.setattr(packages, "run_winget", fake)
    tool = registry.get("winget_install")
    args = tool.validate_args({"package_id": "VideoLAN.VLC"})
    result = tool.execute(args)
    assert result.success and fake.calls == ["list", "install"]
    v = tool.verify(args, result)
    assert v.passed and "VideoLAN.VLC" in v.evidence["winget_list_tail"] and fake.calls[-1] == "list"


def test_install_flags_are_exact_silent_and_accept_agreements(registry, monkeypatch):
    seen = []
    def run(args, timeout):
        seen.append(args)
        return SimpleNamespace(returncode=1 if args[0] == "list" else 0, stdout="", stderr="")
    monkeypatch.setattr(packages, "run_winget", run)
    tool = registry.get("winget_install")
    tool.execute(tool.validate_args({"package_id": "VideoLAN.VLC"}))
    install = seen[1]
    assert install[:5] == ["install", "--id", "VideoLAN.VLC", "--exact", "--source"]
    assert "--accept-package-agreements" in install and "--silent" in install


def test_already_installed_is_not_installed_again(registry, monkeypatch):
    fake = Winget(installed=True)
    monkeypatch.setattr(packages, "run_winget", fake)
    tool = registry.get("winget_install")
    result = tool.execute(tool.validate_args({"package_id": "VideoLAN.VLC"}))
    assert result.success and result.output["already_installed"] and fake.calls == ["list"]


@pytest.mark.parametrize("fake,ok", [(Winget(install_code=1603), False), (Winget(installs_ok=False), None)])
def test_failed_or_unconfirmed_install_is_not_a_success(registry, monkeypatch, fake, ok):
    monkeypatch.setattr(packages, "run_winget", fake)
    tool = registry.get("winget_install")
    args = tool.validate_args({"package_id": "VideoLAN.VLC"})
    result = tool.execute(args)
    if ok is False:
        assert not result.success and "1603" in result.error
    else:  # winget said 0 but the package is not listed afterwards
        assert result.success and not tool.verify(args, result).passed


def test_winget_missing_is_reported(registry, monkeypatch):
    def missing(args, timeout):
        raise FileNotFoundError("WinGet was not found.")
    monkeypatch.setattr(packages, "run_winget", missing)
    tool = registry.get("winget_install")
    assert "WinGet" in tool.execute(tool.validate_args({"package_id": "VideoLAN.VLC"})).error


# ------------------------------------------------------------------ email stays a draft
def test_email_is_draft_only(registry, monkeypatch):
    opened = []
    monkeypatch.setattr(apps.webbrowser, "open", lambda url: opened.append(url) or True)
    tool = registry.get("compose_email")
    result = tool.execute(tool.validate_args({"to": "a@example.com", "subject": "S", "body": "B"}))
    assert opened[0].startswith("mailto:a@example.com?") and result.output["sent"] is False
    source = inspect.getsource(apps)
    assert "smtplib" not in source and "sendmail" not in source
    assert not [n for n in registry.model_names() if n.startswith("send")]   # send_email / send_message exist, but are hidden from the model (U6)
