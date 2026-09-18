/* osu! live — phone overlay.
 *
 * Three screens driven purely by the game state the local program reports:
 *   home    menu / idle            → now-playing + account
 *   select  song select / editing  → map detail, star rating, theoretical pp
 *   play    a map is being played  → score, accuracy, grade, judgement counts
 *   results after a play finishes  → the finished score
 *
 * The page is chosen in `pickPage()`; every other function only paints.
 */
'use strict';

const $ = (id) => document.getElementById(id);

const els = {};
for (const id of [
  'backdrop', 'shell',
  'page-home', 'page-select', 'page-play', 'page-results',
  'home-clock', 'home-dot', 'home-conn-text', 'home-avatar', 'home-name',
  'home-sub', 'home-pp', 'home-song', 'home-song-sub', 'home-tags', 'home-fill', 'home-stats',
  'sel-artist', 'sel-title', 'sel-diff', 'sel-mapper', 'sel-stars', 'sel-ar',
  'sel-od', 'sel-cs', 'sel-hp', 'sel-maxpp', 'sel-ppacc', 'sel-fill', 'sel-time',
  'sel-mods', 'sel-total', 'sel-extra',
  'play-map', 'play-diff', 'play-score', 'play-combo', 'play-grade', 'play-acc',
  'play-pp', 'play-hp-fill', 'play-judgements', 'play-fill',
  'res-grade', 'res-song', 'res-diff', 'res-score', 'res-score-land', 'res-acc', 'res-combo',
  'res-pp', 'res-hits', 'res-mods', 'res-meta',
  'res-avatar', 'res-player', 'res-player-pp',
  'offline', 'offline-hint', 'token-row', 'token-input', 'token-save',
]) {
  els[id] = $(id);
}

/* Every page carries its own status bar, so each needs the clock and the live
 * indicator updated -- not just the home page's. Collected once here rather
 * than repeating the same ids in the lookup table above. */
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

let lastPlayScore = null;   // newest finished score seen after a play
let shownResults = null;    // the score currently on the results screen
let lastPage = '';
let lastBackground = '';

/* ─────────────────────────────────────────────────────────── utilities */

function setText(el, value) {
  const text = value === null || value === undefined ? '—' : String(value);
  if (el.textContent !== text) el.textContent = text;
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
  if (!Number.isFinite(value) || value <= 0) return '—';
  return nf2.format(value);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function modsHtml(mods, emptyLabel = 'NoMod') {
  const list = (mods && mods.array) || [];
  if (!list.length) return `<span class="mod none">${emptyLabel}</span>`;
  return list.map((m) => `<span class="mod">${escapeHtml(m)}</span>`).join('');
}

function mapTitle(beatmap) {
  if (!beatmap || (!beatmap.title && !beatmap.filename)) return '—';
  return beatmap.title
    ? `${beatmap.artist} - ${beatmap.title}`
    : beatmap.filename.replace(/\.osu$/, '');
}


/** Shrink a numeric element until it fits its box.
 *
 * Scores run from six to ten digits, and the grade letter beside them must
 * never be overlapped or have digits clipped off. Measuring the laid-out width
 * is exact, where picking a size per digit count only ever approximates.
 */
function fitScoreToWidth(el) {
  el.style.fontSize = '';
  const parent = el.parentElement;
  if (!parent) return;
  // clientWidth includes the parent's padding, which is not space the text can
  // actually use -- comparing against it lets the number overflow a padded
  // panel (the results card) by exactly that padding.
  const pc = getComputedStyle(parent);
  const available = parent.clientWidth
    - parseFloat(pc.paddingLeft || 0) - parseFloat(pc.paddingRight || 0);
  if (!(available > 0)) return;
  // scrollWidth reflects the unconstrained text width at the current size.
  const needed = el.scrollWidth;
  if (needed > available) {
    const current = parseFloat(getComputedStyle(el).fontSize) || 1;
    el.style.fontSize = `${Math.max(18, current * (available / needed) * 0.98)}px`;
  }
}

/** Point the backdrop at the map's artwork, re-requesting only when it changes. */
function setBackdrop(beatmap) {
  const folder = beatmap && beatmap.folder;
  const background = beatmap && beatmap.background;
  const key = folder && background ? `${folder}/${background}` : '';
  if (key === lastBackground) return;
  lastBackground = key;
  els.backdrop.style.backgroundImage = key
    ? `url("/api/background?v=${encodeURIComponent(key)}")`
    : '';
}

/* ─────────────────────────────────────────────────────── page selection */

function pickPage(state) {
  const st = state.state;
  const name = (state.player && state.player.name) || '';
  if (!state.connected) return 'home';
  if (PLAY_STATES.has(st)) return 'play';
  if (RESULTS_STATES.has(st)) return 'results';
  if (SELECT_STATES.has(st)) return 'select';
  return 'home';
}

function showPage(page) {
  if (page === lastPage) return;
  for (const p of ['home', 'select', 'play', 'results']) {
    els[`page-${p}`].classList.toggle('active', p === page);
  }
  // Drives per-page styling, e.g. how strongly the artwork backdrop is blurred.
  document.body.dataset.page = page;
  lastPage = page;
}

/* ───────────────────────────────────────────────────────────── rendering */

function renderHome(state) {
  const player = state.player || {};
  setText(els['home-name'], player.name || '未登录');
  setText(els['home-sub'], player.name
    ? `Lv ${Math.floor(player.level || 0)} · ${nf2.format(player.accuracy || 0)}% · ${nf.format(player.play_count || 0)} 次游玩`
    : '');
  const avatar = player.id ? `/api/avatar?id=${encodeURIComponent(player.id)}` : '';
  if (avatar && els['home-avatar'].getAttribute('src') !== avatar) {
    els['home-avatar'].setAttribute('src', avatar);
  }
  els['home-avatar'].hidden = !avatar;

  const serverStats = (state.bancho && state.bancho.stats) || null;
  const totalPp = serverStats ? serverStats.pp : player.pp;
  setText(els['home-pp'], Number.isFinite(totalPp) && totalPp > 0 ? nf.format(totalPp) : '—');

  const beatmap = state.beatmap || {};
  setText(els['home-song'], mapTitle(beatmap));
  setText(els['home-song-sub'], beatmap.difficulty ? `[${beatmap.difficulty}] · ${beatmap.creator || '?'}` : '');

  const tags = [];
  if (beatmap.ar) tags.push(`${nf2.format(beatmap.ar)} AR`);
  if (beatmap.od) tags.push(`${nf2.format(beatmap.od)} OD`);
  if (beatmap.object_count) tags.push(`${nf.format(beatmap.object_count)} 物件`);
  els['home-tags'].innerHTML = tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('');

  // The map is loaded on the home screen too, so show how far into it we are.
  els['home-fill'].style.width = `${Math.round((state.progress || 0) * 100)}%`;

  const stats = [];
  if (serverStats) {
    stats.push(['排名', serverStats.global_rank ? `#${nf.format(serverStats.global_rank)}` : '—']);
    stats.push(['准确率', `${nf2.format(serverStats.accuracy)}%`]);
    stats.push(['总分', fmtCount(serverStats.total_score)]);
  } else {
    stats.push(['准确率', player.accuracy ? `${nf2.format(player.accuracy)}%` : '—']);
    stats.push(['总分', player.ranked_score ? fmtCount(player.ranked_score) : '—']);
    stats.push(['游玩', player.play_count ? nf.format(player.play_count) : '—']);
  }
  els['home-stats'].innerHTML = stats.map(([label, value]) =>
    `<div class="stat"><div class="stat-label">${escapeHtml(label)}</div>` +
    `<div class="stat-value">${escapeHtml(value)}</div></div>`
  ).join('');
}

function renderSelect(state) {
  const beatmap = state.beatmap || {};
  const pp = state.pp || {};
  const current = pp.current || {};

  setText(els['sel-artist'], beatmap.artist || '—');
  setText(els['sel-title'], beatmap.title || (beatmap.filename ? mapTitle(beatmap) : '未选择谱面'));
  setText(els['sel-diff'], beatmap.difficulty || '—');
  setText(els['sel-mapper'], beatmap.creator ? `mapped by ${beatmap.creator}` : '');

  setText(els['sel-stars'], current.stars ? nf2.format(current.stars) : '—');
  setText(els['sel-ar'], beatmap.ar ? nf2.format(beatmap.ar) : '—');
  setText(els['sel-od'], beatmap.od ? nf2.format(beatmap.od) : '—');
  setText(els['sel-cs'], beatmap.cs ? nf2.format(beatmap.cs) : '—');
  setText(els['sel-hp'], beatmap.hp ? nf2.format(beatmap.hp) : '—');

  // Theoretical pp is the value for a full-combo, 100% run of this map.
  const best = pp.error ? 0 : ((pp.max || {}).pp || current.pp || 0);
  setText(els['sel-maxpp'], best > 0 ? nf2.format(best) : '—');

  const accList = pp.acc_list || {};
  const entries = Object.entries(accList).sort((a, b) => Number(b[0]) - Number(a[0]));
  els['sel-ppacc'].innerHTML = entries.length
    ? entries.map(([acc, value]) =>
        `<div class="acc-cell"><div class="acc-label">${acc}%</div>` +
        `<div class="acc-value">${nf2.format(value)}</div></div>`
      ).join('')
    : '';

  els['sel-mods'].innerHTML = modsHtml(state.mods);

  const position = state.play_time || 0;
  const duration = state.audio_length || 0;
  els['sel-fill'].style.width = `${Math.round((state.progress || 0) * 100)}%`;
  setText(els['sel-time'], fmtClock(position));
  setText(els['sel-total'], fmtClock(duration));

  const extra = [
    ['物件', beatmap.object_count ? nf.format(beatmap.object_count) : '—'],
    ['谱面 ID', beatmap.map_id ? `#${beatmap.map_id}` : '—'],
    ['难度名', beatmap.difficulty || '—'],
  ];
  els['sel-extra'].innerHTML = extra.map(([label, value]) =>
    `<div class="meta-item"><div class="meta-label">${escapeHtml(label)}</div>` +
    `<div class="meta-value">${escapeHtml(value)}</div></div>`
  ).join('');
}

function renderPlay(state) {
  const beatmap = state.beatmap || {};
  const gameplay = state.gameplay || {};
  const pp = state.pp || {};

  setText(els['play-map'], mapTitle(beatmap));
  setText(els['play-diff'], beatmap.difficulty
    ? `[${beatmap.difficulty}]${beatmap.object_count ? ` · ${nf.format(beatmap.object_count)} 物件` : ''}`
    : '');

  const scoreText = nf.format(gameplay.score || 0);
  setText(els['play-score'], scoreText);
  fitScoreToWidth(els['play-score']);
  setText(els['play-combo'], `${nf.format(gameplay.combo || 0)}x`);
  setText(els['play-grade'], gameplay.grade || '—');
  setText(els['play-acc'], gameplay.accuracy ? `${nf2.format(gameplay.accuracy)}%` : '—');
  setText(els['play-pp'], (pp.current || {}).pp > 0 ? `${nf2.format(pp.current.pp)} pp` : '');

  els['play-hp-fill'].style.width = `${Math.max(0, Math.min(100, (gameplay.hp || 0) / 2))}%`;
  els['play-fill'].style.width = `${Math.round((state.progress || 0) * 100)}%`;

  const hits = gameplay.hits || {};
  const rows = [
    ['300', hits.hit300, 'k300'],
    ['100', hits.hit100, 'k100'],
    ['50', hits.hit50, 'k50'],
    ['0', hits.miss, 'kmiss'],
  ];
  els['play-judgements'].innerHTML = rows.map(([label, value, cls]) =>
    `<div class="hit ${cls}"><div class="hit-label">${label}</div>` +
    `<div class="hit-value">${nf.format(value || 0)}</div></div>`
  ).join('');
}

function renderResults(state) {
  const score = shownResults;
  if (!score) {
    setText(els['res-song'], '暂无成绩');
    setText(els['res-diff'], '');
    return;
  }
  const beatmap = score.beatmap || {};
  const hits = score.hits || {};
  const player = state.player || {};

  setText(els['res-grade'], score.grade || '—');
  els['res-grade'].dataset.grade = score.grade || '';
  setText(els['res-song'], mapTitle(beatmap));
  setText(els['res-diff'], beatmap.difficulty
    ? `Difficulty: ${beatmap.difficulty}${beatmap.creator ? ` · Mapped by ${beatmap.creator}` : ''}`
    : '');
  setText(els['res-score'], nf.format(score.score || 0));
  setText(els['res-score-land'], nf.format(score.score || 0));  // landscape card
  // Grade first: it decides how wide the panel gets, and the score is fitted
  // against that resulting width.
  fitGradeRow(els['res-grade']);
  fitScoreToWidth(els['res-score']);
  fitScoreToWidth(els['res-score-land']);
  setText(els['res-acc'], score.accuracy ? `${nf2.format(score.accuracy)}%` : '—');
  setText(els['res-combo'], score.combo ? `${nf.format(score.combo)}x` : '—');

  // The score's own pp: `score_pp` is recalculated from the captured hits,
  // because osu! clears the play structures on song end and the live block
  // would report the map's theoretical maximum instead.
  const scorePp = (state.score_pp || {});
  const ppValue = (scorePp.current || {}).pp || 0;
  setText(els['res-pp'], ppValue > 0 ? `${nf2.format(ppValue)}` : '—');

  const playerName = score.player_name || player.name || '';
  setText(els['res-player'], playerName || '—');
  const playerId = player.id;
  const avatar = playerId ? `/api/avatar?id=${encodeURIComponent(playerId)}` : '';
  if (avatar && els['res-avatar'].getAttribute('src') !== avatar) {
    els['res-avatar'].setAttribute('src', avatar);
  }
  els['res-avatar'].hidden = !avatar;
  setText(els['res-player-pp'], Number.isFinite(player.pp) && player.pp > 0
    ? `performance ${nf.format(player.pp)}pp`
    : '');

  const rows = [
    ['300', hits.hit300, 'k300'],
    ['100', hits.hit100, 'k100'],
    ['50', hits.hit50, 'k50'],
    ['0', hits.miss, 'kmiss'],
  ];
  els['res-hits'].innerHTML = rows.map(([label, value, cls]) =>
    `<div class="judge ${cls}"><div class="judge-label">${label}</div>` +
    `<div class="judge-value">${nf.format(value || 0)}</div></div>`
  ).join('');

  els['res-mods'].innerHTML = modsHtml(score.mods);

  const meta = [];
  if (score.max_combo) meta.push(`最大连击 ${nf.format(score.max_combo)}x`);
  if (scorePp.current && scorePp.current.stars) meta.push(`${nf2.format(scorePp.current.stars)}★`);
  if (beatmap.object_count) meta.push(`${nf.format(beatmap.object_count)} 物件`);
  els['res-meta'].innerHTML = meta.map((m) => 
    `<div class="meta-item"><div class="meta-value">${escapeHtml(m)}</div></div>`
  ).join('');
}
function render(state) {
  const page = pickPage(state);
  showPage(page);
  setBackdrop(state.beatmap);

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

  const connected = !!state.connected;
  const playing = page === 'play';
  const offline = !connected;
  els.offline.classList.toggle('active', offline);
  if (offline) {
    setText(els['offline-hint'], state.error ? `错误：${state.error}` : '正在等待 osu! 客户端…');
  }

  // One broken panel must not take the rest of the page down with it.
  let renderError = null;
  try {
    if (page === 'home') renderHome(state);
    else if (page === 'select') renderSelect(state);
    else if (page === 'play') renderPlay(state);
    else renderResults(state);
  } catch (err) {
    renderError = `${err.name}: ${err.message}`;
    console.error(`render(${page}) failed`, err);
  }

  // The offline overlay sits above everything, so its text is only worth
  // overwriting when the client is actually gone.
  if (renderError) {
    setText(els['offline-hint'], `界面渲染出错：${renderError}`);
    els.offline.classList.add('active');
  }

  const clock = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  for (const bar of statusBars) {
    setText(bar.time, clock);
    if (bar.dot) bar.dot.className = 'dot' + (connected ? (playing ? ' live' : ' idle') : '');
    setText(bar.text, connected ? '已连接' : '未连接');
  }
}

/** Shrink the grade so the score panel keeps enough width to stay readable.
 *
 * The grade scales with viewport height, which is what makes it the headline.
 * Two-letter ranks (SH/XH) are far wider than one, and a long score needs real
 * room beside it, so the size is bounded by measurement rather than guessed
 * per rank. Must run BEFORE the score is fitted: the grade is what decides how
 * wide the panel ends up, and the score is fitted against that width.
 */
function fitGradeRow(gradeEl) {
  const row = gradeEl.parentElement;
  if (!row) return;
  const scoreBox = row.querySelector(".res-score-box");
  gradeEl.style.fontSize = '';
  const rowWidth = row.clientWidth;
  if (!rowWidth) return;
  const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
  // The score card needs real room for a full nine-digit number. The rank
  // letter is what gives up the space, since it scales with the viewport.
  const reserve = scoreBox ? Math.max(140, rowWidth * 0.42) : 0;
  const maxGradeWidth = rowWidth - gap - reserve;
  const needed = gradeEl.scrollWidth;
  if (needed <= maxGradeWidth) return;
  const current = parseFloat(getComputedStyle(gradeEl).fontSize) || 1;
  gradeEl.style.fontSize =
    `${Math.max(48, current * (maxGradeWidth / needed) * 0.98)}px`;
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

connect();
