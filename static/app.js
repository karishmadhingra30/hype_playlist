/* ============================================================
   Hype Playlist — frontend.
   Add a preset by adding an object to PRESETS. label is what
   shows on the card, prompt is what Claude actually receives.
   ============================================================ */

const PRESETS = [
  { label: 'Big meeting',
    prompt: 'a big meeting I am walking into in ten minutes' },
  { label: 'First date',
    prompt: 'a first date, the nervous kind where I actually care' },
  { label: 'Gym',
    prompt: 'the gym, a session I have been putting off' },
  { label: 'Crying in the shower',
    prompt: 'crying in the shower' },
  { label: '3pm and I have done nothing',
    prompt: 'it is 3pm and I have done nothing all day' },
  { label: 'Cleaning the entire apartment',
    prompt: 'cleaning the entire apartment, top to bottom, all at once' },
  { label: 'Driving home after bad news',
    prompt: 'driving home alone after getting bad news' },
  { label: "Sending the email I've been avoiding",
    prompt: "sending the email I have been avoiding for two weeks" },
];

const LOADING_LINES = [
  'consulting the vibes',
  'arguing about track two',
  'rejecting the obvious pick',
  'sequencing for maximum devastation',
  'checking the key changes',
  'removing one crowd pleaser',
  'defending a deep cut',
  'reordering the middle',
  'letting it breathe',
  'one more left turn',
];

const VIBE_READINGS = [
  [15, 'Play it straight'],
  [35, 'Mildly interesting'],
  [65, 'Even split'],
  [85, 'Getting weird'],
  [101, 'Feral'],
];

const $ = (id) => document.getElementById(id);

const el = {
  presets: $('presets'),
  situation: $('situation'),
  length: $('length'),
  vibe: $('vibe'),
  vibeRead: $('vibe-read'),
  generate: $('generate'),
  error: $('error'),
  loading: $('loading'),
  loadingLine: $('loading-line'),
  results: $('results'),
  playlistName: $('playlist-name'),
  vibeNote: $('vibe-note'),
  tracks: $('tracks'),
  spotify: $('spotify'),
  exportNote: $('export-note'),
};

const state = {
  preset: null,   // index into PRESETS, or null
  length: 'medium',
  busy: false,
  playlist: null,
  spotify: { configured: false, connected: false },
};

const STORE_KEY = 'hype.playlist';

let loadingTimer = null;

/* ---------- presets ---------- */

function buildPresets() {
  PRESETS.forEach((preset, i) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'preset';
    card.setAttribute('aria-pressed', 'false');
    card.dataset.index = String(i);

    const idx = document.createElement('span');
    idx.className = 'preset-idx';
    idx.textContent = String(i + 1).padStart(2, '0');

    const name = document.createElement('span');
    name.className = 'display preset-name';
    name.textContent = preset.label;

    card.append(idx, name);
    card.addEventListener('click', () => choosePreset(i));
    el.presets.append(card);
  });
}

function choosePreset(i) {
  state.preset = state.preset === i ? null : i;
  el.situation.value = '';
  paintPresets();
}

function paintPresets() {
  [...el.presets.children].forEach((card, i) => {
    card.setAttribute('aria-pressed', state.preset === i ? 'true' : 'false');
  });
}

/* ---------- dials ---------- */

function bindLength() {
  el.length.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    state.length = button.dataset.value;
    [...el.length.children].forEach((b) => {
      b.setAttribute('aria-checked', b === button ? 'true' : 'false');
    });
  });
}

function bindVibe() {
  const paint = () => {
    const v = Number(el.vibe.value);
    const reading = VIBE_READINGS.find(([ceiling]) => v < ceiling);
    el.vibeRead.textContent = reading ? reading[1] : 'Feral';
  };
  el.vibe.addEventListener('input', paint);
  paint();
}

/* ---------- loading ---------- */

function startLoading() {
  const lines = [...LOADING_LINES].sort(() => Math.random() - 0.5);
  let i = 0;
  const show = () => {
    el.loadingLine.textContent = lines[i % lines.length];
    // restart the entrance animation on every swap
    el.loadingLine.style.animation = 'none';
    void el.loadingLine.offsetWidth;
    el.loadingLine.style.animation = '';
    i += 1;
  };
  show();
  loadingTimer = setInterval(show, 1900);
  el.loading.hidden = false;
}

function stopLoading() {
  clearInterval(loadingTimer);
  loadingTimer = null;
  el.loading.hidden = true;
}

/* ---------- results ---------- */

function renderPlaylist(data) {
  el.playlistName.textContent = data.playlist_name;
  el.vibeNote.textContent = data.vibe_note || '';
  el.tracks.replaceChildren();

  data.tracks.forEach((track, i) => {
    const li = document.createElement('li');
    li.className = 'track';
    li.style.setProperty('--i', String(i));

    const idx = document.createElement('span');
    idx.className = 'track-idx';
    idx.textContent = String(i + 1);

    const body = document.createElement('div');
    body.className = 'track-body';

    const title = document.createElement('h3');
    title.className = 'display track-title';
    title.textContent = track.title;

    const artist = document.createElement('p');
    artist.className = 'track-artist';
    artist.textContent = track.artist;

    body.append(title, artist);

    if (track.reason) {
      const reason = document.createElement('p');
      reason.className = 'track-reason';
      reason.textContent = track.reason;
      body.append(reason);
    }

    li.append(idx, body);
    el.tracks.append(li);
  });

  el.results.hidden = false;
  paintSpotifyButton();
}

function showError(message) {
  el.error.textContent = message;
  el.error.hidden = false;
}

function clearError() {
  el.error.hidden = true;
  el.error.textContent = '';
}

/* ---------- generate ---------- */

function currentSituation() {
  const typed = el.situation.value.trim();
  if (typed) return typed;
  if (state.preset !== null) return PRESETS[state.preset].prompt;
  return '';
}

async function generate() {
  if (state.busy) return;

  const situation = currentSituation();
  if (!situation) {
    showError('Pick a situation or describe your own first.');
    el.situation.focus();
    return;
  }

  clearError();
  state.busy = true;
  state.playlist = null;
  el.generate.disabled = true;
  el.results.hidden = true;
  el.exportNote.hidden = true;
  startLoading();

  try {
    const res = await fetch('/api/playlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        situation,
        length: state.length,
        vibe: Number(el.vibe.value),
      }),
    });

    const data = await res.json().catch(() => null);

    if (!res.ok) {
      throw new Error((data && data.detail) || `Request failed (${res.status}).`);
    }

    state.playlist = data;
    remember(data);
    renderPlaylist(data);
    el.results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    showError(err.message || 'Something went wrong. Try again.');
  } finally {
    stopLoading();
    state.busy = false;
    el.generate.disabled = false;
  }
}

/* ---------- spotify export ---------- */

function remember(data) {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify(data));
  } catch (err) {
    /* private mode, or storage is full. The playlist is still on screen. */
  }
}

function recall() {
  try {
    const raw = sessionStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    return null;
  }
}

function paintSpotifyButton() {
  const show = state.spotify.configured && !!state.playlist;
  el.spotify.hidden = !show;
  el.spotify.textContent = state.spotify.connected
    ? 'Send to Spotify'
    : 'Connect Spotify';
}

function showExportNote(nodes) {
  el.exportNote.replaceChildren(...nodes);
  el.exportNote.hidden = false;
}

function reportExport(result) {
  const nodes = [];
  const missed = result.missed || [];

  if (missed.length === 0) {
    nodes.push(document.createTextNode(`Added all ${result.added}. `));
  } else {
    nodes.push(
      document.createTextNode(
        `Added ${result.added} of ${result.requested}. Couldn't find: ` +
        `${missed.join(', ')}. Nothing was swapped in for them. `
      )
    );
  }

  const dupes = result.duplicates || [];
  if (dupes.length) {
    nodes.push(
      document.createTextNode(
        `Skipped as already in the list: ${dupes.join(', ')}. `
      )
    );
  }

  if (result.url) {
    const link = document.createElement('a');
    link.href = result.url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Open the playlist';
    nodes.push(link);
  }

  showExportNote(nodes);
}

async function exportToSpotify() {
  if (!state.playlist) return;

  if (!state.spotify.connected) {
    remember(state.playlist);
    window.location.href = '/api/spotify/login';
    return;
  }

  el.spotify.disabled = true;
  el.spotify.textContent = 'Sending…';
  el.exportNote.hidden = true;

  try {
    const res = await fetch('/api/spotify/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        playlist_name: state.playlist.playlist_name,
        vibe_note: state.playlist.vibe_note || '',
        tracks: state.playlist.tracks,
      }),
    });

    const data = await res.json().catch(() => null);

    if (res.status === 401) {
      state.spotify.connected = false;
      showExportNote([document.createTextNode('Spotify session expired. Connect again.')]);
      return;
    }
    if (!res.ok) {
      throw new Error((data && data.detail) || `Export failed (${res.status}).`);
    }

    reportExport(data);
  } catch (err) {
    showExportNote([document.createTextNode(err.message || 'Export failed.')]);
  } finally {
    el.spotify.disabled = false;
    paintSpotifyButton();
  }
}

const REDIRECT_MESSAGES = {
  denied: 'Spotify access was declined. The playlist is still here.',
  badstate: 'That Spotify login did not check out. Try connecting again.',
  failed: 'Spotify would not hand over a token. Try connecting again.',
};

async function initSpotify() {
  try {
    const res = await fetch('/api/spotify/status');
    if (res.ok) state.spotify = await res.json();
  } catch (err) {
    /* leave export off */
  }

  if (!state.spotify.configured) {
    console.info(
      'Spotify export is off: SPOTIFY_CLIENT_ID is not set on the server. ' +
      'Playlist generation works without it.'
    );
  }

  // Coming back from the OAuth round trip, the page reloaded. Put the
  // playlist back so the export button has something to send.
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get('spotify');
  if (outcome) {
    const saved = recall();
    if (saved) {
      state.playlist = saved;
      renderPlaylist(saved);
    }
    if (REDIRECT_MESSAGES[outcome]) {
      showExportNote([document.createTextNode(REDIRECT_MESSAGES[outcome])]);
    }
    window.history.replaceState({}, '', window.location.pathname);

    // The user already asked for this before being sent to Spotify. Finish it
    // rather than making them find the button again.
    if (outcome === 'connected' && saved && state.spotify.connected) {
      paintSpotifyButton();
      exportToSpotify();
      return;
    }
  }

  paintSpotifyButton();
}

/* ---------- init ---------- */

buildPresets();
bindLength();
bindVibe();

el.situation.addEventListener('input', () => {
  if (el.situation.value.trim() && state.preset !== null) {
    state.preset = null;
    paintPresets();
  }
});

el.situation.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') generate();
});

el.generate.addEventListener('click', generate);
el.spotify.addEventListener('click', exportToSpotify);

initSpotify();
