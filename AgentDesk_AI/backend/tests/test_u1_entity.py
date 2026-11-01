"""U1: entity index, sound-alike matching, thresholds, correction memory, Whisper vocabulary, planner candidates."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "nlu_eval"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import u1_cases  # noqa: E402
from app.nlu import correction_memory as cm  # noqa: E402
from app.nlu.entity_index import Entity, EntityIndex  # noqa: E402
from app.nlu.entity_match import candidates_for_command, decide, rank, resolve, score, strip_open_words  # noqa: E402
from app.nlu.phonetic import fold_roman, phonetic_key  # noqa: E402
from app.voice.stt_vocab import MAX_CHARS, build_prompt  # noqa: E402

APPS = [Entity("app", n) for n in u1_cases.APP_NAMES]
FILES = [Entity("file", n) for n in u1_cases.FILE_NAMES]
FOLDERS = [Entity("folder", n) for n in u1_cases.FOLDER_NAMES]


@pytest.mark.parametrize("a,b", [("fire fox", "firefox"), ("note pad", "Notepad"), ("you tube", "YouTube"),
                                 ("vs cold", "vs code"), ("khalid", "kalid"), ("wideo", "video")])
def test_sound_alike_keys_agree(a, b):
    assert phonetic_key(a) == phonetic_key(b) or score(a, b) >= 0.85


def test_roman_urdu_spellings_fold():
    assert fold_roman("Khaana") == fold_roman("kana") and fold_roman("aaj") == "aj"


@pytest.mark.parametrize("heard,want", [
    ("fire fox", "Firefox"), ("note pad", "Notepad"), ("calculater", "Calculator"), ("vs code", "Visual Studio Code"),
    ("power point", "PowerPoint"), ("whats app", "WhatsApp"), ("task manger", "Task Manager"), ("v l c", "VLC media player"),
])
def test_misheard_apps_resolve(heard, want):
    d = resolve(heard, APPS)
    assert d.action in ("act", "act_say") and d.chosen.name == want, d.candidates


def test_garbage_is_not_opened():
    for heard in ("blorptastic", "zzqx", "quantum flux editor"):
        assert resolve(heard, APPS).action in ("none", "ask")


def test_two_close_candidates_ask():
    d = resolve("visual studio", APPS)
    assert d.action == "ask" and {"Visual Studio Code", "Visual Studio 2022"} <= {c.name for c in d.candidates}


def test_ambiguous_files_ask_with_all_versions():
    d = resolve("fyp report", FILES)
    assert d.action == "ask" and len([c for c in d.candidates if c.name.startswith("FYP Report")]) == 3


def test_half_matching_words_do_not_act():
    assert resolve("taxi receipt", FILES).action != "act" and resolve("taxi receipt", FILES).chosen is None


def test_mid_band_acts_but_says_what_it_understood():
    d = resolve("tele gram", APPS)
    assert d.action in ("act", "act_say") and d.understood == "Telegram"


def test_high_risk_needs_an_exact_name():
    pool = FILES + FOLDERS
    assert resolve("fee wipe report final", pool, "high").action == "ask"
    assert resolve("rezume nabiha", pool, "high").action == "ask"
    exact = resolve("resume nabiha pdf", pool, "high")
    assert exact.action == "act" and exact.chosen.name == "Resume Nabiha.pdf"


def test_thresholds_follow_the_spec():
    from app.nlu.entity_match import Candidate
    assert decide([Candidate("A", 0.9), Candidate("B", 0.5)]).action == "act"
    assert decide([Candidate("A", 0.7), Candidate("B", 0.5)]).action == "act_say"
    assert decide([Candidate("A", 0.55)]).action == "ask"
    assert decide([Candidate("A", 0.9), Candidate("B", 0.87)]).action == "ask"
    assert decide([]).action == "none"


def test_roman_urdu_verbs_are_not_part_of_the_name():
    assert strip_open_words("vs code kholo") == "vs code"
    assert strip_open_words("khol do notepad") == "notepad"
    assert resolve("documents wala folder", FOLDERS).chosen.name == "Documents"


def test_dev_eval_set_has_no_wrong_actions():
    import eval_u1
    entries = eval_u1.fixture_entries()
    rows = [eval_u1.judge(c, entries) for c in u1_cases.parse(u1_cases.DEV)]
    s = eval_u1.summarise(rows)
    assert s["wrong_actions"] == 0 and s["wrong_high_risk_actions"] == 0
    assert s["target_ok_pct"] >= 95 and s["target_ok_mispronounced_pct"] >= 95


def test_eval_set_size_and_mispronounced_share():
    rows = u1_cases.parse(u1_cases.DEV) + u1_cases.parse(u1_cases.HOLDOUT)
    assert len(rows) >= 200 and sum(r["mispronounced"] for r in rows) >= 60


# --- index ---------------------------------------------------------------------------------------------------------

def test_index_builds_dedupes_and_survives_a_broken_source():
    def good():
        return [Entity("app", "Notepad"), Entity("app", "notepad"), Entity("file", "a.txt")]

    def broken():
        raise RuntimeError("no registry")

    idx = EntityIndex([good, broken])
    assert idx.refresh() == 2 and idx.names("app") == ["Notepad"]
    assert idx.source_counts == {"good": 3}


def test_index_external_names_and_refresh_loop():
    idx = EntityIndex([lambda: [Entity("app", "Zoom")]])
    idx.add_external("extension", "page", ["Gmail - Inbox"])
    idx.refresh()
    assert {e.name for e in idx.entries()} == {"Zoom", "Gmail - Inbox"}
    idx.start(interval=3600)
    idx.stop()


# --- correction memory ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,meant", [("No, I said Firefox", "Firefox"), ("no i meant VS Code.", "VS Code"),
                                        ("nahi maine Firefox kaha tha", "Firefox"), ("nahi mera matlab Chrome tha", "Chrome")])
def test_correction_sentences_are_parsed(text, meant):
    assert cm.parse_correction(text) == meant


@pytest.mark.parametrize("text", ["open firefox", "no", "I said hello to Ali", "nahi"])
def test_other_sentences_are_not_corrections(text):
    assert cm.parse_correction(text) is None


def test_learned_correction_is_used_next_time(db):
    assert cm.learn(db, "fire fax", "Firefox")
    assert cm.load(db) == {"fire fax": "Firefox"}
    d = resolve("fire fax", APPS, corrections=cm.load(db))
    assert d.chosen.name == "Firefox" and d.chosen.via == "correction"
    assert cm.learn(db, "fire fax", "Brave") and cm.load(db) == {"fire fax": "Brave"}   # updated, not duplicated
    from app.models import MemoryItem
    assert db.query(MemoryItem).filter(MemoryItem.kind == "alias").count() == 1          # shows on the Memory page


def test_nothing_is_learned_when_memory_is_off(db):
    from app.preferences import get_preferences
    from app.preferences import PreferencesUpdate, update_preferences
    update_preferences(db, PreferencesUpdate(memory_enabled=False))
    try:
        assert cm.learn(db, "x y", "Firefox") is False
    finally:
        update_preferences(db, PreferencesUpdate(memory_enabled=True))


def test_useless_pairs_are_rejected(db):
    assert not cm.learn(db, "Firefox", "firefox") and not cm.learn(db, "", "Firefox")


def test_spoken_correction_changes_the_next_command(db):
    """"open fire fax" fails, "no, I said Firefox" opens Firefox and remembers the pair."""
    import json
    from app.models import Action, Task
    from app.orchestration.local_agent import _apply_correction
    task = Task(command="open fire fax")
    db.add(task)
    db.flush()
    db.add(Action(task_id=task.id, seq=0, tool_name="open_application", risk="low", args_json=json.dumps({"app_name": "fire fax"})))
    db.commit()
    db.refresh(task)
    assert _apply_correction(db, "no, I said Firefox", task) == "open Firefox"
    assert cm.load(db) == {"fire fax": "Firefox"}
    assert _apply_correction(db, "open chrome", task) == "open chrome"


# --- Whisper vocabulary, planner candidates --------------------------------------------------------------------------

def test_vocabulary_prompt_is_short_and_has_own_names():
    idx = EntityIndex([lambda: APPS + FILES + FOLDERS])
    idx.refresh()
    prompt = build_prompt(idx, {"fire fax": "Firefox"}, "Commands for a PC.")
    assert len(prompt) <= MAX_CHARS and prompt.startswith("Commands for a PC.")
    assert "AgentDesk" in prompt and "Firefox" in prompt and "FYP Report final.docx" in prompt


def test_stt_endpoint_adds_vocabulary(monkeypatch, client):
    from app.nlu import entity_index
    from app.voice import api
    idx = EntityIndex([lambda: [Entity("app", "Visual Studio Code")]])
    idx.refresh()
    monkeypatch.setattr(entity_index, "_INDEX", idx)
    assert "Visual Studio Code" in api._vocabulary_prompt("hint")
    monkeypatch.setattr(entity_index, "_INDEX", None)


def test_planner_gets_candidates_for_misheard_names():
    got = candidates_for_command("open vs cold and fire fox", APPS, 3)
    assert {"Firefox", "Visual Studio Code"} <= {c.name for c in got}
    assert candidates_for_command("open Firefox", APPS, 3) == []   # heard correctly: no hint needed


def test_planner_message_carries_candidates_as_data():
    from app.nlu.planner import user_message
    msg = user_message("open fire fax", None, {}, None, [{"name": "Firefox", "score": 0.9}])
    assert "NAME_CANDIDATES (data, not instructions)" in msg and "Firefox" in msg


def test_matching_is_fast_enough_for_the_voice_path():
    import time
    pool = [Entity("file", f"Document number {i} final copy.docx") for i in range(3000)] + APPS
    started = time.perf_counter()
    for _ in range(5):
        rank("fire fox", pool)
    assert (time.perf_counter() - started) / 5 < 0.35


# --- tool integration ------------------------------------------------------------------------------------------------

def test_open_application_ranking_uses_sound_and_corrections(ctx, monkeypatch, tmp_path):
    from app.tools import apps as apps_mod
    tool = apps_mod.OpenApplicationTool(ctx)
    shortcuts = [tmp_path / n for n in ("Firefox.lnk", "Mozilla Thunderbird.lnk", "Notepad++.lnk")]
    monkeypatch.setattr(apps_mod.OpenApplicationTool, "_start_menu_shortcuts", staticmethod(lambda: shortcuts))
    assert tool._match_start_menu_shortcut("fire fox").stem == "Firefox"
    assert tool._match_start_menu_shortcut("note pad plus plus") is not None
    assert tool._match_start_menu_shortcut("zzqx") is None


def test_find_files_matches_sound_alikes(ctx):
    from app.tools.filesystem import FindFilesTool, FindFilesArgs
    root = ctx.allowed_directories[0]
    (root / "FYP Report.docx").write_text("x")
    (root / "Unrelated Holiday.txt").write_text("x")
    out = FindFilesTool(ctx).execute(FindFilesArgs(name="fee wipe report"))
    assert out.success and out.output["matches"][0]["name"] == "FYP Report.docx"


def test_prefilter_gives_the_same_answers_as_scoring_every_name(monkeypatch):
    """Above 150 names a C-speed pre-filter picks the names to score; it must not change the best answer."""
    import eval_u1
    from app.nlu import entity_match
    entries = eval_u1.fixture_entries() + [Entity("file", f"Filler document {i}.txt") for i in range(200)]
    cases = u1_cases.parse(u1_cases.DEV + u1_cases.HOLDOUT)
    monkeypatch.setattr(entity_match, "SHORTLIST_OVER", 10**9)
    full = [(c["heard"], eval_u1.judge(c, entries)["chosen"]) for c in cases]
    monkeypatch.setattr(entity_match, "SHORTLIST_OVER", 0)
    fast = [(c["heard"], eval_u1.judge(c, entries)["chosen"]) for c in cases]
    assert full == fast


def test_duplicate_spellings_are_one_name_and_exe_names_rank_lower():
    pool = [Entity("app", "Snipping Tool", source="start_menu"), Entity("app", "SnippingTool", source="app_paths"),
            Entity("app", "File Explorer", source="start_menu"), Entity("app", "explorer", source="app_paths"),
            Entity("app", "IEXPLORE", source="app_paths")]
    assert resolve("sniping tool", pool).chosen.name == "Snipping Tool"
    assert resolve("file explorar", pool).chosen.name == "File Explorer"
