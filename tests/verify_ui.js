/* Runs with plain node; no dependencies. Assumes the repository layout:
 * this file lives in tests/ and the web assets in osu_live/web/. */
/* Regression suite for the overlay front end.
   Covers: id coverage, every render path, the values each produces, and the
   play -> results hand-off (a held "stale" frame must not blank the screen). */
const fs=require('fs');
const dir = require('path').join(__dirname, '..', 'osu_live', 'web') + require('path').sep;
const appjs=fs.readFileSync(dir+'app.js','utf8');
const html=fs.readFileSync(dir+'index.html','utf8');
const realIds=new Set([...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]));
let fails=0;
const fail=(m)=>{fails++;console.log('  FAIL  '+m);};
const pass=(m)=>console.log('  pass  '+m);
const chk=(l,c,x='')=>c?pass(l+(x?' :: '+x:'')):fail(l+(x?' :: got '+x:''));

console.log('== static ==');
const catBlock=appjs.match(/const ids = \{[\s\S]*?\n\};/)[0];
const catIds=[...catBlock.matchAll(/'([\w-]+)'/g)].map(m=>m[1])
  .filter(x=>!['common','home','select','play','results'].includes(x));
const missing=catIds.filter(id=>!realIds.has(id));
if(missing.length)fail('declared but absent: '+missing.join(', '));else pass(`all ${catIds.length} catalogued ids exist`);
// every id-shaped string in app.js must be a real element (the bug class that
// silently no-oped setText twice during development)
const ignore=new Set(['text/html','zh-CN','en-US']);
const classNames=new Set([...appjs.matchAll(/classList\.(?:add|remove|toggle)\(\s*'([^']+)'/g)].map(m=>m[1]));
const suspicious=[...new Set([...appjs.matchAll(/'([a-z][a-z0-9]*(?:-[a-z0-9]+)+)'/g)].map(m=>m[1]))]
  .filter(s=>!ignore.has(s)&&!realIds.has(s)&&!classNames.has(s));
if(suspicious.length)fail('id-shaped strings with no element: '+suspicious.join(', '));
else pass('no dangling id-shaped strings');
const dupes=[...realIds].filter(id=>(html.match(new RegExp(`id="${id}"`,'g'))||[]).length>1);
if(dupes.length)fail('duplicate ids: '+dupes.join(', '));else pass('no duplicate ids');

/* ── DOM stub ─────────────────────────────────────────────────────────── */
function makeEl(id){
  const props={};
  const el={id,textContent:'',innerHTML:'',hidden:false,dataset:{},value:'',onclick:null,title:'',
    scrollWidth:100,clientWidth:500,parentElement:null,
    getAttribute(){return null;},setAttribute(){},querySelector(){return null;},querySelectorAll(){return[];}};
  el.style={setProperty(k,v){props[k]=String(v);},getPropertyValue(k){return props[k]??'';},_props:props};
  for(const k of ['width','height','fontSize','backgroundImage','opacity'])
    Object.defineProperty(el.style,k,{get:()=>props[k]??'',set:v=>{props[k]=v;},configurable:true});
  const list=new Set();
  Object.defineProperty(el,'className',{get:()=>[...list].join(' '),set(v){list.clear();String(v).split(/\s+/).filter(Boolean).forEach(c=>list.add(c));}});
  el.classList={add:(...c)=>c.forEach(x=>list.add(x)),remove:(...c)=>c.forEach(x=>list.delete(x)),
    contains:c=>list.has(c),toggle:(c,on)=>{const w=on===undefined?!list.has(c):!!on;w?list.add(c):list.delete(c);return w;},get _s(){return list;}};
  return el;
}
const created=new Map();

const mkProbeEl = () => {
  const props = {};
  return {
    style: { setProperty(k, v) { props[k] = String(v); }, getPropertyValue(k) { return props[k] ?? ''; }, _props: props },
    getBoundingClientRect: () => ({ top: 0, bottom: 64, left: 0, right: 800, width: 800, height: 64 }),
    offsetTop: 0, offsetHeight: 64, children: [],
  };
};
const document = {getElementById(id){if(!realIds.has(id))return null;if(!created.has(id))created.set(id,makeEl(id));return created.get(id);},
  querySelector: () => mkProbeEl(),
  querySelectorAll: (sel) => (sel && sel.indexOf('.bd') !== -1 ? [bd1, bd2] : []),body:makeEl('body')};
for(const p of ['page-home','page-select','page-play','page-results'])created.set(p,makeEl(p));
const bd1=makeEl('bd1'), bd2=makeEl('bd2');
created.set('bd1',bd1); created.set('bd2',bd2);
const vbody=makeEl('res-score-parent'); vbody.clientWidth=420;
const resScore=makeEl('res-score'); resScore.parentElement=vbody;

global.Image = class { set src(v) { this._src = v; if (this.onload) this.onload(); } };
global.document=document;
global.location={search:'',pathname:'/'};
global.history={replaceState(){}};
global.EventSource=class{constructor(){this.close=()=>{};}};
global.fetch=async()=>({status:200,json:async()=>({})});
let timers=[];
global.setTimeout=(fn)=>{timers.push(fn);return timers.length;};
global.clearTimeout=(id)=>{if(id)timers[id-1]=null;};
global.setInterval=()=>0;global.clearInterval=()=>{};
global.getComputedStyle=()=>({paddingLeft:'10',paddingRight:'10',fontSize:'40px',columnGap:'10'});
eval(appjs.replace(/^connect\(\);\s*$/m,'')+'\nglobalThis.__render=render;');

const rungTweens = [];
const flushTimers=()=>{const t=timers.filter(Boolean);timers=[];t.forEach(fn=>fn());};
const t=(id)=>created.get(id);
/* Frozen fixture: the app writes .last_score.json while a game is running, so
   reading it here would race the client and make assertions flaky. */
const score={player_name:'Yuzu',mode:0,score:46517430,combo:1539,max_combo:1539,accuracy:98.86,
  grade:'S',captured_at:1789717662,
  hits:{hit300:978,hit100:17,hit50:0,geki:177,katu:15,miss:0},
  mods:{array:[],bits:0,rate:1},
  beatmap:{checksum:'x',filename:'a.osu',folder:'f',background:'b.jpg',artist:'yuikonnu',title:'Kakushigoto',
    difficulty:'Insane',creator:'jonathanlfj',ar:9,cs:4,hp:7,od:8,map_id:315260,set_id:122605,object_count:995,ranked_status:4}};
const scen={
  home:{connected:true,state:'menu',player:{name:'Yuzu',level:86.27,accuracy:98.1,play_count:5000,id:1,pp:4597,bancho_status:'idle'},
        beatmap:{artist:'a',title:'t',difficulty:'d',ar:9,od:8,object_count:995,creator:'m'},
        bancho:{enabled:true,stats:{pp:4597,global_rank:12,accuracy:98.1,total_score:1e9}},
        pp:{current:{stars:5.755}},progress:0.42,play_time:100000,audio_length:240000},
  select:{connected:true,state:'selectPlay',player:{name:'Yuzu'},
        beatmap:{artist:'a',title:'t',difficulty:'d',ar:9,od:8,cs:4,hp:7,object_count:995,map_id:1,creator:'m'},
        pp:{current:{pp:264.7,stars:5.755},max:{pp:264.7},acc_list:{'100':264.7,'99':233.6,'98':210.6,'97':194.1,'95':172.1,'90':138.9}},
        mods:{bits:0},progress:0.3,play_time:60000,audio_length:240000},
  play:{connected:true,state:'play',player:{name:'Yuzu'},
        beatmap:{artist:'a',title:'t',difficulty:'d',object_count:995},
        gameplay:{score:12345678,combo:500,grade:'S',accuracy:99.1,hp:1.5,hits:{hit300:500,hit100:5,hit50:0,miss:1}},
        pp:{current:{pp:250.5}},progress:0.6,play_time:150000,audio_length:240000},
  results:{connected:true,state:'resultScreen',player:{name:'Yuzu',id:1,pp:4597},
        last_score:score,score_pp:{current:{pp:230.033,stars:5.755}}},
};
console.log('\n== render paths ==');
const snap=()=>{const o={};for(const[k,el]of created)o[k]={text:el.textContent,html:el.innerHTML,cls:el.className,
  grade:el.dataset.grade,w:el.style._props.width,h:el.style._props.height,p:el.style._props['--p'], arc:el.style._props['--arc'],
    rungs:rungTweens.slice()};return o;};
const run=(name,st)=>{let err=null;try{globalThis.__render(st||scen[name]);}catch(e){err=e;}
  if(err){fail(`render(${name}) threw ${err.message}`);return null;}
  pass(`render(${name}) ok (page=${document.body.dataset.page})`);return snap();};

/* Before anything has been played in this page's lifetime: once a score has
   been seen it is kept, so this state is only reachable on a fresh load. */
run('results-empty', {connected:true, state:'resultScreen', player:{name:'Yuzu'}, last_score:null, score_pp:{}});
chk('no score yet: says so', t('res-song').textContent === '暂无成绩', t('res-song').textContent);
chk('no score yet: and how to get one', /打完一张图/.test(t('res-diff').textContent), t('res-diff').textContent);

const H=run('home');
if(H){chk('home pp headline',H['home-pp'].text==='4,597',H['home-pp'].text);
  chk('home acc tick',H['home-acc'].text==='98.10%',H['home-acc'].text);
  chk('home rank tick',H['home-rank'].text==='#12',H['home-rank'].text);
  chk('home progress',H['home-fill'].w==='42.0%',H['home-fill'].w);}
const S=run('select');
if(S){chk('select star ring arc', S['sel-ring'].arc === '0.5755turn', String(S['sel-ring'].arc));
  chk('select ring value',S['sel-stars'].text==='5.76',S['sel-stars'].text);
  chk('select ladder rungs',(S['sel-ladder'].html.match(/class="rung"/g)||[]).length===6);
  chk('select ladder built for every accuracy', created.get('sel-ladder').dataset.rendered === '90,95,97,98,99,100', String(created.get('sel-ladder').dataset.rendered));
  chk('select elapsed set',S['sel-elapsed'].text==='1:00',S['sel-elapsed'].text);}
const P=run('play');
if(P){chk('play score',P['play-score'].text==='12,345,678',P['play-score'].text);
  chk('play hp height',P['play-hp-fill'].h==='75.0%',P['play-hp-fill'].h);
  chk('play pp',P['play-pp'].text==='250.50',P['play-pp'].text);
  chk('play judges',(P['play-judges'].html.match(/class="judge/g)||[]).length===4);}
const R=run('results');
if(R){chk('res grade',R['res-grade'].text==='S'&&R['res-grade'].grade==='S');
  chk('res pp',R['res-pp'].text==='230.03',R['res-pp'].text);
  chk('res judges',(R['res-judges'].html.match(/class="judge/g)||[]).length===4);}

console.log('\n== play -> results hand-off (no black flash) ==');
globalThis.__render(scen.play); flushTimers();
chk('during play: overlay hidden', !t('offline').classList.contains('active'));
globalThis.__render({...scen.play,stale:true,gameplay:null,error:'ReadProcessMemory(0x68, 4) failed: 299'});
chk('held frame: overlay still hidden', !t('offline').classList.contains('active'));
chk('held frame: page preserved', document.body.dataset.page==='play');
const RR=run('results-arrives',scen.results);
if(RR){chk('results render after hand-off',RR['res-grade'].text==='S');}
chk('results arrived: overlay hidden', !t('offline').classList.contains('active'));

console.log('\n== a stored score that is not this map\'s ==');
/* The score is written out while a map is played, so what is on disk can be
   left over from an earlier map -- or from a play that happened while this
   program was not running. It is still shown, because it is the newest result
   there is, but the line under the title says so: a result for another map
   presented without comment reads as this play's. */
globalThis.__render({...scen.results, beatmap:{artist:'other', title:'另一张图', difficulty:'d', checksum:'0000000000000000000000000000dead'}});
chk('still shown', t('res-grade').textContent === 'S', t('res-grade').textContent);
chk('with the score', t('res-score').textContent === '46,517,430', t('res-score').textContent);
chk('and marked as an earlier play', /上一局的成绩/.test(t('res-diff').textContent), t('res-diff').textContent);
globalThis.__render({...scen.results, beatmap:score.beatmap});
chk('the same map carries no marker', t('res-diff').textContent === '[Insane] · mapped by jonathanlfj', t('res-diff').textContent);
/* A block with nothing to compare must not be treated as a mismatch. */
globalThis.__render({...scen.results, beatmap:{artist:'a', title:'t', difficulty:'d'}});
chk('no identity to compare -> no marker', !/上一局/.test(t('res-diff').textContent), t('res-diff').textContent);

console.log('\n== genuine disconnect still surfaces ==');
globalThis.__render({connected:false,error:'osu!.exe is not running',state:'unknown'});
chk('not immediate (grace period)', !t('offline').classList.contains('active'));
flushTimers();
chk('shown after grace', t('offline').classList.contains('active'));
chk('reason displayed', /osu!\.exe is not running/.test(t('offline-hint').textContent), t('offline-hint').textContent);
globalThis.__render(scen.home);
chk('recovery clears overlay', !t('offline').classList.contains('active'));

console.log('\n== hp danger colouring ==');
globalThis.__render({...scen.play,gameplay:{...scen.play.gameplay,hp:0.4}});
chk('danger threshold', t('play-hp').className.includes('is-danger'), t('play-hp').className);

console.log('\nfailures:',fails);
process.exit(fails?1:0);













