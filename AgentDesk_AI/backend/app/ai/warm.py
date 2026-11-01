"""Keep the HTTPS connection to Groq open so a command does not pay for a new TLS handshake (150-400 ms).

Pings a no-token endpoint every 50 s while the app was used in the last 30 minutes; an idle PC makes no requests at all.
"""
from __future__ import annotations

import threading
import time

PING_SECONDS = 50
IDLE_STOP_SECONDS = 1800
_started = False
_lock = threading.Lock()


def start(client=None, interval: float = PING_SECONDS) -> bool:
    """Warm now and keep warm. False when there is no Groq key (nothing to warm)."""
    global _started
    from ..config import get_settings
    if client is None:
        if not get_settings().groq_configured:
            return False
        from .groq_client import get_groq_client
        client = get_groq_client()
    with _lock:
        if _started:
            return True
        _started = True

    def loop() -> None:
        client.last_used = time.monotonic()
        while True:
            if time.monotonic() - client.last_used < IDLE_STOP_SECONDS:
                client.warm()
            time.sleep(interval)

    threading.Thread(target=loop, name="groq-warm", daemon=True).start()
    return True
