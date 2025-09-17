const $ = (sel)=>document.querySelector(sel);
const $$ = (sel)=>document.querySelectorAll(sel);
const text = (el, s)=>{ if(el) el.textContent = s; };
const fmt = (v, d)=> (v===undefined||v===null||Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d||0);

let ST = { active: 'NAV', test: false };

function setActive(id){ ST.active=id; $$('.toolbar .btn').forEach(b=> b.classList.toggle('active', b.dataset.st===id)); render(window._status||{}); }

function rowKV(arr){ const d=document.createElement('div'); d.className='mono'; arr.forEach(x=>{ const s=document.createElement('span'); s.className='badge'; s.style.marginRight='6px'; s.textContent=x; d.appendChild(s); }); return d; }

function renderNAV(j){
  // Avoid stomping user typing: if NAV inputs are focused, skip re-render
  try{
    const ae=document.activeElement; const aid=(ae&&ae.id)||'';
    if(aid==='nav-speed' || aid==='nav-course') return;
  }catch(_){ }
  const p=$('#station-panel'); p.innerHTML='';
  const h=document.createElement('h2'); h.textContent='NAV (navigation)'; p.appendChild(h);
  let fleet = Array.isArray(j.ownfleet)? j.ownfleet.slice() : [];
  // Fallback: derive a minimal own row from state when ownfleet missing
  if(!fleet.length){
    try{
      const st = (j.state||{}); const ship = st.ship||{};
      const own = { id:'own', name:'Own', cell:'—', speed: ship.speed, heading: ship.heading };
      fleet = [own];
    }catch(_){ fleet = []; }
  }
  // Own ship first, then escorts (convoy)
  const own = fleet.find(u=>String(u.id||'')==='own') || fleet[0] || {};
  const escorts = fleet.filter(u=>String(u.id||'')!=='own');

  const sec=document.createElement('div');
  // Own ship line
  if(own){ sec.appendChild(rowKV([String(own.name||'Own'), String(own.cell||'—'), 'spd '+fmt(own.speed), 'hdg '+fmt(own.heading)])); }
  // Convoy escorts (bring back explicit view)
  if(escorts.length){ escorts.forEach(u=>{ sec.appendChild(rowKV([String(u.name||'Escort'), String(u.cell||'—'), 'spd '+fmt(u.speed), 'hdg '+fmt(u.heading)])); }); }
  p.appendChild(sec);

  // Simple course/speed form (stabilized: POST /api/nav/set)
  const form=document.createElement('div'); form.className='row section';
  const speed=document.createElement('input'); speed.id='nav-speed'; speed.type='text'; speed.className='input mono'; speed.placeholder='Speed'; speed.style.width='100px';
  const course=document.createElement('input'); course.id='nav-course'; course.type='text'; course.className='input mono'; course.placeholder='Course'; course.style.width='100px';
  // Prefill from own ship if empty
  const spNow = (own && typeof own.speed!== 'undefined') ? String(own.speed) : '';
  const hdgNow = (own && typeof own.heading!== 'undefined') ? String(own.heading) : '';
  if((speed.value||'')==='') speed.value = spNow;
  if((course.value||'')==='') course.value = hdgNow;
  const btn=document.createElement('button'); btn.className='btn'; btn.textContent='Apply';
  const msg=document.createElement('span'); msg.className='mono muted'; msg.style.marginLeft='6px';
  btn.onclick=async function(){
    const spStr=(speed.value||'').trim(); const crStr=(course.value||'').trim();
    const body={}; if(spStr!=='' && !Number.isNaN(Number(spStr))) body['speed']=Number(spStr); if(crStr!=='' && !Number.isNaN(Number(crStr))) body['heading']=Number(crStr);
    if(!Object.keys(body).length){ msg.textContent=''; return; }
    try{ const r=await fetch('/api/nav/set',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)}); const j=await r.json(); msg.textContent = (j&&j.ok)?'OK':'ERR'; }
    catch(e){ msg.textContent='ERR'; }
  };
  // Enter key submits
  speed.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); btn.click(); }});
  course.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); btn.click(); }});
  form.appendChild(speed); form.appendChild(course); form.appendChild(btn); form.appendChild(msg); p.appendChild(form);

  // Hermes controls moved from SYS: Close In / Stand Off
  const hrow=document.createElement('div'); hrow.className='row section';
  const hIn=document.createElement('button'); hIn.className='btn'; hIn.textContent='Hermes: Close In';
  const hOff=document.createElement('button'); hOff.className='btn'; hOff.textContent='Hermes: Stand Off';
  const hMsg=document.createElement('span'); hMsg.className='mono muted'; hMsg.style.marginLeft='6px';
  hIn.onclick=async function(){
    try{ const r=await fetch('/nav/hermes/close_in'); const j=await r.json();
      if(j&&j.ok){ hMsg.textContent=`BRG ${j.bearing}°, RNG ${j.range_nm} nm, REC HDG ${j.recommend_hdg}°`; }
      else{ hMsg.textContent='ERR'; }
    }catch(e){ hMsg.textContent='ERR'; }
  };
  hOff.onclick=async function(){
    try{ const r=await fetch('/nav/hermes/stand_off'); const j=await r.json();
      if(j&&j.ok){ hMsg.textContent=`BRG ${j.bearing}°, RNG ${j.range_nm} nm, STAND OFF ${j.standoff_nm} nm`; }
      else{ hMsg.textContent='ERR'; }
    }catch(e){ hMsg.textContent='ERR'; }
  };
  hrow.appendChild(hIn); hrow.appendChild(hOff); hrow.appendChild(hMsg); p.appendChild(hrow);
}

function colorTag(kind){ const s=document.createElement('span'); s.className='tag '+(kind==='Friendly'?'green': kind==='Hostile'?'red':'grey'); s.textContent=kind||'—'; return s; }

function renderRADAR(j){
  const p=$('#station-panel'); p.innerHTML='';
  const title=document.createElement('h2'); title.textContent='RADAR CONSOLE:'; p.appendChild(title);
  const lockedId = (j.radar && j.radar.locked_id!==undefined && j.radar.locked_id!==null)? Number(j.radar.locked_id): null;
  const primary = (j.primary && typeof j.primary==='object')? j.primary : null;
  const contacts = Array.isArray(j.contacts)? j.contacts.slice(): [];
  const lockedContact = contacts.find(c=>Number(c.id)===lockedId) || null;
  const primaryBox=document.createElement('div'); primaryBox.className='primary-box';
  const primFields=[
    ['#ID', lockedContact ? String(lockedContact.id).padStart(2,'0') : '—'],
    ['Name', lockedContact ? String(lockedContact.name||'') : (primary? String(primary.name||''): '—')],
    ['Range', lockedContact && lockedContact.range_nm!=null ? `${fmt(lockedContact.range_nm,1)} nm` : (primary&&primary.range_nm!=null? `${fmt(primary.range_nm,1)} nm` : '—')],
    ['Speed', lockedContact && lockedContact.speed!=null ? `${fmt(lockedContact.speed,0)} kn` : '—'],
    ['TTI', (function(){ const t=lockedContact?computeTTI(lockedContact):null; return t!==null? `${t}s` : '—'; })()]
  ];
  primFields.forEach(function(pair){
    const box=document.createElement('div'); box.className='primary-field';
    const lab=document.createElement('span'); lab.className='primary-label'; lab.textContent=pair[0];
    const val=document.createElement('span'); val.className='primary-value'; val.textContent=pair[1];
    box.appendChild(lab); box.appendChild(val); primaryBox.appendChild(box);
  });
  p.appendChild(primaryBox);
  const tbl=document.createElement('table'); const thead=document.createElement('thead'); const trh=document.createElement('tr');
  ['#','Status','Type','Name','Range','Speed','TTI','ID','Lock'].forEach(function(k){ const th=document.createElement('th'); th.textContent=k; trh.appendChild(th); });
  thead.appendChild(trh); tbl.appendChild(thead);

  const tb=document.createElement('tbody');
  const list = contacts.sort(function(a,b){ return (a.range_nm||1e9)-(b.range_nm||1e9); });
  const lockContact = async (id)=>{ if(id===undefined||id===null) return; await fetch('/api/command?cmd='+encodeURIComponent(`/radar lock ${id}`)); await poll().catch(()=>{}); };
  list.forEach(function(c, idx){
    const tr=document.createElement('tr');
    if(String(c.type||'').toLowerCase()==='hostile'){ tr.classList.add('hostile-row'); }
    if(lockedId!==null && Number(c.id)===lockedId){ tr.classList.add('locked-row'); }
    const tdIdx=document.createElement('td'); tdIdx.className='num'; tdIdx.textContent=String(idx+1);
    const tdStatus=document.createElement('td'); tdStatus.appendChild(colorTag(String(c.type||'—')));
    const tdType=document.createElement('td'); tdType.textContent=String(c.class||c.meta_class||c.meta?.cap?.class||'—');
    const tdName=document.createElement('td'); tdName.textContent=String(c.name||'—');
    const tdRange=document.createElement('td'); tdRange.className='num'; tdRange.textContent=(c.range_nm!==undefined&&c.range_nm!==null)?`${fmt(c.range_nm,1)} nm`:'—';
    const tdSpeed=document.createElement('td'); tdSpeed.className='num'; tdSpeed.textContent=(c.speed!==undefined&&c.speed!==null)?`${fmt(c.speed,0)} kn`:'—';
    const tti = computeTTI(c);
    const tdTTI=document.createElement('td'); tdTTI.className='num'; tdTTI.textContent = (tti!==null)? `${tti}s` : '—';
    const tdId=document.createElement('td'); tdId.className='num'; tdId.textContent=(c.id!==undefined&&c.id!==null)?String(c.id).padStart(2,'0'):'—';
    const tdLock=document.createElement('td');
    const btn=document.createElement('button'); btn.className='btn'; btn.textContent='LOCK';
    if(c.id===undefined||c.id===null){ btn.disabled=true; }
    btn.onclick=function(){ lockContact(c.id); };
    tdLock.appendChild(btn);
    [tdIdx,tdStatus,tdType,tdName,tdRange,tdSpeed,tdTTI,tdId,tdLock].forEach(function(td){ tr.appendChild(td); });
    tb.appendChild(tr);
  });
  // Pad to ten rows for layout consistency
  for(let i=list.length; i<10; i+=1){
    const tr=document.createElement('tr');
    for(let jdx=0;jdx<9;jdx+=1){
      const td=document.createElement('td');
      if(jdx===0) td.className='num';
      tr.appendChild(td);
    }
    tb.appendChild(tr);
  }
  tbl.appendChild(tb); p.appendChild(tbl);

  const controls=document.createElement('div'); controls.className='row section radar-controls';
  const scanBtn=document.createElement('button'); scanBtn.className='btn'; scanBtn.textContent='SCAN';
  scanBtn.onclick=async function(){ await fetch('/api/command?cmd='+encodeURIComponent('/radar scan')); await poll().catch(()=>{}); };
  const lockNearest=document.createElement('button'); lockNearest.className='btn'; lockNearest.textContent='GO';
  lockNearest.onclick=async function(){ await fetch('/api/command?cmd='+encodeURIComponent('/radar lock nearest')); await poll().catch(()=>{}); };
  const unlockBtn=document.createElement('button'); unlockBtn.className='btn'; unlockBtn.textContent='UNLOCK';
  unlockBtn.onclick=async function(){ await fetch('/api/command?cmd='+encodeURIComponent('/radar unlock')); await poll().catch(()=>{}); };
  controls.appendChild(scanBtn); controls.appendChild(lockNearest); controls.appendChild(unlockBtn); p.appendChild(controls);
}

function computeTTI(contact){
  try{
    if(String(contact.type||'').toLowerCase()!=='hostile') return null;
    const rng = Number(contact.range_nm);
    const spd = Number(contact.speed);
    if(!Number.isFinite(rng) || !Number.isFinite(spd) || spd <= 0){ return null; }
    const sec = Math.max(0, Math.round((rng * 3600) / spd));
    return sec;
  }catch(_){ return null; }
}

function renderWPN(j){
  const p=$('#station-panel'); p.innerHTML=''; const h=document.createElement('h2'); h.textContent='WEAPONS'; p.appendChild(h);
  const row=document.createElement('div'); row.className='row section';
  const lab=document.createElement('span'); lab.textContent='Test mode'; lab.style.marginRight='6px';
  const btn=document.createElement('button'); btn.className='btn'; btn.textContent=ST.test?'ON':'OFF'; btn.onclick=function(){ ST.test=!ST.test; btn.textContent=ST.test?'ON':'OFF'; };
  row.appendChild(lab); row.appendChild(btn); p.appendChild(row);
  const tbl=document.createElement('table'); const thead=document.createElement('thead'); const trh=document.createElement('tr');
  ['Weapon','Ammo','Range (nm)','Status','Arm','Fire'].forEach(function(k){ const th=document.createElement('th'); if(k!=='Weapon') th.className = (k.indexOf('Ammo')>=0||k.indexOf('Range')>=0)?'num':''; th.textContent=k; trh.appendChild(th); }); thead.appendChild(trh); tbl.appendChild(thead);
  const tb=document.createElement('tbody');
  (Array.isArray(j.weapons)? j.weapons : []).forEach(function(w){
    const tr=document.createElement('tr');
    const st=String(w.armed||'Safe');
    const tdN=document.createElement('td'); tdN.textContent=String(w.name||'—');
    const tdA=document.createElement('td'); tdA.className='num'; tdA.textContent=String(w.ammo||0);
    const tdR=document.createElement('td'); tdR.className='num'; tdR.textContent=fmt(w.min_nm,0)+'–'+fmt(w.max_nm,0);
    const tdS=document.createElement('td'); const dot=document.createElement('span'); dot.className='statdot'+(st==='Armed'?' on':''); tdS.appendChild(dot);
    const tdArm=document.createElement('td'); const ab=document.createElement('button'); ab.className='btn'; ab.textContent=(st==='Armed')?'Safe':'Arm'; ab.onclick=async function(){ const next=(st==='Armed')?'Safe':'Armed'; await fetch('/weapons/arm',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:w.name, state: next})}); }; tdArm.appendChild(ab);
    const tdF=document.createElement('td'); const fb=document.createElement('button'); fb.className='btn'; fb.textContent='Fire'; fb.disabled = !(st==='Armed') || (Number(w.ammo||0)<=0) || (Number(w.cooldown_s||0)>0);
    fb.onclick=async function(){ const body={name:w.name, mode:(ST.test?'test':'real')}; const r=await fetch('/weapons/fire',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)}); await r.json().catch(function(){}); };
    tdF.appendChild(fb);
    tr.appendChild(tdN); tr.appendChild(tdA); tr.appendChild(tdR); tr.appendChild(tdS); tr.appendChild(tdArm); tr.appendChild(tdF); tb.appendChild(tr);
  });
  tbl.appendChild(tb); p.appendChild(tbl);
}

function renderRADIO(j){
  const p=$('#station-panel'); p.innerHTML=''; const h=document.createElement('h2'); h.textContent='RADIO / HERMES CAP'; p.appendChild(h);
  const row=document.createElement('div'); row.className='row section';
  const bI=document.createElement('button'); bI.className='btn'; bI.textContent='Intercept'; bI.onclick=async function(){ await fetch('/cap/request',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})}); };
  const cell=document.createElement('input'); cell.className='input mono'; cell.placeholder='Cell'; cell.style.width='80px';
  const min=document.createElement('input'); min.type='range'; min.min='5'; min.max='30'; min.step='1'; min.value='10'; min.style.width='120px';
  const rad=document.createElement('input'); rad.type='range'; rad.min='2'; rad.max='20'; rad.step='1'; rad.value='10'; rad.style.width='120px';
  const bC=document.createElement('button'); bC.className='btn'; bC.textContent='CAP to Cell'; bC.onclick=async function(){ const c=(cell.value||'').trim().toUpperCase(); if(!c) return; const body={cell:c, station_minutes:Number(min.value||'10'), radius_nm:Number(rad.value||'10')}; await fetch('/cap/launch_to',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)}); };
  row.appendChild(bI); row.appendChild(cell); row.appendChild(min); row.appendChild(rad); row.appendChild(bC); p.appendChild(row);
  const mini=document.createElement('div'); mini.className='row section'; const cap=j.cap||{};
  [ 'READY '+(cap.ready?'YES':'NO'), 'pairs '+(cap.pairs||0), 'airframes '+(cap.airframes||0), 'committed '+(cap.committed||0) ].forEach(function(s){ const b=document.createElement('span'); b.className='badge'; b.textContent=s; b.style.marginRight='6px'; mini.appendChild(b); });
  p.appendChild(mini);
}

function renderENG(j){
  const p=$('#station-panel'); p.innerHTML=''; const h=document.createElement('h2'); h.textContent='ENG (Engineering)'; p.appendChild(h);
  const systems=['Hull','Engines','Weapons','Fire Control','Navigation']; const sec=document.createElement('div');
  systems.forEach(function(s){ const r=document.createElement('div'); r.className='row'; const nm=document.createElement('div'); nm.style.minWidth='140px'; nm.textContent=s; const ind=document.createElement('div'); const d=document.createElement('span'); d.className='statdot on'; ind.appendChild(d); r.appendChild(nm); r.appendChild(ind); sec.appendChild(r); });
  p.appendChild(sec);
  const lives=Number(((j.state||{}).lives)||0); const maxl=Number(((j.state||{}).max_lives)||0); const pct = maxl>0 ? Math.round(100*lives/maxl) : 100;
  const info=document.createElement('div'); info.className='row section mono'; const a=document.createElement('span'); a.textContent='Ship State: '+pct+'%'; const b=document.createElement('span'); b.textContent='Repair teams: 0/0'; info.appendChild(a); info.appendChild(b); p.appendChild(info);
}

function renderSYS(j){
  const p=$('#station-panel'); p.innerHTML=''; const h=document.createElement('h2'); h.textContent='SYS'; p.appendChild(h);
  const row=document.createElement('div'); row.className='row section';
  const bScan=document.createElement('button'); bScan.className='btn'; bScan.textContent='Scan'; bScan.onclick=async function(){ await fetch('/api/command?cmd='+encodeURIComponent('/radar scan')); };
  const bAir=document.createElement('button'); bAir.className='btn'; bAir.textContent='Spawn Near Aircraft'; bAir.onclick=async function(){ await fetch('/radar/force_spawn_near?class=Aircraft&range=2.5'); };
  const bShip=document.createElement('button'); bShip.className='btn'; bShip.textContent='Spawn Near Ship'; bShip.onclick=async function(){ await fetch('/radar/force_spawn_near?class=Ship&range=4'); };
  row.appendChild(bScan); row.appendChild(bAir); row.appendChild(bShip); p.appendChild(row);
}

function render(j){
  const ship=((j.state||{}).ship)||{};
  // Stabilize HUD ship cell by sourcing from ownfleet when available
  let hudCell='—';
  try{
    const fleet = Array.isArray(j.ownfleet)? j.ownfleet : [];
    const own = fleet.find(u=>String(u.id||'')==='own') || fleet[0];
    if(own && own.cell) hudCell = String(own.cell);
  }catch(e){ hudCell='—'; }
  text($('#hud-ship'), 'Ship '+hudCell);
  text($('#hud-hdg'), 'hdg '+fmt(ship.heading)); text($('#hud-spd'), 'spd '+fmt(ship.speed)+' kn');
  $('#hud-dot') && $('#hud-dot').classList.toggle('ok', !!j.ok);
  if(ST.active==='NAV') return renderNAV(j);
  if(ST.active==='RADAR') return renderRADAR(j);
  if(ST.active==='WPN') return renderWPN(j);
  if(ST.active==='RADIO') return renderRADIO(j);
  if(ST.active==='ENG') return renderENG(j);
  if(ST.active==='SYS') return renderSYS(j);
}

async function poll(){
  try{ const r=await fetch('/api/status',{cache:'no-store'}); const j=await r.json(); window._status=j; render(j); renderRadioBox(j);}catch(e){}
}

function playKlik(){ try{ const a=new Audio('/data/sounds/klik.m4a'); a.volume=0.6; a.play().catch(function(){}); }catch(e){} }

function wire(){
  $$('.toolbar .btn').forEach(function(b){ b.addEventListener('click', function(ev){ playKlik(); setActive(b.dataset.st); }); });
  const rs=$('#radio-send'); const ri=$('#radio-input'); if(rs) rs.onclick=async function(){ const s=(ri.value||'').trim(); if(!s) return; await fetch('/radio/ask',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text:s})}); ri.value=''; };
  if(ri) ri.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); const btn=$('#radio-send'); if(btn) btn.click(); }});
  // Global KLIK on all button presses
  document.addEventListener('click', function(ev){ const t=ev.target; if(t && t.matches && t.matches('button.btn')){ playKlik(); } }, true);
  setActive('NAV'); poll(); setInterval(poll, 1500);
}

document.addEventListener('DOMContentLoaded', wire);
// Bootstrap marker to help verify the right asset is served
try { window.__stations_loaded = true; window.__stations_loaded_ts = Date.now(); console.log('stations.js loaded', new Date(window.__stations_loaded_ts).toISOString()); } catch (_e) {}
