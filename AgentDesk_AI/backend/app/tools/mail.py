"""`send_email`: the one tool that sends a dictated e-mail. It is hidden from the model.

The model cannot plan it (it is not in any tool list or schema, and a plan that names it is rejected) and cannot make up its
argument: `confirmation` is a secret issued by the server when the read-back was spoken, tied to the exact text, and only the dictation
flow knows it. A click on a planner-made action could not reach this tool either: without the right confirmation it refuses to send.
After sending, `verify` looks for the message in the provider's Sent folder; only then is it called sent.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..database import SessionLocal
from ..mail import draft as drafts
from ..mail import service
from ..mail.models import MailError, SendResult, mask_email
from ..verification import failed_result, verdict
from .base import BaseTool, RiskLevel, ToolContext, ToolResult, Verification


class SendEmailArgs(BaseModel):
    draft_id: str = Field(min_length=4, max_length=40)
    confirmation: str = Field(min_length=8, max_length=80, description="Issued by the server after the read-back; never made up")


class SendEmailTool(BaseTool):
    name = "send_email"
    description = "Send the dictated draft (internal: only after the read-back and the user's yes)."
    risk_level = RiskLevel.HIGH
    args_schema = SendEmailArgs
    hidden_from_model = True
    isolation = "thread"

    def _settings(self):
        from ..config import get_settings
        return get_settings()

    def execute(self, args: BaseModel) -> ToolResult:
        a = SendEmailArgs.model_validate(args.model_dump())
        with SessionLocal() as db:
            draft = drafts.load(db)
            if draft is None or draft.id != a.draft_id:
                return ToolResult(success=False, error="That email is not being written any more. Nothing was sent.")
            try:
                result = service.send(draft, a.confirmation, self._settings())
            except MailError as exc:
                return ToolResult(success=False, error=str(exc))
            draft.sent = {"message_id": result.message_id, "sent_at": result.sent_at, "provider": result.provider}
            draft.state = "sent"
            drafts.save(db, draft)
            return ToolResult(success=True, output={
                "provider": result.provider, "message_id": result.message_id, "sent_at": result.sent_at, "subject": draft.subject,
                "to": [c.get("name") or c["email"] for c in draft.to], "to_masked": [mask_email(c["email"]) for c in draft.to],
                "attachments": [x["name"] for x in draft.attachments], "spoken": drafts.sent_text(draft.to, draft.language)})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        out = result.output
        with SessionLocal() as db:
            raw = drafts.load_any(db)
        if raw is None:
            return Verification(passed=False, checks=["draft found"], details="The draft record is gone, so the Sent folder cannot be checked.")
        try:
            evidence = service.find_in_sent(raw, SendResult(out["provider"], out.get("message_id"), out["sent_at"]), self._settings())
        except MailError as exc:
            return Verification(passed=False, checks=["Sent folder read"], details=str(exc))
        return verdict([(f"the message is in {evidence.folder}", evidence.found), ("it went to the chosen recipients", evidence.found)],
                       {"folder": evidence.folder, "to": [mask_email(x) for x in evidence.to], "subject": evidence.subject,
                        "message_id": evidence.message_id},
                       details=None if evidence.found else "It is not in the Sent folder (yet): it may not have been sent. Check your mailbox.")


def mail_tools(ctx: ToolContext | None = None) -> list[BaseTool]:
    return [SendEmailTool(ctx)]
