"""Base classes for AgentDesk AI tools.

Every tool the AI can invoke is a BaseTool subclass with:
- a JSON input schema (Pydantic),
- a risk level driving the permission policy,
- execution logic, and
- verification logic.

The AI NEVER runs arbitrary code: it only picks a registered tool name and
arguments, which are validated here independently of the model.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolResult(BaseModel):
    success: bool
    output: dict[str, Any] = {}
    error: str | None = None


class Verification(BaseModel):
    passed: bool
    checks: list[str] = []
    details: str | None = None
    # What was observed on the real system (sizes, hashes, PIDs, window titles,
    # read-back values). A pass without evidence is treated as a failure.
    evidence: dict[str, Any] = {}


@dataclass
class ToolContext:
    """Runtime context handed to every tool."""

    allowed_directories: list[Path] = field(default_factory=list)
    approved_applications: list[str] = field(default_factory=list)
    test_dir: Path = field(default_factory=lambda: Path("./agentdesk_test").resolve())
    discovery_directories: list[Path] = field(default_factory=list)
    isolate_system_controls: bool = False

    @classmethod
    def from_settings(cls) -> "ToolContext":
        from ..config import get_settings

        settings = get_settings()
        return cls(
            allowed_directories=settings.resolved_allowed_directories(),
            approved_applications=settings.resolved_approved_applications(),
            test_dir=Path(settings.test_dir).expanduser().resolve(),
            discovery_directories=settings.resolved_discovery_directories(),
            isolate_system_controls=True,
        )


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    args_schema: Type[BaseModel] = BaseModel
    # Seconds before the worker running this tool is killed; None = settings default.
    timeout_seconds: int | None = None
    # "process": run in a killable worker process (default). "thread": must run inside the
    # server process (e.g. tools that use the browser-extension socket held by the server).
    isolation: str = "process"
    # True: a pause/cancel may stop this tool mid-run (killing its worker) without leaving a
    # half-applied change. Other tools finish their current step first.
    interruptible: bool = False
    # True: not shown to the model, not in any plan schema, and a plan that names it is rejected (only server code runs it).
    hidden_from_model: bool = False

    def on_cancel(self, args: BaseModel) -> None:
        """Clean up partial work after the step was cancelled (e.g. a partial download)."""

    def __init__(self, ctx: ToolContext | None = None) -> None:
        self.ctx = ctx or ToolContext.from_settings()

    @property
    def requires_approval(self) -> bool:
        return self.risk_for_args(None) != RiskLevel.LOW

    def risk_for_args(self, args: BaseModel | None) -> RiskLevel:
        """Risk may depend on arguments (e.g. approved vs unapproved app)."""
        return self.risk_level

    def validate_args(self, raw: dict[str, Any]) -> BaseModel:
        return self.args_schema.model_validate(raw)

    @abstractmethod
    def execute(self, args: BaseModel) -> ToolResult:
        """Perform the action. Must be honest: report real outcomes only."""

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        """Check the real system state after execute(). Every tool must override this:
        the tool's own report is never evidence."""
        return Verification(passed=False, checks=["no verifier"],
                            details=f"{self.name} has no verifier; success cannot be confirmed.")

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk_level.value,
            "requires_approval": self.risk_level != RiskLevel.LOW,
            "args_schema": self.args_schema.model_json_schema(),
        }
