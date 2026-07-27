"""Regression tests for playlist parsing and the retry.

The case that matters is the one seen in the wild: Claude returned complete,
valid JSON carrying playlist_name and vibe_note and no tracks at all, so the
whole request 502'd. Prompting cannot rule that out, so the schema is enforced
by the API and one retry covers the rest.

    python -m tests.test_playlist
"""

import json
import sys
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from backend import main

# Verbatim from the failing run.
NO_TRACKS = (
    '{"playlist_name": "Everything Off the Floor", "vibe_note": "Starts loose '
    'and groove-first, hardens into repetition for the tedious middle hours, '
    'snaps awake for the last push, then lets you sit down in a clean room."}'
)

GOOD = json.dumps({
    "playlist_name": "Everything Off the Floor",
    "vibe_note": "An arc.",
    "tracks": [
        {"title": f"Song {i}", "artist": f"Artist {i}", "reason": "Because."}
        for i in range(12)
    ],
})

FENCED = f"```json\n{GOOD}\n```"

def playlist(n: int, name: str = "Thin") -> str:
    return json.dumps({
        "playlist_name": name,
        "vibe_note": "An arc.",
        "tracks": [
            {"title": f"Song {i}", "artist": f"Artist {i}", "reason": "Because."}
            for i in range(n)
        ],
    })


ONE_TRACK = playlist(1)
FOUR_TRACKS = playlist(4)


@dataclass
class Block:
    text: str
    type: str = "text"


@dataclass
class Usage:
    output_tokens: int = 1234


@dataclass
class Response:
    content: list
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        # Repeat the last reply rather than raising, so an unexpected extra
        # call shows up as a failed assertion instead of an IndexError.
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return Response(content=[Block(reply)])


class FakeClient:
    def __init__(self, replies):
        self.messages = FakeMessages(replies)


def run(replies, length="medium"):
    main._client = FakeClient(replies)
    with TestClient(main.app) as client:
        res = client.post(
            "/api/playlist",
            json={"situation": "cleaning the entire apartment",
                  "length": length, "vibe": 50},
        )
    return res, main._client.messages


def main_() -> int:
    # 1. The exact live failure, recovered on the retry.
    res, msgs = run([NO_TRACKS, GOOD])
    assert res.status_code == 200, res.text
    assert len(res.json()["tracks"]) == 12, res.json()
    assert msgs.calls == 2, msgs.calls
    print("[ ok ] a response with no tracks is retried, not surfaced as an error")

    # 2. The schema is actually sent, so the API can reject that shape upstream.
    fmt = msgs.last_kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema", fmt
    assert "tracks" in fmt["schema"]["required"], fmt["schema"]["required"]
    print("[ ok ] tracks is a required field in the schema sent to the API")

    # 3. Two bad responses give up rather than looping.
    res, msgs = run([NO_TRACKS, NO_TRACKS])
    assert res.status_code == 502, res.status_code
    assert msgs.calls == 2, msgs.calls
    print("[ ok ] two failures stop at two calls and return a clear 502")

    # 4. Fences are still stripped, since the model may add them anyway.
    res, msgs = run([FENCED])
    assert res.status_code == 200, res.text
    assert msgs.calls == 1, msgs.calls
    print("[ ok ] a fenced response still parses on the first call")

    # 5. A near-empty playlist is retried.
    res, msgs = run([ONE_TRACK, GOOD])
    assert res.status_code == 200 and len(res.json()["tracks"]) == 12
    assert msgs.calls == 2
    print("[ ok ] 1 track out of 12 is retried")

    # 6. Slightly short is still a playlist. Do not spend a second call on it.
    res, msgs = run([FOUR_TRACKS], length="short")
    assert res.status_code == 200, res.text
    assert len(res.json()["tracks"]) == 4, res.json()
    assert msgs.calls == 1, msgs.calls
    print("[ ok ] 4 tracks out of 6 is returned as-is, without a second call")

    # 7. Never more than asked for.
    res, msgs = run([playlist(30)], length="short")
    assert len(res.json()["tracks"]) == 6, len(res.json()["tracks"])
    print("[ ok ] an over-long playlist is trimmed to the requested length")

    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
