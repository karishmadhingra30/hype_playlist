"""The Spotify OAuth dance and playlist creation.

Everything here is optional. If SPOTIFY_CLIENT_ID is not set, is_configured()
returns False and main.py never routes anything here.
"""

import base64
import logging
import os
import re
import time
from typing import Iterable

import httpx

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = [
    "playlist-modify-private",
    "playlist-modify-public",
    "user-read-email",
]
SCOPE = " ".join(SCOPES)

TIMEOUT = httpx.Timeout(15.0)

log = logging.getLogger("hype.spotify")


def log_refusal(res: httpx.Response, token: dict, note: str = "") -> None:
    """Record everything Spotify said, so a refusal never has to be guessed."""
    log.warning(
        "Spotify %s %s -> %s%s\n  body: %s\n  granted scope: %r",
        res.request.method,
        res.request.url,
        res.status_code,
        f" ({note})" if note else "",
        res.text[:800] or "<empty>",
        token.get("scope", "<not stored>"),
    )


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


def identify(profile: dict) -> str:
    """Name the account that actually authorized, as the dashboard sees it."""
    email = profile.get("email")
    name = profile.get("display_name") or profile.get("id", "unknown")
    return f"{email} ({name})" if email else f"{name}, email not readable"


def fetch_profile(token: dict) -> dict:
    with httpx.Client(timeout=TIMEOUT) as http:
        res = http.get(f"{API}/me", headers=_headers(token))
    if res.status_code != 200:
        log_refusal(res, token, "reading the profile")
        raise SpotifyError(f"could not read the Spotify profile ({res.status_code})")
    return res.json()


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


PROBE_NAME = "Hype Playlist connection probe"


def probe(token: dict) -> dict:
    """Try several ways of creating a playlist and report what each one does.

    Three rounds of reasoning about a bare 403 produced three wrong answers.
    This settles it by experiment: if every variant fails the account or the
    app is the problem, and if one succeeds the request body or the endpoint
    was the problem.
    """
    report: dict = {"attempts": [], "cleaned_up": []}

    with httpx.Client(timeout=TIMEOUT) as http:
        me = http.get(f"{API}/me", headers=_headers(token))
        report["profile_status"] = me.status_code
        if me.status_code != 200:
            report["profile_body"] = me.text[:400]
            return report

        profile = me.json()
        uid = profile["id"]
        report["account"] = identify(profile)
        report["granted_scope"] = token.get("scope", "")

        # Can it read the library at all?
        listing = http.get(
            f"{API}/me/playlists", params={"limit": 1}, headers=_headers(token)
        )
        report["read_own_playlists"] = listing.status_code

        attempts = [
            ("POST /v1/users/{id}/playlists  name only",
             f"{API}/users/{uid}/playlists", {"name": PROBE_NAME}),
            ("POST /v1/users/{id}/playlists  public true",
             f"{API}/users/{uid}/playlists", {"name": PROBE_NAME, "public": True}),
            ("POST /v1/users/{id}/playlists  public false",
             f"{API}/users/{uid}/playlists", {"name": PROBE_NAME, "public": False}),
            ("POST /v1/users/{id}/playlists  with description",
             f"{API}/users/{uid}/playlists",
             {"name": PROBE_NAME, "public": False, "description": "probe"}),
            ("POST /v1/me/playlists  name only",
             f"{API}/me/playlists", {"name": PROBE_NAME}),
        ]

        for label, url, body in attempts:
            res = http.post(url, json=body, headers=_headers(token))
            entry = {"attempt": label, "status": res.status_code}
            if res.status_code in (200, 201):
                created = res.json()
                entry["created_id"] = created.get("id")
                # Unfollowing is how a playlist is removed from a library.
                gone = http.delete(
                    f"{API}/playlists/{created.get('id')}/followers",
                    headers=_headers(token),
                )
                entry["cleanup_status"] = gone.status_code
                report["cleaned_up"].append(created.get("id"))
            else:
                entry["body"] = res.text[:300]
            report["attempts"].append(entry)

    report["verdict"] = verdict(report)
    return report


def verdict(report: dict) -> str:
    codes = {a["status"] for a in report["attempts"]}
    worked = [a["attempt"] for a in report["attempts"] if a["status"] in (200, 201)]
    if worked:
        return (
            "At least one form of creating a playlist works: "
            + "; ".join(worked)
            + ". The account is fine and the app code should use that form."
        )
    if codes == {403}:
        return (
            "Every way of creating a playlist returns 403 while reads succeed. "
            "Nothing in the request body explains that, so the block is on the "
            "Spotify app or the account, not on this code. Check that the app "
            "has Web API enabled in the dashboard, and that this exact account "
            "is on its user list."
        )
    return f"Mixed results across attempts: {sorted(codes)}."


def create_playlist(token: dict, name: str, description: str, tracks: list[dict]) -> dict:
    """Create a private playlist and add every track we could find.

    Returns the playlist URL, the number added, and the titles that missed.
    """
    with httpx.Client(timeout=TIMEOUT) as http:
        me = http.get(f"{API}/me", headers=_headers(token))
        if me.status_code != 200:
            log_refusal(me, token, "reading the profile")
            raise SpotifyError(
                f"could not read the Spotify profile ({me.status_code}): {_detail(me)}"
            )
        profile = me.json()
        user_id = profile["id"]

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
            log_refusal(created, token, "creating the playlist")
            log.warning("  profile: %s", profile)
            lacking = missing_scopes(token)
            if lacking:
                raise SpotifyError(
                    f"Spotify refused to create the playlist: {_detail(created)}. "
                    f"This connection is missing {', '.join(lacking)}. "
                    f"Connect again to re-approve."
                )
            raise SpotifyError(
                f"Spotify refused to create the playlist: {_detail(created)}. "
                f"The login granted every scope it needs, so this is not about "
                f"permissions. You are connected as {identify(profile)}. While "
                f"the app is in development mode Spotify only allows accounts "
                f"listed under User Management, matched on email. If the "
                f"address listed there is not this one, that is the mismatch. "
                f"Log out of Spotify in your browser and connect again as the "
                f"listed account, or add this one."
            )
        if created.status_code not in (200, 201):
            log_refusal(created, token, "creating the playlist")
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
                log_refusal(added, token, "adding tracks")
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
