// Extracted from templates/index.html inline script.
// No behavior changes; this file mirrors the previous inline JS.

// ---------- Helpers ----------
const $ = (sel)=>document.querySelector(sel);
const $$ = (sel)=>document.querySelectorAll(sel);
const fmt = (v, d=0)=> (v===undefined||v===null) ? '—' : Number(v).toFixed(d);
const text = (el, s)=>{ el.textContent = s; };
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

// ---------- Poll status ----------
async function getJSON(url){
  const t0=performance.now();
  const r = await fetch(url, {cache:'no-store'});
  lastPoll = Math.round(performance.now()-t0);
  if(!r.ok) throw new Error(r.status+' '+url);
  return await r.json();
}

function setHUD(j){
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
    const statusEl = arming? badge('Arming','badge warn') : (armed? badge('Armed','badge ok') : badge('Safe','badge'));
    const armBtn = Object.assign(document.createElement('button'), {className:'btn', textContent: (armed||arming)?'Safe':'Arm'});
    armBtn.onclick = ()=> toggleArm(w.name, (armed||arming)? 'Safe':'Armed');
    const testBtn = Object.assign(document.createElement('button'), {className:'btn', textContent:'Test Fire', disabled: (!armed || ammo<=0)});
    testBtn.onclick = ()=> fireWeapon(w.name, 'test');
    const fireBtn = Object.assign(document.createElement('button'), {className:'btn danger', textContent:'Fire',
                       disabled: (!armed || !inRange || ammo<=0)});
    fireBtn.onclick = ()=> fireWeapon(w.name, 'real');

    const actions = document.createElement('div'); actions.className='row';
    actions.append(armBtn, testBtn, fireBtn);

    body.appendChild(rowEl([
      w.name || '—',
      {cls:'num', el: badge(String(ammo), ammo>0?'badge':'badge warn')},
      {cls:'num', el: rangeEl},
      statusEl,
      {el: actions}
    ]));
  });
}

async function toggleArm(name, state){
  try{
    const r = await fetch('/weapons/arm',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name,state})});
    const j=await r.json();
    if(!j.ok) throw new Error('arm failed');
  }catch(e){ appendConsole(`[arm] ERR ${e}`); }
}

async function fireWeapon(name, mode){
  try{
    const r = await fetch('/weapons/fire',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name,mode})});
    const j=await r.json();
    appendConsole(`[fire] ${j.ok?'OK':'ERR'} ${j.result||''}`);
  }catch(e){ appendConsole(`[fire] ERR ${e}`); }
}

function renderCAP(cap){
  const head=$('#cap-head');
  if(!cap){ head.textContent='CAP: —'; return; }
  head.innerHTML='';
  head.append(
    badge(cap.ready?'READY':'NOT READY', cap.ready?'badge ok':'badge warn'),
    badge(`pairs ${cap.pairs??0}`), badge(`airframes ${cap.airframes??0}`),
    badge(`cooldown ${cap.cooldown_s??0}s`), badge(`committed ${cap.committed??0}`)
  );
}

function renderRadio(lines){
  const out=$('#radio');
  out.innerHTML='';
  (lines||[]).slice(-10).forEach(l=>{
    const row=document.createElement('div'); row.className='row';
    row.append(badge(l.ts||'--:--:--','badge muted'), badge(l.role||'OFF','badge'),
               Object.assign(document.createElement('div'),{textContent:l.text||''}));
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
const skStart = $('#skirmish-start'); if (skStart) skStart.onclick = skirmishStart;
const skStop = $('#skirmish-stop'); if (skStop) skStop.onclick = skirmishStop;
const skReview = $('#skirmish-review'); if (skReview) skReview.onclick = skirmishReview;
const skQuick = $('#skirmish-quick'); if (skQuick) skQuick.onclick = skirmishQuick;
const navIn = $('#nav-hermes-in'); if (navIn) navIn.onclick = navHermesClose;
const navOff = $('#nav-hermes-off'); if (navOff) navOff.onclick = navHermesStand;
const nskCreate = $('#nsk-create'); if (nskCreate) nskCreate.onclick = createSkirmishFromForm;
loadHostiles();
const ownApply = $('#own-apply'); if (ownApply) ownApply.onclick = async ()=>{
  const sp = Number(($('#own-speed').value||'').trim());
  const cr = Number(($('#own-course').value||'').trim());
  const parts=[];
  if(!Number.isNaN(cr)) parts.push(`heading=${encodeURIComponent(cr)}`);
  if(!Number.isNaN(sp)) parts.push(`speed=${encodeURIComponent(sp)}`);
  if(!parts.length){ appendConsole('[nav] ERR missing values'); return; }
  try{
    const cmd = `/nav set ${parts.join(' ')}`;
    const j = await doGET(`/api/command?cmd=${encodeURIComponent(cmd)}`);
    appendConsole(`[nav] ${j.ok?'OK':'ERR'} ${j.result||''}`);
  }catch(e){ appendConsole(`[nav] ERR ${e}`); }
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

