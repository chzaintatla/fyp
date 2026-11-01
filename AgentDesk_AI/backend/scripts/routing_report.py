"""Step 1e measurement: how many of the 360 routing phrasings the regex fast path decides, and whether it is right.

Run from backend/:  python scripts/routing_report.py   -> writes docs/eval/m5_routing_fastpath.json
No network, no model. The planner's own accuracy (real LLM labelling intents) is NOT measured here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.routing_phrasings import PHRASINGS  # noqa: E402
from tests.test_m5_routing import MEMORY_TOOL, fast_route  # noqa: E402


def main() -> None:
    report: dict = {"note": "regex fast path only; misses go to the planner's intent. Phrasings were not tuned to the regex, "
                            "but the fast path was changed after a first run found misroutes, so this is not a blind test.",
                    "per_intent": {}}
    totals = {"phrasings": 0, "fast_path_decided": 0, "fast_path_wrong": 0}
    for intent, langs in PHRASINGS.items():
        expected = intent if intent in ("summarise", "web_answer", "chat") else MEMORY_TOOL[intent]
        row: dict = {}
        for lang, texts in langs.items():
            got = [fast_route(t) for t in texts]
            decided = sum(g is not None for g in got)
            wrong = sum(g is not None and g != expected for g in got)
            row[lang] = {"phrasings": len(texts), "fast_path_decided": decided, "fast_path_wrong": wrong}
            totals["phrasings"] += len(texts)
            totals["fast_path_decided"] += decided
            totals["fast_path_wrong"] += wrong
        report["per_intent"][intent] = row
    report["totals"] = totals
    out = ROOT.parent / "docs" / "eval" / "m5_routing_fastpath.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for intent, row in report["per_intent"].items():
        print(f"{intent:16}", "  ".join(f"{lang}:{v['fast_path_decided']}/{v['phrasings']}" for lang, v in row.items()))
    print(totals)


if __name__ == "__main__":
    main()
