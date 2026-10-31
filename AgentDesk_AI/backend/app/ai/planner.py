"""Planning: ask the provider for a plan, then validate it independently.

The backend never trusts the model blindly: every tool name must exist in
the registry and every argument set must pass the tool's own Pydantic
schema before anything is stored or executed.
"""
from __future__ import annotations

from pydantic import ValidationError

from ..tools.registry import ToolRegistry, UnknownToolError
from .base import AIConfigError, AIError, AIProvider, Plan
from .groq_provider import GroqProvider, SYSTEM_PROMPT as GROQ_SYSTEM_PROMPT
from .mock_provider import MockProvider
from .openai_provider import OpenAIProvider


def _pool(settings):
    from .key_pool import pool_for

    keys = settings.resolved_groq_keys()
    return pool_for(keys) if len(keys) > 1 else None


def get_provider(settings=None) -> AIProvider:
    """Select the AI provider from configuration.

    There is deliberately NO silent fallback: if the configured provider
    cannot be built, this raises AIConfigError with a clear message.
    """
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    provider_name = settings.ai_provider.strip().lower()
    if provider_name == "groq":
        # Raises AIConfigError when the key is missing - never falls back.
        primary = GroqProvider(
            api_key=(settings.resolved_groq_keys() or [""])[0],
            key_pool=_pool(settings),
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
        )
        from .resilient_provider import CompatibleAgentProvider, ResilientProvider
        backup = None
        fallback = settings.ai_fallback_provider.strip().lower()
        if fallback == "gemini" and settings.gemini_api_key:
            backup = CompatibleAgentProvider(settings.gemini_api_key, settings.ai_fallback_model or settings.gemini_model, "gemini", settings.ai_timeout_seconds)
        elif fallback == "openai" and settings.openai_api_key:
            backup = CompatibleAgentProvider(settings.openai_api_key, settings.ai_fallback_model or settings.openai_agent_model, "openai", settings.ai_timeout_seconds)
        elif fallback not in {"none", "gemini", "openai"}:
            raise AIConfigError("AI_FALLBACK_PROVIDER must be none, gemini or openai")
        return ResilientProvider(primary, backup)
    if provider_name == "openai":
        if not settings.openai_api_key:
            raise AIConfigError(
                "AI_PROVIDER is 'openai' but OPENAI_API_KEY is not set. "
                "Add it to backend/.env, or set AI_PROVIDER=mock for explicit offline mode."
            )
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
        )
    if provider_name == "mock":
        return MockProvider()
    raise AIConfigError(
        f"Unknown AI_PROVIDER '{settings.ai_provider}'. Use 'groq', 'openai' or 'mock'."
    )


def plan_command(command: str, registry: ToolRegistry, provider: AIProvider) -> Plan:
    """Generate a plan and validate every action against the registry."""
    plan = provider.generate_plan(command, registry.describe_for_prompt())
    for i, action in enumerate(plan.actions):
        try:
            tool = registry.get(action.tool)
        except UnknownToolError as exc:
            raise AIError(f"Plan rejected: action {i} uses {exc}") from exc
        try:
            tool.validate_args(action.args)
        except ValidationError as exc:
            raise AIError(
                f"Plan rejected: action {i} ('{action.tool}') has invalid arguments: {exc}"
            ) from exc
    return plan
