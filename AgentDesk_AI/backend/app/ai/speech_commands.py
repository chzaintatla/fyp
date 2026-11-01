"""Conservative normalization of short spoken commands; never fuzzy-match file targets."""
import re
import unicodedata
from difflib import get_close_matches
from .base import Plan, PlanAction

GROUPS = {
    "volume": "volume volum valume volium awaz awaaz sound audio वॉल्यूम वाल्यूम वोल्यूम आवाज آواز والیوم وولیوم وولیم وولوم والیم بولیم",
    "brightness": "brightness brightnes roshni ब्राइटनेस برائٹنیس برائٹنس روشنی",
    "bluetooth": "bluetooth blutooth ब्लूटूथ بلوٹوتھ بلیوٹوتھ",
    "wifi": "wifi wi-fi وائیفائی वाईफाई",
    "theme": "theme", "mode": "mode मोड موڈ",
    "dark": "dark डार्क ڈارک", "light": "light लाइट لائٹ",
    "increase": "up increase raise louder zyada ziyada zada barhao badhao ज्यादा बढ़ाओ زیادہ بڑھاؤ بڑھا اپ انکریز इंक्रीज इनक्रीज",
    "decrease": "down decrease lower reduce dim kam कम کم डाउन घटाओ ڈاؤن ڈاون ڈیکریز ڈکریز डिक्रीज डीक्रीज",
    "on": "on enable chalu آن ان ऑन चालू", "off": "off disable band بند آف اف ऑफ बंद",
    "mute": "mute म्यूट میوٹ میُوٹ", "unmute": "unmute अनम्यूट انمیوٹ",
    "get": "get status check batao", "%": "percent percentage فیصد پرسنٹ प्रतिशत",
}
ALIASES = {unicodedata.normalize("NFKC", word).replace("़", ""): key for key, values in GROUPS.items() for word in values.split()}
FILLERS = set("please kindly meri mera mere my the turn set to by kar karo kardo do dein dena kr kijiye zara thora thodi sa ki ko ka ke mein me is it ji uh um erm laptop pc computer screen display मेरा मेरी मेरे मुझे कर करो दो दीजिए में को का के जरा थोड़ा پلیز میری میرا مجھے کر کرو دو دیں ذرا پی سی کی کا کے کو کمپیوٹر لیپ ٹاپ".split())
NEGATIONS = {"not", "no", "never", "dont", "don't", "nahi", "nahin", "mat", "नहीं", "मत", "نہیں", "مت"}


_END_PUNCTUATION = re.compile(r"[\s.,;:!?،۔।؟…]+$")


def strip_end_punctuation(text):
    """Drop the sentence punctuation a speech-to-text service adds ("... in Documents.") before the command parsers see it."""
    stripped = _END_PUNCTUATION.sub("", (text or "").strip())
    return stripped or (text or "").strip()


_ARTICLE_AFTER_VERB = re.compile(
    r"^((?:please\s+|can you\s+|could you\s+)?(?:open|launch|start|run|close|go to|show|switch to|play|find|delete|remove|"
    r"copy|move|rename|zip|unzip|extract|read|list)\s+)(?:the|a|an)\s+", re.I)
_ARTICLE_BEFORE_VERB = re.compile(r"^(?:the|a|an)\s+(?=\S+(?:\s+\S+)?\s+(?:kholo|khol|band|chalao|chalu|karo|kar do)$)", re.I)


def strip_leading_article(text):
    """"open the youtube" -> "open youtube": people say "the" all the time and no app or site is called "the youtube"."""
    text = (text or "").strip()
    return _ARTICLE_BEFORE_VERB.sub("", _ARTICLE_AFTER_VERB.sub(lambda m: m.group(1), text, count=1), count=1)


def clean_transcript(text):
    """What the command parsers get from a spoken or typed command: no final punctuation, no article after the verb."""
    return strip_leading_article(strip_end_punctuation(text))


def normalize_speech(text):
    text = unicodedata.normalize("NFKC", text).casefold().replace("़", "")
    for pattern, replacement in [
        (r"\bvol\s+ume\b", "volume"), (r"\bbright\s+ness\b", "brightness"),
        (r"\bblue\s+tooth\b", "bluetooth"), (r"\bin\s+crease\b", "increase"),
        (r"\bde\s+crease\b", "decrease"), (r"\bv\s+s\s+code\b", "vs code"),
        (r"\bun\s+mute\b", "unmute"), (r"\bwi[\s-]+fi\b", "wifi"),
        (r"وائی\s+فائی", "wifi"), (r"वाई\s*फाई", "wifi"),
        (r"(?<!\w)ان\s+میوٹ(?!\w)", "unmute"),
        (r"(?<!\w)بلو\s+(?:ت|ٹوتھ|توتھ)(?!\w)", "bluetooth"),
    ]:
        text = re.sub(pattern, replacement, text)
    # Preserve script combining marks (Hindi/Urdu vowels), unlike a plain \w regex.
    return " ".join("".join(c if c.isalnum() or unicodedata.category(c).startswith("M") or c in "%'" else " " for c in text).replace("%", " % ").split())


def local_plan(command):
    from .settings_commands import settings_plan
    page = settings_plan(command)
    if page is not None:
        return page
    if re.search(r"[-−]\s*\d", command):
        return None
    text = normalize_speech(command)
    words = text.split()
    if any(word in NEGATIONS for word in words):
        return None
    mapped = []
    for word in words:
        if word in FILLERS:
            continue
        canonical = ALIASES.get(word)
        if canonical is None and word.isascii() and word.isalpha() and len(word) >= 5:
            matches = get_close_matches(word, ["volume", "brightness", "bluetooth"], n=2, cutoff=0.84)
            if len(matches) == 1:
                canonical = matches[0]
        canonical = canonical or word
        if not mapped or mapped[-1] != canonical:
            mapped.append(canonical)
    settings = [w for w in mapped if w in {"volume", "brightness", "bluetooth", "wifi", "theme"}]
    # Treat close/open as radio actions only in a short explicit radio command.
    # Never apply this substitution to application or filesystem target names.
    if len(settings) == 1 and settings[0] in {"bluetooth", "wifi"}:
        mapped = [word for word in mapped if word != "ٹرن"]
        if mapped and mapped[0] in {"ہیں", "hey"}:
            mapped = mapped[1:]
        radio_verbs = {"close": "off", "کلوز": "off", "क्लोज": "off",
                       "open": "on", "اوپن": "on", "ओपन": "on"}
        mapped = [radio_verbs.get(word, word) for word in mapped]
    actions = [w for w in mapped if w in {"increase", "decrease", "on", "off", "mute", "unmute", "get", "dark", "light"}]
    if any(w in {"dark", "light"} for w in mapped) and not settings:
        settings = ["theme"]
    if not settings and mapped in (["mute"], ["unmute"]):
        settings = ["volume"]
    if len(settings) == 1:
        setting = settings[0]
        numbers = [w for w in mapped if w.isdecimal()]
        allowed = {setting, "mode", "%", *actions, *numbers}
        if any(w not in allowed for w in mapped) or len(actions) > 1 or len(numbers) > 1:
            return None
        action = actions[0] if actions else ("set" if numbers else None)
        if action is None:
            return Plan(note=f"{setting} ke liye kya karna hai? Kam/zyada, on/off ya exact value bata dein.")
        supported = {"volume": {"get", "set", "increase", "decrease", "mute", "unmute", "off", "on"},
                     "brightness": {"get", "set", "increase", "decrease"},
                     "bluetooth": {"get", "on", "off"}, "wifi": {"get", "on", "off"}, "theme": {"get", "dark", "light"}}
        if action not in supported[setting] or (numbers and action not in {"set", "increase", "decrease"}):
            return None
        if setting == "volume" and action in {"off", "on"}:
            action = "mute" if action == "off" else "unmute"
        args = {"setting": setting, "action": action}
        if numbers:
            value = int(numbers[0])
            if value > 100 or (action in {"increase", "decrease"} and value == 0):
                return None
            args["value"] = value
        elif action in {"increase", "decrease"}:
            args["value"] = 10
        return Plan(actions=[PlanAction(tool="manage_system_setting", args=args)], note="Local command; API quota use nahi hua.")
    from ..tools.apps import APP_ALIASES, APP_TARGETS
    apps = set(APP_ALIASES) | set(APP_TARGETS) | {"whatsapp", "visual studio code"}
    match = re.fullmatch(r"(?:open|kholo|खोलो|کھولو)\s+(.+)", text)
    if not match:
        match = re.fullmatch(r"(.+?)\s+(?:kholo|khol do|खोल दो|کھول دو)", text)
    if match:
        app = " ".join(dict.fromkeys(match.group(1).split()))
        if app in apps:
            return Plan(actions=[PlanAction(tool="open_application", args={"app_name": app})], note="Local command; API quota use nahi hua.")
        if app in {"code x", "codex", "agent x"}:
            return Plan(note="Code X se aap Agent X AI keh rahi hain ya VS Code? App ka naam confirm kar dein.")
    return None
