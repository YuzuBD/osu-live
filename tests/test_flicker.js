/* Runs with plain node; no dependencies. Assumes the repository layout:
 * this file lives in tests/ and the web assets in osu_live/web/. */
/* Does an empty read block the screen? */
const fs=require('fs');
const dir = require('path').join(__dirname, '..', 'osu_live', 'web') + require('path').sep;
const appjs=fs.readFileSync(dir+'app.js','utf8');
const html=fs.readFileSync(dir+'index.html','utf8');
const realIds=new Set([...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]));
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
const homeRing=makeEl('home-ring'); homeRing.querySelector=()=>makeEl('x'); created.set('home-ring',homeRing);
global.Image = class { set src(v) { this._src = v; if (this.onload) this.onload(); } };
global.document=document;global.location={search:'',pathname:'/'};
global.history={replaceState(){}};global.EventSource=class{constructor(){this.close=()=>{};}};
global.fetch=async()=>({status:200,json:async()=>({})});
let timers=[];
global.setTimeout=(fn)=>{timers.push(fn);return timers.length;};
global.clearTimeout=(id)=>{if(id)timers[id-1]=null;};
global.setInterval=()=>0;global.clearInterval=()=>{};
global.getComputedStyle=()=>({paddingLeft:'10',paddingRight:'10',fontSize:'40px',columnGap:'10'});
eval(appjs.replace(/^connect\(\);\s*$/m,'') + ';globalThis.__render = render;');

const t=(id)=>created.get(id);
let fails=0;
const chk=(l,c,x='')=>{ if(!c)fails++; console.log(`  ${c?'pass':'FAIL'}  ${l}${x?' :: '+x:''}`); };
const P=()=>document.body.dataset.page;

const good={connected:true,state:'resultScreen',
  player:{name:'Yuzu',id:1,pp:4597,level:86,accuracy:98.1,play_count:5124,bancho_status:'idle'},
  beatmap:{artist:'ONE OK ROCK',title:'American Girls',difficulty:"Omekyu's Extra",creator:'Juztan',ar:9,od:9,cs:4,hp:7,object_count:995,folder:'123 map',background:'bg.jpg'},
  pp:{current:{pp:243.55,stars:5.835},max:{pp:243.55},acc_list:{'100':243.55,'99':215.0,'98':195.0,'97':180.0,'95':160.0,'90':130.0}},
  score_pp:{current:{pp:138.603,stars:5.835}},
  last_score:{player_name:'Yuzu',score:970516,grade:'A',accuracy:95.49,combo:110,max_combo:995,captured_at:1789719000,
    hits:{hit300:205,hit100:9,hit50:1,miss:3},mods:{array:[],bits:0},beatmap:{artist:'ONE OK ROCK',title:'American Girls',difficulty:"Omekyu's Extra",creator:'Juztan',object_count:995}},
  mods:{array:[],bits:0}};

console.log('== baseline (full frame) ==');
globalThis.__render(good);
chk('page is results', P()==='results', P());
chk('player name shown', t('res-player').textContent==='Yuzu', t('res-player').textContent);
chk('song title shown', /American Girls/.test(t('res-song').textContent), t('res-song').textContent);
chk('grade shown', t('res-grade').textContent==='A', t('res-grade').textContent);

console.log('\n== the flicker: player block comes back empty ==');
globalThis.__render({...good, player:{}});
chk('page NOT flipped to home', P()==='results', P());
chk('name NOT replaced by 未登录', t('res-player').textContent!=='未登录', t('res-player').textContent);
chk('name kept from previous frame', t('res-player').textContent==='Yuzu', t('res-player').textContent);
chk('grade still shown', t('res-grade').textContent==='A', t('res-grade').textContent);

console.log('\n== the flicker: beatmap block comes back empty ==');
globalThis.__render({...good, beatmap:{}});
chk('page still results', P()==='results', P());
chk('song title kept', /American Girls/.test(t('res-song').textContent), t('res-song').textContent);
const bdArt = [bd1, bd2].map(b => b.style._props.backgroundImage || '').join(' ');
chk('backdrop kept', /api\/background/.test(bdArt), bdArt.trim());

console.log('\n== both empty at once (worst case mid-transition) ==');
globalThis.__render({...good, player:{}, beatmap:{}});
chk('page still results', P()==='results', P());
chk('name still real', t('res-player').textContent==='Yuzu', t('res-player').textContent);
chk('title still real', /American Girls/.test(t('res-song').textContent), t('res-song').textContent);

console.log('\n== select page: empty beatmap must not blank the title ==');
globalThis.__render({...good, state:'selectPlay'});
const title=()=>t('sel-title').textContent;
const before=title();
globalThis.__render({...good, state:'selectPlay', beatmap:{}, pp:null});
chk('select kept the title', title()===before, `${JSON.stringify(before)} -> ${JSON.stringify(title())}`);

console.log('\n== a real disconnect must still clear everything ==');
globalThis.__render({connected:false, error:'osu!.exe is not running', state:'unknown'});
[timers.filter(Boolean)].flat().forEach(fn=>fn()); timers=[];
globalThis.__render({connected:true,state:'menu',player:{},beatmap:{}});
chk('after disconnect, empty player shows 未登录', t('home-name').textContent==='未登录', t('home-name').textContent);

console.log('\nfailures:',fails);
process.exit(fails?1:0);


