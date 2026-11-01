"""Local commands for exact tabs and explicit YouTube media state."""
import re
from .base import Plan, PlanAction
from .speech_commands import normalize_speech, NEGATIONS
from .command_language import END, LINK, OPEN

TAB = r"(?:tab|ٹیب|टैब)"
CLOSE = r"(?:close|off|band|بند|کلوز|اف|آف|बंद|क्लोज|ऑफ)"
SITES = {"یوٹیوب": "youtube", "यूट्यूब": "youtube", "گوگل ڈرائیو": "google drive", "गूगल ड्राइव": "google drive",
         "واٹس ایپ": "whatsapp", "व्हाट्सएप": "whatsapp", "جی میل": "gmail", "जीमेल": "gmail",
         "اوپن اے آئی": "openai", "ओपन एआई": "openai", "چیٹ جی پی ٹی": "chatgpt", "चैट जीपीटी": "chatgpt"}


def tab_target(value):
    """Remove only recognized spoken descriptors around one known site.

    Keep meaningful unknown title words intact; never reduce 'YouTube lecture'
    or a request naming two sites to an arbitrary website close.
    """
    raw = value.strip()
    aliases = {**{name: name for name in SITES.values()}, **SITES}
    names = "|".join(re.escape(name) for name in sorted(aliases, key=len, reverse=True))
    matches = list(re.finditer(rf"(?<!\w)({names})(?!\w)", raw, re.I))
    if len(matches) != 1:
        return raw
    match = matches[0]
    remaining = normalize_speech(raw[:match.start()] + " " + raw[match.end():]).split()
    descriptors = set("the my that this one tab browser chrome google edge in on is opened open which was it wala wali woh wo jo ka ki ke mein me par khula khuli khuli hui hua hai hui hay کروم گوگل ایج براؤزر میں پر کھلا کھلی کلی ہوا ہوئی ہے ہیں وہ والا والی جو یہ کا کی کے ٹیب क्रोम गूगल एज ब्राउज़र में पर खुला खुली हुआ हुई है वह वाला वाली जो यह का की के टैब".split())
    if all(word in descriptors for word in remaining):
        return aliases[match.group(1).casefold()]
    return raw


def browser_plan(command):
    raw = command.strip().rstrip("۔।!?")
    if any(word in NEGATIONS for word in normalize_speech(raw).split()):
        return None
    chrome = r"(?:chrome|google chrome|کروم|گوگل کروم|क्रोम|गूगल क्रोम)"
    inside = r"(?:in|ان|mein|میں|में|इन)"
    opened = re.fullmatch(rf"{OPEN}\s+(.+?)(?:\s+{inside}\s+({chrome}))?{END}", raw, re.I)
    if not opened:
        opened = re.fullmatch(rf"(.+?)\s+({chrome})\s+{inside}\s+{OPEN}{END}", raw, re.I)
    if opened:
        site = SITES.get(opened.group(1).strip().casefold(), opened.group(1).strip().casefold())
        urls = {"youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google drive": "https://drive.google.com", "whatsapp": "https://web.whatsapp.com", "openai": "https://openai.com", "chatgpt": "https://chatgpt.com",
                "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com", "twitter": "https://twitter.com",
                "linkedin": "https://www.linkedin.com", "google": "https://www.google.com", "netflix": "https://www.netflix.com",
                "reddit": "https://www.reddit.com", "amazon": "https://www.amazon.com", "wikipedia": "https://www.wikipedia.org"}
        if site in urls:
            args = {"url": urls[site]}
            if opened.group(2):
                args["browser"] = "chrome"
            return Plan(actions=[PlanAction(tool="open_url", args=args)])
    # A named site's "off/close" refers to its tab, not the browser process.
    named = re.fullmatch(rf"(?:{CLOSE}\s+(.+?)|(.+?)\s+{CLOSE}){END}", raw, re.I)
    if named:
        target = (named.group(1) or named.group(2)).strip().casefold()
        target = SITES.get(target, target)
        if target in {"youtube", "gmail", "whatsapp", "google drive", "openai", "chatgpt"}:
            return Plan(actions=[PlanAction(tool="control_browser", args={"action": "close", "target": target})])
    patterns = [rf"(.+?){LINK}\s+{TAB}\s+{CLOSE}{END}",
                rf"{CLOSE}\s+(.+?){LINK}\s+{TAB}{END}",
                rf"{CLOSE}\s+{TAB}\s+(.+?){END}"]
    for pattern in patterns:
        match = re.fullmatch(pattern, raw, re.IGNORECASE)
        if match:
            target = re.sub(r"^the\s+", "", match.group(1).strip(), flags=re.IGNORECASE)
            args = {"action": "close", "target": tab_target(target)}
            if target.isdecimal():
                args["tab_id"] = int(target)
            return Plan(actions=[PlanAction(tool="control_browser", args=args)])
    text = normalize_speech(raw)
    tab_media = re.fullmatch(r"(pause|resume|play) (?:video in )?tab (\d+)", text)
    if tab_media:
        return Plan(actions=[PlanAction(tool="control_browser", args={"action": "pause" if tab_media.group(1) == "pause" else "resume", "target": "youtube", "tab_id": int(tab_media.group(2))})])
    if text in {"list tabs", "show tabs", "tabs dikhao", "ٹیب دکھاؤ"}:
        return Plan(actions=[PlanAction(tool="control_browser", args={"action": "inspect"})])
    aliases = {"ویڈیو": "video", "वीडियो": "video", "یوٹیوب": "youtube", "यूट्यूब": "youtube",
               "pause": "pause", "stop": "pause", "rok": "pause", "roko": "pause", "روک": "pause", "روکو": "pause", "پاز": "pause", "پوز": "pause", "रोक": "pause", "पॉज": "pause", "पॉज़": "pause",
               "resume": "resume", "play": "resume", "پلے": "resume", "chala": "resume", "chalao": "resume", "چلا": "resume", "چلاؤ": "resume", "چلادو": "resume", "चला": "resume", "चलाओ": "resume",
               "band": "pause", "بند": "pause", "बंद": "pause"}
    filler = {"the", "my", "meri", "mera", "ki", "ka", "ko", "par", "karo", "kar", "do", "please", "yeh", "this", "that", "یہ", "وہ", "ये", "इस", "کی", "کا", "کو", "پر", "کرو", "کر", "دو", "میری", "मेरा", "मेरी", "की", "का", "को", "पर", "कर", "करो", "दो"}
    words = [aliases.get(w, w) for w in text.split() if w not in filler]
    if words and set(words) <= {"youtube", "video", "pause", "resume"}:
        actions = set(words) & {"pause", "resume"}
        if len(actions) == 1 and ("youtube" in words or "video" in words):
            return Plan(actions=[PlanAction(tool="control_browser", args={"action": actions.pop(), "target": "youtube"})])
    named = [r"play (.+?) on youtube", r"(?:youtube|یوٹیوب|यूट्यूब) (?:par|پر|पर) (.+?) (?:chala do|چلا دو|चला दो)"]
    for pattern in named:
        match = re.fullmatch(pattern, raw, re.IGNORECASE)
        if match:
            return Plan(actions=[PlanAction(tool="play_youtube_video", args={"query": match.group(1)})])
    return None
