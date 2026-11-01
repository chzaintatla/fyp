"""U1 evaluation: does the name matcher pick the right app / file / folder for misheard names, and never act wrongly?

Text level only (what the recogniser hands over), against the fixture index in tests/nlu_eval/u1_cases.py.
Usage: python scripts/eval_u1.py [--holdout] [--verbose] [--out docs/eval/u1_eval.json]
Without --holdout only the DEV cases run (the ones used while tuning).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "nlu_eval"))

from app.nlu.entity_index import Entity  # noqa: E402
from app.nlu.entity_match import resolve  # noqa: E402
import u1_cases  # noqa: E402


def fixture_entries() -> list[Entity]:
    return ([Entity("app", n) for n in u1_cases.APP_NAMES] + [Entity("file", n) for n in u1_cases.FILE_NAMES]
            + [Entity("folder", n) for n in u1_cases.FOLDER_NAMES])


def judge(case: dict, entries: list[Entity]) -> dict:
    kinds = {"app": {"app"}, "file": {"file"}, "folder": {"folder"}, "risky": {"file", "folder"}}[case["kind"]]
    pool = [e for e in entries if e.kind in kinds]
    risk = "high" if case["kind"] == "risky" else "low"
    d = resolve(case["heard"], pool, risk)
    names = [c.name for c in d.candidates]
    exp = case["expected"]
    auto = d.action in ("act", "act_say")
    if exp == "NONE":
        outcome = "wrong_action" if auto else "ok_refused"
    elif exp.startswith("ASK:"):
        want = exp[4:]
        outcome = "wrong_action" if auto and d.chosen.name != want else ("acted_on_ambiguous" if auto else ("ok_asked" if want in names else "miss"))
    elif auto:
        outcome = "correct_auto" if d.chosen.name == exp else "wrong_action"
    else:
        outcome = "asked_with_right" if exp in names else "miss"
    return {**case, "decision": d.action, "chosen": d.chosen.name if d.chosen else None,
            "top": [(c.name, c.score) for c in d.candidates], "outcome": outcome}


def summarise(rows: list[dict]) -> dict:
    def pct(a: int, b: int) -> float:
        return round(100 * a / b, 1) if b else 0.0
    good = {"correct_auto", "asked_with_right", "ok_asked", "ok_refused"}
    named = [r for r in rows if not r["expected"].startswith(("ASK:", "NONE"))]
    mis = [r for r in rows if r["mispronounced"]]
    wrong = [r for r in rows if r["outcome"] in ("wrong_action", "acted_on_ambiguous")]
    return {
        "cases": len(rows), "mispronounced": len(mis),
        "target_ok_pct": pct(sum(r["outcome"] in good for r in rows), len(rows)),
        "target_ok_mispronounced_pct": pct(sum(r["outcome"] in good for r in mis), len(mis)),
        "auto_correct_pct_of_named": pct(sum(r["outcome"] == "correct_auto" for r in named), len(named)),
        "asked_with_right_candidate": sum(r["outcome"] in ("asked_with_right", "ok_asked") for r in rows),
        "misses": sum(r["outcome"] == "miss" for r in rows),
        "wrong_actions": len(wrong),
        "wrong_high_risk_actions": sum(r["kind"] == "risky" for r in wrong),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true", help="also run the HOLDOUT cases (do this once, after tuning)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    entries = fixture_entries()
    result = {}
    for name, lines in (("dev", u1_cases.DEV), ("holdout", u1_cases.HOLDOUT)):
        if name == "holdout" and not args.holdout:
            continue
        rows = [judge(c, entries) for c in u1_cases.parse(lines)]
        result[name] = {"summary": summarise(rows), "rows": rows}
        print(name, json.dumps(result[name]["summary"]))
        for r in rows:
            if r["outcome"] in ("wrong_action", "acted_on_ambiguous", "miss") or args.verbose:
                print(f"  [{r['outcome']}] {r['kind']}: heard {r['heard']!r} want {r['expected']!r} -> {r['decision']} {r['chosen']} {r['top'][:3]}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
