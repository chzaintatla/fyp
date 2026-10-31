#!/usr/bin/env python3
"""Store the Tavily API key in Windows Credential Manager (service 'AgentDesk-search', user 'tavily').

    python scripts/set_search_key.py            # asks for the key with a hidden prompt, checks it with one tiny search
    python scripts/set_search_key.py --remove   # deletes the stored key

The key is never printed, never written to a file in the project, and not passed on the command line.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyring  # noqa: E402

from app.search.provider import SearchBusy, SearchNotConfigured  # noqa: E402
from app.search.tavily import KEY_SERVICE, KEY_USER, TavilyProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if args.remove:
        try:
            keyring.delete_password(KEY_SERVICE, KEY_USER)
            print("Removed the stored Tavily key.")
        except keyring.errors.PasswordDeleteError:
            print("No Tavily key was stored.")
        return 0
    key = getpass.getpass("Paste your Tavily API key (nothing is shown): ").strip()
    if not key:
        print("No key entered; nothing stored.")
        return 1
    try:
        TavilyProvider(key=key).search("what is the capital of france", 1)
    except SearchNotConfigured:
        print("Tavily refused this key. Nothing was stored.")
        return 1
    except SearchBusy:
        print("The key was accepted but Tavily is busy right now; storing it anyway.")
    except Exception as exc:  # noqa: BLE001 - network problems should not lose a good key
        print(f"Could not verify the key ({type(exc).__name__}); storing it anyway.")
    keyring.set_password(KEY_SERVICE, KEY_USER, key)
    print("Stored in Windows Credential Manager as AgentDesk-search / tavily.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
