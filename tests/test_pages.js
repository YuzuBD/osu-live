/* Runs with plain node; no dependencies. Assumes the repository layout:
 * this file lives in tests/ and the web assets in osu_live/web/. */
/* Regression suite for the screen transition -- what happens when the overlay
   moves between home, song select, play and results.

   The switch is driven by the game, so it happens on its own schedule and can
   be interrupted half way through. What matters is the bookkeeping around it,
   which is the part no screenshot can show: which screen is still laid out,
   that one dropped mid-exit does not stay on screen, and that a screen coming
   back before its own exit has finished is not dropped by a stale timer.

   The movement itself is CSS. It is checked only for the one coupling that
   spans the two files -- the release time in JS has to outlast the exit in the
   stylesheet -- and the animation is switched off in the one place it could
   otherwise lie: with no rAF to drive it. */
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const dir = path.join(__dirname, '..', 'osu_live', 'web') + path.sep;
const appjs = fs.readFileSync(dir + 'app.js', 'utf8').replace(/^connect\(\);\s*$/m, '');
const style = fs.readFileSync(dir + 'style.css', 'utf8');
const html = fs.readFileSync(dir + 'index.html', 'utf8');
const realIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));

const PAGES = ['home', 'select', 'play', 'results'];

let fails = 0;
const fail = (m) => { fails++; console.log('  FAIL  ' + m); };
const pass = (m) => console.log('  pass  ' + m);
const chk = (l, c, x = '') => (c ? pass(l + (x ? ' :: ' + x : '')) : fail(l + (x ? ' :: got ' + x : '')));

/* ── harness ──────────────────────────────────────────────────────────────
 * app.js is evaluated in its own context so it can be booted twice: once with
 * rAF present, once without. Timers are collected rather than run, so a test
 * can stop the clock in the middle of an exit and look at what is on screen.
 */

function makeEl(id) {
  const props = {};
  const classes = new Set();
  const el = {
    id, textContent: '', innerHTML: '', hidden: false, dataset: {}, value: '',
    onclick: null, title: '', scrollWidth: 100, clientWidth: 500, parentElement: null,
    getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  el.style = {
    setProperty(k, v) { props[k] = String(v); },
    getPropertyValue(k) { return props[k] ?? ''; },
    _props: props,
  };
  for (const k of ['width', 'height', 'fontSize', 'backgroundImage', 'opacity']) {
    Object.defineProperty(el.style, k, { get: () => props[k] ?? '', set: (v) => { props[k] = v; }, configurable: true });
  }
  Object.defineProperty(el, 'className', {
    get: () => [...classes].join(' '),
    set(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
  });
  el.classList = {
    add: (...c) => c.forEach((x) => classes.add(x)),
    remove: (...c) => c.forEach((x) => classes.delete(x)),
    contains: (c) => classes.has(c),
    toggle: (c, on) => { const w = on === undefined ? !classes.has(c) : !!on; w ? classes.add(c) : classes.delete(c); return w; },
  };
  return el;
}

function boot({ motion }) {
  const created = new Map();
  const timers = new Map();
  let seq = 0;

  const el = (id) => {
    if (!created.has(id)) created.set(id, makeEl(id));
    return created.get(id);
  };
  /* Stands in for both `#shell` and `.page.active .head`: the app only ever
   * writes custom properties into the one and reads a rect off the other. */
  const probe = {
    style: {
      _p: {},
      setProperty(k, v) { this._p[k] = String(v); },
      getPropertyValue(k) { return this._p[k] ?? ''; },
    },
    getBoundingClientRect: () => ({ top: 0, bottom: 64, left: 0, right: 800, width: 800, height: 64 }),
    children: [],
  };
  const bd = [el('bd1'), el('bd2')];

  const document = {
    getElementById(id) { return realIds.has(id) ? el(id) : null; },
    querySelector() { return probe; },
    querySelectorAll: (sel) => (sel && sel.indexOf('.bd') !== -1 ? bd : []),
    body: makeEl('body'),
  };

  const sandbox = {
    console,
    document,
    location: { search: '', pathname: '/' },
    history: { replaceState() {} },
    performance: { now: () => Date.now() },
    URLSearchParams,
    getComputedStyle: () => ({ paddingLeft: '10', paddingRight: '10', fontSize: '40px', columnGap: '10' }),
    Image: class { set src(v) { this._src = v; if (this.onload) this.onload(); } },
    EventSource: class { constructor() { this.close = () => {}; } },
    fetch: async () => ({ status: 200, json: async () => ({}) }),
    setTimeout: (fn, ms) => { const id = ++seq; timers.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => { timers.delete(id); },
    setInterval: () => 0,
    clearInterval: () => {},
  };
  if (motion) sandbox.requestAnimationFrame = (fn) => { void fn; return 0; };

  vm.createContext(sandbox);
  vm.runInContext(appjs + '\nglobalThis.__render = render; globalThis.__MOTION = MOTION;', sandbox);

  const flush = () => {
    const due = [...timers.entries()];
    timers.clear();
    for (const [, t] of due) t.fn();
  };
  const view = () => ({
    active: PAGES.filter((p) => el('page-' + p).classList.contains('active')),
    leaving: PAGES.filter((p) => el('page-' + p).classList.contains('leaving')),
    dir: probe.style.getPropertyValue('--pg-dir'),
    body: document.body.dataset.page,
  });

  return { render: sandbox.__render, MOTION: sandbox.__MOTION, flush, view, pending: () => timers.size };
}

/* Enough state for every painter to run; this suite is about the switch, not
   about what the figures say. */
const score = {
  player_name: 'Yuzu', score: 46517430, combo: 1539, max_combo: 1539, accuracy: 98.86, grade: 'S',
  captured_at: 1789717662, hits: { hit300: 978, hit100: 17, hit50: 0, miss: 0 },
  mods: { array: [], bits: 0, rate: 1 },
  beatmap: { artist: 'a', title: 't', difficulty: 'd', object_count: 995 },
};
const scen = {
  home: { connected: true, state: 'menu', player: { name: 'Yuzu', pp: 4597 }, beatmap: {}, pp: {}, progress: 0, play_time: 0, audio_length: 0 },
  select: { connected: true, state: 'selectPlay', player: { name: 'Yuzu' }, beatmap: { artist: 'a', title: 't', difficulty: 'd' }, pp: {}, progress: 0, play_time: 0, audio_length: 0 },
  play: { connected: true, state: 'play', player: { name: 'Yuzu' }, beatmap: { artist: 'a', title: 't' }, gameplay: { score: 1, combo: 1, accuracy: 99, hp: 1, hits: { hit300: 1 } }, pp: {}, progress: 0, play_time: 0, audio_length: 0 },
  results: { connected: true, state: 'resultScreen', player: { name: 'Yuzu' }, beatmap: { artist: 'a', title: 't' }, last_score: score, score_pp: {}, progress: 1, play_time: 0, audio_length: 0 },
};

/* ── the timing the two files have to agree on ─────────────────────────── */

console.log('== static ==');
const num = (src, re) => Number((src.match(re) || [])[1]);
const leaveMs = num(appjs, /const PAGE_LEAVE_MS = (\d+);/);
const outMs = num(style, /--pg-out:\s*(\d+)ms/);
const inDelayMs = num(style, /--pg-in-delay:\s*(\d+)ms/);
chk('PAGE_LEAVE_MS is declared', Number.isFinite(leaveMs), String(leaveMs));
chk('--pg-out is declared', Number.isFinite(outMs), String(outMs));
chk('--pg-in-delay is declared', Number.isFinite(inDelayMs), String(inDelayMs));
/* The hand-over. These three are the same fact written in three places and
 * nothing but this ties them together. Getting the first one wrong is what
 * made two screens legible at once: the entry has to wait out the exit. */
chk('the entry starts after the exit ends', inDelayMs >= outMs, `${inDelayMs}ms >= ${outMs}ms`);
chk('a leaving screen outlives its own exit', leaveMs >= outMs, `${leaveMs}ms >= ${outMs}ms`);

/* ── every painted element has to be animated ──────────────────────────────
 * This is the bug class behind the doubled text. A screen is a fixed layer
 * over the previous one and has no background of its own, so anything left
 * out of the animation is drawn by both screens at once -- identical footing
 * for a moment, then two different values on top of each other. Checking it
 * structurally is the only way to catch it: on screen it just looks wrong,
 * and only while the switch is happening.
 */
const section = style
  .slice(style.indexOf('switching screens'), style.indexOf('@keyframes pg-in'))
  /* Comments are stripped first: the prose above a rule lands in the same
   * split as its first selector and would hide it. */
  .replace(/\/\*[\s\S]*?\*\//g, '');
const animated = new Set();
for (const block of section.matchAll(/([^{}]+)\{/g)) {
  for (const sel of block[1].split(',')) {
    const s = sel.trim();
    if (!s.startsWith('.page.active')) continue;
    const last = s.match(/\.([a-z][\w-]*)$/);
    if (last && last[1] !== 'active') animated.add(last[1]);
  }
}
chk('the transition animates something', animated.size > 0, [...animated].join(' '));

/* A tag-depth scan is enough to find which elements carry text of their own;
 * index.html has no scripting and its comments are stripped first. */
const VOID = new Set(['img', 'br', 'input', 'hr', 'meta', 'link']);
function parse(body) {
  const root = { tag: 'root', cls: [], text: false, children: [] };
  const stack = [root];
  const re = /<(\/?)([a-z]+)([^>]*?)(\/?)>/g;
  let last = 0, m;
  while ((m = re.exec(body))) {
    if (body.slice(last, m.index).trim()) stack[stack.length - 1].text = true;
    last = re.lastIndex;
    const [, close, tag, attrs, selfClose] = m;
    if (close) { stack.pop(); continue; }
    const cls = ((attrs.match(/class="([^"]*)"/) || [, ''])[1]).split(/\s+/).filter(Boolean);
    const node = { tag, cls, text: false, children: [] };
    stack[stack.length - 1].children.push(node);
    if (!VOID.has(tag) && !selfClose) stack.push(node);
  }
  return root;
}
/* The status rail is the deliberate exception: its text really is the same on
 * all four screens, so it is held still on purpose. */
const exempt = (node, covered) => node.cls.includes('rail-right') || covered;
function uncovered(node, covered) {
  const now = covered || node.cls.some((c) => animated.has(c));
  const bad = [];
  if (node.text && !exempt(node, now)) bad.push(node.cls.join('.') || node.tag);
  for (const child of node.children) bad.push(...uncovered(child, exempt(node, now)));
  return bad;
}

const clean = html.replace(/<!--[\s\S]*?-->/g, '');
for (const m of clean.matchAll(/<section class="page page-([a-z]+)"[^>]*>([\s\S]*?)<\/section>/g)) {
  const [, name, body] = m;
  const bad = uncovered(parse(body), false);
  chk(`every painted part of ${name} is animated`, bad.length === 0, bad.join(', ') || 'all covered');
}

/* ── the switch, with motion on ─────────────────────────────────────────── */

console.log('\n== switching screens ==');
const app = boot({ motion: true });
chk('rAF is present, so motion is on', app.MOTION === true, String(app.MOTION));

app.render(scen.home);
let v = app.view();
chk('first paint shows home', v.active.join() === 'home', v.active.join());
chk('first paint has nothing to leave', v.leaving.length === 0, v.leaving.join());
chk('a forward step is aimed forward', v.dir === '1', v.dir);
chk('body records the screen', v.body === 'home', v.body);

app.render(scen.select);
v = app.view();
chk('home hands over to select', v.active.join() === 'select', v.active.join());
chk('home is still laid out while it leaves', v.leaving.join() === 'home', v.leaving.join());
chk('body follows', v.body === 'select', v.body);

app.flush();
v = app.view();
chk('home is released once its exit is over', v.leaving.length === 0, v.leaving.join());
chk('select survives the release', v.active.join() === 'select', v.active.join());

console.log('\n== an interrupted exit ==');
app.render(scen.play);
chk('select starts leaving', app.view().leaving.join() === 'select', app.view().leaving.join());
/* The game can pass through two states in a row; the middle screen is on its
 * way out and is replaced before its own exit has finished. */
app.render(scen.results);
v = app.view();
chk('the screen passed through is released early', !v.leaving.includes('select'), v.leaving.join());
chk('only the screen just left is leaving', v.leaving.join() === 'play', v.leaving.join());
chk('results is the one on screen', v.active.join() === 'results', v.active.join());
app.flush();
chk('the interrupted exit still settles', app.view().leaving.length === 0, app.view().leaving.join());

console.log('\n== going back ==');
app.render(scen.select);
v = app.view();
chk('a step back is aimed back', v.dir === '-1', v.dir);
chk('results leaves', v.leaving.join() === 'results', v.leaving.join());
/* Back to a screen that is still mid-exit: its own departure has to be
 * cancelled, or the exit animation keeps winning over the entry one. */
app.render(scen.results);
v = app.view();
chk('returning to a screen cancels its exit', !v.leaving.includes('results'), v.leaving.join());
chk('and it is the active one again', v.active.join() === 'results', v.active.join());
chk('aimed forward again', v.dir === '1', v.dir);
app.flush();
chk('no stale release drops it', app.view().active.join() === 'results', app.view().active.join());

console.log('\n== a repeat of the same screen ==');
const before = app.pending();
app.render(scen.results);
chk('nothing is scheduled', app.pending() === before, String(app.pending()));
chk('and nothing is left over', app.view().leaving.length === 0, app.view().leaving.join());

/* ── the switch with motion off ─────────────────────────────────────────── */

console.log('\n== with no rAF to drive it ==');
const still = boot({ motion: false });
chk('motion is off', still.MOTION === false, String(still.MOTION));
still.render(scen.home);
still.render(scen.select);
v = still.view();
chk('the switch still happens', v.active.join() === 'select' && v.body === 'select', v.active.join());
chk('but nothing is held laid out', v.leaving.length === 0, v.leaving.join());
chk('and no release is pending', still.pending() === 0, String(still.pending()));

console.log('\nfailures:', fails);
process.exit(fails ? 1 : 0);
