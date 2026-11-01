"""Writing an e-mail by voice: who, subject, then the message dictated sentence by sentence, edited by voice, read back, and sent.

    "Write an email to Ali"  ->  "What is the subject?"  ->  (subject)  ->  "Go ahead with the message."  ->  (sentences)
    "stop dictation"  ->  "Your email is written. Shall I send it, or do you want to change something?"
    "send it"  ->  reads the recipient, the subject and the first line, then "Shall I send it?"  ->  "yes, send it"  ->  sent, found in Sent  ->  "Email sent to Ali."

While a draft is open every plain sentence is part of the message, and only whole-utterance phrases are commands ("read it back", "delete the last
sentence", "change tomorrow to Monday", "make it more formal", "attach the report", "send it", "cancel the email"). The send happens ONLY
in the `confirm` state, after the read-back, on a sentence that contains "send" (or "bhej") and a yes: a dictated sentence, a page, a file
or the model can never confirm. The confirmation is a server-issued secret tied to the exact text (mail/draft.py); any edit voids it.
Called from `executor._start_task` BEFORE the other parsers (so "read it to me" and "undo" mean the email while one is open).
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from ..ai.base import Plan, PlanAction
from ..config import Settings
from ..mail import contacts, rewrite, service
from ..mail import draft as drafts
from ..mail.models import Contact, MailError, NotConnected, mask_email, spoken_to_email, valid_email
from ..models import Task, TaskStatus, utcnow
from ..nlu.normalizer import detect_language
from ..tools.registry import ToolRegistry

_PUNCT = re.compile(r"[\s.,;:!?،۔।؟…\"'`]+")

# ---------------------------------------------------------------------------------------------------------- wording
SAYS: dict[str, dict[str, str]] = {
    "not_connected": {"en": "Email is not connected yet. Open Settings, Email, and connect Gmail or Outlook.",
                      "roman_ur": "Email abhi connect nahi hai. Settings, Email mein Gmail ya Outlook connect karein.",
                      "ur": "ای میل ابھی جڑی نہیں۔ سیٹنگز، ای میل میں جی میل یا آؤٹ لک جوڑیں۔", "hi": "ईमेल अभी जुड़ा नहीं है। सेटिंग्स, ईमेल में Gmail या Outlook जोड़ें।"},
    "who": {"en": "Who is it for?", "roman_ur": "Kis ko bhejna hai?", "ur": "کس کو بھیجنی ہے؟", "hi": "किसे भेजना है?"},
    "subject": {"en": "What is the subject?", "roman_ur": "Subject kya hai?", "ur": "سبجیکٹ کیا ہے؟", "hi": "विषय क्या है?"},
    "go_ahead": {"en": "Go ahead with the message. Say stop dictation when you are done.", "roman_ur": "Message boliye. Khatam hone par stop dictation kahein.",
                 "ur": "پیغام بولیے۔ ختم ہونے پر اسٹاپ ڈکٹیشن کہیں۔", "hi": "संदेश बोलिए। पूरा होने पर स्टॉप डिक्टेशन कहें।"},
    "noted": {"en": "Okay.", "roman_ur": "Theek hai.", "ur": "ٹھیک ہے۔", "hi": "ठीक है।"},
    "written": {"en": "Your email is written. Shall I send it, or do you want to change something?",
                "roman_ur": "Email likh di hai. Bhej doon, ya kuch badalna hai?", "ur": "ای میل لکھ دی ہے۔ بھیج دوں، یا کچھ بدلنا ہے؟",
                "hi": "ईमेल लिख दी है। भेज दूँ, या कुछ बदलना है?"},
    "shall_send": {"en": "Shall I send it?", "roman_ur": "Bhej doon?", "ur": "بھیج دوں؟", "hi": "भेज दूँ?"},
    "say_yes_send": {"en": "Say yes, send it, to send, or no to change something.", "roman_ur": "Bhejne ke liye haan, bhej do kahein, ya badalne ke liye nahi.",
                     "ur": "بھیجنے کے لیے ہاں، بھیج دو کہیں، یا بدلنے کے لیے نہیں۔", "hi": "भेजने के लिए हाँ, भेज दो कहें, या बदलने के लिए नहीं।"},
    "sent": {"en": "Email sent to {who}.", "roman_ur": "{who} ko email bhej di.", "ur": "{who} کو ای میل بھیج دی۔", "hi": "{who} को ईमेल भेज दी।"},
    "cancelled": {"en": "Okay, the email is discarded.", "roman_ur": "Theek hai, email hata di.", "ur": "ٹھیک ہے، ای میل ہٹا دی۔", "hi": "ठीक है, ईमेल हटा दी।"},
    "which": {"en": "Which one do you mean: {names}?", "roman_ur": "Kaun sa: {names}?", "ur": "کون سا: {names}؟", "hi": "कौन सा: {names}?"},
    "not_found": {"en": "I couldn't find {name} in your contacts. Say the email address, like ali at example dot com.",
                  "roman_ur": "{name} contacts mein nahi mila. Email address boliye, jaise ali at example dot com.",
                  "ur": "{name} رابطوں میں نہیں ملا۔ ای میل ایڈریس بولیں، جیسے ali at example dot com۔", "hi": "{name} संपर्कों में नहीं मिला। ईमेल पता बोलें, जैसे ali at example dot com।"},
    "empty": {"en": "The email has no text yet.", "roman_ur": "Email mein abhi kuch likha nahi.", "ur": "ای میل میں ابھی کچھ نہیں لکھا۔", "hi": "ईमेल में अभी कुछ नहीं लिखा।"},
    "deleted": {"en": "Deleted the last sentence.", "roman_ur": "Aakhri jumla hata diya.", "ur": "آخری جملہ ہٹا دیا۔", "hi": "आख़िरी वाक्य हटा दिया।"},
    "undone": {"en": "Undone.", "roman_ur": "Wapas kar diya.", "ur": "واپس کر دیا۔", "hi": "वापस कर दिया।"},
    "nothing_to_undo": {"en": "There is nothing to undo.", "roman_ur": "Wapas karne ko kuch nahi.", "ur": "واپس کرنے کو کچھ نہیں۔", "hi": "वापस करने को कुछ नहीं।"},
    "changed": {"en": "Changed {n} place{s}.", "roman_ur": "{n} jagah badal di.", "ur": "{n} جگہ بدل دی۔", "hi": "{n} जगह बदल दी।"},
    "not_in_text": {"en": "I couldn't find \"{old}\" in the email.", "roman_ur": "Email mein \"{old}\" nahi mila.", "ur": "ای میل میں \"{old}\" نہیں ملا۔",
                    "hi": "ईमेल में \"{old}\" नहीं मिला।"},
    "subject_set": {"en": "Subject: {subject}.", "roman_ur": "Subject: {subject}.", "ur": "سبجیکٹ: {subject}۔", "hi": "विषय: {subject}।"},
    "recipient_set": {"en": "Now it is to {who}.", "roman_ur": "Ab yeh {who} ke liye hai.", "ur": "اب یہ {who} کے لیے ہے۔", "hi": "अब यह {who} के लिए है।"},
    "rewrite_offer": {"en": "Here is {style}: {text} Shall I use it?", "roman_ur": "Yeh {style} hai: {text} Istemal karoon?", "ur": "یہ {style} ہے: {text} استعمال کروں؟",
                      "hi": "यह {style} है: {text} इस्तेमाल करूँ?"},
    "rewrite_used": {"en": "Done, I used it.", "roman_ur": "Theek hai, istemal kar liya.", "ur": "ٹھیک ہے، استعمال کر لیا۔", "hi": "ठीक है, इस्तेमाल कर लिया।"},
    "rewrite_kept": {"en": "Okay, I kept your original.", "roman_ur": "Theek hai, aap ka asal rakha.", "ur": "ٹھیک ہے، آپ کا اصل رکھا۔", "hi": "ठीक है, आपका मूल रखा।"},
    "attach_ask": {"en": "Attach {name}, {size}? Say yes.", "roman_ur": "{name} attach karoon, {size}? Haan kahein.", "ur": "{name} لگاؤں، {size}؟ ہاں کہیں۔", "hi": "{name} जोड़ूँ, {size}? हाँ कहें।"},
    "attached": {"en": "Attached {name}.", "roman_ur": "{name} attach kar di.", "ur": "{name} لگا دی۔", "hi": "{name} जोड़ दी।"},
    "attach_none": {"en": "I couldn't find a file called {name}.", "roman_ur": "{name} naam ki file nahi mili.", "ur": "{name} نام کی فائل نہیں ملی۔", "hi": "{name} नाम की फ़ाइल नहीं मिली।"},
    "attach_many": {"en": "More than one file fits {name}: {names}. Which one?", "roman_ur": "{name} ke liye kai files hain: {names}. Kaun si?",
                    "ur": "{name} کے لیے کئی فائلیں ہیں: {names}۔ کون سی؟", "hi": "{name} के लिए कई फ़ाइलें हैं: {names}। कौन सी?"},
    "review_hint": {"en": "Say send it, read it back, or tell me what to change.", "roman_ur": "Bhejne ke liye send it, ya parh kar sunao, ya batayein kya badalna hai.",
                    "ur": "بھیجنے کے لیے سینڈ اٹ، یا پڑھ کر سنانے کو کہیں، یا بتائیں کیا بدلنا ہے۔", "hi": "भेजने के लिए सेंड इट, या पढ़कर सुनाओ, या बताइए क्या बदलना है।"},
    "change_what": {"en": "Okay. What would you like to change?", "roman_ur": "Theek hai. Kya badalna hai?", "ur": "ٹھیک ہے۔ کیا بدلنا ہے؟", "hi": "ठीक है। क्या बदलना है?"},
    "already": {"en": "You already have an unfinished email to {who}. Say continue my email, or cancel the email.",
                "roman_ur": "{who} ke liye ek email adhoori hai. Continue my email ya cancel the email kahein.",
                "ur": "{who} کے لیے ایک ای میل ادھوری ہے۔ کنٹینیو مائی ای میل یا کینسل دی ای میل کہیں۔", "hi": "{who} के लिए एक ईमेल अधूरी है। कंटिन्यू माई ईमेल या कैंसल द ईमेल कहें।"},
    "no_draft": {"en": "There is no email in progress.", "roman_ur": "Koi email likhi nahi ja rahi.", "ur": "کوئی ای میل نہیں لکھی جا رہی۔", "hi": "कोई ईमेल नहीं लिखी जा रही।"},
    "send_failed": {"en": "I could not send it: {why}", "roman_ur": "Main bhej nahi saka: {why}", "ur": "میں بھیج نہیں سکا: {why}", "hi": "मैं भेज नहीं सका: {why}"},
    "problem": {"en": "{why}", "roman_ur": "{why}", "ur": "{why}", "hi": "{why}"},
}


def say(key: str, lang: str, **slots) -> str:
    table = SAYS[key]
    text = table.get(lang) or table["en"]
    return text.format(**slots)


# ------------------------------------------------------------------------------------------------------------ phrases
def norm(text: str) -> str:
    return _PUNCT.sub(" ", (text or "").casefold()).strip()


_START = [
    re.compile(r"^(?:please )?(?:write|compose|draft|create|start|make|send|prepare)(?: me)?(?: an?| the| a new| new)? e-?mail(?: to (?P<n>.+))?$"),
    re.compile(r"^(?:please )?e-?mail (?P<n>.+)$"),
    re.compile(r"^(?:please )?(?:write|send|compose|draft) (?:a )?mail to (?P<n>.+)$"),
    re.compile(r"^(?P<n>.+?) ko (?:e-?mail|mail) (?:bhejo|bhej do|karo|kar do|likho|likh do|bhejna hai|likhni hai)$"),
    re.compile(r"^(?:e-?mail|mail) (?:likho|banao|karo|bhejo|likhni hai)(?: (?P<n>.+) ko)?$"),
]
_NOT_NAMES = {"settings", "app", "client", "address", "account", "provider", "setup", "me", "myself"}
_CANCEL = re.compile(r"^(?:cancel|discard|delete|scrap|drop)(?: the| this| my)?(?: e-?mail| draft| message)?$|^never ?mind$|^(?:email|mail) (?:cancel|hata do|delete karo)$")
_FINISH = re.compile(r"^(?:stop dictation|stop dictating|end dictation|finish dictation|done|i am done|i m done|that s all|that is all|that s it|finished|bas|bas itna|bas itna hi|khatam|ho gaya)$")
_READ = re.compile(r"^(?:read (?:it|that|the email|the message|the draft|my email)(?: back| to me| out| aloud| out loud)?|read back|parh ke sunao|padh ke sunao|sunao)$")
_DELETE_LAST = re.compile(r"^(?:delete|remove|scratch|erase|cut)(?: the)? last (?:sentence|line|one)$|^last (?:sentence|line) (?:delete|hata do|hata dein)$")
_UNDO = re.compile(r"^undo(?: that| it| the last change)?$|^wapas karo$")
_CHANGE = re.compile(r"^(?:change|replace|swap) (?P<a>.+?) (?:to|with|into|by) (?P<b>.+)$")
_SUBJECT = re.compile(r"^(?:change|set|make|update)(?: the)? subject(?: line)?(?: to| as| is)? (?P<s>.+)$|^subject (?:should be|is|will be) (?P<s2>.+)$")
_RECIPIENT = re.compile(r"^(?:change|set|make|send it to|send to|address it to)(?: the)? (?:recipient|receiver|to)(?: to)? (?P<n>.+)$|^(?:send it to|address it to) (?P<n2>.+)$")
_REWRITE = re.compile(r"^(?:please )?(?:make|rewrite|rephrase|reword)(?: it| this| the (?:email|message|text))?(?: be)? (?P<s>more formal|more professional|more polite|more friendly|friendlier|more casual|shorter|more concise|longer|more detailed)$|^(?:please )?(?:rewrite|rephrase|reword)(?: it| this)?$")
_ATTACH = re.compile(r"^(?:please )?(?:attach|add(?: an?)? attachment|add the attachment)(?: the| a| file| the file)? (?P<f>.+)$")
_SEND = re.compile(r"^(?:please )?(?:send|send it|send the (?:e-?mail|message)|send this(?: e-?mail| message)?|bhej do|bhejo|bhej dein)$")
_YES_SEND = re.compile(r"^(?:yes|yeah|yep|haan|han|ji haan|ji|okay|ok|sure)? ?(?:please )?(?:send(?: it| the (?:e-?mail|message))?|bhej do|bhejo|bhej dein)(?: now)?$")
_NO = re.compile(r"^(?:no|nope|nahi|nahin|wait|hold on|not yet|don t|do not|change|edit|change something|change it|let me change it|ruko|abhi nahi)$")
_YES = re.compile(r"^(?:yes|yeah|yep|haan|han|ji haan|ji|okay|ok|sure|use it|do it|yes please|yes use it|theek hai)$")
_KEEP = re.compile(r"^(?:no|nope|nahi|nahin|keep (?:my |the )?original|keep it|don t use it|cancel|skip)$")
_CONTINUE = re.compile(r"^(?:continue|resume|open|show)(?: my| the)?(?: unfinished| draft)? ?(?:e-?mail|draft|message)$|^continue dictation$|^keep dictating$|^add more$")
_ADD = re.compile(r"^(?:add|write|type|append) (?P<t>.+)$", re.I)


def parse_start(command: str) -> tuple[bool, str] | None:
    """(is an e-mail command, the name that was said or ""). The name keeps the capitals the speech-to-text gave it."""
    text = _PUNCT.sub(" ", (command or "").strip()).strip()
    for rx in _START:
        m = re.match(rx.pattern, text, re.I)
        if m:
            name = (m.groupdict().get("n") or "").strip()
            if name.casefold() in _NOT_NAMES:
                return None
            return True, name
    return None


# ------------------------------------------------------------------------------------------------------ task plumbing
def _finish(db: Session, task: Task, text: str, *, full: bool = False, draft: drafts.Draft | None = None) -> Task:
    task.agent_state_json = json.dumps({"provider": "local", "matcher": "mail", **({"speak_full": True} if full else {})})
    task.plan_note, task.status, task.error = text, TaskStatus.COMPLETED, None
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task


def _provider_name(db: Session) -> str:
    return service.chosen_provider(db)


def _connected(db: Session, settings: Settings) -> str | None:
    """The chosen provider if it can be used right now, else None."""
    name = _provider_name(db)
    if name == "none":
        return None
    try:
        service.provider_for(name, settings)
        from ..mail import oauth
        if service._override is None:
            oauth.access_token(name, settings)
    except (MailError, NotConnected):
        return None
    return name


# ----------------------------------------------------------------------------------------------------------- the flow
def try_handle(db: Session, task: Task, command: str, registry: ToolRegistry, settings: Settings) -> Task | None:
    draft = drafts.load(db)
    lang = draft.language if draft is not None else detect_language(command)   # the e-mail keeps the language it was started in
    start = parse_start(command)
    if draft is None:
        if start is None:
            return None
        return _begin(db, task, start[1], lang, settings)
    text = norm(command)
    if text in ("stop", "wait", "hold on"):
        return None                       # the kill switch and "wait" belong to the session; the draft stays as it is
    if start is not None:
        who = draft.recipients_label() or "someone"
        return _finish(db, task, say("already", lang, who=who))
    return _continue(db, task, draft, command, text, lang, registry, settings)


def _begin(db: Session, task: Task, name: str, lang: str, settings: Settings) -> Task:
    provider = _connected(db, settings)
    if provider is None:
        return _finish(db, task, say("not_connected", lang))
    draft = drafts.new_draft(provider, lang)
    if not name:
        draft.state = "to"
        drafts.save(db, draft)
        return _finish(db, task, say("who", lang))
    return _resolve_to(db, task, draft, name, lang, settings)


def _resolve_to(db: Session, task: Task, draft: drafts.Draft, spoken: str, lang: str, settings: Settings) -> Task:
    try:
        found = contacts.resolve(spoken, service.provider_for(draft.provider, settings))
    except (MailError, NotConnected) as exc:
        drafts.save(db, draft)
        return _finish(db, task, say("problem", lang, why=str(exc)))
    if found.kind == "none":
        draft.state, draft.wanted_name = "to", spoken
        drafts.save(db, draft)
        return _finish(db, task, say("not_found", lang, name=spoken))
    if found.kind == "many":
        draft.state, draft.wanted_name, draft.candidates = "to_choose", spoken, [c.as_dict() for c in found.contacts]
        drafts.save(db, draft)
        names = _options(found.contacts)
        return _finish(db, task, say("which", lang, names=names))
    draft.set_recipients([found.contacts[0].as_dict()])
    draft.candidates, draft.wanted_name = [], ""
    if draft.state in ("to", "to_choose"):
        draft.state = "subject"
        drafts.save(db, draft)
        return _finish(db, task, say("subject", lang))
    drafts.save(db, draft)
    return _finish(db, task, say("recipient_set", lang, who=found.contacts[0].label()))


def _options(cs: list[Contact]) -> str:
    labels = [f"{c.label()} at {c.email.split('@')[1]}" if sum(x.label() == c.label() for x in cs) > 1 else c.label() for c in cs[:4]]
    return ", ".join(labels[:-1]) + " or " + labels[-1] if len(labels) > 1 else labels[0]


def _continue(db, task, draft, raw, text, lang, registry, settings) -> Task:
    # 1. cancel works in every state
    if _CANCEL.match(text):
        drafts.clear(db)
        return _finish(db, task, say("cancelled", lang))
    # 2. a question the assistant asked about a suggestion (rewrite / attachment)
    if draft.pending:
        return _answer_pending(db, task, draft, text, lang)
    state = draft.state
    if state == "to":
        return _resolve_to(db, task, draft, raw.strip(), lang, settings) if not _CONTINUE.match(text) else _finish(db, task, say("who", lang))
    if state == "to_choose":
        picked = contacts.pick(raw, draft.candidates)
        if picked is None:
            return _finish(db, task, say("which", lang, names=_options([Contact(c.get("name", ""), c["email"]) for c in draft.candidates])))
        draft.set_recipients([picked.as_dict()])
        draft.candidates = []
        draft.state = "subject"
        drafts.save(db, draft)
        return _finish(db, task, say("subject", lang))
    if state == "subject":
        draft.set_subject(raw)
        draft.state = "body"
        drafts.save(db, draft)
        return _finish(db, task, say("go_ahead", lang))
    if state == "confirm":
        return _confirm_state(db, task, draft, text, lang, registry, settings)
    # body / review: whole-utterance commands first
    handled = _commands(db, task, draft, raw, text, lang, settings, registry)
    if handled is not None:
        return handled
    if state == "review":
        added = _ADD.match(raw.strip())
        if added:
            return _dictate(db, task, draft, added.group("t"), lang)
        return _finish(db, task, say("review_hint", lang))
    return _dictate(db, task, draft, raw, lang)


def _dictate(db, task, draft, text: str, lang: str) -> Task:
    if draft.state == "review":
        draft.state = "body"
    try:
        draft.add_text(text)
    except ValueError as exc:
        return _finish(db, task, str(exc))
    drafts.save(db, draft)
    return _finish(db, task, say("noted", lang))


def _commands(db, task, draft, raw, text, lang, settings, registry) -> Task | None:
    if _CONTINUE.match(text):
        draft.state = "body"
        drafts.save(db, draft)
        return _finish(db, task, say("go_ahead", lang))
    if _FINISH.match(text):
        draft.state = "review"
        drafts.save(db, draft)
        return _finish(db, task, say("written", lang))
    if _READ.match(text):
        return _finish(db, task, draft.body if draft.body else say("empty", lang), full=True)
    if _DELETE_LAST.match(text):
        removed = draft.delete_last()
        drafts.save(db, draft)
        return _finish(db, task, say("deleted", lang) if removed is not None else say("empty", lang))
    if _UNDO.match(text):
        ok = draft.undo()
        drafts.save(db, draft)
        return _finish(db, task, say("undone", lang) if ok else say("nothing_to_undo", lang))
    m = _SUBJECT.match(text)
    if m:
        original = _subject_from_raw(raw)
        draft.set_subject(original or (m.group("s") or m.group("s2") or ""))
        drafts.save(db, draft)
        return _finish(db, task, say("subject_set", lang, subject=draft.subject))
    m = _RECIPIENT.match(text)
    if m:
        return _resolve_to(db, task, draft, (m.group("n") or m.group("n2") or "").strip(), lang, settings)
    m = _CHANGE.match(text)
    if m and not _SUBJECT.match(text):
        old, new = _change_from_raw(raw)
        if not old:
            old, new = m.group("a"), m.group("b")
        count = draft.replace(old, new)
        drafts.save(db, draft)
        if not count:
            return _finish(db, task, say("not_in_text", lang, old=old))
        return _finish(db, task, say("changed", lang, n=count, s="" if count == 1 else "s"))
    m = _REWRITE.match(text)
    if m:
        return _offer_rewrite(db, task, draft, m.group("s") or "more formal", lang, settings)
    m = _ATTACH.match(text)
    if m:
        return _offer_attachment(db, task, draft, _file_from_raw(raw) or m.group("f"), lang, registry.ctx.allowed_directories)
    if _SEND.match(text):
        return _read_back(db, task, draft, lang)
    return None


def _subject_from_raw(raw: str) -> str:
    m = re.match(r"(?is)^\s*(?:please )?(?:change|set|make|update)(?: the)? subject(?: line)?(?: to| as| is)?\s+(.+?)[.!?]?\s*$", raw)
    return (m.group(1).strip() if m else "")


def _change_from_raw(raw: str) -> tuple[str, str]:
    """"Change 'tomorrow' to 'Monday'." keeps the capitals the user said (the normalised text is lower case)."""
    m = re.match(r"(?is)^\s*(?:change|replace|swap)\s+[\"'“]?(.+?)[\"'”]?\s+(?:to|with|into|by)\s+[\"'“]?(.+?)[\"'”]?\s*[.!?]?\s*$", raw)
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", "")


def _file_from_raw(raw: str) -> str:
    m = re.match(r"(?is)^\s*(?:please )?(?:attach|add(?: an?)? attachment|add the attachment)(?: the| a| file| the file)?\s+(.+?)[.!?]?\s*$", raw)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------------------------- suggestions awaiting yes
def _offer_rewrite(db, task, draft, style: str, lang: str, settings) -> Task:
    if not draft.body:
        return _finish(db, task, say("empty", lang))
    try:
        text = rewrite.rewrite(draft.body, style, settings, db)
    except MailError as exc:
        return _finish(db, task, say("problem", lang, why=str(exc)))
    draft.pending = {"kind": "rewrite", "text": text, "style": style}
    drafts.save(db, draft)
    return _finish(db, task, say("rewrite_offer", lang, style=rewrite.style_phrase(style).split(" and ")[0] + " version", text=text), full=True)


def _offer_attachment(db, task, draft, spoken: str, lang: str, roots) -> Task:
    from ..tools.filesystem import find_path_candidates
    found = find_path_candidates(spoken, roots, is_dir_only=False).get("candidates", [])
    from pathlib import Path
    files = [Path(p) for p in found if Path(p).is_file()][:5]
    if not files:
        return _finish(db, task, say("attach_none", lang, name=spoken))
    if len(files) > 1 and not any(f.name.casefold() == spoken.casefold() for f in files):
        draft.pending = {"kind": "attach_choose", "files": [str(f) for f in files]}
        drafts.save(db, draft)
        return _finish(db, task, say("attach_many", lang, name=spoken, names=", ".join(f.name for f in files[:4])))
    chosen = next((f for f in files if f.name.casefold() == spoken.casefold()), files[0])
    return _ask_attach(db, task, draft, chosen, lang)


def _ask_attach(db, task, draft, path, lang: str) -> Task:
    size = path.stat().st_size
    shown = f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
    draft.pending = {"kind": "attach", "path": str(path), "name": path.name, "size": size}
    drafts.save(db, draft)
    return _finish(db, task, say("attach_ask", lang, name=path.name, size=shown))


def _answer_pending(db, task, draft, text: str, lang: str) -> Task:
    pending = draft.pending or {}
    kind = pending.get("kind")
    if kind == "attach_choose":
        from pathlib import Path
        files = [Path(p) for p in pending.get("files", [])]
        pick = contacts.pick(text, [{"name": f.name, "email": str(f)} for f in files])
        draft.pending = None
        if pick is None:
            drafts.save(db, draft)
            return _finish(db, task, say("attach_none", lang, name=text))
        return _ask_attach(db, task, draft, Path(pick.email), lang)
    if _YES.match(text):
        draft.pending = None
        if kind == "rewrite":
            draft.set_body(pending["text"])
            drafts.save(db, draft)
            return _finish(db, task, say("rewrite_used", lang))
        if kind == "attach":
            draft.add_attachment({"name": pending["name"], "path": pending["path"], "size": pending["size"]})
            drafts.save(db, draft)
            return _finish(db, task, say("attached", lang, name=pending["name"]))
    if _KEEP.match(text) or _NO.match(text):
        draft.pending = None
        drafts.save(db, draft)
        return _finish(db, task, say("rewrite_kept", lang) if kind == "rewrite" else say("noted", lang))
    return _finish(db, task, say("rewrite_offer", lang, style="the new version", text=pending.get("text", "")) if kind == "rewrite" else say("attach_ask", lang, name=pending.get("name", ""), size=""), full=True)


# ------------------------------------------------------------------------------------------------ read-back and sending
def _read_back(db, task, draft, lang: str) -> Task:
    try:
        service.check_ready(draft)
    except MailError as exc:
        return _finish(db, task, say("problem", lang, why=str(exc)))
    draft.issue_confirmation()
    drafts.save(db, draft)
    return _finish(db, task, f"{draft.readback()} {say('shall_send', lang)}", full=True)


def _confirm_state(db, task, draft, text: str, lang: str, registry, settings) -> Task:
    """Only a sentence with "send" (or "bhej") in it, after the read-back, sends. Anything else is NOT consent."""
    if _YES_SEND.match(text):
        return _send_now(db, task, draft, lang, registry, settings)
    if _NO.match(text):
        draft.state, draft.confirm = "review", None
        drafts.save(db, draft)
        return _finish(db, task, say("change_what", lang))
    return _finish(db, task, say("say_yes_send", lang))


def _send_now(db, task, draft, lang: str, registry, settings) -> Task:
    from . import executor
    token = (draft.confirm or {}).get("token")
    if not draft.confirmation_valid(token):
        return _read_back(db, task, draft, lang)       # the text changed or it took too long: read it back again
    plan = Plan(actions=[PlanAction(tool="send_email", args={"draft_id": draft.id, "confirmation": token})], note="")
    task.agent_state_json = json.dumps({"provider": "local", "matcher": "mail"})
    task = executor.plan_task(db, task, registry, settings, auto_approve=False, preplanned=plan)
    if task.status == TaskStatus.AWAITING_APPROVAL:
        # The user's "yes, send it" after the read-back IS the explicit confirmation for this one action; the server records the approval.
        executor.approve_task(db, task.id, [a.id for a in task.actions])
        task = executor.run_task(db, task.id, registry, settings)
    return task

