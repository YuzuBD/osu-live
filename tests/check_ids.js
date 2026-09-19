/* Runs with plain node; no dependencies. Assumes the repository layout:
 * this file lives in tests/ and the web assets in osu_live/web/. */
/* Guard against the exact bug class that broke this page twice: a string that
   looks like an element id in app.js but does not exist in index.html, which
   makes setText()/setRing() no-op silently. */
const fs=require('fs');
const dir = require('path').join(__dirname, '..', 'osu_live', 'web') + require('path').sep;
const appjs=fs.readFileSync(dir+'app.js','utf8');
const html=fs.readFileSync(dir+'index.html','utf8');
const realIds=new Set([...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]));

// Only the id-shaped strings: quoted kebab/lower identifiers.
const candidates=new Set();
for(const m of appjs.matchAll(/'([a-z][a-z0-9]*(?:-[a-z0-9]+)+)'/g)) candidates.add(m[1]);
// Drop the ones that are obviously not element ids, and any string that is
// used as a CSS class rather than an element id.
const ignore=new Set(['text/html','use-strict','zh-CN','no-store','en-US']);
const classNames=new Set();
for(const m of appjs.matchAll(/classList\.(?:add|remove|toggle)\(\s*'([^']+)'/g)) classNames.add(m[1]);
const suspicious=[...candidates].filter(x=>!ignore.has(x)&&!realIds.has(x)&&!classNames.has(x));

console.log('== id-shaped strings in app.js that are NOT elements in index.html ==');
if(!suspicious.length) console.log('  none');
for(const s of suspicious){
  // show the line so a human can judge
  const line=appjs.split('\n').findIndex(l=>l.includes("'"+s+"'"))+1;
  console.log(`  app.js:${line}  '${s}'  -> ${appjs.split('\n')[line-1].trim().slice(0,80)}`);
}
process.exit(suspicious.length?1:0);
