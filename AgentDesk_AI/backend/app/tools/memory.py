"""Memory tools: remember, recall, forget. The user's own notes, stored locally (the Memory page shows the same rows).

remember and recall are low risk (they only add to / read the user's own list; every item can be edited or deleted on the
Memory page). forget deletes, so it is medium risk: the user sees what will be removed first. Each tool re-reads the
database in verify(); nothing is reported done on the tool's say-so.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..database import SessionLocal
from ..models import MemoryItem
from ..verification import failed_result, verdict
from .base import BaseTool, RiskLevel, ToolContext, ToolResult, Verification

_WORD = re.compile(r"[\w؀-ۿऀ-ॿ]+", re.U)
_STOP = {"my", "the", "a", "an", "is", "on", "of", "to", "and", "that", "about", "meri", "mera", "ka", "ki", "ke", "ko", "hai"}


def _memory_on(db) -> bool:
    from ..preferences import get_preferences
    return get_preferences(db).memory_enabled


def _tokens(text: str) -> set[str]:
    return {w for w in (t.casefold() for t in _WORD.findall(text)) if w not in _STOP and len(w) > 1}


def matches(items: list[MemoryItem], query: str) -> list[MemoryItem]:
    """Items that contain every meaningful word of the query (so 'exam date' finds 'my exam is on 12 Dec' only if both words occur)."""
    want = _tokens(query)
    if not want:
        return []
    exact = [i for i in items if want <= _tokens(i.text)]
    if exact:
        return exact
    return [i for i in items if want & _tokens(i.text) and len(want & _tokens(i.text)) >= max(1, (len(want) + 1) // 2)]


class RememberArgs(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="What to remember, as the user said it")
    kind: Literal["fact", "preference", "alias"] = "fact"

    @field_validator("text")
    @classmethod
    def _no_secrets(cls, value: str) -> str:
        from ..preferences import MemoryIn
        try:
            return MemoryIn(text=value).text
        except ValidationError as exc:
            raise ValueError(exc.errors()[0]["msg"].removeprefix("Value error, ")) from exc


class MemoryRememberTool(BaseTool):
    name = "memory_remember"
    description = "Save something the user asked to be remembered (name, preference, date). Shown on the Memory page; never secrets."
    risk_level = RiskLevel.LOW
    args_schema = RememberArgs
    isolation = "thread"  # uses the server's database

    def execute(self, args: BaseModel) -> ToolResult:
        a = RememberArgs.model_validate(args.model_dump())
        with SessionLocal() as db:
            if not _memory_on(db):
                return ToolResult(success=False, error="Memory is turned off in Settings, so I did not save it.")
            existing = next((i for i in db.query(MemoryItem).all() if i.text.casefold() == a.text.casefold()), None)
            if existing is not None:
                return ToolResult(success=True, output={"id": existing.id, "text": existing.text, "already_saved": True,
                                                        "spoken": "I already had that."})
            item = MemoryItem(kind=a.kind, text=a.text)
            db.add(item)
            db.commit()
            return ToolResult(success=True, output={"id": item.id, "text": item.text, "spoken": f"Okay, I'll remember: {item.text}"})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        with SessionLocal() as db:
            row = db.get(MemoryItem, result.output["id"])
            return verdict([("the note is stored", row is not None and row.text == result.output["text"])],
                           {"id": result.output["id"], "stored_text": row.text if row else None, "total_items": db.query(MemoryItem).count()})

    def undo(self, args: BaseModel, result: ToolResult) -> ToolResult:
        with SessionLocal() as db:
            row = db.get(MemoryItem, (result.output or {}).get("id", ""))
            if row is not None and not (result.output or {}).get("already_saved"):
                db.delete(row)
                db.commit()
        return ToolResult(success=True, output={"removed": True})


class RecallArgs(BaseModel):
    topic: str | None = Field(default=None, max_length=200, description="Optional word to filter by")


class MemoryRecallTool(BaseTool):
    name = "memory_recall"
    description = "Read out what the user asked to be remembered."
    risk_level = RiskLevel.LOW
    args_schema = RecallArgs
    isolation = "thread"

    def execute(self, args: BaseModel) -> ToolResult:
        a = RecallArgs.model_validate(args.model_dump())
        with SessionLocal() as db:
            if not _memory_on(db):
                return ToolResult(success=False, error="Memory is turned off in Settings.")
            items = db.query(MemoryItem).order_by(MemoryItem.updated_at.desc()).all()
            if a.topic:
                items = matches(items, a.topic)
            texts = [i.text for i in items][:10]
        if not texts:
            return ToolResult(success=True, output={"items": [], "count": 0, "spoken": "I don't have anything saved about you yet."})
        return ToolResult(success=True, output={"items": texts, "count": len(items),
                                                "spoken": "Here is what I remember: " + "; ".join(texts[:5]) + ("." if len(texts) <= 5 else f"; and {len(texts) - 5} more on the Memory page.")})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        a = RecallArgs.model_validate(args.model_dump())
        with SessionLocal() as db:
            rows = db.query(MemoryItem).all()
            expected = len(matches(rows, a.topic) if a.topic else rows)
        return verdict([("count matches the stored notes", expected == result.output["count"])],
                       {"reported": result.output["count"], "stored": expected})


class ForgetArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="Words that identify the note to delete, e.g. 'exam date'")


class MemoryForgetTool(BaseTool):
    name = "memory_forget"
    description = "Delete one remembered note that matches the words given. Needs confirmation; refuses if several match."
    risk_level = RiskLevel.MEDIUM
    args_schema = ForgetArgs
    isolation = "thread"

    def execute(self, args: BaseModel) -> ToolResult:
        a = ForgetArgs.model_validate(args.model_dump())
        with SessionLocal() as db:
            if not _memory_on(db):
                return ToolResult(success=False, error="Memory is turned off in Settings.")
            found = matches(db.query(MemoryItem).all(), a.query)
            if not found:
                return ToolResult(success=False, error=f"I have nothing saved that matches '{a.query}'.")
            if len(found) > 1:
                options = "; ".join(i.text for i in found[:4])
                return ToolResult(success=False, output={"candidates": [i.text for i in found]},
                                  error=f"Several notes match: {options}. Say more of the words, or delete it on the Memory page.")
            item = found[0]
            backup = {"id": item.id, "kind": item.kind, "text": item.text}
            db.delete(item)
            db.commit()
        return ToolResult(success=True, output={"deleted": backup, "spoken": f"Okay, I forgot: {backup['text']}"})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        gone_id = result.output["deleted"]["id"]
        with SessionLocal() as db:
            still = db.get(MemoryItem, gone_id)
            remaining = db.query(MemoryItem).count()
        return verdict([("the note is gone", still is None)], {"deleted_id": gone_id, "remaining_items": remaining})

    def undo(self, args: BaseModel, result: ToolResult) -> ToolResult:
        backup = (result.output or {}).get("deleted")
        if not backup:
            return ToolResult(success=False, error="Nothing to restore.")
        with SessionLocal() as db:
            db.add(MemoryItem(id=backup["id"], kind=backup["kind"], text=backup["text"]))
            db.commit()
        return ToolResult(success=True, output={"restored": backup["text"]})


def memory_tools(ctx: ToolContext | None = None) -> list[BaseTool]:
    return [MemoryRememberTool(ctx), MemoryRecallTool(ctx), MemoryForgetTool(ctx)]
