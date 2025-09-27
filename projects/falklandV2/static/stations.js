const $ = (sel)=>document.querySelector(sel);
const $$ = (sel)=>document.querySelectorAll(sel);
const text = (el, s)=>{ if(el) el.textContent = s; };
const fmt = (v, d)=> (v===undefined||v===null||Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d||0);

function formatHermesOrderSummary(payload){
  try{
    if(!payload || payload.ok === false) return null;
  }catch(_){
    return null;
  }
  const pieces=[];
  try{
    if(payload.bearing!==undefined && payload.bearing!==null){
      const bearing = Math.round(Number(payload.bearing));
      if(Number.isFinite(bearing)) pieces.push(`BRG ${bearing}°`);
    }
  }catch(_){ }
  try{
    if(payload.range_nm!==undefined && payload.range_nm!==null){
      const rng = Number(payload.range_nm);
      if(Number.isFinite(rng)) pieces.push(`RNG ${rng.toFixed(1)} nm`);
    }
  }catch(_){ }
  try{
    if(payload.recommend_hdg!==undefined && payload.recommend_hdg!==null){
      const hdg = Math.round(Number(payload.recommend_hdg));
      if(Number.isFinite(hdg)) pieces.push(`HDG ${hdg}°`);
    }
  }catch(_){ }
  try{
    if(payload.standoff_nm!==undefined && payload.standoff_nm!==null){
      const standoff = Number(payload.standoff_nm);
      if(Number.isFinite(standoff)) pieces.push(`STAND OFF ${standoff.toFixed(1)} nm`);
    }
  }catch(_){ }
  return pieces.length ? pieces.join(', ') : 'Order acknowledged';
}

let ST = {
  active: 'NAV',
  test: false,
  nav: { desiredHeading: '', desiredSpeed: '' },
  wpn: { lockInput: '' },
  events: [],
  eventHistory: [],
  eventKeys: { launch: null, result: null, cap: null },
  muteRoles: {}
};

const STATION_ROLE_MAP = {
  NAV: ['Navigation'],
  RADAR: ['Radar'],
  WPN: ['Weapons', 'Fire Control'],
  RADIO: ['Pilot'],
  ENG: ['Engineering'],
  LOG: [],
  SYS: ['Ensign']
};

try {
  const savedMute = localStorage.getItem('muteRoles');
  if (savedMute) {
    ST.muteRoles = JSON.parse(savedMute) || {};
  }
} catch (_) {
  ST.muteRoles = {};
}
if (!ST.muteRoles || typeof ST.muteRoles !== 'object') ST.muteRoles = {};
window.__stationMute = ST.muteRoles;

function _isRoleMuted(role){
  try { return !!(ST.muteRoles || {})[role]; } catch(_) { return false; }
}

function _setRoleMuted(role, muted){
  if (!ST.muteRoles || typeof ST.muteRoles !== 'object') ST.muteRoles = {};
  if (muted) ST.muteRoles[role] = true;
  else delete ST.muteRoles[role];
  try { localStorage.setItem('muteRoles', JSON.stringify(ST.muteRoles)); } catch(_){ }
  window.__stationMute = ST.muteRoles;
}

function createStationControls(stationKey){
  const roles = STATION_ROLE_MAP[stationKey] || [];
  if (!roles.length) return null;
  const bar=document.createElement('div'); bar.className='station-controls';
  const btn=document.createElement('button'); btn.className='btn mute-btn';
  function update(){
    const muted = roles.every(_isRoleMuted);
    btn.textContent = muted ? 'RADIO OFF' : 'RADIO ON';
    btn.classList.toggle('muted', muted);
    btn.title = muted ? 'Enable radio for this station' : 'Silence radio for this station';
  }
  btn.onclick=function(){
    const muted = roles.every(_isRoleMuted);
    roles.forEach(function(role){ _setRoleMuted(role, !muted); });
    update();
  };
  update();
  bar.appendChild(btn);
  return bar;
}

function addStationControls(p, key){
  const ctrl = createStationControls(key);
  if (ctrl) p.appendChild(ctrl);
}

function setActive(id){ ST.active=id; $$('.toolbar .btn').forEach(b=> b.classList.toggle('active', b.dataset.st===id)); render(window._status||{}); }

const WEAPON_LABELS = {
  exocet_mm38: 'MM38 Exocet',
  seacat: 'Sea Dart',
  gun_4_5in: '4.5in Gun',
  oerlikon_20mm: '20mm Oerlikon',
  gam_bo1_20mm: '20mm GAM-BO1',
  corvus_chaff: 'Corvus Chaff',
  weapon_launch: 'Weapon'
};

function formatWeaponLabel(key){
  if(!key) return '';
  if(WEAPON_LABELS[key]) return WEAPON_LABELS[key];
  try{
    return String(key).replace(/[._-]+/g,' ').replace(/\b\w/g, function(s){ return s.toUpperCase(); });
  }catch(_){
    return String(key);
  }
}

function eventTimestamp(){
  try{
    return new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false});
  }catch(_){
    return new Date().toISOString().slice(11,19);
  }
}

function formatEventTs(ts){
  try{
    if(ts===undefined || ts===null) return eventTimestamp();
    const num = Number(ts);
    if(!Number.isFinite(num)) return eventTimestamp();
    const date = new Date(num * 1000);
    if(Number.isNaN(date.getTime())) return eventTimestamp();
    return date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false});
  }catch(_){ return eventTimestamp(); }
}

function renderEventConsole(){
  const box=$('#event-log');
  if(!box) return;
  box.innerHTML='';
  const list=Array.isArray(ST.events)? ST.events.slice().reverse(): [];
  if(!list.length){
    const row=document.createElement('div'); row.className='event-line muted';
    const label=document.createElement('div'); label.className='event-label'; label.textContent='No recent events';
    row.appendChild(label);
    box.appendChild(row);
    return;
  }
  list.forEach(function(ev){
    const row=document.createElement('div'); row.className='event-line';
    const label=document.createElement('div'); label.className='event-label'; label.textContent=ev.text || '';
    row.appendChild(label);
    if(ev.time){
      const tm=document.createElement('div'); tm.className='event-time'; tm.textContent=ev.time; row.appendChild(tm);
    }
    box.appendChild(row);
  });
}

function pushEvent(kind, text){
  if(!Array.isArray(ST.events)) ST.events=[];
  ST.events.push({ kind, text, time: eventTimestamp() });
  if(ST.events.length>5) ST.events = ST.events.slice(-5);
  renderEventConsole();
}

function trackEvents(j){
  try{
    if(!ST.eventKeys) ST.eventKeys = { launch: null, result: null, cap: null };
    if(Array.isArray(j.events)){
      ST.eventHistory = j.events.map(function(ev){
        return {
          kind: ev && ev.id,
          text: String((ev && ev.text) || '—'),
          time: formatEventTs(ev && ev.ts),
          ts: ev && ev.ts
        };
      });
      ST.events = ST.eventHistory.slice(-5);
      renderEventConsole();
      if(ST.active === 'LOG') renderLOG();
    }
    const audio = (j && j.audio) || {};
    const launch = audio.last_launch;
    if(launch && launch.ts){
      const key = String(launch.ts)+':'+String(launch.weapon||'');
      if(ST.eventKeys.launch !== key){
        ST.eventKeys.launch = key;
        const label = formatWeaponLabel(launch.weapon || '');
        pushEvent('launch', label?`Missile fired: ${label}`:'Missile fired');
      }
    }
    const cap = audio.cap_launch;
    if(cap && cap.ts){
      const key = 'cap:'+String(cap.ts);
      if(ST.eventKeys.cap !== key){
        ST.eventKeys.cap = key;
        pushEvent('cap','Aircraft launched (CAP)');
      }
    }
    const result = audio.last_result;
    if(result && result.ts){
      const key = String(result.ts)+':'+String(result.event||'');
      if(ST.eventKeys.result !== key){
        ST.eventKeys.result = key;
        const kind = String(result.event||'').toLowerCase();
        if(kind==='miss') pushEvent('miss','Shot missed');
        else if(kind==='hit') pushEvent('hit','Target hit');
      }
    }
  }catch(_){ }
}

function renderNAV(j){
  // Avoid stomping user typing: if NAV inputs are focused, skip re-render
  try{
    const ae=document.activeElement; const aid=(ae&&ae.id)||'';
    if(aid==='nav-speed' || aid==='nav-course') return;
  }catch(_){ }
  const p=$('#station-panel'); p.innerHTML='';
  addStationControls(p,'NAV');

  let fleet = Array.isArray(j.ownfleet)? j.ownfleet.slice() : [];
  if(!fleet.length){
    try{
      const st = (j.state||{}); const ship = st.ship||{};
      fleet = [{ id:'own', name:'Own Ship', cell:'—', speed: ship.speed, heading: ship.heading }];
    }catch(_){ fleet = []; }
  }
  if(!ST.nav) ST.nav = { desiredHeading: '', desiredSpeed: '' };
  const own = fleet.find(u=>String(u.id||'')==='own') || fleet[0] || {};
  const orderedFleet = [];
  if(own && Object.keys(own).length){ orderedFleet.push(own); }
  fleet.filter(u=>String(u.id||'')!=='own').forEach(u=> orderedFleet.push(u));

  const table=document.createElement('table'); table.className='nav-table';
  const thead=document.createElement('thead'); const thr=document.createElement('tr');
  ['Fleet status:', 'Grid:', 'Speed:', 'Course:'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; thr.appendChild(th); });
  thead.appendChild(thr); table.appendChild(thead);
  const tbody=document.createElement('tbody');
  orderedFleet.forEach(function(unit){
    const tr=document.createElement('tr');
    const nameTd=document.createElement('td'); nameTd.textContent=String(unit.name||'—'); tr.appendChild(nameTd);
    const cellTd=document.createElement('td'); cellTd.textContent=String(unit.cell||'—'); tr.appendChild(cellTd);
    const spdTd=document.createElement('td'); spdTd.className='num'; spdTd.textContent=(unit.speed!==undefined&&unit.speed!==null)?fmt(unit.speed,0):'—'; tr.appendChild(spdTd);
    const crsTd=document.createElement('td'); crsTd.className='num'; crsTd.textContent=(unit.heading!==undefined&&unit.heading!==null)?fmt(unit.heading,0):'—'; tr.appendChild(crsTd);
    tbody.appendChild(tr);
  });
  if(!orderedFleet.length){
    const tr=document.createElement('tr');
    const td=document.createElement('td'); td.colSpan=4; td.className='muted'; td.textContent='No navigation data available.';
    tr.appendChild(td); tbody.appendChild(tr);
  }
  table.appendChild(tbody); p.appendChild(table);

  const hint=document.createElement('div'); hint.className='nav-button-hint'; hint.textContent='Orders move Hermes by one grid cell at a time.'; p.appendChild(hint);

  const controlsWrap=document.createElement('div'); controlsWrap.className='nav-controls';
  const controlsTable=document.createElement('table'); controlsTable.className='nav-control-table';
  const ctrlBody=document.createElement('tbody');
  controlsTable.appendChild(ctrlBody);

  const currentSpeed = (own && own.speed!=null)? String(own.speed) : '';
  const currentHeading = (own && own.heading!=null)? String(own.heading) : '';
  const desiredHeading = (ST.nav.desiredHeading || '').trim();
  const desiredSpeed = (ST.nav.desiredSpeed || '').trim();
  const coursePreset = desiredHeading !== '' ? desiredHeading : currentHeading;
  const speedPreset = desiredSpeed !== '' ? desiredSpeed : currentSpeed;

  async function sendNavUpdate(payload, msgEl){
    if(!Object.keys(payload).length){ msgEl.textContent=''; return; }
    try{
      const r=await fetch('/api/nav/set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const resp=await r.json();
      msgEl.textContent = (resp && resp.ok)?'OK':'ERR';
      msgEl.className = 'nav-msg '+((resp && resp.ok)?'ok':'err');
    }catch(_){
      msgEl.textContent='ERR';
      msgEl.className='nav-msg err';
    }
  }

  function addControlRow(label, inputId, preset, key, stateKey){
    const tr=document.createElement('tr');
    const labTd=document.createElement('td'); labTd.textContent=label; tr.appendChild(labTd);
    const inputTd=document.createElement('td');
    const input=document.createElement('input'); input.type='text'; input.className='input mono'; input.placeholder='....'; input.id=inputId; if(preset) input.value=preset;
    inputTd.appendChild(input); tr.appendChild(inputTd);
    const actionTd=document.createElement('td');
    const btn=document.createElement('button'); btn.className='btn nav-set-btn'; btn.textContent='SET';
    const msg=document.createElement('span'); msg.className='nav-msg muted';
    const submit=async function(){
      const raw=(input.value||'').trim();
      if(raw===''){ ST.nav[stateKey]=''; msg.textContent=''; msg.className='nav-msg muted'; return; }
      const num=Number(raw);
      if(Number.isNaN(num)){ msg.textContent='ERR'; msg.className='nav-msg err'; return; }
      const payload={}; payload[key]=num;
      ST.nav[stateKey]=raw;
      await sendNavUpdate(payload, msg);
    };
    btn.onclick=function(){ submit(); };
    input.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); submit(); }});
    input.addEventListener('input', function(){ ST.nav[stateKey]=(input.value||'').trim(); });
    actionTd.appendChild(btn); actionTd.appendChild(msg); tr.appendChild(actionTd);
    ctrlBody.appendChild(tr);
  }

  addControlRow('Set course', 'nav-course', coursePreset, 'heading', 'desiredHeading');
  addControlRow('Set speed', 'nav-speed', speedPreset, 'speed', 'desiredSpeed');

  controlsTable.appendChild(ctrlBody); controlsWrap.appendChild(controlsTable); p.appendChild(controlsWrap);

  // Hermes controls moved from SYS: Close In / Stand Off
  const hermesRow=document.createElement('div'); hermesRow.className='row section';
  const hermesLabel=document.createElement('span'); hermesLabel.textContent='Hermes movement:'; hermesLabel.style.marginRight='8px';
  const moveCloserBtn=document.createElement('button'); moveCloserBtn.className='btn'; moveCloserBtn.textContent='MOVE CLOSER';
  const moveAwayBtn=document.createElement('button'); moveAwayBtn.className='btn'; moveAwayBtn.textContent='MOVE AWAY'; moveAwayBtn.style.marginLeft='6px';
  const hermesMsg=document.createElement('span'); hermesMsg.className='mono muted'; hermesMsg.style.marginLeft='6px';
  async function adjustHermes(mode){
    const path = mode==='in'? '/nav/hermes/close_in' : '/nav/hermes/stand_off';
    hermesMsg.className='mono muted';
    hermesMsg.textContent = mode==='in' ? 'Ordering Hermes to move closer…' : 'Ordering Hermes to move out…';
    try{
      const res=await fetch(path);
      const data=await res.json();
      if(data && data.ok){
        const summary = formatHermesOrderSummary(data);
        const note = (data && typeof data.message==='string') ? data.message.trim() : '';
        if(summary && note){
          hermesMsg.textContent = `${summary} — ${note}`;
          hermesMsg.className='mono warn';
        }else if(note){
          hermesMsg.textContent = note;
          hermesMsg.className='mono warn';
        }else{
          hermesMsg.textContent = summary || 'Order acknowledged';
          hermesMsg.className='mono ok';
        }
        await poll().catch(()=>{});
      }else{
        hermesMsg.textContent = (data && data.error)? String(data.error) : 'Command failed';
        hermesMsg.className='mono err';
      }
    }catch(_){
      hermesMsg.textContent='Command failed';
      hermesMsg.className='mono err';
    }
  }
  moveCloserBtn.onclick=function(){ adjustHermes('in'); };
  moveAwayBtn.onclick=function(){ adjustHermes('out'); };
  hermesRow.appendChild(hermesLabel);
  hermesRow.appendChild(moveCloserBtn);
  hermesRow.appendChild(moveAwayBtn);
  hermesRow.appendChild(hermesMsg);
  p.appendChild(hermesRow);
}

function colorTag(kind){ const s=document.createElement('span'); s.className='tag '+(kind==='Friendly'?'green': kind==='Hostile'?'red':'grey'); s.textContent=kind||'—'; return s; }

function engSystemStatus(j, systemId){
  try{
    const systems = (j && j.eng && Array.isArray(j.eng.systems))? j.eng.systems : [];
    const rec = systems.find(function(s){ return String(s.id||'')===String(systemId||''); });
    return rec ? String(rec.status||'OK') : 'OK';
  }catch(_){ return 'OK'; }
}

function renderStationOffline(p, label, stationKey){
  p.innerHTML='';
  addStationControls(p, stationKey || 'SYS');
  const pane=document.createElement('div'); pane.className='station-offline'; pane.textContent=label || 'SYSTEM OFFLINE';
  p.appendChild(pane);
  return true;
}

function renderRADAR(j){
  const p=$('#station-panel'); p.innerHTML='';
  addStationControls(p,'RADAR');
  const radarStatus = engSystemStatus(j,'Radar');
  if(radarStatus && radarStatus.toLowerCase()!=='ok'){
    renderStationOffline(p,'SYSTEM OFFLINE','RADAR');
    return;
  }
  // Own-fleet toggle (persist via localStorage)
  let showOwnFleet = true;
  try{ const raw = localStorage.getItem('radar_show_ownfleet'); if(raw!==null) showOwnFleet = raw==='1'; }catch(_){ showOwnFleet = true; }

  const toggleWrap=document.createElement('div'); toggleWrap.className='row section';
  const ownLabel=document.createElement('span'); ownLabel.textContent='Show Own Fleet'; ownLabel.style.marginRight='8px';
  const ownTgl=document.createElement('button'); ownTgl.className='btn'; ownTgl.textContent = showOwnFleet ? 'ON' : 'OFF';
  ownTgl.onclick=function(){ showOwnFleet = !showOwnFleet; ownTgl.textContent = showOwnFleet ? 'ON' : 'OFF'; try{ localStorage.setItem('radar_show_ownfleet', showOwnFleet?'1':'0'); }catch(_){} render(j); };
  toggleWrap.appendChild(ownLabel); toggleWrap.appendChild(ownTgl); p.appendChild(toggleWrap);

  const lockedId = (j.radar && j.radar.locked_id!==undefined && j.radar.locked_id!==null)? Number(j.radar.locked_id): null;
  const primary = (j.primary && typeof j.primary==='object')? j.primary : null;
  let contacts = Array.isArray(j.contacts)? j.contacts.slice(): [];
  // Apply own-fleet visibility filter (own-fleet entries use id prefix 'fleet:')
  if(!showOwnFleet){ contacts = contacts.filter(c => String(c.id||'').slice(0,6) !== 'fleet:'); }
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
  ['#','Status','Type','Name','Grid','Range','Speed','TTI','ID','Lock'].forEach(function(k){ const th=document.createElement('th'); th.textContent=k; trh.appendChild(th); });
  thead.appendChild(trh); tbl.appendChild(thead);

  const tb=document.createElement('tbody');
  const list = contacts
    .slice()
    .sort(function(a,b){ return (a.range_nm||1e9)-(b.range_nm||1e9); })
    .slice(0,10);
  const lockContact = async (id)=>{ if(id===undefined||id===null) return; await fetch('/api/command?cmd='+encodeURIComponent(`/radar lock ${id}`)); await poll().catch(()=>{}); };
  list.forEach(function(c, idx){
    const tr=document.createElement('tr');
    if(lockedId!==null && Number(c.id)===lockedId){ tr.classList.add('locked-row'); }
    const tdIdx=document.createElement('td'); tdIdx.className='num'; tdIdx.textContent=String(idx+1);
    const tdStatus=document.createElement('td'); tdStatus.appendChild(colorTag(String(c.type||'—')));
    const tdType=document.createElement('td'); tdType.textContent=String(c.class||c.meta_class||c.meta?.cap?.class||'—');
    const tdName=document.createElement('td'); tdName.textContent=String(c.name||'—');
    const tdCell=document.createElement('td'); tdCell.textContent = c.cell ? String(c.cell) : '—';
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
    [tdIdx,tdStatus,tdType,tdName,tdCell,tdRange,tdSpeed,tdTTI,tdId,tdLock].forEach(function(td){ tr.appendChild(td); });
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
  const p=$('#station-panel'); p.innerHTML='';
  addStationControls(p,'WPN');
  const weaponsStatus = engSystemStatus(j,'FireControl_Weapons');
  if(weaponsStatus && weaponsStatus.toLowerCase()!=='ok'){
    renderStationOffline(p,'SYSTEM OFFLINE','WPN');
    return;
  }
  if(!ST.wpn) ST.wpn = { lockInput: '' };

  const contacts = Array.isArray(j.contacts)? j.contacts.slice(): [];
  const lockedId = (j.radar && j.radar.locked_id!==undefined && j.radar.locked_id!==null)? Number(j.radar.locked_id): null;
  const primaryContact = contacts.find(c=>Number(c.id)===lockedId) || null;

  const primaryBox=document.createElement('div'); primaryBox.className='wpn-primary';
  const primaryTitle=document.createElement('div'); primaryTitle.className='wpn-primary-title'; primaryTitle.textContent='Primary Target'; primaryBox.appendChild(primaryTitle);

  const infoTable=document.createElement('table'); infoTable.className='wpn-primary-table';
  const headRow=document.createElement('tr');
  ['#ID','Type','Name','Cell','Range','Speed','TTI'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; headRow.appendChild(th); });
  infoTable.appendChild(headRow);
  const dataRow=document.createElement('tr');
  const idCell=document.createElement('td'); idCell.textContent=primaryContact? String(primaryContact.id).padStart(2,'0') : '—'; dataRow.appendChild(idCell);
  const typeCell=document.createElement('td'); typeCell.textContent=primaryContact? String(primaryContact.type || primaryContact.class || '—') : '—'; dataRow.appendChild(typeCell);
  const nameCell=document.createElement('td'); nameCell.textContent=primaryContact? String(primaryContact.name||'—') : '—'; dataRow.appendChild(nameCell);
  const cellCell=document.createElement('td'); cellCell.textContent=primaryContact && primaryContact.cell ? String(primaryContact.cell) : '—'; dataRow.appendChild(cellCell);
  const rangeCell=document.createElement('td'); rangeCell.className='num'; rangeCell.textContent=primaryContact && primaryContact.range_nm!=null? `${fmt(primaryContact.range_nm,1)} nm`:'—'; dataRow.appendChild(rangeCell);
  const speedCell=document.createElement('td'); speedCell.className='num'; speedCell.textContent=primaryContact && primaryContact.speed!=null? `${fmt(primaryContact.speed,0)} kn`:'—'; dataRow.appendChild(speedCell);
  const ttiCell=document.createElement('td'); ttiCell.className='num';
  try{
    const tti = primaryContact? computeTTI(primaryContact):null;
    ttiCell.textContent = (tti!==null)? `${tti}s`:'—';
  }catch(_){ ttiCell.textContent='—'; }
  dataRow.appendChild(ttiCell);
  infoTable.appendChild(dataRow);
  primaryBox.appendChild(infoTable);

  const controls=document.createElement('div'); controls.className='wpn-lock-controls';
  const label=document.createElement('span'); label.textContent='PRIMARY TARGET ID'; controls.appendChild(label);
  const input=document.createElement('input'); input.type='text'; input.className='input mono wpn-lock-input'; input.placeholder='— —';
  const preset = ST.wpn.lockInput || (primaryContact? String(primaryContact.id).padStart(2,'0') : '');
  if(preset) input.value=preset;
  input.addEventListener('input', function(){ ST.wpn.lockInput = (input.value||'').trim(); });
  controls.appendChild(input);

  const lockBtn=document.createElement('button'); lockBtn.className='btn nav-set-btn wpn-lock-btn'; lockBtn.textContent='LOCK';
  const unlockBtn=document.createElement('button'); unlockBtn.className='btn wpn-unlock-btn'; unlockBtn.textContent='UNLOCK';
  const msg=document.createElement('span'); msg.className='wpn-msg muted';

  const doLock=async function(idStr){
    const sanitized=(idStr||'').trim();
    if(!sanitized){ msg.textContent=''; msg.className='wpn-msg muted'; return; }
    ST.wpn.lockInput = sanitized;
    try{
      const res=await fetch('/api/command?cmd='+encodeURIComponent('/radar lock '+sanitized));
      let ok=false;
      try{
        const payload=await res.json();
        ok = !!(payload && payload.ok !== false);
      }catch(_){ ok = res.ok; }
      if(ok){ msg.textContent='LOCKED'; msg.className='wpn-msg ok'; await poll().catch(()=>{}); }
      else{ msg.textContent='ERR'; msg.className='wpn-msg err'; }
    }catch(_){ msg.textContent='ERR'; msg.className='wpn-msg err'; }
  };

  lockBtn.onclick=async function(){ await doLock(input.value); };
  input.addEventListener('keydown', function(ev){ if(ev.key==='Enter'){ ev.preventDefault(); doLock(input.value); } });

  unlockBtn.onclick=async function(){
    try{
      const res = await fetch('/api/command?cmd='+encodeURIComponent('/radar unlock'));
      let ok=false;
      try{
        const payload=await res.json();
        ok = !!(payload && payload.ok !== false);
      }catch(_){ ok = res.ok; }
      if(ok){ msg.textContent='UNLOCKED'; msg.className='wpn-msg ok'; ST.wpn.lockInput=''; input.value=''; await poll().catch(()=>{}); }
      else{ msg.textContent='ERR'; msg.className='wpn-msg err'; }
    }catch(_){ msg.textContent='ERR'; msg.className='wpn-msg err'; }
  };

  controls.appendChild(lockBtn);
  controls.appendChild(unlockBtn);
  controls.appendChild(msg);
  primaryBox.appendChild(controls);
  p.appendChild(primaryBox);

  const audioState = j && j.audio ? j.audio : {};
  const rawShots = Array.isArray(audioState.shots_in_flight) ? audioState.shots_in_flight : [];
  const shotsBox=document.createElement('div'); shotsBox.className='wpn-flight';
  const shotsTitle=document.createElement('div'); shotsTitle.className='wpn-flight-title'; shotsTitle.textContent='Shots In Flight'; shotsBox.appendChild(shotsTitle);
  if(rawShots.length){
    const shotsTable=document.createElement('table'); shotsTable.className='wpn-flight-table';
    const head=document.createElement('thead'); const hr=document.createElement('tr');
    ['Weapon','Target','Grid','ETA','Pk','Result','Range'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; hr.appendChild(th); });
    head.appendChild(hr); shotsTable.appendChild(head);
    const body=document.createElement('tbody');
    const shots = rawShots.slice().sort(function(a,b){ return Number(a.eta_s||0) - Number(b.eta_s||0); });
    shots.forEach(function(shot){
      const tr=document.createElement('tr');
      const weaponCell=document.createElement('td'); weaponCell.textContent=String(shot.weapon||'—'); tr.appendChild(weaponCell);
      const tgtLabel = shot.target ? String(shot.target) : (shot.target_id!=null ? `Target ${shot.target_id}` : '—');
      const tgtCell=document.createElement('td'); tgtCell.textContent=tgtLabel; tr.appendChild(tgtCell);
      const cellCell=document.createElement('td'); cellCell.textContent = shot.cell ? String(shot.cell) : '—'; tr.appendChild(cellCell);
      const etaCell=document.createElement('td'); etaCell.className='num';
      const eta = Number(shot.eta_s||0);
      const resultRaw = String(shot.result||'').trim().toLowerCase();
      const hasResult = resultRaw==='hit' || resultRaw==='miss';
      if(hasResult){
        etaCell.textContent = '—';
      }else if(eta > 0){
        etaCell.textContent = `${eta}s`;
        if(eta <= 10) etaCell.classList.add('eta-soon');
      }else{
        etaCell.textContent = 'IMPACT';
      }
      tr.appendChild(etaCell);
      const pkCell=document.createElement('td'); pkCell.className='num';
      const pkPct = Number(shot.pk_pct||0);
      pkCell.textContent = `${Math.max(0, Math.min(100, pkPct))}%`;
      tr.appendChild(pkCell);
      const resultCell=document.createElement('td');
      if(hasResult){
        const upper = resultRaw.toUpperCase();
        resultCell.textContent = upper;
        resultCell.className = 'result-cell '+(resultRaw==='hit'?'result-hit':'result-miss');
      }else{
        resultCell.textContent = '—';
        resultCell.className = 'result-cell muted';
      }
      tr.appendChild(resultCell);
      const rangeCell=document.createElement('td'); rangeCell.className='num';
      const rangeNm = Number(shot.range_nm||0);
      rangeCell.textContent = Number.isFinite(rangeNm) ? `${rangeNm.toFixed(1)} nm` : '—';
      tr.appendChild(rangeCell);
      body.appendChild(tr);
    });
    shotsTable.appendChild(body);
    shotsBox.appendChild(shotsTable);
  }else{
    const empty=document.createElement('div'); empty.className='wpn-flight-empty muted'; empty.textContent='No active shots'; shotsBox.appendChild(empty);
  }
  p.appendChild(shotsBox);

  const row=document.createElement('div'); row.className='row section';
  const lab=document.createElement('span'); lab.textContent='Test mode'; lab.style.marginRight='6px';
  const btn=document.createElement('button'); btn.className='btn'; btn.textContent=ST.test?'ON':'OFF'; btn.onclick=function(){ ST.test=!ST.test; btn.textContent=ST.test?'ON':'OFF'; };
  row.appendChild(lab); row.appendChild(btn); p.appendChild(row);
  const tbl=document.createElement('table'); const thead=document.createElement('thead'); const trh=document.createElement('tr');
  ['Weapon','Ammo','Range (nm)','Status','Arm','ARM Status','Timer','Fire'].forEach(function(k){
    const th=document.createElement('th');
    if(k==='Ammo' || k.startsWith('Range') || k==='Timer') th.className='num';
    th.textContent=k;
    trh.appendChild(th);
  });
  thead.appendChild(trh); tbl.appendChild(thead);
  const tb=document.createElement('tbody');
  const weaponsList = Array.isArray(j.weapons)? j.weapons : [];

  function weaponReadyToFire(w){
    if(!w) return false;
    const state = String(w.armed||'Safe');
    if(state !== 'Armed') return false;
    if(Number(w.arming_s||0) > 0) return false;
    if(Number(w.cooldown_s||0) > 0) return false;
    if(Number(w.ammo||0) <= 0) return false;
    if(ST.test) return true;
    return !!w.in_range;
  }

  weaponsList.forEach(function(w){
    const tr=document.createElement('tr');
    const state=String(w.armed||'Safe');
    const isArmed = state==='Armed';
    const isArming = state==='Arming';
    const ammo = Number(w.ammo||0);
    const cooldownLeft = Math.max(0, Number(w.cooldown_s||0));
    const armingLeft = Math.max(0, Number(w.arming_s||0));
    const inRange = !!w.in_range;

    const tdN=document.createElement('td'); tdN.textContent=String(w.name||'—');
    const tdA=document.createElement('td'); tdA.className='num'; tdA.textContent=String(ammo);
    const tdR=document.createElement('td'); tdR.className='num'; tdR.textContent=fmt(w.min_nm,0)+'–'+fmt(w.max_nm,0);

    const tdStatus=document.createElement('td');
    const statusBadge=document.createElement('span'); statusBadge.className='status-badge '+(inRange?'on':'off');
    statusBadge.textContent=inRange?'IN RANGE':'OUT OF RANGE';
    tdStatus.appendChild(statusBadge);

    const tdArm=document.createElement('td');
    const armBtn=document.createElement('button'); armBtn.className='btn toggle-btn';
    armBtn.setAttribute('aria-pressed', isArmed || isArming ? 'true' : 'false');
    if(isArmed) armBtn.classList.add('on');
    if(isArming) armBtn.classList.add('pending');
    let armLabel='ARM';
    if(isArming){ armLabel='ARMING'; }
    else if(isArmed){ armLabel='SAFE'; }
    armBtn.textContent=armLabel;
    armBtn.title = isArmed ? 'Click to safe weapon' : (isArming ? 'Arming in progress' : 'Click to arm weapon');
    if(isArming) armBtn.disabled = true;
    armBtn.onclick=async function(){
      const next=(state==='Safe')?'Armed':'Safe';
      armBtn.disabled=true;
      msg.textContent = (next==='Armed')?'ARMING...':'SAFING...';
      msg.className='wpn-msg muted';
      try{
        const res=await fetch('/weapons/arm',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:w.name, state: next})});
        const payload=await res.json().catch(()=>({}));
        if(payload && payload.ok){
          const stLabel = String(payload.state || next || 'OK').toUpperCase();
          msg.textContent=stLabel;
          msg.className='wpn-msg ok';
          await poll().catch(()=>{});
        }else{
          msg.textContent = payload && payload.error ? String(payload.error) : 'ERR';
          msg.className='wpn-msg err';
        }
      }catch(_){
        msg.textContent='ERR';
        msg.className='wpn-msg err';
      }finally{
        armBtn.disabled=false;
      }
    };
    tdArm.appendChild(armBtn);

    const tdArmStatus=document.createElement('td');
    const armDot=document.createElement('span'); armDot.className='statdot';
    if(isArmed) armDot.classList.add('on');
    else if(isArming) armDot.classList.add('pending');
    tdArmStatus.appendChild(armDot);

    const tdTimer=document.createElement('td'); tdTimer.className='num';
    let timerLabel='—';
    if(armingLeft>0){
      timerLabel = `ARM ${armingLeft}s`;
      tdTimer.classList.add('timer-await');
    }else if(cooldownLeft>0){
      timerLabel = `${cooldownLeft}s`;
      tdTimer.classList.add('timer-await');
    }else if(isArmed){
      timerLabel = 'READY';
    }
    tdTimer.textContent=timerLabel;

    const tdF=document.createElement('td');
    const fb=document.createElement('button'); fb.className='btn'; fb.textContent=ST.test?'TEST FIRE':'FIRE';
    const ready = weaponReadyToFire(w);
    fb.disabled = !ready;
    fb.classList.toggle('ready', ready);
    fb.onclick=async function(){
      if(fb.disabled) return;
      const body={name:w.name, mode:(ST.test?'test':'real')};
      const r=await fetch('/weapons/fire',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
      const payload=await r.json().catch(()=>({}));
      if(payload && payload.error){
        msg.textContent=String(payload.error||'ERR'); msg.className='wpn-msg err';
      }else{
        const modeLabel = String(payload && payload.result || '').toUpperCase();
        const rangeOk = !!(payload && (payload.range_ok !== false));
        if(modeLabel==='TEST'){
          msg.textContent = 'TEST FIRE';
        }else{
          msg.textContent = rangeOk ? 'FIRED' : 'FIRED (OOR)';
        }
        msg.className='wpn-msg ok';
        try{
          const tgtName = primaryContact && primaryContact.name ? String(primaryContact.name) : '';
          let label;
          if(tgtName){
            label = `${w.name} fired at ${tgtName}`;
          }else{
            label = `Weapon fired: ${w.name}`;
          }
          if(!rangeOk) label += ' (range exceeded)';
          pushEvent('weapon.fire', label);
        }catch(_){ }
      }
      await poll().catch(()=>{});
    };
    tdF.appendChild(fb);
    tr.appendChild(tdN);
    tr.appendChild(tdA);
    tr.appendChild(tdR);
    tr.appendChild(tdStatus);
    tr.appendChild(tdArm);
    tr.appendChild(tdArmStatus);
    tr.appendChild(tdTimer);
    tr.appendChild(tdF);
    tb.appendChild(tr);
  });
  tbl.appendChild(tb); p.appendChild(tbl);
}


function renderRADIO(j){
  const p=$('#station-panel');
  try{
    const ae=document.activeElement;
    if(ae && ['mission-loadout','mission-type','mission-target','mission-cap-cell'].includes(ae.id)){
      return;
    }
  }catch(_){ }
  p.innerHTML='';
  addStationControls(p,'RADIO');
  const commsStatus = engSystemStatus(j,'COMMS');
  if(commsStatus && commsStatus.toLowerCase()!=='ok'){
    renderStationOffline(p,'SYSTEM OFFLINE','RADIO');
    return;
  }

  const fleet = Array.isArray(j.ownfleet)? j.ownfleet : [];
  const flagship = fleet.find(u=>String(u.name||'').toLowerCase().includes('hermes')) ||
                   fleet.find(u=>String(u.id||'')!=='own') || null;
  const flagshipName = flagship ? String(flagship.name||'HMS Hermes') : 'HMS Hermes';
  const flagshipCell = flagship ? String(flagship.cell||'—') : '—';
  const flagshipSpd = (flagship && flagship.speed!=null)? fmt(flagship.speed,0) : '—';
  const flagshipCourse = (flagship && flagship.heading!=null)? fmt(flagship.heading,0) : '—';

  const hermesTable=document.createElement('table'); hermesTable.className='comms-table comms-top-table';
  const hermesHead=document.createElement('tr');
  ['Hermes','Grid','Spd','Course','Actions'].forEach(function(label){
    const th=document.createElement('th'); th.textContent=label; hermesHead.appendChild(th);
  });
  hermesTable.appendChild(hermesHead);
  const hermesRow=document.createElement('tr');
  [flagshipName,flagshipCell,flagshipSpd,flagshipCourse].forEach(function(val,idx){
    const td=document.createElement('td');
    if(idx>=2) td.className='num';
    td.textContent=val;
    hermesRow.appendChild(td);
  });
  const hermesActionTd=document.createElement('td'); hermesActionTd.className='action';
  const moveCloserBtn=document.createElement('button'); moveCloserBtn.className='btn'; moveCloserBtn.textContent='MOVE CLOSER';
  const moveAwayBtn=document.createElement('button'); moveAwayBtn.className='btn'; moveAwayBtn.textContent='MOVE AWAY'; moveAwayBtn.style.marginLeft='6px';
  hermesActionTd.appendChild(moveCloserBtn);
  hermesActionTd.appendChild(moveAwayBtn);
  hermesRow.appendChild(hermesActionTd);
  hermesTable.appendChild(hermesRow);
  p.appendChild(hermesTable);

  const hermesMsg=document.createElement('div'); hermesMsg.className='comms-msg muted'; hermesMsg.textContent='';
  p.appendChild(hermesMsg);

  function setHermesMsg(text, kind){
    hermesMsg.textContent = text || '';
    hermesMsg.className = 'comms-msg ' + (kind || 'muted');
  }
  async function adjustHermes(direction){
    const path = direction==='in'? '/nav/hermes/close_in' : '/nav/hermes/stand_off';
    setHermesMsg(direction==='in'?'Ordering Hermes to move closer…':'Ordering Hermes to move away…','muted');
    try{
      const res=await fetch(path);
      const data=await res.json();
      if(data && data.ok){
        const summary = formatHermesOrderSummary(data);
        const note = (data && typeof data.message==='string') ? data.message.trim() : '';
        if(summary && note){
          setHermesMsg(`${summary} — ${note}`,'warn');
        }else if(note){
          setHermesMsg(note,'warn');
        }else{
          setHermesMsg(summary || 'Order acknowledged','ok');
        }
        await poll().catch(()=>{});
      }else{
        setHermesMsg((data && data.error)? String(data.error) : 'Command failed','err');
      }
    }catch(_){
      setHermesMsg('Command failed','err');
    }
  }
  moveCloserBtn.onclick=function(){ adjustHermes('in'); };
  moveAwayBtn.onclick=function(){ adjustHermes('out'); };

  const lockedId = (j.radar && j.radar.locked_id!==undefined && j.radar.locked_id!==null)? Number(j.radar.locked_id) : null;
  const primaryContact = (lockedId!=null && Array.isArray(j.contacts))? j.contacts.find(c=>Number(c.id)===lockedId) : null;
  const cap = j.cap || {};
  const tasks = Array.isArray(cap.tasks)? cap.tasks : (Array.isArray(cap.missions)? cap.missions : []);

  if(!ST.comms) ST.comms = {};
  const missionDefaults = { loadout: 'aim9', missionType: 'intercept', target: 'primary', capCell: '' };
  const missionState = Object.assign({}, missionDefaults, ST.comms.mission || {});
  try{
    const savedLoadout = localStorage.getItem('comms_loadout');
    if(savedLoadout) missionState.loadout = savedLoadout;
    const savedType = localStorage.getItem('comms_mission_type');
    if(savedType) missionState.missionType = savedType;
    const savedTarget = localStorage.getItem('comms_mission_target');
    if(savedTarget) missionState.target = savedTarget;
    const savedCell = localStorage.getItem('comms_capCell');
    if(savedCell) missionState.capCell = savedCell;
  }catch(_){ }
  ST.comms.mission = missionState;
  ST.comms.capCell = missionState.capCell || '';

  const missionHeader=document.createElement('h3'); missionHeader.className='comms-subhead'; missionHeader.textContent='Mission editor'; p.appendChild(missionHeader);

  const missionTable=document.createElement('table'); missionTable.className='comms-table mission-editor';
  const missionHeadRow=document.createElement('tr');
  ['Loadout','Mission type','Target','Enter grid','Active mission'].forEach(function(label){
    const th=document.createElement('th'); th.textContent=label; missionHeadRow.appendChild(th);
  });
  missionTable.appendChild(missionHeadRow);
  const missionBodyRow=document.createElement('tr');

  const loadoutTd=document.createElement('td');
  const loadoutSelect=document.createElement('select'); loadoutSelect.className='input'; loadoutSelect.id='mission-loadout';
  [{value:'aim9',label:'Sidewinder'},{value:'bombs',label:'Bombs'}].forEach(function(opt){
    const option=document.createElement('option'); option.value=opt.value; option.textContent=opt.label; loadoutSelect.appendChild(option);
  });
  loadoutSelect.value = missionState.loadout || 'aim9';
  loadoutTd.appendChild(loadoutSelect);
  missionBodyRow.appendChild(loadoutTd);

  const typeTd=document.createElement('td');
  const missionTypeSelect=document.createElement('select'); missionTypeSelect.className='input'; missionTypeSelect.id='mission-type';
  [{value:'intercept',label:'Intercept'},{value:'cap',label:'CAP'}].forEach(function(opt){
    const option=document.createElement('option'); option.value=opt.value; option.textContent=opt.label; missionTypeSelect.appendChild(option);
  });
  missionTypeSelect.value = missionState.missionType || 'intercept';
  typeTd.appendChild(missionTypeSelect);
  missionBodyRow.appendChild(typeTd);

  const targetTd=document.createElement('td');
  const targetSelect=document.createElement('select'); targetSelect.className='input'; targetSelect.id='mission-target';
  targetTd.appendChild(targetSelect);
  missionBodyRow.appendChild(targetTd);

  const cellTd=document.createElement('td');
  const capCellInput=document.createElement('input'); capCellInput.className='input mono'; capCellInput.id='mission-cap-cell'; capCellInput.placeholder='CAP cell'; capCellInput.maxLength=5;
  if(missionState.capCell) capCellInput.value = missionState.capCell;
  cellTd.appendChild(capCellInput);
  missionBodyRow.appendChild(cellTd);

  const summaryTd=document.createElement('td');
  const missionSummary=document.createElement('div'); missionSummary.className='mission-summary mono muted';
  summaryTd.appendChild(missionSummary);
  missionBodyRow.appendChild(summaryTd);

  missionTable.appendChild(missionBodyRow);
  p.appendChild(missionTable);

  const activeMissionCard=document.createElement('div'); activeMissionCard.className='active-mission-card';
  const activeMissionLabel=document.createElement('span'); activeMissionLabel.className='active-mission-label'; activeMissionLabel.textContent='Active mission';
  const activeMissionValue=document.createElement('span'); activeMissionValue.className='active-mission-value mono muted'; activeMissionValue.textContent='—';
  activeMissionCard.appendChild(activeMissionLabel);
  activeMissionCard.appendChild(activeMissionValue);
  p.appendChild(activeMissionCard);

  const hermesHeader=document.createElement('h3'); hermesHeader.className='comms-subhead'; hermesHeader.textContent='HERMES'; p.appendChild(hermesHeader);
  const sharSummary=document.createElement('div'); sharSummary.className='shar-summary';

  const readyPairs = (cap.readiness && typeof cap.readiness==='object')? cap.readiness.ready_pairs : (cap.pairs ?? cap.ready_pairs);
  const airframes = (cap.readiness && typeof cap.readiness==='object')? cap.readiness.airframes : (cap.airframes ?? cap.airframe_pool_total);
  const cooldownRaw = (cap.readiness && typeof cap.readiness==='object')? cap.readiness.cooldown_s : (cap.cooldown_s ?? 0);
  const readyPairsNum = Number(readyPairs || 0);
  const airframesNum = Number(airframes || 0);
  const hasCooldown = Number(cooldownRaw || 0) > 0;
  const sidewindersCount = (function(){
    if(cap && cap.sidewinders!=null){
      const val = Number(cap.sidewinders);
      return Number.isFinite(val)? val : 0;
    }
    const pool = Number(cap && cap.sidewinders_pool != null ? cap.sidewinders_pool : NaN);
    const committed = Number(cap && cap.sidewinders_committed != null ? cap.sidewinders_committed : NaN);
    if(Number.isFinite(pool) || Number.isFinite(committed)){
      const p = Number.isFinite(pool)? pool : 0;
      const c = Number.isFinite(committed)? committed : 0;
      return p + c;
    }
    if(Array.isArray(tasks)){
      return tasks.reduce(function(sum, t){
        if(!t) return sum;
        const loadout = String(t.loadout || '').toLowerCase();
        if(loadout !== 'aim9') return sum;
        const status = String(t.status || '').toLowerCase();
        if(!['queued','airborne','onstation','rtb','recovering'].includes(status)) return sum;
        const left = Number(t.missiles_left != null ? t.missiles_left : 0);
        return sum + (Number.isFinite(left) ? Math.max(0, left) : 0);
      }, 0);
    }
    return 0;
  })();
  const committedCount = (function(){
    if(cap && cap.committed_airframes!=null){
      const val = Number(cap.committed_airframes);
      if(Number.isFinite(val)) return val;
    }
    if(cap && cap.committed!=null){
      const pairs = Number(cap.committed);
      if(Number.isFinite(pairs)){
        const basePairSize = Number(cap.pair_size || 2);
        const size = Number.isFinite(basePairSize) && basePairSize > 0 ? basePairSize : 2;
        return pairs * size;
      }
    }
    if(Array.isArray(tasks)){
      const active = tasks.filter(function(t){
        if(!t) return false;
        const status = String(t.status || '').toLowerCase();
        return ['queued','airborne','onstation','rtb','recovering'].includes(status);
      }).length;
      return active * 2;
    }
    return 0;
  })();

  const readyTag=document.createElement('span');
  readyTag.className='shar-ready ' + ((readyPairsNum > 0 && airframesNum >= 2 && !hasCooldown)? 'ok':'err');
  readyTag.textContent = (readyPairsNum > 0 && airframesNum >= 2 && !hasCooldown)? 'READY':'STANDBY';
  sharSummary.appendChild(readyTag);
  const summaryItems=[
    `pairs: ${readyPairsNum}`,
    `airframes: ${airframesNum}`,
    `sidewinders: ${Math.max(0, Math.round(sidewindersCount))}`,
    `cooldown: ${hasCooldown ? fmtDuration(cooldownRaw) : '0s'}`,
    `committed airframes: ${Math.max(0, Math.round(committedCount))}`
  ];
  summaryItems.forEach(function(text){
    const item=document.createElement('span'); item.textContent=text; sharSummary.appendChild(item);
  });
  p.appendChild(sharSummary);

  const actionsTable=document.createElement('table'); actionsTable.className='comms-launch-table';
  const actionsHead=document.createElement('tr');
  ['Action','Controls'].forEach(function(label){
    const th=document.createElement('th'); th.textContent=label; actionsHead.appendChild(th);
  });
  actionsTable.appendChild(actionsHead);

  const harrierRow=document.createElement('tr');
  const harrierLabel=document.createElement('td'); harrierLabel.textContent='Launch Harrier flight'; harrierRow.appendChild(harrierLabel);
  const harrierControls=document.createElement('td'); harrierControls.className='num';
  const launchBtn=document.createElement('button'); launchBtn.className='btn'; launchBtn.textContent='LAUNCH';
  harrierControls.appendChild(launchBtn);
  harrierRow.appendChild(harrierControls);
  actionsTable.appendChild(harrierRow);

  const seaRow=document.createElement('tr');
  const seaLabel=document.createElement('td'); seaLabel.textContent='Launch Sea King'; seaRow.appendChild(seaLabel);
  const seaControls=document.createElement('td'); seaControls.className='num';
  const resupplyBtn=document.createElement('button'); resupplyBtn.className='btn'; seaControls.appendChild(resupplyBtn);
  seaRow.appendChild(seaControls);
  actionsTable.appendChild(seaRow);
  p.appendChild(actionsTable);

  const statusMsg=document.createElement('div'); statusMsg.className='comms-msg muted'; statusMsg.textContent=''; p.appendChild(statusMsg);

  const commitHeader=document.createElement('h3'); commitHeader.className='comms-subhead'; commitHeader.textContent='Active flights'; p.appendChild(commitHeader);
  const commitTable=document.createElement('table'); commitTable.className='comms-commit-table';
  const commitHead=document.createElement('tr');
  ['Flight','Status','POS','Target','RNG','TOT','TOS','Engage','Reassign','RTB'].forEach(function(label){
    const th=document.createElement('th'); th.textContent=label; commitHead.appendChild(th);
  });
  commitTable.appendChild(commitHead);

  function setStatus(text, kind){
    statusMsg.textContent = text || '';
    statusMsg.className = 'comms-msg ' + (kind || 'muted');
  }

  function fmtDuration(sec){
    if(sec===undefined || sec===null) return '—';
    const n = Number(sec);
    if(!Number.isFinite(n)) return '—';
    if(n <= 0) return '0s';
    if(n < 120) return `${Math.round(n)}s`;
    if(n < 3600) return `${Math.round(n / 60)} min`;
    return `${Math.round(n / 3600)} hr`;
  }

  function normalizeCell(val){
    try{
      const raw=String(val||'').toUpperCase().replace(/[^A-Z0-9]/g,'');
      const match=raw.match(/^([A-Z]+)([0-9]+)$/);
      return match ? match[1]+match[2] : '';
    }catch(_){
      return '';
    }
  }

function currentMissionConfig(){
  const rawCell = (capCellInput.value || '').trim().toUpperCase();
  const missionType = missionTypeSelect.value || 'intercept';
  const targetValue = targetSelect.value || (missionType === 'cap' ? 'cap_cell' : 'primary');
  const hermesCap = missionType === 'cap' && targetValue === 'hermes';
  const rawLoadout = hermesCap ? 'aim9' : (loadoutSelect.value || 'aim9');
  return {
    loadout: rawLoadout,
    missionType,
    target: targetValue,
    capCell: rawCell,
    capCellNorm: normalizeCell(rawCell),
  };
}

  function enforceHermesDefaults(){
    const hermesCap = (missionTypeSelect.value === 'cap') && (targetSelect.value === 'hermes');
    if(hermesCap){
      if(loadoutSelect.value !== 'aim9') loadoutSelect.value = 'aim9';
      loadoutSelect.setAttribute('disabled','disabled');
    }else{
      loadoutSelect.removeAttribute('disabled');
    }
  }

  function refreshCapCellState(opts){
    const isCapMission = missionTypeSelect.value === 'cap';
    const capTarget = targetSelect.value === 'cap_cell';
    const locked = isCapMission && !capTarget;
    capCellInput.classList.toggle('input-locked', locked);
    if(locked){
      capCellInput.setAttribute('aria-disabled','true');
    }else{
      capCellInput.removeAttribute('aria-disabled');
      if(opts && opts.focus && document.activeElement !== capCellInput){
        try{ capCellInput.focus({preventScroll:true}); capCellInput.select(); }catch(_){ }
      }
    }
  }

let missionValid=false;

  function updateTargetOptions(){
    const missionType = missionTypeSelect.value || 'intercept';
    const desiredTarget = missionState.target || 'primary';
    const options=[
      {value:'primary',label:'Locked primary target'},
      {value:'cap_cell',label:'CAP grid cell'},
      {value:'hermes',label:'Hermes (Defend)'}
    ];
    targetSelect.innerHTML='';
    options.forEach(function(opt){
      if(missionType==='intercept' && opt.value!=='primary') return;
      const option=document.createElement('option'); option.value=opt.value; option.textContent=opt.label; targetSelect.appendChild(option);
    });
  if(missionType==='intercept'){
    missionState.target = 'primary';
    targetSelect.value = 'primary';
  }else{
    if(options.every(opt=>opt.value!==desiredTarget) || desiredTarget==='primary'){
      missionState.target = 'cap_cell';
    }
    targetSelect.value = missionState.target;
  }
  enforceHermesDefaults();
  saveMissionState();
  refreshCapCellState({focus: missionType==='cap' && missionState.target==='cap_cell'});
}

  function missionSummaryText(){
    const cfg = currentMissionConfig();
    const pieces=[];
    const loadoutLabel = cfg.loadout === 'bombs' ? 'Bombs' : 'Sidewinder';
    pieces.push(cfg.missionType === 'intercept' ? 'Intercept' : 'CAP');
    if(loadoutLabel) pieces.push(loadoutLabel);
    if(cfg.missionType === 'intercept'){
      if(primaryContact){
        const name = String(primaryContact.name || '').trim();
        const cell = String(primaryContact.cell || '').trim();
        if(name && cell) pieces.push(`${name} @ ${cell}`);
        else if(name) pieces.push(name);
        else if(cell) pieces.push(`Target ${cell}`);
        else pieces.push('Primary target');
      }else{
        pieces.push('No primary target locked');
      }
    }else{
      if(cfg.target === 'hermes'){
        const hermesLabel = (flagshipCell && flagshipCell !== '—') ? `Protect Hermes (${flagshipCell})` : 'Protect Hermes';
        pieces.push(hermesLabel);
      }else{
        const cellLabel = cfg.capCellNorm || cfg.capCell;
        pieces.push(cellLabel ? `CAP ${cellLabel}` : 'CAP cell needed');
      }
      pieces.push('5 nm diameter circle');
      pieces.push('10 min station');
    }
    return pieces.join(' • ');
  }

  function updateMissionSummary(){
    const summaryText = missionSummaryText();
    const displayText = summaryText || '—';
    missionSummary.textContent = displayText;
    activeMissionValue.textContent = displayText;
    const cfg = currentMissionConfig();
    let valid;
    if(cfg.missionType === 'intercept'){
      valid = !!primaryContact;
    }else if(cfg.target === 'hermes'){
      valid = Boolean(flagshipCell && flagshipCell !== '—');
    }else{
      valid = !!cfg.capCellNorm;
    }
    missionValid = Boolean(valid);
    if(displayText === '—'){
      missionSummary.className = 'mission-summary mono muted';
      activeMissionValue.className = 'active-mission-value mono muted';
    }else{
      missionSummary.className = 'mission-summary mono ' + (missionValid? 'ok':'err');
      activeMissionValue.className = 'active-mission-value mono ' + (missionValid? 'ok':'err');
    }
    if(!ST.comms) ST.comms = {};
    ST.comms.activeMissionSummary = summaryText || '';
    ST.comms.activeMissionValid = missionValid;
    ST.comms.activeMission = Object.assign({}, cfg);
    return missionValid;
  }

  function saveMissionState(){
    const cfg = currentMissionConfig();
    missionState.loadout = cfg.loadout;
    missionState.missionType = cfg.missionType;
    missionState.target = cfg.target;
    missionState.capCell = cfg.capCell;
    ST.comms.mission = missionState;
    ST.comms.capCell = missionState.capCell || '';
    ST.comms.activeMission = Object.assign({}, cfg);
    try{
      localStorage.setItem('comms_loadout', cfg.loadout);
      localStorage.setItem('comms_mission_type', cfg.missionType);
      localStorage.setItem('comms_mission_target', cfg.target);
      localStorage.setItem('comms_capCell', cfg.capCell || '');
    }catch(_){ }
  }

  function canRetaskStatus(status){
    const key=String(status||'').toLowerCase();
    return !['rtb','recovering','complete'].includes(key);
  }

  function updateButtonStates(){
    const canLaunch = missionValid && readyPairsNum > 0 && airframesNum >= 2 && !hasCooldown;
    launchBtn.disabled = !canLaunch;
    const resupply = j.resupply || {};
    resupplyBtn.disabled = Boolean(resupply && resupply.active);
    resupplyBtn.textContent = (resupply && resupply.active)? 'EN ROUTE' : 'LAUNCH';
  }

  function buildMissionDescriptor(){
    const cfg = currentMissionConfig();
   if(cfg.missionType==='intercept'){
      if(!primaryContact) return {ok:false, error:'No locked target.'};
      return {ok:true, data:{missionType:'intercept', loadout: cfg.loadout, target:'primary', primaryContact}};
    }
    if(cfg.target==='hermes'){
      if(!flagshipCell || flagshipCell==='—') return {ok:false, error:'Hermes position unknown.'};
      return {ok:true, data:{missionType:'cap', loadout: 'aim9', target:'hermes', cell:flagshipCell, follow:'hermes'}};
    }
    if(!cfg.capCellNorm) return {ok:false, error:'Enter CAP grid cell.'};
    return {ok:true, data:{missionType:'cap', loadout: cfg.loadout, target:'cap_cell', cell: cfg.capCellNorm}};
  }

  loadoutSelect.addEventListener('change', function(){
    saveMissionState();
    updateMissionSummary();
    updateButtonStates();
  });
  missionTypeSelect.addEventListener('change', function(){
    updateTargetOptions();
    updateMissionSummary();
    updateButtonStates();
  });
  targetSelect.addEventListener('change', function(){
    enforceHermesDefaults();
    saveMissionState();
    updateMissionSummary();
    updateButtonStates();
    refreshCapCellState({focus: missionTypeSelect.value==='cap' && targetSelect.value==='cap_cell'});
  });
  capCellInput.addEventListener('input', function(){
    try{
      const raw = capCellInput.value || '';
      const upper = raw.toUpperCase();
      if(raw !== upper){
        const pos = capCellInput.selectionStart;
        capCellInput.value = upper;
        if(pos!=null){ capCellInput.setSelectionRange(pos,pos); }
      }
    }catch(_){ }
    saveMissionState();
    updateMissionSummary();
    updateButtonStates();
  });
  capCellInput.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); try{ launchBtn.click(); }catch(_){ } } });

  updateTargetOptions();
  updateMissionSummary();
  refreshCapCellState();

  async function launchHarrier(){
    const descriptor = buildMissionDescriptor();
    if(!descriptor.ok){
      setStatus(descriptor.error, 'err');
      return;
    }
    const mission = descriptor.data;
    const summaryLabel = (ST.comms && ST.comms.activeMissionSummary) ? ST.comms.activeMissionSummary : missionSummaryText();
    setStatus('Processing launch order…','muted');
    launchBtn.disabled = true;
    try{
      if(mission.missionType==='intercept'){
        const body={ id: mission.primaryContact.id, loadout: mission.loadout };
        if(mission.primaryContact.cell) body.cell = String(mission.primaryContact.cell);
        const res=await fetch('/cap/request',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
        const data=await res.json();
        if(data && data.ok){
          setStatus((data.message && String(data.message)) || summaryLabel || 'Intercept pair launching','ok');
          await poll().catch(()=>{});
        }else{
          setStatus((data && (data.message || data.error))? String(data.message || data.error) : 'Intercept request failed','err');
        }
      }else{
        const payload={ cell: mission.cell, station_minutes: 10, radius_nm: 2.5, loadout: mission.loadout };
        if(mission.follow==='hermes') payload.follow='hermes';
        const res=await fetch('/cap/launch_to',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
        const data=await res.json();
        if(data && data.ok){
          const msg = (data.message && String(data.message)) || (summaryLabel || (mission.follow==='hermes'? 'CAP launching to defend Hermes' : `CAP launching to ${mission.cell}`));
          setStatus(msg,'ok');
          await poll().catch(()=>{});
        }else{
          setStatus((data && (data.message || data.error))? String(data.message || data.error) : 'CAP request failed','err');
        }
      }
    }catch(_){
      setStatus('Launch request failed','err');
    }finally{
      launchBtn.disabled = false;
      updateButtonStates();
    }
  }

  async function launchSeaKing(){
    const resupply = j.resupply || {};
    if(resupply && resupply.active){
      setStatus('Sea King already en route','muted');
      return;
    }
    setStatus('Launching Sea King…','muted');
    resupplyBtn.disabled = true;
    try{
      const res=await fetch('/resupply/launch',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
      const data=await res.json();
      if(data && data.ok){
        setStatus('Resupply helicopter launched','ok');
        await poll().catch(()=>{});
      }else{
        setStatus((data && data.error)? String(data.error) : 'Resupply failed','err');
      }
    }catch(_){
      setStatus('Resupply failed','err');
    }finally{
      updateButtonStates();
    }
  }

  launchBtn.onclick=launchHarrier;
  resupplyBtn.onclick=launchSeaKing;

  async function reassignFlight(missionId, triggerBtn){
    if(!missionId){
      setStatus('Unknown mission id','err');
      return;
    }
    const descriptor = buildMissionDescriptor();
    if(!descriptor.ok){
      setStatus(descriptor.error,'err');
      return;
    }
    const mission = descriptor.data;
    const summaryLabel = (ST.comms && ST.comms.activeMissionSummary) ? ST.comms.activeMissionSummary : missionSummaryText();
    const retaskLabel = summaryLabel && summaryLabel !== '—' ? summaryLabel : '';
    if(triggerBtn) triggerBtn.disabled = true;
    setStatus(`Retasking SHAR ${missionId}…`,'muted');
    try{
      let res;
      if(mission.missionType==='intercept'){
        res = await fetch('/cap/vector',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mission_id: missionId})});
      }else{
        const payload={ mission_id: missionId, cell: mission.cell, minutes: 10 };
        if(mission.follow==='hermes') payload.follow='hermes';
        res = await fetch('/cap/convert_to_cap',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      }
      const data = await res.json();
      if(!data || !data.ok){
        setStatus((data && (data.message || data.error))? String(data.message || data.error) : 'Retask failed','err');
        return;
      }
      if(retaskLabel){
        setStatus(`SHAR ${missionId} retasked to ${retaskLabel}`,'ok');
      }else{
        setStatus(`SHAR ${missionId} retasked`,'ok');
      }
      await poll().catch(()=>{});
    }catch(_){
      setStatus('Retask failed','err');
    }finally{
      if(triggerBtn) triggerBtn.disabled = false;
      updateButtonStates();
    }
  }

  function targetDescription(t){
    if(!t) return '—';
    const follow = (t.follow || (t.permission && t.permission.follow)) ? String(t.follow || t.permission.follow) : null;
    if(follow && follow.toLowerCase()==='hermes') return 'Hermes (Defend)';
    if(t.target_label) return String(t.target_label);
    const name = String(t.target_name || '').trim();
    const cell = String(t.target_cell || '').trim();
    if(String(t.kind || '').toLowerCase()==='cap'){
      if(cell) return `CAP ${cell}`;
    }
    if(name && cell) return `${name} @ ${cell}`;
    if(name) return name;
    if(cell) return cell;
    return '—';
  }

  function addSharRow(t){
    const tr=document.createElement('tr');
    const missionId = t && (t.id!=null ? t.id : t.n);
    const statusKey = String(t && t.status || '').toLowerCase();
    const flightTd=document.createElement('td'); flightTd.textContent = missionId ? `SHAR ${missionId}` : 'SHAR'; tr.appendChild(flightTd);
    const statusTd=document.createElement('td'); statusTd.textContent = String(t.status || '—').toUpperCase(); tr.appendChild(statusTd);
    const posTd=document.createElement('td'); posTd.textContent = String(t.cur_cell || t.origin_cell || '—'); tr.appendChild(posTd);
    const tgtTd=document.createElement('td'); tgtTd.textContent = targetDescription(t); tr.appendChild(tgtTd);
    const rngTd=document.createElement('td'); rngTd.className='num';
    rngTd.textContent = (t.range_nm!=null && Number.isFinite(Number(t.range_nm)))? `${fmt(t.range_nm,1)} nm` : '—';
    tr.appendChild(rngTd);
    const totTd=document.createElement('td'); totTd.className='num';
    totTd.textContent = t.tot_s!=null ? fmtDuration(t.tot_s) : (statusKey==='onstation' ? 'ON STN' : '—');
    tr.appendChild(totTd);
    const tosTd=document.createElement('td'); tosTd.className='num';
    tosTd.textContent = t.tos_s!=null ? fmtDuration(t.tos_s) : '—';
    tr.appendChild(tosTd);

    const permTd=document.createElement('td'); permTd.className='action';
    const perm = (t && t.permission && typeof t.permission==='object')? t.permission : {};
    if(perm.required){
      const authorized = Boolean(perm.authorized);
      const engageBtn=document.createElement('button'); engageBtn.className='btn'; engageBtn.textContent = authorized ? 'HOLD FIRE' : 'ENGAGE';
      engageBtn.onclick = async function(){
        if(!missionId){ setStatus('Unknown mission id','err'); return; }
        engageBtn.disabled = true;
        setStatus(authorized? 'Revoking engagement…':'Authorizing engagement…','muted');
        try{
          const res=await fetch('/cap/authorize',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: missionId, authorize: !authorized})});
          const data=await res.json();
          if(data && data.ok){
            setStatus(!authorized ? 'Engagement authorized' : 'Holding fire', 'ok');
            await poll().catch(()=>{});
          }else{
            setStatus((data && data.error)? String(data.error) : 'Authorization failed','err');
          }
        }catch(_){
          setStatus('Authorization failed','err');
        }finally{
          engageBtn.disabled = false;
        }
      };
      permTd.appendChild(engageBtn);
    }else{
      permTd.textContent='—';
    }
    tr.appendChild(permTd);

    const reassignTd=document.createElement('td'); reassignTd.className='action';
    if(missionId && canRetaskStatus(t.status)){
      const reBtn=document.createElement('button'); reBtn.className='btn'; reBtn.textContent='REASSIGN';
      reBtn.disabled = !missionValid;
      reBtn.onclick = function(){
        if(reBtn.disabled) return;
        reassignFlight(missionId, reBtn);
      };
      reassignTd.appendChild(reBtn);
    }else{
      reassignTd.textContent='—';
    }
    tr.appendChild(reassignTd);

    const rtbTd=document.createElement('td'); rtbTd.className='action';
    const rtbBtn=document.createElement('button'); rtbBtn.className='btn'; rtbBtn.textContent='RTB';
    if(!missionId || ['rtb','recovering','complete'].includes(statusKey)) rtbBtn.disabled=true;
    rtbBtn.onclick = async function(){
      if(!missionId){ setStatus('Unknown mission id','err'); return; }
      setStatus('Ordering RTB…','muted');
      rtbBtn.disabled = true;
      try{
        const res=await fetch('/cap/rtb',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mission_id: missionId})});
        const data=await res.json();
        if(data && data.ok){
          setStatus(data.message || `SHAR ${missionId} RTB`, 'ok');
          await poll().catch(()=>{});
        }else{
          setStatus((data && (data.message || data.error))? String(data.message || data.error) : 'RTB order failed','err');
          rtbBtn.disabled = false;
        }
      }catch(_){
        setStatus('RTB order failed','err');
        rtbBtn.disabled = false;
      }
    };
    rtbTd.appendChild(rtbBtn);
    tr.appendChild(rtbTd);
    commitTable.appendChild(tr);
  }

  const sharTasks = tasks.filter(function(t){
    if(!t) return false;
    if(t.kind && String(t.kind).toLowerCase()==='resupply') return false;
    return true;
  });

  if(sharTasks.length){
    sharTasks.forEach(addSharRow);
  }else{
    const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=10; td.textContent='No active SHAR flights'; tr.appendChild(td); commitTable.appendChild(tr);
  }

  const resupply = j.resupply || {};
  if(resupply && resupply.active){
    const tr=document.createElement('tr'); tr.className='resupply-row';
    const cols=[
      'Sea King',
      String(resupply.stage || 'ACTIVE').toUpperCase(),
      String(resupply.cell || resupply.pos || '—'),
      'Sheffield (Resupply)',
      (resupply.range_nm!=null && Number.isFinite(Number(resupply.range_nm)))? `${fmt(resupply.range_nm,1)} nm` : '—',
      '—',
      resupply.left_s!=null ? fmtDuration(resupply.left_s) : '—',
      '—',
      '—',
      '—'
    ];
    cols.forEach(function(text, idx){
      const td=document.createElement('td');
      if(idx>=4 && idx<=6) td.className='num';
      td.textContent=text;
      tr.appendChild(td);
    });
    commitTable.appendChild(tr);
  }

  p.appendChild(commitTable);

  updateButtonStates();
}
function renderENG(j){
  const p=$('#station-panel'); p.innerHTML='';
  addStationControls(p,'ENG');
  const eng = j.eng || {};
  const capReady = (j.cap && j.cap.readiness) || {};
  const systems = Array.isArray(eng.systems) ? eng.systems : [];
  const teamsTotal = Number(eng.teams_total ?? 0);
  const teamsFree = Number(eng.teams_free ?? teamsTotal);
  const teamsUsed = Math.max(0, teamsTotal - teamsFree);
  const shipPct = eng.ship_pct != null ? Number(eng.ship_pct) : null;

  const fleet = Array.isArray(j.ownfleet) ? j.ownfleet : [];
  let flagship = fleet.find(u=>String(u.name||'').toLowerCase().includes('hermes'));
  if(!flagship && fleet.length) flagship = fleet[0];
  const flagshipName = flagship ? String(flagship.name||'Flagship') : 'HMS Hermes';

  const wrap=document.createElement('div'); wrap.className='eng-wrap';

  const title=document.createElement('h2'); title.className='eng-title'; title.textContent='Engineering Console'; wrap.appendChild(title);

  const summary=document.createElement('table'); summary.className='eng-summary';
  const head=document.createElement('tr');
  ['Flagship','Status','SHAR','Repair team'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; head.appendChild(th); });
  summary.appendChild(head);
  const shipClass = shipPct==null ? 'status-ok' : (shipPct < 40 ? 'status-err' : (shipPct < 70 ? 'status-warn' : 'status-ok'));
  const row=document.createElement('tr');
  const tdName=document.createElement('td'); tdName.textContent=flagshipName; row.appendChild(tdName);
  const tdShip=document.createElement('td'); tdShip.textContent = shipPct!=null ? `${shipPct}%` : '—'; tdShip.className=`eng-pill-value ${shipClass}`; row.appendChild(tdShip);
  const sharTd=document.createElement('td');
  const sharReady = capReady.available ? 'READY' : 'STANDBY';
  sharTd.textContent = sharReady;
  sharTd.className = 'eng-pill-value ' + (capReady.available ? 'status-ok' : 'status-warn');
  row.appendChild(sharTd);
  const teamTd=document.createElement('td');
  teamTd.className='eng-pill-value ' + (teamsFree > 0 ? 'status-ok' : 'status-err');
  teamTd.textContent = `(${teamsUsed}/${teamsTotal})`;
  row.appendChild(teamTd);
  summary.appendChild(row);
  wrap.appendChild(summary);

  const sub=document.createElement('h3'); sub.className='eng-subtitle'; sub.textContent='ENG Status'; wrap.appendChild(sub);

  function timerLabel(sec){
    if(sec===undefined || sec===null) return '—';
    const n = Number(sec);
    if(!Number.isFinite(n) || n<=0) return '—';
    if(n < 120) return `${Math.round(n)}s`;
    return `${Math.round(n/60)} min`;
  }

  async function toggleTeam(id, assign){
    try{
      const res = await fetch(assign?'/eng/assign':'/eng/release',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
      const payload = await res.json().catch(()=>({}));
      if(!(payload && payload.ok)){ console.warn('ENG action failed', payload); }
    }catch(err){ console.error('ENG action error', err); }
    await poll().catch(()=>{});
  }

  const table=document.createElement('table'); table.className='eng-table';
  const thead=document.createElement('tr');
  ['#','Systems','Status','Timer','Repair'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; thead.appendChild(th); });
  table.appendChild(thead);

  if(!systems.length){
    const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=5; td.textContent='No engineering data available'; tr.appendChild(td); table.appendChild(tr);
  }else{
    systems.forEach(function(sys){
      const tr=document.createElement('tr');
      const idxTd=document.createElement('td'); idxTd.className='index'; idxTd.textContent=String(sys.index||sys.id||''); tr.appendChild(idxTd);
      const nameTd=document.createElement('td'); nameTd.className='system-name'; nameTd.textContent=String(sys.label||sys.id||'System'); tr.appendChild(nameTd);
      const statusTd=document.createElement('td');
      const status = String(sys.status||'OK');
      const statusKey = status.toLowerCase();
      let statusClass = 'ok';
      if(statusKey==='offline') statusClass='offline';
      else if(statusKey==='damaged') statusClass = sys.team_assigned ? 'repair' : 'damaged';
      statusTd.className='eng-status '+statusClass;
      statusTd.textContent=status;
      tr.appendChild(statusTd);
      const timerTd=document.createElement('td'); timerTd.className='timer';
      timerTd.textContent = timerLabel(sys.timer_s);
      tr.appendChild(timerTd);
      const actTd=document.createElement('td'); actTd.className='num';
      const btn=document.createElement('button'); btn.className='btn eng-commit';
      const assigned = Boolean(sys.team_assigned);
      btn.textContent = assigned ? 'RELEASE' : 'COMMIT';
      const canCommit = !assigned && statusKey !== 'ok';
      const teamsAvailable = teamsFree > 0;
      if(assigned){
        btn.disabled = false;
      }else{
        btn.disabled = !(canCommit && teamsAvailable);
      }
      btn.onclick=function(){ if(!btn.disabled){ toggleTeam(sys.id || sys.label || '', !assigned); } };
      actTd.appendChild(btn);
      if(assigned){
        const badge=document.createElement('span'); badge.className='eng-pill-value status-ok'; badge.textContent='1'; badge.style.marginLeft='8px';
        actTd.appendChild(badge);
      }
      tr.appendChild(actTd);
      if(statusKey==='offline') tr.classList.add('eng-row-offline');
      else if(statusKey==='damaged' && !assigned) tr.classList.add('eng-row-damaged');
      else if(assigned) tr.classList.add('eng-row-repair');
      table.appendChild(tr);
    });
  }

  wrap.appendChild(table);
  p.appendChild(wrap);
}

function renderLOG(){
  const p = $('#station-panel');
  p.innerHTML = '';

  const wrap = document.createElement('div');
  wrap.className = 'log-wrap';

  const title = document.createElement('div');
  title.className = 'log-title';
  title.textContent = 'SHIPS LOG';
  wrap.appendChild(title);

  const entries = Array.isArray(ST.eventHistory) ? ST.eventHistory.slice() : [];
  const latest = entries.length ? entries[entries.length - 1] : null;

  const headline = document.createElement('div');
  headline.className = 'log-headline';
  if (latest) {
    headline.textContent = String(latest.text || '').toUpperCase();
  } else {
    headline.classList.add('muted');
    headline.textContent = 'NO EVENTS RECORDED';
  }
  wrap.appendChild(headline);

  if (latest && latest.time) {
    const headTime = document.createElement('div');
    headTime.className = 'log-headline-time';
    headTime.textContent = latest.time;
    wrap.appendChild(headTime);
  }

  const list = document.createElement('div');
  list.className = 'log-list';
  const history = entries.slice(0, -1).reverse();

  if (!history.length) {
    const row = document.createElement('div');
    row.className = 'log-row muted';
    row.textContent = latest ? '—' : 'Awaiting first contact…';
    list.appendChild(row);
  } else {
    history.forEach(function(ev){
      const row = document.createElement('div');
      row.className = 'log-row';
      const time = document.createElement('span');
      time.className = 'log-time';
      time.textContent = ev.time || '—';
      const textSpan = document.createElement('span');
      textSpan.className = 'log-text';
      textSpan.textContent = ev.text || '';
      row.append(time, textSpan);
      list.appendChild(row);
    });
  }

  wrap.appendChild(list);
  p.appendChild(wrap);
}

function renderSYS(j){
  const p=$('#station-panel'); p.innerHTML='';
  addStationControls(p,'SYS');
  if(!ST.sys) ST.sys = {};
  const row=document.createElement('div'); row.className='row section';
  const bScan=document.createElement('button'); bScan.className='btn'; bScan.textContent='Scan'; bScan.onclick=async function(){ await fetch('/api/command?cmd='+encodeURIComponent('/radar scan')); };
  const bAir=document.createElement('button'); bAir.className='btn'; bAir.textContent='Spawn Near Aircraft'; bAir.onclick=async function(){ await fetch('/radar/force_spawn_near?class=Aircraft&range=2.5'); };
  const bShip=document.createElement('button'); bShip.className='btn'; bShip.textContent='Spawn Near Ship'; bShip.onclick=async function(){ await fetch('/radar/force_spawn_near?class=Ship&range=4'); };
  row.appendChild(bScan); row.appendChild(bAir); row.appendChild(bShip); p.appendChild(row);

  const quitRow=document.createElement('div'); quitRow.className='row section';
  const quitBtn=document.createElement('button'); quitBtn.className='btn danger'; quitBtn.textContent='QUIT GAME';
  const quitMsg=document.createElement('span'); quitMsg.className='sys-msg muted';
  quitBtn.onclick=async function(){
    if(quitBtn.disabled) return;
    quitBtn.disabled = true;
    quitMsg.textContent = 'Shutting down…';
    quitMsg.className = 'sys-msg muted';
    try{
      const res = await fetch('/diag/quit',{method:'POST'});
      let payload = {};
      try{ payload = await res.json(); }catch(_){ payload = {}; }
      if(res.ok && (!payload || payload.ok !== false)){
        quitMsg.textContent = 'Server exiting';
        quitMsg.className = 'sys-msg ok';
      }else{
        const errTxt = payload && payload.error ? String(payload.error) : 'Failed';
        quitMsg.textContent = errTxt;
        quitMsg.className = 'sys-msg err';
      }
    }catch(e){
      quitMsg.textContent = 'Connection lost (exit expected)';
      quitMsg.className = 'sys-msg ok';
    }
  };
  quitRow.appendChild(quitBtn);
  quitRow.appendChild(quitMsg);
  p.appendChild(quitRow);
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
  if(ST.active==='LOG') return renderLOG();
  if(ST.active==='SYS') return renderSYS(j);
}

async function poll(){
  try{
    const r=await fetch('/api/status',{cache:'no-store'});
    const j=await r.json();
    window._status=j;
    render(j);
    trackEvents(j);
  }catch(e){}
}

function playKlik(){ try{ const a=new Audio('/data/sounds/klik.m4a'); a.volume=0.6; a.play().catch(function(){}); }catch(e){} }

// --- Push-To-Talk (PTT) mic capture + browser STT fallback ---
const PTT = { rec: null, chunks: [], stream: null, recording: false, stt: null, transcript: '' };

async function _pttStart(){
  if(PTT.recording) return;
  try{
    const btn = $('#ptt-btn'); if(btn) btn.classList.add('recording');
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    PTT.stream = stream;
    const mt = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '');
    const rec = new MediaRecorder(stream, mt?{ mimeType: mt }:{});
    PTT.chunks = [];
    rec.ondataavailable = function(e){ if(e && e.data && e.data.size>0) PTT.chunks.push(e.data); };
    rec.onstop = async function(){
      try{
        const url = '/radio/voice?speak=1&voice_role=' + encodeURIComponent('Weapons');
        // If browser STT produced a transcript, prefer that (more robust cross-browser)
        const txt = (PTT.transcript||'').trim();
        if(txt){
          await fetch(url, { method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text: txt}) });
        }else{
          const blob = new Blob(PTT.chunks, { type: mt || 'audio/webm' });
          // Upload to interpreter voice chain; speak via Weapons voice; no execution
          const form = new FormData();
          form.append('file', blob, 'ptt.webm');
          await fetch(url, { method: 'POST', body: form });
        }
      }catch(_){ /* swallow */ }
      finally{
        try{ if(PTT.stream){ PTT.stream.getTracks().forEach(t=>t.stop()); } }catch(_){ }
        PTT.stream = null; PTT.rec = null; PTT.chunks = []; PTT.recording = false; PTT.transcript='';
        const btn = $('#ptt-btn'); if(btn) btn.classList.remove('recording');
      }
    };
    PTT.rec = rec; PTT.recording = true; rec.start();
    // Start browser STT if available (fallback when server ASR fails)
    try{
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if(SR){
        const r = new SR();
        r.lang = 'en-GB';
        r.interimResults = false;
        r.maxAlternatives = 1;
        r.onresult = function(ev){ try{ const s = ev.results && ev.results[0] && ev.results[0][0] && ev.results[0][0].transcript; if(s) PTT.transcript = String(s); }catch(_){ } };
        r.onerror = function(){ /* ignore */ };
        r.onend = function(){ /* end of STT */ };
        PTT.stt = r; r.start();
      } else { PTT.stt = null; }
    }catch(_){ PTT.stt = null; }
  }catch(e){
    const btn = $('#ptt-btn'); if(btn) btn.classList.remove('recording');
    // Optional: user feedback could be added here
  }
}

function _pttStop(){
  try{ if(PTT.rec && PTT.recording){ PTT.rec.stop(); } }catch(_){ }
  try{ if(PTT.stt && PTT.recording){ PTT.stt.stop(); } }catch(_){ }
}

function wire(){
  $$('.toolbar .btn').forEach(function(b){ b.addEventListener('click', function(ev){
    playKlik();
    const st = (b && b.dataset) ? b.dataset.st : undefined;
    if(st) setActive(st);
  }); });
  // Global Radio Mute button
  const gbtn = $('#radio-mute-btn');
  function _radioMuteState(){ try{ return localStorage.getItem('MUTE_ALL_RADIO')==='1'; }catch(_){ return false; } }
  function _setRadioMute(on){ try{ if(on) localStorage.setItem('MUTE_ALL_RADIO','1'); else localStorage.removeItem('MUTE_ALL_RADIO'); }catch(_){ } }
  function _updateG(){ const on=_radioMuteState(); if(gbtn){ gbtn.textContent = on? 'RADIO OFF':'RADIO ON'; gbtn.classList.toggle('radio-mute', true); gbtn.classList.toggle('muted', on); } }
  if(gbtn){ _updateG(); gbtn.onclick = function(){ const on=_radioMuteState(); _setRadioMute(!on); _updateG(); }; }
  // Wire PTT button events (press to record, release to send)
  const ptt = $('#ptt-btn');
  if(ptt){
    ptt.addEventListener('mousedown', _pttStart);
    ptt.addEventListener('touchstart', function(e){ e.preventDefault(); _pttStart(); }, { passive:false });
    ['mouseup','mouseleave'].forEach(function(ev){ ptt.addEventListener(ev, _pttStop); });
    ptt.addEventListener('touchend', function(e){ e.preventDefault(); _pttStop(); }, { passive:false });
    ptt.addEventListener('touchcancel', function(e){ e.preventDefault(); _pttStop(); }, { passive:false });
  }
  // Keyboard PTT: hold Spacebar to record; release to send
  function _isTyping(){
    try{
      const ae = document.activeElement;
      if(!ae) return false;
      const tag = (ae.tagName||'').toLowerCase();
      if(tag==='input' || tag==='textarea' || ae.isContentEditable) return true;
    }catch(_){ }
    return false;
  }
  document.addEventListener('keydown', function(e){
    if(e.code==='Space'){
      if(_isTyping()) return;
      if(e.repeat){ e.preventDefault(); return; }
      e.preventDefault();
      _pttStart();
    }
  }, true);
  document.addEventListener('keyup', function(e){
    if(e.code==='Space'){
      if(_isTyping()) return;
      e.preventDefault();
      _pttStop();
    }
  }, true);
  window.addEventListener('blur', _pttStop);
  // Global KLIK on all button presses
  document.addEventListener('click', function(ev){ const t=ev.target; if(t && t.matches && t.matches('button.btn')){ playKlik(); } }, true);
  renderEventConsole();
  setActive('NAV'); poll(); setInterval(poll, 1500);
}

document.addEventListener('DOMContentLoaded', wire);
// Bootstrap marker to help verify the right asset is served
try { window.__stations_loaded = true; window.__stations_loaded_ts = Date.now(); console.log('stations.js loaded', new Date(window.__stations_loaded_ts).toISOString()); } catch (_e) {}
