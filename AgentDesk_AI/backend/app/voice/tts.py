"""Text-to-speech with fallbacks: Groq Orpheus (English) -> edge-tts (Urdu/Hindi, and English backup) -> Windows SAPI.

Each engine is a callable `(text, voice) -> (audio_bytes, mime)` that raises on failure, so the chain
(and the tests) never depend on a particular service. A failing engine is skipped for `COOLDOWN` seconds
so one unavailable model (e.g. Orpheus terms not accepted) costs one slow call, not one per sentence.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import platform
import queue
import subprocess
import tempfile
import threading
import time
import wave
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .chunking import ORPHEUS_LIMIT, split_for_speech

log = logging.getLogger(__name__)

COOLDOWN = 300.0
CACHE_SIZE = 64   # repeated phrases ("Done. Chrome is open.") are served without a new synthesis
Engine = Callable[[str, str], tuple[bytes, str]]


def is_silent(audio: bytes, mime: str) -> bool:
    """A clip with (almost) no sound. SAPI returns an empty WAV, with no error, for text its voices cannot read
    (this PC has only English voices, so every Urdu / Hindi sentence came back as 46 bytes of silence)."""
    if "wav" not in (mime or ""):
        return False
    try:
        with wave.open(io.BytesIO(audio)) as clip:
            return clip.getnframes() < max(1, clip.getframerate() // 50)  # a valid WAV shorter than 20 ms
    except (wave.Error, EOFError):
        return False  # not a WAV we can read: leave the judgement to the player

ORPHEUS_VOICES = {"female": ("autumn", "diana", "hannah"), "male": ("austin", "daniel", "troy")}
DEFAULT_URDU = {"female": "ur-PK-UzmaNeural", "male": "ur-PK-AsadNeural"}
DEFAULT_HINDI = {"female": "hi-IN-SwaraNeural", "male": "hi-IN-MadhurNeural"}
DEFAULT_ENGLISH_EDGE = {"female": "en-US-JennyNeural", "male": "en-US-GuyNeural"}


@dataclass
class VoiceChoice:
    """Which voice to use. `direction` is a 1-2 word Orpheus vocal direction such as 'warm' (no brackets)."""
    language: str = "en"
    gender: str = "female"
    voice_en: str = "diana"
    voice_ur: str | None = None
    voice_hi: str | None = None
    direction: str | None = None

    def edge_voice(self) -> str:
        lang = self.language[:2].lower()
        if lang == "ur":
            return self.voice_ur or DEFAULT_URDU[self.gender]
        if lang == "hi":
            return self.voice_hi or DEFAULT_HINDI[self.gender]
        return DEFAULT_ENGLISH_EDGE[self.gender]


@dataclass
class Clip:
    audio: bytes
    mime: str
    engine: str
    voice: str
    text: str


@dataclass
class TTSChain:
    orpheus: Engine | None = None
    edge: Engine | None = None
    sapi: Engine | None = None
    clock: Callable[[], float] = time.monotonic
    cache: bool = field(default_factory=lambda: os.environ.get("AGENTDESK_TTS_CACHE", "1") != "0")
    _blocked: dict[str, float] = field(default_factory=dict)
    _clips: OrderedDict = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _usable(self, name: str) -> bool:
        with self._lock:
            return self._blocked.get(name, 0.0) <= self.clock()

    def _trip(self, name: str, exc: Exception) -> None:
        log.warning("tts engine %s failed (%s); skipping it for %ds", name, type(exc).__name__, int(COOLDOWN))
        with self._lock:
            self._blocked[name] = self.clock() + COOLDOWN

    def plan(self, choice: VoiceChoice) -> list[tuple[str, Engine | None, str]]:
        """Engines to try, in order, with the voice each one uses."""
        lang = choice.language[:2].lower()
        if lang in ("ur", "hi"):
            return [("edge", self.edge, choice.edge_voice()), ("sapi", self.sapi, choice.gender)]
        return [("orpheus", self.orpheus, choice.voice_en), ("edge", self.edge, choice.edge_voice()),
                ("sapi", self.sapi, choice.gender)]

    def synthesize(self, text: str, choice: VoiceChoice | None = None) -> Clip:
        """One chunk (<= 200 chars) -> audio. Raises RuntimeError only if every engine failed."""
        choice = choice or VoiceChoice()
        errors: list[str] = []
        for name, engine, voice in self.plan(choice):
            key = (name, voice, choice.direction if name == "orpheus" else None, text)
            hit = self._clips.get(key) if self.cache else None
            if hit is not None and engine is not None:
                self._clips.move_to_end(key)
                return hit
            if engine is None or not self._usable(name):
                continue
            payload = text
            if name == "orpheus" and choice.direction:
                payload = f"[{choice.direction}] {text}"[: ORPHEUS_LIMIT]
            try:
                audio, mime = engine(payload, voice)
            except Exception as exc:  # noqa: BLE001 - any engine failure falls through to the next engine
                self._trip(name, exc)
                errors.append(f"{name}: {type(exc).__name__}")
                continue
            if audio and is_silent(audio, mime):
                errors.append(f"{name}: silent")  # not an engine failure: no cool-down, the next engine or the browser voice speaks
                continue
            if audio:
                clip = Clip(audio, mime, name, voice, text)
                if self.cache:
                    with self._lock:
                        self._clips[key] = clip
                        while len(self._clips) > CACHE_SIZE:
                            self._clips.popitem(last=False)
                return clip
        raise RuntimeError("No speech engine produced audio (" + "; ".join(errors or ["none configured"]) + ")")

    def chunks(self, text: str, choice: VoiceChoice | None = None) -> list[str]:
        """The pieces to request, in order; the direction tag is budgeted into Orpheus' 200-char limit."""
        reserve = len(choice.direction) + 3 if choice and choice.direction else 0
        return split_for_speech(text, ORPHEUS_LIMIT - reserve)


# ---------------------------------------------------------------- real engines
def groq_orpheus_engine(client) -> Engine:
    """`client` needs .speak(text, voice=) -> WAV bytes (GroqClient or CloudBrain)."""
    def run(text: str, voice: str) -> tuple[bytes, str]:
        return client.speak(text, voice=voice), "audio/wav"
    return run


def edge_tts_engine() -> Engine:
    import edge_tts  # imported late: optional dependency

    def run(text: str, voice: str) -> tuple[bytes, str]:
        async def fetch() -> bytes:
            parts: list[bytes] = []
            async for item in edge_tts.Communicate(text, voice).stream():
                if item["type"] == "audio":
                    parts.append(item["data"])
            return b"".join(parts)
        return _run_async(fetch(), timeout=15), "audio/mpeg"
    return run


def _run_async(coro, timeout: float):
    """Run a coroutine from sync code, also when called inside a running event loop's worker thread."""
    box: dict = {}

    def target():
        try:
            box["value"] = asyncio.run(asyncio.wait_for(coro, timeout))
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout + 2)
    if "error" in box:
        raise box["error"]
    if "value" not in box:
        raise TimeoutError("speech engine timed out")
    return box["value"]


_SAPI_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
# SelectVoiceByHints ignores the gender on some machines, so pick an installed voice of that gender by name.
$want = $env:AGENTDESK_SAPI_GENDER
$match = $s.GetInstalledVoices() | Where-Object {{ $_.Enabled -and $_.VoiceInfo.Gender.ToString() -eq $want }} | Select-Object -First 1
if ($match) {{ $s.SelectVoice($match.VoiceInfo.Name) }}
$s.SetOutputToWaveFile($env:AGENTDESK_SAPI_OUT)
$s.Speak($env:AGENTDESK_SAPI_TEXT)
$s.Dispose()
"""


# One long-lived PowerShell that keeps System.Speech loaded: starting a new PowerShell for every sentence cost ~0.9 s each
# (the largest part of the reply delay). Requests are one line "gender|base64(out path)|base64(text)"; the text never
# becomes script text. It exits by itself when the server closes its stdin.
_SAPI_WORKER = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$names = @{}
foreach ($g in 'Male', 'Female') {
  $m = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Gender.ToString() -eq $g } | Select-Object -First 1
  if ($m) { $names[$g] = $m.VoiceInfo.Name }
}
[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()
while ($null -ne ($line = [Console]::In.ReadLine())) {
  try {
    $parts = $line.Split('|')
    $out = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($parts[1]))
    $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($parts[2]))
    if ($names.ContainsKey($parts[0])) { $s.SelectVoice($names[$parts[0]]) }
    $s.SetOutputToWaveFile($out)
    $s.Speak($text)
    $s.SetOutputToNull()
    [Console]::Out.WriteLine('OK')
  } catch {
    [Console]::Out.WriteLine('ERR ' + $_.Exception.Message.Replace("`r", ' ').Replace("`n", ' '))
  }
  [Console]::Out.Flush()
}
"""


class SapiWorker:
    """Talks to the persistent PowerShell above. Any problem raises, and `sapi_engine` then uses the one-shot path."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()

    def _reader(self, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stdout.readline, b""):
            self._lines.put(raw.decode("utf-8", "replace").strip())
        self._lines.put("EOF")

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def _start(self, timeout: float = 40.0) -> None:
        self._lines = queue.Queue()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SAPI_WORKER], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
        threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()
        try:
            if self._lines.get(timeout=timeout) != "READY":
                raise RuntimeError("the SAPI worker did not start")
        except queue.Empty as exc:
            self._stop()
            raise TimeoutError("the SAPI worker did not start in time") from exc

    def warm(self) -> None:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()

    def speak(self, text: str, gender: str, timeout: float = 20.0) -> bytes:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "speech.wav"
                b64 = lambda value: base64.b64encode(value.encode("utf-8")).decode("ascii")  # noqa: E731
                try:
                    self._proc.stdin.write(f"{gender}|{b64(str(out))}|{b64(text)}\n".encode("ascii"))
                    self._proc.stdin.flush()
                    reply = self._lines.get(timeout=timeout)
                except (OSError, queue.Empty) as exc:
                    self._stop()
                    raise RuntimeError("the SAPI worker did not answer") from exc
                if reply != "OK":
                    if reply == "EOF":
                        self._stop()
                    raise RuntimeError(f"the SAPI worker failed: {reply[:120]}")
                return out.read_bytes()

    def close(self) -> None:
        with self._lock:
            self._stop()


SAPI_WORKER = SapiWorker()


def warm_sapi() -> None:
    """Start the persistent voice in the background (called once when the server starts) so the first reply is not slow."""
    if platform.system() != "Windows" or os.environ.get("AGENTDESK_SAPI_WORKER", "1") == "0":
        return
    threading.Thread(target=lambda: _quiet(SAPI_WORKER.warm), daemon=True).start()


def _quiet(call) -> None:
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - warming is best effort
        log.warning("SAPI worker could not start (%s); each sentence will start its own PowerShell", type(exc).__name__)


def sapi_engine() -> Engine:
    """Offline Windows voice. Text goes through the persistent worker (base64), or environment variables on the one-shot path."""
    def run(text: str, voice: str) -> tuple[bytes, str]:
        if platform.system() != "Windows":
            raise RuntimeError("SAPI is only available on Windows")
        gender = "Male" if str(voice).lower() == "male" else "Female"
        if os.environ.get("AGENTDESK_SAPI_WORKER", "1") != "0":
            try:
                return SAPI_WORKER.speak(text, gender), "audio/wav"
            except Exception as exc:  # noqa: BLE001 - fall back to the slower one-shot PowerShell below
                log.warning("SAPI worker failed (%s: %s); using a one-shot PowerShell", type(exc).__name__, str(exc)[:80])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "speech.wav"
            env = {**os.environ, "AGENTDESK_SAPI_OUT": str(out), "AGENTDESK_SAPI_TEXT": text, "AGENTDESK_SAPI_GENDER": gender}
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SAPI_SCRIPT.format()],
                           env=env, check=True, capture_output=True, timeout=20)
            return out.read_bytes(), "audio/wav"
    return run


def default_chain(client=None) -> TTSChain:
    """The chain for this machine. `client` is the Groq client or cloud brain used for Orpheus."""
    edge = None
    try:
        edge = edge_tts_engine()
    except ImportError:
        pass
    return TTSChain(orpheus=groq_orpheus_engine(client) if client is not None else None, edge=edge,
                    sapi=sapi_engine() if platform.system() == "Windows" else None)
