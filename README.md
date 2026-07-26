# Hype Playlist

A small web app that builds you a playlist for whatever you are about to walk
into. Pick a situation or describe your own, and Claude returns a tracklist
with a reason for every pick. Optional one-click export to a real Spotify
playlist.

The point is the reasons. "Builds for ninety seconds before it goes anywhere,
which is the point" is the bar, not "high energy track."

## Screenshot

Drop a capture at `docs/screenshot.png` and it will show up here.

## Install and run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your Anthropic key in it
uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000.

## Environment variables

| Variable | Required | What it does |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Read by the backend only. Never sent to the browser. |
| `SPOTIFY_CLIENT_ID` | no | Turns the export button on. |
| `SPOTIFY_CLIENT_SECRET` | no | Needed alongside the client ID. |
| `SPOTIFY_REDIRECT_URI` | no | Must match the redirect URI on your Spotify app exactly. Defaults to `http://127.0.0.1:8000/api/spotify/callback`. |
| `SESSION_SECRET` | no | Signs the cookie holding the Spotify token. A random one is generated at boot if unset, which means restarts log you out. |

## Spotify is optional

The core app works fully without it. If `SPOTIFY_CLIENT_ID` is absent the
server logs a note at startup, `/api/spotify/status` reports
`configured: false`, and the frontend hides the export button. Nothing about
playlist generation depends on it.

### Turning it on

1. Go to https://developer.spotify.com/dashboard and create an app. Name and
   description can be anything.
2. Tick **Web API** when it asks which APIs you plan to use.
3. Add this exact redirect URI and save:

   ```
   http://127.0.0.1:8000/api/spotify/callback
   ```

   Use the IP literal. Spotify rejects `localhost` in redirect URIs for
   loopback addresses, and the error it gives at login is not obvious. If you
   run the server on another port, change it here and in `SPOTIFY_REDIRECT_URI`
   so the two match character for character.
4. Copy the Client ID and Client Secret from the app's settings into `.env`.
5. Restart `uvicorn`. The export button appears once the server sees the
   client ID.

A new app starts in development mode, which means only your own Spotify
account can authorize it until you add other users to its allowlist. That is
fine for running it yourself.

The flow is Authorization Code with scope `playlist-modify-private`, so the
playlists it creates are private to your account.

Track matching is imperfect and the app says so. Each track is searched by
title and artist; a loose fallback search is only accepted when the artist on
the result actually matches. Anything still unmatched is reported by name
("Added 10 of 12. Couldn't find: ..."). No song is ever swapped in for one
that could not be found.

## Tests

`tests/test_spotify_flow.py` runs the whole export against a stand-in for the
Spotify Web API: the OAuth round trip, a forged `state`, the search fallback,
and the report at the end. It needs no credentials and no network.

```bash
python -m tests.test_spotify_flow
```

It asserts the thing most worth protecting: when the loose fallback search
returns a track by the wrong artist, that track is reported as a miss and
never reaches the playlist.

## Adding presets

Presets live in one config object at the top of `static/app.js`:

```js
const PRESETS = [
  { label: 'Big meeting',
    prompt: 'a big meeting I am walking into in ten minutes' },
  // ...
];
```

`label` is what shows on the card. `prompt` is what Claude actually receives,
so it can be longer and more specific than the label. Add an object and it
appears; the grid and the numbering handle themselves.

## Endpoints

```
GET  /                       serves index.html
POST /api/playlist           { situation, length, vibe } -> the playlist JSON
GET  /api/spotify/status     { configured, connected }
GET  /api/spotify/login      redirect into Spotify OAuth
GET  /api/spotify/callback   token exchange, redirect back to the app
POST /api/spotify/create     { playlist_name, vibe_note, tracks[] } -> URL and misses
```

## Layout

```
backend/
  main.py      routes and defensive JSON parsing
  prompts.py   the system prompt, where the taste lives
  spotify.py   OAuth and playlist creation
static/
  index.html
  styles.css   hand-written, no framework
  app.js       presets config, fetch, render
tests/
  test_spotify_flow.py
```

## Notes

Length maps to 6, 12, or 20 tracks. The vibe dial maps to how conventional the
picks are: the calm end asks for recognizable and safe, the unhinged end asks
for deep cuts and tonal left turns.

If the playlists come back boring, edit `backend/prompts.py`. That is almost
always where the fix is.
