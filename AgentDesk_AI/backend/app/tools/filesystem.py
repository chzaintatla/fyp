"""Filesystem tools: folders, files, archives.

Most paths are validated against allowed directories. Folder creation and
high-risk deletion have their own broader path policies; destructive
operations require explicit user approval via the orchestration layer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from difflib import SequenceMatcher
from pydantic import BaseModel, Field

from ..security.paths import is_allowed_root, is_executable_path, resolve_within_allowed
from . import platform as _platform
from ..verification import failed_result, file_evidence, text_sha256, verdict, window_check
from .base import BaseTool, RiskLevel, ToolContext, ToolResult, Verification


def _allowed_root_for(path: Path, allowed: list[Path]) -> Path:
    roots = sorted((p.expanduser().resolve() for p in allowed), key=lambda p: len(p.parts), reverse=True)
    for root in roots:
        if path == root or root in path.parents:
            return root
    raise ValueError(f"Path is outside the allowed directories: {path}")


def _trash_root_for(path: Path, allowed: list[Path]) -> Path:
    try:
        return _allowed_root_for(path, allowed) / ".agentdesk_trash"
    except ValueError:
        return path.parent / ".agentdesk_trash"


def _trash_manifest_root(allowed: list[Path]) -> Path:
    roots = sorted(
        (root.expanduser().resolve() for root in allowed),
        key=lambda root: len(root.parts),
        reverse=True,
    )
    return (roots[0] if roots else Path.home().resolve()) / ".agentdesk_trash"


def _trash_manifest_path(original: Path, trash_id: str, allowed: list[Path]) -> Path:
    try:
        root = _allowed_root_for(original, allowed) / ".agentdesk_trash"
    except ValueError:
        root = _trash_manifest_root(allowed)
    return root / f"{trash_id}.json"


def _trash_manifest_roots(allowed: list[Path]) -> list[Path]:
    roots = [
        root.expanduser().resolve() / ".agentdesk_trash"
        for root in allowed
    ]
    roots.append(_trash_manifest_root(allowed))
    return list(dict.fromkeys(roots))


def _resolve_delete_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"Only absolute paths are accepted (got {raw_path!r}).")
    try:
        return candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot resolve path {raw_path!r}: {exc}") from exc


def _is_in_internal_trash(path: Path, allowed: list[Path]) -> bool:
    if any(part.casefold() == ".agentdesk_trash" for part in path.parts):
        return True
    for root in allowed:
        trash = root.expanduser().resolve() / ".agentdesk_trash"
        if path == trash or trash in path.parents:
            return True
    return False


def _windows_drive_roots() -> list[Path]:
    if os.name != "nt":
        return []
    roots: list[Path] = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:/")
        try:
            if root.exists():
                roots.append(root)
        except OSError:
            continue
    return roots


def _resolve_existing_open_path(raw_path: str, is_dir: bool) -> Path | None:
    """Open-only: any existing local folder/file the user named, not just allowed dirs."""
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if is_dir:
        return resolved if resolved.is_dir() else None
    return resolved if resolved.is_file() else None


def _sounds_like(spoken: str, name: str) -> float:
    """Similarity that also counts sound-alike spellings ("fee wipe report" ~ "FYP Report"); 0 below 0.8 so it only adds matches."""
    from ..nlu.entity_match import score
    value = score(Path(spoken).name, Path(name).stem if "." in name else name)
    return value if value >= 0.8 else 0.0


def find_path_candidates(query: str, roots: list[Path], is_dir_only: bool = True) -> dict:
    """Bounded search; return all discovered matches rather than silently choosing one."""
    key = "".join(c for c in Path(query).name.casefold() if c.isalnum())
    if not key:
        return {"candidates": [], "truncated": False}
    matches = {}
    alias_matches = set()
    from ..ai.command_language import search_name_key
    alias_key = search_name_key(Path(query).name)
    visited = set()
    count = 0
    truncated = False
    skip = {".git", "node_modules", ".venv", "appdata", ".agentdesk_trash", "$recycle.bin"}
    for root in roots:
        root = root.expanduser().resolve()
        if is_dir_only and root.is_dir():
            root_key = "".join(c for c in root.name.casefold() if c.isalnum())
            if key == root_key or (alias_key and alias_key == search_name_key(root.name)):
                matches[str(root)] = 1.0 if key == root_key else 0.95
                if key != root_key:
                    alias_matches.add(str(root))
        for current, dirs, files in os.walk(root, followlinks=False):
            path = Path(current)
            if path in visited:
                dirs.clear()
                continue
            visited.add(path)
            count += 1
            dirs[:] = [d for d in dirs if d.casefold() not in skip and not d.startswith(".")
                       and not (path / d).is_symlink() and not getattr(path / d, "is_junction", lambda: False)()]
            for name in (dirs if is_dir_only else files):
                normalized = "".join(c for c in name.casefold() if c.isalnum())
                score = 1.0 if key == normalized else max(SequenceMatcher(None, key, normalized).ratio(), _sounds_like(query, name))
                if key in normalized:
                    score = max(score, 0.85)
                if alias_key and alias_key == search_name_key(name) and key != normalized:
                    score = max(score, 0.95)
                    alias_matches.add(str(path / name))
                if score >= 0.75:
                    matches[str(path / name)] = score
            if len(path.relative_to(root).parts) >= 6:
                dirs.clear()
            if count >= 5000:
                truncated = True
                break
        if truncated:
            break
    ordered = sorted(matches, key=lambda p: (-matches[p], p.casefold()))
    return {"candidates": ordered[:30], "truncated": truncated or len(ordered) > 30,
            "alias_matches": [path for path in ordered[:30] if path in alias_matches]}


class AmbiguousPathError(ValueError):
    def __init__(self, result):
        self.result = result
        super().__init__("Choose the exact path from the candidates, or provide another location.")


def _fuzzy_find_path(query_name: str, allowed: list[Path], is_dir_only: bool = True) -> Path | None:
    roots = allowed or [Path.home()]
    result = find_path_candidates(query_name, roots, is_dir_only)
    candidates = result["candidates"]
    if len(candidates) > 1 or result["truncated"]:
        raise AmbiguousPathError(result)
    return Path(candidates[0]) if candidates else None


class FindPathsArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="folder", pattern="^(folder|file)$")


class FindPathsTool(BaseTool):
    name = "find_paths"
    description = "Find files/folders by name within allowed directories. Returns candidate absolute paths and truncation status. Ask the user to choose when multiple matches exist; never guess a destructive target."
    args_schema = FindPathsArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = FindPathsArgs.model_validate(args.model_dump())
        roots = self.ctx.discovery_directories or self.ctx.allowed_directories
        return ToolResult(success=True, output=find_path_candidates(a.name, roots, a.kind == "folder"))


# ---------------------------------------------------------------- open_folder

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        a = FindPathsArgs.model_validate(args.model_dump())
        candidates = [Path(c) for c in result.output.get("candidates", [])]
        kind_ok = [(c.is_dir() if a.kind == "folder" else c.is_file()) for c in candidates]
        return verdict([("every candidate exists with the right kind", all(kind_ok))],
                       {"candidates": len(candidates), "existing": sum(kind_ok),
                        "truncated": bool(result.output.get("truncated"))})

class OpenFolderArgs(BaseModel):
    path: str = Field(description="Name or path of the folder to open. If relative or partial name, the system locates it automatically.")


class OpenFolderTool(BaseTool):
    name = "open_folder"
    description = "Open a folder in the system's file explorer. Automatically finds matching folder if exact path is not known."
    risk_level = RiskLevel.LOW
    interruptible = True  # read-only
    args_schema = OpenFolderArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = OpenFolderArgs.model_validate(args.model_dump())
        target = None
        try:
            target = resolve_within_allowed(a.path, self.ctx.allowed_directories)
        except ValueError:
            target = _resolve_existing_open_path(a.path, is_dir=True)
        if not target or not target.is_dir():
            try:
                fuzzy_target = _fuzzy_find_path(a.path, self.ctx.allowed_directories, is_dir_only=True)
            except AmbiguousPathError as exc:
                return ToolResult(success=False, output={**exc.result, "needs_confirmation": True}, error=str(exc))
            if fuzzy_target and fuzzy_target.is_dir():
                target = fuzzy_target
            else:
                return ToolResult(
                    success=False,
                    error=f"Folder does not exist and no matching folder found: {a.path}",
                )
        try:
            _platform.open_path_with_os(str(target))
        except Exception as exc:  # noqa: BLE001 - report honestly
            return ToolResult(success=False, error=f"Could not open folder: {exc}")
        return ToolResult(success=True, output={"path": str(target), "launched": True})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        target = Path(result.output["path"])
        seen, windows = window_check(title_contains=[target.name or str(target)], processes={"explorer.exe"})
        return verdict([("folder exists", target.is_dir()), ("Explorer window shows the folder", seen)],
                       {**file_evidence(target), **windows})

# --------------------------------------------------------------- create_folder
class CreateFolderArgs(BaseModel):
    path: str = Field(
        description=(
            "Absolute destination path (for example D:\\Projects\\new-folder or "
            "~/Desktop/new-folder). Do not guess the Windows user name."
        )
    )


def _resolve_create_folder_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"Only absolute paths are accepted (got {raw_path!r}).")
    try:
        return candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot resolve path {raw_path!r}: {exc}") from exc


class CreateFolderTool(BaseTool):
    name = "create_folder"
    description = (
        "Create a new folder (including parents) at any absolute path the operating "
        "system permits. Requires user confirmation and fails if a file already exists."
    )
    risk_level = RiskLevel.MEDIUM
    args_schema = CreateFolderArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = CreateFolderArgs.model_validate(args.model_dump())
        try:
            target = _resolve_create_folder_path(a.path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if target.exists():
            if target.is_dir():
                return ToolResult(success=True, output={"path": str(target), "already_existed": True})
            return ToolResult(success=False, error=f"A file already exists at {target}")
        try:
            target.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not create folder: {exc}")
        return ToolResult(success=True, output={"path": str(target), "already_existed": False})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        a = CreateFolderArgs.model_validate(args.model_dump())
        try:
            target = _resolve_create_folder_path(a.path)
        except ValueError as exc:
            return Verification(passed=False, checks=[f"path invalid: {exc}"])
        return verdict([("folder exists", target.is_dir())], file_evidence(target))

# ----------------------------------------------------------------- list_folder
class ListFolderArgs(BaseModel):
    path: str = Field(description="Absolute path of the folder to list")


class ListFolderTool(BaseTool):
    name = "list_folder"
    description = "List the contents of a folder inside an allowed directory."
    risk_level = RiskLevel.LOW
    interruptible = True  # read-only
    args_schema = ListFolderArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = ListFolderArgs.model_validate(args.model_dump())
        try:
            target = resolve_within_allowed(a.path, self.ctx.allowed_directories)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not target.is_dir():
            return ToolResult(success=False, error=f"Folder does not exist: {target}")
        try:
            entries = [
                {"name": p.name, "type": "dir" if p.is_dir() else "file"}
                for p in sorted(target.iterdir(), key=lambda p: p.name.lower())
            ]
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not list folder: {exc}")
        return ToolResult(success=True, output={"path": str(target), "entries": entries})


# ------------------------------------------------------------------- move_file

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        target = Path(result.output["path"])
        listed = {e["name"] for e in result.output.get("entries", [])}
        try:
            on_disk = {p.name for p in target.iterdir()}
        except OSError as exc:
            return Verification(passed=False, checks=[f"folder re-read failed: {exc}"])
        return verdict([("every listed entry exists", listed <= on_disk), ("no entry missing", on_disk <= listed)],
                       {"path": str(target), "listed": len(listed), "on_disk": len(on_disk),
                        "missing_from_list": sorted(on_disk - listed)[:5], "not_on_disk": sorted(listed - on_disk)[:5]})

class CopyFileArgs(BaseModel):
    source: str = Field(description="Absolute path of the file/folder to copy")
    destination: str = Field(description="Absolute destination path or an existing folder")


class CopyFileTool(BaseTool):
    name = "copy_file"
    description = "Copy a file or folder inside allowed directories. Refuses overwrites and symbolic links."
    risk_level = RiskLevel.MEDIUM
    args_schema = CopyFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = CopyFileArgs.model_validate(args.model_dump())
        try:
            src = resolve_within_allowed(a.source, self.ctx.allowed_directories)
            dst = resolve_within_allowed(a.destination, self.ctx.allowed_directories)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not src.exists():
            return ToolResult(success=False, error=f"Source does not exist: {src}")
        if src.is_symlink():
            return ToolResult(success=False, error="Refusing to copy a symbolic link")
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            return ToolResult(success=False, error=f"Destination already exists: {dst} (not overwritten)")
        if src.is_dir() and (dst == src or src in dst.parents):
            return ToolResult(success=False, error="Cannot copy a folder into itself or one of its subfolders")
        if src.is_dir():
            for folder, directories, files in os.walk(src, followlinks=False):
                for name in directories + files:
                    if (Path(folder) / name).is_symlink():
                        return ToolResult(success=False, error="Refusing to copy folders that contain symbolic links")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not copy: {exc}")
        return ToolResult(success=True, output={"source": str(src), "destination": str(dst)})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        src = Path(result.output["source"])
        dst = Path(result.output["destination"])
        if src.is_file():
            se, de = file_evidence(src), file_evidence(dst)
            same = de.get("exists") and se.get("size") == de.get("size") and se.get("sha256") == de.get("sha256")
            return verdict([("source still exists", se["exists"]), ("copy has identical size and hash", bool(same))],
                           {"source": se, "destination": de})
        source_files = {Path(f, n).relative_to(src) for f, _, names in os.walk(src) for n in names} if src.is_dir() else set()
        destination_files = {Path(f, n).relative_to(dst) for f, _, names in os.walk(dst) for n in names} if dst.is_dir() else set()
        return verdict([("source folder still exists", src.is_dir()), ("destination has every file", source_files <= destination_files)],
                       {"source": str(src), "destination": str(dst), "source_files": len(source_files),
                        "destination_files": len(destination_files)})

# ------------------------------------------------------------------- move_file
class MoveFileArgs(BaseModel):
    source: str = Field(description="Absolute path of the file/folder to move")
    destination: str = Field(description="Absolute destination path or an existing folder")


class MoveFileTool(BaseTool):
    name = "move_file"
    description = "Move a file or folder. Refuses to overwrite an existing destination."
    risk_level = RiskLevel.MEDIUM
    args_schema = MoveFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = MoveFileArgs.model_validate(args.model_dump())
        try:
            src = resolve_within_allowed(a.source, self.ctx.allowed_directories)
            dst = resolve_within_allowed(a.destination, self.ctx.allowed_directories)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not src.exists():
            return ToolResult(success=False, error=f"Source does not exist: {src}")
        if is_allowed_root(src, self.ctx.allowed_directories):
            return ToolResult(success=False, error=f"Refused: {src} is an allowed root folder and cannot be moved")
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            return ToolResult(success=False, error=f"Destination already exists: {dst} (not overwritten)")
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not move: {exc}")
        return ToolResult(success=True, output={"source": str(src), "destination": str(dst)})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        dst = Path(result.output["destination"])
        src = Path(result.output["source"])
        return verdict([("destination exists", dst.exists()), ("source removed", not src.exists())],
                       {"destination": file_evidence(dst), "source_exists": src.exists()})

# ----------------------------------------------------------------- rename_file
class RenameFileArgs(BaseModel):
    path: str = Field(description="Absolute path of the file/folder to rename")
    new_name: str = Field(description="New file/folder name (no path separators)")


class RenameFileTool(BaseTool):
    name = "rename_file"
    description = "Rename a file or folder within its current directory."
    risk_level = RiskLevel.MEDIUM
    args_schema = RenameFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = RenameFileArgs.model_validate(args.model_dump())
        if Path(a.new_name).name != a.new_name or "/" in a.new_name or "\\" in a.new_name or ":" in a.new_name:
            return ToolResult(success=False, error="new_name must be a plain file name, not a path")
        try:
            src = resolve_within_allowed(a.path, self.ctx.allowed_directories)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not src.exists():
            return ToolResult(success=False, error=f"Path does not exist: {src}")
        if is_allowed_root(src, self.ctx.allowed_directories):
            return ToolResult(success=False, error=f"Refused: {src} is an allowed root folder and cannot be renamed")
        dst = src.parent / a.new_name
        if dst.exists():
            return ToolResult(success=False, error=f"Destination already exists: {dst} (not overwritten)")
        try:
            src.rename(dst)
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not rename: {exc}")
        return ToolResult(success=True, output={"old_path": str(src), "new_path": str(dst)})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        new = Path(result.output["new_path"])
        old = Path(result.output["old_path"])
        return verdict([("new path exists", new.exists()), ("old path removed", not old.exists())],
                       {"new": file_evidence(new), "old_exists": old.exists()})

# -------------------------------------------------------------- compress_folder
class CompressFolderArgs(BaseModel):
    folder_path: str = Field(description="Absolute path of the folder to compress")
    zip_path: str | None = Field(default=None, description="Absolute path for the .zip (default: folder_path.zip)")


class CompressFolderTool(BaseTool):
    name = "compress_folder"
    description = "Compress a folder into a ZIP archive."
    risk_level = RiskLevel.MEDIUM
    args_schema = CompressFolderArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = CompressFolderArgs.model_validate(args.model_dump())
        try:
            folder = resolve_within_allowed(a.folder_path, self.ctx.allowed_directories)
            zip_target = resolve_within_allowed(
                a.zip_path or f"{a.folder_path}.zip", self.ctx.allowed_directories
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not folder.is_dir():
            return ToolResult(success=False, error=f"Folder does not exist: {folder}")
        if zip_target.suffix.lower() != ".zip":
            zip_target = zip_target.with_suffix(".zip")
        # Never write the archive inside the folder being compressed.
        if folder == zip_target or folder in zip_target.parents:
            return ToolResult(success=False, error="The ZIP archive cannot be created inside the folder being compressed")
        if zip_target.exists():
            return ToolResult(success=False, error=f"Archive already exists: {zip_target} (not overwritten)")
        try:
            base = str(zip_target.with_suffix(""))
            shutil.make_archive(base, "zip", root_dir=str(folder))
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not compress: {exc}")
        return ToolResult(success=True, output={"zip_path": str(zip_target)})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        zp = Path(result.output["zip_path"])
        valid = zp.is_file() and zipfile.is_zipfile(zp)
        members = len(zipfile.ZipFile(zp).namelist()) if valid else 0
        return verdict([("zip exists and is valid", valid), ("zip has members", members > 0)],
                       {**file_evidence(zp), "members": members})

# ---------------------------------------------------------------- extract_file
MAX_EXTRACT_ENTRIES = 5000
MAX_EXTRACT_BYTES = 1024 ** 3  # 1 GiB declared uncompressed size


class ExtractFileArgs(BaseModel):
    zip_path: str = Field(description="Absolute path of the .zip archive to extract")
    destination: str | None = Field(default=None, description="Absolute destination folder (default: alongside archive)")


class ExtractFileTool(BaseTool):
    name = "extract_file"
    description = "Extract a ZIP archive safely (protected against zip-slip attacks)."
    risk_level = RiskLevel.MEDIUM
    args_schema = ExtractFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = ExtractFileArgs.model_validate(args.model_dump())
        try:
            zp = resolve_within_allowed(a.zip_path, self.ctx.allowed_directories)
            dest = resolve_within_allowed(
                a.destination or str(Path(a.zip_path).with_suffix("")) + "_extracted",
                self.ctx.allowed_directories,
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not (zp.is_file() and zipfile.is_zipfile(zp)):
            return ToolResult(success=False, error=f"Not a valid ZIP archive: {zp}")
        try:
            dest.mkdir(parents=True, exist_ok=True)
            dest_resolved = dest.resolve()
            extracted: list[str] = []
            with zipfile.ZipFile(zp) as zf:
                members = zf.infolist()
                if len(members) > MAX_EXTRACT_ENTRIES or sum(m.file_size for m in members) > MAX_EXTRACT_BYTES:
                    return ToolResult(success=False, error="Archive is too large to extract safely (entry or size limit exceeded)")
                for member in members:
                    member_path = (dest_resolved / member.filename).resolve()
                    if dest_resolved != member_path and dest_resolved not in member_path.parents:
                        return ToolResult(success=False, error=f"Blocked unsafe archive entry: {member.filename}")
                    if not member.is_dir() and member_path.exists():
                        return ToolResult(success=False, error=f"Refused: {member_path} already exists (nothing was overwritten)")
                zf.extractall(dest_resolved)
                extracted = zf.namelist()
        except (OSError, zipfile.BadZipFile) as exc:
            return ToolResult(success=False, error=f"Could not extract: {exc}")
        return ToolResult(success=True, output={"destination": str(dest_resolved), "files": extracted})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        dest = Path(result.output["destination"])
        files = [p for p in dest.rglob("*") if p.is_file()] if dest.is_dir() else []
        return verdict([("destination has extracted files", bool(files))],
                       {"destination": str(dest), "files": len(files),
                        "sample": [file_evidence(p, with_hash=False) for p in files[:3]]})

# ----------------------------------------------------------------- delete_file
class DeleteFileArgs(BaseModel):
    path: str = Field(description="Absolute path of the file (or empty folder) to delete")
    include_contents: bool = Field(default=False, description="Explicitly include a non-empty folder and all contents in the approval. Moves everything to restorable internal Trash.")


class DeleteFileTool(BaseTool):
    name = "delete_file"
    description = (
        "Move a file or folder into restorable internal Trash. Non-empty folders need include_contents=true, shown in approval. "
        "HIGH RISK: requires explicit user approval."
    )
    risk_level = RiskLevel.HIGH
    args_schema = DeleteFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = DeleteFileArgs.model_validate(args.model_dump())
        try:
            target = _resolve_delete_path(a.path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if target == Path(target.anchor):
            return ToolResult(success=False, error="Refusing to delete a filesystem root")
        if is_allowed_root(target, self.ctx.allowed_directories):
            return ToolResult(success=False, error="Refusing to delete an allowed root directory")
        if _is_in_internal_trash(target, self.ctx.allowed_directories):
            return ToolResult(success=False, error="Refusing to delete items already inside AgentDesk Trash")
        if not target.exists():
            return ToolResult(success=False, error=f"Path does not exist: {target}")
        if target.is_dir() and any(target.iterdir()) and not a.include_contents:
            return ToolResult(success=False, error=f"Refusing to delete non-empty folder: {target}")
        if a.include_contents:
            try:
                resolve_within_allowed(str(target), self.ctx.allowed_directories)
            except ValueError as exc:
                return ToolResult(success=False, error=str(exc))
            protected = [Path.home().resolve(), *[Path(os.environ[v]).resolve() for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]]
            if any(target == root or target in root.parents or root in target.parents for root in protected[1:]) or target == protected[0]:
                return ToolResult(success=False, error="Refusing to remove a protected system or home directory")
        trash_root = _trash_root_for(target, self.ctx.allowed_directories)
        trash_id = uuid.uuid4().hex
        trashed_path = trash_root / trash_id
        manifest_path = _trash_manifest_path(target, trash_id, self.ctx.allowed_directories)
        try:
            if trash_root.is_symlink():
                return ToolResult(success=False, error=f"AgentDesk Trash must not be a symbolic link: {trash_root}")
            if manifest_path.parent.is_symlink():
                return ToolResult(
                    success=False,
                    error=f"AgentDesk Trash must not be a symbolic link: {manifest_path.parent}",
                )
            trash_root.mkdir(parents=True, exist_ok=True)
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(trashed_path))
            manifest_path.write_text(
                json.dumps({"original_path": str(target), "trashed_path": str(trashed_path)}),
                encoding="utf-8",
            )
        except OSError as exc:
            if trashed_path.exists() and not target.exists():
                try:
                    shutil.move(str(trashed_path), str(target))
                except OSError as restore_exc:
                    return ToolResult(
                        success=False,
                        error=f"Could not save item in Trash ({exc}); rollback failed: {restore_exc}",
                    )
            manifest_path.unlink(missing_ok=True)
            return ToolResult(success=False, error=f"Could not move item to Trash: {exc}")
        return ToolResult(
            success=True,
            output={"deleted": str(target), "trash_id": trash_id, "restorable": True},
        )

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        original = Path(result.output["deleted"])
        trash_root = _trash_root_for(original, self.ctx.allowed_directories)
        trash_id = result.output["trash_id"]
        manifest_path = _trash_manifest_path(original, trash_id, self.ctx.allowed_directories)
        retained = (trash_root / trash_id).exists() and manifest_path.is_file()
        return verdict([("original path removed", not original.exists()), ("item retained in AgentDesk Trash", retained)],
                       {"original_exists": original.exists(), "trashed": file_evidence(trash_root / trash_id, with_hash=False),
                        "manifest": str(manifest_path)})

class ListTrashArgs(BaseModel):
    pass


class ListTrashTool(BaseTool):
    name = "list_trash"
    description = "List files and empty folders in AgentDesk's internal Trash that can be restored."
    risk_level = RiskLevel.LOW
    interruptible = True  # read-only
    args_schema = ListTrashArgs

    def execute(self, args: BaseModel) -> ToolResult:
        del args
        items: list[dict[str, str]] = []
        seen: set[Path] = set()
        for root in _trash_manifest_roots(self.ctx.allowed_directories):
            if root in seen:
                continue
            seen.add(root)
            if not root.is_dir():
                continue
            try:
                if root.resolve() != root:
                    return ToolResult(success=False, error=f"AgentDesk Trash must not be a symbolic link: {root}")
                manifests = sorted(root.glob("*.json"))
                for manifest in manifests:
                    try:
                        if manifest.resolve().parent != root:
                            return ToolResult(
                                success=False,
                                error=f"Trash record must not be a symbolic link: {manifest.name}",
                            )
                        item = json.loads(manifest.read_text(encoding="utf-8"))
                        original_path = _resolve_delete_path(str(item["original_path"]))
                        trashed = _resolve_delete_path(str(item["trashed_path"]))
                        expected_manifest = _trash_manifest_path(
                            original_path, manifest.stem, self.ctx.allowed_directories
                        )
                        expected_trash_root = _trash_root_for(original_path, self.ctx.allowed_directories)
                        if manifest != expected_manifest:
                            return ToolResult(
                                success=False,
                                error=f"Trash record is stored in an unexpected location: {manifest.name}",
                            )
                        if trashed.parent != expected_trash_root or trashed.name != manifest.stem:
                            return ToolResult(
                                success=False,
                                error=f"Trash record points outside its internal Trash: {manifest.name}",
                            )
                        if trashed.exists():
                            items.append(
                                {
                                    "trash_id": manifest.stem,
                                    "original_path": str(original_path),
                                    "item_type": "folder" if trashed.is_dir() else "file",
                                }
                            )
                    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        return ToolResult(
                            success=False,
                            error=f"Could not read AgentDesk Trash record {manifest.name}: {exc}",
                        )
            except OSError as exc:
                return ToolResult(success=False, error=f"Could not list AgentDesk Trash: {exc}")
        return ToolResult(success=True, output={"items": items})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        items = result.output.get("items", [])
        present = []
        for item in items:
            original = Path(item["original_path"])
            trashed = _trash_root_for(original, self.ctx.allowed_directories) / item["trash_id"]
            present.append(trashed.exists())
        return verdict([("every listed item is in Trash", all(present))],
                       {"items": len(items), "present": sum(present)})



class RestoreFileArgs(BaseModel):
    trash_id: str = Field(description="trash_id returned by list_trash")


class RestoreFileTool(BaseTool):
    name = "restore_file"
    description = "Restore an item from AgentDesk Trash using its trash_id. Refuses to overwrite existing paths."
    risk_level = RiskLevel.MEDIUM
    args_schema = RestoreFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = RestoreFileArgs.model_validate(args.model_dump())
        if len(a.trash_id) != 32 or any(c not in "0123456789abcdef" for c in a.trash_id.lower()):
            return ToolResult(success=False, error="Invalid trash_id; use an id returned by list_trash")
        for trash_root in _trash_manifest_roots(self.ctx.allowed_directories):
            manifest_path = trash_root / f"{a.trash_id}.json"
            if not manifest_path.is_file():
                continue
            try:
                item = json.loads(manifest_path.read_text(encoding="utf-8"))
                original = _resolve_delete_path(item["original_path"])
                trashed = _resolve_delete_path(item["trashed_path"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                return ToolResult(success=False, error=f"Trash record is invalid or inaccessible: {exc}")
            expected_manifest = _trash_manifest_path(original, a.trash_id, self.ctx.allowed_directories)
            expected_trash_root = _trash_root_for(original, self.ctx.allowed_directories)
            if manifest_path != expected_manifest:
                return ToolResult(success=False, error="Trash record is stored in an unexpected location")
            if trashed.parent != expected_trash_root or trashed.name != a.trash_id:
                return ToolResult(success=False, error="Trash record points outside its internal Trash")
            if trashed.parent.is_symlink():
                return ToolResult(success=False, error="AgentDesk Trash must not be a symbolic link")
            if not trashed.exists():
                return ToolResult(success=False, error="The item is missing from AgentDesk Trash")
            if _is_in_internal_trash(original, self.ctx.allowed_directories):
                return ToolResult(success=False, error="Refusing to restore an item into AgentDesk Trash")
            if original.exists():
                return ToolResult(success=False, error=f"Restore would overwrite an existing path: {original}")
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(trashed), str(original))
                manifest_path.unlink()
            except OSError as exc:
                return ToolResult(success=False, error=f"Could not restore item: {exc}")
            return ToolResult(success=True, output={"restored": str(original)})
        return ToolResult(success=False, error=f"No Trash item found with id {a.trash_id}")

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        restored = Path(result.output["restored"])
        return verdict([("restored path exists", restored.exists())], file_evidence(restored))

class WriteTextFileArgs(BaseModel):
    path: str = Field(description="Absolute path of a text document inside an allowed directory")
    content: str = Field(max_length=100_000, description="Text to write (maximum 100,000 characters)")
    overwrite: bool = Field(default=False, description="Set true only when intentionally replacing existing content")


class WriteTextFileTool(BaseTool):
    name = "write_text_file"
    description = "Create or replace a UTF-8 text document. Replacing existing content requires explicit approval."
    risk_level = RiskLevel.MEDIUM
    args_schema = WriteTextFileArgs

    def risk_for_args(self, args: BaseModel | None) -> RiskLevel:
        if args is not None and WriteTextFileArgs.model_validate(args.model_dump()).overwrite:
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM

    def execute(self, args: BaseModel) -> ToolResult:
        a = WriteTextFileArgs.model_validate(args.model_dump())
        try:
            target = resolve_within_allowed(a.path, self.ctx.allowed_directories)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if _is_in_internal_trash(target, self.ctx.allowed_directories):
            return ToolResult(success=False, error="Cannot write inside AgentDesk Trash")
        if is_executable_path(target):
            return ToolResult(success=False, error=f"Refused: cannot author executable or script files ({target.suffix}).")
        if target.exists() and not a.overwrite:
            return ToolResult(success=False, error=f"File already exists; set overwrite=true only if replacement is intended: {target}")
        if target.exists() and not target.is_file():
            return ToolResult(success=False, error=f"Path is not a file: {target}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(a.content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not write document: {exc}")
        return ToolResult(success=True, output={"path": str(target), "characters_written": len(a.content)})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        target = Path(result.output["path"])
        expected = WriteTextFileArgs.model_validate(args.model_dump()).content
        evidence = file_evidence(target)
        matches = target.is_file() and target.read_text(encoding="utf-8") == expected
        return verdict([("document content matches requested text", matches)],
                       {**evidence, "expected_sha256": text_sha256(expected)})

class ReadTextFileArgs(BaseModel):
    path: str = Field(description="Absolute path of a UTF-8 text document inside an allowed directory")


class ReadTextFileTool(BaseTool):
    name = "read_text_file"
    description = "Read a UTF-8 text document inside an allowed directory (maximum 50,000 characters)."
    risk_level = RiskLevel.LOW
    interruptible = True  # read-only
    args_schema = ReadTextFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = ReadTextFileArgs.model_validate(args.model_dump())
        try:
            target = resolve_within_allowed(a.path, self.ctx.allowed_directories)
            if _is_in_internal_trash(target, self.ctx.allowed_directories):
                return ToolResult(success=False, error="Cannot read items directly from AgentDesk Trash")
            if not target.is_file():
                return ToolResult(success=False, error=f"File does not exist: {target}")
            with target.open("r", encoding="utf-8") as file:
                content = file.read(50_001)
        except (OSError, UnicodeError, ValueError) as exc:
            return ToolResult(success=False, error=f"Could not read text document: {exc}")
        if len(content) > 50_000:
            return ToolResult(success=False, error="Document is larger than the 50,000-character read limit")
        return ToolResult(success=True, output={"path": str(target), "content": content})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        target = Path(result.output["path"])
        try:
            on_disk = target.read_text(encoding="utf-8")
        except OSError as exc:
            return Verification(passed=False, checks=[f"could not re-read document: {exc}"])
        returned = result.output.get("content", "")
        return verdict([("returned text equals file on disk", returned == on_disk)],
                       {**file_evidence(target), "returned_sha256": text_sha256(returned), "characters": len(returned)})



class FindFilesArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="File or folder name, including approximate spoken names")
    folder: str | None = Field(default=None, description="Optional absolute folder to search; defaults to allowed directories")


class FindFilesTool(BaseTool):
    name = "find_files"
    description = (
        "Find files or folders by a close name match inside allowed directories. "
        "Use this when the user named something approximately, then ask them to confirm if several matches exist."
    )
    risk_level = RiskLevel.LOW
    interruptible = True  # read-only
    args_schema = FindFilesArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = FindFilesArgs.model_validate(args.model_dump())
        query = re.sub(r"[^a-z0-9]+", "", a.name.casefold())
        if not query:
            return ToolResult(success=False, error="Provide a file or folder name to search")
        roots: list[Path] = []
        if a.folder:
            try:
                roots = [resolve_within_allowed(a.folder, self.ctx.allowed_directories)]
            except ValueError as exc:
                return ToolResult(success=False, error=str(exc))
            if not roots[0].is_dir():
                return ToolResult(success=False, error=f"Folder does not exist: {roots[0]}")
        else:
            roots = [path.expanduser().resolve() for path in self.ctx.allowed_directories]
        matches: list[tuple[float, dict[str, str]]] = []
        skip_dirs = {".git", "node_modules", ".venv", "AppData", ".agentdesk_trash"}
        scanned = 0
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for path in root.rglob("*"):
                    scanned += 1
                    if scanned > 4000:
                        break
                    try:
                        rel_parts = path.relative_to(root).parts[:-1]
                    except ValueError:
                        rel_parts = path.parts[:-1]
                    if any(part in skip_dirs for part in rel_parts):
                        continue
                    if _is_in_internal_trash(path, self.ctx.allowed_directories):
                        continue
                    label = re.sub(r"[^a-z0-9]+", "", path.name.casefold())
                    if not label:
                        continue
                    if query in label or label in query:
                        score = 0.95 if query == label else 0.8
                    else:
                        score = max(SequenceMatcher(None, query, label).ratio(), _sounds_like(a.name, path.name))
                    if score < 0.55:
                        continue
                    matches.append(
                        (
                            score,
                            {
                                "name": path.name,
                                "path": str(path),
                                "type": "dir" if path.is_dir() else "file",
                            },
                        )
                    )
                    if len(matches) >= 80:
                        break
            except OSError as exc:
                return ToolResult(success=False, error=f"Could not search {root}: {exc}")
        matches.sort(key=lambda item: item[0], reverse=True)
        unique: list[dict[str, str]] = []
        seen: set[str] = set()
        for _, item in matches:
            if item["path"] in seen:
                continue
            seen.add(item["path"])
            unique.append(item)
            if len(unique) >= 12:
                break
        return ToolResult(
            success=True,
            output={
                "query": a.name,
                "matches": unique,
                "needs_confirmation": len(unique) != 1,
                "message": (
                    "Ask the user which path to use, then continue the original task."
                    if len(unique) != 1
                    else "One close match; confirm if the name was approximate, then act."
                ),
            },
        )

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        matches = result.output.get("matches", [])
        exists = [Path(m["path"] if isinstance(m, dict) else m).exists() for m in matches]
        return verdict([("every match exists", all(exists))], {"matches": len(matches), "existing": sum(exists)})



class EditTextFileArgs(BaseModel):
    path: str = Field(description="Absolute path of a UTF-8 text document inside an allowed directory")
    old_text: str = Field(min_length=1, max_length=20_000, description="Exact text to replace")
    new_text: str = Field(max_length=20_000, description="Replacement text")


class EditTextFileTool(BaseTool):
    name = "edit_text_file"
    description = "Replace one exact snippet inside an existing UTF-8 text document. Does not send email or run code."
    risk_level = RiskLevel.HIGH
    args_schema = EditTextFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = EditTextFileArgs.model_validate(args.model_dump())
        try:
            target = resolve_within_allowed(a.path, self.ctx.allowed_directories)
            if _is_in_internal_trash(target, self.ctx.allowed_directories):
                return ToolResult(success=False, error="Cannot edit items inside AgentDesk Trash")
            if not target.is_file():
                return ToolResult(success=False, error=f"File does not exist: {target}")
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            return ToolResult(success=False, error=f"Could not read document: {exc}")
        count = content.count(a.old_text)
        if count != 1:
            return ToolResult(
                success=False,
                error=f"old_text must match exactly once (found {count}). Read the file and use a unique snippet.",
            )
        updated = content.replace(a.old_text, a.new_text, 1)
        try:
            target.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not edit document: {exc}")
        return ToolResult(
            success=True,
            output={"path": str(target), "replacements": 1, "characters": len(updated)},
        )

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        a = EditTextFileArgs.model_validate(args.model_dump())
        target = Path(result.output["path"])
        try:
            content = target.read_text(encoding="utf-8")
        except OSError:
            return Verification(passed=False, checks=["could not re-read document"])
        replaced = a.new_text in content and (not a.old_text or a.old_text not in content or a.old_text in a.new_text)
        return verdict([("snippet replaced", replaced)], file_evidence(target))

class OpenFileArgs(BaseModel):
    path: str = Field(description="Name or path of an existing file. If relative or partial name, the system locates it automatically.")


class OpenFileTool(BaseTool):
    name = "open_file"
    description = "Open an existing file with its default desktop application. Automatically finds matching file if exact path is not known."
    risk_level = RiskLevel.LOW
    args_schema = OpenFileArgs

    def execute(self, args: BaseModel) -> ToolResult:
        a = OpenFileArgs.model_validate(args.model_dump())
        target = None
        try:
            target = resolve_within_allowed(a.path, self.ctx.allowed_directories)
        except ValueError:
            target = _resolve_existing_open_path(a.path, is_dir=False)
        if not target or not target.is_file():
            try:
                fuzzy_target = _fuzzy_find_path(a.path, self.ctx.allowed_directories, is_dir_only=False)
            except AmbiguousPathError as exc:
                return ToolResult(success=False, output={**exc.result, "needs_confirmation": True}, error=str(exc))
            if fuzzy_target and fuzzy_target.is_file():
                target = fuzzy_target
            else:
                return ToolResult(
                    success=False,
                    error=f"File does not exist and no matching file found: {a.path}",
                )
        if is_executable_path(target):
            return ToolResult(success=False, error=f"Refused: {target.name} is an executable or script type; opening it would run code.")
        try:
            _platform.open_path_with_os(str(target))
        except (OSError, ValueError) as exc:
            return ToolResult(success=False, error=f"Could not open file: {exc}")
        return ToolResult(success=True, output={"path": str(target), "launched": True})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        target = Path(result.output["path"])
        seen, windows = window_check(title_contains=[target.name, target.stem])
        return verdict([("file exists", target.is_file()), ("a window shows the file", seen)],
                       {**file_evidence(target, with_hash=False), **windows})

def filesystem_tools(ctx: ToolContext | None = None) -> list[BaseTool]:
    ctx = ctx or ToolContext.from_settings()
    return [
        FindPathsTool(ctx),
        OpenFolderTool(ctx),
        CreateFolderTool(ctx),
        ListFolderTool(ctx),
        CopyFileTool(ctx),
        MoveFileTool(ctx),
        RenameFileTool(ctx),
        CompressFolderTool(ctx),
        ExtractFileTool(ctx),
        DeleteFileTool(ctx),
        ListTrashTool(ctx),
        RestoreFileTool(ctx),
        WriteTextFileTool(ctx),
        ReadTextFileTool(ctx),
        EditTextFileTool(ctx),
        FindFilesTool(ctx),
        OpenFileTool(ctx),
    ]
