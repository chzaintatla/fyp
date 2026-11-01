"""AI provider abstraction: plans are structured data, never raw code."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class PlanAction(BaseModel):
    tool: str = Field(description="Registered tool name, e.g. 'create_folder'")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool")
    rationale: str = Field(default="", description="Why this action is needed")


class Plan(BaseModel):
    actions: list[PlanAction] = Field(default_factory=list)
    note: str | None = Field(default=None, description="Message for the user when no actions apply")
    mock: bool = Field(default=False, description="True when produced by the mock provider")
    intent: str | None = Field(default=None, description="The planner's intent label (routing only; never used for risk)")


class AIError(Exception):
    """Anything that goes wrong talking to / understanding the AI provider."""


class AIConfigError(AIError):
    """The AI provider is misconfigured (missing key, unknown provider, ...)."""


class AIRateLimitError(AIError):
    """A recoverable pause; no desktop action should be replayed to retry it."""
    def __init__(self, message: str, retry_after: float = 60):
        super().__init__(message)
        self.retry_after = max(1, retry_after)


class AIProvider(ABC):
    name: str = "base"
    is_mock: bool = False

    @abstractmethod
    def generate_plan(self, command: str, tool_descriptions: str) -> Plan:
        """Turn a natural-language command into a structured Plan."""
