"""M4 settings: follow-up window and speech volume are bounded and persisted."""
from __future__ import annotations

import pytest


def test_defaults(client):
    body = client.get("/preferences").json()
    assert body["follow_up_seconds"] == 8 and body["speech_volume"] == 100


def test_saved_and_read_back(client):
    got = client.put("/preferences", json={"follow_up_seconds": 0, "speech_volume": 40}).json()
    assert got["follow_up_seconds"] == 0 and got["speech_volume"] == 40
    assert client.get("/preferences").json()["speech_volume"] == 40
    client.put("/preferences", json={"follow_up_seconds": 8, "speech_volume": 100})


@pytest.mark.parametrize("payload", [{"follow_up_seconds": 16}, {"follow_up_seconds": -1}, {"speech_volume": 101}, {"speech_volume": -5}])
def test_out_of_range_is_rejected(client, payload):
    assert client.put("/preferences", json=payload).status_code == 422
