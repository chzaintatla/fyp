"""Application launching and URL opening tools."""
from __future__ import annotations

import platform
import json
import os
import re
import time
import shutil
import webbrowser
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus, urlencode, urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

from . import platform as _platform
from ..verification import BROWSER_PROCESSES, MAIL_PROCESSES, ProbeUnavailable, current, failed_result, verdict, wait_until, window_check
from .base import BaseTool, RiskLevel, ToolContext, ToolResult, Verification

# Known executables per platform. Windows entries rely on PATH/CreateProcess
# lookup; Linux/macOS entries are resolved with shutil.which.
APP_TARGETS: dict[str, dict[str, list[str]]] = {
    "powershell": {"Windows": ["powershell.exe"]},
    "chrome": {
        "Windows": ["chrome.exe"],
        "Linux": ["google-chrome", "chromium", "chromium-browser"],
        "Darwin": ["open", ],  # handled specially below
    },
    "notepad": {
        "Windows": ["notepad.exe"],
        "Linux": ["gedit", "gnome-text-editor", "kate"],
        "Darwin": ["open"],
    },
    "calc": {
        "Windows": ["calc.exe"],
        "Linux": ["gnome-calculator", "kcalc"],
        "Darwin": ["open"],
    },
    "explorer": {
        "Windows": ["explorer.exe"],
        "Linux": ["xdg-open"],
        "Darwin": ["open"],
    },
    "word": {
        "Windows": ["WINWORD.EXE", r"C:\Program Files\Microsoft Office\Office16\WINWORD.EXE", r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE"],
        "Linux": ["libreoffice"],
        "Darwin": ["open"],
    },
    "excel": {
        "Windows": ["EXCEL.EXE", r"C:\Program Files\Microsoft Office\Office16\EXCEL.EXE", r"C:\Program Files (x86)\Microsoft Office\Office16\EXCEL.EXE"],
        "Linux": ["libreoffice"],
        "Darwin": ["open"],
    },
    "powerpoint": {
        "Windows": ["POWERPNT.EXE", r"C:\Program Files\Microsoft Office\Office16\POWERPNT.EXE", r"C:\Program Files (x86)\Microsoft Office\Office16\POWERPNT.EXE"],
        "Linux": ["libreoffice"],
        "Darwin": ["open"],
    },
    "antigravity": {
        "Windows": ["antigravity.exe", "agy.exe"],
        "Linux": ["antigravity"],
        "Darwin": ["open"],
    },
    "intellij": {
        "Windows": ["idea64.exe", "idea.exe"],
        "Linux": ["idea"],
        "Darwin": ["open"],
    },
}

APP_ALIASES = {
    "mail": "mail",
    "email": "mail",
    "e-mail": "mail",
    "mail app": "mail",
    "email app": "mail",
    "mail client": "mail",
    "email client": "mail",
    "google chrome": "chrome",
    "google-chrome": "chrome",
    "google charmin honor": "chrome",
    "charmin honor": "chrome",
    "file explorer": "explorer",
    "windows explorer": "explorer",
    "calculator": "calc",
    "microsoft edge": "microsoft edge",
    "edge": "microsoft edge",
    # Antigravity / Integrity
    "antigravity": "antigravity",
    "integrity": "antigravity",
    "anti gravity": "antigravity",
    "anitgravity": "antigravity",
    "انٹیگریٹی": "antigravity",
    "انٹی گریوٹی": "antigravity",
    # IntelliJ
    "intellij": "intellij",
    "intellij idea": "intellij",
    "idea": "intellij",
    "انٹیلیجے": "intellij",
    # Microsoft Office
    "word": "word",
    "ms word": "word",
    "microsoft word": "word",
    "winword": "word",
    "ورڈ": "word",
    "excel": "excel",
    "ms excel": "excel",
    "microsoft excel": "excel",
    "powerpoint": "powerpoint",
    "ppt": "powerpoint",
    "ms powerpoint": "powerpoint",
    # ChatGPT
    "gpt": "chatgpt",
    "chatgpt": "chatgpt",
    "chat gpt": "chatgpt",
    "جی پی ٹی": "chatgpt",
    # Code editors
    "code": "visual studio code",
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "cursor": "cursor",
}

_DARWIN_APP_PATHS = {
    "chrome": ["/Applications/Google Chrome.app"],
    "notepad": ["/System/Applications/TextEdit.app"],
    "calc": ["/System/Applications/Calculator.app"],
}



# Process and title hints used by verify() to find the real window after a launch.
_APP_PROCESSES: dict[str, set[str]] = {
    "calc": {"calculatorapp.exe", "calculator.exe", "calc.exe"}, "notepad": {"notepad.exe"},
    "chrome": {"chrome.exe"}, "explorer": {"explorer.exe"}, "word": {"winword.exe"}, "excel": {"excel.exe"},
    "powerpoint": {"powerpnt.exe"}, "visual studio code": {"code.exe"}, "microsoft edge": {"msedge.exe"},
    "powershell": {"powershell.exe", "pwsh.exe", "windowsterminal.exe"}, "antigravity": {"antigravity.exe", "agy.exe"},
    "intellij": {"idea64.exe", "idea.exe"}, "cursor": {"cursor.exe"},
}
_APP_TITLES: dict[str, list[str]] = {
    "calc": ["calculator"], "visual studio code": ["visual studio code"], "explorer": ["file explorer", "home", "this pc"],
    "word": ["word"], "excel": ["excel"], "powerpoint": ["powerpoint"], "microsoft edge": ["edge"],
    "powershell": ["powershell"], "chrome": ["google chrome"], "notepad": ["notepad"],
}
_SITE_TITLES = {"youtube.com": ["youtube"], "mail.google.com": ["gmail", "inbox"], "web.whatsapp.com": ["whatsapp"],
                "chatgpt.com": ["chatgpt"], "drive.google.com": ["google drive", "my drive"]}


def _site_title_hints(url: str) -> list[str]:
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    if host in _SITE_TITLES:
        return _SITE_TITLES[host]
    label = host.split(".")[-2] if host.count(".") >= 1 else host
    return [label] if label else []

class OpenApplicationArgs(BaseModel):
    app_name: str = Field(description="Application to launch, e.g. 'chrome', 'notepad'")

    @field_validator("app_name")
    @classmethod
    def _name_not_a_path(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("app_name must not be empty")
        # Defense in depth: an application name is never a file path. This
        # guarantees a tool call can never ask the OS to open a document
        # (e.g. a .js file) "with" an application by smuggling a path here.
        if any(c in v for c in ("/", "\\", ":")):
            raise ValueError(
                f"app_name must be an application name, not a file path (got {v!r})"
            )
        return v


class OpenApplicationTool(BaseTool):
    name = "open_application"
    description = (
        "Launch an installed desktop application by its Start Menu name. "
        "Names are matched exactly or by a unique close spelling match; no executable commands or file paths."
    )
    risk_level = RiskLevel.LOW  # default; overridden per-args below
    args_schema = OpenApplicationArgs

    @staticmethod
    def _canonical_app_name(app_name: str) -> str:
        normalized = app_name.strip().lower()
        return APP_ALIASES.get(normalized, normalized)

    @staticmethod
    def _normalize_search_name(name: str) -> str:
        cleaned = re.sub(r"\.(lnk|url)$", "", name.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(app|application|program|desktop)\b", " ", cleaned, flags=re.IGNORECASE)
        return re.sub(r"[^a-z0-9]+", "", cleaned.casefold())

    @staticmethod
    def _start_menu_shortcuts() -> list[Path]:
        if platform.system() != "Windows":
            return []
        roots = [
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        ]
        shortcuts: list[Path] = []
        for root in roots:
            if not str(root) or not root.is_dir():
                continue
            try:
                shortcuts.extend(path for path in root.rglob("*.lnk") if path.is_file())
            except OSError:
                continue
        return shortcuts

    def _match_start_menu_shortcut(self, app_name: str) -> Path | None:
        from ..nlu import entity_match
        self._last_score = 1.0
        ranked = self._rank_start_menu_shortcuts(app_name)
        if not ranked:
            return None
        best_score, paths = ranked[0]
        unique = len(paths) == 1 and (len(ranked) == 1 or best_score - ranked[1][0] >= entity_match.GAP)
        if unique and best_score >= entity_match.SAY:  # 0.60-0.85: opened, and the reply says what was understood
            self._last_score = best_score
            return paths[0]
        return None

    def _rank_start_menu_shortcuts(self, app_name: str) -> list[tuple[float, list[Path]]]:
        from ..nlu import correction_memory, entity_match
        heard_as = correction_memory.current().get(entity_match._norm(app_name))
        if heard_as:
            app_name = heard_as  # the user corrected this spelling before: "fire fox" really means Firefox
        requested = self._canonical_app_name(app_name)
        queries = {
            normalized
            for normalized in (
                self._normalize_search_name(app_name),
                self._normalize_search_name(requested),
            )
            if normalized
        }
        if not queries:
            return []
        names: dict[str, list[Path]] = {}
        for shortcut in self._start_menu_shortcuts():
            label = shortcut.stem
            normalized = self._normalize_search_name(label)
            if normalized:
                names.setdefault(normalized, []).append(shortcut)
        exact_matches = [names[query] for query in queries if query in names]
        if exact_matches:
            matches = [path for group in exact_matches for path in group]
            unique_matches = list(dict.fromkeys(matches))
            return [(1.0, unique_matches)]
        def _score_match(query: str, label: str) -> float:
            if query == label:
                return 1.0
            if label.startswith(query) or query.startswith(label):
                return 0.95
            if query in label or label in query:
                return 0.90
            return max(SequenceMatcher(None, query, label).ratio(), entity_match.score(query, label))

        scores = sorted(
            (
                (max(_score_match(query, label) for query in queries), paths)
                for label, paths in names.items()
            ),
            key=lambda entry: entry[0],
            reverse=True,
        )
        return scores

    def _candidate_app_names(self, app_name: str, limit: int = 5) -> list[str]:
        seen: list[str] = []
        for score, paths in self._rank_start_menu_shortcuts(app_name):
            if score < 0.45:
                continue
            for path in paths:
                if path.stem not in seen:
                    seen.append(path.stem)
                if len(seen) >= limit:
                    return seen
        return seen

    def _activate_running_app(self, app_name: str) -> str | None:
        if platform.system() != "Windows":
            return None
        try:
            import pyautogui
        except ImportError:
            return None
        aliases = {
            "calc": ("calculator",),
            "chrome": ("google chrome",),
            "explorer": ("file explorer", "windows explorer"),
        }
        shortcut = self._match_start_menu_shortcut(app_name)
        terms = [app_name, self._canonical_app_name(app_name), *aliases.get(app_name, ())]
        if shortcut:
            terms.append(shortcut.stem)
        normalized_terms = {
            self._normalize_search_name(term)
            for term in terms
            if self._normalize_search_name(term)
        }
        matches = [
            window
            for window in pyautogui.getAllWindows()
            if window.title
            and any(
                normalized in self._normalize_search_name(window.title)
                for normalized in normalized_terms
            )
        ]
        if not matches:
            return None
        active = pyautogui.getActiveWindow()
        target = active if active in matches else matches[0]
        target.activate()
        return target.title

    def risk_for_args(self, args: BaseModel | None) -> RiskLevel:
        if args is None:
            return RiskLevel.LOW
        a = OpenApplicationArgs.model_validate(args.model_dump())
        app = self._canonical_app_name(a.app_name)
        if app == "mail":
            return RiskLevel.LOW
        if app in self.ctx.approved_applications or self._match_start_menu_shortcut(app) is not None:
            return RiskLevel.LOW
        return RiskLevel.HIGH

    @staticmethod
    def _windows_registered_app_path(executable: str) -> str | None:
        try:
            import winreg
        except ImportError:
            return None

        app_path_key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, app_path_key) as key:
                    path, _ = winreg.QueryValueEx(key, None)
            except OSError:
                continue
            if Path(path).is_file():
                return path
        return None

    def _find_executable(self, app: str) -> str | Path | None:
        system = platform.system()
        candidates = APP_TARGETS.get(app, {}).get(system, [])
        if system == "Windows" and app == "chrome":
            chrome_relative_path = Path("Google") / "Chrome" / "Application" / "chrome.exe"
            candidates = [
                *candidates,
                *(
                    str(Path(root) / chrome_relative_path)
                    for root in (
                        os.environ.get("ProgramFiles"),
                        os.environ.get("ProgramFiles(x86)"),
                        os.environ.get("LOCALAPPDATA"),
                    )
                    if root
                ),
            ]
        for candidate in candidates:
            found = shutil.which(candidate)
            if found:
                return found
            if system == "Windows":
                registered = self._windows_registered_app_path(candidate)
                if registered:
                    return registered
        if system == "Windows" and candidates:
            # Let Windows resolve it via PATH / App Paths.
            return candidates[0]
        if system == "Windows":
            return self._match_start_menu_shortcut(app)
        if system == "Darwin" and app in _DARWIN_APP_PATHS:
            return _DARWIN_APP_PATHS[app][0]
        return None

    def execute(self, args: BaseModel) -> ToolResult:
        try:
            raw = args.model_dump() if hasattr(args, "model_dump") else args
            a = OpenApplicationArgs.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - invalid input is a normal failure
            return ToolResult(success=False, error=f"Invalid app_name: {exc}")
        app = self._canonical_app_name(a.app_name)
        system = platform.system()
        try:
            if app == "mail":
                if system == "Darwin":
                    proc = _platform.launch_program(
                        "open",
                        ["-a", _DARWIN_APP_PATHS["chrome"][0], "https://mail.google.com"],
                    )
                else:
                    chrome = self._find_executable("chrome")
                    if chrome is None:
                        return ToolResult(
                            success=False,
                            error="Google Chrome was not found; cannot open Gmail in Chrome.",
                        )
                    proc = _platform.launch_program(chrome, ["https://mail.google.com"])
                return ToolResult(
                    success=True,
                    output={"app": "Gmail", "browser": "Chrome", "pid": proc.pid, "launched": True},
                )
            existing_window = self._activate_running_app(app)
            if existing_window:
                return ToolResult(
                    success=True,
                    output={"app": app, "window": existing_window, "reused_window": True, "launched": True},
                )
            if system == "Darwin" and app in _DARWIN_APP_PATHS:
                proc = _platform.launch_program("open", ["-a", _DARWIN_APP_PATHS[app][0]])
                return ToolResult(success=True, output={"app": app, "pid": proc.pid, "launched": True})
            exe = self._find_executable(app)
            if exe is None:
                if app in ("chatgpt", "gpt"):
                    webbrowser.open("https://chatgpt.com")
                    return ToolResult(
                        success=True,
                        output={"app": "ChatGPT", "launched": True, "url": "https://chatgpt.com"},
                    )
                candidates = self._candidate_app_names(a.app_name)
                if candidates:
                    shown = ", ".join(candidates)
                    return ToolResult(
                        success=False,
                        output={"candidates": candidates, "needs_confirmation": True},
                        error=(
                            f"Application '{app}' is not an exact unique match. "
                            f"Closest installed apps: {shown}. Ask the user which one to open, "
                            "then call open_application with that exact name."
                        ),
                    )
                return ToolResult(
                    success=False,
                    error=f"Application '{app}' was not found on this system ({system}).",
                )
            if isinstance(exe, Path) and exe.suffix.lower() == ".lnk":
                _platform.open_path_with_os(str(exe))
                output = {"app": app, "matched_shortcut": exe.stem, "launched": True}
                if getattr(self, "_last_score", 1.0) < 0.85:
                    output.update({"heard": a.app_name, "understood": exe.stem})
                return ToolResult(success=True, output=output)
            proc = _platform.launch_program(exe)
        except Exception as exc:  # noqa: BLE001 - report honestly
            return ToolResult(success=False, error=f"Could not launch '{app}': {exc}")
        return ToolResult(success=True, output={"app": app, "pid": proc.pid, "launched": True})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        out = result.output or {}
        app = str(out.get("app") or OpenApplicationArgs.model_validate(args.model_dump()).app_name).casefold()
        if out.get("url"):  # ChatGPT fallback opens a website
            seen, windows = window_check(title_contains=["chatgpt"], processes=BROWSER_PROCESSES)
            return verdict([("browser window shows ChatGPT", seen)], windows)
        if app == "gmail":
            seen, windows = window_check(title_contains=["gmail", "inbox"], processes={"chrome.exe"})
            return verdict([("Chrome window shows Gmail", seen)], windows)
        pid = out.get("pid")
        alive = bool(pid) and current().process_alive(int(pid))
        titles = _APP_TITLES.get(app, [app])
        processes = _APP_PROCESSES.get(app, {f"{app.replace(' ', '')}.exe"})
        seen, windows = window_check(title_contains=titles, processes=processes, match="any")
        return verdict([("app window is open", seen)], {"pid": pid, "pid_alive": alive, **windows},
                       details=None if seen else "The app did not show a window in time.")


class OpenUrlArgs(BaseModel):
    url: str = Field(description="Website URL to open, e.g. https://example.com")
    browser: Literal["default", "chrome"] = "default"

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("Only http:// and https:// URLs are allowed")
        return v


def _reuse_youtube_window(url: str) -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        import pyautogui
    except ImportError:
        return None
    from .desktop import is_shell_target

    windows = [
        window for window in pyautogui.getAllWindows()
        if window.title and "youtube" in window.title.casefold() and not is_shell_target(window.title)
    ]
    if not windows:
        return None
    active = pyautogui.getActiveWindow()
    target = active if active in windows else windows[0]
    try:
        target.activate()
    except Exception as exc:  # noqa: BLE001 - pygetwindow raises "Error code 0" even when activation worked
        if "Error code from Windows: 0" not in str(exc):
            raise
    pyautogui.hotkey("ctrl", "l")
    pyautogui.write(url, interval=0.005)
    pyautogui.press("enter")
    return target.title


def _find_youtube_video(query: str) -> tuple[str, str] | None:
    search_url = f"https://www.youtube.com/results?{urlencode({'search_query': query})}"
    try:
        with httpx.Client(
            timeout=12,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AgentDeskAI/1.0)"},
        ) as client:
            with client.stream("GET", search_url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type:
                    raise ValueError("YouTube did not return a search results page")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 5 * 1024 * 1024:
                        raise ValueError("YouTube search response exceeded the 5 MB limit")
                    chunks.append(chunk)
        page = b"".join(chunks).decode("utf-8", errors="replace")
    except (httpx.HTTPError, OSError) as exc:
        raise ValueError(f"YouTube search failed: {exc}") from exc

    decoder = json.JSONDecoder()
    renderers = re.finditer(r'"videoRenderer"\s*:\s*(\{)', page)
    matches: list[tuple[float, str, str]] = []
    query_words = set(re.findall(r"[a-z0-9]+", query.casefold()))
    for match in renderers:
        try:
            renderer, _ = decoder.raw_decode(page, match.start(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(renderer, dict):
            continue
        video_id = renderer.get("videoId")
        title_data = renderer.get("title", {})
        runs = title_data.get("runs", []) if isinstance(title_data, dict) else []
        title = " ".join(
            run.get("text", "") for run in runs if isinstance(run, dict)
        ).strip()
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or not title:
            continue
        title_words = set(re.findall(r"[a-z0-9]+", title.casefold()))
        overlap = len(query_words & title_words) / max(len(query_words), 1)
        similarity = SequenceMatcher(
            None,
            " ".join(sorted(query_words)),
            " ".join(sorted(title_words)),
        ).ratio()
        score = overlap * 0.7 + similarity * 0.3
        matches.append((score, video_id, title))
        if len(matches) >= 20:
            break
    matches.sort(key=lambda item: item[0], reverse=True)
    if not matches or matches[0][0] < 0.3:
        return None
    if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.035 and matches[0][0] < 0.9:
        return None
    return matches[0][1], matches[0][2]


class OpenUrlTool(BaseTool):
    name = "open_url"
    isolation = "thread"  # may open the tab through the extension socket held by the server
    description = "Open an http(s) website in the default browser or explicitly requested Chrome."
    risk_level = RiskLevel.LOW
    args_schema = OpenUrlArgs

    def execute(self, args: BaseModel) -> ToolResult:
        try:
            raw = args.model_dump() if hasattr(args, "model_dump") else args
            a = OpenUrlArgs.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - invalid input is a normal failure
            return ToolResult(success=False, error=str(exc))
        if a.browser == "chrome":
            from ..browser_bridge import bridge
            if bridge.clients and bridge.supports("open"):
                result = bridge.request("open", url=a.url, browser="chrome")
                if result.get("success") and isinstance(result.get("tab_id"), int):
                    return ToolResult(success=True, output=result)
                return ToolResult(success=False, error=result.get("error", "Browser did not confirm the requested tab."))
            if bridge.clients:
                # Extension older than 1.1 cannot open tabs: launch Chrome, then confirm through the
                # extension's inspect that the tab really appeared in the connected profile.
                return self._open_and_find_tab(a.url)
            try:
                executable = OpenApplicationTool(self.ctx)._find_executable("chrome")
                if not executable:
                    raise OSError("Chrome is not installed or its executable was not found")
                _platform.launch_program(str(executable), [a.url])
                return ToolResult(success=True, output={"url": a.url, "browser": "chrome", "launched": True})
            except OSError as exc:
                return ToolResult(success=False, error=f"Could not open the requested Chrome browser: {exc}")
        try:
            hostname = urlsplit(a.url).hostname or ""
            if hostname.lower().removeprefix("www.").endswith("youtube.com"):
                reused_title = _reuse_youtube_window(a.url)
                if reused_title:
                    return ToolResult(
                        success=True,
                        output={"url": a.url, "launched": True, "reused_window": reused_title},
                    )
        except Exception as exc:  # noqa: BLE001 - report navigation errors clearly
            return ToolResult(success=False, error=f"Could not navigate the existing YouTube window: {exc}")
        try:
            launched = webbrowser.open(a.url)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Could not open URL: {exc}")
        if not launched:
            return ToolResult(success=False, error="The system reported that no browser could handle the URL")
        return ToolResult(success=True, output={"url": a.url, "launched": True})

    def _open_and_find_tab(self, url: str, wait_seconds: float = 6.0) -> ToolResult:
        from ..browser_bridge import bridge

        executable = OpenApplicationTool(self.ctx)._find_executable("chrome")
        if not executable:
            return ToolResult(success=False, error="Chrome is not installed or its executable was not found.")
        try:
            _platform.launch_program(str(executable), [url])
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not open Chrome: {exc}")
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            observed = bridge.request("inspect")
            tabs = [t for t in observed.get("tabs", []) if isinstance(t, dict) and _same_url(str(t.get("url", "")), url)]
            if tabs:
                return ToolResult(success=True, output={"url": url, "browser": "chrome", "tab_id": tabs[0].get("id"),
                                                        "launched": True, "via": "chrome+inspect (extension < 1.1)"})
            time.sleep(0.4)
        return ToolResult(success=False, error=(
            "Chrome opened, but the connected browser profile did not show the tab. Reload Agent X Browser Bridge "
            "once at chrome://extensions (version 1.1+ then updates itself)."))

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        out = result.output or {}
        if isinstance(out.get("tab_id"), int):
            # The browser extension (a separate process) created/activated this tab and reported its URL.
            from ..browser_bridge import bridge

            tabs = bridge.request("inspect")
            ids = {t.get("id") for t in tabs.get("tabs", [])} if tabs.get("success") else set()
            return verdict([("extension reports the tab open", out["tab_id"] in ids)],
                           {"tab_id": out["tab_id"], "tabs_seen": len(ids)})
        url = out.get("url") or OpenUrlArgs.model_validate(args.model_dump()).url
        seen, windows = window_check(title_contains=_site_title_hints(url), processes=BROWSER_PROCESSES)
        return verdict([("browser window shows the site", seen)], {"url": url, **windows})



def _same_url(a: str, b: str) -> bool:
    def norm(u: str) -> str:
        parts = urlsplit(u)
        return f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}{parts.path or '/'}{'?' + parts.query if parts.query else ''}"
    try:
        return norm(a) == norm(b)
    except ValueError:
        return False


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="Search terms")
    site: str | None = Field(default=None, description="Optional website to search, e.g. youtube.com")

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Open Google search results for a query, optionally limited to a website such as YouTube."
    risk_level = RiskLevel.LOW
    args_schema = WebSearchArgs

    def execute(self, args: BaseModel) -> ToolResult:
        try:
            a = WebSearchArgs.model_validate(args.model_dump())
        except Exception as exc:  # noqa: BLE001 - invalid input is a normal tool failure
            return ToolResult(success=False, error=f"Invalid search request: {exc}")
        is_youtube = bool(
            a.site
            and a.site.strip().lower().removeprefix("www.") in {"youtube.com", "youtube"}
        )
        query = f"site:youtube.com {a.query}" if is_youtube else (
            f"site:{a.site.strip()} {a.query}" if a.site and a.site.strip() else a.query
        )
        url = (
            f"https://www.youtube.com/results?{urlencode({'search_query': a.query})}"
            if is_youtube
            else f"https://www.google.com/search?q={quote_plus(query)}"
        )
        if is_youtube:
            try:
                reused_title = _reuse_youtube_window(url)
            except Exception as exc:  # noqa: BLE001 - report navigation errors clearly
                return ToolResult(success=False, error=f"Could not navigate the existing YouTube window: {exc}")
            if reused_title:
                return ToolResult(
                    success=True,
                    output={
                        "query": query,
                        "url": url,
                        "launched": True,
                        "reused_window": reused_title,
                    },
                )
        try:
            launched = webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 - report system browser errors
            return ToolResult(success=False, error=f"Could not open search results: {exc}")
        if not launched:
            return ToolResult(success=False, error="The system reported that no browser could handle the search")
        return ToolResult(success=True, output={"query": query, "url": url, "launched": True})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        query = str(result.output.get("query") or "")
        hints = [w for w in re.findall(r"\w+", query) if len(w) > 2][:3] + ["search"]
        seen, windows = window_check(title_contains=hints, processes=BROWSER_PROCESSES)
        return verdict([("browser window shows the search", seen)], {"url": result.output.get("url"), **windows})


def _nudge_youtube_playback(video_id: str, wait_seconds: float = 4.0) -> bool:
    """If the browser extension is connected, press play on the new tab (autoplay is often blocked)."""
    from ..browser_bridge import bridge

    if not (bridge.clients and bridge.supports("resume")):
        return False
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        tabs = bridge.request("inspect").get("tabs", [])
        match = [t for t in tabs if isinstance(t, dict) and f"v={video_id}" in str(t.get("url", ""))]
        if match:
            reply = bridge.request("resume", tab_id=match[0]["id"], expected_url=match[0]["url"])
            return bool(reply.get("success"))
        time.sleep(0.5)
    return False


class PlayYouTubeVideoArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300, description="Specific song/video title and useful details")

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Enter a video or song title")
        return value


class PlayYouTubeVideoTool(BaseTool):
    name = "play_youtube_video"
    description = (
        "When the user explicitly asks to play a named video or song, find a close YouTube result "
        "and open its watch page in an existing YouTube window or the default browser. "
        "If matching is ambiguous, show search results instead. Playback may be blocked by browser policy."
    )
    risk_level = RiskLevel.LOW
    args_schema = PlayYouTubeVideoArgs

    def execute(self, args: BaseModel) -> ToolResult:
        try:
            a = PlayYouTubeVideoArgs.model_validate(args.model_dump())
        except Exception as exc:  # noqa: BLE001 - invalid input is a normal tool failure
            return ToolResult(success=False, error=f"Invalid YouTube play request: {exc}")
        search_url = f"https://www.youtube.com/results?{urlencode({'search_query': a.query})}"
        try:
            match = _find_youtube_video(a.query)
        except ValueError as exc:
            try:
                reused_title = _reuse_youtube_window(search_url)
                opened = bool(reused_title) or webbrowser.open(search_url)
            except Exception as open_exc:  # noqa: BLE001 - report both lookup and browser failures
                return ToolResult(
                    success=False,
                    error=f"{exc}; could not open YouTube search results: {open_exc}",
                )
            if not opened:
                return ToolResult(success=False, error=f"{exc}; no browser could open YouTube search results")
            return ToolResult(
                success=True,
                output={
                    "query": a.query,
                    "results_url": search_url,
                    "video_selected": False,
                    "message": f"Could not identify a video automatically ({exc}); opened search results instead.",
                },
            )
        if match is None:
            try:
                reused_title = _reuse_youtube_window(search_url)
                opened = bool(reused_title) or webbrowser.open(search_url)
            except Exception as exc:  # noqa: BLE001 - report system browser errors
                return ToolResult(success=False, error=f"Could not open YouTube search results: {exc}")
            if not opened:
                return ToolResult(success=False, error="No browser could open YouTube search results")
            return ToolResult(
                success=True,
                output={
                    "query": a.query,
                    "results_url": search_url,
                    "video_selected": False,
                    "message": "No single confident video match; opened YouTube results for you to choose.",
                },
            )

        video_id, title = match
        watch_url = f"https://www.youtube.com/watch?{urlencode({'v': video_id, 'autoplay': '1'})}"
        try:
            reused_title = _reuse_youtube_window(watch_url)
            opened = bool(reused_title) or webbrowser.open(watch_url)
        except Exception as exc:  # noqa: BLE001 - report system browser errors
            return ToolResult(success=False, error=f"Could not open the selected YouTube video: {exc}")
        if not opened:
            return ToolResult(success=False, error="No browser could open the selected YouTube video")
        nudged = _nudge_youtube_playback(video_id)
        return ToolResult(
            success=True,
            output={
                "query": a.query,
                "title": title,
                "url": watch_url,
                "video_selected": True,
                "opened_window": reused_title,
                "playback_confirmed": False,
                "play_requested_via_extension": nudged,
                "message": "Opened the closest matching video; playback is checked next.",
            },
        )

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        """Search results: the YouTube window is enough. A chosen video: Windows must report it playing."""
        if not result.success:
            return failed_result(result)
        if not result.output.get("video_selected"):
            seen, windows = window_check(title_contains=["youtube"], processes=BROWSER_PROCESSES)
            return verdict([("browser window shows YouTube results", seen)], {"url": result.output.get("results_url"), **windows})
        title = str(result.output.get("title") or "")
        words = [w.casefold() for w in re.findall(r"\w{4,}", title)][:4]

        def playing():
            state = current().media_session()
            if state and state["status"] == "playing" and (not words or any(w in state["title"].casefold() for w in words)
                                                            or "youtube" in state["title"].casefold()):
                return state
            return None
        try:
            ok, state = wait_until(playing, timeout=2 * current().poll_timeout, interval=0.5)
        except ProbeUnavailable as exc:
            seen, windows = window_check(title_contains=["youtube"], processes=BROWSER_PROCESSES)
            return Verification(passed=False, checks=[f"YouTube window open: {seen}", "playback not readable"],
                                evidence=windows, details=str(exc))
        return verdict([("Windows reports the video playing", bool(ok))],
                       {"url": result.output.get("url"), "media_session": state if ok else None},
                       details=None if ok else "The video opened but did not start (autoplay may be blocked). Press Play once.")


class ComposeEmailArgs(BaseModel):
    to: str = Field(min_length=3, max_length=254, description="Recipient email address")
    subject: str = Field(max_length=200, description="Email subject")
    body: str = Field(max_length=4000, description="Email body")

    @field_validator("to")
    @classmethod
    def _valid_recipient(cls, value: str) -> str:
        value = value.strip()
        if value.count("@") != 1 or any(char.isspace() for char in value):
            raise ValueError("to must be a single valid email address")
        local, domain = value.split("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("to must be a single valid email address")
        return value


class ComposeEmailTool(BaseTool):
    name = "compose_email"
    description = (
        "Prepare an email draft in the default email app using a mailto link. "
        "Never sends the email; the user must review and send or reject it."
    )
    risk_level = RiskLevel.MEDIUM
    args_schema = ComposeEmailArgs

    def execute(self, args: BaseModel) -> ToolResult:
        try:
            a = ComposeEmailArgs.model_validate(args.model_dump())
        except Exception as exc:  # noqa: BLE001 - invalid input is a normal tool failure
            return ToolResult(success=False, error=f"Invalid email draft: {exc}")
        url = f"mailto:{a.to}?{urlencode({'subject': a.subject, 'body': a.body})}"
        try:
            launched = webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 - report system browser errors
            return ToolResult(success=False, error=f"Could not open the email draft: {exc}")
        if not launched:
            return ToolResult(
                success=False,
                error="No email application handled the draft. Configure a default mail app and try again.",
            )
        return ToolResult(
            success=True,
            output={
                "to": a.to,
                "subject": a.subject,
                "draft_opened": True,
                "sent": False,
                "message": "Draft opened for review; it has not been sent.",
            },
        )

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        a = ComposeEmailArgs.model_validate(args.model_dump())
        hints = [h for h in (a.subject[:40], "new message", "compose", "untitled - message") if h]
        seen, windows = window_check(title_contains=hints, processes=MAIL_PROCESSES | BROWSER_PROCESSES)
        return verdict([("a draft window is open (not sent)", seen)], {"to": a.to, **windows})


def app_tools(ctx: ToolContext | None = None) -> list[BaseTool]:
    ctx = ctx or ToolContext.from_settings()
    return [
        OpenApplicationTool(ctx),
        OpenUrlTool(ctx),
        WebSearchTool(ctx),
        PlayYouTubeVideoTool(ctx),
        ComposeEmailTool(ctx),
    ]
