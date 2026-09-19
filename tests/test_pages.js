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
const leaveMs = Number((appjs.match(/const PAGE_LEAVE_MS = (\d+);/) || [])[1]);
const outMs = Number((style.match(/--pg-out:\s*(\d+)ms/) || [])[1]);
chk('PAGE_LEAVE_MS is declared', Number.isFinite(leaveMs), String(leaveMs));
chk('--pg-out is declared', Number.isFinite(outMs), String(outMs));
chk('the screen is held at least as long as its exit', leaveMs > outMs, `${leaveMs}ms > ${outMs}ms`);
/* Each of the four screens has to animate one of the shell's siblings; a
 * selector that names a class the markup does not have would leave that screen
 * cutting in with no transition at all. */
const animated = [...style.matchAll(/\.page\.active > \.body > (?:\.sel-right > )?\.([\w-]+)/g)].map((m) => m[1]);
const styled = new Set([...html.matchAll(/class="([^"]+)"/g)].flatMap((m) => m[1].split(/\s+/)));
const orphan = [...new Set(animated)].filter((c) => !styled.has(c));
chk('every animated block exists in the markup', orphan.length === 0, orphan.join(', ') || `${new Set(animated).size} classes`);

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
