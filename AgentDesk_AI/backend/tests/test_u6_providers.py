"""U6: the Gmail and Outlook REST calls and the OAuth round trip, against mock HTTP transports (no network, no real account)."""
from __future__ import annotations

import base64
import email
import json
import time
from email import policy
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.mail import oauth
from app.mail.models import Attachment, Contact, MailError, NotConnected, SendResult
from app.mail.providers import GmailProvider, OutlookProvider, make_provider


class Settings:
    google_oauth_client_id = "gid.apps.googleusercontent.com"
    google_oauth_client_secret = "gsecret"
    ms_oauth_client_id = "ms-client-id"


class DictKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, service, user):
        return self.data.get((service, user))

    def set_password(self, service, user, value):
        self.data[(service, user)] = value

    def delete_password(self, service, user):
        if (service, user) not in self.data:
            raise KeyError("no such password")
        del self.data[(service, user)]


@pytest.fixture()
def keyring():
    kr = DictKeyring()
    oauth.set_store(oauth.TokenStore(kr))
    oauth._pending.clear()
    yield kr
    oauth.set_store(None)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------------------------------------------------- Gmail
def gmail(handler):
    return GmailProvider(Settings(), client(handler), token=lambda: "tok123")


def test_gmail_send_posts_a_base64_mime_message(tmp_path):
    seen = {}
    attachment = tmp_path / "plan.pdf"
    attachment.write_bytes(b"%PDF-1.4 hello")

    def handler(request):
        seen["auth"], seen["url"], seen["body"] = request.headers["authorization"], str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"id": "18f", "threadId": "t1", "labelIds": ["SENT"]})

    result = gmail(handler).send([Contact("Ali Khan", "ali@example.com")], "Project update", "Hello Ali.\nSee you.", [Attachment("plan.pdf", str(attachment), 14)])
    assert seen["auth"] == "Bearer tok123" and seen["url"].endswith("/gmail/v1/users/me/messages/send") and result.message_id == "18f"
    msg = email.message_from_bytes(base64.urlsafe_b64decode(seen["body"]["raw"]), policy=policy.default)
    assert msg["To"] == "Ali Khan <ali@example.com>" and msg["Subject"] == "Project update"
    assert msg.get_body(preferencelist=("plain",)).get_content().strip() == "Hello Ali.\nSee you."
    assert [p.get_filename() for p in msg.iter_attachments()] == ["plan.pdf"]
    assert next(msg.iter_attachments()).get_content() == b"%PDF-1.4 hello"


def test_gmail_sent_check_needs_the_sent_label_subject_and_recipients():
    def reply(labels, subject="Project update", to="Ali Khan <ali@example.com>"):
        def handler(request):
            assert request.url.path.endswith("/messages/18f") and request.url.params.get_list("metadataHeaders") == ["To", "Subject"]
            return httpx.Response(200, json={"id": "18f", "labelIds": labels, "payload": {"headers": [{"name": "To", "value": to}, {"name": "Subject", "value": subject}]}})
        return handler

    ali = [Contact("Ali", "ali@example.com")]
    result = SendResult("gmail", "18f", time.time())
    assert gmail(reply(["SENT", "INBOX"])).find_sent(result, ali, "Project update").found
    assert not gmail(reply(["DRAFT"])).find_sent(result, ali, "Project update").found           # not in Sent
    assert not gmail(reply(["SENT"], subject="Other")).find_sent(result, ali, "Project update").found
    assert not gmail(reply(["SENT"], to="Bob <bob@example.com>")).find_sent(result, ali, "Project update").found
    assert not gmail(reply(["SENT"])).find_sent(SendResult("gmail", None, 0), ali, "Project update").found


def test_gmail_contacts_search_both_lists_and_dedupes():
    def handler(request):
        if "people:searchContacts" in str(request.url):
            return httpx.Response(200, json={"results": [{"person": {"names": [{"displayName": "Ali Khan"}], "emailAddresses": [{"value": "ali@example.com"}]}}]})
        return httpx.Response(200, json={"results": [{"person": {"names": [{"displayName": "Ali K"}], "emailAddresses": [{"value": "ali@example.com"}, {"value": "ak@work.org"}]}}]})

    found = gmail(handler).search_contacts("ali")
    assert [(c.name, c.email) for c in found] == [("Ali Khan", "ali@example.com"), ("Ali K", "ak@work.org")]


@pytest.mark.parametrize("status,exc,text", [(401, NotConnected, "Connect it again"), (403, MailError, "permission"), (429, MailError, "busy"), (500, MailError, "500")])
def test_gmail_errors_are_readable(status, exc, text):
    with pytest.raises(exc, match=text):
        gmail(lambda r: httpx.Response(status)).send([Contact("A", "a@b.co")], "s", "b", [])


def test_an_unreachable_provider_is_a_readable_error():
    def boom(request):
        raise httpx.ConnectError("no network")
    with pytest.raises(MailError, match="could not be reached"):
        gmail(boom).send([Contact("A", "a@b.co")], "s", "b", [])


def test_a_contacts_failure_does_not_block_a_spoken_address():
    assert gmail(lambda r: httpx.Response(500)).search_contacts("ali") == []


def test_a_vanished_attachment_stops_the_send(tmp_path):
    with pytest.raises(MailError, match="not there any more"):
        gmail(lambda r: httpx.Response(200, json={})).send([Contact("A", "a@b.co")], "s", "b", [Attachment("gone.txt", str(tmp_path / "gone.txt"), 1)])


# ----------------------------------------------------------------------------------------------------------- Outlook
def outlook(handler):
    return OutlookProvider(Settings(), client(handler), token=lambda: "tok456")


def test_outlook_send_mail_payload(tmp_path):
    seen = {}
    f = tmp_path / "a.txt"
    f.write_text("data")

    def handler(request):
        seen["url"], seen["body"], seen["auth"] = str(request.url), json.loads(request.content), request.headers["authorization"]
        return httpx.Response(202)

    result = outlook(handler).send([Contact("Ali", "ali@example.com")], "Hi", "Body text", [Attachment("a.txt", str(f), 4)])
    msg = seen["body"]["message"]
    assert seen["url"].endswith("/me/sendMail") and seen["auth"] == "Bearer tok456" and seen["body"]["saveToSentItems"] is True
    assert msg["subject"] == "Hi" and msg["body"] == {"contentType": "Text", "content": "Body text"}
    assert msg["toRecipients"] == [{"emailAddress": {"address": "ali@example.com", "name": "Ali"}}]
    assert msg["attachments"][0]["name"] == "a.txt" and base64.b64decode(msg["attachments"][0]["contentBytes"]) == b"data"
    assert result.message_id is None and abs(result.sent_at - time.time()) < 5


def test_outlook_sent_items_check_matches_subject_recipient_and_time():
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))      # noqa: E731

    def items(*values):
        return lambda r: httpx.Response(200, json={"value": list(values)})

    mine = {"id": "m1", "subject": "Hi", "sentDateTime": iso(now), "toRecipients": [{"emailAddress": {"address": "Ali@Example.com"}}]}
    result = SendResult("outlook", None, now - 5)
    ali = [Contact("Ali", "ali@example.com")]
    assert outlook(items(mine)).find_sent(result, ali, "Hi").found
    assert not outlook(items({**mine, "subject": "Different"})).find_sent(result, ali, "Hi").found
    assert not outlook(items({**mine, "toRecipients": [{"emailAddress": {"address": "bob@example.com"}}]})).find_sent(result, ali, "Hi").found
    assert not outlook(items({**mine, "sentDateTime": iso(now - 3600)})).find_sent(result, ali, "Hi").found      # an old message with the same subject
    assert not outlook(items()).find_sent(result, ali, "Hi").found


def test_outlook_people_search():
    def handler(request):
        assert request.url.params["$search"] == '"ali"'
        return httpx.Response(200, json={"value": [{"displayName": "Ali Raza", "scoredEmailAddresses": [{"address": "ali@work.org"}]}]})
    assert [(c.name, c.email) for c in outlook(handler).search_contacts("ali")] == [("Ali Raza", "ali@work.org")]


def test_make_provider():
    assert make_provider("gmail", Settings()).name == "gmail" and make_provider("outlook", Settings()).name == "outlook"
    with pytest.raises(MailError, match="No e-mail account"):
        make_provider("none", Settings())


# --------------------------------------------------------------------------------------------------------------- OAuth
def test_the_sign_in_url_uses_pkce_state_and_the_smallest_scopes(keyring):
    url = oauth.start("gmail", Settings(), "http://127.0.0.1:8000/mail/oauth/callback")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert urlparse(url).netloc == "accounts.google.com" and q["client_id"] == Settings.google_oauth_client_id
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) >= 43 and q["state"] and q["response_type"] == "code"
    assert q["redirect_uri"] == "http://127.0.0.1:8000/mail/oauth/callback" and q["access_type"] == "offline"
    scopes = q["scope"].split()
    assert "https://www.googleapis.com/auth/gmail.send" in scopes and "https://www.googleapis.com/auth/gmail.metadata" in scopes
    assert not any(s.endswith(("gmail.modify", "gmail.readonly", "mail.google.com/")) for s in scopes)      # no wider mail access
    ms = parse_qs(urlparse(oauth.start("outlook", Settings(), "http://127.0.0.1:8000/x")).query)
    assert "Mail.Send" in ms["scope"][0] and "offline_access" in ms["scope"][0] and "client_secret" not in ms


def test_a_provider_without_a_client_id_says_what_to_add(keyring):
    class Empty:
        google_oauth_client_id = ""
        ms_oauth_client_id = ""
    with pytest.raises(MailError, match="GOOGLE_OAUTH_CLIENT_ID"):
        oauth.start("gmail", Empty(), "http://x/cb")
    with pytest.raises(MailError, match="Choose gmail or outlook"):
        oauth.start("yahoo", Settings(), "http://x/cb")


def test_the_code_is_exchanged_with_the_verifier_and_tokens_go_to_the_credential_manager(keyring):
    url = oauth.start("gmail", Settings(), "http://127.0.0.1:8000/mail/oauth/callback")
    state = parse_qs(urlparse(url).query)["state"][0]
    seen = {}

    def handler(request):
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": "x"})

    assert oauth.finish(state, "the-code", Settings(), client(handler)) == "gmail"
    form = seen["form"]
    assert form["code"] == "the-code" and form["grant_type"] == "authorization_code" and form["client_secret"] == "gsecret"
    assert form["redirect_uri"] == "http://127.0.0.1:8000/mail/oauth/callback" and len(form["code_verifier"]) >= 43
    stored = json.loads(keyring.data[(oauth.SERVICE, "gmail")])
    assert stored["access_token"] == "AT" and stored["refresh_token"] == "RT"
    assert oauth.status("gmail") == {"provider": "gmail", "connected": True, "account": None}
    assert "AT" not in json.dumps(oauth.status("gmail")) and "RT" not in json.dumps(oauth.status("gmail"))


def test_the_state_is_single_use_unknown_or_expired(keyring):
    url = oauth.start("outlook", Settings(), "http://x/cb")
    state = parse_qs(urlparse(url).query)["state"][0]
    ok = lambda r: httpx.Response(200, json={"access_token": "A", "expires_in": 10})      # noqa: E731
    oauth.finish(state, "c", Settings(), client(ok))
    with pytest.raises(MailError, match="expired or was already used"):
        oauth.finish(state, "c", Settings(), client(ok))
    with pytest.raises(MailError, match="expired or was already used"):
        oauth.finish("forged-state", "c", Settings(), client(ok))
    state2 = parse_qs(urlparse(oauth.start("outlook", Settings(), "http://x/cb")).query)["state"][0]
    oauth._pending[state2].created -= oauth.STATE_SECONDS + 1
    with pytest.raises(MailError, match="expired"):
        oauth.finish(state2, "c", Settings(), client(ok))


def test_the_provider_refusing_the_code_is_reported_and_nothing_is_stored(keyring):
    state = parse_qs(urlparse(oauth.start("gmail", Settings(), "http://x/cb")).query)["state"][0]
    with pytest.raises(MailError, match="refused"):
        oauth.finish(state, "bad", Settings(), client(lambda r: httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code"})))
    assert keyring.data == {}


def test_an_expired_token_is_refreshed_and_keeps_the_refresh_token(keyring):
    keyring.set_password(oauth.SERVICE, "gmail", json.dumps({"access_token": "old", "refresh_token": "RT", "expires_at": time.time() - 10, "account": "me@x.com"}))
    seen = {}

    def handler(request):
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})       # Google sends no refresh token here

    assert oauth.access_token("gmail", Settings(), client(handler)) == "new"
    assert seen["form"]["grant_type"] == "refresh_token" and seen["form"]["refresh_token"] == "RT"
    stored = json.loads(keyring.data[(oauth.SERVICE, "gmail")])
    assert stored["refresh_token"] == "RT" and stored["account"] == "me@x.com" and stored["access_token"] == "new"
    assert oauth.access_token("gmail", Settings(), client(lambda r: pytest.fail("a valid token must not be refreshed"))) == "new"


def test_a_revoked_sign_in_asks_to_connect_again(keyring):
    with pytest.raises(NotConnected, match="not connected"):
        oauth.access_token("gmail", Settings())
    keyring.set_password(oauth.SERVICE, "outlook", json.dumps({"access_token": "old", "refresh_token": "RT", "expires_at": 0}))
    with pytest.raises(NotConnected, match="no longer works"):
        oauth.access_token("outlook", Settings(), client(lambda r: httpx.Response(400, json={"error": "invalid_grant"})))
    keyring.set_password(oauth.SERVICE, "outlook", json.dumps({"access_token": "old", "refresh_token": "", "expires_at": 0}))
    with pytest.raises(NotConnected, match="expired"):
        oauth.access_token("outlook", Settings())


def test_disconnect_and_missing_credential_manager(keyring):
    keyring.set_password(oauth.SERVICE, "gmail", json.dumps({"access_token": "x"}))
    assert oauth.disconnect("gmail") is True and oauth.disconnect("gmail") is False
    class Broken:
        def get_password(self, *a):
            raise RuntimeError("no backend")
    oauth.set_store(oauth.TokenStore(Broken()))
    with pytest.raises(MailError, match="Credential Manager"):
        oauth.get_store().load("gmail")
    assert oauth.status("gmail")["connected"] is False


# -------------------------------------------------------------------------------------------------------- the endpoints
def test_connect_and_callback_endpoints(client, keyring, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "google_oauth_client_id", "gid", raising=False)
    monkeypatch.setattr(oauth, "_token_request", lambda url, data, http: {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 3600, "scope": ""})
    monkeypatch.setattr("app.mail.api._remember_account", lambda provider: None)
    url = client.post("/mail/connect", json={"provider": "gmail"}).json()["url"]
    assert "accounts.google.com" in url and "http%3A%2F%2Ftestserver%2Fmail%2Foauth%2Fcallback" in url
    state = parse_qs(urlparse(url).query)["state"][0]
    page = client.get("/mail/oauth/callback", params={"state": state, "code": "abc"})
    assert page.status_code == 200 and "Gmail is connected" in page.text
    assert client.get("/mail/oauth/callback", params={"state": state, "code": "abc"}).status_code == 400            # single use
    assert client.get("/mail/oauth/callback", params={"error": "access_denied"}).status_code == 400
    status = client.get("/mail/status").json()
    assert status["providers"]["gmail"]["connected"] is True and "AT" not in json.dumps(status) and "RT" not in json.dumps(status)
    assert client.post("/mail/disconnect", json={"provider": "gmail"}).json() == {"disconnected": True}
    assert client.post("/mail/connect", json={"provider": "yahoo"}).status_code == 422


def test_the_callback_is_the_only_mail_page_without_the_api_token():
    from app import main
    assert "/mail" in main._PROTECTED_PREFIXES
    import inspect
    source = inspect.getsource(main._guarded)
    assert '"/mail/oauth/callback"' in source and source.count("oauth/callback") == 1
