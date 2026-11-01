"""Local intent -> discovery -> clarification -> approved tool plan, without cloud AI."""
from __future__ import annotations
import json
import re
from pathlib import Path
from ..ai.base import Plan, PlanAction
from ..ai.local_commands import local_plan, normalize_speech
from ..ai.command_language import command_grammar, known_target
from ..ai.browser_commands import browser_plan
from ..models import Task, TaskStatus


def _path(value):
    value = known_target(value.strip().strip('"'))
    places = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads"}
    return str(Path.home() / places[value.casefold()]) if value.casefold() in places else str(Path(value).expanduser())


def _action(tool, args):
    return Plan(actions=[PlanAction(tool=tool, args=args)], note="Local agent ne plan tayyar kiya; cloud API use nahi hui.")


def relative_candidates(value, registry):
    raw = Path(value.strip().strip('"').replace("\\", "/")).expanduser()
    if raw.is_absolute():
        return [raw.resolve()] if raw.exists() else []
    if ".." in raw.parts:
        return []
    matches = []
    for base in registry.ctx.discovery_directories or registry.ctx.allowed_directories:
        base = base.expanduser().resolve()
        candidates = [base / raw]
        if raw.parts and raw.parts[0].casefold() == base.name.casefold():
            candidates.append(base.parent / raw)
        for candidate in candidates:
            candidate = candidate.resolve()
            if candidate.exists() and (candidate == base or base in candidate.parents) and candidate not in matches:
                matches.append(candidate)
    return matches


def correction_plan(task, original, reply, registry):
    """Apply a path reply to a specific workflow, never wrap it as prose for AI."""
    paths = relative_candidates(reply, registry)
    state = json.loads(original.agent_state_json or "{}")
    if state.get("window_choices"):
        from ..ai.window_commands import close_action
        choices = state["window_choices"]
        if reply.strip().isdecimal() and 1 <= int(reply.strip()) <= len(choices):
            return close_action(choices[int(reply.strip()) - 1])
        if reply.strip() in choices:
            return close_action(reply.strip())
        task.agent_state_json = original.agent_state_json
        return Plan(note="Window ka number ya listed exact title batayein.\n" + "\n".join(f"{i+1}. {title}" for i, title in enumerate(choices)))
    workflow = state.get("workflow")
    if workflow:
        workflow = dict(workflow)
        candidates = workflow.get("candidates", [])
        choice = re.fullmatch(r"(?:(preview|open)\s+)?(\d+)", reply.strip(), re.IGNORECASE)
        if choice and 1 <= int(choice.group(2)) <= len(candidates):
            workflow["source"] = candidates[int(choice.group(2)) - 1]
            workflow.pop("candidates", None)
            if choice.group(1):
                task.agent_state_json = json.dumps({"provider": "local", "workflow": workflow, "preview": True})
                plan = _action("open_folder" if workflow["kind"] == "folder" else "open_file", {"path": workflow["source"]})
                plan.note = "Kya isi par original operation karna hai? Haan ya nahin batayein."
                return plan
            return _resolve(task, workflow, registry)
        normalized = normalize_speech(reply)
        if normalized in {"yes", "haan", "han", "haan yahi", "haan kar do", "ہاں", "हाँ"}:
            if state.get("preview"):
                return _resolve(task, workflow, registry)
            return _ask(task, workflow, "Pehle candidate ka number ya exact path select karein.")
        if len(paths) == 1:
            workflow["destination" if workflow.pop("awaiting_destination", False) else "source"] = str(paths[0])
            workflow.pop("candidates", None)
            return _resolve(task, workflow, registry)
        if len(paths) > 1:
            workflow["candidates"] = [str(path) for path in paths]
        return _ask(task, workflow, "Sahi path ka number ya exact address batayein.\n" + "\n".join(f"{i+1}. {path}" for i, path in enumerate(workflow.get("candidates", []))))
    if len(paths) == 1:
        path = paths[0]
        if len(original.actions) == 1:
            action = original.actions[0]
            args = json.loads(action.args_json)
            if action.tool_name == "open_application":
                return _action("open_folder" if path.is_dir() else "open_file", {"path": str(path)})
            if "path" in args:
                args["path"] = str(path)
                return _action(action.tool_name, args)
            if action.tool_name in {"copy_file", "move_file"} and "source" in args:
                args["source"] = str(path)
                return _action(action.tool_name, args)
        command = original.command
        if command.startswith("Original request:") and "User correction/clarification:" in command:
            command = command.split("User correction/clarification:", 1)[0].removeprefix("Original request:").strip()
        if command_grammar(command).casefold().startswith("open "):
            return _action("open_folder" if path.is_dir() else "open_file", {"path": str(path)})
    return None


def _ask(task, intent, note):
    task.agent_state_json = json.dumps({"provider": "local", "workflow": intent}, ensure_ascii=False)
    return Plan(note=note)


def _parse(command):
    raw = command.strip().rstrip("۔")
    patterns = [
        (r"(?:open|list|show)(?: the| my)? (folder|file) (.+)", "open"),
        (r"(?:delete|remove)(?: the)? (folder|file) (.+)", "delete"),
        (r"(copy|move)(?: the)? (folder|file) (.+?) to (.+)", "transfer"),
        (r"rename(?: the)? (folder|file) (.+?) to (.+)", "rename"),
        (r"(?:folder|file) (.+?) (?:delete|remove) (?:kar do|karo)", "roman_delete"),
        (r"(.+?) (folder|file) (?:kholo|khol do|खोल दो|کھول دو)", "roman_open"),
        (r"(?:create|make)(?: a| new)? folder(?: called| named)? (.+?) (?:in|on) (.+)", "create"),
    ]
    for pattern, mode in patterns:
        m = re.fullmatch(pattern, raw, re.IGNORECASE)
        if not m:
            continue
        groups = m.groups()
        if mode == "transfer":
            op, kind, source, destination = groups
            return {"op": op.lower(), "kind": kind.lower(), "source": source, "destination": destination}
        if mode == "rename":
            kind, source, new_name = groups
            return {"op": "rename", "kind": kind.lower(), "source": source, "new_name": new_name}
        if mode == "create":
            name, destination = groups
            return {"op": "create", "name": name, "destination": destination}
        if mode == "roman_delete":
            return {"op": "delete", "kind": "folder", "source": groups[0]}
        if mode == "roman_open":
            return {"op": "open", "kind": groups[1].lower(), "source": groups[0]}
        kind, source = groups
        include = bool(re.search(r" including (?:all )?(?:its )?contents$", source, re.IGNORECASE))
        source = re.sub(r" including (?:all )?(?:its )?contents$", "", source, flags=re.IGNORECASE)
        return {"op": "list" if raw.lower().startswith(("list", "show")) else mode,
                "kind": kind.lower(), "source": source, "include_contents": include}
    m = re.fullmatch(r"(?:create|make)(?: a| new)? folder(?: called| named)? (.+)", raw, re.IGNORECASE)
    if m:
        return {"op": "create", "name": m.group(1), "destination": "", "awaiting_destination": True}
    return None


def _resolve(task, intent, registry):
    op = intent["op"]
    if op == "create":
        if not intent.get("destination"):
            return _ask(task, intent, "Naya folder kahan banana hai? Desktop, Documents, Downloads ya parent ka exact path batayein.")
        dest = Path(_path(intent["destination"]))
        name = intent["name"].strip().strip('"')
        if not dest.is_absolute() or name in {".", ".."} or any(c in name for c in '/\\:'):
            return _ask(task, intent, "Folder ka simple naam aur parent ka exact path batayein; misal: create folder Lab in Documents.")
        return _action("create_folder", {"path": str(dest / name)})
    source = _path(intent["source"])
    if not Path(source).is_absolute():
        tool = registry.get("find_paths")
        result = tool.execute(tool.validate_args({"name": source, "kind": intent["kind"]}))
        candidates = result.output.get("candidates", [])
        if not result.success or not candidates:
            return _ask(task, intent, "Yeh naam nahi mila. File/folder ka exact path batayein.")
        if len(candidates) > 1 or result.output.get("truncated") or result.output.get("alias_matches"):
            intent["candidates"] = candidates
            note = "Kaunsa path? Number ya exact address batayein. Preview ke liye 'preview 1' keh sakti hain.\n"
            return _ask(task, intent, note + "\n".join(f"{i+1}. {p}" for i, p in enumerate(candidates)))
        source = candidates[0]
    if op in {"copy", "move"}:
        destination = _path(intent["destination"])
        if not Path(destination).is_absolute():
            intent["source"] = source
            intent["awaiting_destination"] = True
            return _ask(task, intent, "Destination ka exact path ya Desktop/Documents/Downloads batayein.")
        return _action(op + "_file", {"source": source, "destination": destination})
    if op == "rename":
        return _action("rename_file", {"path": source, "new_name": intent["new_name"].strip().strip('"')})
    if op == "delete":
        return _action("delete_file", {"path": source, "include_contents": intent.get("include_contents", False)})
    return _action("list_folder" if op == "list" else "open_folder" if intent["kind"] == "folder" else "open_file", {"path": source})


def _apply_correction(db, spoken, recent):
    """"No, I said Firefox": remember that the name heard last time meant Firefox, and open Firefox now."""
    from ..nlu.correction_memory import learn, parse_correction
    meant = parse_correction(spoken)
    if not meant or recent is None:
        return spoken
    for action in reversed(list(recent.actions)):
        if action.tool_name == "open_application":
            heard = json.loads(action.args_json).get("app_name", "")
            learn(db, heard, meant)
            return f"open {meant}"
    return spoken


def prepare_local_request(db, task, registry):
    """Return a validated local plan, or None when cloud reasoning is appropriate."""
    from ..ai.command_intents import canonical_command, switch_followup
    from ..ai.speech_commands import clean_transcript
    recent = db.query(Task).filter(Task.id != task.id).order_by(Task.created_at.desc()).first()
    spoken = clean_transcript(task.command)  # Whisper ends sentences with "." and people say "open THE youtube": the parsers expect neither
    spoken = _apply_correction(db, spoken, recent)
    routed = switch_followup(spoken, recent) or canonical_command(spoken)
    from ..ai.browser_commands import youtube_followup
    media = youtube_followup(routed, recent) or browser_plan(routed)
    if media is not None:
        return media
    pieces = re.split(r"\s+(?:and|aur|और|اور)\s+", routed, flags=re.IGNORECASE)
    if 1 < len(pieces) <= 4:
        plans = [local_plan(piece) for piece in pieces]
        if all(plan and plan.actions for plan in plans):
            return Plan(actions=[action for plan in plans for action in plan.actions], note="Local agent in commands ko ek ek karke chalaye ga.")
    simple = local_plan(routed)
    if simple is not None:
        return simple
    from ..ai.window_commands import window_plan
    window = window_plan(task, registry, routed)
    if window is not None:
        return window
    canonical = command_grammar(routed)
    intent = _parse(canonical)
    if intent is not None:
        return _resolve(task, intent, registry)
    raw = canonical
    # Context is the immediately preceding local clarification, never arbitrary old approval.
    prior = db.query(Task).filter(Task.id != task.id).order_by(Task.created_at.desc()).first()
    if prior and prior.status == TaskStatus.COMPLETED and prior.actions:
        last = prior.actions[-1]
        if last.tool_name == "manage_system_setting" and last.status == "succeeded" and len(raw.split()) <= 8:
            setting = json.loads(last.args_json)["setting"]
            relative = re.sub(r"\b(?:aur|thora)\b", "", raw, flags=re.IGNORECASE)
            followup = local_plan(f"{setting} {relative}")
            if followup and followup.actions:
                return followup
    if prior and prior.status in {TaskStatus.COMPLETED, TaskStatus.AWAITING_INPUT} and prior.agent_state_json:
        state = json.loads(prior.agent_state_json)
        workflow = state.get("workflow") if state.get("provider") == "local" else None
        if workflow:
            workflow = dict(workflow)
            candidates = workflow.get("candidates", [])
            choice = re.fullmatch(r"(?:(preview|open)\s+)?(\d+)", raw, re.IGNORECASE)
            if choice and 1 <= int(choice.group(2)) <= len(candidates):
                selected = candidates[int(choice.group(2)) - 1]
                workflow["source"] = selected
                workflow.pop("candidates", None)
                if choice.group(1):
                    task.agent_state_json = json.dumps({"provider": "local", "workflow": workflow, "preview": True})
                    plan = _action("open_folder" if workflow["kind"] == "folder" else "open_file", {"path": selected})
                    plan.note = "Preview khol raha hoon. Kya isi par original operation karna hai? Haan ya nahin batayein."
                    return plan
                return _resolve(task, workflow, registry)
            resolved = relative_candidates(raw, registry)
            path = str(resolved[0]) if len(resolved) == 1 else _path(raw)
            if Path(path).is_absolute():
                workflow["destination" if workflow.pop("awaiting_destination", False) else "source"] = path
                workflow.pop("candidates", None)
                return _resolve(task, workflow, registry)
            reply = normalize_speech(raw)
            if reply in {"yes", "haan", "han", "haan yahi", "haan kar do", "हाँ", "हां", "ہاں"}:
                if state.get("preview"):
                    return _resolve(task, workflow, registry)
                return _ask(task, workflow, "Pehle candidate ka number ya exact path select karein.")
            if reply in {"no", "nahi", "nahin", "नहीं", "نہیں"}:
                workflow.pop("candidates", None)
                return _ask(task, workflow, "Theek hai. Sahi folder/file ka exact path batayein.")
    # A plain web search needs no LLM: the browser handles search results.
    match = re.fullmatch(r"(?:search|google|search for) (.+)", raw, re.IGNORECASE)
    if match:
        return _action("web_search", {"query": match.group(1)})
    match = re.fullmatch(r"open (?:app|application) (.+)", raw, re.IGNORECASE)
    if match:
        target = known_target(match.group(1))
        if target.casefold() == "youtube":
            return _action("open_url", {"url": "https://www.youtube.com"})
        return _action("open_application", {"app_name": target})
    match = re.fullmatch(r"open (?:website|site) (\S+)", raw, re.IGNORECASE)
    if match:
        url = match.group(1)
        return _action("open_url", {"url": url if url.startswith(("https://", "http://")) else "https://" + url})
    match = re.fullmatch(r"(?:download (?!package )|find app )(.+)", raw, re.IGNORECASE)
    if match:
        return _action("application_package", {"action": "search", "query": match.group(1)})
    match = re.fullmatch(r"download package ([\w.+-]+)(?: from (winget|msstore))?", raw, re.IGNORECASE)
    if match:
        return _action("application_package", {"action": "download", "query": match.group(1), "source": match.group(2) or "winget"})
    if normalize_speech(raw) in {"take screenshot", "take a screenshot", "screenshot lo"}:
        return _action("take_screenshot", {})
    match = re.fullmatch(r"(?:compose|draft|write)(?: an?| the)? email to (\S+) with subject (.+?) and body (.+)", raw, re.IGNORECASE | re.DOTALL)
    if match:
        return _action("compose_email", {"to": match.group(1), "subject": match.group(2), "body": match.group(3)})
    match = re.fullmatch(r"(compress folder|extract archive|read file) (.+)", raw, re.IGNORECASE)
    if match:
        tool, key = {"compress folder": ("compress_folder", "folder_path"), "extract archive": ("extract_file", "zip_path"), "read file": ("read_text_file", "path")}[match.group(1).lower()]
        path = _path(match.group(2))
        if Path(path).is_absolute():
            return _action(tool, {key: path})
        return Plan(note="Is operation ke liye file/folder ka exact path batayein.")
    match = re.fullmatch(r"(?:close|band karo|band kar do) (?:app |window )?(.+)", raw, re.IGNORECASE)
    if match:
        wanted = match.group(1).casefold()
        tool = registry.get("control_desktop")
        inspected = tool.execute(tool.validate_args({"steps": [{"action": "inspect"}]}))
        if not inspected.success:
            return Plan(note=inspected.error or "Open windows inspect nahi ho sakin.")
        titles = [title for step in inspected.output.get("completed_steps", []) for title in step.get("open_windows", [])]
        matches = [title for title in dict.fromkeys(titles) if wanted in title.casefold()]
        if len(matches) == 1:
            return _action("control_desktop", {"target_window": matches[0], "steps": [{"action": "close_window"}]})
        return Plan(note="Exact window title ke saath 'close window ...' kahein:\n" + "\n".join(matches or titles))
    if re.match(r"^(?:create|copy|move|rename|delete|open|close|volume|brightness|bluetooth|folder|file)\b", raw, re.IGNORECASE):
        return Plan(note="Local agent ko command clear nahi hui. App ka exact naam, file/folder ka path, ya setting ki value batayein. Mushkil reasoning ke liye 'AI:' se request shuru kar sakti hain.")
    return None
