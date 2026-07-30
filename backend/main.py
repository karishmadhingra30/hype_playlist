"""Hype Playlist backend.

Two jobs: hold the API key and serve the static frontend. All of the
personality lives in static/ and in backend/prompts.py.
"""

import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Literal

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import spotify
from .prompts import (
    PLAYLIST_SCHEMA,
    SYSTEM_PROMPT,
    TRACK_COUNTS,
    build_user_prompt,
)

load_dotenv()

log = logging.getLogger("hype")
logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MODEL = "claude-opus-5"

# One retry. A response that parses but carries no tracks is recoverable,
# and it is cheaper to ask again than to hand the user a dead button.
MAX_ATTEMPTS = 2

app = FastAPI(title="Hype Playlist", docs_url=None, redoc_url=None)
SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    log.warning(
        "SESSION_SECRET is not set, so a random one is used and every restart "
        "disconnects Spotify. Put any random string in .env to keep it."
    )

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET or secrets.token_hex(32),
    same_site="lax",
    https_only=False,
)

if not spotify.is_configured():
    log.info(
        "SPOTIFY_CLIENT_ID is not set. Playlist generation works; the export "
        "button stays hidden."
    )

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(
                status_code=503,
                detail="ANTHROPIC_API_KEY is not set. Add it to .env and restart.",
            )
        _client = anthropic.Anthropic()
    return _client


class Track(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    artist: str = Field(min_length=1, max_length=300)
    reason: str = ""


class PlaylistRequest(BaseModel):
    situation: str = Field(min_length=1, max_length=400)
    length: Literal["short", "medium", "long"] = "medium"
    vibe: int = Field(default=50, ge=0, le=100)


class ExportRequest(BaseModel):
    playlist_name: str = Field(min_length=1, max_length=200)
    vibe_note: str = ""
    tracks: list[Track] = Field(min_length=1, max_length=50)


def extract_json(raw: str) -> dict:
    """Claude is asked for bare JSON. Sometimes it fences it anyway."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: the outermost braces in whatever came back.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(text[start : end + 1])


def clean_payload(data: dict, wanted: int) -> dict:
    """Keep only well-formed tracks and cap the list at the requested length."""
    tracks = []
    for item in data.get("tracks") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        artist = str(item.get("artist") or "").strip()
        if not title or not artist:
            continue
        tracks.append(
            {
                "title": title,
                "artist": artist,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    if not tracks:
        raise ValueError("no usable tracks in response")
    return {
        "playlist_name": str(data.get("playlist_name") or "Untitled Playlist").strip(),
        "vibe_note": str(data.get("vibe_note") or "").strip(),
        "tracks": tracks[:wanted],
    }


def describe_failure(attempt: int, response, raw: str, reason: str) -> None:
    """Log enough to tell a truncation apart from a well-formed wrong answer."""
    usage = getattr(response, "usage", None)
    log.warning(
        "playlist attempt %s/%s failed: %s | stop_reason=%s | "
        "output_tokens=%s | chars=%s",
        attempt,
        MAX_ATTEMPTS,
        reason,
        getattr(response, "stop_reason", "?"),
        getattr(usage, "output_tokens", "?"),
        len(raw),
    )
    log.warning("full response text: %s", raw or "<empty>")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/playlist")
def create_playlist(req: PlaylistRequest) -> dict:
    client = get_client()
    wanted = TRACK_COUNTS[req.length]
    prompt = build_user_prompt(req.situation, req.length, req.vibe)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": PLAYLIST_SCHEMA},
                },
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise HTTPException(status_code=503, detail="Anthropic rejected the API key.")
        except anthropic.RateLimitError:
            raise HTTPException(status_code=429, detail="Rate limited. Try again shortly.")
        except anthropic.APIError as exc:
            raise HTTPException(status_code=502, detail=f"Anthropic call failed: {exc}")

        raw = "".join(b.text for b in response.content if b.type == "text")

        try:
            data = clean_payload(extract_json(raw), wanted)
        except (ValueError, json.JSONDecodeError) as exc:
            describe_failure(attempt, response, raw, str(exc))
            continue

        # A handful of tracks when twenty were asked for is a bad playlist,
        # not a good short one. Worth one more try.
        if len(data["tracks"]) * 2 < wanted and attempt < MAX_ATTEMPTS:
            describe_failure(
                attempt, response, raw,
                f"only {len(data['tracks'])} of {wanted} tracks",
            )
            continue

        if len(data["tracks"]) < wanted:
            log.info("returning %s tracks, %s were asked for",
                     len(data["tracks"]), wanted)
        return data

    raise HTTPException(
        status_code=502,
        detail="Claude returned something that was not a playlist. Try again.",
    )


# ---------- Spotify (optional) ----------


def require_spotify() -> None:
    if not spotify.is_configured():
        raise HTTPException(status_code=404, detail="Spotify export is not configured.")


@app.get("/api/spotify/status")
def spotify_status(request: Request) -> dict:
    token = request.session.get("spotify_token") or {}
    return {
        "configured": spotify.is_configured(),
        "connected": bool(token),
        # What the connection can actually do, so a scope problem is visible
        # rather than showing up later as a 403.
        "granted_scope": token.get("scope", ""),
        "missing_scopes": spotify.missing_scopes(token) if token else [],
        "account": request.session.get("spotify_account") or {},
    }


@app.get("/api/spotify/login")
def spotify_login(request: Request) -> RedirectResponse:
    require_spotify()
    state = secrets.token_urlsafe(16)
    request.session["spotify_state"] = state
    return RedirectResponse(spotify.authorize_url(state))


@app.get("/api/spotify/callback")
def spotify_callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    require_spotify()
    if error:
        return RedirectResponse("/?spotify=denied")
    expected = request.session.pop("spotify_state", None)
    if not code or not state or state != expected:
        return RedirectResponse("/?spotify=badstate")
    try:
        token = spotify.exchange_code(code)
    except spotify.SpotifyError as exc:
        log.warning("Spotify token exchange failed: %s", exc)
        return RedirectResponse("/?spotify=failed")

    request.session["spotify_token"] = token

    # Record who authorized. When the app is in development mode this is the
    # value Spotify matches against the dashboard's user list.
    try:
        profile = spotify.fetch_profile(token)
        request.session["spotify_account"] = {
            "email": profile.get("email", ""),
            "display_name": profile.get("display_name", ""),
            "id": profile.get("id", ""),
        }
        log.info("Spotify connected as %s", spotify.identify(profile))
    except spotify.SpotifyError as exc:
        log.warning("connected but could not read the profile: %s", exc)

    lacking = spotify.missing_scopes(token)
    log.info("Granted scope: %r", token.get("scope", ""))
    if lacking:
        log.warning(
            "Spotify did not grant %s. Creating a playlist will fail with 403.",
            ", ".join(lacking),
        )
    return RedirectResponse("/?spotify=connected")


@app.get("/api/spotify/probe")
def spotify_probe(request: Request) -> dict:
    """Diagnostic. Open in a browser while connected to see what Spotify allows."""
    require_spotify()
    token = request.session.get("spotify_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not connected to Spotify.")
    try:
        token = spotify.ensure_fresh(token)
        request.session["spotify_token"] = token
        report = spotify.probe(token)
    except spotify.SpotifyError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    log.warning("Spotify probe: %s", report.get("verdict"))
    for entry in report.get("attempts", []):
        log.warning("  %s -> %s %s", entry["attempt"], entry["status"],
                    entry.get("body", ""))
    return report


@app.post("/api/spotify/create")
def spotify_create(request: Request, req: ExportRequest) -> dict:
    require_spotify()
    token = request.session.get("spotify_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not connected to Spotify.")

    try:
        token = spotify.ensure_fresh(token)
        request.session["spotify_token"] = token
        return spotify.create_playlist(
            token,
            req.playlist_name,
            req.vibe_note or "Made by Hype Playlist.",
            [t.model_dump() for t in req.tracks],
        )
    except spotify.SpotifyError as exc:
        # An expired refresh token is the common case; make them reconnect.
        request.session.pop("spotify_token", None)
        request.session.pop("spotify_account", None)
        raise HTTPException(status_code=502, detail=str(exc))


# Mounted last so /api/* and / are matched first.
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
