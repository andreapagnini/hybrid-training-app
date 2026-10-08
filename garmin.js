/* Send to Garmin: sends planned workouts to the Garmin Connect calendar, one way only.
   Option E: through Andrea's private helper (helper/ folder), which uses python-garminconnect.
   Connection details live under their own storage key, so they never end up in a backup file.
   Room for more connection types (intervals.icu, Garmin's official API) in CONNECTORS below. */
(function(){
'use strict';
const H=window.HT;if(!H)return;
const {S,A,IN,CONFIRM,ui,esc,sv,P,seg}=H;
const CFG_KEY='hybrid-training-garmin',CAT_KEY='hybrid-training-garmin-ex';
const DEFAULTS={mode:'helper',url:'',key:'',connected:false,name:'',auto:true,days:28,last:null};

let cfg=loadCfg();
const g={busy:false,mfa:false,err:'',email:'',catalog:null,catLoading:false,lastTry:0};

function loadCfg(){try{return {...DEFAULTS,...(JSON.parse(localStorage.getItem(CFG_KEY))||{})};}catch(e){return {...DEFAULTS};}}
function saveCfg(){try{localStorage.setItem(CFG_KEY,JSON.stringify(cfg));}catch(e){}}
const configured=()=>!!(cfg.url&&cfg.key);
const ready=()=>configured()&&cfg.connected;
const state=()=>H.state;

/* ---------- connection types ---------- */
const ERR={
  wrong_app_key:'The setup code does not match the helper. Paste it again.',
  not_connected:'Garmin is not connected yet.',
  garmin_sign_in_failed:'Garmin refused the sign-in. Check your email and password, or connect again.',
  garmin_busy:'Garmin is busy right now. Try again in a few minutes.',
  garmin_error:'Garmin returned an error. Try again later.',
  sync_running:'Already sending. Try again in a moment.',
  network:'Can\'t reach the helper. Check your connection and try again.'
};
async function call(path,{method='GET',body=null,keepalive=false}={}){
  let res;
  try{res=await fetch(cfg.url.replace(/\/+$/,'')+path,{method,keepalive,headers:{'Authorization':'Bearer '+cfg.key,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});}
  catch(e){throw new Error('network');}
  let data=null;try{data=await res.json();}catch(e){}
  if(!res.ok){const code=data&&typeof data.detail==='string'?data.detail:'garmin_error';const err=new Error(code);err.code=code;throw err;}
  return data;
}
const errText=e=>ERR[e&&(e.code||e.message)]||ERR.garmin_error;
const CONNECTORS={
  helper:{label:'Private helper',
    status:()=>call('/api/status'),
    login:(email,password)=>call('/api/garmin/login',{method:'POST',body:{email,password}}),
    mfa:code=>call('/api/garmin/mfa',{method:'POST',body:{code}}),
    disconnect:removeWorkouts=>call('/api/garmin/disconnect',{method:'POST',body:{removeWorkouts}}),
    sync:(body,keepalive)=>call('/api/sync',{method:'POST',body,keepalive})}
};
const conn=()=>CONNECTORS[cfg.mode]||CONNECTORS.helper;

/* ---------- Garmin exercise matches ---------- */
/* The starter set comes pre-matched; anything else gets a suggestion from the title. */
const STARTER={
  'back squat':['Barbell Back Squat','SQUAT','BARBELL_BACK_SQUAT'],
  'bench press':['Barbell Bench Press','BENCH_PRESS','BARBELL_BENCH_PRESS'],
  'deadlift':['Barbell Deadlift','DEADLIFT','BARBELL_DEADLIFT'],
  'overhead press':['Barbell Overhead Press','SHOULDER_PRESS','OVERHEAD_BARBELL_PRESS'],
  'barbell row':['Bent-over Row with Barbell','ROW','BENT_OVER_ROW_WITH_BARBELL'],
  'pull-ups':['Pull-up','PULL_UP','PULL_UP'],
  'romanian deadlift':['Romanian Deadlift','DEADLIFT','ROMANIAN_DEADLIFT'],
  'walking lunges':['Weighted Walking Lunge','LUNGE','WEIGHTED_WALKING_LUNGE'],
  'plank':['Plank','PLANK','PLANK']
};
const ALIAS={rdl:'romanian deadlift',ohp:'overhead press',db:'dumbbell',bb:'barbell',kb:'kettlebell',pullup:'pull up',pullups:'pull up',chinup:'chin up',pushup:'push up',situp:'sit up'};
const words=s=>String(s||'').toLowerCase().replace(/[^a-z0-9 ]+/g,' ').split(/\s+/).filter(Boolean)
  .flatMap(w=>(ALIAS[w]||w).split(' ')).map(w=>w.length>3&&w.endsWith('s')&&!w.endsWith('ss')?w.slice(0,-1):w);
function loadCatalog(){
  if(g.catalog||g.catLoading)return;
  try{const c=JSON.parse(localStorage.getItem(CAT_KEY));if(Array.isArray(c)&&c.length>100){g.catalog=c;return;}}catch(e){}
  if(!configured())return;
  g.catLoading=true;
  fetch(cfg.url.replace(/\/+$/,'')+'/api/exercises').then(r=>r.json()).then(rows=>{
    if(Array.isArray(rows)&&rows.length){g.catalog=rows;try{localStorage.setItem(CAT_KEY,JSON.stringify(rows));}catch(e){}}
  }).catch(()=>{}).finally(()=>{g.catLoading=false;H.render();});
}
function suggest(title){
  const st=STARTER[String(title||'').trim().toLowerCase()];if(st)return {name:st[0],category:st[1],exercise:st[2]};
  if(!g.catalog)return null;
  const t=words(title);if(!t.length)return null;const ts=new Set(t);
  let best=null,bs=0;
  for(const [name,category,exercise] of g.catalog){
    const n=words(name),ns=new Set(n);let common=0;ts.forEach(w=>{if(ns.has(w))common++;});
    if(!common)continue;const score=common/new Set([...ts,...ns]).size+(name.toLowerCase()===String(title).toLowerCase()?1:0);
    if(score>bs){bs=score;best={name,category,exercise};}
  }
  return bs>=0.5?best:null;
}
/* ex.garmin: object = chosen, null = "no Garmin match", undefined = use the suggestion */
function matchOf(ex){
  if(!ex||ex.category!=='strength')return null;
  if(ex.garmin===null)return null;
  return ex.garmin||suggest(ex.title);
}
function matchLabel(ex){
  if(ex.garmin===null)return 'Generic (no match)';
  if(ex.garmin)return ex.garmin.name;
  const s=suggest(ex.title);return s?s.name+' (suggested)':'Generic (no match)';
}

/* ---------- what gets sent ---------- */
const firstLine=s=>String(s||'').split('\n').map(x=>x.trim()).find(Boolean)||'';
function payload(){
  const start=H.todayKey(),end=H.addDays(start,cfg.days-1);
  const workouts=state().workouts.filter(w=>w.date>=start&&w.date<=end).map(w=>({
    id:w.id,date:w.date,status:w.status,name:w.name||'Workout',notes:w.notes||'',
    entries:w.entries.map(e=>{const ex=H.exById(e.exerciseId)||{};const m=matchOf(ex);
      return {title:ex.title||'Exercise',category:ex.category||'other',measurement:ex.measurement||'time',perSide:!!e.perSide,
        notes:firstLine(ex.notes),garmin:m?{category:m.category,exercise:m.exercise}:null,
        plannedSets:e.plannedSets||[],plannedCardio:e.plannedCardio||null};})
  }));
  return {start,end,workouts};
}
function hash(s){let h=5381;for(let i=0;i<s.length;i++)h=(h*33^s.charCodeAt(i))>>>0;return h.toString(36);}

async function sync({force=false,keepalive=false}={}){
  if(!ready()||g.busy)return;
  const body=payload(),h=hash(JSON.stringify(body)),last=cfg.last||{};
  if(!force&&last.hash===h&&last.day===body.start&&!last.error)return;
  if(!force&&Date.now()-g.lastTry<20000)return;
  if(keepalive&&JSON.stringify(body).length>60000)keepalive=false;  // browsers cap keepalive requests at 64 KB
  g.busy=true;g.lastTry=Date.now();if(!keepalive)H.render();
  try{
    const r=await conn().sync(body,keepalive);
    cfg.last={at:r.syncedAt||new Date().toISOString(),hash:h,day:body.start,error:'',
      created:r.created,updated:r.updated,removed:r.removed,unchanged:r.unchanged,errors:(r.errors||[]).slice(0,5)};
    if(r.errors&&r.errors.length)cfg.last.hash='';  // retry the failed ones next time
    if(force)H.toast(r.errors&&r.errors.length?`Sent, but ${r.errors.length} workout${r.errors.length===1?'':'s'} failed`:'Plan sent to Garmin');
  }catch(e){
    if(e.code==='garmin_sign_in_failed'||e.code==='not_connected')cfg.connected=false;
    cfg.last={...last,error:errText(e),errorAt:new Date().toISOString()};
    if(force)H.toast(errText(e));
  }finally{g.busy=false;saveCfg();if(!keepalive||!document.hidden)H.render();}
}

/* ---------- screens ---------- */
const fmtWhen=iso=>{if(!iso)return 'Never';const d=new Date(iso),t=`${d.getHours()}:${String(d.getMinutes()).padStart(2,'0')}`;
  const k=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  return k===H.todayKey()?`Today ${t}`:`${H.fmtShort(k)} ${t}`;};
function statusText(){
  if(!configured())return 'Not set up';
  if(!cfg.connected)return 'Not connected';
  if(g.busy)return 'Sending…';
  if(cfg.last&&cfg.last.error)return 'Problem';
  return cfg.last?'Sent '+fmtWhen(cfg.last.at).toLowerCase():'Connected';
}
const row=(label,value)=>`<div class="row"><span class="row-l" style="width:auto">${esc(label)}</span><span class="row-v">${esc(value)}</span></div>`;
const btnRow=(act,label,cls='',extra='')=>`<button class="row row-btn ${cls}" data-act="${act}" ${extra}>${label}</button>`;

S.garmin=()=>{
  let body='';
  if(!configured()){
    body=`<p class="hint" style="margin:0 4px 12px">Sends your planned workouts to your Garmin Connect calendar, so your watch shows each day's session. Nothing is read from Garmin.</p>
<div class="sec-h"><span>Set up</span></div><textarea id="gSetup" class="ta" placeholder="Paste the setup code here" autocapitalize="off" autocomplete="off" spellcheck="false"></textarea>
<div class="sheet-actions"><button class="btn btn-primary" data-act="gSetup">Save setup code</button></div>
<p class="hint">You get the setup code when the private helper is deployed. Treat it like a password.</p>`;
  }else if(!cfg.connected){
    body=`<div class="sec-h"><span>Connect your Garmin account</span></div>`+(g.mfa
      ?`<div class="group"><label class="row"><span class="row-l">Code</span><input id="gCode" class="row-in" inputmode="numeric" autocomplete="one-time-code" placeholder="6-digit code"></label></div>
<div class="sheet-actions"><button class="btn btn-primary" data-act="gMfa" ${g.busy?'disabled':''}>${g.busy?'Checking…':'Verify'}</button><button class="btn btn-ghost" data-act="gMfaCancel">Start again</button></div>
<p class="hint">Garmin sent you a two-step code by email or text. It is valid for a few minutes.</p>`
      :`<div class="group"><label class="row"><span class="row-l">Email</span><input id="gEmail" class="row-in" type="email" autocomplete="username" autocapitalize="off" placeholder="Garmin account email" value="${esc(g.email)}"></label><label class="row"><span class="row-l">Password</span><input id="gPass" class="row-in" type="password" autocomplete="current-password" placeholder="Garmin password"></label></div>
<div class="sheet-actions"><button class="btn btn-primary" data-act="gLogin" ${g.busy?'disabled':''}>${g.busy?'Connecting…':'Connect Garmin'}</button></div>
<p class="hint">Your password goes to Garmin through your private helper and is not stored anywhere. The helper keeps only the sign-in Garmin gives back.</p>`);
    if(g.err)body+=`<p class="err">${esc(g.err)}</p>`;
    body+=`<div class="group" style="margin-top:22px">${btnRow('gForget','Remove setup code','danger')}</div>`;
  }else{
    const l=cfg.last||{};
    const res=l.at&&!l.error?`${l.created||0} new · ${l.updated||0} updated · ${l.removed||0} removed`:'';
    body=`<div class="group">${row('Garmin account',cfg.name||'Connected')}${row('Last sent',g.busy?'Sending…':fmtWhen(l.at))}${res?row('Result',res):''}</div>`;
    if(l.error)body+=`<p class="err">${esc(l.error)}</p>`;
    if(l.errors&&l.errors.length)body+=`<p class="err">Not sent: ${l.errors.map(x=>esc(x.name||'workout')).join(', ')}</p>`;
    body+=`<div class="sheet-actions"><button class="btn btn-primary" data-act="gSyncNow" ${g.busy?'disabled':''}>${g.busy?'Sending…':'Send now'}</button></div>
<div class="sec-h"><span>Sending</span></div><div class="group">
<button class="row row-btn" data-act="gAuto" role="switch" aria-checked="${cfg.auto}"><span class="row-l" style="color:var(--fg);flex:1;text-align:left;white-space:nowrap">Send automatically</span><span class="switch ${cfg.auto?'on':''}"></span></button>
<div class="row"><span class="row-l">Send ahead</span>${seg('gDays',[['7','1 week'],['14','2 weeks'],['28','4 weeks']],String(cfg.days))}</div></div>
<p class="hint">Planned workouts from today on are sent when you open or leave the app. Moving or deleting a workout updates Garmin; done workouts stay as they are.</p>
<div class="sec-h"><span>Exercises</span></div><div class="group">${btnRow('gMatches',`Garmin exercise matches${sv(P.right,16)}`)}</div>
<p class="hint">Each gym exercise is shown on the watch as one of Garmin's exercises, with your own name in the step note.</p>
<div class="group" style="margin-top:22px">${btnRow('gAskDisconnect','Disconnect Garmin','danger')}${btnRow('gAskRemove','Remove sent workouts and disconnect','danger')}</div>`;
  }
  body+=`<p class="hint" style="margin-top:18px">Connection: ${esc(conn().label)}. This uses an unofficial Garmin connection (python-garminconnect, MIT licence, © Ron Klinkien) for your own account only.</p>`;
  return {title:'Garmin',body};
};

S.garminMatches=()=>{
  loadCatalog();
  const list=state().exercises.filter(e=>e.category==='strength'&&!e.isArchived).sort((a,b)=>a.title.localeCompare(b.title));
  const body=list.length?`<div class="group">${list.map(ex=>`<button class="row row-btn" data-act="gExOpen" data-id="${ex.id}"><span class="row-l" style="width:auto;color:var(--fg);flex:1;text-align:left">${esc(ex.title)}</span><span class="row-v">${esc(matchLabel(ex))}</span>${sv(P.right,16)}</button>`).join('')}</div>
<p class="hint">"Suggested" matches are used until you pick one. Generic exercises show as "Total Body" on the watch.</p>`
    :'<p class="hint">No gym exercises in your library yet.</p>';
  return {title:'Exercise matches',body};
};

function exTarget(scr){
  if(scr.id)return H.exById(scr.id);
  const st=H.stack();for(let i=st.length-2;i>=0;i--)if(st[i].s==='exEditor')return st[i].draft;
  return null;
}
function pickList(scr){
  const ex=exTarget(scr);if(!g.catalog)return `<p class="hint">${g.catLoading?'Loading Garmin\'s exercise list…':'Garmin\'s exercise list is not available yet. Check the Garmin setup in Settings.'}</p>`;
  const q=(scr.q||'').trim(),qw=words(q);
  let rows=g.catalog;
  if(qw.length){const phrase=qw.join(' ');
    rows=rows.filter(([n])=>{const nw=words(n).join(' ');return qw.every(w=>nw.includes(w));})
      .map(r=>{const nw=words(r[0]).join(' ');return [nw===phrase?0:nw.endsWith(phrase)||nw.startsWith(phrase)?1:nw.includes(phrase)?2:3,r[0].length,r];})
      .sort((a,b)=>a[0]-b[0]||a[1]-b[1]).map(x=>x[2]);}
  const sug=ex&&suggest(ex.title);
  const top=!q&&sug?[[sug.name,sug.category,sug.exercise]]:[];
  const shown=[...top,...rows.filter(r=>!top.length||r[0]!==top[0][0])].slice(0,80);
  const cur=ex&&ex.garmin&&ex.garmin.name;
  return `<div class="group">${btnRow('gExNone',`<span class="row-l" style="width:auto;color:var(--fg);flex:1;text-align:left">Generic (no match)</span>${ex&&ex.garmin===null?sv(P.check,18):''}`)}${shown.map(r=>`<button class="row row-btn" data-act="gExPick" data-n="${esc(r[0])}" data-c="${esc(r[1])}" data-e="${esc(r[2])}"><span class="row-l" style="width:auto;color:var(--fg);flex:1;text-align:left">${esc(r[0])}${top.length&&r===top[0]?' <span class="muted">· suggested</span>':''}</span>${cur===r[0]?sv(P.check,18):''}</button>`).join('')}</div>
${rows.length>shown.length?`<p class="hint">${rows.length-shown.length} more. Type to narrow the list.</p>`:''}`;
}
S.garminEx=(scr)=>{
  loadCatalog();
  const ex=exTarget(scr);
  const body=`<p class="hint" style="margin:0 4px 10px">How “${esc(ex?ex.title:'')}” shows on your watch.</p>
<div class="group"><label class="row"><span class="row-l">${sv(P.search,18)}</span><input class="row-in" style="text-align:left" data-in="gExQ" value="${esc(scr.q||'')}" placeholder="Search Garmin exercises" autocomplete="off"></label></div>
<div id="gExList">${pickList(scr)}</div>`;
  return {title:'Garmin exercise',body};
};

/* ---------- hooks used by index.html ---------- */
window.HTG={
  statusText,
  exRow(scr){
    const d=scr.draft;if(!configured()||!d||d.category!=='strength')return '';
    loadCatalog();
    return `<div class="sec-h"><span>Garmin</span></div><div class="group"><button class="row row-btn" data-act="gExOpen"><span class="row-l" style="width:auto;color:var(--fg);flex:1;text-align:left">Garmin exercise</span><span class="row-v">${esc(matchLabel(d))}</span>${sv(P.right,16)}</button></div><p class="hint">How this exercise shows on your watch. Your own title stays in the step note.</p>`;
  }
};

/* ---------- actions ---------- */
function parseSetup(text){
  let s=String(text||'').trim();const m=s.match(/[#&?]garmin=([A-Za-z0-9_-]+)/);if(m)s=m[1];
  s=s.replace(/\s+/g,'');const b=s.replace(/-/g,'+').replace(/_/g,'/');
  const o=JSON.parse(atob(b+'='.repeat((4-b.length%4)%4)));
  if(!o||!/^(https:\/\/|http:\/\/(localhost|127\.0\.0\.1)[:/])/.test(o.u)||typeof o.k!=='string'||o.k.length<32)throw new Error('bad');
  return o;
}
Object.assign(A,{
  garmin:()=>{g.err='';H.push({s:'garmin'});},
  gSetup:async()=>{
    const el=document.getElementById('gSetup');let o;
    try{o=parseSetup(el&&el.value);}catch(e){H.toast('That doesn\'t look like a setup code');return;}
    cfg={...cfg,url:o.u,key:o.k,connected:false};saveCfg();g.busy=true;H.render();
    try{const s=await conn().status();cfg.connected=!!s.connected;cfg.name=s.garminName||'';saveCfg();H.toast('Helper found');}
    catch(e){cfg.url='';cfg.key='';saveCfg();H.toast(errText(e));}
    finally{g.busy=false;H.render();}
  },
  gLogin:async()=>{
    const email=(document.getElementById('gEmail')||{}).value||'',pass=(document.getElementById('gPass')||{}).value||'';
    g.email=email.trim();
    if(!email.trim()||!pass){g.err='Enter your Garmin email and password.';H.render();return;}
    g.busy=true;g.err='';H.render();
    try{const r=await conn().login(email.trim(),pass);
      if(r.state==='mfa')g.mfa=true;else await connected();}
    catch(e){g.err=errText(e);}
    finally{g.busy=false;H.render();}
  },
  gMfa:async()=>{
    const code=((document.getElementById('gCode')||{}).value||'').trim();if(!code){g.err='Enter the code Garmin sent you.';H.render();return;}
    g.busy=true;g.err='';H.render();
    try{await conn().mfa(code);g.mfa=false;await connected();}
    catch(e){g.err=e.code==='not_connected'?'The code expired. Start again.':errText(e);if(e.code==='not_connected')g.mfa=false;}
    finally{g.busy=false;H.render();}
  },
  gMfaCancel:()=>{g.mfa=false;g.err='';H.render();},
  gForget:()=>{cfg={...DEFAULTS};saveCfg();g.mfa=false;g.err='';H.render();H.toast('Setup code removed');},
  gSyncNow:()=>sync({force:true}),
  gAuto:()=>{cfg.auto=!cfg.auto;saveCfg();H.render();},
  gDays:d=>{cfg.days=+d.v||28;cfg.last={...(cfg.last||{}),hash:''};saveCfg();H.render();},
  gMatches:()=>H.push({s:'garminMatches'}),
  gExOpen:d=>H.push({s:'garminEx',id:d.id||null,q:''}),
  gExPick:d=>{const scr=H.topScr(),ex=exTarget(scr);if(ex){ex.garmin={name:d.n,category:d.c,exercise:d.e};if(scr.id)H.save();}H.pop();},
  gExNone:()=>{const scr=H.topScr(),ex=exTarget(scr);if(ex){ex.garmin=null;if(scr.id)H.save();}H.pop();},
  gAskDisconnect:()=>H.openSheet({k:'confirm',title:'Disconnect Garmin?',msg:'The helper forgets your Garmin sign-in. Workouts already sent stay in your Garmin calendar.',ok:'Disconnect',then:'gDisconnect'}),
  gAskRemove:()=>H.openSheet({k:'confirm',title:'Remove sent workouts?',msg:'Every workout this app sent is deleted from your Garmin calendar and library, then Garmin is disconnected. Your own Garmin workouts are not touched.',ok:'Remove and disconnect',then:'gRemove'})
});
async function connected(){
  cfg.connected=true;
  try{const s=await conn().status();cfg.name=s.garminName||'';}catch(e){}
  cfg.last=null;saveCfg();H.toast('Garmin connected');
  setTimeout(()=>sync({force:true}),0);  // after the caller has cleared its busy flag
}
async function disconnect(remove){
  g.busy=true;H.render();
  try{const r=await conn().disconnect(remove);cfg.connected=false;cfg.last=null;cfg.name='';saveCfg();
    H.toast(remove?`Removed ${r.removed||0} workout${r.removed===1?'':'s'} and disconnected`:'Garmin disconnected');}
  catch(e){H.toast(errText(e));}
  finally{g.busy=false;H.render();}
}
CONFIRM.gDisconnect=()=>disconnect(false);
CONFIRM.gRemove=()=>disconnect(true);
IN.gExQ=(v)=>{const scr=H.topScr();if(!scr||scr.s!=='garminEx')return;scr.q=v;const el=document.getElementById('gExList');if(el)el.innerHTML=pickList(scr);};

/* ---------- automatic sending ---------- */
document.addEventListener('visibilitychange',()=>{if(cfg.auto)sync({keepalive:document.hidden});});
if(ready()&&cfg.auto)setTimeout(()=>sync(),1500);
if(configured())loadCatalog();
H.render();
})();
