"""Unit tests for the tool registry: execution, verification, error handling."""
from __future__ import annotations

import sys
from types import SimpleNamespace
import zipfile
from pathlib import Path

import pytest

from app.tools import platform as platform_tools
from app.tools.apps import (
    ComposeEmailTool,
    OpenApplicationTool,
    OpenUrlTool,
    PlayYouTubeVideoTool,
    WebSearchTool,
)
from app.tools.base import RiskLevel, ToolContext
from app.tools.desktop import DesktopControlTool
from app.tools.filesystem import (
    CompressFolderTool,
    CopyFileTool,
    CreateFolderTool,
    DeleteFileTool,
    EditTextFileTool,
    ExtractFileTool,
    FindFilesTool,
    ListFolderTool,
    ListTrashTool,
    MoveFileTool,
    OpenFileTool,
    OpenFolderTool,
    ReadTextFileTool,
    RenameFileTool,
    RestoreFileTool,
    WriteTextFileTool,
)
from app.tools.registry import ToolRegistry, UnknownToolError
from app.tools.system import ManageSystemSettingTool, TakeScreenshotTool
from app.tools import web as web_module


def test_registry_lists_all_tools(ctx: ToolContext):
    registry = ToolRegistry(ctx)
    assert registry.names() == [
        "application_package",
        "compose_email",
        "compress_folder",
        "control_browser",
        "control_desktop",
        "copy_file",
        "create_folder",
        "delete_file",
        "download_web_content",
        "edit_text_file",
        "extract_file",
        "find_files",
        "find_paths",
        "list_folder",
        "list_trash",
        "manage_system_setting",
        "media_control",
        "memory_forget",
        "memory_recall",
        "memory_remember",
        "move_file",
        "open_application",
        "open_file",
        "open_folder",
        "open_url",
        "open_windows_settings",
        "play_youtube_video",
        "read_text_file",
        "rename_file",
        "restore_file",
        "summarise_file",
        "summarise_page",
        "take_screenshot",
        "web_answer",
        "web_search",
        "whatsapp_open_chat",
        "winget_install",
        "write_text_file",
    ]


def test_unknown_tool_rejected(ctx: ToolContext):
    with pytest.raises(UnknownToolError):
        ToolRegistry(ctx).get("rm_rf_everything")


def test_desktop_control_requires_approval_and_bounds_input(ctx: ToolContext):
    tool = DesktopControlTool(ctx)
    inspect = tool.validate_args({"steps": [{"action": "inspect"}]})
    typing = tool.validate_args(
        {"target_window": "Notepad", "steps": [{"action": "type_text", "text": "hello"}]}
    )
    assert tool.risk_for_args(inspect) == RiskLevel.LOW
    assert tool.risk_for_args(typing) == RiskLevel.HIGH
    assert tool.risk_for_user_command(typing, "In Notepad, type hello") == RiskLevel.LOW
    assert tool.risk_for_user_command(typing, "Open Notepad and type hello") == RiskLevel.LOW
    assert tool.risk_for_user_command(typing, "Open Chrome") == RiskLevel.HIGH
    assert tool.risk_for_user_command(typing, "Open Notepad") == RiskLevel.HIGH
    close = tool.validate_args(
        {"target_window": "Untitled - Notepad", "steps": [{"action": "close_window"}]}
    )
    assert tool.risk_for_args(close) == RiskLevel.HIGH
    assert tool.risk_for_user_command(close, "Close Notepad") == RiskLevel.LOW
    eclipse = tool.validate_args(
        {"target_window": "Eclipse IDE", "steps": [{"action": "close_window"}]}
    )
    assert tool.risk_for_user_command(eclipse, "Eclipse IDE band karo") == RiskLevel.LOW
    assert tool.risk_for_user_command(eclipse, "band kar do") == RiskLevel.HIGH
    chrome_target = tool.validate_args(
        {"target_window": "YouTube - Google Chrome", "steps": [{"action": "press_key", "key": "enter"}]}
    )
    assert tool.risk_for_user_command(chrome_target, "Google Charmin Honor me song search karo") == RiskLevel.LOW
    with pytest.raises(ValueError):
        tool.validate_args({"steps": [{"action": "click", "x": -1, "y": 1}]})
    with pytest.raises(ValueError):
        tool.validate_args({"steps": [{"action": "hotkey", "keys": ["ctrl", "powershell"]}]})
    with pytest.raises(ValueError):
        tool.validate_args({"steps": [{"action": "type_text", "text": "x", "key": "enter"}]})
    with pytest.raises(ValueError, match="target_window"):
        tool.validate_args(
            {"steps": [{"action": "hotkey", "keys": ["alt", "f4"]}]}
        )
    with pytest.raises(ValueError, match="final"):
        tool.validate_args(
            {
                "target_window": "Notepad",
                "steps": [
                    {"action": "close_window"},
                    {"action": "press_key", "key": "enter"},
                ],
            }
        )


def test_desktop_control_executes_only_validated_steps(ctx: ToolContext, monkeypatch):
    calls = []
    fake = SimpleNamespace(
        FAILSAFE=False,
        size=lambda: (800, 600),
        click=lambda x, y: calls.append(("click", x, y)),
        write=lambda text, interval: calls.append(("write", text)),
        press=lambda key: calls.append(("press", key)),
        hotkey=lambda *keys: calls.append(("hotkey", *keys)),
        scroll=lambda amount: calls.append(("scroll", amount)),
        getActiveWindow=lambda: SimpleNamespace(title="Notepad"),
        getAllWindows=lambda: [SimpleNamespace(title="Notepad")],
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    tool = DesktopControlTool(ctx)
    args = tool.validate_args(
        {
            "target_window": "Notepad",
            "steps": [
                {"action": "inspect"},
                {"action": "click", "x": 10, "y": 20},
                {"action": "type_text", "text": "hello"},
                {"action": "hotkey", "keys": ["ctrl", "s"]},
            ]
        }
    )
    result = tool.execute(args)
    assert result.success, result.error
    assert fake.FAILSAFE is True
    assert result.output["completed_steps"][0]["active_window"] == "Notepad"
    assert calls == [
        ("click", 10, 20),
        ("write", "hello"),
        ("hotkey", "ctrl", "s"),
    ]
    fake.getActiveWindow = lambda: SimpleNamespace(title="Chrome")
    mismatch = tool.execute(args)
    assert not mismatch.success
    assert "does not match target" in mismatch.error
    assert len(calls) == 3


def test_desktop_control_close_window_requires_unique_target_and_verifies(ctx: ToolContext, monkeypatch):
    calls = []

    class FakeWindow:
        title = "Untitled - Notepad"

        def activate(self):
            calls.append(("activate", self.title))

    windows = [FakeWindow()]
    fake = SimpleNamespace(
        FAILSAFE=False,
        getAllWindows=lambda: list(windows),
        getActiveWindow=lambda: windows[0] if windows else None,
        size=lambda: (800, 600),
        hotkey=lambda *keys: (calls.append(("hotkey", *keys)), windows.clear()),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setattr("app.tools.desktop.time.sleep", lambda _seconds: None)
    tool = DesktopControlTool(ctx)
    args = tool.validate_args(
        {"target_window": "Untitled - Notepad", "steps": [{"action": "close_window"}]}
    )
    assert tool.risk_for_args(args) == RiskLevel.HIGH
    result = tool.execute(args)
    assert result.success, result.error
    assert result.output["completed_steps"] == [
        {
            "action": "close_window",
            "window": "Untitled - Notepad",
            "hwnd": None,
            "pid": None,
            "closed": True,
            "posted_close": False,
        }
    ]
    assert calls == [("activate", "Untitled - Notepad"), ("hotkey", "alt", "f4")]


def test_desktop_control_close_window_ignores_benign_activate_error(ctx: ToolContext, monkeypatch):
    class FakeWindow:
        title = "Eclipse IDE"

        def activate(self):
            raise RuntimeError("Error code from Windows: 0 - The operation completed successfully.")

        def close(self):
            windows.clear()

    windows = [FakeWindow()]
    fake = SimpleNamespace(
        FAILSAFE=False,
        getAllWindows=lambda: list(windows),
        getActiveWindow=lambda: windows[0] if windows else None,
        size=lambda: (800, 600),
        hotkey=lambda *keys: (_ for _ in ()).throw(AssertionError("alt+f4 should not be required")),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setattr("app.tools.desktop.time.sleep", lambda _seconds: None)
    tool = DesktopControlTool(ctx)
    args = tool.validate_args(
        {"target_window": "Eclipse IDE", "steps": [{"action": "close_window"}]}
    )
    result = tool.execute(args)
    assert result.success, result.error
    assert result.output["completed_steps"][0]["closed"] is True
    assert result.output["completed_steps"][0]["posted_close"] is True


def test_desktop_control_close_window_does_not_alt_f4_another_app(ctx: ToolContext, monkeypatch):
    calls = []

    class EclipseWindow:
        title = "Eclipse IDE"

        def activate(self):
            calls.append(("activate", self.title))

    class ChromeWindow:
        title = "AgentDesk AI - Google Chrome"

    eclipse = EclipseWindow()
    chrome = ChromeWindow()
    fake = SimpleNamespace(
        FAILSAFE=False,
        getAllWindows=lambda: [eclipse, chrome],
        getActiveWindow=lambda: chrome,
        size=lambda: (800, 600),
        hotkey=lambda *keys: calls.append(("hotkey", *keys)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setattr("app.tools.desktop.time.sleep", lambda _seconds: None)
    tool = DesktopControlTool(ctx)
    args = tool.validate_args(
        {"target_window": "Eclipse IDE", "steps": [{"action": "close_window"}]}
    )
    result = tool.execute(args)
    assert not result.success
    assert "Alt+F4 was not sent" in result.error
    assert "AgentDesk AI - Google Chrome" in result.error
    assert ("hotkey", "alt", "f4") not in calls


def test_desktop_control_close_tab_uses_ctrl_w(ctx: ToolContext, monkeypatch):
    calls = []

    class FakeWindow:
        title = "YouTube - Google Chrome"

        def activate(self):
            calls.append(("activate", self.title))

    windows = [FakeWindow()]
    fake = SimpleNamespace(
        FAILSAFE=False,
        getAllWindows=lambda: list(windows),
        getActiveWindow=lambda: windows[0],
        size=lambda: (800, 600),
        hotkey=lambda *keys: calls.append(("hotkey", *keys)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setattr("app.tools.desktop.time.sleep", lambda _seconds: None)
    tool = DesktopControlTool(ctx)
    args = tool.validate_args(
        {"target_window": "YouTube - Google Chrome", "steps": [{"type": "key", "keys": "ctrl+w"}]}
    )
    assert args.steps[0].action == "close_tab"
    result = tool.execute(args)
    assert result.success, result.error
    assert result.output["completed_steps"][0]["closed_tab"] is True
    assert ("hotkey", "ctrl", "w") in calls


def test_desktop_control_does_not_close_unmatched_browser_window(ctx: ToolContext, monkeypatch):
    calls = []

    class BrowserWindow:
        def __init__(self, title):
            self.title = title

        def activate(self):
            calls.append(("activate", self.title))

    active = BrowserWindow("AgentDesk AI - Google Chrome")
    other = BrowserWindow("ChatGPT - Google Chrome")
    fake = SimpleNamespace(
        FAILSAFE=False,
        getAllWindows=lambda: [active, other],
        getActiveWindow=lambda: active,
        size=lambda: (800, 600),
        hotkey=lambda *keys: calls.append(("hotkey", *keys)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    tool = DesktopControlTool(ctx)
    args = tool.validate_args(
        {"target_window": "YouTube", "steps": [{"action": "close_tab"}]}
    )

    result = tool.execute(args)

    assert not result.success
    assert "Expected one window matching 'YouTube'; found 0." in result.error
    assert "Do not substitute another window" in result.error
    assert calls == []


def test_desktop_control_accepts_model_key_chord_shape(ctx: ToolContext):
    tool = DesktopControlTool(ctx)
    args = tool.validate_args(
        {
            "target_window": "Notepad",
            "steps": [{"type": "key", "keys": "alt+f4"}],
        }
    )
    assert args.steps[0].action == "close_window"
    assert args.steps[0].keys is None
    assert tool.risk_for_args(args) == RiskLevel.HIGH


def test_find_files_returns_close_name_matches(ctx: ToolContext, tmp_path: Path):
    folder = tmp_path / "Reports"
    folder.mkdir()
    (folder / "quarterly-summary.txt").write_text("ok")
    tool = FindFilesTool(ctx)
    result = tool.execute(tool.validate_args({"name": "quartly summary", "folder": str(tmp_path)}))
    assert result.success, result.error
    assert any(item["name"] == "quarterly-summary.txt" for item in result.output["matches"])


def test_edit_text_file_replaces_unique_snippet(ctx: ToolContext, tmp_path: Path):
    path = tmp_path / "note.txt"
    path.write_text("Hello world")
    tool = EditTextFileTool(ctx)
    args = tool.validate_args({"path": str(path), "old_text": "world", "new_text": "AgentDesk"})
    result = tool.execute(args)
    assert result.success, result.error
    assert path.read_text() == "Hello AgentDesk"
    assert tool.verify(args, result).passed


def test_create_and_list_folder(ctx: ToolContext, tmp_path: Path):
    create = CreateFolderTool(ctx)
    target = tmp_path / "FYP"
    result = create.execute(create.validate_args({"path": str(target)}))
    assert result.success, result.error
    assert create.verify(create.validate_args({"path": str(target)}), result).passed

    # creating again reports already_existed instead of failing
    again = create.execute(create.validate_args({"path": str(target)}))
    assert again.success and again.output["already_existed"] is True

    lst = ListFolderTool(ctx)
    out = lst.execute(lst.validate_args({"path": str(tmp_path)}))
    assert out.success
    assert {"name": "FYP", "type": "dir"} in out.output["entries"]


def test_create_folder_expands_current_user_desktop_path(ctx: ToolContext, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    create = CreateFolderTool(ctx)
    args = create.validate_args({"path": "~/Desktop/ai"})

    result = create.execute(args)

    assert result.success, result.error
    assert (tmp_path / "Desktop" / "ai").is_dir()
    assert create.verify(args, result).passed


def test_create_folder_outside_allowed_directories_succeeds(
    ctx: ToolContext, tmp_path: Path
):
    target = tmp_path.parent / f"{tmp_path.name}_outside" / "nested" / "folder"
    create = CreateFolderTool(ctx)
    args = create.validate_args({"path": str(target)})
    result = create.execute(args)

    assert result.success, result.error
    assert target.is_dir()
    assert create.verify(args, result).passed


def test_rename_file(ctx: ToolContext, tmp_path: Path):
    src = tmp_path / "old.txt"
    src.write_text("hello")
    rename = RenameFileTool(ctx)
    args = rename.validate_args({"path": str(src), "new_name": "new.txt"})
    result = rename.execute(args)
    assert result.success, result.error
    assert rename.verify(args, result).passed
    # path traversal via new_name is rejected
    bad = rename.execute(rename.validate_args({"path": str(tmp_path / "new.txt"), "new_name": "../evil.txt"}))
    assert not bad.success


def test_move_file(ctx: ToolContext, tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    move = MoveFileTool(ctx)
    args = move.validate_args({"source": str(src), "destination": str(dest_dir)})
    result = move.execute(args)
    assert result.success, result.error
    assert move.verify(args, result).passed
    # refuses to overwrite
    src2 = tmp_path / "b.txt"
    src2.write_text("x")
    clash = move.execute(move.validate_args({"source": str(src2), "destination": str(dest_dir / "a.txt")}))
    assert not clash.success


def test_copy_file_and_folder(ctx: ToolContext, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.txt").write_text("copy me")
    copy = CopyFileTool(ctx)
    args = copy.validate_args({"source": str(source), "destination": str(tmp_path / "copied")})
    result = copy.execute(args)
    assert result.success, result.error
    assert copy.verify(args, result).passed
    assert (tmp_path / "copied" / "note.txt").read_text() == "copy me"

    file_source = source / "note.txt"
    file_copy = tmp_path / "copy.txt"
    assert copy.execute(copy.validate_args({"source": str(file_source), "destination": str(file_copy)})).success
    refused = copy.execute(copy.validate_args({"source": str(file_source), "destination": str(file_copy)}))
    assert not refused.success and "already exists" in refused.error.lower()
    nested = copy.execute(
        copy.validate_args({"source": str(source), "destination": str(source / "inside")})
    )
    assert not nested.success


def test_compose_email_opens_draft_without_sending(ctx: ToolContext, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("app.tools.apps.webbrowser.open", lambda url: opened.append(url) or True)
    tool = ComposeEmailTool(ctx)
    args = tool.validate_args(
        {"to": "user@example.com", "subject": "Project update", "body": "Hello there"}
    )
    result = tool.execute(args)
    assert result.success
    assert result.output["draft_opened"] is True
    assert result.output["sent"] is False
    assert opened[0].startswith("mailto:user@example.com?")
    with pytest.raises(Exception):
        tool.validate_args({"to": "not-an-email", "subject": "Hello", "body": "Text"})


def test_compress_and_extract(ctx: ToolContext, tmp_path: Path):
    folder = tmp_path / "project"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "code.py").write_text("print('hi')")
    comp = CompressFolderTool(ctx)
    args = comp.validate_args({"folder_path": str(folder)})
    result = comp.execute(args)
    assert result.success, result.error
    assert comp.verify(args, result).passed
    assert zipfile.is_zipfile(tmp_path / "project.zip")

    # refuses to create the archive inside the folder being compressed
    bad = comp.execute(
        comp.validate_args({"folder_path": str(folder), "zip_path": str(folder / "inner.zip")})
    )
    assert not bad.success

    ext = ExtractFileTool(ctx)
    eargs = ext.validate_args({"zip_path": str(tmp_path / "project.zip")})
    eresult = ext.execute(eargs)
    assert eresult.success, eresult.error
    assert ext.verify(eargs, eresult).passed


def test_extract_blocks_zip_slip(ctx: ToolContext, tmp_path: Path):
    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../../evil.txt", "pwned")
    ext = ExtractFileTool(ctx)
    result = ext.execute(ext.validate_args({"zip_path": str(evil_zip)}))
    assert not result.success
    assert "unsafe" in result.error.lower()


def test_delete_file_high_risk(ctx: ToolContext, tmp_path: Path):
    tool = DeleteFileTool(ctx)
    assert tool.risk_level == RiskLevel.HIGH
    target = tmp_path / "todelete.txt"
    target.write_text("bye")
    args = tool.validate_args({"path": str(target)})
    result = tool.execute(args)
    assert result.success, result.error
    assert tool.verify(args, result).passed
    assert not target.exists()
    listed = ListTrashTool(ctx).execute(ListTrashTool(ctx).validate_args({}))
    assert listed.success and len(listed.output["items"]) == 1
    restored = RestoreFileTool(ctx)
    restore_args = restored.validate_args({"trash_id": listed.output["items"][0]["trash_id"]})
    target.write_text("keep this version")
    conflict = restored.execute(restore_args)
    assert not conflict.success and "overwrite" in conflict.error.lower()
    target.unlink()
    restored_result = restored.execute(restore_args)
    assert restored_result.success, restored_result.error
    assert target.read_text() == "bye"
    # refuses non-empty folders
    folder = tmp_path / "full"
    folder.mkdir()
    (folder / "x.txt").write_text("x")
    bad = tool.execute(tool.validate_args({"path": str(folder)}))
    assert not bad.success
    # refuses to delete an allowed root
    root_bad = tool.execute(tool.validate_args({"path": str(tmp_path)}))
    assert not root_bad.success


def test_delete_file_outside_allowed_dirs_remains_restorable(
    ctx: ToolContext, tmp_path: Path
):
    outside = tmp_path.parent / f"{tmp_path.name}_outside_delete"
    outside.mkdir()
    target = outside / "remove-me.txt"
    target.write_text("restore this")
    tool = DeleteFileTool(ctx)
    args = tool.validate_args({"path": str(target)})

    result = tool.execute(args)

    assert result.success, result.error
    assert tool.verify(args, result).passed
    assert not target.exists()
    assert (outside / ".agentdesk_trash" / result.output["trash_id"]).read_text() == "restore this"

    listed = ListTrashTool(ctx).execute(ListTrashTool(ctx).validate_args({}))
    assert listed.success, listed.error
    assert any(item["trash_id"] == result.output["trash_id"] for item in listed.output["items"])

    restored = RestoreFileTool(ctx).execute(
        RestoreFileTool(ctx).validate_args({"trash_id": result.output["trash_id"]})
    )
    assert restored.success, restored.error
    assert target.read_text() == "restore this"


def test_open_folder_uses_platform_launcher(ctx: ToolContext, tmp_path: Path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(platform_tools, "open_path_with_os", lambda p: calls.append(p))
    tool = OpenFolderTool(ctx)
    args = tool.validate_args({"path": str(tmp_path)})
    result = tool.execute(args)
    assert result.success, result.error
    assert calls == [str(tmp_path)]
    assert tool.verify(args, result).passed
    # missing folder -> honest failure
    missing = tool.execute(tool.validate_args({"path": str(tmp_path / "nope")}))
    assert not missing.success


def test_open_folder_allows_existing_path_outside_allowed_dirs(tmp_path: Path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(platform_tools, "open_path_with_os", lambda p: calls.append(p))
    sandbox = tmp_path / "sandbox"
    outside = tmp_path / "6th sem"
    sandbox.mkdir()
    outside.mkdir()
    tool = OpenFolderTool(ToolContext(allowed_directories=[sandbox]))
    result = tool.execute(tool.validate_args({"path": str(outside)}))
    assert result.success, result.error
    assert calls == [str(outside)]


def test_open_application_approved_vs_unapproved(ctx: ToolContext, monkeypatch):
    launched: list[str] = []

    class FakeProc:
        pid = 1234

        def poll(self):
            return None

    monkeypatch.setattr(platform_tools, "launch_program", lambda exe, args=None: launched.append(exe) or FakeProc())
    monkeypatch.setattr(OpenApplicationTool, "_activate_running_app", lambda _self, _app: None)
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    tool = OpenApplicationTool(ctx)
    assert tool.risk_for_args(tool.validate_args({"app_name": "chrome"})) == RiskLevel.LOW
    assert tool.risk_for_args(tool.validate_args({"app_name": "ransomware"})) == RiskLevel.HIGH
    result = tool.execute(tool.validate_args({"app_name": "chrome"}))
    assert result.success, result.error
    assert launched and tool.verify(tool.validate_args({"app_name": "chrome"}), result).passed


def test_google_chrome_alias_uses_approved_chrome_and_registered_path(ctx: ToolContext, monkeypatch):
    launched: list[str] = []

    class FakeProc:
        pid = 4242

    monkeypatch.setattr(platform_tools, "launch_program", lambda exe, args=None: launched.append(exe) or FakeProc())
    monkeypatch.setattr(OpenApplicationTool, "_activate_running_app", lambda _self, _app: None)
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.tools.apps.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        OpenApplicationTool,
        "_windows_registered_app_path",
        staticmethod(lambda executable: r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    )
    monkeypatch.setattr(OpenApplicationTool, "_activate_running_app", lambda _self, _app: None)

    tool = OpenApplicationTool(ctx)
    args = tool.validate_args({"app_name": "Google Chrome"})
    assert tool.risk_for_args(args) == RiskLevel.LOW
    result = tool.execute(args)
    assert result.success, result.error
    assert launched == [r"C:\Program Files\Google\Chrome\Application\chrome.exe"]


def test_chrome_is_found_in_standard_windows_installation(
    ctx: ToolContext, tmp_path: Path, monkeypatch
):
    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.touch()

    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(
        OpenApplicationTool,
        "_windows_registered_app_path",
        staticmethod(lambda _executable: None),
    )
    monkeypatch.setattr(
        "app.tools.apps.shutil.which",
        lambda candidate: candidate if Path(candidate).is_file() else None,
    )
    tool = OpenApplicationTool(ctx)

    assert tool._find_executable("chrome") == str(chrome)


def test_open_application_reuses_existing_matching_window(ctx: ToolContext, monkeypatch):
    calls = []

    class Window:
        title = "YouTube - Google Chrome"

        def activate(self):
            calls.append("activated")

    window = Window()
    fake_pyautogui = SimpleNamespace(
        getAllWindows=lambda: [window],
        getActiveWindow=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    monkeypatch.setattr(platform_tools, "launch_program", lambda *_args, **_kwargs: pytest.fail("should reuse"))
    tool = OpenApplicationTool(ctx)
    result = tool.execute(tool.validate_args({"app_name": "Chrome"}))
    assert result.success, result.error
    assert result.output["reused_window"] is True
    assert calls == ["activated"]


def test_installed_start_menu_apps_support_close_names_without_approval(
    ctx: ToolContext, tmp_path: Path, monkeypatch
):
    shortcut = tmp_path / "Microsoft Edge.lnk"
    shortcut.touch()
    opened: list[str] = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(getAllWindows=lambda: [], getActiveWindow=lambda: None))
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.tools.apps.webbrowser.open", lambda _url: True)
    monkeypatch.setattr(platform_tools, "open_path_with_os", lambda path: opened.append(path))
    tool = OpenApplicationTool(ctx)
    monkeypatch.setattr(tool, "_start_menu_shortcuts", lambda: [shortcut])

    args = tool.validate_args({"app_name": "Microsft Edge"})
    assert tool.risk_for_args(args) == RiskLevel.LOW
    result = tool.execute(args)
    assert result.success, result.error
    assert result.output["matched_shortcut"] == "Microsoft Edge"
    assert opened == [str(shortcut)]


def test_unknown_app_returns_close_candidates_instead_of_giving_up(
    ctx: ToolContext, tmp_path: Path, monkeypatch
):
    shortcuts = [tmp_path / "Microsoft Edge.lnk", tmp_path / "Microsoft Excel.lnk"]
    for shortcut in shortcuts:
        shortcut.touch()
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(getAllWindows=lambda: [], getActiveWindow=lambda: None))
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    tool = OpenApplicationTool(ctx)
    monkeypatch.setattr(tool, "_start_menu_shortcuts", lambda: shortcuts)
    result = tool.execute(tool.validate_args({"app_name": "Microsoft"}))
    assert not result.success
    assert result.output.get("needs_confirmation") is True
    assert "Microsoft Edge" in result.output["candidates"]
    assert "Microsoft Excel" in result.output["candidates"]


def test_ambiguous_or_unknown_start_menu_app_is_not_auto_approved(ctx: ToolContext, tmp_path: Path, monkeypatch):
    shortcuts = [tmp_path / "Microsoft Edge.lnk", tmp_path / "Microsoft Excel.lnk"]
    for shortcut in shortcuts:
        shortcut.touch()
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    tool = OpenApplicationTool(ctx)
    monkeypatch.setattr(tool, "_start_menu_shortcuts", lambda: shortcuts)

    unclear = tool.validate_args({"app_name": "Microsoft"})
    assert tool.risk_for_args(unclear) == RiskLevel.HIGH
    assert tool._match_start_menu_shortcut("Not Installed App") is None


def test_open_application_never_passes_file_arguments(ctx: ToolContext, monkeypatch):
    """Regression test for the Notepad/.js incident.

    Launching an application must pass EXACTLY the executable to the OS -
    never a document/file path alongside it. This locks in the invariant
    that `open_application` cannot ask Notepad (or any app) to open a .js
    file, no matter what the planner produced.
    """
    captured: dict = {}

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    def spy(exe, args=None):
        captured["argv"] = [exe] + list(args or [])
        return FakeProc()

    monkeypatch.setattr(platform_tools, "launch_program", spy)
    monkeypatch.setattr(OpenApplicationTool, "_activate_running_app", lambda _self, _app: None)
    import platform as _platform
    import shutil

    # Simulate Windows so the Windows lookup branch is exercised.
    monkeypatch.setattr(_platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        shutil, "which",
        lambda name: r"C:\Windows\System32\notepad.exe" if name.lower() == "notepad.exe" else None,
    )
    tool = OpenApplicationTool(ctx)
    result = tool.execute(tool.validate_args({"app_name": "notepad"}))
    assert result.success, result.error
    assert captured["argv"] == ["C:\\Windows\\System32\\notepad.exe"]
    assert not any(str(x).lower().endswith(".js") for x in captured["argv"])
    assert len(captured["argv"]) == 1  # exe only: no file arguments, ever


def test_open_application_rejects_path_like_names(ctx: ToolContext):
    """app_name must be a plain application name, never a file path."""
    tool = OpenApplicationTool(ctx)
    for bad in [
        r"C:\Users\HP 840 G5\Downloads\PT_step3_links.js",
        "../../evil.exe",
        "notepad.exe /p",
        "C:notes.txt",
    ]:
        with pytest.raises(Exception):
            tool.validate_args({"app_name": bad})
    # and execute() reports it as a normal failure, not a crash
    result = tool.execute({"app_name": r"C:\temp\evil.js"})
    assert not result.success
    assert "not a file path" in result.error


def test_open_url_validates_scheme(ctx: ToolContext, monkeypatch):
    monkeypatch.setattr("app.tools.apps.webbrowser.open", lambda url: True)
    tool = OpenUrlTool(ctx)
    ok = tool.execute(tool.validate_args({"url": "https://example.com"}))
    assert ok.success
    bad = tool.execute({"url": "file:///etc/passwd"})
    assert not bad.success


def test_youtube_search_reuses_open_youtube_window(ctx: ToolContext, monkeypatch):
    calls = []

    class Window:
        title = "YouTube - Google Chrome"

        def activate(self):
            calls.append("activate")

    window = Window()
    fake_pyautogui = SimpleNamespace(
        getAllWindows=lambda: [window],
        getActiveWindow=lambda: window,
        hotkey=lambda *keys: calls.append(("hotkey", *keys)),
        write=lambda text, interval: calls.append(("write", text)),
        press=lambda key: calls.append(("press", key)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.tools.apps.webbrowser.open", lambda _url: pytest.fail("should reuse YouTube"))
    tool = WebSearchTool(ctx)
    result = tool.execute(tool.validate_args({"query": "relaxing piano", "site": "youtube.com"}))
    assert result.success, result.error
    assert result.output["reused_window"] == window.title
    assert result.output["url"].startswith("https://www.youtube.com/results?")
    assert ("hotkey", "ctrl", "l") in calls
    assert calls[-1] == ("press", "enter")


def test_play_youtube_video_opens_close_match_in_existing_window(ctx: ToolContext, monkeypatch):
    calls = []

    class Window:
        title = "YouTube - Google Chrome"

        def activate(self):
            calls.append("activate")

    window = Window()
    fake_pyautogui = SimpleNamespace(
        getAllWindows=lambda: [window],
        getActiveWindow=lambda: window,
        hotkey=lambda *keys: calls.append(("hotkey", *keys)),
        write=lambda text, interval: calls.append(("write", text)),
        press=lambda key: calls.append(("press", key)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "app.tools.apps._find_youtube_video",
        lambda _query: ("abcdefghijk", "Requested Hindi Song"),
    )
    monkeypatch.setattr("app.tools.apps.webbrowser.open", lambda _url: pytest.fail("should reuse YouTube"))
    tool = PlayYouTubeVideoTool(ctx)
    result = tool.execute(tool.validate_args({"query": "Requested Hindi Song"}))
    assert result.success, result.error
    assert result.output["title"] == "Requested Hindi Song"
    assert result.output["playback_confirmed"] is False
    assert result.output["url"] == (
        "https://www.youtube.com/watch?v=abcdefghijk&autoplay=1"
    )
    assert calls[-1] == ("press", "enter")


def test_play_youtube_video_ambiguous_opens_search_results(ctx: ToolContext, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.tools.apps._find_youtube_video", lambda _query: None)
    monkeypatch.setattr("app.tools.apps.webbrowser.open", lambda url: opened.append(url) or True)
    result = PlayYouTubeVideoTool(ctx).execute(
        PlayYouTubeVideoTool(ctx).validate_args({"query": "some Hindi cartoon"})
    )
    assert result.success
    assert result.output["video_selected"] is False
    assert opened[0].startswith("https://www.youtube.com/results?")


def test_play_youtube_video_search_failure_falls_back_to_results(ctx: ToolContext, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Linux")

    def fail_search(_query):
        raise ValueError("YouTube search failed: network unavailable")

    monkeypatch.setattr("app.tools.apps._find_youtube_video", fail_search)
    monkeypatch.setattr("app.tools.apps.webbrowser.open", lambda url: opened.append(url) or True)
    tool = PlayYouTubeVideoTool(ctx)
    result = tool.execute(tool.validate_args({"query": "Hindi cartoon song"}))

    assert result.success, result.error
    assert result.output["video_selected"] is False
    assert "network unavailable" in result.output["message"]
    assert opened[0].startswith("https://www.youtube.com/results?")


def test_write_read_text_document_and_overwrite_approval(ctx: ToolContext, tmp_path: Path):
    target = tmp_path / "notes.txt"
    writer = WriteTextFileTool(ctx)
    initial = writer.validate_args({"path": str(target), "content": "first"})
    assert writer.risk_for_args(initial) == RiskLevel.MEDIUM
    created = writer.execute(initial)
    assert created.success, created.error
    assert writer.verify(initial, created).passed

    refused = writer.execute(writer.validate_args({"path": str(target), "content": "replace"}))
    assert not refused.success
    replacement = writer.validate_args({"path": str(target), "content": "updated", "overwrite": True})
    assert writer.risk_for_args(replacement) == RiskLevel.HIGH
    assert writer.execute(replacement).success

    read = ReadTextFileTool(ctx).execute(ReadTextFileTool(ctx).validate_args({"path": str(target)}))
    assert read.success and read.output["content"] == "updated"


def test_open_file_uses_default_handler(ctx: ToolContext, tmp_path: Path, monkeypatch):
    target = tmp_path / "document.txt"
    target.write_text("hello")
    calls: list[str] = []
    monkeypatch.setattr(platform_tools, "open_path_with_os", lambda path: calls.append(path))
    result = OpenFileTool(ctx).execute(OpenFileTool(ctx).validate_args({"path": str(target)}))
    assert result.success, result.error
    assert calls == [str(target)]


def test_web_search_opens_google_results(ctx: ToolContext, monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr("app.tools.apps.webbrowser.open", lambda url: urls.append(url) or True)
    monkeypatch.setattr("app.tools.apps._reuse_youtube_window", lambda _url: None)
    result = WebSearchTool(ctx).execute(
        WebSearchTool(ctx).validate_args({"query": "piano music", "site": "youtube.com"})
    )
    assert result.success
    assert urls[0] == "https://www.youtube.com/results?search_query=piano+music"


def test_download_blocks_local_hosts_and_saves_public_file(ctx: ToolContext, tmp_path: Path, monkeypatch):
    tool = web_module.DownloadWebContentTool(ctx)
    blocked = tool.execute(
        tool.validate_args({"url": "http://127.0.0.1/private.txt", "output_path": str(tmp_path / "private.txt")})
    )
    assert not blocked.success

    class Response:
        status_code = 200
        headers = {"content-type": "text/plain", "content-length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"test"

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(web_module, "_validate_public_url", lambda url: None)
    monkeypatch.setattr(web_module.httpx, "Client", Client)
    target = tmp_path / "download.txt"
    result = tool.execute(
        tool.validate_args({"url": "https://example.com/download.txt", "output_path": str(target)})
    )
    assert result.success, result.error
    assert target.read_bytes() == b"test"
    args = tool.validate_args({"url": "https://example.com/download.txt", "output_path": str(target)})
    assert tool.verify(args, result).passed

    class HtmlResponse(Response):
        headers = {"content-type": "text/html; charset=utf-8", "content-length": "4"}

        def iter_bytes(self):
            yield b"<p/>"

    class HtmlClient(Client):
        def stream(self, *args, **kwargs):
            return HtmlResponse()

    monkeypatch.setattr(web_module.httpx, "Client", HtmlClient)
    webpage = tmp_path / "saved-page.html"
    page_args = tool.validate_args(
        {
            "url": "https://example.com/page",
            "output_path": str(webpage),
            "save_as_webpage": True,
        }
    )
    page_result = tool.execute(page_args)
    assert page_result.success, page_result.error
    assert webpage.read_bytes() == b"<p/>"
    assert tool.verify(page_args, page_result).passed


def test_screenshot_graceful_when_unavailable(ctx: ToolContext):
    tool = TakeScreenshotTool(ctx)
    assert tool.risk_level == RiskLevel.MEDIUM
    result = tool.execute(tool.validate_args({}))
    # In headless CI this fails gracefully instead of pretending to work.
    assert isinstance(result.success, bool)
    if not result.success:
        assert result.error


def test_system_setting_arguments_validate_supported_actions(ctx: ToolContext):
    tool = ManageSystemSettingTool(ctx)
    assert tool.validate_args({"setting": "brightness", "action": "increase"}).value == 10
    assert tool.validate_args({"setting": "volume", "action": "set", "value": 35}).value == 35
    assert tool.validate_args({"setting": "bluetooth", "action": "off"}).value is None
    with pytest.raises(ValueError, match="not supported"):
        tool.validate_args({"setting": "bluetooth", "action": "increase"})
    with pytest.raises(ValueError, match="required"):
        tool.validate_args({"setting": "volume", "action": "set"})


def test_system_setting_reads_and_adjusts_brightness_and_volume(ctx: ToolContext, monkeypatch):
    tool = ManageSystemSettingTool(ctx)
    state = {"brightness": 40, "volume": 75}
    monkeypatch.setattr(tool, "_require_windows", lambda: None)
    monkeypatch.setattr(tool, "_brightness", lambda: state["brightness"])
    monkeypatch.setattr(tool, "_set_brightness", lambda value: state.update(brightness=value))
    monkeypatch.setattr(tool, "_volume", lambda: state["volume"])
    monkeypatch.setattr(tool, "_set_volume", lambda value: state.update(volume=value))
    monkeypatch.setattr(tool, "_set_mute", lambda value: state.update(muted=value))

    dimmed = tool.execute(tool.validate_args({"setting": "brightness", "action": "decrease"}))
    louder = tool.execute(tool.validate_args({"setting": "volume", "action": "increase", "value": 15}))

    assert dimmed.success and dimmed.output == {"setting": "brightness", "value": 30}
    assert louder.success and louder.output == {"setting": "volume", "value": 90}


def test_system_setting_can_mute_and_toggle_bluetooth(ctx: ToolContext, monkeypatch):
    tool = ManageSystemSettingTool(ctx)
    calls = []
    monkeypatch.setattr(tool, "_require_windows", lambda: None)
    monkeypatch.setattr(tool, "_set_mute", lambda muted: calls.append(("mute", muted)))
    monkeypatch.setattr(tool, "_set_bluetooth", lambda enabled: calls.append(("bluetooth", enabled)))

    muted = tool.execute(tool.validate_args({"setting": "volume", "action": "mute"}))
    bluetooth = tool.execute(tool.validate_args({"setting": "bluetooth", "action": "off"}))

    assert muted.success and muted.output["muted"] is True
    assert bluetooth.success and bluetooth.output["enabled"] is False
    assert calls == [("mute", True), ("bluetooth", False)]


def test_system_setting_read_is_low_risk_but_changes_require_confirmation(ctx: ToolContext):
    tool = ManageSystemSettingTool(ctx)
    assert tool.risk_for_args(tool.validate_args({"setting": "volume", "action": "get"})) == RiskLevel.LOW
    assert tool.risk_for_args(tool.validate_args({"setting": "bluetooth", "action": "off"})) == RiskLevel.MEDIUM


def test_open_folder_fuzzy_matching(ctx: ToolContext, tmp_path: Path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(platform_tools, "open_path_with_os", lambda p: calls.append(p))
    target_folder = tmp_path / "FYP_Project"
    target_folder.mkdir()
    tool = OpenFolderTool(ToolContext(allowed_directories=[tmp_path]))
    result = tool.execute(tool.validate_args({"path": "FYP"}))
    assert result.success, result.error
    assert len(calls) == 1
    assert "FYP_Project" in calls[0]


def test_open_application_aliases_resolution(ctx: ToolContext):
    tool = OpenApplicationTool(ctx)
    assert tool._canonical_app_name("email") == "mail"
    assert tool._canonical_app_name("mail") == "mail"
    assert tool._canonical_app_name("email app") == "mail"
    assert tool._canonical_app_name("integrity") == "antigravity"
    assert tool._canonical_app_name("انٹیگریٹی") == "antigravity"
    assert tool._canonical_app_name("intellij") == "intellij"
    assert tool._canonical_app_name("انٹیلیجے") == "intellij"
    assert tool._canonical_app_name("word") == "word"
    assert tool._canonical_app_name("ورڈ") == "word"
    assert tool._canonical_app_name("gpt") == "chatgpt"
    assert tool._canonical_app_name("جی پی ٹی") == "chatgpt"


def test_open_email_opens_gmail_in_chrome(ctx: ToolContext, monkeypatch):
    launched: list[tuple[str, list[str] | None]] = []

    class FakeProc:
        pid = 9876

    def fake_launch(executable, args=None):
        launched.append((executable, args))
        return FakeProc()

    monkeypatch.setattr(platform_tools, "launch_program", fake_launch)
    monkeypatch.setattr("app.tools.apps.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.tools.apps.shutil.which", lambda _name: r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    monkeypatch.setattr(
        OpenApplicationTool,
        "_activate_running_app",
        lambda *_args: pytest.fail("Gmail launch should not use generic app matching"),
    )

    tool = OpenApplicationTool(ctx)
    args = tool.validate_args({"app_name": "mail"})
    assert tool.risk_for_args(args) == RiskLevel.LOW
    result = tool.execute(args)

    assert result.success, result.error
    assert result.output == {
        "app": "Gmail",
        "browser": "Chrome",
        "pid": 9876,
        "launched": True,
    }
    assert launched == [
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", ["https://mail.google.com"])
    ]


def test_desktop_control_window_state_validation():
    from app.tools.desktop import DesktopControlArgs
    parsed = DesktopControlArgs.model_validate({
        "target_window": "ChatGPT",
        "steps": [{"action": "minimize_window"}]
    })
    assert len(parsed.steps) == 1
    assert parsed.steps[0].action == "minimize_window"
