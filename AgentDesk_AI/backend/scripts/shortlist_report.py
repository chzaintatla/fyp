"""Step 1g: does the tool shortlist keep the tool the plan needs, and how many tokens does the planner request cost now?

Offline (no model, no network). Recall = the gold plan's tools are all inside the shortlist. Reported for the dev split (looked at
while writing the word lists) and the holdout split (not used for that), plus the conversation phrasings of Step 1e.
Writes ../docs/eval/1g_shortlist.json.   Run from backend/:  python scripts/shortlist_report.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tiktoken  # noqa: E402

from app.nlu.planner import PlannerContext, build_messages, response_format, shortlist_for  # noqa: E402
from app.tools.base import ToolContext  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402
from tests.routing_phrasings import PHRASINGS  # noqa: E402

LIMIT_TOKENS = 8000  # Groq free tier, per minute, per organization
ENC = tiktoken.get_encoding("o200k_base")


def tokens(registry, command: str, tools: list[str] | None) -> int:
    ctx = PlannerContext(command=command, registry=registry, tools=tools)
    messages = build_messages(ctx)
    return sum(len(ENC.encode(m["content"])) for m in messages) + len(ENC.encode(json.dumps(response_format(registry, ctx.tools or shortlist_for(ctx)))))


def context_for(item: dict, registry) -> PlannerContext:
    """The planner context for a gold item, including its earlier turns (follow-ups like "a bit more")."""
    from app.nlu.memory import ConversationMemory, Turn

    turns = [Turn(**{k: v for k, v in t.items() if k in Turn.__dataclass_fields__}) for t in ((item.get("context") or {}).get("turns") or [])]
    return PlannerContext(command=item["command"], registry=registry, memory=ConversationMemory(turns))


def main() -> None:
    registry = ToolRegistry(ToolContext.from_settings())
    names = registry.names()
    rows = [json.loads(line) for line in (ROOT / "tests" / "nlu_eval" / "commands.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    out: dict = {"registry_tools": len(names), "limit_tokens_per_minute": LIMIT_TOKENS}
    stats = defaultdict(lambda: {"n": 0, "hit": 0, "misses": []})
    sizes, counts = [], []
    for r in rows:
        need = {s["tool"] for s in r["expected"]["steps"]}
        chosen = shortlist_for(context_for(r, registry))
        counts.append(len(chosen))
        sizes.append(tokens(registry, r["command"], None))
        if not need:
            continue
        for key in (r["split"], "all", f"{r['lang']}"):
            s = stats[key]
            s["n"] += 1
            if need <= set(chosen):
                s["hit"] += 1
            elif key == "holdout":
                s["misses"].append({"id": r["id"], "missing": sorted(need - set(chosen))})
    conv = {"n": 0, "hit": 0, "misses": []}
    expect = {"memory_remember": "memory_remember", "memory_recall": "memory_recall", "memory_forget": "memory_forget",
              "summarise": "summarise_page", "web_answer": "web_answer"}
    for intent, langs in PHRASINGS.items():
        if intent not in expect:
            continue
        for texts in langs.values():
            for t in texts:
                conv["n"] += 1
                chosen = shortlist_for(PlannerContext(command=t, registry=registry))
                ok = expect[intent] in chosen or (intent == "summarise" and "summarise_file" in chosen)
                conv["hit"] += ok
                if not ok:
                    conv["misses"].append(intent)
    full = tokens(registry, "turn the volume up", names)
    sizes.sort()
    out.update({
        "recall": {k: {"n": v["n"], "hit": v["hit"], "rate": round(v["hit"] / v["n"], 4), **({"holdout_misses": v["misses"]} if k == "holdout" else {})} for k, v in stats.items()},
        "conversation_phrasings_recall": {"n": conv["n"], "hit": conv["hit"], "rate": round(conv["hit"] / conv["n"], 4)},
        "shortlist_size": {"mean": round(sum(counts) / len(counts), 1), "max": max(counts)},
        "request_tokens_full_catalogue": full,
        "request_tokens_shortlist": {"median": sizes[len(sizes) // 2], "p95": sizes[int(len(sizes) * 0.95) - 1], "max": sizes[-1]},
        "note": "request tokens = prompt + examples + json schema (o200k_base, the gpt-oss tokenizer family), excluding the 900-token answer budget",
    })
    target = ROOT.parent / "docs" / "eval" / "1g_shortlist.json"
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
