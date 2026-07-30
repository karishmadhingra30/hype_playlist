# Hype Playlist

A small web app that builds a playlist for whatever you are about to walk
into. Pick a situation or describe your own, and Claude returns a tracklist
with a reason for every pick. Optionally push it to a real Spotify playlist.

The reasons are the point. "Builds for ninety seconds before it goes anywhere,
which is the point" is the bar, not "high energy track".

## Screenshot

Drop a capture at `docs/screenshot.png` and reference it here.

## Run it locally

You need Python 3.11 or newer and an Anthropic API key from
https://console.anthropic.com.

```bash
git clone https://github.com/karishmadhingra30/hype_playlist.git
cd hype_playlist

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
```

Open `.env` and put your key on the first line:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Leave the Spotify lines empty for now. Then start it:

```bash
uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000. Pick a situation, hit generate, and you have a
playlist. Everything except the Spotify export works at this point.

Run `uvicorn` from the project root. The app looks for `.env` from the
directory you launched it in.

## Spotify export

This part is optional. Without it the app hides the export button and works
normally.

### Try it without setting up your own app

Spotify apps start in development mode, which means only accounts the app
owner has added can connect. If you would rather not create your own Spotify
app, open an issue on this repo or message
[@karishmadhingra30](https://github.com/karishmadhingra30) with the email on
your Spotify account, and I can add you to mine. Then you only need the client
ID and secret, which I can share with you directly.

### Or set up your own

1. Go to https://developer.spotify.com/dashboard and create an app. The name
   and description can be anything.
2. Tick **Web API** when it asks which APIs you plan to use.
3. Add this redirect URI exactly and save:

   ```
   http://127.0.0.1:8000/api/spotify/callback
   ```

   Use the IP literal. Spotify rejects `localhost` for loopback addresses, and
   the error it gives at login does not explain why.
4. Under **Settings, then User Management**, add your own Spotify account with
   the display name and email on it. Development mode only serves accounts on
   that list.
5. Copy the client ID and secret into `.env`:

   ```
   SPOTIFY_CLIENT_ID=...
   SPOTIFY_CLIENT_SECRET=...
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/api/spotify/callback
   ```
6. Check it before opening the browser:

   ```bash
   python -m scripts.check_spotify
   ```

   This verifies the credentials against Spotify, flags a mispasted key,
   catches a `.env` line that failed to parse, and prints the redirect string
   to compare against the dashboard.
7. Restart `uvicorn`. Generate a playlist, hit **Connect Spotify**, approve,
   and it exports.

Once connected, the app shows "Connected as" with your account email under the
export button, so you can tell which account it will write to.

Set `SESSION_SECRET` in `.env` to any random string if you want the Spotify
connection to survive a server restart. Without it a new secret is generated
each boot and you reconnect each time.

### If an export fails

Connect Spotify, then open http://127.0.0.1:8000/api/spotify/probe. It tries
three ways of creating a playlist, reports each one, deletes anything it
creates, and states a verdict.

Track matching is imperfect and the app says so. Each track is searched by
title and artist. A looser fallback search is accepted only when the artist on
the result matches. Anything unmatched is reported by name, and no song is
ever substituted for one that could not be found.

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

## Tests

```bash
python -m tests.test_playlist        # parsing, retry, trimming
python -m tests.test_spotify_flow    # OAuth and export
```

Neither needs credentials or network access. `test_spotify_flow` runs the real
OAuth round trip and export against a stand-in that speaks the Spotify
protocol.

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
picks are. If the playlists come back boring, edit `backend/prompts.py`. That
is where the taste lives.

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

**Log the whole failure, not a prefix.** Two separate bugs took extra rounds
because the code kept a truncated response and discarded the status reason.
Both Claude and Spotify explain themselves in the part that was being thrown
away.

**A protocol-level mock beats a live integration for debugging.** The Spotify
stand-in made the OAuth round trip, the partial-match reporting and both 403
shapes reproducible without credentials, and it is where each fix was verified
before shipping.
