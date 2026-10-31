"""What the assistant says out loud: short phrases per (event, language, personality) with slots.

Rules: at most two sentences; "Done" is only produced for a task whose every action was verified
(`line_for_task`); personalities change wording only, never the event chosen (safety rule 3.9).
Languages: "en", "roman" (Roman Urdu), "ur" (Urdu script), "hi" (Hindi).
"""
from __future__ import annotations

from typing import Any

LANGUAGES = ("en", "roman", "ur", "hi")
PERSONALITIES = ("assistant", "mentor", "friend", "coach", "custom")

# event -> language -> template. Only the "assistant" set exists in M2; other personalities fall back to it.
_T: dict[str, dict[str, str]] = {
    "wake": {"en": "Yes, I'm listening.", "roman": "Ji, boliye.", "ur": "جی، بولیے۔", "hi": "जी, बोलिए।"},
    "acknowledge": {"en": "On it.", "roman": "Abhi karta hoon.", "ur": "ابھی کرتا ہوں۔", "hi": "अभी करता हूँ।"},
    "needs_approval": {"en": "{summary} Please confirm on screen.", "roman": "{summary} Screen par confirm karein.",
                       "ur": "{summary} اسکرین پر تصدیق کریں۔", "hi": "{summary} स्क्रीन पर पुष्टि करें।"},
    "progress": {"en": "Still working.", "roman": "Kaam ho raha hai.", "ur": "کام ہو رہا ہے۔", "hi": "काम हो रहा है।"},
    "success": {"en": "Done. {detail}", "roman": "Ho gaya. {detail}", "ur": "ہو گیا۔ {detail}", "hi": "हो गया। {detail}"},
    "partial": {"en": "I did part of it: {detail} Want me to retry the rest?",
                "roman": "Kuch hissa hua: {detail} Baqi dobara karoon?",
                "ur": "کچھ حصہ ہوا: {detail} باقی دوبارہ کروں؟", "hi": "कुछ हिस्सा हुआ: {detail} बाकी दोबारा करूँ?"},
    "failure": {"en": "I couldn't do that: {reason}", "roman": "Nahi ho saka: {reason}",
                "ur": "نہیں ہو سکا: {reason}", "hi": "नहीं हो सका: {reason}"},
    "clarify": {"en": "{question}", "roman": "{question}", "ur": "{question}", "hi": "{question}"},
    "cancelled": {"en": "Okay, cancelled. Nothing was changed.", "roman": "Theek hai, cancel kar diya.",
                  "ur": "ٹھیک ہے، منسوخ کر دیا۔", "hi": "ठीक है, रद्द कर दिया।"},
    "slow": {"en": "Thinking slower than usual.", "roman": "Is baar sochne mein zyada waqt lag raha hai.",
             "ur": "معمول سے زیادہ وقت لگ رہا ہے۔", "hi": "सामान्य से ज़्यादा समय लग रहा है।"},
    "clarify_generic": {"en": "Which one do you mean? See the choices on screen.", "roman": "Kaun sa? Screen par options dekhein.",
                        "ur": "کون سا؟ اسکرین پر دیکھیں۔", "hi": "कौन सा? स्क्रीन पर देखें।"},
    "not_understood": {"en": "I didn't catch that. Could you say it again?", "roman": "Samajh nahi aaya, dobara kahein.",
                       "ur": "سمجھ نہیں آیا، دوبارہ کہیں۔", "hi": "समझ नहीं आया, दोबारा कहें।"},
}

# tool -> language -> (acknowledge, success detail); {app} {query} are filled from the action's args.
_TOOL: dict[str, dict[str, tuple[str, str]]] = {
    "open_application": {"en": ("Opening {app}.", "{app} is open."), "roman": ("{app} khol raha hoon.", "{app} khul gaya hai."),
                         "ur": ("{app} کھول رہا ہوں۔", "{app} کھل گیا ہے۔"), "hi": ("{app} खोल रहा हूँ।", "{app} खुल गया है।")},
    "open_url": {"en": ("Opening the page.", "The page is open."), "roman": ("Page khol raha hoon.", "Page khul gaya hai."),
                 "ur": ("صفحہ کھول رہا ہوں۔", "صفحہ کھل گیا ہے۔"), "hi": ("पेज खोल रहा हूँ।", "पेज खुल गया है।")},
    "web_search": {"en": ("Searching for {query}.", "Search results are open."), "roman": ("{query} search kar raha hoon.", "Search khul gaya hai."),
                   "ur": ("{query} تلاش کر رہا ہوں۔", "تلاش کھل گئی ہے۔"), "hi": ("{query} खोज रहा हूँ।", "खोज खुल गई है।")},
    "play_youtube_video": {"en": ("Finding that on YouTube.", "It is playing."), "roman": ("YouTube par dhoond raha hoon.", "Chal raha hai."),
                           "ur": ("یوٹیوب پر ڈھونڈ رہا ہوں۔", "چل رہا ہے۔"), "hi": ("यूट्यूब पर ढूँढ रहा हूँ।", "चल रहा है।")},
    "create_folder": {"en": ("Creating the folder.", "The folder exists."), "roman": ("Folder bana raha hoon.", "Folder ban gaya hai."),
                      "ur": ("فولڈر بنا رہا ہوں۔", "فولڈر بن گیا ہے۔"), "hi": ("फ़ोल्डर बना रहा हूँ।", "फ़ोल्डर बन गया है।")},
    "delete_file": {"en": ("Preparing to delete.", "Deleted. You can restore it from trash."),
                    "roman": ("Delete ki tayyari kar raha hoon.", "Delete ho gaya, trash se wapas aa sakta hai."),
                    "ur": ("حذف کی تیاری کر رہا ہوں۔", "حذف ہو گیا، ٹریش سے واپس آ سکتا ہے۔"),
                    "hi": ("हटाने की तैयारी कर रहा हूँ।", "हट गया, ट्रैश से वापस आ सकता है।")},
    "take_screenshot": {"en": ("Taking a screenshot.", "The screenshot is saved."), "roman": ("Screenshot le raha hoon.", "Screenshot save ho gaya."),
                        "ur": ("اسکرین شاٹ لے رہا ہوں۔", "اسکرین شاٹ محفوظ ہو گیا۔"), "hi": ("स्क्रीनशॉट ले रहा हूँ।", "स्क्रीनशॉट सेव हो गया।")},
}


class _Safe(dict):
    def __missing__(self, key):  # an unknown slot degrades to empty text, never to an exception
        return ""


def _fmt(template: str, slots: dict[str, Any]) -> str:
    return " ".join(template.format_map(_Safe(slots)).split())


def _lang(language: str | None) -> str:
    code = (language or "en").lower()
    if code in ("roman", "roman_urdu", "ur-latn", "ur_roman"):
        return "roman"
    if code.startswith("ur"):
        return "ur"
    if code.startswith("hi"):
        return "hi"
    return "en"


def line(event: str, language: str = "en", personality: str = "assistant", **slots: Any) -> str:
    """One spoken line. Unknown personality -> assistant; unknown language -> English."""
    table = _T[event]
    return _fmt(table.get(_lang(language)) or table["en"], slots)


def _slots_for(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app_name") or args.get("app") or args.get("name") or "").strip()
    return {"app": app[:1].upper() + app[1:], "query": str(args.get("query") or "")[:60]}


def acknowledge_line(tool: str, args: dict[str, Any], language: str = "en", personality: str = "assistant") -> str:
    entry = _TOOL.get(tool, {}).get(_lang(language))
    if not entry:
        return line("acknowledge", language, personality)
    return _fmt(entry[0], _slots_for(args))


def _verified(action: dict[str, Any]) -> bool:
    ver = action.get("verification") or {}
    return action.get("status") == "succeeded" and bool(ver.get("passed"))


def line_for_task(task: dict[str, Any], language: str = "en", personality: str = "assistant") -> str:
    """The closing line for a finished (or waiting) task. "Done" only when the verifier confirmed every action."""
    status, actions = task.get("status"), task.get("actions") or []
    if status == "awaiting_approval":
        return line("needs_approval", language, personality, summary=_approval_summary(language))
    if status == "awaiting_input":
        note = " ".join(str(task.get("plan_note") or "").split())
        if 0 < len(note) <= 110:  # a long note is a screen message, not something to read out
            return line("clarify", language, personality, question=note)
        return line("clarify_generic", language, personality)
    if status == "waiting_retry":
        return line("slow", language, personality)
    if status == "completed" and actions and all(_verified(a) for a in actions):
        return line("success", language, personality, detail=_success_detail(actions, language))
    if status == "completed" and not actions:
        return line("success", language, personality, detail="")  # an answer-only task has nothing to verify
    if status == "partially_completed":
        done = [a for a in actions if _verified(a)]
        return line("partial", language, personality, detail=_success_detail(done, language) if done else "")
    if status in ("rejected", "cancelled"):
        return line("cancelled", language, personality)
    reason = (task.get("error") or next((a.get("error") for a in actions if a.get("error")), None)
              or _failed_check(actions) or "it did not work")
    return line("failure", language, personality, reason=_one_sentence(reason))


def _success_detail(actions: list[dict[str, Any]], language: str) -> str:
    lang = _lang(language)
    if len(actions) > 1:
        return {"en": f"All {len(actions)} steps are finished.", "roman": f"Saray {len(actions)} steps mukammal.",
                "ur": f"تمام {len(actions)} مراحل مکمل۔", "hi": f"सभी {len(actions)} चरण पूरे।"}[lang]
    action = actions[0] if actions else {}
    entry = _TOOL.get(action.get("tool_name", ""), {}).get(lang)
    return _fmt(entry[1], _slots_for(action.get("args") or {})) if entry else ""


def _approval_summary(language: str) -> str:
    return {"en": "This needs your approval.", "roman": "Is ke liye aap ki ijazat chahiye.",
            "ur": "اس کے لیے آپ کی اجازت چاہیے۔", "hi": "इसके लिए आपकी अनुमति चाहिए।"}[_lang(language)]


def _failed_check(actions: list[dict[str, Any]]) -> str | None:
    for a in actions:
        ver = a.get("verification") or {}
        if ver and not ver.get("passed"):
            return ver.get("details") or "the result could not be confirmed"
    return None


def _one_sentence(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    if ". " in text:
        text = text.split(". ")[0] + "."
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
