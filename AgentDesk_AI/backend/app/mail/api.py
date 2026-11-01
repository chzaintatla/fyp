"""HTTP endpoints for e-mail: connection status, the OAuth sign-in round trip, and the draft shown on screen.

Everything except the OAuth callback needs the local API token like the rest of the app. The callback is opened by the BROWSER coming back from
Google / Microsoft, so it cannot carry the token; it is protected by the single-use `state` and the PKCE verifier instead (see oauth.py).
No endpoint ever returns a token.
"""
from __future__ import annotations

import html

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from . import draft as drafts
from . import oauth
from .models import MailError

router = APIRouter(prefix="/mail")
CALLBACK_PATH = "/mail/oauth/callback"


class ProviderIn(BaseModel):
    provider: str = Field(pattern="^(gmail|outlook)$")


def _configured(provider: str) -> bool:
    spec = oauth.PROVIDERS[provider]
    return bool(getattr(get_settings(), spec["id_setting"], ""))


@router.get("/status")
def mail_status(db: Session = Depends(get_db)) -> dict:
    from ..preferences import get_preferences
    draft = drafts.load(db)
    return {"chosen": get_preferences(db).mail_provider,
            "providers": {p: {**oauth.status(p), "configured": _configured(p)} for p in oauth.PROVIDERS},
            "draft": draft.public() if draft else None}


@router.post("/connect")
def mail_connect(body: ProviderIn, request: Request) -> dict:
    """The sign-in URL to open in the browser. The redirect goes back to this same server."""
    redirect = f"{request.url.scheme}://{request.headers.get('host', '127.0.0.1:8000')}{CALLBACK_PATH}"
    try:
        return {"url": oauth.start(body.provider, get_settings(), redirect)}
    except MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
    if error or not code or not state:
        return _page("Sign-in was cancelled", f"The provider said: {error or 'no code'}. Nothing was connected.", 400)
    try:
        provider = oauth.finish(state, code, get_settings())
        _remember_account(provider)
    except MailError as exc:
        return _page("Sign-in failed", str(exc), 400)
    return _page("Connected", f"{provider.capitalize()} is connected. You can close this tab and go back to the assistant.", 200)


def _remember_account(provider: str) -> None:
    """Best effort: store which address signed in, so the screen can say so. A failure here never undoes the sign-in."""
    try:
        token = oauth.access_token(provider, get_settings())
        url = "https://openidconnect.googleapis.com/v1/userinfo" if provider == "gmail" else "https://graph.microsoft.com/v1.0/me"
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        data = r.json() if r.status_code == 200 else {}
        account = data.get("email") or data.get("mail") or data.get("userPrincipalName") or ""
        tokens = oauth.get_store().load(provider) or {}
        tokens["account"] = account
        oauth.get_store().save(provider, tokens)
    except Exception:  # noqa: BLE001
        pass


def _page(title: str, text: str, status: int) -> HTMLResponse:
    body = (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'><h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>")
    return HTMLResponse(body, status_code=status)


@router.post("/disconnect")
def mail_disconnect(body: ProviderIn) -> dict:
    return {"disconnected": oauth.disconnect(body.provider)}


@router.get("/draft")
def mail_draft(db: Session = Depends(get_db)) -> dict:
    draft = drafts.load(db)
    return draft.public() if draft else {}


@router.post("/draft/cancel")
def mail_draft_cancel(db: Session = Depends(get_db)) -> dict:
    return {"cancelled": drafts.clear(db)}
