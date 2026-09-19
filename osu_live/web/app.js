/* osu! live — phone overlay.
 *
 * Four screens, one per game state:
 *
 *   home     menu / idle            → account, then the selected map
 *   select   song select / editing  → map detail, star rating, pp ladder
 *   play     a map is being played  → score anchored, accuracy/combo/pp live
 *   results  after a play finishes  → grade and score, then what it was worth
 *
 * `pickPage()` decides which one shows; each `renderX()` only paints, and the
 * paint functions are deliberately free of destructive writes -- a panel that
 * cannot be fully populated must still leave the rest of the screen usable.
 */
'use strict';

const $ = (id) => document.getElementById(id);

/* Element catalogue. Split by page so it is obvious which screen a missing id
 * would take down. Everything here must exist in index.html: a null entry used
 * to abort the whole render, which is exactly how three of these screens were
 * blanked before. `smoke` in the repo's test notes asserts the two stay in
 * sync. */
const ids = {
  common: ['backdrop', 'offline', 'offline-hint', 'token-row', 'token-input', 'token-save',
           'page-home', 'page-select', 'page-play', 'page-results'],
  home: ['home-clock', 'home-dot', 'home-conn-text', 'home-avatar', 'home-name', 'home-sub',
         'home-pp', 'home-pp-label', 'home-acc', 'home-ranked', 'home-plays', 'home-rank',
         'home-song', 'home-song-sub', 'home-tags', 'home-fill',
         'home-time', 'home-total'],
  select: ['sel-clock', 'sel-dot', 'sel-conn-text', 'sel-artist', 'sel-title', 'sel-diff',
           'sel-mapper', 'sel-stars', 'sel-ring', 'sel-objects', 'sel-ar', 'sel-od', 'sel-cs',
           'sel-hp', 'sel-maxpp', 'sel-pp-fc', 'sel-pp-99', 'sel-pp-98', 'sel-pp-95',
           'sel-ladder', 'sel-elapsed', 'sel-fill', 'sel-mods', 'sel-total'],
  play: ['play-clock', 'play-dot', 'play-conn-text', 'play-map', 'play-diff', 'play-score',
         'play-hp', 'play-hp-fill', 'play-combo', 'play-acc', 'play-pp', 'play-judges',
         'play-fill', 'play-time', 'play-total'],
  results: ['res-clock', 'res-dot', 'res-conn-text', 'res-avatar', 'res-player', 'res-song',
            'res-diff', 'res-mods', 'res-grade', 'res-score', 'res-stars', 'res-pp', 'res-acc',
            'res-combo', 'res-judges', 'res-meta'],
};

const els = {};
for (const id of Object.values(ids).flat()) els[id] = $(id);

/* Each page owns a status rail, so all four need the clock and live indicator
 * updated -- not just the home page's. */
const statusBars = ['home', 'sel', 'play', 'res'].map((prefix) => ({
  time: $(`${prefix}-clock`),
  dot: $(`${prefix}-dot`),
  text: $(`${prefix}-conn-text`),
}));

const nf = new Intl.NumberFormat('en-US');
const nf2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/* Pages, and which game states belong to each. Anything unlisted falls back to
 * home, which is also where exiting the results screen lands once the game
 * returns to song select. */
const PLAY_STATES = new Set(['play', 'watchingReplay', 'spectating']);
const RESULTS_STATES = new Set(['resultScreen', 'rankingVs', 'rankingTagCoop', 'rankingTeam']);
const SELECT_STATES = new Set([
  'selectPlay', 'selectEdit', 'selectDrawings', 'selectMulti', 'edit',
  'onlineSelection', 'beatmapImport',
]);

/* How long a dropped connection is tolerated before the "waiting for osu!"
 * overlay is shown. osu! frees its play structures the instant a map ends, so
 * the play → results hand-off can cost a state or two; anything shorter than
 * this never reaches the screen. */
const OFFLINE_GRACE_MS = 1200;

let lastPlayScore = null;   // newest finished score seen after a play
let shownResults = null;    // the score currently on the results screen
let lastPage = '';
let offlineTimer = null;    // defers the "no client" overlay, see render()
let offlineReason = '';     // most recent error, shown if the overlay does open
//: Which map the select screen is currently showing, so its identity can be
//: crossfaded when the selection changes and left alone otherwise.
let selectMapKey = '';
/* Last non-empty player block and beatmap. osu! frees the profile and beatmap
 * objects on the frame it changes screens, so a read can come back empty while
 * the map is still loaded. Rendering that emptiness puts "未登录" and a blank
 * title on screen for a frame or two, so an empty block inherits the previous
 * one and only an explicit disconnect clears them. */
let lastPlayer = {};
let lastBeatmap = {};

/** Keep the previous block when this frame's is empty.
 *
 * Only whole empty containers are substituted: a count that arrives as 0 is a
 * real value (no misses yet) and must never inherit the last frame's.
 */
function keepFilled(previous, incoming) {
  return (incoming && Object.keys(incoming).length) ? incoming : (previous || {});
}

/* ─────────────────────────────────────────────────────────── utilities */

/* Motion is decoration here, never the only carrier of a value: every tween
 * below ends on the exact number it was given, and switching it off only
 * removes the travel. Also off wherever there is no rAF to drive it, which
 * makes the figures settle synchronously. */
const MOTION = !(
  typeof requestAnimationFrame !== 'function'
  || (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches)
);

const TWEEN_MS = 420;
//: Must match the `.bd` opacity transition in the stylesheet.
const BACKDROP_FADE_MS = 500;

/** Ease-out: fast off the mark, settling onto the value. */
function easeOut(t) {
  return 1 - Math.pow(1 - t, 3);
}

/* One rAF loop for every animation on the page rather than one per animated
 * element; the map select screen runs a tween per pp figure.
 *
 * A figure must never be left showing a value it never reached, so the loop
 * also carries a fallback: if frames stop arriving, a timer settles every
 * animation still in flight onto its target. rAF is paused in background tabs
 * and does not fire at all in some embedded contexts, and without this the
 * last painted frame -- part way to the target -- would simply stay there. */
const running = new Map();
let rafId = 0;
let fallbackId = 0;

function settleAll() {
  for (const [key, anim] of running) {
    anim.step(anim.to);
    running.delete(key);
  }
  if (fallbackId) { clearTimeout(fallbackId); fallbackId = 0; }
}

function tickAnimations(now) {
  for (const [key, anim] of running) {
    const t = anim.duration > 0 ? Math.min(1, (now - anim.start) / anim.duration) : 1;
    anim.step(anim.from + (anim.to - anim.from) * easeOut(t));
    if (t >= 1) running.delete(key);
  }
  if (running.size) {
    rafId = requestAnimationFrame(tickAnimations);
  } else {
    rafId = 0;
    if (fallbackId) { clearTimeout(fallbackId); fallbackId = 0; }
  }
}

function scheduleFallback(ms) {
  if (fallbackId) clearTimeout(fallbackId);
  fallbackId = setTimeout(settleAll, ms + 120);
}

/** Animate a number towards `to`, reporting each frame to `apply`.
 *
 * The travel starts from whatever is currently on screen, read back out of the
 * element. Seeding from the previous tween's value instead made every map
 * change roll up from zero, because the first tween of a new map starts with
 * nothing to interpolate from.
 */
function tween(key, to, apply, duration = TWEEN_MS, el = null) {
  if (!Number.isFinite(to)) {
    running.delete(key);
    return;
  }
  if (!MOTION || duration <= 0) {
    running.delete(key);
    apply(to);
    return;
  }
  const existing = running.get(key);
  const from = existing ? existing.value : numberFromElement(el);
  if (Math.abs(to - from) < 0.005) {
    running.delete(key);
    apply(to);
    return;
  }
  const anim = {
    from,
    to,
    value: from,
    start: performance.now(),
    duration,
    step(v) {
      anim.value = v;
      apply(v);
    },
  };
  running.set(key, anim);
  if (!rafId) rafId = requestAnimationFrame(tickAnimations);
  scheduleFallback(duration);
}

/** Animate a text figure, formatting every frame so the digits roll.
 *
 * A figure that is currently unknown holds what it shows rather than blanking:
 * osu! clears the pp block the instant a different map is selected, so the
 * first frames after a map change carry no value at all. Writing the
 * placeholder there made every map change read as *blank, then jump*; holding
 * keeps the old number up until the new one is known, and the roll then starts
 * from it, which is the whole point of rolling.
 */
function tweenNumber(key, to, el, format) {
  if (!el) return;
  if (!Number.isFinite(to) || to <= 0) {
    if (!el.textContent) setText(el, '—');
    return;
  }
  tween(key, to, (v) => setText(el, format(v)), TWEEN_MS, el);
}

/** The number an element is currently showing, so a new tween can start there.
 *
 * Formatted text is all that is available -- "264.72", "#128", "98.42%" -- so
 * the digits are picked out of it. A figure showing a placeholder yields 0,
 * which is the right starting point for one that has never had a value.
 */
function numberFromElement(el) {
  if (!el || !el.textContent) return 0;
  const digits = String(el.textContent).replace(/[^0-9.]/g, '');
  const value = parseFloat(digits);
  return Number.isFinite(value) ? value : 0;
}

/** Draw the star ring's arc.
 *
 * The fraction is written straight into the gradient as a turn value rather
 * than left to `calc(var(--p) * 1turn)` on a registered property. Animating a
 * registered custom property inside a gradient repaints the element every
 * frame -- custom properties do not animate on the compositor -- and this
 * screen runs several at once while the artwork is cross-fading. The arc
 * lands on the same value either way; it just no longer travels to it.
 */
function setArc(el, fraction) {
  if (!el) return;
  const turns = Math.max(0, Math.min(1, Number(fraction) || 0));
  el.style.setProperty('--arc', `${turns.toFixed(4)}turn`);
}

/** Draw one ladder rung's fill.
 *
 * The alpha is resolved here and written as a plain colour: `rgba()` with a
 * `calc()` over a custom property inside a gradient is the same per-frame
 * repaint as above.
 */
function setRungFill(el, weight) {
  if (!el) return;
  const w = Math.max(0, Math.min(1, Number(weight) || 0));
  el.style.background =
    `linear-gradient(180deg, rgba(255, 95, 162, ${(w * 0.13).toFixed(4)}) 0%, rgba(11, 10, 13, .55) 100%)`;
}

function setText(el, value) {
  if (!el) return;
  const text = value === null || value === undefined ? '—' : String(value);
  if (el.textContent !== text) el.textContent = text;
}

function setWidth(el, fraction) {
  if (!el) return;
  const pct = Math.max(0, Math.min(100, (Number(fraction) || 0) * 100));
  el.style.width = `${pct.toFixed(1)}%`;
}

function fmtClock(ms) {
  if (!Number.isFinite(ms) || ms < 0) ms = 0;
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function fmtCount(value) {
  if (!Number.isFinite(value)) return '—';
  if (Math.abs(value) >= 1e6) return (value / 1e6).toFixed(2) + 'M';
  if (Math.abs(value) >= 1e4) return (value / 1e3).toFixed(1) + 'k';
  return nf.format(Math.round(value));
}

function fmtPp(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return '—';
  return nf2.format(n);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function modsHtml(mods, emptyLabel = 'NoMod') {
  const list = (mods && mods.array) || [];
  if (!list.length) return `<span class="mod none">${escapeHtml(emptyLabel)}</span>`;
  return list.map((m) => `<span class="mod">${escapeHtml(m)}</span>`).join('');
}

/* A map carries two sets of names: the romanised Artist/Title osu! uses for
 * sorting and filenames, and the original ArtistUnicode/TitleUnicode. The
 * original is what the map calls itself and what a player recognises, so it
 * wins wherever there is one; the romanised form is the fallback for the
 * majority of maps that have no separate original. */
function artistOf(beatmap) {
  return (beatmap && (beatmap.artist_unicode || beatmap.artist)) || '';
}

function titleOf(beatmap) {
  return (beatmap && (beatmap.title_unicode || beatmap.title)) || '';
}

function mapTitle(beatmap) {
  const title = titleOf(beatmap);
  if (!title) {
    if (!beatmap || !beatmap.filename) return '—';
    return beatmap.filename.replace(/\.osu$/, '');
  }
  const artist = artistOf(beatmap);
  return artist ? `${artist} - ${title}` : title;
}

function tagsHtml(pairs) {
  return pairs
    .filter(([, value]) => value !== undefined && value !== null && value !== '' && value !== '—')
    .map(([label, value]) => `<span class="tag">${escapeHtml(value)} ${escapeHtml(label)}</span>`)
    .join('');
}

/** Publish the header band's height as `--head-band-height`.
 *
 * In landscape the header is taken out of flow and pinned under the status
 * rail, so the content beneath it has to reserve the space itself. Its height
 * is not a constant: the title is a vh-derived clamp, and it becomes two lines
 * when the song name is long. Measuring it and writing the result back as a
 * custom property keeps the clearance exact at every size and wrap, which a
 * hard-coded padding or grid row cannot do.
 *
 * The border box is used rather than `offsetHeight` because the header may sit
 * inside the body grid; `offsetTop` there is measured against the grid area,
 * not the page, which makes the value depend on the very layout it feeds.
 */
function syncHeadBand() {
  const head = document.querySelector('.page.active .head');
  const shell = document.querySelector('#shell');
  if (!head || !shell) return;
  const rect = head.getBoundingClientRect();
  if (!rect.height) return; // hidden page; nothing meaningful to measure
  shell.style.setProperty('--head-band-height', `${(rect.height + 16).toFixed(1)}px`);
}

/** Shrink a numeric element until it fits the space its box actually offers.
 *
 * Scores run from six to ten digits; measuring the laid-out width is exact,
 * where picking a size per digit count only ever approximates. Returns the
 * scale applied, so callers can constrain it (the live score must not end up
 * smaller than the readouts beneath it).
 */
function fitScoreToWidth(el, { min = 18, maxScale = 1 } = {}) {
  if (!el) return 1;
  el.style.fontSize = '';
  const parent = el.parentElement;
  if (!parent) return 1;
  const pc = getComputedStyle(parent);
  const available = parent.clientWidth
    - (parseFloat(pc.paddingLeft) || 0) - (parseFloat(pc.paddingRight) || 0);
  if (!(available > 0)) return 1;
  const needed = el.scrollWidth;
  if (needed <= available) return 1;
  const current = parseFloat(getComputedStyle(el).fontSize) || 1;
  const scale = Math.min(maxScale, (available / needed) * 0.98);
  el.style.fontSize = `${Math.max(min, current * scale)}px`;
  return scale;
}

/** Point the backdrop at the map's artwork.
 *
 * The new image is fetched into an `Image` before anything is painted, and
 * only then cross-faded in over the previous one. Swapping `background-image`
 * directly shows the layer's backdrop colour for as long as the download
 * takes, which on a phone over Wi-Fi is a plainly visible flash -- and these
 * are full-size beatmap backgrounds, so it is not a short one.
 *
 * Only one cross-fade may be in flight at a time, and the layer that has faded
 * out is emptied shortly after. A phone decoding two full-size backgrounds at
 * once is a lot of memory to hold for decoration; iOS in particular reacts to
 * that by discarding the page, which is what a repeat visit to this screen was
 * doing.
 */
const backdropLayers = Array.from(document.querySelectorAll('#backdrop .bd'));
let backdropTop = 0;
let backdropKey = '';
let backdropShown = '';
let backdropToken = 0;
let backdropRelease = 0;

function applyBackdrop(front, url) {
  front.style.backgroundImage = url ? `url("${url}")` : '';
  for (let i = 0; i < backdropLayers.length; i++) {
    backdropLayers[i].classList.toggle('on', i === backdropTop);
  }
}

function scheduleBackdropRelease() {
  if (backdropRelease) clearTimeout(backdropRelease);
  backdropRelease = setTimeout(() => {
    backdropRelease = 0;
    const hidden = backdropLayers[1 - backdropTop];
    if (hidden) hidden.style.backgroundImage = '';
  }, BACKDROP_FADE_MS + 200);
}

function setBackdrop(beatmap) {
  const folder = beatmap && beatmap.folder;
  const background = beatmap && beatmap.background;
  const key = folder && background ? `${folder}/${background}` : '';

  // While a cross-fade is running, an empty key is a transition artefact --
  // osu! clears the beatmap object for a frame or two between maps -- not the
  // map being deselected. Keep what is on screen.
  if (!key && backdropShown && backdropToken) return;
  if (key === backdropKey) return;
  backdropKey = key;

  if (!key) {
    if (backdropRelease) { clearTimeout(backdropRelease); backdropRelease = 0; }
    backdropToken = 0;
    backdropShown = '';
    backdropTop = 0;
    for (const layer of backdropLayers) layer.style.backgroundImage = '';
    applyBackdrop(backdropLayers[0], '');
    return;
  }

  // Abandon a load already in flight: a new map is being selected, and two
  // decoded backgrounds is the memory cost this is trying to avoid.
  const token = ++backdropToken;
  const front = backdropLayers[1 - backdropTop];
  const show = () => {
    if (token !== backdropToken) return;
    backdropTop = 1 - backdropTop;
    backdropShown = key;
    applyBackdrop(front, `/api/background?v=${encodeURIComponent(key)}`);
    scheduleBackdropRelease();
  };

  // Without an Image constructor there is nothing to preload into; painting
  // directly is the old behaviour, which is worse but still correct.
  if (typeof Image !== 'function') { show(); return; }

  const image = new Image();
  image.onload = show;
  // If the fetch fails the map's art is simply missing; showing nothing is
  // better than leaving a half-painted layer behind.
  image.onerror = () => {
    if (token === backdropToken) applyBackdrop(backdropLayers[backdropTop], '');
  };
  image.src = `/api/background?v=${encodeURIComponent(key)}`;
}

/* ─────────────────────────────────────────────────────── page selection */

function pickPage(state) {
  if (!state.connected) return 'home';
  const st = state.state;
  if (PLAY_STATES.has(st)) return 'play';
  if (RESULTS_STATES.has(st)) return 'results';
  if (SELECT_STATES.has(st)) return 'select';
  return 'home';
}

/* The order the four screens appear in as a play runs, so showPage() can tell
 * a step forward from a step back and aim the movement accordingly. */
const PAGE_ORDER = ['home', 'select', 'play', 'results'];

/* How long an outgoing screen is kept laid out. `display: none` cannot be
 * animated from, so the exit needs the element to still exist; this is the
 * longest exit in the stylesheet (--pg-out, plus room for a late frame), and
 * has to be kept in step with it. */
const PAGE_LEAVE_MS = 150;

/* One pending release per screen, so a screen that comes back before its exit
 * has finished is not dropped by its own stale timer. */
const leaveTimers = new Map();

function showPage(page) {
  if (page === lastPage) return;
  const previous = lastPage;
  lastPage = page;

  // Forward through the game's own order rises; going back descends. Read by
  // both keyframes, so the outgoing block continues the same travel.
  const dir = PAGE_ORDER.indexOf(page) > PAGE_ORDER.indexOf(previous) ? 1 : -1;
  const shell = document.querySelector('#shell');
  if (shell) {
    shell.style.setProperty('--pg-dir', String(dir));
    /* The entry normally waits out the exit, but the first paint has nothing
     * to wait for -- holding it back there only delays the overlay appearing. */
    shell.style.setProperty('--pg-in-delay', previous ? '' : '0ms');
  }

  for (const p of PAGE_ORDER) {
    const el = els[`page-${p}`];
    if (!el) continue;
    const timer = leaveTimers.get(p);
    if (timer) { clearTimeout(timer); leaveTimers.delete(p); }
    // Reactivating a screen cancels its departure: leaving the class on would
    // keep the exit animation winning over the entry one.
    el.classList.remove('leaving');
    el.classList.toggle('active', p === page);
  }

  /* The screen just left stays displayed for the length of its exit. It is
   * only ever the middle of it that is still visible -- the shell it shares
   * with the incoming screen is identical and sits under it. Without motion
   * there is nothing to wait for, so it is dropped at once. */
  const gone = MOTION ? els[`page-${previous}`] : null;
  if (gone) {
    gone.classList.add('leaving');
    leaveTimers.set(previous, setTimeout(() => {
      leaveTimers.delete(previous);
      gone.classList.remove('leaving');
    }, PAGE_LEAVE_MS));
  }

  // Drives per-page styling, e.g. how strongly the artwork backdrop is blurred.
  document.body.dataset.page = page;
}

/* ───────────────────────────────────────────────────────────── rendering */

/* Judgement rails share their markup; only the class prefix differs. */
function judgesHtml(hits) {
  const rows = [
    ['300', hits.hit300, 'k300'],
    ['100', hits.hit100, 'k100'],
    ['50', hits.hit50, 'k50'],
    ['0', hits.miss, 'kmiss'],
  ];
  return rows.map(([label, value, cls]) =>
    `<div class="judge ${cls}"><b>${nf.format(value || 0)}</b><i>${label}</i></div>`
  ).join('');
}

function renderHome(state) {
  const player = state.player || {};
  const beatmap = state.beatmap || {};
  const server = state.bancho && state.bancho.enabled ? state.bancho : null;
  const stats = server && server.stats ? server.stats : null;

  setText(els['home-name'], player.name || '未登录');
  setText(els['home-sub'], player.name
    ? `Lv ${Math.floor(player.level || 0)} · ${player.bancho_status || 'idle'}`
    : '等待 osu! 登录');

  const avatar = player.id ? `/api/avatar?id=${encodeURIComponent(player.id)}` : '';
  if (avatar && els['home-avatar'].getAttribute('src') !== avatar) {
    els['home-avatar'].setAttribute('src', avatar);
  }
  els['home-avatar'].hidden = !avatar;

  // Total pp is the account's headline. Server-side data wins when present,
  // because that is the number the player is actually ranked by.
  const totalPp = stats ? stats.pp : player.pp;
  setText(els['home-pp'], Number.isFinite(totalPp) && totalPp > 0 ? nf.format(totalPp) : '—');
  setText(els['home-pp-label'], stats ? '总表现（服务器）' : '总表现');

  setText(els['home-acc'], player.accuracy ? `${nf2.format(player.accuracy)}%` : '—');
  setText(els['home-ranked'], player.ranked_score ? fmtCount(player.ranked_score) : '—');
  setText(els['home-plays'], player.play_count ? nf.format(player.play_count) : '—');
  const rank = stats && stats.global_rank;
  setText(els['home-rank'], rank ? `#${nf.format(rank)}` : '—');

  setText(els['home-song'], mapTitle(beatmap));
  setText(els['home-song-sub'], beatmap.difficulty ? `[${beatmap.difficulty}]` : '');
  els['home-tags'].innerHTML = tagsHtml([
    ['★', beatmap.object_count ? nf2.format((state.pp && state.pp.current && state.pp.current.stars) || 0) : ''],
    ['物件', beatmap.object_count ? nf.format(beatmap.object_count) : ''],
    ['AR', beatmap.ar ? nf2.format(beatmap.ar) : ''],
    ['OD', beatmap.od ? nf2.format(beatmap.od) : ''],
  ].filter(([, v]) => v && v !== '0.00'));

  setWidth(els['home-fill'], state.progress);
  const played = state.play_time || 0;
  setText(els['home-time'], fmtClock(played));
  setText(els['home-total'], fmtClock(state.audio_length || 0));
}

/* ── shared element factories ─────────────────────────────────────────────
 * These exist so a value can be *updated* rather than the markup rewritten.
 * Rebuilding the ladder on every state would throw away the elements the CSS
 * transitions are animating, and reset any tween in flight. */

function modsInto(container, mods) {
  if (!container) return;
  const list = (mods && mods.array) || [];
  const wanted = list.length ? list : null;
  const current = container.dataset.rendered || '';

  if (!wanted) {
    if (current !== 'none') {
      container.dataset.rendered = 'none';
      container.innerHTML = '<span class="mod none">NoMod</span>';
    }
    return;
  }
  if (current === wanted.join(' ')) return;
  container.dataset.rendered = wanted.join(' ');
  container.innerHTML = wanted.map((m) => `<span class="mod">${escapeHtml(m)}</span>`).join('');
}

/** The pp ladder, keyed by accuracy. Rebuilt only when the accuracies change;
 * otherwise the existing rungs are updated in place so their fill transitions. */
function ladderInto(container, entries, best) {
  if (!container) return;
  const signature = entries.map(([acc]) => acc).join(',');
  if (container.dataset.rendered !== signature) {
    container.dataset.rendered = signature;
    container.innerHTML = entries.map(([acc]) =>
      `<div class="rung" data-acc="${acc}"><b>—</b><i>${acc}%</i></div>`
    ).join('');
  }
  for (const [acc, value] of entries) {
    const rung = container.querySelector(`.rung[data-acc="${acc}"]`);
    if (!rung) continue;
    const weight = best > 0 ? Math.max(0.12, Math.min(1, value / best)) : 0.5;
    setRungFill(rung, weight);
    setText(rung.querySelector('b'), nf2.format(value));
  }
}

/** Crossfade elements that have just been given new content.
 *
 * The text is written first and unconditionally -- a title must never be left
 * blank waiting on a timer -- and the fade is a CSS animation restarted by
 * re-adding a class. An earlier version wrote the text inside a setTimeout,
 * which meant a throttled or never-run timer left the map name empty. */
function restartFade(nodes) {
  if (!MOTION || !nodes) return;
  for (const el of nodes) {
    if (!el) continue;
    el.classList.remove('is-swapping');
    // Reading this forces a style flush, so re-adding the class restarts the
    // animation instead of being ignored as a no-op.
    void el.offsetWidth;
    el.classList.add('is-swapping');
  }
}

function renderSelect(state) {
  const beatmap = state.beatmap || {};
  const pp = state.pp || {};
  const current = pp.current || {};
  const accList = pp.acc_list || {};

  const mapKey = `${beatmap.folder || ''}/${beatmap.filename || ''}`;
  if (mapKey !== selectMapKey) {
    selectMapKey = mapKey;
    // The identity of the map swaps outright; the figures below roll to their
    // new values, which reads as the data catching up to the title.
    setText(els['sel-artist'], artistOf(beatmap) || '—');
    setText(els['sel-title'], titleOf(beatmap) || (beatmap.filename ? mapTitle(beatmap) : '未选择谱面'));
    setText(els['sel-diff'], beatmap.difficulty ? `[${beatmap.difficulty}]` : '—');
    setText(els['sel-mapper'], beatmap.creator || '未知');
    setText(els['sel-objects'], beatmap.object_count ? nf.format(beatmap.object_count) : '—');
    setText(els['sel-ar'], beatmap.ar ? nf2.format(beatmap.ar) : '—');
    setText(els['sel-od'], beatmap.od ? nf2.format(beatmap.od) : '—');
    setText(els['sel-cs'], beatmap.cs ? nf2.format(beatmap.cs) : '—');
    setText(els['sel-hp'], beatmap.hp ? nf2.format(beatmap.hp) : '—');
    restartFade([els['sel-artist'], els['sel-title'], els['sel-diff'], els['sel-mapper']]);
    restartFade([els['sel-objects'], els['sel-ar'], els['sel-od'], els['sel-cs'], els['sel-hp']]);
  }

  // The ring encodes the star rating against a fixed 10★ ceiling; its arc is
  // driven by a registered property so it can animate.
  const stars = Number(current.stars) || 0;
  tweenNumber('sel.stars', stars, els['sel-stars'], (v) => nf2.format(v));
  setArc(els['sel-ring'], stars / 10);
  if (els['sel-ring']) els['sel-ring'].title = stars ? `${nf2.format(stars)} 星` : '';

  // A full-combo, 100% run of this map is the ceiling everything is relative to.
  const best = pp.error ? 0 : ((pp.max || {}).pp || current.pp || 0);
  tweenNumber('sel.maxpp', best, els['sel-maxpp'], (v) => nf2.format(v));
  tweenNumber('sel.fc', Number((pp.max || {}).pp) || 0, els['sel-pp-fc'], (v) => nf2.format(v));
  for (const acc of ['99', '98', '95']) {
    const value = Number(accList[acc]) || 0;
    tweenNumber(`sel.pp${acc}`, value, els[`sel-pp-${acc}`], (v) => nf2.format(v));
  }

  // The pp ladder is where the picking actually happens, so every accuracy gets
  // a bar. Fill is normalised against the 100% value: the 90% bar lands around
  // half height, which reads as the drop-off it is.
  const entries = Object.entries(accList)
    .map(([acc, value]) => [Number(acc), Number(value)])
    .filter(([acc, value]) => Number.isFinite(acc) && Number.isFinite(value))
    .sort((a, b) => a[0] - b[0]);
  ladderInto(els['sel-ladder'], entries, best);

  modsInto(els['sel-mods'], state.mods);

  setWidth(els['sel-fill'], state.progress);
  setText(els['sel-elapsed'], fmtClock(state.play_time || 0));
  setText(els['sel-total'], fmtClock(state.audio_length || 0));
}

function renderPlay(state) {
  const beatmap = state.beatmap || {};
  const gameplay = state.gameplay || {};
  const pp = state.pp || {};

  setText(els['play-map'], mapTitle(beatmap));
  // Difficulty only: the object count is already on the judgement rail's own
  // screen budget, and repeating it under the title crowds the header.
  setText(els['play-diff'], beatmap.difficulty ? `[${beatmap.difficulty}]` : '');

  setText(els['play-score'], nf.format(gameplay.score || 0));
  // Cap the shrink: the score may not collapse below the readouts under it.
  fitScoreToWidth(els['play-score'], { min: 26, maxScale: 0.9 });

  setText(els['play-combo'], `${nf.format(gameplay.combo || 0)}x`);
  setText(els['play-acc'], gameplay.accuracy ? `${nf2.format(gameplay.accuracy)}%` : '—');
  const livePp = (pp.current || {}).pp;
  setText(els['play-pp'], livePp > 0 ? nf2.format(livePp) : '—');

  // HP is 0..2 in osu!; colour it once it is worth worrying about.
  const hp = Math.max(0, Math.min(2, Number(gameplay.hp) || 0));
  els['play-hp-fill'].style.height = `${(hp / 2 * 100).toFixed(1)}%`;
  els['play-hp'].className = 'hp' + (hp <= 0.5 ? ' is-danger' : hp <= 1 ? ' is-warn' : '');

  els['play-judges'].innerHTML = judgesHtml(gameplay.hits || {});

  setWidth(els['play-fill'], state.progress);
  const played = state.play_time || 0;
  setText(els['play-time'], fmtClock(played));
  setText(els['play-total'], fmtClock(state.audio_length || 0));
}

function renderResults(state) {
  const score = shownResults;
  if (!score) {
    setText(els['res-song'], '暂无成绩');
    setText(els['res-diff'], '打完一张图后这里会显示成绩');
    setText(els['res-grade'], '—');
    els['res-grade'].dataset.grade = '';
    setText(els['res-score'], '—');
    els['res-judges'].innerHTML = '';
    els['res-mods'].innerHTML = '';
    setText(els['res-stars'], '');
    setText(els['res-meta'], '');
    return;
  }

  const beatmap = score.beatmap || {};
  const hits = score.hits || {};
  const player = state.player || {};

  // Grade first: it fixes how wide the verdict block becomes, and the score is
  // then fitted against whatever room is left.
  setText(els['res-grade'], score.grade || '—');
  els['res-grade'].dataset.grade = score.grade || '';
  setText(els['res-score'], nf.format(score.score || 0));
  fitScoreToWidth(els['res-score'], { min: 24 });

  const scorePp = (state.score_pp || {});
  const ppValue = (scorePp.current || {}).pp || 0;
  setText(els['res-pp'], ppValue > 0 ? nf2.format(ppValue) : '—');
  setText(els['res-acc'], score.accuracy ? `${nf2.format(score.accuracy)}%` : '—');
  setText(els['res-combo'], score.combo ? `${nf.format(score.combo)}x` : '—');

  setText(els['res-player'], score.player_name || player.name || '—');
  setText(els['res-song'], mapTitle(beatmap));
  setText(els['res-diff'], beatmap.difficulty
    ? `[${beatmap.difficulty}]${beatmap.creator ? ` · mapped by ${beatmap.creator}` : ''}`
    : '');

  const avatar = player.id ? `/api/avatar?id=${encodeURIComponent(player.id)}` : '';
  if (avatar && els['res-avatar'].getAttribute('src') !== avatar) {
    els['res-avatar'].setAttribute('src', avatar);
  }
  els['res-avatar'].hidden = !avatar;

  const stars = (scorePp.current || {}).stars;
  setText(els['res-stars'], stars ? `${nf2.format(stars)}★ 难度` : '');

  els['res-judges'].innerHTML = judgesHtml(hits);
  els['res-mods'].innerHTML = modsHtml(score.mods);

  const meta = [];
  if (score.max_combo) meta.push(`最大连击 ${nf.format(score.max_combo)}x`);
  if (beatmap.object_count) meta.push(`${nf.format(beatmap.object_count)} 物件`);
  if (score.captured_at) {
    meta.push(new Date(score.captured_at * 1000)
      .toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }));
  }
  setText(els['res-meta'], meta.join(' · '));
}

function render(state) {
  const connected = !!state.connected;

  // Resolve the blocks that flicker before anything reads them.
  const player = keepFilled(lastPlayer, state.player);
  if (state.player && Object.keys(state.player).length) lastPlayer = state.player;
  const beatmap = keepFilled(lastBeatmap, state.beatmap);
  if (beatmap && Object.keys(beatmap).length) lastBeatmap = beatmap;
  // An explicit disconnect means the client is gone, not mid-transition: drop
  // the carried-over data so the overlay is not backed by stale values.
  if (!connected) { lastPlayer = {}; lastBeatmap = {}; }

  const page = pickPage({ ...state, player, beatmap });
  showPage(page);
  setBackdrop(beatmap);

  // Remember the newest finished score. `last_score` is only populated once a
  // play has happened, and never cleared, so it survives the song-select
  // transition that ends the results screen.
  if (state.last_score) {
    const stamp = state.last_score.captured_at || 0;
    if (!lastPlayScore || stamp > (lastPlayScore.captured_at || 0)) {
      lastPlayScore = state.last_score;
    }
  }
  if (page === 'results') shownResults = lastPlayScore;

  const playing = page === 'play';

  /* osu! frees its play structures the moment a map ends, so a state can
   * briefly stop arriving right at the play → results hand-off. Showing the
   * "waiting for osu!" overlay for that flash is worse than showing the last
   * screen a moment longer, so the overlay waits out a short grace period and
   * anything shorter than that is never seen at all. */
  offlineReason = state.error || offlineReason;
  if (connected) {
    if (offlineTimer) { clearTimeout(offlineTimer); offlineTimer = null; }
    els.offline.classList.remove('active');
  } else if (!offlineTimer && !els.offline.classList.contains('active')) {
    offlineTimer = setTimeout(() => {
      offlineTimer = null;
      setText(els['offline-hint'],
        offlineReason ? `错误：${offlineReason}` : '正在等待 osu! 客户端…');
      els.offline.classList.add('active');
    }, OFFLINE_GRACE_MS);
  }

  // One broken panel must not take the rest of the page down with it. The
  // painters get the merged blocks, so what they draw matches what chose the
  // page even when this frame arrived with an empty profile or beatmap.
  const view = { ...state, player, beatmap };
  let renderError = null;
  try {
    if (page === 'home') renderHome(view);
    else if (page === 'select') renderSelect(view);
    else if (page === 'play') renderPlay(view);
    else renderResults(view);
  } catch (err) {
    renderError = `${err.name}: ${err.message}`;
    console.error(`render(${page}) failed`, err);
  }

  // A render fault is a real fault, not a transient gap: surface it at once
  // rather than after the offline grace period.
  if (renderError) {
    if (offlineTimer) { clearTimeout(offlineTimer); offlineTimer = null; }
    setText(els['offline-hint'], `界面渲染出错：${renderError}`);
    els.offline.classList.add('active');
  }

  // After painting, so the header being measured is the one now on screen.
  syncHeadBand();

  const clock = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  for (const bar of statusBars) {
    setText(bar.time, clock);
    if (bar.dot) bar.dot.className = 'dot' + (connected ? (playing ? ' live' : ' idle') : '');
    setText(bar.text, connected ? '已连接' : '未连接');
  }
}

/* ─────────────────────────────────────────────────────────────── feed */

let token = new URLSearchParams(location.search).get('token') || '';
let source = null;
let pollTimer = null;

function feedUrl(path) {
  return token ? `${path}?token=${encodeURIComponent(token)}` : path;
}

function connect() {
  if (source) source.close();
  source = new EventSource(feedUrl('/api/events'));
  source.onmessage = (event) => {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    try { render(JSON.parse(event.data)); }
    catch (err) { console.error('bad payload', err); }
  };
  source.onerror = () => {
    // EventSource retries by itself; poll meanwhile so a rejected token still
    // surfaces a 401 through fetch.
    if (!pollTimer) pollTimer = setInterval(pollOnce, 1000);
  };
}

async function pollOnce() {
  try {
    const response = await fetch(feedUrl('/api/state'));
    if (response.status === 401) { showTokenPrompt(); return; }
    render(await response.json());
  } catch (err) { console.error(err); }
}

function showTokenPrompt() {
  els['token-row'].hidden = false;
  els['token-save'].onclick = () => {
    token = els['token-input'].value.trim();
    history.replaceState(null, '', token ? `?token=${encodeURIComponent(token)}` : location.pathname);
    els['token-row'].hidden = true;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    connect();
  };
}

/* ───────────────────────────────────────────────────────────── demo mode */

/* `?demo=home|select|play|results` paints that screen from a fixed sample
 * payload instead of connecting, so a layout can be checked on a real phone
 * without having to reach the matching game state. It listens to nothing and
 * writes nothing; leaving it out of the URL changes no other behaviour. */
const DEMO_PLAYER = {
  name: 'Yuzu', id: 1, level: 86.27, accuracy: 98.1, play_count: 5124,
  ranked_score: 812_345_678, pp: 4597, play_mode: 0, bancho_status: 'idle',
};
const DEMO_BEATMAP = {
  artist: 'irohaRingo feat. flower', title: 'Why I hate you', difficulty: "O K U's Ex",
  // The originals for this map, so the demo exercises the same path a
  // non-English map takes rather than always falling back.
  artist_unicode: 'いろは蘋果 feat. flower', title_unicode: '歪なわけ、教えてあげる',
  creator: 'Ryuusei Aika', ar: 9, cs: 4, hp: 7, od: 8, object_count: 995,
  map_id: 0, folder: '', background: '',
};
const DEMO_PP = {
  error: null,
  current: { pp: 251.4, stars: 5.755 },
  max: { pp: 264.723 },
  acc_list: { '100': 264.723, '99': 233.625, '98': 210.69, '97': 194.115, '95': 172.178, '90': 138.949 },
};

function demoState(name) {
  const base = {
    connected: true, error: null, player: DEMO_PLAYER, beatmap: DEMO_BEATMAP,
    mods: { array: ['HD', 'HR'], bits: 0, rate: 1 },
    bancho: { enabled: true, ok: true, name: 'Yuzu', stats: { pp: 4597, global_rank: 128, accuracy: 98.1, total_score: 1_204_000_000 } },
    pp: DEMO_PP, progress: 0.42, play_time: 101_000, audio_length: 240_744,
    gameplay: null,
  };
  if (name === 'select') return { ...base, state: 'selectPlay' };
  if (name === 'play') {
    return {
      ...base, state: 'play', progress: 0.61, play_time: 147_000,
      mods: { array: ['HD', 'DT'], bits: 0, rate: 1.5 },
      gameplay: {
        score: 48_271_930, combo: 812, max_combo: 1104, accuracy: 98.42, grade: 'S', hp: 1.32,
        hits: { hit300: 712, hit100: 21, hit50: 0, geki: 140, katu: 12, miss: 2 },
      },
      pp: { ...DEMO_PP, current: { pp: 268.9, stars: 5.755 } },
    };
  }
  if (name === 'results') {
    return {
      ...base, state: 'resultScreen', progress: 1,
      last_score: {
        player_name: 'Yuzu', mode: 0, score: 46_517_430, combo: 1539, max_combo: 1539,
        accuracy: 98.86, grade: 'S', captured_at: Math.floor(Date.now() / 1000),
        hits: { hit300: 978, hit100: 17, hit50: 0, geki: 177, katu: 15, miss: 0 },
        mods: { array: [], bits: 0, rate: 1 }, beatmap: DEMO_BEATMAP,
      },
      score_pp: { current: { pp: 230.033, stars: 5.755 } },
    };
  }
  return { ...base, state: 'menu' };
}

const demo = new URLSearchParams(location.search).get('demo');
if (demo && ['home', 'select', 'play', 'results'].includes(demo)) {
  render(demoState(demo));
} else {
  connect();
}

/* A handle for driving the overlay from the console or a test page. The
 * animation work cannot be checked from a screenshot -- a still frame says
 * nothing about whether a value travelled or jumped -- so the entry point is
 * exposed deliberately, alongside the state it renders from. */
if (typeof window !== 'undefined') {
  window.osuLive = { render, demoState, els, MOTION };
}

