"""End-to-end check of the Spotify export against a stand-in for the Web API.

Spotify cannot be called from CI, and the parts most likely to break quietly
are the ones that decide whether a track counts as found. So this runs the
real OAuth round trip and the real export against a mock that speaks the same
protocol, and asserts on what the user is told at the end.

    python -m tests.test_spotify_flow
"""

import os
import threading
import time
from urllib.parse import parse_qs

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

MOCK_PORT = 8765
APP_PORT = 8766

# ---------- the stand-in ----------

mock = FastAPI()


@mock.get("/authorize")
def authorize(request: Request):
    """Spotify would show a consent screen. Approve immediately."""
    q = request.query_params
    return RedirectResponse(f"{q['redirect_uri']}?code=REALCODE&state={q['state']}")


@mock.post("/api/token")
async def token(request: Request):
    if not request.headers.get("Authorization", "").startswith("Basic "):
        return JSONResponse({"error": "no basic auth"}, status_code=401)
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    return {
        "access_token": "tok_" + form.get("grant_type", "?"),
        "refresh_token": "refresh_abc",
        "token_type": "Bearer",
        "scope": "playlist-modify-private",
        "expires_in": 3600,
    }


@mock.get("/v1/me")
def me():
    return {"id": "tester", "display_name": "Tester"}


# Field-scoped queries that resolve. Nightcall (Reprise) deliberately maps to
# the same URI as Nightcall, to prove a duplicate is not reported as a miss.
CATALOG = {
    'track:"Nightcall" artist:"Kavinsky"': ("Kavinsky", "spotify:track:aaa"),
    'track:"Bad Habit" artist:"Steve Lacy"': ("Steve Lacy", "spotify:track:bbb"),
    'track:"Nightcall (Reprise)" artist:"Kavinsky"': ("Kavinsky", "spotify:track:aaa"),
}

# The loose fallback query returns a plausible but WRONG artist. The app must
# refuse it rather than quietly export a cover version.
LOOSE_WRONG = {"Seventeen Sharon Van Etten": ("Some Cover Band", "spotify:track:zzz")}

RECEIVED: dict = {"uris": [], "playlist": None}


@mock.get("/v1/search")
def search(q: str, type: str = "track", limit: int = 1):
    hit = CATALOG.get(q) or LOOSE_WRONG.get(q)
    if not hit:
        return {"tracks": {"items": []}}
    artist, uri = hit
    return {"tracks": {"items": [{"uri": uri, "name": "x",
                                  "artists": [{"name": artist}]}]}}


@mock.post("/v1/users/{user_id}/playlists")
async def create(user_id: str, request: Request):
    RECEIVED["playlist"] = await request.json()
    return JSONResponse(
        {
            "id": "pl123",
            "external_urls": {"spotify": "https://open.spotify.com/playlist/pl123"},
        },
        status_code=201,
    )


@mock.post("/v1/playlists/{pid}/tracks")
async def add(pid: str, request: Request):
    RECEIVED["uris"].extend((await request.json())["uris"])
    return JSONResponse({"snapshot_id": "snap"}, status_code=201)


# ---------- harness ----------

def serve(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError(f"server on {port} did not start")


TRACKS = [
    {"title": "Nightcall", "artist": "Kavinsky", "reason": "a"},
    {"title": "Bad Habit", "artist": "Steve Lacy", "reason": "b"},
    {"title": "Seventeen", "artist": "Sharon Van Etten", "reason": "c"},
    {"title": "Nightcall (Reprise)", "artist": "Kavinsky", "reason": "d"},
    {"title": "A Song That Does Not Exist", "artist": "Nobody At All", "reason": "e"},
]


def main() -> None:
    os.environ.update(
        SPOTIFY_CLIENT_ID="testid",
        SPOTIFY_CLIENT_SECRET="testsecret",
        SPOTIFY_REDIRECT_URI=f"http://127.0.0.1:{APP_PORT}/api/spotify/callback",
        SESSION_SECRET="test-session-secret",
    )

    from backend import spotify
    spotify.AUTH_URL = f"http://127.0.0.1:{MOCK_PORT}/authorize"
    spotify.TOKEN_URL = f"http://127.0.0.1:{MOCK_PORT}/api/token"
    spotify.API = f"http://127.0.0.1:{MOCK_PORT}/v1"

    from backend.main import app

    serve(mock, MOCK_PORT)
    serve(app, APP_PORT)

    base = f"http://127.0.0.1:{APP_PORT}"
    # A real cookie jar, because the token lives in the session cookie.
    with httpx.Client(base_url=base, follow_redirects=True, timeout=20) as client:
        status = client.get("/api/spotify/status").json()
        assert status == {"configured": True, "connected": False}, status

        # Forged state must be refused before any token exchange.
        client.get("/api/spotify/login")
        forged = client.get(
            "/api/spotify/callback", params={"code": "x", "state": "wrong"}
        )
        assert forged.url.params.get("spotify") == "badstate", forged.url

        # The real round trip.
        landed = client.get("/api/spotify/login")
        assert landed.url.params.get("spotify") == "connected", landed.url
        assert client.get("/api/spotify/status").json()["connected"] is True

        result = client.post(
            "/api/spotify/create",
            json={"playlist_name": "Composure, Borrowed",
                  "vibe_note": "an arc", "tracks": TRACKS},
        )
        assert result.status_code == 200, result.text
        body = result.json()

    assert body["added"] == 2, body
    assert body["requested"] == 5, body
    assert body["missed"] == [
        "Seventeen - Sharon Van Etten",
        "A Song That Does Not Exist - Nobody At All",
    ], body
    assert body["duplicates"] == ["Nightcall (Reprise) - Kavinsky"], body
    assert body["url"] == "https://open.spotify.com/playlist/pl123", body

    # The wrong-artist result must never reach the playlist.
    assert "spotify:track:zzz" not in RECEIVED["uris"], RECEIVED["uris"]
    assert RECEIVED["uris"] == ["spotify:track:aaa", "spotify:track:bbb"], RECEIVED
    assert RECEIVED["playlist"]["public"] is False, RECEIVED["playlist"]

    print("added        ", body["added"], "of", body["requested"])
    print("missed       ", body["missed"])
    print("duplicates   ", body["duplicates"])
    print("uris exported", RECEIVED["uris"])
    print("private      ", RECEIVED["playlist"]["public"] is False)
    print("\nall assertions passed")


if __name__ == "__main__":
    main()
