"""M2 voice: sentence chunking, spoken lines, TTS fallback chain, latency log, /voice endpoints."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.voice import latency, responses
from app.voice import api as voice_api
from app.voice.chunking import ORPHEUS_LIMIT, split_for_speech
from app.voice.tts import TTSChain, VoiceChoice


# ------------------------------------------------------------------ chunking
def test_short_text_is_one_chunk():
    assert split_for_speech("Done. Chrome is open.") == ["Done.", "Chrome is open."]


def test_every_chunk_fits_and_text_is_preserved():
    text = ("This is a long sentence, with several clauses, that keeps going " * 8).strip() + ". Short one."
    chunks = split_for_speech(text)
    assert all(0 < len(c) <= ORPHEUS_LIMIT for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_unbroken_string_is_hard_wrapped():
    chunks = split_for_speech("x" * 450)
    assert [len(c) for c in chunks] == [200, 200, 50]


def test_urdu_sentence_marks_split():
    assert split_for_speech("ہو گیا۔ کروم کھل گیا ہے۔") == ["ہو گیا۔", "کروم کھل گیا ہے۔"]


def test_empty_text_gives_no_chunks():
    assert split_for_speech("  ") == []


def test_direction_tag_is_budgeted_into_the_limit():
    chain = TTSChain()
    choice = VoiceChoice(direction="warm")
    assert all(len(c) + len("[warm] ") <= ORPHEUS_LIMIT for c in chain.chunks("word " * 120, choice))


# ------------------------------------------------------------------ spoken lines
def _action(tool="open_application", args=None, status="succeeded", passed=True, error=None):
    return {"tool_name": tool, "args": args or {"app_name": "chrome"}, "status": status, "error": error,
            "verification": {"passed": passed, "details": None if passed else "window not found"}}


def test_success_requires_verification():
    ok = responses.line_for_task({"status": "completed", "actions": [_action()]})
    assert ok == "Done. Chrome is open."
    bad = responses.line_for_task({"status": "completed", "actions": [_action(passed=False)]})
    assert not bad.startswith("Done")


def test_completed_task_with_one_unverified_action_never_says_done():
    task = {"status": "completed", "actions": [_action(), _action("open_url", {"url": "https://a.b"}, passed=False)]}
    assert "Done" not in responses.line_for_task(task)


@pytest.mark.parametrize("language,expected", [("en", "Done. Chrome is open."), ("roman", "Ho gaya. Chrome khul gaya hai.")])
def test_success_lines_by_language(language, expected):
    assert responses.line_for_task({"status": "completed", "actions": [_action()]}, language) == expected


def test_failure_gives_the_reason():
    task = {"status": "failed", "error": "The file Report.docx doesn't exist. More text.", "actions": []}
    assert responses.line_for_task(task) == "I couldn't do that: The file Report.docx doesn't exist."


def test_approval_and_cancel_lines():
    assert "confirm on screen" in responses.line_for_task({"status": "awaiting_approval", "actions": [_action(status="pending")]})
    assert responses.line_for_task({"status": "rejected", "actions": []}) == "Okay, cancelled. Nothing was changed."
    assert responses.line("cancelled", "roman") == "Theek hai, cancel kar diya."


def test_multi_step_success_is_one_summary():
    task = {"status": "completed", "actions": [_action(), _action("create_folder", {"path": "x"})]}
    assert responses.line_for_task(task) == "Done. All 2 steps are finished."


def test_acknowledge_uses_the_tool_and_falls_back():
    assert responses.acknowledge_line("open_application", {"app_name": "chrome"}) == "Opening Chrome."
    assert responses.acknowledge_line("open_application", {"app_name": "chrome"}, "roman") == "Chrome khol raha hoon."
    assert responses.acknowledge_line("some_new_tool", {}) == "On it."


def test_every_event_exists_in_every_language_and_is_short():
    for event, table in responses._T.items():
        assert set(table) == set(responses.LANGUAGES), event
    for lang in responses.LANGUAGES:
        assert len(responses.line("wake", lang).split(". ")) <= 2


def test_unknown_personality_and_language_do_not_break():
    assert responses.line("wake", "fr", "pirate") == "Yes, I'm listening."


# ------------------------------------------------------------------ TTS chain
class Boom(Exception):
    pass


def _engine(name, calls, fail=False):
    def run(text, voice):
        calls.append((name, text, voice))
        if fail:
            raise Boom(name)
        return name.encode(), "audio/wav"
    return run


def test_english_uses_orpheus_first():
    calls = []
    chain = TTSChain(orpheus=_engine("orpheus", calls), edge=_engine("edge", calls), sapi=_engine("sapi", calls))
    clip = chain.synthesize("Hello.", VoiceChoice(voice_en="daniel"))
    assert (clip.engine, clip.voice) == ("orpheus", "daniel") and len(calls) == 1


def test_orpheus_failure_falls_back_and_is_skipped_afterwards():
    calls = []
    now = [0.0]
    chain = TTSChain(orpheus=_engine("orpheus", calls, fail=True), edge=_engine("edge", calls),
                     sapi=_engine("sapi", calls), clock=lambda: now[0])
    assert chain.synthesize("One.").engine == "edge"
    assert chain.synthesize("Two.").engine == "edge"
    assert [c[0] for c in calls].count("orpheus") == 1  # not retried for each sentence
    now[0] += 301
    chain.synthesize("Three.")
    assert [c[0] for c in calls].count("orpheus") == 2  # retried after the cooldown


def test_urdu_and_hindi_use_edge_voices_for_the_gender():
    calls = []
    chain = TTSChain(orpheus=_engine("orpheus", calls), edge=_engine("edge", calls), sapi=_engine("sapi", calls))
    assert chain.synthesize("x", VoiceChoice(language="ur", gender="male")).voice == "ur-PK-AsadNeural"
    assert chain.synthesize("x", VoiceChoice(language="hi", gender="female")).voice == "hi-IN-SwaraNeural"
    assert not any(c[0] == "orpheus" for c in calls)


def test_direction_is_prefixed_only_for_orpheus():
    calls = []
    chain = TTSChain(orpheus=_engine("orpheus", calls), edge=_engine("edge", calls))
    chain.synthesize("Hi.", VoiceChoice(direction="warm"))
    assert calls[0][1] == "[warm] Hi."


def test_everything_failing_raises_with_engine_names():
    chain = TTSChain(orpheus=_engine("orpheus", [], True), edge=_engine("edge", [], True), sapi=_engine("sapi", [], True))
    with pytest.raises(RuntimeError, match="orpheus.*edge.*sapi"):
        chain.synthesize("Hi.")


def test_sapi_script_never_contains_the_spoken_text():
    from app.voice import tts
    assert "AGENTDESK_SAPI_TEXT" in tts._SAPI_SCRIPT and "{text}" not in tts._SAPI_SCRIPT


# ------------------------------------------------------------------ latency log
def test_latency_durations_and_summary(tmp_path):
    for i, first_word in enumerate([1.0, 2.0, 3.0]):
        u = latency.Utterance("local", tmp_path / "l.jsonl")
        u.mark("speech_end", 10.0)
        u.mark("first_word_played", 10.0 + first_word)
        u.finish()
    summary = latency.summarize(tmp_path / "l.jsonl")
    assert summary["runs"] == 3 and summary["p50_ms"] == 2000 and summary["under_2500ms"] == 2
    row = json.loads((tmp_path / "l.jsonl").read_text().splitlines()[0])
    assert set(row) == {"ts", "kind", "ms"}  # no transcript text


def test_summary_of_missing_log_is_empty(tmp_path):
    assert latency.summarize(tmp_path / "none.jsonl")["runs"] == 0


# ------------------------------------------------------------------ endpoints
@pytest.fixture()
def client():
    calls = []
    voice_api.set_chain(TTSChain(orpheus=_engine("orpheus", calls), edge=_engine("edge", calls)))
    yield TestClient(app, base_url="http://127.0.0.1:8000")
    voice_api.set_chain(None)


def test_tts_endpoint_returns_audio_and_engine(client):
    r = client.post("/voice/tts", json={"text": "Done. Chrome is open.", "voice_en": "troy", "gender": "male"})
    assert r.status_code == 200 and r.content == b"orpheus"
    assert r.headers["x-tts-engine"] == "orpheus" and r.headers["x-tts-voice"] == "troy"


def test_tts_rejects_text_over_200_chars(client):
    assert client.post("/voice/tts", json={"text": "a" * 201}).status_code == 422


def test_tts_503_when_all_engines_fail():
    voice_api.set_chain(TTSChain(orpheus=_engine("o", [], True)))
    try:
        assert TestClient(app, base_url="http://127.0.0.1:8000").post("/voice/tts", json={"text": "Hi."}).status_code == 503
    finally:
        voice_api.set_chain(None)


def test_chunks_endpoint(client):
    r = client.post("/voice/chunks", json={"text": "One. Two."})
    assert r.json()["chunks"] == ["One.", "Two."]


def test_line_endpoint_for_task_and_tool(client):
    r = client.post("/voice/line", json={"task": {"status": "completed", "actions": [_action()]}})
    assert r.json()["text"] == "Done. Chrome is open."
    r = client.post("/voice/line", json={"tool": "open_application", "args": {"app_name": "notepad"}, "language": "roman"})
    assert r.json()["text"] == "Notepad khol raha hoon."
    assert client.post("/voice/line", json={"event": "nope"}).status_code == 422


def test_stt_without_any_service_is_503(client):
    r = client.post("/voice/stt", files={"audio": ("s.wav", b"RIFFxxxx", "audio/wav")})
    assert r.status_code == 503


def test_stt_rejects_empty_audio(client):
    assert client.post("/voice/stt", files={"audio": ("s.wav", b"", "audio/wav")}).status_code == 422


def test_latency_report_accepts_only_known_stages(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTDESK_LATENCY_LOG", str(tmp_path / "v.jsonl"))
    assert client.post("/voice/latency", json={"kind": "local", "ms": {"first_word_played": 1800, "evil": 5}}).status_code == 204
    row = json.loads((tmp_path / "v.jsonl").read_text())
    assert row["ms"] == {"first_word_played": 1800.0}
    assert client.get("/voice/latency").json()["first_word"]["runs"] == 1


def test_real_task_shape_from_the_api_is_recognised():
    """A task exactly as /tasks returns it for a verified action (ActionStatus.SUCCEEDED)."""
    from app.models import ActionStatus
    action = {"id": "a", "seq": 1, "tool_name": "open_application", "args": {"app_name": "chrome"}, "risk": "low",
              "status": ActionStatus.SUCCEEDED, "result": {"pid": 1}, "verification": {"passed": True, "checks": ["x"], "evidence": {"a": 1}}}
    assert responses.line_for_task({"status": "completed", "actions": [action]}) == "Done. Chrome is open."


def test_long_clarification_note_is_not_read_aloud():
    task = {"status": "awaiting_input", "plan_note": "x " * 200, "actions": []}
    assert responses.line_for_task(task) == "Which one do you mean? See the choices on screen."
    short = {"status": "awaiting_input", "plan_note": "Which Report file, Documents or Desktop?", "actions": []}
    assert responses.line_for_task(short) == "Which Report file, Documents or Desktop?"


def test_waiting_retry_is_a_slow_line_not_a_failure():
    assert "slower" in responses.line_for_task({"status": "waiting_retry", "actions": []})


def test_repeated_phrase_is_served_from_cache_without_resynthesis():
    calls = []
    chain = TTSChain(orpheus=_engine("orpheus", calls), cache=True)
    first = chain.synthesize("Done. Chrome is open.", VoiceChoice(voice_en="diana"))
    second = chain.synthesize("Done. Chrome is open.", VoiceChoice(voice_en="diana"))
    assert first is second and len(calls) == 1


def test_cache_is_per_voice_and_can_be_disabled():
    calls = []
    chain = TTSChain(orpheus=_engine("orpheus", calls), cache=True)
    chain.synthesize("Hi.", VoiceChoice(voice_en="diana"))
    chain.synthesize("Hi.", VoiceChoice(voice_en="troy"))
    assert len(calls) == 2
    off = TTSChain(orpheus=_engine("orpheus", calls), cache=False)
    off.synthesize("Hi.")
    off.synthesize("Hi.")
    assert len(calls) == 4


def test_cache_is_bounded():
    from app.voice import tts
    chain = TTSChain(orpheus=_engine("orpheus", []), cache=True)
    for i in range(tts.CACHE_SIZE + 10):
        chain.synthesize(f"Line {i}.")
    assert len(chain._clips) == tts.CACHE_SIZE
