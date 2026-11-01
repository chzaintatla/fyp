"""Index of the names the user can say: installed apps, recent files, folders, contacts, page titles.

Names only (never file contents). Built in a background thread at start-up and every 10 minutes; a lookup never waits
for a rebuild. Each source is a small function so a failing one (no PowerShell, no registry) is skipped, not fatal.
Sources not available yet (browser titles from the extension, e-mail contacts) are filled with `add_external`.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger("agentdesk.entity_index")

REFRESH_SECONDS = 600
MAX_FILES = 400
MAX_FOLDER_NAMES = 1500
_USER_FOLDERS = ("Documents", "Desktop", "Downloads", "Pictures", "Videos", "Music")
_SKIP = {".git", "node_modules", ".venv", "appdata", "__pycache__", "$recycle.bin", ".agentdesk_trash"}


@dataclass(frozen=True)
class Entity:
    kind: str        # app | file | folder | contact | page
    name: str
    path: str = ""
    source: str = ""


def _start_menu_apps() -> list[Entity]:
    from ..tools.apps import OpenApplicationTool
    return [Entity("app", p.stem, str(p), "start_menu") for p in OpenApplicationTool._start_menu_shortcuts()]


def _alias_apps() -> list[Entity]:
    from ..tools.apps import APP_ALIASES, APP_TARGETS
    names = {v for v in APP_ALIASES.values() if v.isascii()} | set(APP_TARGETS)
    return [Entity("app", n, "", "builtin") for n in sorted(names)]


def _app_paths_apps() -> list[Entity]:
    try:
        import winreg
    except ImportError:
        return []
    out: list[Entity] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths") as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    exe = winreg.EnumKey(root, i)
                    out.append(Entity("app", exe.removesuffix(".exe").removesuffix(".EXE"), exe, "app_paths"))
        except OSError:
            continue
    return out


def _powershell_json(script: str, timeout: int = 20):
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    text = result.stdout.strip()
    return json.loads(text) if text else []


def _uwp_apps() -> list[Entity]:
    """Store apps (Calculator, Settings, WhatsApp...): name + app id. Slow (about a second), so only in the background."""
    rows = _powershell_json("Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    rows = [rows] if isinstance(rows, dict) else rows
    return [Entity("app", r["Name"], r.get("AppID", ""), "uwp") for r in rows if r.get("Name")]


def _recent_files() -> list[Entity]:
    recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    if not recent.is_dir():
        return []
    items = []
    for p in recent.glob("*.lnk"):
        try:
            items.append((p.stat().st_mtime, p))
        except OSError:
            continue
    items.sort(reverse=True)
    return [Entity("file", p.stem, str(p), "recent") for _, p in items[:MAX_FILES]]


def _user_folder_names() -> list[Entity]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    out: list[Entity] = []
    files = 0
    for sub in _USER_FOLDERS:
        base = home / sub
        if not base.is_dir():
            continue
        out.append(Entity("folder", sub, str(base), "user_folder"))
        stack = [(base, 0)]
        while stack and len(out) < MAX_FOLDER_NAMES:
            folder, depth = stack.pop()
            try:
                with os.scandir(folder) as it:
                    for e in it:
                        if e.name.casefold() in _SKIP or e.name.startswith("."):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            out.append(Entity("folder", e.name, e.path, "user_folder"))
                            if depth < 2:
                                stack.append((Path(e.path), depth + 1))
                        elif files < MAX_FILES * 3:
                            files += 1
                            out.append(Entity("file", e.name, e.path, "user_folder"))
            except OSError:
                continue
    return out


def _quick_access() -> list[Entity]:
    rows = _powershell_json(
        "$s=New-Object -ComObject Shell.Application;"
        "$s.Namespace('shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}').Items()|ForEach-Object{[pscustomobject]@{Name=$_.Name;Path=$_.Path}}|ConvertTo-Json -Compress")
    rows = [rows] if isinstance(rows, dict) else rows
    return [Entity("folder", r["Name"], r.get("Path", ""), "quick_access") for r in rows if r.get("Name")]


DEFAULT_SOURCES: tuple[Callable[[], list[Entity]], ...] = (
    _alias_apps, _start_menu_apps, _app_paths_apps, _uwp_apps, _recent_files, _user_folder_names, _quick_access)


class EntityIndex:
    def __init__(self, sources: Iterable[Callable[[], list[Entity]]] | None = None):
        self._sources = tuple(sources) if sources is not None else DEFAULT_SOURCES
        self._entries: list[Entity] = []
        self._external: dict[str, list[Entity]] = {}
        self._prepared: dict[tuple, object] = {}
        self._lock = threading.Lock()
        self.built_at = 0.0
        self.build_seconds = 0.0
        self.source_counts: dict[str, int] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def refresh(self) -> int:
        started = time.perf_counter()
        seen: dict[tuple[str, str], Entity] = {}
        counts: dict[str, int] = {}
        for source in self._sources:
            try:
                rows = source()
            except Exception as exc:  # noqa: BLE001 - one broken source must not empty the index
                log.warning("entity source %s failed: %s", getattr(source, "__name__", source), exc)
                continue
            counts[getattr(source, "__name__", "source")] = len(rows)
            for e in rows:
                if e.name.strip():
                    seen.setdefault((e.kind, e.name.casefold()), e)
        with self._lock:
            self._entries = list(seen.values())
            self.source_counts = counts
            self._prepared = {}
            self.built_at = time.time()
            self.build_seconds = round(time.perf_counter() - started, 3)
        return len(self._entries)

    def add_external(self, source: str, kind: str, names: Iterable[str]) -> None:
        """Names that only another component knows (browser page titles, e-mail contacts)."""
        with self._lock:
            self._external[source] = [Entity(kind, n, "", source) for n in names if n.strip()]
            self._prepared = {}

    def entries(self, kinds: Iterable[str] | None = None) -> list[Entity]:
        with self._lock:
            rows = self._entries + [e for group in self._external.values() for e in group]
        if kinds is None:
            return rows
        wanted = set(kinds)
        return [e for e in rows if e.kind in wanted]

    def prepared(self, kinds: Iterable[str] | None = None):
        """Entries ready for fast matching (cached until the next refresh or external update)."""
        from .entity_match import Prepared
        key = tuple(sorted(kinds)) if kinds else ()
        with self._lock:
            hit = self._prepared.get(key)
        if hit is None:
            hit = Prepared(self.entries(kinds))
            with self._lock:
                self._prepared[key] = hit
        return hit

    def names(self, kind: str, limit: int = 50) -> list[str]:
        return [e.name for e in self.entries([kind])][:limit]

    def start(self, interval: float = REFRESH_SECONDS) -> None:
        if self._thread and self._thread.is_alive():
            return

        def loop() -> None:
            while not self._stop.is_set():
                self.refresh()
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, name="entity-index", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


_INDEX: EntityIndex | None = None
_INDEX_LOCK = threading.Lock()


def get_index() -> EntityIndex:
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            _INDEX = EntityIndex(DEFAULT_SOURCES if platform.system() == "Windows" else (_alias_apps,))
        return _INDEX


def set_index(index: EntityIndex | None) -> None:
    global _INDEX
    with _INDEX_LOCK:
        _INDEX = index
