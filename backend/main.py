"""Hype Playlist backend.

Two jobs: hold the API key and serve the static frontend. All of the
personality lives in static/ and in backend/prompts.py.
"""

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

TRACK_COUNTS = {"short": 6, "medium": 12, "long": 20}

app = FastAPI(title="Hype Playlist", docs_url=None, redoc_url=None)


class PlaylistRequest(BaseModel):
    situation: str = Field(min_length=1, max_length=400)
    length: Literal["short", "medium", "long"] = "medium"
    vibe: int = Field(default=50, ge=0, le=100)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/playlist")
def create_playlist(req: PlaylistRequest) -> dict:
    """Stubbed for now. Wired to Claude in the next step."""
    count = TRACK_COUNTS[req.length]
    stub = [
        ("Nightcall", "Kavinsky", "Nothing has happened yet and it already sounds inevitable."),
        ("Bad Habit", "Steve Lacy", "Loose enough to unclench your jaw on the way in."),
        ("Seventeen", "Sharon Van Etten", "Builds for ninety seconds before it goes anywhere, which is the point."),
        ("Can You Feel It", "Mr. Fingers", "Keeps your pulse flat while everything else speeds up."),
        ("Deceptacon", "Le Tigre", "The tonal left turn. One song has to refuse to be reasonable."),
        ("Green Aphrodisiac", "Corinne Bailey Rae", "Lands you back on the ground without letting the air out."),
    ]
    tracks = [
        {"title": t, "artist": a, "reason": r}
        for t, a, r in (stub * ((count // len(stub)) + 1))[:count]
    ]
    return {
        "playlist_name": "Composure, Borrowed",
        "vibe_note": f"Stubbed arc for '{req.situation}' at vibe {req.vibe}.",
        "tracks": tracks,
    }


# Mounted last so /api/* and / are matched first.
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
