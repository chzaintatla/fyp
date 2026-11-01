"""Cloud brain: token guard, planning/chat/speech endpoints, and the Windows-side client that re-validates."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import cloud_main
from app.ai.base import AIConfigError
from app.cloud_client import CloudBrain, RemotePlanner
from app.nlu.planner import PlannerContext, PlanResult
from app.nlu.schema import NLUPlan, PlanStep, validate_plan
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry

TOKEN = "t" * 40
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CLOUD_API_TOKEN", TOKEN)
    monkeypatch.setenv("CLOUD_RATE_PER_MINUTE", "1000")
    cloud_main._hits.clear()
    return TestClient(cloud_main.app)


def test_health_is_open_everything_else_needs_the_token(client, monkeypatch):
    assert client.get("/health").json()["token_set"] is True
    assert client.post("/cloud/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401
    assert client.post("/cloud/chat", headers={"Authorization": "Bearer wrong"}, json={}).status_code == 401
    monkeypatch.delenv("CLOUD_API_TOKEN")
    assert client.post("/cloud/chat", headers=H, json={}).status_code == 503  # fails closed without a secret


def test_rate_limit(client, monkeypatch):
    monkeypatch.setenv("CLOUD_RATE_PER_MINUTE", "2")
    codes = [client.post("/cloud/tts", headers=H, json={"text": ""}).status_code for _ in range(3)]
    assert codes[-1] == 429


class FakeGroq:
    def chat(self, messages, **kw):
        return {"content": "hello", "model": kw.get("model", "")}

    def transcribe(self, data, **kw):
        return {"text": "chrome kholo", "model": "whisper"}

    def speak(self, text, voice=None):
        return b"RIFFfake"


def test_chat_stt_tts(client, monkeypatch):
    monkeypatch.setattr("app.ai.groq_client.get_groq_client", lambda: FakeGroq())
    assert client.post("/cloud/chat", headers=H, json={"messages": [{"role": "user", "content": "hi"}]}).json()["content"] == "hello"
    assert client.post("/cloud/chat", headers=H, json={"messages": [{"role": "tool", "content": "x"}]}).status_code == 422
    r = client.post("/cloud/stt", headers=H, files={"audio": ("a.wav", b"123", "audio/wav")})
    assert r.json()["text"] == "chrome kholo"
    assert client.post("/cloud/tts", headers=H, json={"text": "Done."}).content == b"RIFFfake"
    assert client.post("/cloud/tts", headers=H, json={"text": "x" * 201}).status_code == 422


def _plan(tool="open_application", **args):
    return NLUPlan(intent="open_app", confidence=0.9, steps=[PlanStep(tool=tool, args=args)])


class FakePlanner:
    llm = None

    def __init__(self, plan):
        self.answer = plan

    def plan(self, ctx):
        return PlanResult(self.answer, validate_plan(self.answer, ctx.registry, check_paths=ctx.check_paths), "groq", "m")


def test_plan_endpoint_returns_plan_without_path_checks(client, monkeypatch):
    plan = _plan("create_folder", path=r"C:\Users\Someone\Documents\X")  # a Windows path, unknown to the server
    monkeypatch.setattr(cloud_main, "planner_from_settings", lambda s: FakePlanner(plan))
    body = client.post("/cloud/plan", headers=H, json={"command": "make folder X", "memory": [{"command": "a", "bogus": 1}]}).json()
    assert body["valid"] and body["plan"]["steps"][0]["tool"] == "create_folder"


def _brain(app_client) -> CloudBrain:
    transport = httpx.MockTransport(lambda req: httpx.Response(
        app_client.post(req.url.path, headers=dict(req.headers), content=req.content).status_code,
        content=app_client.post(req.url.path, headers=dict(req.headers), content=req.content).content,
        headers={"content-type": "application/json"}))
    return CloudBrain("http://cloud.test", TOKEN, transport=transport)


def test_client_revalidates_with_local_rules(client, monkeypatch, tmp_path: Path):
    outside = _plan("create_folder", path=r"C:\Windows\System32\evil")
    monkeypatch.setattr(cloud_main, "planner_from_settings", lambda s: FakePlanner(outside))
    registry = ToolRegistry(ToolContext(allowed_directories=[tmp_path], approved_applications=["chrome"], test_dir=tmp_path))
    result = RemotePlanner(_brain(client)).plan(PlannerContext(command="make evil", registry=registry))
    assert result.source == "cloud" and not result.validation.ok  # the PC's own allowed-folder rule rejects it


def test_client_needs_url_and_token_and_hides_token():
    with pytest.raises(AIConfigError):
        CloudBrain("", "")
    assert TOKEN not in repr(CloudBrain("http://x", TOKEN))


def test_foreign_database_url_cannot_break_the_cloud_brain():
    """A postgresql:// DATABASE_URL (no driver installed on Render) used to make every /cloud/plan answer 500."""
    import subprocess
    import sys
    code = ("import os; os.environ['DATABASE_URL']='postgresql://u:p@localhost/db'; os.environ['CLOUD_API_TOKEN']='a'*36\n"
            "from fastapi.testclient import TestClient\nfrom app.cloud_main import app\n"
            "r = TestClient(app).post('/cloud/plan', json={'command': 'open notepad'}, headers={'Authorization': 'Bearer ' + 'a'*36})\n"
            "print(r.status_code)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
                         env={**__import__("os").environ, "AI_PROVIDER": "mock", "GROQ_API_KEYS": "", "CLOUD_BRAIN_URL": ""})
    assert out.stdout.strip().splitlines()[-1] in {"200", "502", "503"}, out.stderr[-500:]   # anything but a crash (500)
