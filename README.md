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

To turn it on, create an app in the Spotify developer dashboard, add your
redirect URI to it, and put the client ID and secret in `.env`. The flow is
Authorization Code with scope `playlist-modify-private`.

Track matching is imperfect and the app says so. Each track is searched by
title and artist; a loose fallback search is only accepted when the artist on
the result actually matches. Anything still unmatched is reported by name
("Added 10 of 12. Couldn't find: ..."). No song is ever swapped in for one
that could not be found.

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
```

## Notes

Length maps to 6, 12, or 20 tracks. The vibe dial maps to how conventional the
picks are: the calm end asks for recognizable and safe, the unhinged end asks
for deep cuts and tonal left turns.

If the playlists come back boring, edit `backend/prompts.py`. That is almost
always where the fix is.
