"""Preflight check for the Spotify config.

Most Spotify setup failures are a mismatched string, not broken code, and the
error you get in the browser does not say which string. This checks the ones
that matter and verifies the client ID and secret against Spotify directly.

    python -m scripts.check_spotify
"""

import base64
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values, load_dotenv

TOKEN_URL = os.environ.get(
    "SPOTIFY_TOKEN_URL_OVERRIDE", "https://accounts.spotify.com/api/token"
)
CALLBACK_PATH = "/api/spotify/callback"

OK, WARN, BAD = "  ok  ", " warn ", " fail "
problems: list[str] = []
warnings: list[str] = []


def line(tag: str, text: str) -> None:
    print(f"[{tag}] {text}")


def fail(text: str) -> None:
    line(BAD, text)
    problems.append(text)


def warn(text: str) -> None:
    line(WARN, text)
    warnings.append(text)


def mask(value: str) -> str:
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


DECLARED = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

# A Spotify client ID and secret are both 32 hex characters.
CREDENTIAL = re.compile(r"^[0-9a-f]{32}$")


def check_parse(env_path: Path) -> None:
    """Find keys written in the file that did not survive parsing.

    An unclosed quote makes python-dotenv drop the line and carry on, so the
    variable reads as simply missing with no obvious cause.
    """
    declared = set()
    for raw_line in env_path.read_text().splitlines():
        if raw_line.lstrip().startswith("#"):
            continue
        found = DECLARED.match(raw_line)
        if found:
            declared.add(found.group(1))

    parsed = {k for k, v in dotenv_values(env_path).items() if v is not None}
    for key in sorted(declared - parsed):
        fail(f"{key} is written in .env but could not be parsed. The usual "
             f"cause is an unclosed quote on that line or the one above it.")


def check_credential_shape(name: str, value: str) -> None:
    """A mispasted credential is the most common cause of invalid_client."""
    if CREDENTIAL.match(value):
        return
    if len(value) != 32:
        warn(f"{name} is {len(value)} characters. Spotify's are normally 32. "
             f"Check for a truncated or doubled paste.")
    else:
        warn(f"{name} is 32 characters but is not all lowercase hex. Check it "
             f"came from the right field in the dashboard.")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"

    print(f"\nReading {env_path}\n")
    if not env_path.exists():
        fail(".env does not exist. Copy .env.example to .env and fill it in.")
        return report(None)

    check_parse(env_path)
    load_dotenv(env_path)

    # ---- Anthropic ----
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        fail("ANTHROPIC_API_KEY is empty. Playlist generation will not work.")
    else:
        line(OK, f"ANTHROPIC_API_KEY present ({mask(anthropic_key)})")

    # ---- Spotify credentials ----
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        fail("SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET is empty. The export "
             "button will stay hidden.")
        return report(None)

    line(OK, f"SPOTIFY_CLIENT_ID present ({mask(client_id)})")
    line(OK, f"SPOTIFY_CLIENT_SECRET present ({mask(client_secret)})")
    check_credential_shape("SPOTIFY_CLIENT_ID", client_id)
    check_credential_shape("SPOTIFY_CLIENT_SECRET", client_secret)
    if client_id == client_secret:
        fail("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are identical. The "
             "secret is behind 'View client secret' in the dashboard.")

    # ---- redirect URI ----
    redirect = os.environ.get("SPOTIFY_REDIRECT_URI",
                              f"http://127.0.0.1:8000{CALLBACK_PATH}")
    parsed = urlparse(redirect)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if parsed.hostname == "localhost":
        fail("SPOTIFY_REDIRECT_URI uses localhost. Spotify rejects that for "
             "loopback addresses. Use 127.0.0.1 instead, in .env AND in the "
             "dashboard.")
    elif parsed.hostname in ("127.0.0.1", "::1"):
        line(OK, f"redirect host is the loopback IP ({parsed.hostname})")
    else:
        warn(f"redirect host is {parsed.hostname}, not a loopback address. "
             f"Fine if you are deploying, unexpected if you are running locally.")

    if parsed.path != CALLBACK_PATH:
        fail(f"SPOTIFY_REDIRECT_URI path is '{parsed.path}', but the app "
             f"serves the callback at '{CALLBACK_PATH}'.")
    else:
        line(OK, f"redirect path is {CALLBACK_PATH}")

    if parsed.hostname in ("127.0.0.1", "::1") and parsed.scheme != "http":
        warn(f"redirect scheme is {parsed.scheme}. Loopback usually needs http.")

    # ---- session secret ----
    if not os.environ.get("SESSION_SECRET", ""):
        warn("SESSION_SECRET is empty. A random one is generated at boot, so "
             "every server restart disconnects Spotify. Fine for testing.")
    else:
        line(OK, "SESSION_SECRET set, so Spotify stays connected across restarts")

    # ---- the live check ----
    print("\nAsking Spotify whether these credentials are real...\n")
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        res = httpx.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        warn(f"Could not reach Spotify to verify ({exc.__class__.__name__}). "
             f"Check your network, then rerun.")
        return report(port)

    if res.status_code == 200:
        line(OK, "Spotify accepted the client ID and secret")
    elif res.status_code == 400 and "invalid_client" in res.text:
        fail("Spotify rejected the client ID or secret. Recopy both from the "
             "dashboard, watching for a truncated paste.")
    else:
        fail(f"Spotify returned {res.status_code}: {res.text[:200]}")

    return report(port)


def report(port: int | None) -> int:
    print()
    if problems:
        print(f"{len(problems)} thing(s) to fix before this will work:\n")
        for p in problems:
            print(f"  - {p}")
        print()
        return 1

    if warnings:
        print(f"No blocking problems, but read the {len(warnings)} warning(s) "
              f"above before you test.\n")
    else:
        print("Config looks good.\n")
    if port:
        print("This check cannot verify the redirect URI itself. Spotify only")
        print("checks it during login, and it must match the dashboard exactly.")
        print("Confirm this string is listed in your app's settings:\n")
        print(f"    {os.environ.get('SPOTIFY_REDIRECT_URI')}\n")
        print(f"Then run the server on port {port}:\n")
        print(f"    uvicorn backend.main:app --reload --port {port}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
