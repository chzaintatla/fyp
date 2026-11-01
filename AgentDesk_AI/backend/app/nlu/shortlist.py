"""Which tools the strict planner is shown for one command.

The planner request carries one JSON-schema variant per tool; with all 38 tools that is ~9.3k tokens, more than the
free Groq tier's 8k tokens/minute, so every call failed with HTTP 413. Showing only the tools a command could need
brings it to ~4-5k tokens.

This only narrows what the MODEL may name. The server still validates every plan against the full registry, and risk and
approvals come from the tool, so a wrong shortlist can only make the planner say "unsupported" / ask a question, never
widen what runs. Words below are written by hand (English, Roman Urdu, Urdu, Hindi), not learned from the evaluation set.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

MAX_SCORED = 10          # most tools chosen by trigger words
MIN_TOOLS = 6            # pad with generic tools up to this many
FILE_TOOLS = {"copy_file", "move_file", "rename_file", "delete_file", "compress_folder", "extract_file", "read_text_file",
              "edit_text_file", "write_text_file", "open_file", "open_folder", "list_folder", "summarise_file"}
GENERIC = ("open_application", "open_folder", "open_file", "open_url", "web_search", "control_desktop",
           "manage_system_setting", "find_paths")

_FILES = r"files?|documents?|docs?|pdf|docx?|xlsx?|txt|csv|\.\w{2,4}\b|faa?il|فائل|फ़?ाइल"
TRIGGERS: dict[str, str] = {
    "manage_system_setting": r"volume|brightness|bright|dim|mute|unmute|bluetooth|wi-?fi|sound|louder|quieter|awaz|aawaz|roshni|"
                             r"آواز|روشنی|بلوٹوتھ|वॉल्यूम|आवाज़?|ब्राइटनेस|ब्लूटूथ|वाईफाई|वाई-फाई|وائی ?فائی|"
                             r"barha|kam kar|barhao|ghatao|بڑھا|کم کر",
    "open_windows_settings": r"settings?|windows update|update|display|setting|سیٹنگ|سیٹنگز|सेटिंग",
    "open_application": r"open|launch|start|run|kholo|khol|kholna|chalao|chalu|کھولو|کھول|چلاؤ|खोलो|खोल|चलाओ|चालू|"
                        r"app|application|notepad|chrome|word|excel|powerpoint|calculator|calc|paint|vlc|spotify|zoom|vs ?code|"
                        r"edge|firefox|teams|telegram|terminal|cmd|نوٹ ?پیڈ|کیلکولیٹر|नोटपैड|कैलकुलेटर",
    "open_folder": r"folders?|director(?:y|ies)|desktop|downloads?|pictures|music|videos|فولڈر|ڈیسک ?ٹاپ|ڈاؤن ?لوڈز|फ़?ोल्डर|डेस्कटॉप|डाउनलोड्स",
    "open_file": _FILES,
    "list_folder": r"list|show|what'?s in|contents?|dikhao|kya hai|دکھاؤ|کیا ہے|दिखाओ|क्या है|folders?|desktop",
    "find_files": r"find|where is|locate|dhund\w*|dhoond\w*|kahan|ڈھونڈ\w*|کہاں|ढूं?ढ\w*|कहाँ|" + _FILES,
    "find_paths": r"find|where is|locate|dhund\w*|dhoond\w*|kahan|folders?|" + _FILES,
    "create_folder": r"create|make|new|banao|bana|بناؤ|بنا|बनाओ|बना|folders?|فولڈر|फ़?ोल्डर",
    "copy_file": r"copy|nakal|نقل|कॉपी|copy karo",
    "move_file": r"move|shift|cut|muntaqil|منتقل|मूव|स्थानांतरित",
    "rename_file": r"rename|naam badal\w*|naam change|نام بدل|नाम बदल|\bnaam\b|نام",
    "delete_file": r"delete|remove|erase|hata\w*|mita\w*|حذف|مٹا\w*|ہٹا\w*|डिलीट|हटा\w*|मिटा\w*",
    "list_trash": r"trash|recycle|ٹریش|ری ?سائیکل|ट्रैश|रीसायकल",
    "restore_file": r"restore|undelete|wapas|recover|trash|واپس|बहाल|वापस|ٹریش|ट्रैश",
    "compress_folder": r"zip|compress|archive|کمپریس|زپ|ज़िप|कंप्रेस",
    "extract_file": r"extract|unzip|unpack|\.zip|zip|نکال\w*|निकाल\w*|अनज़िप",
    "read_text_file": r"read|parh\w*|padh\w*|contents? of|پڑھ\w*|पढ़\w*|" + _FILES,
    "write_text_file": r"write|save|note down|likh\w*|لکھ\w*|लिख\w*|create .*file|new file",
    "edit_text_file": r"edit|replace|change|append|badal\w*|تبدیل|بدل|बदल\w*|" + _FILES,
    "take_screenshot": r"screenshot|screen ?shot|capture|اسکرین ?شاٹ|स्क्रीन ?शॉट",
    "download_web_content": r"download|save (?:the )?page|ڈاؤن ?لوڈ|डाउनलोड|https?://",
    "application_package": r"install|uninstall|winget|package|download|setup|انسٹال|इंस्टॉल|ڈاؤن ?لوڈ|डाउनलोड",
    "winget_install": r"install|winget|انسٹال|इंस्टॉल",
    "web_search": r"search|google|look ?up|find online|serch|سرچ|گوگل|सर्च|गूगल|ڈھونڈ|ढूंढ",
    "open_url": r"\.com|\.org|\.net|\.pk|\.io|https?://|www\.|websites?|sites?|go to|youtube|gmail|google|facebook|instagram|"
                r"twitter|linkedin|chatgpt|github|netflix|daraz|whatsapp|یوٹیوب|جی ?میل|گوگل|فیس ?بک|ویب ?سائٹ|"
                r"यूट्यूब|जीमेल|गूगल|फेसबुक|वेबसाइट",
    "play_youtube_video": r"play|chala\w*|gaana|gane|gaane|song|songs|music|video|lofi|youtube|گانا|گانے|چلا\w*|یوٹیوب|"
                          r"गाना|गाने|चला\w*|यूट्यूब",
    "media_control": r"next|previous|pause|resume|skip|track|song|gaana|agla|pichla|rok do|stop (?:the )?music|اگلا|پچھلا|روک|"
                     r"अगला|पिछला|रोक|play|music",
    "control_browser": r"tabs?|browser|youtube|gmail|site|close|band|pause|resume|reload|refresh|mute|ٹیب|بند|یوٹیوب|"
                       r"टैब|बंद|यूट्यूब|ब्राउज़र|براؤزر",
    "control_desktop": r"close|window|minimi[sz]e|maximi[sz]e|restore|switch|focus|bring|band|inspect|what'?s open|open windows|"
                       r"بند|ونڈو|छोटا|बड़ा|बंद|विंडो|ونڈوز",
    "compose_email": r"e-?mail|mail|compose|draft|ای ?میل|ईमेल|@\w",
    "whatsapp_open_chat": r"whats ?app|message|msg|text him|text her|واٹس ?ایپ|پیغام|व्हाट्स?ऐप|व्हाट्सएप|मैसेज",
    "memory_remember": r"remember|yaad|keep in mind|save (?:this )?(?:in|to) memory|(?:store|keep|make) a note|note that|یاد|याद",
    "memory_recall": r"remember|about me|on me|of mine|know about me|yaad|mere b(?:aa|a)re|saved|stored|memor(?:y|ies)|notes|یاد|میرے بارے|میموری|याद|मेरे बारे|मेमोरी",
    "memory_forget": r"forget|bhool|bhul|yaad|بھول|یاد|भूल|याद",
    "summarise_page": r"summari[sz]e|summary|sum up|tl;?dr|khulasa|mukhtasar|brief|https?://|خلاصہ|مختصر|सारांश|संक्षेप",
    "summarise_file": r"summari[sz]e|summary|sum up|tl;?dr|khulasa|mukhtasar|brief|[a-z]:[\\/]|خلاصہ|مختصر|सारांश|संक्षेप",
    "web_answer": r"weather|news|headlines?|price|latest|score|forecast|rate|current|today|this (?:week|year)|opening hours|who won|stock|"
                  r"exchange|search the web|look up|google|online|mausam|khabar|khabrein|taaza|taza|qeemat|kitne ka|hafte|aaj|abhi|"
                  r"موسم|خبر|تازہ|آج|سرخیاں|ریٹ|اسکور|ابھی|मौसम|खबर|ताज़ा|आज|सुर्खियां|रेट|स्कोर|अभी|how much|release date",
}
_COMPILED = {tool: re.compile(r"(?<![\w])(?:" + pattern + r")", re.IGNORECASE) for tool, pattern in TRIGGERS.items()}
# a tool that is usually planned together with another one
PAIRS = {"winget_install": ("application_package",), "download_web_content": ("web_search",)}


def _scores(command: str, registry_names: set[str], extra: Iterable[str], last_tools: Iterable[str]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for tool, rx in _COMPILED.items():
        if tool in registry_names:
            hits = len({m.group(0).casefold() for m in rx.finditer(command)})
            if hits:
                scores[tool] = hits
    for tool in extra:
        if tool in registry_names:
            scores[tool] = scores.get(tool, 0) + 3  # the local matcher already thought of it
    for tool in last_tools:
        if tool in registry_names:
            scores[tool] = scores.get(tool, 0) + 1  # "close it" refers to the previous turn
    return scores


def shortlist_tools(command: str, registry_names: list[str], local_tools: Iterable[str] = (), last_tools: Iterable[str] = ()) -> list[str]:
    """Tool names (registry order) the planner may use for this command. Always at least MIN_TOOLS, at most ~12."""
    names = set(registry_names)
    scores = _scores(command, names, local_tools, last_tools)
    ranked = sorted(scores, key=lambda t: (-scores[t], registry_names.index(t)))[:MAX_SCORED]
    chosen = list(ranked)
    if any(t in FILE_TOOLS for t in chosen):
        chosen.append("find_paths")
    for tool in list(chosen):
        chosen.extend(PAIRS.get(tool, ()))
    for tool in GENERIC:
        if len(set(chosen)) >= MIN_TOOLS:
            break
        chosen.append(tool)
    picked = {t for t in chosen if t in names}
    return [t for t in registry_names if t in picked]


@lru_cache(maxsize=256)
def _cached(command: str, names: tuple[str, ...], local: tuple[str, ...], last: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(shortlist_tools(command, list(names), local, last))


def tools_for(command: str, registry_names: list[str], local_tools: Iterable[str] = (), last_tools: Iterable[str] = ()) -> list[str]:
    return list(_cached(command, tuple(registry_names), tuple(local_tools), tuple(last_tools)))
