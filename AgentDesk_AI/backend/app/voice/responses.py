"""What the assistant says out loud: short phrases per (event, language, personality) with slots.

Rules: at most two sentences; "Done" is only produced for a task whose every action was verified
(`line_for_task`); personalities change wording only, never the event chosen (safety rule 3.9).
Languages: "en", "roman" (Roman Urdu), "ur" (Urdu script), "hi" (Hindi).
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple

from . import personas

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


class Voiceprint(NamedTuple):
    """Who is speaking: the personality plus the user's name and the custom sliders. Wording only."""
    personality: str = "assistant"
    user_name: str = ""
    formality: int = 50
    humour: int = 50


# style -> event -> language -> template. Missing entries fall back to the Assistant set above, so every
# personality can say every event; Urdu-script and Hindi currently use the Assistant wording.
_P: dict[str, dict[str, dict[str, str]]] = {
    "mentor": {
        "wake": {"en": "I'm here. What shall we work on?", "roman": "Main hoon. Kya karna hai?"},
        "acknowledge": {"en": "Let's do that.", "roman": "Chalein, karte hain."},
        "progress": {"en": "This step takes a moment.", "roman": "Is step mein thora waqt lagta hai."},
        "success": {"en": "That's finished. {detail}", "roman": "Mukammal. {detail}"},
        "failure": {"en": "That didn't work, and here is why: {reason}", "roman": "Nahi hua, wajah yeh hai: {reason}"},
        "cancelled": {"en": "No problem. Nothing was changed.", "roman": "Koi masla nahi. Kuch nahi badla."},
    },
    "friend": {
        "wake": {"en": "Hey{who}, I'm listening!", "roman": "Haan bolo{who}!"},
        "acknowledge": {"en": "On it{who}!", "roman": "Abhi karta hoon{who}!"},
        "progress": {"en": "Hang on, almost there!", "roman": "Bas thora sa aur!"},
        "success": {"en": "All done{who}! {detail}", "roman": "Ho gaya{who}! {detail}"},
        "failure": {"en": "Oops, that didn't work: {reason}", "roman": "Oho, nahi hua: {reason}"},
        "cancelled": {"en": "No worries, cancelled. Nothing changed.", "roman": "Koi baat nahi, cancel. Kuch nahi badla."},
    },
    "coach": {
        "wake": {"en": "Ready. What's the goal?", "roman": "Tayyar hoon. Goal kya hai?"},
        "acknowledge": {"en": "Let's go!", "roman": "Chalo shuru!"},
        "progress": {"en": "Keep going, we're getting there.", "roman": "Lage raho, pohnchne wale hain."},
        "success": {"en": "Nailed it. {detail}", "roman": "Kar diya. {detail}"},
        "failure": {"en": "Not this time: {reason} We'll fix it.", "roman": "Is baar nahi: {reason} Theek kar lenge."},
        "cancelled": {"en": "Cancelled, nothing changed. What's next?", "roman": "Cancel, kuch nahi badla. Ab agla?"},
    },
}


def _voiceprint(personality: "str | Voiceprint") -> Voiceprint:
    return personality if isinstance(personality, Voiceprint) else Voiceprint(personality or "assistant")


class _Safe(dict):
    def __missing__(self, key):  # an unknown slot degrades to empty text, never to an exception
        return ""


_GREETING = {
    "morning": {"en": "Good morning{who}.", "roman": "Subah bakhair{who}.", "ur": "صبح بخیر{who}۔", "hi": "सुप्रभात{who}।"},
    "afternoon": {"en": "Good afternoon{who}.", "roman": "Assalam o alaikum{who}.", "ur": "السلام علیکم{who}۔", "hi": "नमस्ते{who}।"},
    "evening": {"en": "Good evening{who}.", "roman": "Shaam bakhair{who}.", "ur": "شام بخیر{who}۔", "hi": "शुभ संध्या{who}।"},
}
_REMINDERS = {
    "en": ("You have {n} reminder today.", "You have {n} reminders today."),
    "roman": ("Aaj aap ka {n} reminder hai.", "Aaj aap ke {n} reminders hain."),
    "ur": ("آج آپ کا {n} ریمائنڈر ہے۔", "آج آپ کے {n} ریمائنڈر ہیں۔"),
    "hi": ("आज आपका {n} रिमाइंडर है।", "आज आपके {n} रिमाइंडर हैं।"),
}


def greeting(hour: int, language: str = "en", user_name: str = "", reminders: int = 0) -> str:
    """Time-of-day greeting with the user's name and, when there are any, today's reminder count."""
    part = "morning" if 4 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening"
    lang = _lang(language)
    name = " ".join(user_name.split())[:40]
    text = _fmt(_GREETING[part][lang], {"who": f", {name}" if name else ""})
    if reminders > 0:
        one, many = _REMINDERS[lang]
        text += " " + _fmt(one if reminders == 1 else many, {"n": reminders})
    return text


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


def line(event: str, language: str = "en", personality: "str | Voiceprint" = "assistant", **slots: Any) -> str:
    """One spoken line. Unknown personality -> assistant; unknown language -> English."""
    vp = _voiceprint(personality)
    lang = _lang(language)
    style = personas.resolve_style(vp.personality, vp.formality, vp.humour)
    name = " ".join(vp.user_name.split())[:40]
    slots = {**slots, "who": f", {name}" if name else ""}
    template = _P.get(style, {}).get(event, {}).get(lang)
    if template is None:
        table = _T[event]
        template = table.get(lang) or table["en"]
    return _fmt(template, slots)


def _slots_for(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app_name") or args.get("app") or args.get("name") or "").strip()
    return {"app": app[:1].upper() + app[1:], "query": str(args.get("query") or "")[:60]}


def acknowledge_line(tool: str, args: dict[str, Any], language: str = "en", personality: "str | Voiceprint" = "assistant") -> str:
    entry = _TOOL.get(tool, {}).get(_lang(language))
    if not entry:
        return line("acknowledge", language, personality)
    return _fmt(entry[0], _slots_for(args))


def _verified(action: dict[str, Any]) -> bool:
    ver = action.get("verification") or {}
    return action.get("status") == "succeeded" and bool(ver.get("passed"))


def line_for_task(task: dict[str, Any], language: str = "en", personality: "str | Voiceprint" = "assistant") -> str:
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
        said = _tool_speech(actions)
        if said:
            return said  # an answer or a read-out ("Here is what I remember...") is the reply itself
        return line("success", language, personality, detail=_success_detail(actions, language))
    if status == "completed" and not actions:
        note = task.get("plan_note")
        if note:
            return _answer_speech(note)  # a chat answer / report: say it as it is
        return line("success", language, personality, detail="")  # nothing to verify
    if status == "partially_completed":
        done = [a for a in actions if _verified(a)]
        return line("partial", language, personality, detail=_success_detail(done, language) if done else "")
    if status in ("rejected", "cancelled"):
        return line("cancelled", language, personality)
    reason = (task.get("error") or next((a.get("error") for a in actions if a.get("error")), None)
              or _failed_check(actions) or "it did not work")
    return line("failure", language, personality, reason=_one_sentence(reason))


def _tool_speech(actions: list[dict[str, Any]]) -> str:
    """The text a single verified action wants spoken (its `spoken` output), if any."""
    if len(actions) != 1:
        return ""
    return _answer_speech(str(((actions[0].get("result") or {}).get("output") or {}).get("spoken") or ""))


def _answer_speech(text: str, sentences: int = 3, limit: int = 420) -> str:
    text = " ".join(str(text).split())
    out = " ".join(re.split(r"(?<=[.!?؟۔।])\s+", text)[:sentences])
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


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
