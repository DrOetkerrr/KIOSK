const $ = (sel)=>document.querySelector(sel);
const $$ = (sel)=>document.querySelectorAll(sel);
const text = (el, s)=>{ if(el) el.textContent = s; };
const fmt = (v, d)=> (v===undefined||v===null||Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d||0);

let ST = {
  active: 'NAV',
  test: false,
  nav: { desiredHeading: '', desiredSpeed: '' },
  wpn: { lockInput: '' },
  events: [],
  eventKeys: { launch: null, result: null, cap: null }
};

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
    if(Array.isArray(j.events) && j.events.length){
      ST.events = j.events.slice(-5).map(function(ev){
        const text = String((ev && ev.text) || '—');
        return {
          kind: ev && ev.id,
          text,
          time: formatEventTs(ev && ev.ts)
        };
      });
      renderEventConsole();
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
        if(String(result.event||'').toLowerCase()==='miss'){
          pushEvent('miss','Enemy bomb missed');
        }else if(String(result.event||'').toLowerCase()==='hit'){
          pushEvent('hit','Target hit');
        }
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

  const hint=document.createElement('div'); hint.className='nav-button-hint'; hint.textContent='(BUTTON)'; p.appendChild(hint);

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
  const p=$('#station-panel'); p.innerHTML='';
  if(!ST.wpn) ST.wpn = { lockInput: '' };

  const contacts = Array.isArray(j.contacts)? j.contacts.slice(): [];
  const lockedId = (j.radar && j.radar.locked_id!==undefined && j.radar.locked_id!==null)? Number(j.radar.locked_id): null;
  const primaryContact = contacts.find(c=>Number(c.id)===lockedId) || null;

  const primaryBox=document.createElement('div'); primaryBox.className='wpn-primary';
  const primaryTitle=document.createElement('div'); primaryTitle.className='wpn-primary-title'; primaryTitle.textContent='Primary Target'; primaryBox.appendChild(primaryTitle);

  const infoTable=document.createElement('table'); infoTable.className='wpn-primary-table';
  const headRow=document.createElement('tr');
  ['#ID','Type','Name','Range','Speed','TTI'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; headRow.appendChild(th); });
  infoTable.appendChild(headRow);
  const dataRow=document.createElement('tr');
  const idCell=document.createElement('td'); idCell.textContent=primaryContact? String(primaryContact.id).padStart(2,'0') : '—'; dataRow.appendChild(idCell);
  const typeCell=document.createElement('td'); typeCell.textContent=primaryContact? String(primaryContact.type || primaryContact.class || '—') : '—'; dataRow.appendChild(typeCell);
  const nameCell=document.createElement('td'); nameCell.textContent=primaryContact? String(primaryContact.name||'—') : '—'; dataRow.appendChild(nameCell);
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
        msg.textContent='FIRED'; msg.className='wpn-msg ok';
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
  const p=$('#station-panel'); p.innerHTML='';
  const fleet = Array.isArray(j.ownfleet)? j.ownfleet : [];
  const flagship = fleet.find(u=>String(u.name||'').toLowerCase().includes('hermes'))
                  || fleet.find(u=>String(u.id||'')!=='own') || null;
  const lockedId = (j.radar && j.radar.locked_id!==undefined && j.radar.locked_id!==null)? Number(j.radar.locked_id): null;
  const primaryContact = (lockedId!=null && Array.isArray(j.contacts))? j.contacts.find(c=>Number(c.id)===lockedId): null;

  const table=document.createElement('table'); table.className='comms-table';
  const head=document.createElement('tr');
  ['Flagship','Grid','Spd','Course'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; head.appendChild(th); });
  table.appendChild(head);
  const data=document.createElement('tr');
  const flagshipName = flagship? String(flagship.name||'—'): '—';
  const flagshipCell = flagship? String(flagship.cell||'—'): '—';
  const flagshipSpd = flagship && flagship.speed!=null? fmt(flagship.speed,0): '—';
  const flagshipCourse = flagship && flagship.heading!=null? fmt(flagship.heading,0): '—';
  [flagshipName, flagshipCell, flagshipSpd, flagshipCourse].forEach(function(val, idx){
    const td=document.createElement('td');
    if(idx>=2) td.className='num';
    td.textContent=val;
    data.appendChild(td);
  });
  table.appendChild(data); p.appendChild(table);

  const actions=document.createElement('div'); actions.className='comms-actions';
  const consoleMsg=document.createElement('div'); consoleMsg.className='comms-msg muted';
  async function hermesCmd(url){
    consoleMsg.textContent='...'; consoleMsg.className='comms-msg muted';
    try{
      const r=await fetch(url); const res=await r.json();
      if(res && res.ok){
        const bearings = [];
        if(res.bearing!==undefined) bearings.push(`brg ${res.bearing}\u00b0`);
        if(res.range_nm!==undefined) bearings.push(`${res.range_nm} nm`);
        if(res.recommend_hdg!==undefined) bearings.push(`rec ${res.recommend_hdg}\u00b0`);
        if(res.standoff_nm!==undefined) bearings.push(`stand-off ${res.standoff_nm} nm`);
        consoleMsg.textContent = bearings.length? bearings.join(' • ') : 'OK';
        consoleMsg.className='comms-msg ok';
      }else{
        consoleMsg.textContent = res && res.error ? String(res.error) : 'ERR';
        consoleMsg.className='comms-msg err';
      }
    }catch(e){
      consoleMsg.textContent='ERR'; consoleMsg.className='comms-msg err';
    }
  }
  const closeBtn=document.createElement('button'); closeBtn.className='btn'; closeBtn.textContent='CLOSE UP';
  closeBtn.onclick=()=>hermesCmd('/nav/hermes/close_in');
  const standBtn=document.createElement('button'); standBtn.className='btn'; standBtn.textContent='MOVE AWAY';
  standBtn.onclick=()=>hermesCmd('/nav/hermes/stand_off');
  actions.appendChild(closeBtn); actions.appendChild(standBtn); p.appendChild(actions); p.appendChild(consoleMsg);

  const capHeader=document.createElement('h3'); capHeader.className='comms-subhead'; capHeader.textContent='SHAR menu:'; p.appendChild(capHeader);
  const cap=j.cap || {};
  const sharSummary=document.createElement('div'); sharSummary.className='shar-summary';
  const readyTag=document.createElement('span'); readyTag.className='shar-ready ' + (cap.ready?'ok':'err'); readyTag.textContent=cap.ready? 'READY':'STANDBY'; sharSummary.appendChild(readyTag);
  const summaryItems=[`pairs: ${cap.pairs ?? 0}`, `airframes: ${cap.airframes ?? 0}`, `cooldown: ${cap.cooldown_s ? fmt(cap.cooldown_s,0)+'s' : '—'}`, `committed: ${cap.committed ?? 0}`];
  summaryItems.forEach(function(txt){ const span=document.createElement('span'); span.className='badge'; span.textContent=txt; sharSummary.appendChild(span); });
  p.appendChild(sharSummary);

  const launchTable=document.createElement('table'); launchTable.className='comms-launch-table';
  const launchHead=document.createElement('tr');
  ['Flight','Callsign / Cell','Grid','Action'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; launchHead.appendChild(th); });
  launchTable.appendChild(launchHead);

  const capRow=document.createElement('tr');
  const capFlight=document.createElement('td'); capFlight.textContent='CAP'; capRow.appendChild(capFlight);
  const capCellTd=document.createElement('td');
  const capCellInput=document.createElement('input'); capCellInput.className='input mono'; capCellInput.placeholder='Cell'; capCellInput.style.width='90px'; capCellTd.appendChild(capCellInput); capRow.appendChild(capCellTd);
  const capGridTd=document.createElement('td'); capGridTd.textContent='—'; capRow.appendChild(capGridTd);
  const capActionTd=document.createElement('td'); capActionTd.className='num';
  const capLaunchBtn=document.createElement('button'); capLaunchBtn.className='btn'; capLaunchBtn.textContent='LAUNCH';
  capActionTd.appendChild(capLaunchBtn); capRow.appendChild(capActionTd);
  launchTable.appendChild(capRow);

  const interceptRow=document.createElement('tr');
  const interceptFlight=document.createElement('td'); interceptFlight.textContent='INTERCEPT'; interceptRow.appendChild(interceptFlight);
  const interceptCall=document.createElement('td'); interceptCall.textContent = primaryContact ? `ID ${String(primaryContact.id).padStart(2,'0')}` : '—'; interceptRow.appendChild(interceptCall);
  const interceptGrid=document.createElement('td'); interceptGrid.textContent = primaryContact? String(primaryContact.cell||'—'):'—'; interceptRow.appendChild(interceptGrid);
  const interceptAction=document.createElement('td'); interceptAction.className='num';
  const interceptBtn=document.createElement('button'); interceptBtn.className='btn'; interceptBtn.textContent='LAUNCH';
  if(!primaryContact) interceptBtn.disabled = true;
  interceptAction.appendChild(interceptBtn); interceptRow.appendChild(interceptAction);
  launchTable.appendChild(interceptRow);
  p.appendChild(launchTable);

  const capMsg=document.createElement('div'); capMsg.className='comms-msg muted'; p.appendChild(capMsg);

  capLaunchBtn.onclick=async function(){
    const cell = (capCellInput.value||'').trim().toUpperCase();
    if(!cell){ capMsg.textContent='Enter CAP grid cell'; capMsg.className='comms-msg err'; return; }
    capMsg.textContent='Requesting CAP...'; capMsg.className='comms-msg muted';
    try{
      const body={cell, station_minutes: 20, radius_nm: 5};
      const res=await fetch('/cap/launch_to',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
      const data=await res.json();
      if(data && data.ok){
        capMsg.textContent=data.message || 'CAP launching';
        capMsg.className='comms-msg ok';
        capCellInput.value='';
        await poll().catch(()=>{});
      }else{
        capMsg.textContent=data && data.message ? String(data.message) : (data && data.error ? String(data.error) : 'CAP request failed');
        capMsg.className='comms-msg err';
      }
    }catch(e){
      capMsg.textContent='CAP request failed'; capMsg.className='comms-msg err';
    }
  };

  interceptBtn.onclick=async function(){
    if(!primaryContact){ capMsg.textContent='No locked target.'; capMsg.className='comms-msg err'; return; }
    capMsg.textContent='Vectoring intercept...'; capMsg.className='comms-msg muted';
    try{
      const res=await fetch('/cap/request',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: primaryContact.id})});
      const data=await res.json();
      if(data && data.ok){
        capMsg.textContent=data.message || 'Intercept pair launching';
        capMsg.className='comms-msg ok';
        await poll().catch(()=>{});
      }else{
        capMsg.textContent=data && data.message ? String(data.message) : (data && data.error ? String(data.error) : 'Intercept request failed');
        capMsg.className='comms-msg err';
      }
    }catch(e){
      capMsg.textContent='Intercept request failed'; capMsg.className='comms-msg err';
    }
  };

  const commitHeader=document.createElement('h3'); commitHeader.className='comms-subhead'; commitHeader.textContent='SHAR commit status:'; p.appendChild(commitHeader);
  const commitTable=document.createElement('table'); commitTable.className='comms-commit-table';
  const commitHead=document.createElement('tr');
  ['Flight','Status','POS','Target','Range','TOT','TOS'].forEach(function(label){ const th=document.createElement('th'); th.textContent=label; commitHead.appendChild(th); });
  commitTable.appendChild(commitHead);
  const tasks = Array.isArray(cap.tasks)? cap.tasks : [];
  function fmtDuration(sec){
    if(sec===undefined || sec===null) return '—';
    const s = Number(sec);
    if(!Number.isFinite(s)) return '—';
    if(s < 60) return `${Math.round(s)}s`;
    if(s < 3600) return `${Math.round(s/60)} min`;
    return `${Math.round(s/3600)} hr`;
  }
  if(!tasks.length){
    const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=7; td.textContent='No active missions'; tr.appendChild(td); commitTable.appendChild(tr);
  }else{
    tasks.forEach(function(t){
      const tr=document.createElement('tr');
      const flightTd=document.createElement('td'); flightTd.textContent = t.n!=null ? `SHAR ${t.n}` : 'SHAR —'; tr.appendChild(flightTd);
      const statusTd=document.createElement('td'); statusTd.textContent=String(t.status||'—'); tr.appendChild(statusTd);
      const posTd=document.createElement('td'); posTd.textContent=String(t.cur_cell||'—'); tr.appendChild(posTd);
      const tgtTd=document.createElement('td'); tgtTd.textContent=String(t.target_cell||'—'); tr.appendChild(tgtTd);
      const rngTd=document.createElement('td'); rngTd.className='num'; rngTd.textContent = t.range_nm!=null? `${fmt(t.range_nm,1)} nm`:'—'; tr.appendChild(rngTd);
      const totTd=document.createElement('td'); totTd.className='num'; totTd.textContent=fmtDuration(t.tot_s); tr.appendChild(totTd);
      const tosTd=document.createElement('td'); tosTd.className='num'; tosTd.textContent=fmtDuration(t.tos_s); tr.appendChild(tosTd);
      commitTable.appendChild(tr);
    });
  }
  p.appendChild(commitTable);
}

function renderENG(j){
  const p=$('#station-panel'); p.innerHTML='';
  const systems=['Hull','Engines','Weapons','Fire Control','Navigation']; const sec=document.createElement('div');
  systems.forEach(function(s){ const r=document.createElement('div'); r.className='row'; const nm=document.createElement('div'); nm.style.minWidth='140px'; nm.textContent=s; const ind=document.createElement('div'); const d=document.createElement('span'); d.className='statdot on'; ind.appendChild(d); r.appendChild(nm); r.appendChild(ind); sec.appendChild(r); });
  p.appendChild(sec);
  const lives=Number(((j.state||{}).lives)||0); const maxl=Number(((j.state||{}).max_lives)||0); const pct = maxl>0 ? Math.round(100*lives/maxl) : 100;
  const info=document.createElement('div'); info.className='row section mono'; const a=document.createElement('span'); a.textContent='Ship State: '+pct+'%'; const b=document.createElement('span'); b.textContent='Repair teams: 0/0'; info.appendChild(a); info.appendChild(b); p.appendChild(info);
}

function renderSYS(j){
  const p=$('#station-panel'); p.innerHTML='';
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
  try{
    const r=await fetch('/api/status',{cache:'no-store'});
    const j=await r.json();
    window._status=j;
    render(j);
    trackEvents(j);
  }catch(e){}
}

function playKlik(){ try{ const a=new Audio('/data/sounds/klik.m4a'); a.volume=0.6; a.play().catch(function(){}); }catch(e){} }

function wire(){
  $$('.toolbar .btn').forEach(function(b){ b.addEventListener('click', function(ev){ playKlik(); setActive(b.dataset.st); }); });
  // Global KLIK on all button presses
  document.addEventListener('click', function(ev){ const t=ev.target; if(t && t.matches && t.matches('button.btn')){ playKlik(); } }, true);
  renderEventConsole();
  setActive('NAV'); poll(); setInterval(poll, 1500);
}

document.addEventListener('DOMContentLoaded', wire);
// Bootstrap marker to help verify the right asset is served
try { window.__stations_loaded = true; window.__stations_loaded_ts = Date.now(); console.log('stations.js loaded', new Date(window.__stations_loaded_ts).toISOString()); } catch (_e) {}
