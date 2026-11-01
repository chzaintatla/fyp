"""Fixes for the issues the live voice run (scripts/voice_live.py) found.

V1 trailing "." from Whisper broke the command parsers; V2 memory text kept that "."; V3 Urdu / Hindi replies were silent audio;
V4 Urdu / Hindi memory commands were not understood locally; V5 a "could not read that" note was spoken as "Which one?";
V6 a failure read out environment variable names; V7 "list ..." said only "Done."; V8 answers started with "Right away.".
"""
from __future__ import annotations

import io
import wave

import pytest

from app import conversation
from app.ai.speech_commands import strip_end_punctuation
from app.voice import personas, responses
from app.voice.tts import Clip, TTSChain, VoiceChoice, is_silent


# ------------------------------------------------------------------ V1 / V2
@pytest.mark.parametrize("text,expected", [
    ("Create a folder called Lab in Documents.", "Create a folder called Lab in Documents"),
    ("Show trash. ", "Show trash"), ("what time is it?", "what time is it"), ("کھولو۔", "کھولو"), ("खोलो।", "खोलो"),
    ("open example.com.", "open example.com"), ("open report.docx", "open report.docx"), ("...", "..."), ("", ""),
])
def test_end_punctuation_is_stripped_only_at_the_end(text, expected):
    assert strip_end_punctuation(text) == expected


@pytest.mark.parametrize("command,tool", [
    ("Create a folder called Lab in Documents.", "create_folder"),
    ("Show trash.", "list_trash"), ("Open Chrome.", "open_application"),
])
def test_commands_with_the_period_whisper_adds_are_understood(client, command, tool):
    from app.database import SessionLocal
    from app.models import Task, TaskStatus
    from app.orchestration.local_agent import prepare_local_request
    from app.tools.base import ToolContext
    from app.tools.registry import ToolRegistry

    with SessionLocal() as db:
        task = Task(command=command, status=TaskStatus.CREATED)
        db.add(task)
        db.commit()
        registry = ToolRegistry(ToolContext.from_settings())
        from app.orchestration.executor import _nlu_fallback
        plan = prepare_local_request(db, task, registry) or _nlu_fallback(command, registry)  # the order start_task uses
    assert plan is not None and [a.tool for a in plan.actions] == [tool]


def test_memory_text_does_not_keep_the_transcripts_period():
    plan = conversation.memory_plan("Remember my exam is on 12 December.")
    assert plan.actions[0].args == {"text": "my exam is on 12 December"}
    assert conversation.memory_plan("forget my exam date.").actions[0].args == {"query": "exam date"}


# ------------------------------------------------------------------ V4
@pytest.mark.parametrize("text,tool,args", [
    ("یاد رکھو میرا امتحان بارہ دسمبر کو ہے", "memory_remember", {"text": "میرا امتحان بارہ دسمبر کو ہے"}),
    ("میرا لاکر نمبر دو سو چودہ ہے یاد رکھو", "memory_remember", {"text": "میرا لاکر نمبر دو سو چودہ ہے"}),
    ("याद रखो मेरा एग्ज़ाम बारह दिसंबर को है", "memory_remember", {"text": "मेरा एग्ज़ाम बारह दिसंबर को है"}),
    ("मैं चाय पसंद करता हूँ यह याद रखो", "memory_remember", {"text": "मैं चाय पसंद करता हूँ यह"}),
    ("تمہیں میرے بارے میں کیا یاد ہے", "memory_recall", {}),
    ("میری یادداشتیں دکھاؤ", "memory_recall", {}),
    ("तुम्हें मेरे बारे में क्या याद है", "memory_recall", {}),
    ("मेरी यादें दिखाओ", "memory_recall", {}),
    ("میرا امتحان بھول جاؤ", "memory_forget", {"query": "امتحان"}),
    ("मेरा एग्ज़ाम भूल जाओ", "memory_forget", {"query": "एग्ज़ाम"}),
    ("mera exam bhool jao", "memory_forget", {"query": "exam"}),
    ("bhool jao mera exam", "memory_forget", {"query": "exam"}),
])
def test_urdu_and_hindi_memory_commands_are_understood_locally(text, tool, args):
    plan = conversation.memory_plan(text)
    assert plan is not None and plan.actions[0].tool == tool and plan.actions[0].args == args


@pytest.mark.parametrize("text", ["میرا امتحان کب ہے", "मेरा एग्ज़ाम कब है", "yaad", "भूल", "फ़ाइल खोलो", "ڈیسک ٹاپ پر کیا ہے دکھاؤ"])
def test_other_urdu_and_hindi_text_is_not_taken_for_a_memory_command(text):
    assert conversation.memory_plan(text) is None


# ------------------------------------------------------------------ V3
def _wav(frames: int, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * frames)
    return buffer.getvalue()


def test_only_a_readable_wav_with_almost_no_samples_counts_as_silent():
    assert is_silent(_wav(0), "audio/wav") and len(_wav(0)) == 44
    assert not is_silent(_wav(16000), "audio/wav")
    assert not is_silent(b"not a wav", "audio/wav") and not is_silent(b"\x00" * 10, "audio/mpeg")


def test_a_silent_engine_falls_through_to_the_next_one_without_a_cool_down():
    silent = lambda text, voice: (_wav(0), "audio/wav")  # noqa: E731 - SAPI for Urdu text
    good = lambda text, voice: (_wav(8000), "audio/wav")  # noqa: E731
    chain = TTSChain(orpheus=None, edge=good, sapi=silent)
    assert chain.synthesize("hello", VoiceChoice(language="en")).engine == "edge"
    only_silent = TTSChain(orpheus=None, edge=None, sapi=silent)
    for _ in range(3):  # no cool-down: it is asked every time, and every time the caller learns there was no sound
        with pytest.raises(RuntimeError, match="sapi: silent"):
            only_silent.synthesize("میں نے فولڈر کھول دیا ہے۔", VoiceChoice(language="ur"))


def test_the_voice_api_answers_503_instead_of_200_silence(client):
    from app.voice import api as voice_api

    voice_api.set_chain(TTSChain(orpheus=None, edge=None, sapi=lambda text, voice: (_wav(0), "audio/wav")))
    try:
        r = client.post("/voice/tts", json={"text": "ہو گیا۔", "language": "ur"})
        assert r.status_code == 503  # the page then speaks it with the browser's own voice
    finally:
        voice_api.set_chain(None)


# ------------------------------------------------------------------ V5 / V6 / V7
NOT_READ = "Local agent ko yeh command clear nahi hui. App/file ka naam aur operation batayein. Detailed AI reasoning ke liye 'AI:' ya 'research' se request shuru karein."


@pytest.mark.parametrize("lang", ["en", "roman", "ur", "hi"])
def test_a_long_could_not_read_note_is_spoken_as_not_understood_and_a_candidate_list_as_a_choice(lang):
    spoken = responses.line_for_task({"status": "awaiting_input", "plan_note": NOT_READ, "actions": []}, lang)
    assert spoken == responses.line("not_understood", lang)
    choices = "Kaun sa file?\n1. C:/a/report.docx\n2. C:/b/report.docx\n3. C:/c/report.docx\n4. " + "x" * 80
    assert responses.line_for_task({"status": "awaiting_input", "plan_note": choices, "actions": []}, lang) == responses.line("clarify_generic", lang)


def test_a_failure_caused_by_missing_setup_never_reads_out_variable_names():
    error = "Web answers need a search provider: set WIKIPEDIA_CONTACT_EMAIL and/or store a Tavily key (see docs/SEARCH_SETUP.md)."
    for lang in ("en", "roman", "ur", "hi"):
        spoken = responses.line_for_task({"status": "failed", "error": error, "actions": []}, lang)
        assert spoken == responses.line("failure_setup", lang) and "WIKIPEDIA" not in spoken and "docs" not in spoken
    plain = responses.line_for_task({"status": "failed", "error": "The folder is locked.", "actions": []}, "en")
    assert plain == "I couldn't do that: The folder is locked."


def _verified(tool, output, args=None):
    return {"tool_name": tool, "args": args or {}, "status": "succeeded", "result": {"success": True, "output": output},
            "verification": {"passed": True, "evidence": {"x": 1}}}


def test_listing_commands_say_what_is_there_not_just_done():
    folder = _verified("list_folder", {"entries": [{"name": n, "type": "file"} for n in ("a.txt", "b.pdf", "c.png", "d.csv", "e.md")]})
    task = {"status": "completed", "actions": [folder]}
    assert responses.line_for_task(task, "en") == "Done. There are 5 items: a.txt, b.pdf and c.png, 2 more."
    assert "5 چیزیں" in responses.line_for_task(task, "ur") and "5 चीज़ें" in responses.line_for_task(task, "hi")
    one = {"status": "completed", "actions": [_verified("list_folder", {"entries": [{"name": "only.txt", "type": "file"}]})]}
    assert responses.line_for_task(one, "en") == "Done. There are 1 items: only.txt."
    empty = {"status": "completed", "actions": [_verified("list_folder", {"entries": []})]}
    assert responses.line_for_task(empty, "en") == "Done. It is empty."
    trash = {"status": "completed", "actions": [_verified("list_trash", {"items": [{"trash_id": "1", "original_path": "C:/x/old.txt", "item_type": "file"}]})]}
    assert "old.txt" in responses.line_for_task(trash, "en")


def test_a_failed_listing_is_never_announced_as_done():
    failed = {"tool_name": "list_folder", "status": "failed", "error": "Folder does not exist", "args": {}, "result": {"success": False}}
    spoken = responses.line_for_task({"status": "failed", "actions": [failed]}, "en")
    assert not spoken.startswith("Done") and "Folder does not exist" in spoken


# ------------------------------------------------------------------ V8
def test_the_persona_catchphrase_is_not_sent_to_the_model_as_a_phrase_to_copy():
    prompt = personas.personality_prompt("assistant")
    assert '"Right away."' not in prompt and "Begin with the answer itself" in prompt
    assert "Polite, efficient, butler-like" in prompt  # the tone itself is kept
    assert 'butler-like ("Right away.")' in next(p["tone"] for p in personas.PERSONALITIES if p["id"] == "assistant")  # the UI text is unchanged


# ------------------------------------------------------------------ V9 latency: persistent SAPI voice
@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="Windows SAPI")
def test_the_persistent_sapi_worker_speaks_fast_after_the_first_sentence_and_survives_odd_text(monkeypatch):
    import time

    from app.voice import tts

    monkeypatch.setenv("AGENTDESK_SAPI_WORKER", "1")
    worker = tts.SapiWorker()
    try:
        worker.warm()
        t = time.perf_counter()
        first = worker.speak("Done. It is empty.", "Female")
        warm_ms = (time.perf_counter() - t) * 1000
        assert not tts.is_silent(first, "audio/wav") and warm_ms < 700  # the old one-shot PowerShell needed ~900 ms
        assert not tts.is_silent(worker.speak("Hello again.", "Male"), "audio/wav")
        odd = worker.speak('quotes "x" | pipe $env:PATH `tick` 100%', "Female")  # text is data, never script text
        assert not tts.is_silent(odd, "audio/wav")
        assert tts.is_silent(worker.speak("میں نے فولڈر کھول دیا ہے۔", "Female"), "audio/wav")  # English-only voices: silent, reported as such
    finally:
        worker.close()


def test_the_sapi_engine_falls_back_to_the_one_shot_path_when_the_worker_breaks(monkeypatch):
    from app.voice import tts

    monkeypatch.setenv("AGENTDESK_SAPI_WORKER", "1")
    monkeypatch.setattr(tts.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tts.SAPI_WORKER, "speak", lambda text, gender, timeout=20.0: (_ for _ in ()).throw(RuntimeError("worker died")))
    calls = []
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: calls.append(k["env"]["AGENTDESK_SAPI_TEXT"]) or (_ for _ in ()).throw(OSError("stop here")))
    with pytest.raises(OSError, match="stop here"):
        tts.sapi_engine()("Hello", "female")
    assert calls == ["Hello"]  # the old path ran


# ------------------------------------------------------------------ "Open the YouTube" (screenshot): article + stuck task
@pytest.mark.parametrize("text,expected", [
    ("open the youtube", "open youtube"), ("Open the YouTube.", "Open YouTube"), ("please open the gmail", "please open gmail"),
    ("launch a calculator", "launch calculator"), ("close the notepad", "close notepad"), ("the youtube kholo", "youtube kholo"),
    ("open folder downloads", "open folder downloads"), ("open the", "open the"), ("tell me the time", "tell me the time"),
    ("play the beatles on youtube", "play beatles on youtube"),
])
def test_a_spoken_article_after_the_verb_is_dropped_and_nothing_else(text, expected):
    from app.ai.speech_commands import clean_transcript
    assert clean_transcript(text) == expected


@pytest.mark.parametrize("command,tool,arg", [
    ("Open the YouTube", "open_url", "https://www.youtube.com"), ("open the youtube.", "open_url", "https://www.youtube.com"),
    ("open a youtube", "open_url", "https://www.youtube.com"), ("the youtube kholo", "open_url", "https://www.youtube.com"),
    ("open the gmail", "open_url", "https://mail.google.com"), ("go to the youtube", "open_url", "https://www.youtube.com"),
    ("open facebook", "open_url", "https://www.facebook.com"), ("open the google", "open_url", "https://www.google.com"),
    ("open the notepad", "open_application", "notepad"), ("launch the calculator", "open_application", "calculator"),
])
def test_commands_with_the_before_the_target_open_the_right_thing(command, tool, arg):
    from app.database import SessionLocal
    from app.models import Task, TaskStatus
    from app.orchestration.executor import _nlu_fallback
    from app.orchestration.local_agent import prepare_local_request
    from app.tools.base import ToolContext
    from app.tools.registry import ToolRegistry

    with SessionLocal() as db:
        task = Task(command=command, status=TaskStatus.CREATED)
        db.add(task)
        db.commit()
        registry = ToolRegistry(ToolContext.from_settings())
        plan = prepare_local_request(db, task, registry) or _nlu_fallback(command, registry)
    assert plan is not None
    action = plan.actions[0]
    assert action.tool == tool and arg in action.args.values()


def test_an_app_that_needs_a_choice_ends_as_a_question_not_a_hung_task(client, monkeypatch):
    """open_application answered 'closest apps: GitHub Desktop' -> the task went FAILED then AWAITING_INPUT (illegal): /run was HTTP 500
    and the page showed 'Running...' forever."""
    from app.tools import apps
    from app.tools.base import ToolResult

    monkeypatch.setattr(apps.OpenApplicationTool, "execute", lambda self, args: ToolResult(
        success=False, error="Application 'the youtube' is not an exact unique match. Closest installed apps: GitHub Desktop.",
        output={"candidates": ["GitHub Desktop"], "needs_confirmation": True}))
    task = client.post("/tasks", json={"command": "Open Chrome"}).json()
    ids = [a["id"] for a in task["actions"]]
    if task["status"] == "awaiting_approval":
        assert client.post(f"/tasks/{task['id']}/approve", json={"action_ids": ids}).status_code == 200
    run = client.post(f"/tasks/{task['id']}/run") if task["status"] in ("awaiting_approval", "approved") else client.get(f"/tasks/{task['id']}")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "awaiting_input" and body["error"] is None
    assert body["plan_note"].endswith("Say yes, or the exact name.") and "GitHub Desktop" in body["plan_note"] and len(body["plan_note"]) <= 110
    spoken = responses.line_for_task(body, "en")
    assert "GitHub Desktop" in spoken  # short enough to be read out as the question
