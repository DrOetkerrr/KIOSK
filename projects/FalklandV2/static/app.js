// Extracted from templates/index.html inline script.
// No behavior changes; this file mirrors the previous inline JS.

// ---------- Helpers ----------
const $ = (sel)=>document.querySelector(sel);
const $$ = (sel)=>document.querySelectorAll(sel);
const fmt = (v, d=0)=> (v===undefined||v===null) ? '—' : Number(v).toFixed(d);
const text = (el, s)=>{ el.textContent = s; };
const introOverlay = document.getElementById('intro-overlay');
function showIntroOverlay(){ if(introOverlay) introOverlay.classList.remove('hidden'); }
function hideIntroOverlay(){ if(introOverlay) introOverlay.classList.add('hidden'); }
if(introOverlay){
  if(window.__introActive){ showIntroOverlay(); }
  window.addEventListener('intro:start', showIntroOverlay);
  window.addEventListener('intro:end', hideIntroOverlay);
}
function rowEl(cells){
  const tr=document.createElement('tr');
  cells.forEach(c=>{
    const td=document.createElement('td');
    if (c && c.el){
      td.appendChild(c.el);
    } else if (c && typeof c === 'object' && ('text' in c)){
      td.textContent = (c.text ?? '—');
    } else {
      td.textContent = (c ?? '—');
    }
    td.className = (c && c.cls)||'';
    tr.appendChild(td);
  });
  return tr;
}
function badge(txt, cls='badge'){ const span=document.createElement('span'); span.className=cls; span.textContent=txt; return span;}

let lastOK=false, lastPoll=0, pendingLockId=null;
let __serverVersion=null;

// ---------- Poll status ----------
async function getJSON(url){
  const t0=performance.now();
  const r = await fetch(url, {cache:'no-store'});
  lastPoll = Math.round(performance.now()-t0);
  if(!r.ok) throw new Error(r.status+' '+url);
  return await r.json();
}

function setHUD(j){
  // Auto-reload when backend version changes to ensure new assets/logic are visible
  try{
    const sv = j.server_version || j.serverVersion || null;
    if(__serverVersion===null && sv){ __serverVersion = sv; }
    else if(sv && __serverVersion && sv!==__serverVersion){
      console.log('[version] server changed', __serverVersion, '->', sv, 'reloading…');
      location.reload();
      return;
    }
  }catch(_){ }
  $('#hud-dot').classList.toggle('ok', !!j.ok);
  text($('#hud-poll'), `poll: ${lastPoll} ms`);
  const ship = j.state?.ship || {};
  text($('#hud-ship'), `Ship ${j.ship_cell||'—'}`);
  text($('#hud-hdg'), `hdg ${fmt(ship.heading) }°`);
  text($('#hud-spd'), `spd ${fmt(ship.speed)} kn`);
}

function renderOwnFleet(arr){
  const box = $('#ownfleet'); const empty=$('#ownfleet-empty');
  box.innerHTML='';
  if(!arr || !arr.length){ empty.hidden=false; return; }
  empty.hidden=true;
  arr.slice(0,3).forEach(u=>{
    const line=document.createElement('div'); line.className='mono';
    const hp = (u.status&&u.status.health_pct!=null)? String(u.status.health_pct)+'%':'—%';
    line.textContent = `${u.name||'Unit'}: ${u.cell||'—'}  spd ${fmt(u.speed)}  hdg ${fmt(u.heading)}  ${hp}`;
    box.appendChild(line);
    if((u.id||'')==='own'){
      const sp = $('#own-speed'); const cr = $('#own-course');
      if(sp && (sp.value||'')==='') sp.value = String(u.speed||'');
      if(cr && (cr.value||'')==='') cr.value = String(u.heading||'');
    }
  });
}

function renderPrimary(p){
  const kv=$('#primary-kvs'); const empty=$('#primary-empty');
  const btnLock=$('#btn-lock'), btnUnlock=$('#btn-unlock'), cmdUnlock=$('#cmd-unlock');
  kv.innerHTML='';
  if(!p){ empty.hidden=false; btnUnlock.disabled=true; cmdUnlock.disabled=true; return; }
  empty.hidden=true; btnUnlock.disabled=false; cmdUnlock.disabled=false;
  const add=(k,v)=>{ const kEl=badge(k,'badge'); const vEl=document.createElement('div'); vEl.className='mono'; vEl.textContent=v; kv.append(kEl,vEl); };
  add('LOCKED', `${p.name||'—'}`);
  add('Cell', p.cell||'—');
  add('Range', fmt(p.range_nm,2)+' nm');
  add('Course', fmt(p.course)+'°');
  add('Speed', fmt(p.speed)+' kn');
}

function renderWeapons(arr){
  const body=$('#weapons-body'); const empty=$('#weapons-empty');
  body.innerHTML='';
  if(!arr || !arr.length){ empty.hidden=false; return; }
  empty.hidden=true;
  arr.forEach(w=>{
    const inRange = !!w.in_range;
    const state = (w.armed||'Safe');
    const armed = state==='Armed';
    const arming = state==='Arming';
    const ammo = Number(w.ammo ?? 0);
    const rangeEl = badge(`${fmt(w.min_nm)}–${fmt(w.max_nm)} nm`, 'badge '+(inRange?'ok': ''));
    const cdLeft = Number(w.cooldown_s||0);
    const statusEl = cdLeft>0 ? badge(`Cooldown ${cdLeft}s`, 'badge warn') : (arming? badge('Arming','badge warn') : (armed? badge('Armed','badge ok') : badge('Safe','badge')));
    const armBtn = Object.assign(document.createElement('button'), {className:'btn', textContent: (armed||arming)?'Safe':'Arm'});
    armBtn.onclick = ()=> toggleArm(w.name, (armed||arming)? 'Safe':'Armed');
    const testBtn = Object.assign(document.createElement('button'), {className:'btn', textContent:'Test Fire', disabled: (!armed || ammo<=0 || cdLeft>0)});
    testBtn.onclick = ()=> fireWeapon(w.name, 'test');
    const fireBtn = Object.assign(document.createElement('button'), {className:'btn danger', textContent:'Fire',
                       disabled: (!armed || !inRange || ammo<=0 || cdLeft>0)});
    fireBtn.onclick = ()=> fireWeapon(w.name, 'real');

    const actions = document.createElement('div'); actions.className='row';
    actions.append(armBtn, testBtn, fireBtn);

    body.appendChild(rowEl([
      w.name || '—',
      {cls:'num', el: badge(String(ammo), ammo>0?'badge':'badge warn')},
      {cls:'num', el: rangeEl},
      {el: statusEl},
      {el: actions}
    ]));
  });
}

async function toggleArm(name, state){
  try{
    const r = await fetch('/weapons/arm',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name,state})});
    const j=await r.json();
    if(!j.ok) throw new Error('arm failed');
    poll();
  }catch(e){ appendConsole(`[arm] ERR ${e}`); }
}

async function fireWeapon(name, mode){
  try{
    const r = await fetch('/weapons/fire',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name,mode})});
    const j=await r.json();
    appendConsole(`[fire] ${j.ok?'OK':'ERR'} ${j.result||''}`);
    poll();
  }catch(e){ appendConsole(`[fire] ERR ${e}`); }
}

function renderCAP(cap){
  const head=$('#cap-mini'); const body=$('#cap-body'); const empty=$('#cap-empty');
  if(!cap){ if(head) head.textContent='CAP: —'; if(empty) empty.hidden=false; if(body) body.innerHTML=''; return; }
  if(head){
    head.innerHTML='';
    head.append(
      badge(cap.ready?'READY':'NOT READY', cap.ready?'badge ok':'badge warn'),
      badge(`pairs ${cap.pairs??0}`), badge(`airframes ${cap.airframes??0}`),
      badge(`cooldown ${cap.cooldown_s??0}s`), badge(`committed airframes ${cap.committed_airframes ?? cap.committed ?? 0}`)
    );
  }
  if(!body) return;
  body.innerHTML='';
  const tasks = Array.isArray(cap.tasks)? cap.tasks : [];
  if(!tasks.length){ if(empty) empty.hidden=false; return; }
  if(empty) empty.hidden=true;
  tasks.forEach(t=>{
    const statusCell = document.createElement('div');
    statusCell.textContent = String(t.status || '—');
    if (t.vector) statusCell.appendChild(badge('VECTOR','badge accent'));
    // Decision-support badges
    try{
      const msl = (t.missiles_left!=null)? Number(t.missiles_left): null;
      if(msl!=null) statusCell.appendChild(badge(`MSL ${msl}`, 'badge'+(msl<=0?' warn':'')));
      const roe = (t.roe_eta_s!=null)? Number(t.roe_eta_s): null;
      if(roe!=null){ const cls = (roe<=0)? 'badge ok':'badge'; statusCell.appendChild(badge(`ROE ${Math.round(Math.max(0,roe))}s`, cls)); }
      const fox = (t.fox2_eta_s!=null)? Number(t.fox2_eta_s): null;
      if(fox!=null){ const cls = (fox<=30)? 'badge accent':'badge'; statusCell.appendChild(badge(`FOX2 ${Math.round(Math.max(0,fox))}s`, cls)); }
      if(t.pk_now!=null) statusCell.appendChild(badge(`Pk ${Number(t.pk_now).toFixed(2)}`, 'badge'));
      const feas = String(t.feasibility||'');
      if(feas){ const cls = (feas==='good')?'badge ok':(feas==='poor')?'badge warn':'badge'; statusCell.appendChild(badge(feas.toUpperCase(), cls)); }
      if(t.rec){ const rec=document.createElement('div'); rec.className='mono muted'; rec.textContent=String(t.rec); statusCell.appendChild(rec); }
    }catch(_){ }
    body.appendChild(rowEl([
      t.n ?? '—',
      t.cur_cell ?? '—',
      t.target_cell ?? '—',
      {cls:'num', text: (t.range_nm!=null? fmt(t.range_nm,1): '—')},
      {cls:'num', text: (t.tot_s!=null? fmt(t.tot_s,0): '—')},
      {cls:'num', text: (t.tos_s!=null? fmt(t.tos_s,0): '—')},
      {el: statusCell}
    ]));
  });
}

const ROLE_CHANNELS = {
  Navigation: 1,
  Radar: 2,
  Weapons: 3,
  'Fire Control': 3,
  Pilot: 6,
  Engineering: 5,
  Bridge: 4,
  Ensign: 4,
  Captain: 4,
  XO: 4,
};

function _resolveChannel(role, channel){
  let ch = Number.parseInt(channel, 10);
  if(Number.isFinite(ch) && ch>=1 && ch<=6) return ch;
  const fallback = ROLE_CHANNELS[String(role)||''];
  return (typeof fallback === 'number' && fallback>=1 && fallback<=6) ? fallback : 4;
}

function _activeChannel(){
  try{
    const ch = Number(window.__activeChannel);
    if(Number.isFinite(ch) && ch>=1 && ch<=6) return ch;
  }catch(_){ }
  return 1;
}

function renderRadio(lines){
  const out=$('#radio-list'); if(!out) return;
  out.innerHTML='';
  const activeCh = _activeChannel();
  const filtered = [];
  (lines||[]).forEach(l=>{
    const role = l.role || '';
    const channelId = _resolveChannel(role, l.channel);
    const guard = !!(l.guard || channelId === 6);
    if(guard || channelId === activeCh){
      filtered.push({ ts: l.ts, role, text: l.text, channel: channelId, guard });
    }
  });
  filtered.slice(-10).forEach(l=>{
    const row=document.createElement('div'); row.className='row';
    const chLabel = l.guard ? 'GUARD' : `CH ${l.channel}`;
    const chClass = l.guard ? 'badge channel guard' : 'badge channel';
    row.append(
      badge(l.ts||'--:--:--','badge muted'),
      badge(chLabel, chClass),
      badge(l.role||'OFF','badge'),
      Object.assign(document.createElement('div'),{textContent:l.text||''})
    );
    out.appendChild(row);
  });
}

function renderRadar(arr){
  const body=$('#radar-body'); const empty=$('#radar-empty');
  body.innerHTML='';
  if(!arr || !arr.length){ empty.hidden=false; return; }
  empty.hidden=true;
  arr.slice(0,12).forEach(c=>{
    const lockBtn = Object.assign(document.createElement('button'), {className:'btn primary', textContent:'Lock'});
    lockBtn.onclick = ()=> lockNow(c.ID||c.id);
    const actions = document.createElement('div'); actions.className='row'; actions.append(lockBtn);
    body.appendChild(rowEl([
      c.ID ?? c.id ?? '—',
      c.name || '—',
      c.type || '—',
      c.cell || '—',
      {cls:'num', text: fmt(c.Range ?? c.range_nm, 2)},
      {cls:'num', text: fmt(c.CRS ?? c.course)},
      {cls:'num', text: fmt(c.SPD ?? c.speed)},
      {el: actions}
    ]));
  });
}

function appendConsole(s){
  const c=$('#console');
  if(!c) return;
  const line=document.createElement('div'); line.textContent=String(s||'');
  c.appendChild(line);
  c.scrollTop = c.scrollHeight;
}

function lockNow(id){
  if(id){
    doGET(`/api/command?cmd=${encodeURIComponent('/radar lock '+id)}`).then(j=>{
      appendConsole(`[radar] lock ${j.ok?'OK':'ERR'} ${j.result||''}`);
    }).catch(e=> appendConsole(`[radar] lock ERR ${e}`));
    return;
  }
  // else nearest
  doGET(`/api/command?cmd=${encodeURIComponent('/radar lock nearest')}`).then(j=>{
    appendConsole(`[radar] lock ${j.ok?'OK':'ERR'} ${j.result||''}`);
  }).catch(e=> appendConsole(`[radar] lock ERR ${e}`));
}

// Scan and unlock helpers (mirror inline script behavior)
async function scanNow(){
  try{
    const j = await doGET('/api/command?cmd=' + encodeURIComponent('/radar scan'));
    appendConsole(`[scan] ${j.ok?'OK':'ERR'} ${j.result||''}`);
  }catch(e){ appendConsole(`[scan] ERR ${e}`); }
}
async function unlockNow(){
  try{
    const j = await doGET('/api/command?cmd=' + encodeURIComponent('/radar unlock'));
    appendConsole(`[unlock] ${j.ok?'OK':'ERR'}`);
  }catch(e){ appendConsole(`[unlock] ERR ${e}`); }
}

async function doGET(url){
  const r = await fetch(url, {cache:'no-store'});
  if(!r.ok) throw new Error(r.status+' '+url);
  return r.json();
}

// CAP: request to cell
(function initCAP(){
  const capCell=$('#cap-cell'); const capMin=$('#cap-minutes'); const capRad=$('#cap-radius');
  const capMinVal=$('#cap-minutes-val'); const capRadVal=$('#cap-radius-val');
  const capBtn=$('#btn-cap-to-cell');
  if(capMin) capMin.addEventListener('input', ()=> capMinVal.textContent = String(capMin.value||'10'));
  if(capRad) capRad.addEventListener('input', ()=> capRadVal.textContent = String(capRad.value||'10'));
  if(capBtn) capBtn.onclick = async ()=>{
    try{
      const cell = (capCell?.value||'').trim().toUpperCase();
      const nmin = Number(capMin?.value||'10');
      const rn = Number(capRad?.value||'10');
      if(!cell){ appendConsole('[cap] ERR cell?'); return; }
      const body = JSON.stringify({cell, station_minutes:nmin, radius_nm:rn});
      const r = await fetch('/cap/launch_to',{method:'POST', headers:{'Content-Type':'application/json'}, body});
      const j = await r.json();
      appendConsole(`[cap] ${j.ok?'OK':'ERR'} ${j.message||''}`);
    }catch(e){ appendConsole(`[cap] ERR ${e}`); }
  };
})();

// Radio input
async function sendRadio(){
  const el=$('#radio-input'); if(!el) return; const s=(el.value||'').trim(); if(!s) return;
  try{ const r=await fetch('/radio/ask',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text:s})});
    const j=await r.json(); appendConsole(`[radio] ${j.ok?'OK':'ERR'} ${j.role||''}`); el.value=''; }
  catch(e){ appendConsole(`[radio] ERR ${e}`); }
}
const rs=$('#radio-send'); if(rs) rs.onclick = sendRadio;
const ri=$('#radio-input'); if(ri) ri.addEventListener('keydown', (e)=>{ if(e.key==='Enter') sendRadio(); });
// Force-spawn helpers (nearby targets for weapons testing)
async function spawnNear(kind){
  try{
    if(kind==='Aircraft'){
      const j=await doGET('/radar/force_spawn_near?class=Aircraft&range=2.5');
      appendConsole(`[spawn] Aircraft ${j.ok?'OK':'ERR'} ${j.added?.name||''} ${j.added?.cell||''}`);
    }else{
      const j=await doGET('/radar/force_spawn_near?class=Ship&range=4');
      appendConsole(`[spawn] Ship ${j.ok?'OK':'ERR'} ${j.added?.name||''} ${j.added?.cell||''}`);
    }
  }catch(e){ appendConsole(`[spawn:${kind}] ERR ${e}`); }
}
const btnSpawnAir = $('#btn-spawn-air'); if (btnSpawnAir) btnSpawnAir.onclick = ()=> spawnNear('Aircraft');
const btnSpawnShip = $('#btn-spawn-ship'); if (btnSpawnShip) btnSpawnShip.onclick = ()=> spawnNear('Ship');
// ---- Skirmish helpers ----
async function skirmishStart(){
  const id = Number(($('#skirmish-id').value||'').trim());
  if(!id){ appendConsole('[skirmish] ERR missing id'); return; }
  try{
    const r=await fetch('/skirmish/start',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
    const j=await r.json();
    appendConsole(`[skirmish] start ${j.ok?'OK':'ERR'} id=${id}`);
  }catch(e){ appendConsole(`[skirmish] start ERR ${e}`); }
}
async function skirmishStop(){
  const idStr = ($('#skirmish-id').value||'').trim();
  const id = idStr? Number(idStr) : undefined;
  try{
    const r=await fetch(id?`/skirmish/stop?id=${encodeURIComponent(id)}`:'/skirmish/stop',{method:'POST'});
    const j=await r.json();
    appendConsole(`[skirmish] stop ${j.ok?'OK':'ERR'} ${(j.summary&&JSON.stringify(j.summary))||''}`);
  }catch(e){ appendConsole(`[skirmish] stop ERR ${e}`); }
}
async function skirmishReview(){ window.location.href = '/skirmish'; }
async function skirmishQuick(){
  try{
    const r=await fetch('/skirmish/create',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
    const j=await r.json();
    if(j.ok){ $('#skirmish-id').value=String(j.id); appendConsole(`[skirmish] created id=${j.id}`);} else { appendConsole(`[skirmish] create ERR`);} 
  }catch(e){ appendConsole(`[skirmish] create ERR ${e}`); }
}
// NAV Hermes helpers
async function navHermesClose(){ try{ const j=await doGET('/nav/hermes/close_in'); appendConsole(`[nav] hermes close_in ${j.ok?'OK':'ERR'}`);}catch(e){ appendConsole(`[nav] hermes close_in ERR ${e}`);} }
async function navHermesStand(){ try{ const j=await doGET('/nav/hermes/stand_off'); appendConsole(`[nav] hermes stand_off ${j.ok?'OK':'ERR'}`);}catch(e){ appendConsole(`[nav] hermes stand_off ERR ${e}`);} }
// Populate hostile targets for new skirmish
async function loadHostiles(){
  try{ const j = await getJSON('/contacts/catalog?hostile=1'); const items = j.items||[];
    const ids=['nsk-tgt1-name','nsk-tgt2-name','nsk-tgt3-name'];
    ids.forEach(id=>{ const sel=$('#'+id); if(!sel) return; sel.innerHTML=''; const opt=document.createElement('option'); opt.value=''; opt.textContent='(none)'; sel.appendChild(opt);
      items.forEach(it=>{ const o=document.createElement('option'); o.value=it.name; o.textContent=it.name; sel.appendChild(o); }); });
  }catch(e){ appendConsole(`[catalog] ERR ${e}`); }
}
async function createSkirmishFromForm(){
  const name = ($('#nsk-name').value||'').trim();
  const notes = ($('#nsk-notes').value||'').trim();
  const picks = [];
  for(const i of [1,2,3]){
    const n = ($('#nsk-tgt'+i+'-name')?.value||'').trim();
    const c = ($('#nsk-tgt'+i+'-cell')?.value||'').trim().toUpperCase();
    if(n && c) picks.push({name:n, cell:c});
  }
  if(picks.length===0){ appendConsole('[skirmish] ERR add at least one target'); return; }
  const body = { name, notes, config: { hostiles: picks } };
  try{
    const r=await fetch('/skirmish/create',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const j=await r.json(); if(j.ok){ $('#skirmish-id').value=String(j.id); appendConsole(`[skirmish] created id=${j.id}`);} else { appendConsole(`[skirmish] create ERR`); }
  }catch(e){ appendConsole(`[skirmish] create ERR ${e}`); }
}
const skStart = $('#skirmish-start'); if (skStart) skStart.onclick = skirmishStart;
const skStop = $('#skirmish-stop'); if (skStop) skStop.onclick = skirmishStop;
const skReview = $('#skirmish-review'); if (skReview) skReview.onclick = skirmishReview;
const skQuick = $('#skirmish-quick'); if (skQuick) skQuick.onclick = skirmishQuick;
const navIn = $('#nav-hermes-in'); if (navIn) navIn.onclick = navHermesClose;
const navOff = $('#nav-hermes-off'); if (navOff) navOff.onclick = navHermesStand;
const nskCreate = $('#nsk-create'); if (nskCreate) nskCreate.onclick = createSkirmishFromForm;
loadHostiles();
const ownApply = $('#own-apply'); if (ownApply) ownApply.onclick = async ()=>{
  const spStr = ($('#own-speed').value||'').trim();
  const crStr = ($('#own-course').value||'').trim();
  const payload = {};
  if (spStr !== '' && !Number.isNaN(Number(spStr))) payload['speed'] = Number(spStr);
  if (crStr !== '' && !Number.isNaN(Number(crStr))) payload['heading'] = Number(crStr);
  if(Object.keys(payload).length===0){ appendConsole('[nav] ERR missing values'); return; }
  try{
    const r = await fetch('/api/nav/set',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    const j = await r.json();
    appendConsole(`[nav] ${j.ok?'OK':'ERR'}`);
    poll();
  }catch(e){ appendConsole(`[nav] ERR ${e}`); }
};
// CAP header controls wiring
const capIntBtn = $('#cap-int-btn'); if (capIntBtn) capIntBtn.onclick = async ()=>{
  try{
    const r = await fetch('/cap/request', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
    const j = await r.json(); appendConsole(`[cap intercept] ${j.ok?'OK':'ERR'} ${j.message||''}`);
    poll();
  }catch(e){ appendConsole(`[cap intercept] ERR ${e}`); }
};
const capHeadLaunch = $('#cap-head-launch'); if (capHeadLaunch) capHeadLaunch.onclick = ()=>{
  const cellEl = $('#cap-head-cell'); const cell = (cellEl?.value||'').trim().toUpperCase();
  if(!cell){ appendConsole('[CAP] ERR missing cell'); return; }
  (async ()=>{
    try{
      const r = await fetch('/cap/launch_to',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cell, station_minutes:10, radius_nm:10})});
      const j = await r.json(); appendConsole(`[CAP->${cell}] ${j.ok?'OK':'ERR'} ${j.message||''}`);
      poll();
    }catch(e){ appendConsole(`[CAP->${cell}] ERR ${e}`); }
  })();
};
// Wire scan/lock/unlock buttons if present
const btnScan = $('#btn-scan'); if (btnScan) btnScan.onclick = scanNow;
const cmdScan = $('#cmd-scan'); if (cmdScan) cmdScan.onclick = scanNow;
const btnLock = $('#btn-lock'); if (btnLock) btnLock.onclick = ()=> lockNow($('#lock-id')?.value);
const btnUnlock = $('#btn-unlock'); if (btnUnlock) btnUnlock.onclick = unlockNow;
const btnLockNearest = $('#btn-lock-nearest'); if (btnLockNearest) btnLockNearest.onclick = ()=> lockNow();
const cmdUnlockBtn = $('#cmd-unlock'); if (cmdUnlockBtn) cmdUnlockBtn.onclick = unlockNow;
// Self test button
const btnSelf = $('#selftest-run'); if (btnSelf) btnSelf.onclick = async ()=>{
  try{
    const r = await fetch('/diag/selftest'); const j = await r.json();
    const R = j.results || {};
    const n = v => (v && v.ok ? 'OK' : 'ERR');
    appendConsole(`[selftest] nav=${n(R.nav)} radar=${n(R.radar)} weapons=${n(R.weapons)} cap=${n(R.cap)} radio=${n(R.radio)}`);
  }catch(e){ appendConsole(`[selftest] ERR ${e}`); }
};
// Commands card: Request CAP quick action
const capReqBtn = $('#btn-cap-request'); if (capReqBtn) capReqBtn.onclick = async ()=>{
  try{
    const r = await fetch('/cap/request', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
    const j = await r.json(); appendConsole(`[cap request] ${j.ok?'OK':'ERR'} ${j.message||''}`);
    poll();
  }catch(e){ appendConsole(`[cap request] ERR ${e}`); }
};
// Enable Lock button when an ID is typed; lock nearest when none typed
const lockIdEl = $('#lock-id');
if (lockIdEl) {
  lockIdEl.addEventListener('input', ()=>{
    const v = (lockIdEl.value||'').trim();
    const btnLock = $('#btn-lock'); if (btnLock) btnLock.disabled = !v;
  });
  lockIdEl.addEventListener('keydown', (e)=>{
    if(e.key === 'Enter'){
      e.preventDefault();
      lockNow();
    }
  });
}

// ---------- Radio source fallback ----------
async function loadRadio(){
  try{
    const j = await getJSON('/flight/tail?n=20');
    const lines = (j.lines||[]).map(x=>{
      const route = x.route||'';
      if(route==='/radio.officer'){
        return {ts:(x.ts||'').slice(11,19), role:(x.response?.role||'OFF'), text:(x.response?.text||'')};
      }
      return null;
    }).filter(Boolean);
    return lines;
  }catch{ return []; }
}

// ---------- Main poll ----------
async function poll(){
  try{
    const j = await getJSON('/api/status');
    lastOK = !!j.ok;
    setHUD(j);
    renderOwnFleet(j.ownfleet);
    renderPrimary(j.primary);
    renderWeapons(j.weapons);
    renderCAP(j.cap);
    // Enable/disable CAP header buttons
    try{
      const hasPrimary = !!j.primary;
      const ready = !!(j.cap && j.cap.ready);
      const capIntBtn = $('#cap-int-btn'); if (capIntBtn) { capIntBtn.disabled = !(hasPrimary && ready); capIntBtn.classList.toggle('danger', hasPrimary && ready); }
      const cellEl = $('#cap-head-cell'); const capHeadLaunch = $('#cap-head-launch');
      if (capHeadLaunch) capHeadLaunch.disabled = !(ready && (cellEl && (cellEl.value||'').trim().length>0));
    }catch(_){ }
    // Enable CAP button only if a primary exists and CAP reports available
    try{
      const capBtn = $('#btn-cap-request');
      if (capBtn) capBtn.disabled = !(j.primary && j.cap && j.cap.ready);
    }catch(_){ }

    // radio: prefer status.radio; else from recorder
    if (Array.isArray(j.radio) && j.radio.length){
      renderRadio(j.radio);
    } else {
      const r = await loadRadio();
      renderRadio(r);
    }

    renderRadar(j.contacts);

  }catch(e){
    lastOK=false;
    $('#hud-dot').classList.remove('ok');
  }
}
poll();
setInterval(poll, 1500);
