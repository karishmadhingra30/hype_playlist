# Hype Playlist

A web app that builds a playlist for whatever you are about to walk into. Pick
a situation or describe your own, and Claude returns a tracklist with a reason
for every pick. Optional one-click export to a real Spotify playlist.

The reasons are the point. "Builds for ninety seconds before it goes anywhere,
which is the point" is the bar, not "high energy track".

## Screenshot

Drop a capture at `docs/screenshot.png` and reference it here.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then add your Anthropic key
uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Read by the backend only. Never sent to the browser. |
| `SPOTIFY_CLIENT_ID` | no | Turns on the export button. |
| `SPOTIFY_CLIENT_SECRET` | no | Needed alongside the client ID. |
| `SPOTIFY_REDIRECT_URI` | no | Must match the dashboard exactly. Defaults to `http://127.0.0.1:8000/api/spotify/callback`. |
| `SESSION_SECRET` | no | Signs the cookie holding the Spotify token. Without it a random secret is generated at boot, so restarts disconnect Spotify. |

## Spotify export

The app works fully without Spotify. If `SPOTIFY_CLIENT_ID` is absent the
server logs a note, `/api/spotify/status` reports `configured: false`, and the
frontend hides the export button.

To turn it on:

1. Create an app at https://developer.spotify.com/dashboard and tick **Web API**.
2. Add this redirect URI exactly: `http://127.0.0.1:8000/api/spotify/callback`.
   Spotify rejects `localhost` for loopback, so use the IP literal.
3. Put the client ID and secret in `.env`.
4. Check the config before opening the browser:

```bash
python -m scripts.check_spotify
```

That verifies the credentials against Spotify, flags a mispasted key, catches
a `.env` line that failed to parse, and prints the redirect string to compare
against the dashboard.

A new app runs in development mode, where only accounts listed under
**Settings, then User Management** can use it. Once connected, the app shows
"Connected as" with the account email under the export button.

Track matching is imperfect and the app says so. Each track is searched by
title and artist. A looser fallback search is accepted only when the artist on
the result matches. Anything unmatched is reported by name, and no song is
ever substituted for one that could not be found.

If an export fails for an unclear reason, connect Spotify and open
`/api/spotify/probe`. It tries three ways of creating a playlist, reports each
one, deletes anything it creates, and states a verdict.

## Tests

```bash
python -m tests.test_playlist        # parsing, retry, trimming
python -m tests.test_spotify_flow    # OAuth and export
```

Neither needs credentials or network access. `test_spotify_flow` runs the real
OAuth round trip and export against a stand-in that speaks the Spotify
protocol.

## Adding presets

Presets live in one config object at the top of `static/app.js`:

```js
const PRESETS = [
  { label: 'Big meeting',
    prompt: 'a big meeting I am walking into in ten minutes' },
];
```

`label` is the card text. `prompt` is what Claude receives, so it can be
longer and more specific. Add an object and it appears. The grid and numbering
handle themselves.

## Endpoints

```
GET  /                       serves index.html
POST /api/playlist           { situation, length, vibe } -> playlist JSON
GET  /api/spotify/status     { configured, connected, granted_scope, account }
GET  /api/spotify/login      redirect into Spotify OAuth
GET  /api/spotify/callback   token exchange, redirect back to the app
POST /api/spotify/create     { playlist_name, vibe_note, tracks[] } -> URL and misses
GET  /api/spotify/probe      diagnostic for a failing export
```

## Layout

```
backend/
  main.py      routes, JSON parsing, retry
  prompts.py   system prompt and output schema
  spotify.py   OAuth and playlist creation
static/
  index.html
  styles.css   hand-written, no framework
  app.js       presets config, fetch, render
scripts/
  check_spotify.py
tests/
  test_playlist.py
  test_spotify_flow.py
```

Length maps to 6, 12 or 20 tracks. The vibe dial controls how conventional the
picks are. If the playlists come back boring, edit `backend/prompts.py`.

## Learnings

**Spotify retired two endpoints in February 2026.** `POST /v1/users/{id}/playlists`
became `POST /v1/me/playlists`, and `POST /v1/playlists/{id}/tracks` became
`/items`. The retired forms answer a valid token with a bare `403 Forbidden`,
which reads exactly like a permissions problem. This cost four wrong diagnoses.
The test mounts both retired endpoints and fails if any code path calls them.

**Reads succeeding proves nothing about writes.** `/v1/me` and `/v1/search`
need no user permission, so they return 200 for a token that cannot write
anything. Treating those as proof of a healthy connection pointed debugging in
the wrong direction.

**Asking for a JSON shape does not guarantee it.** Claude once returned valid
JSON with `playlist_name` and `vibe_note` and no `tracks` key. The prompt had
asked for tracks. The fix was to pass the schema as `output_config.format` so
the API enforces it, plus one retry.

**Never fabricate a cause in an error message.** A line reading
`missing_scopes(token) or ["playlist-modify-private"]` reported that scope as
missing precisely when nothing was missing. The message was confident and
wrong, and it sent the search in the wrong direction for a full round.

**Log the whole failure, not a prefix.** Two separate bugs here took extra
rounds because the code kept a truncated response and discarded the status
reason. Both Claude and Spotify explain themselves in the part that was being
thrown away.

**A protocol-level mock beats a live integration for debugging.** The Spotify
stand-in made the OAuth round trip, the partial-match reporting and both 403
shapes reproducible without credentials, and it is where each fix was verified
before shipping.
