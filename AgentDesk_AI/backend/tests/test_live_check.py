"""scripts/live_check.py: every registered tool has a probe; sandbox probes pass for real."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.tools.registry import ToolRegistry

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "live_check.py"
spec = importlib.util.spec_from_file_location("live_check", SCRIPT)
live_check = importlib.util.module_from_spec(spec)
sys.modules["live_check"] = live_check  # dataclasses need the module registered
spec.loader.exec_module(live_check)


def test_every_registered_tool_has_exactly_one_probe():
    probes = [p.tool for p in live_check.PROBES]
    assert len(probes) == len(set(probes))
    assert set(probes) == set(ToolRegistry().names())
    assert {p.category for p in live_check.PROBES} <= set(live_check.CATEGORIES)


def test_no_probe_changes_system_settings_or_sends_input():
    import inspect

    source = inspect.getsource(live_check)
    for forbidden in ('"action": "set"', '"action": "off"', '"action": "on"', "type_text", "close_window",
                      '"action": "close"', '"action": "download"'):
        assert forbidden not in source


def test_sandbox_probes_pass_and_every_tool_is_reported(tmp_path):
    rows = live_check.run_checks({"sandbox"}, sandbox=tmp_path / "sb")
    assert {r["tool"] for r in rows} == set(ToolRegistry().names())
    sandbox_rows = [r for r in rows if r["category"] == "sandbox"]
    assert len(sandbox_rows) == 15
    failed = [r for r in sandbox_rows if r["status"] != live_check.PASS]
    assert not failed, failed
    assert all(r["status"] == live_check.SKIP for r in rows if r["category"] != "sandbox")
    # Evidence comes from an independent check, not just the tool's own message.
    assert all(r["evidence"] for r in rows)


def test_missing_probe_is_reported_as_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(live_check, "PROBES", [p for p in live_check.PROBES if p.tool != "list_trash"])
    rows = live_check.run_checks({"sandbox"}, sandbox=tmp_path / "sb")
    row = next(r for r in rows if r["tool"] == "list_trash")
    assert row["status"] == live_check.FAIL and "no live probe" in row["evidence"]


# ------------------------------------------------------------------ --api: control_browser through the running app
def _api(handler):
    import httpx
    return httpx.Client(base_url="http://app", transport=httpx.MockTransport(handler))


def _tab_task(status="completed", passed=True, tabs=3, tool="control_browser"):
    return {"status": status, "actions": [{"tool_name": tool, "status": "succeeded",
                                           "verification": {"passed": passed, "evidence": {"tabs": tabs}}}]}


def test_api_browser_probe_passes_on_a_verified_tab_list():
    import httpx
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get("authorization", "")[:7]))
        if request.url.path == "/browser/status":
            return httpx.Response(200, json={"connected": True, "profiles": 1})
        return httpx.Response(200, json=_tab_task())

    out = live_check._api_browser_inspect("http://app", _api(handler))
    assert out.status == live_check.PASS and "3 tabs" in out.evidence
    assert seen == [("GET", "/browser/status", ""), ("POST", "/tasks", "Bearer ")]


def test_api_browser_probe_is_blocked_honestly_without_or_with_too_many_profiles():
    import httpx
    for profiles, words in ((0, "no browser extension"), (2, "2 browser profiles")):
        out = live_check._api_browser_inspect("http://app", _api(lambda r, n=profiles: httpx.Response(200, json={"connected": False, "profiles": n})))
        assert out.status == live_check.BLOCKED and words in out.evidence


def test_api_browser_probe_is_blocked_when_the_app_is_not_running():
    import httpx

    def down(request):
        raise httpx.ConnectError("refused")

    out = live_check._api_browser_inspect("http://app", _api(down))
    assert out.status == live_check.BLOCKED and "not reachable" in out.evidence


def test_api_browser_probe_fails_when_the_task_or_its_verification_fails():
    import httpx

    def serve(task):
        return lambda r: httpx.Response(200, json={"connected": True, "profiles": 1}) if r.url.path == "/browser/status" else httpx.Response(200, json=task)

    assert live_check._api_browser_inspect("http://app", _api(serve(_tab_task(status="failed")))).status == live_check.FAIL
    assert live_check._api_browser_inspect("http://app", _api(serve(_tab_task(passed=False)))).status == live_check.FAIL
    assert live_check._api_browser_inspect("http://app", _api(serve(_tab_task(tool="open_url")))).status == live_check.FAIL
    assert live_check._api_browser_inspect("http://app", _api(serve(_tab_task(tabs="x")))).status == live_check.FAIL


def test_the_api_option_switches_the_browser_probe_and_the_default_does_not(monkeypatch, tmp_path):
    monkeypatch.setattr(live_check, "_api_browser_inspect", lambda base, client=None: live_check.Outcome(live_check.PASS, f"via {base}"))
    rows = live_check.run_checks({"read"}, {"control_browser"}, tmp_path / "sb", options={"api": "http://x:1"})
    assert rows[0]["status"] == live_check.PASS and rows[0]["evidence"] == "via http://x:1"
    rows = live_check.run_checks({"read"}, {"control_browser"}, tmp_path / "sb2")
    assert "via" not in rows[0]["evidence"]
