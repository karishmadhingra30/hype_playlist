"""The Spotify OAuth dance and playlist creation.

Everything here is optional. If SPOTIFY_CLIENT_ID is not set, is_configured()
returns False and main.py never routes anything here.
"""

import base64
import os
import re
import time
from typing import Iterable

import httpx

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = ["playlist-modify-private", "playlist-modify-public"]
SCOPE = " ".join(SCOPES)

TIMEOUT = httpx.Timeout(15.0)


class SpotifyError(RuntimeError):
    pass


def _detail(res: httpx.Response) -> str:
    """Spotify puts a readable reason in the body. Use it."""
    try:
        body = res.json()
    except ValueError:
        return res.text[:200].strip() or "no detail"
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or body)[:200]


def client_id() -> str | None:
    return os.environ.get("SPOTIFY_CLIENT_ID") or None


def client_secret() -> str | None:
    return os.environ.get("SPOTIFY_CLIENT_SECRET") or None


def redirect_uri() -> str:
    return os.environ.get(
        "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/api/spotify/callback"
    )


def is_configured() -> bool:
    return bool(client_id() and client_secret())


def _basic_auth() -> str:
    raw = f"{client_id()}:{client_secret()}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def authorize_url(state: str) -> str:
    params = httpx.QueryParams(
        {
            "client_id": client_id(),
            "response_type": "code",
            "redirect_uri": redirect_uri(),
            "scope": SCOPE,
            "state": state,
            "show_dialog": "true",
        }
    )
    return f"{AUTH_URL}?{params}"


def _token_request(data: dict) -> dict:
    with httpx.Client(timeout=TIMEOUT) as http:
        res = http.post(
            TOKEN_URL,
            data=data,
            headers={
                "Authorization": _basic_auth(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    if res.status_code != 200:
        raise SpotifyError(f"token exchange failed ({res.status_code}): {_detail(res)}")
    payload = res.json()
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        # What Spotify actually granted, which is not always what was asked
        # for. Dropping this field is what made a 403 hard to diagnose.
        "scope": payload.get("scope", ""),
        # 60s of slack so a token cannot expire mid-export.
        "expires_at": time.time() + int(payload.get("expires_in", 3600)) - 60,
    }


def exchange_code(code: str) -> dict:
    return _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
        }
    )


def refresh(token: dict) -> dict:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise SpotifyError("no refresh token")
    fresh = _token_request(
        {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )
    # Spotify does not always return a new refresh token; keep the old one.
    if not fresh.get("refresh_token"):
        fresh["refresh_token"] = refresh_token
    return fresh


def ensure_fresh(token: dict) -> dict:
    if token.get("expires_at", 0) > time.time():
        return token
    return refresh(token)


def missing_scopes(token: dict) -> list[str]:
    """Scopes we asked for that this token was not actually granted."""
    granted = set((token.get("scope") or "").split())
    return [s for s in SCOPES if s not in granted]


def _headers(token: dict) -> dict:
    return {"Authorization": f"Bearer {token['access_token']}"}


def _scrub(value: str) -> str:
    """Strip characters that break Spotify's field-scoped query syntax."""
    return re.sub(r'["\\:]', " ", value).strip()


def _artists_match(query_artist: str, found_artists: Iterable[str]) -> bool:
    a = query_artist.lower().strip()
    for name in found_artists:
        n = name.lower().strip()
        if a in n or n in a:
            return True
    return False


def search_track(http: httpx.Client, token: dict, title: str, artist: str) -> str | None:
    """Return a track URI, or None. Never returns a different song on purpose.

    The scoped query is tried first. The loose query is only accepted when the
    artist on the result actually matches what we asked for, so a near-miss
    comes back as a miss rather than as a substitution.
    """
    clean_title, clean_artist = _scrub(title), _scrub(artist)
    if not clean_title:
        return None
    attempts = [
        (f'track:"{clean_title}" artist:"{clean_artist}"', False),
        (f"{clean_title} {clean_artist}", True),
    ]
    for query, verify_artist in attempts:
        res = http.get(
            f"{API}/search",
            params={"q": query, "type": "track", "limit": 1},
            headers=_headers(token),
        )
        if res.status_code != 200:
            continue
        items = res.json().get("tracks", {}).get("items") or []
        if not items:
            continue
        top = items[0]
        if verify_artist:
            names = [a.get("name", "") for a in top.get("artists", [])]
            if not _artists_match(artist, names):
                continue
        return top.get("uri")
    return None


def create_playlist(token: dict, name: str, description: str, tracks: list[dict]) -> dict:
    """Create a private playlist and add every track we could find.

    Returns the playlist URL, the number added, and the titles that missed.
    """
    with httpx.Client(timeout=TIMEOUT) as http:
        me = http.get(f"{API}/me", headers=_headers(token))
        if me.status_code != 200:
            raise SpotifyError(
                f"could not read the Spotify profile ({me.status_code}): {_detail(me)}"
            )
        user_id = me.json()["id"]

        uris: list[str] = []
        missed: list[str] = []
        duplicates: list[str] = []
        for track in tracks:
            label = f"{track['title']} - {track['artist']}"
            uri = search_track(http, token, track["title"], track["artist"])
            if not uri:
                missed.append(label)
            elif uri in uris:
                duplicates.append(label)
            else:
                uris.append(uri)

        created = http.post(
            f"{API}/users/{user_id}/playlists",
            json={
                "name": name[:100],
                "public": False,
                "description": description[:300],
            },
            headers=_headers(token),
        )
        if created.status_code == 403:
            lacking = missing_scopes(token) or ["playlist-modify-private"]
            raise SpotifyError(
                f"Spotify refused to create the playlist. It says: "
                f"{_detail(created)}. This connection is missing "
                f"{', '.join(lacking)}. Connect again to re-approve."
            )
        if created.status_code not in (200, 201):
            raise SpotifyError(
                f"could not create the playlist ({created.status_code}): "
                f"{_detail(created)}"
            )
        playlist = created.json()

        if uris:
            added = http.post(
                f"{API}/playlists/{playlist['id']}/tracks",
                json={"uris": uris},
                headers=_headers(token),
            )
            if added.status_code not in (200, 201):
                raise SpotifyError(
                    f"could not add tracks ({added.status_code}): {_detail(added)}"
                )

    return {
        "url": playlist.get("external_urls", {}).get("spotify", ""),
        "added": len(uris),
        "requested": len(tracks),
        "missed": missed,
        "duplicates": duplicates,
    }
