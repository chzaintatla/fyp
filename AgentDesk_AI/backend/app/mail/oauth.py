"""Sign-in to Gmail or Outlook with OAuth 2.0 (authorization code + PKCE) and the token store.

The assistant never sees a password. The user signs in on the provider's own page; the provider sends the browser back to
`/mail/oauth/callback` on this PC with a one-time code, which is exchanged (with the PKCE verifier) for an access token and a refresh token.
Tokens live only in the Windows Credential Manager (keyring); they are not written to the database, the logs, the task history or any
API answer. Scopes are the smallest that let the app do its job:

  Gmail    gmail.send (send), gmail.metadata (check the Sent label of the message just sent), contacts.readonly + contacts.other.readonly (names)
  Outlook  Mail.Send, Mail.Read (check the Sent Items folder), Contacts.Read, People.Read, offline_access, User.Read

The OAuth client ids come from the environment (GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET, MS_OAUTH_CLIENT_ID): see docs/EMAIL_SETUP.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from .models import MailError, NotConnected

SERVICE = "AgentDesk-mail"
STATE_SECONDS = 600
PROVIDERS = {
    "gmail": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
        "scopes": ["openid", "email", "https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.metadata",
                   "https://www.googleapis.com/auth/contacts.readonly", "https://www.googleapis.com/auth/contacts.other.readonly"],
        "extra": {"access_type": "offline", "prompt": "consent"}, "id_setting": "google_oauth_client_id", "secret_setting": "google_oauth_client_secret",
    },
    "outlook": {
        "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize", "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": ["offline_access", "User.Read", "Mail.Send", "Mail.Read", "Contacts.Read", "People.Read"],
        "extra": {"prompt": "select_account"}, "id_setting": "ms_oauth_client_id", "secret_setting": None,
    },
}


# ----------------------------------------------------------------------------------------------------------- token store
class TokenStore:
    """Tokens in the Windows Credential Manager, one entry per provider. `backend` lets tests use a dict instead."""

    def __init__(self, backend: Any | None = None):
        self._backend = backend

    def _kr(self):
        if self._backend is not None:
            return self._backend
        import keyring
        return keyring

    def load(self, provider: str) -> dict | None:
        try:
            raw = self._kr().get_password(SERVICE, provider)
        except Exception as exc:  # noqa: BLE001 - no credential manager is "not connected", not a crash
            raise MailError(f"The Windows Credential Manager is not available: {type(exc).__name__}") from exc
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def save(self, provider: str, tokens: dict) -> None:
        self._kr().set_password(SERVICE, provider, json.dumps(tokens))

    def delete(self, provider: str) -> bool:
        try:
            self._kr().delete_password(SERVICE, provider)
            return True
        except Exception:  # noqa: BLE001 - nothing stored
            return False


_store = TokenStore()


def get_store() -> TokenStore:
    return _store


def set_store(store: TokenStore | None) -> None:
    global _store
    _store = store or TokenStore()


# --------------------------------------------------------------------------------------------------------------- PKCE
@dataclass
class Pending:
    provider: str
    verifier: str
    redirect_uri: str
    created: float


_pending: dict[str, Pending] = {}
_lock = threading.Lock()


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def start(provider: str, settings, redirect_uri: str) -> str:
    """The URL to open in the browser. Fails clearly when the OAuth client id for this provider is not configured."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise MailError("Choose gmail or outlook.")
    client_id = getattr(settings, spec["id_setting"], "") or ""
    if not client_id:
        raise MailError(f"The {provider} sign-in is not set up: add {spec['id_setting'].upper()} to backend\\.env (see docs/EMAIL_SETUP.md).")
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    with _lock:
        for key in [k for k, v in _pending.items() if time.time() - v.created > STATE_SECONDS]:
            _pending.pop(key, None)
        _pending[state] = Pending(provider, verifier, redirect_uri, time.time())
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(spec["scopes"]), "state": state,
              "code_challenge": _challenge(verifier), "code_challenge_method": "S256", **spec["extra"]}
    return f"{spec['auth']}?{urlencode(params)}"


def finish(state: str, code: str, settings, http: httpx.Client | None = None) -> str:
    """Exchange the one-time code for tokens and store them. Returns the provider name. The state is single-use."""
    with _lock:
        pending = _pending.pop(state, None)
    if pending is None or time.time() - pending.created > STATE_SECONDS:
        raise MailError("This sign-in link expired or was already used. Start again from Settings.")
    spec = PROVIDERS[pending.provider]
    data = {"client_id": getattr(settings, spec["id_setting"], ""), "grant_type": "authorization_code", "code": code,
            "redirect_uri": pending.redirect_uri, "code_verifier": pending.verifier}
    if spec["secret_setting"] and getattr(settings, spec["secret_setting"], ""):
        data["client_secret"] = getattr(settings, spec["secret_setting"])
    tokens = _token_request(spec["token"], data, http)
    tokens["account"] = ""
    get_store().save(pending.provider, tokens)
    return pending.provider


def _token_request(url: str, data: dict, http: httpx.Client | None) -> dict:
    client = http or httpx.Client(timeout=20)
    try:
        r = client.post(url, data=data)
    except httpx.HTTPError as exc:
        raise MailError(f"The sign-in service could not be reached: {type(exc).__name__}") from exc
    if r.status_code != 200:
        raise MailError("The provider refused the sign-in" + (": " + str(r.json().get("error_description") or r.json().get("error"))[:120] if _is_json(r) else "."))
    body = r.json()
    if "access_token" not in body:
        raise MailError("The provider answered without an access token.")
    return {"access_token": body["access_token"], "refresh_token": body.get("refresh_token", ""),
            "expires_at": time.time() + int(body.get("expires_in", 3600)), "scope": body.get("scope", "")}


def _is_json(r: httpx.Response) -> bool:
    try:
        r.json()
        return True
    except ValueError:
        return False


def access_token(provider: str, settings, http: httpx.Client | None = None, now: Callable[[], float] = time.time) -> str:
    """A valid access token, refreshed when it is about to expire. NotConnected when there is none and none can be made."""
    tokens = get_store().load(provider)
    if not tokens:
        raise NotConnected(f"{provider.capitalize()} is not connected. Connect it in Settings > Email.")
    if tokens.get("expires_at", 0) - 60 > now():
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise NotConnected(f"The {provider} sign-in expired. Connect it again in Settings > Email.")
    spec = PROVIDERS[provider]
    data = {"client_id": getattr(settings, spec["id_setting"], ""), "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
    if spec["secret_setting"] and getattr(settings, spec["secret_setting"], ""):
        data["client_secret"] = getattr(settings, spec["secret_setting"])
    try:
        fresh = _token_request(spec["token"], data, http)
    except MailError as exc:
        raise NotConnected(f"The {provider} sign-in no longer works ({exc}). Connect it again in Settings > Email.") from exc
    fresh["refresh_token"] = fresh["refresh_token"] or tokens["refresh_token"]   # Google omits it on refresh
    fresh["account"] = tokens.get("account", "")
    get_store().save(provider, fresh)
    return fresh["access_token"]


def disconnect(provider: str) -> bool:
    return get_store().delete(provider)


def status(provider: str) -> dict:
    """Is there a stored sign-in? Never returns a token."""
    try:
        tokens = get_store().load(provider)
    except MailError as exc:
        return {"provider": provider, "connected": False, "error": str(exc)}
    return {"provider": provider, "connected": bool(tokens), "account": (tokens or {}).get("account") or None}
