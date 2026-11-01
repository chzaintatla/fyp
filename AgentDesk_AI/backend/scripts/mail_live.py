#!/usr/bin/env python3
"""U6 live check against a REAL mailbox: sends one test e-mail and looks for it in the Sent folder. Use a TEST mailbox only.

    1. Set the OAuth client id in backend\\.env (docs/EMAIL_SETUP.md), start the app, Settings > Email > Connect (sign in on the provider's page).
    2. python scripts/mail_live.py --provider gmail --to your.test.mailbox@gmail.com --i-am-using-a-test-mailbox

It goes through the same code as the assistant: a draft, the confirmation that normally follows the spoken read-back (issued here by the
script, because there is nobody to speak to), `service.send`, then a look in Sent. The subject starts with "[AgentDesk U6 test]". It refuses to run
without --to and the flag, and it never sends to more than that one address. Optional: --search NAME also shows what the contact search returns.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.mail import draft as drafts  # noqa: E402
from app.mail import oauth, service  # noqa: E402
from app.mail.models import MailError, mask_email, valid_email  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["gmail", "outlook"], required=True)
    ap.add_argument("--to", required=True, help="the TEST mailbox that receives the message")
    ap.add_argument("--i-am-using-a-test-mailbox", action="store_true")
    ap.add_argument("--search", help="also look this name up in the contacts")
    args = ap.parse_args()
    if not args.i_am_using_a_test_mailbox:
        print("Refusing to run: add --i-am-using-a-test-mailbox (this sends a real e-mail).")
        return 2
    if not valid_email(args.to):
        print("--to must be a valid e-mail address.")
        return 2
    settings = get_settings()
    print(f"sign-in: {oauth.status(args.provider)}")
    try:
        provider = service.provider_for(args.provider, settings)
        if args.search:
            found = provider.search_contacts(args.search)
            print(f"contacts for {args.search!r}: {[(c.name, mask_email(c.email)) for c in found]}")
        draft = drafts.new_draft(args.provider)
        draft.to = [{"name": "", "email": args.to}]
        draft.set_subject(f"[AgentDesk U6 test] {time.strftime('%Y-%m-%d %H:%M:%S')}")
        draft.add_text("This is a test message from the AgentDesk assistant.")
        draft.add_text("If you can read this, sending works.")
        token = draft.issue_confirmation()
        print(f"read-back would be: {draft.readback()}")
        started = time.time()
        result = service.send(draft, token, settings)
        print(f"sent in {time.time() - started:.1f}s, message id: {result.message_id}")
        evidence = service.find_in_sent(draft, result, settings)
    except (MailError, oauth.NotConnected) as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"Sent folder check: found={evidence.found} folder={evidence.folder} to={[mask_email(x) for x in evidence.to]} subject={evidence.subject!r}")
    print("PASS" if evidence.found else "FAIL: it is not in the Sent folder")
    return 0 if evidence.found else 1


if __name__ == "__main__":
    raise SystemExit(main())
