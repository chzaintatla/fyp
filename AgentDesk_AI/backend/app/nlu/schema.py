"""Plan schema v2: what every planner (local or LLM) must return.

`NLUPlan` is the single contract. `plan_json_schema(registry)` turns it into a
strict JSON schema for structured outputs: the `tool` field of each step is
an enum generated from the registry (the model cannot name a tool that does
not exist), every property is required, every object has
additionalProperties:false and optional values are nullable.

`validate_plan` is the server-side gate for every provider: unknown tool,
unknown or invalid argument, bad dependency or bad step reference = reject
with a reason. Risk comes from the tool (`risk_for_args`), never from the plan.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..tools.base import RiskLevel

INTENTS: tuple[str, ...] = (
    "system_setting", "settings_page", "open_app", "close_window", "window_control", "open_website", "web_search",
    "play_youtube", "browser_tab", "open_folder", "open_file", "list_folder", "find_files",
    "create_folder", "copy", "move", "rename", "delete", "restore", "list_trash", "read_file", "media_control",
    "whatsapp_draft", "app_install",
    "write_file", "edit_file", "compress", "extract", "screenshot", "email_draft", "package_search",
    "download", "multi", "clarify", "unsupported", "chat",
)
LANGUAGES: tuple[str, ...] = ("en", "ur", "roman_ur", "hi")
NO_STEP_INTENTS = {"clarify", "unsupported", "chat"}
# "$0" = the path found by step 0 (a find_paths/find_files step this step depends on).
STEP_REF = re.compile(r"^\$(\d+)$")
REF_SOURCES = {"find_paths", "find_files"}
PATH_KEYS = {"path", "source", "destination", "folder_path", "zip_path", "output_path", "folder", "output_directory"}


class PlanStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: int | None = None
    expected_outcome: str = ""


class NLUPlan(BaseModel):
    intent: str
    language: Literal["en", "ur", "roman_ur", "hi"] = "en"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)
    reply: str = ""

    def to_legacy(self):
        """Convert to the executor's existing Plan. Step references must be resolved first."""
        from ..ai.base import Plan, PlanAction

        actions = []
        for step in self.steps:
            args = clean_args(step.args)
            if any(isinstance(v, str) and STEP_REF.match(v) for v in args.values()):
                raise ValueError("Plan has unresolved step references; resolve paths before execution.")
            actions.append(PlanAction(tool=step.tool, args=args, rationale=step.expected_outcome))
        note = self.clarifying_question if self.needs_clarification else (self.reply or None)
        return Plan(actions=actions, note=note)


def clean_args(args: dict[str, Any]) -> dict[str, Any]:
    """Strict schemas make optional values null; drop them so tool defaults apply."""
    return {k: v for k, v in (args or {}).items() if v is not None}


# ------------------------------------------------------------------ JSON schema
_DROP_KEYS = {"title", "default", "pattern", "minLength", "maxLength", "minimum", "maximum",
              "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "format", "examples"}


def _inline(schema: Any, defs: dict[str, Any]) -> Any:
    if isinstance(schema, list):
        return [_inline(s, defs) for s in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _inline(copy.deepcopy(defs[name]), defs)
    return {k: _inline(v, defs) for k, v in schema.items() if k != "$defs"}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    if "anyOf" in schema:
        if not any(s.get("type") == "null" for s in schema["anyOf"]):
            schema["anyOf"].append({"type": "null"})
        return schema
    if "enum" in schema:
        return {"anyOf": [schema, {"type": "null"}]}
    kind = schema.get("type")
    if isinstance(kind, str):
        schema["type"] = [kind, "null"]
    elif isinstance(kind, list) and "null" not in kind:
        schema["type"] = kind + ["null"]
    return schema


def _strict(schema: Any) -> Any:
    """Make a JSON schema follow structured-output strict rules."""
    if isinstance(schema, list):
        return [_strict(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: _strict(v) for k, v in schema.items() if k not in _DROP_KEYS}
    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties", {})
        required = set(schema.get("required", []))
        for name, prop in props.items():
            if name not in required:
                props[name] = _nullable(prop)
        out["type"] = "object"
        out["properties"] = props
        if props:  # Groq rejects "required": [] on a no-argument tool
            out["required"] = list(props)
        else:
            out.pop("required", None)
        out["additionalProperties"] = False
    return out


def tool_args_schema(tool) -> dict[str, Any]:
    raw = tool.args_schema.model_json_schema()
    return _strict(_inline(raw, raw.get("$defs", {})))


def plan_json_schema(registry) -> dict[str, Any]:
    """Strict schema for NLUPlan with one step variant per registered tool."""
    variants = []
    for name in registry.names():
        variants.append({
            "type": "object",
            "additionalProperties": False,
            "required": ["tool", "args", "depends_on", "expected_outcome"],
            "properties": {
                "tool": {"type": "string", "enum": [name]},
                "args": tool_args_schema(registry.get(name)),
                "depends_on": {"type": ["integer", "null"]},
                "expected_outcome": {"type": "string"},
            },
        })
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "language", "confidence", "needs_clarification",
                     "clarifying_question", "steps", "reply"],
        "properties": {
            "intent": {"type": "string", "enum": list(INTENTS)},
            "language": {"type": "string", "enum": list(LANGUAGES)},
            "confidence": {"type": "number"},
            "needs_clarification": {"type": "boolean"},
            "clarifying_question": {"type": ["string", "null"]},
            "steps": {"type": "array", "items": {"anyOf": variants}},
            "reply": {"type": "string"},
        },
    }


def tool_names_in_schema(schema: dict[str, Any]) -> list[str]:
    return [v["properties"]["tool"]["enum"][0] for v in schema["properties"]["steps"]["items"]["anyOf"]]


# ------------------------------------------------------------------ validation
@dataclass
class ValidatedStep:
    tool: str
    args: dict[str, Any]
    parsed: Any
    risk: RiskLevel
    depends_on: int | None


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    steps: list[ValidatedStep] = field(default_factory=list)

    @property
    def max_risk(self) -> RiskLevel:
        order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]
        return max((s.risk for s in self.steps), key=order.index, default=RiskLevel.LOW)


def _outside_allowed(value: str, allowed: list[Path]) -> bool:
    from ..security.paths import resolve_within_allowed

    raw = value.strip()
    if not raw or STEP_REF.match(raw):
        return False
    expanded = str(Path(raw).expanduser())
    if not (Path(expanded).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", raw)):
        return False  # a bare name is resolved by discovery inside allowed roots
    try:
        resolve_within_allowed(expanded, allowed)
        return False
    except ValueError:
        return True


def validate_plan(plan: NLUPlan, registry, check_paths: bool = True) -> ValidationResult:
    """Server-side validation shared by every planner. Never trusts the model."""
    from ..tools.registry import UnknownToolError

    errors: list[str] = []
    steps: list[ValidatedStep] = []
    if plan.intent not in INTENTS:
        errors.append(f"Unknown intent '{plan.intent}'.")
    if plan.needs_clarification and not (plan.clarifying_question or "").strip():
        errors.append("needs_clarification is true but clarifying_question is empty.")
    if plan.intent in NO_STEP_INTENTS and plan.steps:
        errors.append(f"Intent '{plan.intent}' must not contain steps.")
    for index, step in enumerate(plan.steps):
        try:
            tool = registry.get(step.tool)
        except UnknownToolError:
            errors.append(f"Step {index}: unknown tool '{step.tool}'.")
            continue
        args = clean_args(step.args)
        unknown = sorted(set(args) - set(tool.args_schema.model_fields))
        if unknown:
            errors.append(f"Step {index}: '{step.tool}' has no argument(s) {', '.join(unknown)}.")
            continue
        if step.depends_on is not None and not (0 <= step.depends_on < index):
            errors.append(f"Step {index}: depends_on must point to an earlier step.")
            continue
        bad_ref = False
        for key, value in args.items():
            ref = STEP_REF.match(value) if isinstance(value, str) else None
            if ref is None:
                continue
            source = int(ref.group(1))
            if source != step.depends_on or source >= index or plan.steps[source].tool not in REF_SOURCES:
                errors.append(f"Step {index}: '{key}' refers to step {source}, which is not a find step it depends on.")
                bad_ref = True
        if bad_ref:
            continue
        try:
            parsed = tool.validate_args(args)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ())) or "args"
            errors.append(f"Step {index}: invalid '{where}' for '{step.tool}': {first.get('msg')}.")
            continue
        if check_paths:
            outside = [k for k, v in args.items() if k in PATH_KEYS and isinstance(v, str)
                       and _outside_allowed(v, registry.ctx.allowed_directories)]
            if outside:
                errors.append(f"Step {index}: path in '{outside[0]}' is outside the allowed folders.")
                continue
        steps.append(ValidatedStep(step.tool, args, parsed, tool.risk_for_args(parsed), step.depends_on))
    return ValidationResult(ok=not errors, errors=errors, steps=steps)
