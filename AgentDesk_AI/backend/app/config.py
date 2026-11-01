"""Application configuration loaded from environment variables.

All secrets come from the environment (or a local .env file). Nothing here
prints or exposes secret values.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    app_name: str = "AgentDesk AI"
    app_version: str = "0.2.0"

    # --- AI provider ---
    # "groq" (primary, real LLM with tool calling), "openai" (alternative),
    # or "mock" (explicit offline/test mode only - never a silent fallback).
    ai_provider: str = "groq"
    # Verified tool-calling model on Groq (see docs/GROQ_SETUP.md). Check
    # https://console.groq.com/docs/models for the current catalogue -
    # model IDs and availability change over time.
    ai_model: str = "openai/gpt-oss-120b"
    groq_api_key: str | None = None
    # Extra keys (comma separated). When one hits its limit the next is used automatically (ai/key_pool.py).
    groq_api_keys: str = ""
    # Optional cloud brain (Render): planning/chat/speech run there, tools still run on this PC. See app/cloud_main.py.
    cloud_brain_url: str = ""
    # Contact e-mail sent to Wikipedia in the User-Agent (their policy). Empty = Wikipedia is not used.
    wikipedia_contact_email: str = ""
    cloud_brain_token: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    # Explicit opt-in: another provider may have separate billing/data handling.
    ai_fallback_provider: str = "none"
    ai_fallback_model: str = ""
    gemini_model: str = "gemini-3.8-flash"
    openai_agent_model: str = "gpt-5.4-mini"
    # Local agent handles PC work. Cloud is explicit unless automatic fallback is opted in.
    ai_cloud_mode: str = "explicit"
    discovery_directories: str = Field(default="", validation_alias=AliasChoices("AGENTDESK_DISCOVERY_DIRECTORIES", "discovery_directories"))
    ai_timeout_seconds: int = 30
    # Speech models on Groq (used by ai/groq_client.py). STT falls back in order.
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_stt_fallback_model: str = "whisper-large-v3"
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "autumn"
    ai_max_retries: int = 2
    # Strict-JSON planner rollout: legacy | shadow | structured (see orchestration/planner_mode.py).
    planner_mode: str = "legacy"
    # Bound on agentic-loop tool steps per task (cost + runaway protection).
    agent_max_steps: int = 10

    # --- E-mail (OAuth client ids; see docs/EMAIL_SETUP.md). The ids are not secrets in a desktop app, but they are not committed either. ---
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    ms_oauth_client_id: str = ""

    # --- Database ---
    database_url: str = "sqlite:///./agentdesk.db"

    # --- Frontend ---
    frontend_origin: str = "http://localhost:5173"

    # --- Security ---
    allowed_directories: str = Field(
        default="", validation_alias=AliasChoices("AGENTDESK_ALLOWED_DIRECTORIES", "allowed_directories")
    )
    approved_applications: str = Field(
        default="chrome,notepad,calc,explorer",
        validation_alias=AliasChoices("AGENTDESK_APPROVED_APPLICATIONS", "approved_applications"),
    )
    test_dir: str = Field(
        default="./agentdesk_test", validation_alias=AliasChoices("AGENTDESK_TEST_DIR", "test_dir")
    )
    tool_timeout_seconds: int = 30
    # "process" (default): tools run in a worker process that is killed on timeout/pause/cancel.
    # "thread": in-process (tests only; a timed-out thread cannot be stopped).
    tool_isolation: str = "process"
    # Pilot: append one privacy-safe CSV row per finished task (app/metrics.py). Off by default.
    pilot_metrics: bool = False
    # Medium/high-risk approvals expire if the action is not run in time.
    approval_ttl_seconds: int = 600

    # --- Retention (0 = disabled; nothing is ever deleted unless the user sets this) ---
    # After this many days, finished tasks lose their stored AI conversation
    # (agent_state_json) and expired rollback backups are deleted. Task rows,
    # actions and results are never deleted.
    retention_days: int = 0

    # --- Local entitlement plan: "unlimited" (default), "standard" or "free". See app/usage.py ---
    entitlement_plan: str = Field(default="unlimited", validation_alias=AliasChoices("AGENTDESK_PLAN", "entitlement_plan"))

    def resolved_allowed_directories(self) -> list[Path]:
        raw = [p.strip() for p in self.allowed_directories.split(",") if p.strip()]
        if not raw:
            raw = [str(Path.home()), str(Path(self.test_dir).resolve())]
        return [Path(p).expanduser() for p in raw]

    def resolved_approved_applications(self) -> list[str]:
        return [a.strip().lower() for a in self.approved_applications.split(",") if a.strip()]

    def resolved_discovery_directories(self) -> list[Path]:
        extra = [Path(p.strip()).expanduser() for p in self.discovery_directories.split(",") if p.strip()]
        return list(dict.fromkeys([*self.resolved_allowed_directories(), *extra]))

    @property
    def is_mock_mode(self) -> bool:
        return self.ai_provider.strip().lower() == "mock"

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key or self.groq_api_keys.strip())

    @property
    def cloud_configured(self) -> bool:
        return bool(self.cloud_brain_url.strip() and (self.cloud_brain_token or "").strip())

    def resolved_groq_keys(self) -> list[str]:
        from .ai.key_pool import parse_keys

        return parse_keys(self.groq_api_key, self.groq_api_keys)


@lru_cache
def get_settings() -> Settings:
    return Settings()
