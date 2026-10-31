"""Client for the cloud brain, used by the Windows app when CLOUD_BRAIN_URL is set.

`RemotePlanner` has the same interface as the local planner providers: it asks `/cloud/plan`, then validates the
returned plan against THIS machine's registry, allowed folders and risk rules. The server is never trusted.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .ai.base import AIConfigError, AIError, AIRateLimitError
from .nlu.planner import PlannerContext, PlannerProvider, PlanResult
from .nlu.schema import NLUPlan, validate_plan

COLD_START_TIMEOUT = 75.0   # a sleeping free Render instance needs ~30-60 s to wake


class CloudBrain:
    def __init__(self, base_url: str, token: str, timeout: float = COLD_START_TIMEOUT,
                 transport: httpx.BaseTransport | None = None):
        if not base_url or not token:
            raise AIConfigError("CLOUD_BRAIN_URL and CLOUD_BRAIN_TOKEN must both be set.")
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {token}"})

    def __repr__(self) -> str:  # never show the token
        return f"CloudBrain({self._http.base_url})"

    def _post(self, path: str, **kw: Any) -> httpx.Response:
        try:
            r = self._http.post(path, **kw)
        except httpx.HTTPError as exc:
            raise AIError(f"Cloud brain unreachable: {type(exc).__name__}") from exc
        if r.status_code == 429:
            raise AIRateLimitError("Cloud brain is busy; try again shortly.", float(r.headers.get("retry-after", 30)))
        if r.status_code in (401, 503):
            raise AIConfigError(f"Cloud brain refused the request ({r.status_code}): check CLOUD_BRAIN_TOKEN.")
        if r.status_code >= 400:
            raise AIError(f"Cloud brain error {r.status_code}: {r.text[:200]}")
        return r

    def plan(self, command: str, memory: list[dict[str, Any]] | None = None, desktop: dict[str, Any] | None = None,
             language_hint: str | None = None) -> dict[str, Any]:
        return self._post("/cloud/plan", json={"command": command, "memory": memory or [], "desktop": desktop or {},
                                               "language_hint": language_hint}).json()

    def chat(self, messages: list[dict[str, str]], fast: bool = False) -> str:
        return self._post("/cloud/chat", json={"messages": messages, "fast": fast}).json()["content"]

    def transcribe(self, audio: bytes, filename: str = "speech.wav", language: str | None = None,
                   prompt: str | None = None) -> str:
        data = {k: v for k, v in {"language": language, "prompt": prompt}.items() if v}
        return self._post("/cloud/stt", files={"audio": (filename, audio, "audio/wav")}, data=data).json()["text"]

    def speak(self, text: str, voice: str | None = None) -> bytes:
        return self._post("/cloud/tts", json={"text": text, "voice": voice}).content


class RemotePlanner(PlannerProvider):
    name = "cloud"

    def __init__(self, brain: CloudBrain):
        self.brain = brain

    def plan(self, context: PlannerContext) -> PlanResult:
        started = time.perf_counter()
        memory = [{"command": t.command, "steps": t.steps, "status": t.status, "reply": t.reply}
                  for t in context.memory.turns]
        body = self.brain.plan(context.command, memory, context.desktop, context.language_hint)
        plan = NLUPlan.model_validate(body["plan"])
        validation = validate_plan(plan, context.registry, check_paths=context.check_paths)  # local rules decide
        return PlanResult(plan, validation, "cloud", body.get("model", ""), body.get("usage", {}),
                          (time.perf_counter() - started) * 1000, [] if validation.ok else validation.errors)
