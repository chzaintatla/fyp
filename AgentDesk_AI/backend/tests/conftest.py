"""Pytest configuration: isolated env, temp workspace, fresh database.

Environment variables are set BEFORE any app modules are imported, because
the settings object and DB engine are created at import time.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# --- isolated sandbox for the whole test session ------------------------------
SESSION_TMP = Path(tempfile.mkdtemp(prefix="agentdesk_tests_"))
os.environ["AI_PROVIDER"] = "mock"
os.environ["AI_MODEL"] = "mock-test-model"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("GROQ_API_KEYS", None)
os.environ["DATABASE_URL"] = f"sqlite:///{SESSION_TMP / 'test.db'}"
os.environ["AGENTDESK_ALLOWED_DIRECTORIES"] = ",".join(
    str(SESSION_TMP / sub) for sub in ("allowed", "home", "testdir")
)
os.environ["AGENTDESK_MOCK_HOME"] = str(SESSION_TMP / "home")
os.environ["AGENTDESK_TEST_DIR"] = str(SESSION_TMP / "testdir")
os.environ["TOOL_TIMEOUT_SECONDS"] = "10"
os.environ["TOOL_ISOLATION"] = "thread"  # tests monkeypatch tools in-process; worker tests opt in
os.environ["AGENTDESK_LOG_DIR"] = str(SESSION_TMP / "logs")
os.environ["AGENTDESK_REQUIRE_API_TOKEN"] = "0"  # auth is covered by tests/test_phase5_approvals.py
os.environ["AGENTDESK_TOKEN_FILE"] = str(SESSION_TMP / "api_token")

for sub in ("allowed", "home", "testdir"):
    (SESSION_TMP / sub).mkdir(parents=True, exist_ok=True)

import importlib.util  # noqa: E402

import pytest  # noqa: E402

# Tests that exercise the Groq/OpenAI SDK adapters need those optional runtime
# packages. Skip them visibly (never silently pass) when they are not installed.
_HAS_GROQ = importlib.util.find_spec("groq") is not None
_HAS_OPENAI = importlib.util.find_spec("openai") is not None
_FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist" / "index.html"
collect_ignore = [] if _HAS_GROQ else ["test_groq_provider.py"]


def pytest_collection_modifyitems(config, items):
    for item in items:
        name = item.nodeid
        if not _HAS_GROQ and "test_agent_loop.py" in name:
            item.add_marker(pytest.mark.skip(reason="groq package not installed"))
        elif not _HAS_OPENAI and "test_resilience.py" in name and (
            "rate_limited_task_resumes" in name or "compatible_backup_preserves" in name
        ):
            item.add_marker(pytest.mark.skip(reason="openai package not installed"))
        elif not _FRONTEND_DIST.is_file() and "test_built_frontend_and_api_share_one_server" in name:
            item.add_marker(pytest.mark.skip(reason="frontend/dist not built (npm run build)"))
        elif not _FRONTEND_DIST.is_file() and "test_launcher_reuses_healthy_server" in name:
            item.add_marker(pytest.mark.skip(reason="frontend/dist not built (npm run build)"))

from app.config import Settings as _Settings  # noqa: E402
_Settings.model_config["env_file"] = None  # tests never read a developer backend/.env
from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.tools.base import ToolContext  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

Base.metadata.create_all(bind=engine)


def _make_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        allowed_directories=[tmp_path],
        approved_applications=["chrome", "notepad"],
        test_dir=tmp_path / "testdir",
    )


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return _make_ctx(tmp_path)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
    # clean all rows after each test for isolation
    session = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        yield c


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(SESSION_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _agreeable_probe():
    """Verifiers need a desktop; tests get a permissive fake unless they install a strict one."""
    from app.verification import use_probe
    from tests.helpers.fake_probe import AgreeableProbe

    with use_probe(AgreeableProbe()) as probe:
        yield probe
