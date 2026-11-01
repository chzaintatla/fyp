"""Pydantic request/response schemas for the HTTP API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    # Unknown fields (e.g. a legacy "auto_approve") are rejected with 422:
    # approval is decided server-side and can never be requested by a client.
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=2000)
    idempotency_key: str | None = Field(default=None, max_length=128)


class TaskReply(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ApprovalRequest(BaseModel):
    action_ids: list[str] | None = None  # None => all pending actions


class ActionOut(BaseModel):
    id: str
    seq: int
    tool_name: str
    args: dict[str, Any]
    risk: str
    status: str
    result: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    error: str | None = None
    rollback_status: str | None = None
    rollback_detail: str | None = None


class TaskOut(BaseModel):
    id: str
    command: str
    status: str
    plan_note: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    retry_at: float | None = None
    provider_note: str | None = None
    # Present when a task stopped after some actions had already changed things.
    partial: dict[str, Any] | None = None
    actions: list[ActionOut] = []


class ConfigStatusOut(BaseModel):
    ai_provider: str
    ai_model: str
    ai_configured: bool
    mock_mode: bool
    groq_configured: bool = False
    search: dict[str, bool] = {}   # which web-search providers are set up: {"wikipedia": bool, "tavily": bool}
    speech_ready: bool = False  # server-side speech (Whisper / Orpheus / edge-tts) can be used
    agent_max_steps: int = 10
    fallback_provider: str = "none"
    fallback_configured: bool = False
    local_commands: bool = True
    cloud_mode: str = "explicit"
    browser_connected: bool = False
    browser_profiles: int = 0
    allowed_directories: list[str]
    approved_applications: list[str]
    platform: str
    plan: str = "unlimited"


class HealthOut(BaseModel):
    status: str
    version: str
