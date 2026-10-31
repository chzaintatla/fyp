#!/usr/bin/env python3
"""Call each real speech engine once and report PASS/FAIL (exit 1 if SAPI, the last resort, fails).

    python scripts/tts_probe.py [--out DIR]   # DIR receives one audio file per passing engine to listen to
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.voice.api import _speech_client  # noqa: E402
from app.voice.tts import edge_tts_engine, groq_orpheus_engine, sapi_engine  # noqa: E402

CASES = [
    ("orpheus female diana", "orpheus", "diana", "[professionally] Done. Chrome is open."),
    ("orpheus male daniel", "orpheus", "daniel", "[professionally] Done. Chrome is open."),
    ("edge urdu female", "edge", "ur-PK-UzmaNeural", "ہو گیا۔ کروم کھل گیا ہے۔"),
    ("edge urdu male", "edge", "ur-PK-AsadNeural", "ہو گیا۔ کروم کھل گیا ہے۔"),
    ("edge hindi female", "edge", "hi-IN-SwaraNeural", "हो गया। क्रोम खुल गया है।"),
    ("edge hindi male", "edge", "hi-IN-MadhurNeural", "हो गया। क्रोम खुल गया है।"),
    ("sapi female", "sapi", "female", "Done. Chrome is open."),
    ("sapi male", "sapi", "male", "Done. Chrome is open."),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    get_settings()
    client = _speech_client()
    engines = {"orpheus": groq_orpheus_engine(client) if client is not None else None,
               "edge": edge_tts_engine(), "sapi": sapi_engine()}
    sapi_ok = True
    for label, name, voice, text in CASES:
        engine = engines[name]
        started = time.perf_counter()
        try:
            if engine is None:
                raise RuntimeError("no speech client configured")
            audio, mime = engine(text, voice)
            ms = (time.perf_counter() - started) * 1000
            print(f"PASS  {label:24s} {len(audio):8d} bytes  {ms:6.0f} ms")
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / f"{label.replace(' ', '_')}.{'mp3' if 'mpeg' in mime else 'wav'}").write_bytes(audio)
        except Exception as exc:  # noqa: BLE001 - report every engine
            print(f"FAIL  {label:24s} {type(exc).__name__}: {str(exc)[:110]}")
            sapi_ok = sapi_ok and name != "sapi"
    return 0 if sapi_ok else 1


if __name__ == "__main__":
    sys.exit(main())
