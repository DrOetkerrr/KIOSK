const $ = (sel)=>document.querySelector(sel);
const $$ = (sel)=>document.querySelectorAll(sel);
const text = (el, s)=>{ if(el) el.textContent = s; };
const fmt = (v, d)=> (v===undefined||v===null||Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d||0);

let appendConsole = null;
try{
  if(typeof window !== 'undefined' && typeof window.appendConsole === 'function'){
    const existing = window.appendConsole;
    appendConsole = function(message){
      try{
        existing.call(window, message);
      }catch(_){
        console.log(String(message));
      }
    };
  }
}catch(_){ appendConsole = null; }
if(!appendConsole){
  appendConsole = function(message){
    try{
      if(typeof window !== 'undefined'){
        const fn = window.appendConsole;
        if(typeof fn === 'function' && fn !== appendConsole){
          fn.call(window, message);
          return;
        }
      }
    }catch(_){ }
    try{
      console.log(String(message));
    }catch(_){ }
  };
  try{
    if(typeof window !== 'undefined' && typeof window.appendConsole !== 'function'){
      window.appendConsole = appendConsole;
    }
  }catch(_){ }
}

try{
  window.__stations_build = '20251012_radar_delta';
  if(!window.__stations_log_once){
    window.__stations_log_once = true;
    console.log('[stations] build', window.__stations_build);
  }
}catch(_){ }

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
  eventKeys: { launch: null, result: null, cap: null, capRecover: null },
  power: {},
  radar: {}
};

const STATION_KEYS = ['NAV','RADAR','WPN','RADIO','ENG'];
const STATION_LABELS = { NAV: 'NAV', RADAR: 'RDR', WPN: 'WPN', RADIO: 'COMMS', ENG: 'ENG' };
const VOICE_DEVICE_STORAGE_KEY = 'voice_device_id';

const POLL_DEFAULT_INTERVAL_MS = 1500;
const POLL_MAX_INTERVAL_MS = 12000;
const POLL_TIMEOUT_MS = 4000;
let pollTimer = null;
let pollAbortController = null;
let pollIntervalMs = POLL_DEFAULT_INTERVAL_MS;
let pollConsecutiveErrors = 0;
let pollLastOkTs = 0;

function ensureConnectionBanner(){
  let banner = document.getElementById('connection-banner');
  if(!banner){
    banner = document.createElement('div');
    banner.id = 'connection-banner';
    banner.className = 'connection-banner hidden';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    banner.innerHTML = '<span class="connection-dot">●</span><span class="connection-text">Connecting…</span>';
    document.body.appendChild(banner);
  }
  return banner;
}

function setConnectionBanner(state, message){
  const banner = ensureConnectionBanner();
  if(state === 'ok'){
    banner.classList.add('hidden');
    banner.classList.remove('warn', 'error');
    banner.setAttribute('aria-hidden', 'true');
    return;
  }
  banner.classList.remove('hidden');
  banner.classList.toggle('warn', state === 'warn');
  banner.classList.toggle('error', state === 'error');
  banner.setAttribute('aria-hidden', 'false');
  const dot = banner.querySelector('.connection-dot');
  const textNode = banner.querySelector('.connection-text');
  if(dot){
    dot.textContent = state === 'error' ? '⨂' : state === 'warn' ? '◐' : '●';
  }
  if(textNode){
    textNode.textContent = message || (state === 'warn' ? 'Connection hiccup — retrying…' : 'Connection lost — retrying…');
  }
}

function scheduleNextPoll(delay){
  if(pollTimer){
    clearTimeout(pollTimer);
  }
  pollTimer = setTimeout(runPoll, delay);
}

async function runPoll(){
  const headers = {};
  if(window.__stations_build){
    headers['X-Stations-Build'] = String(window.__stations_build);
  }
  pollAbortController = new AbortController();
  const timeoutId = setTimeout(() => {
    try{ pollAbortController.abort(); }catch(_){}
  }, POLL_TIMEOUT_MS);
  try{
    const response = await fetch('/api/status', {
      cache: 'no-store',
      signal: pollAbortController.signal,
      headers
    });
    if(!response.ok){
      throw new Error(`status ${response.status}`);
    }
    const data = await response.json();
    window._status = data;
    try{
      render(data);
      trackEvents(data);
    }catch(err){
      console.error('[stations] render error', err);
    }
    pollConsecutiveErrors = 0;
    pollIntervalMs = POLL_DEFAULT_INTERVAL_MS;
    pollLastOkTs = Date.now();
    setConnectionBanner('ok');
  }catch(err){
    const isAbort = err && (err.name === 'AbortError' || err === 'AbortError');
    if(isAbort){
      pollConsecutiveErrors = 0;
      pollIntervalMs = POLL_DEFAULT_INTERVAL_MS;
      return;
    }
    pollConsecutiveErrors += 1;
    pollIntervalMs = Math.min(
      POLL_DEFAULT_INTERVAL_MS * Math.pow(1.6, pollConsecutiveErrors),
      POLL_MAX_INTERVAL_MS
    );
    const sinceOk = pollLastOkTs ? Math.round((Date.now() - pollLastOkTs) / 1000) : null;
    const msg = sinceOk !== null && sinceOk > 5
      ? `Connection lost ${sinceOk}s ago — retrying…`
      : 'Connection hiccup — retrying…';
    setConnectionBanner(pollConsecutiveErrors > 2 ? 'error' : 'warn', msg);
    console.warn('[stations] poll error', err);
  }finally{
    clearTimeout(timeoutId);
    pollAbortController = null;
    scheduleNextPoll(pollIntervalMs);
  }
}

function startPolling(){
  pollConsecutiveErrors = 0;
  pollIntervalMs = POLL_DEFAULT_INTERVAL_MS;
  pollLastOkTs = Date.now();
  scheduleNextPoll(0);
}

async function forceRefreshStatus(){
  if(pollTimer){
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  if(pollAbortController){
    try{ pollAbortController.abort(); }catch(_){ }
    pollAbortController = null;
  }
  pollIntervalMs = POLL_DEFAULT_INTERVAL_MS;
  await runPoll();
}

function stationLabel(key){
  return STATION_LABELS[key] || key;
}
const POWER_STORAGE_KEY = 'station_power';

function loadStationPower(){
  if(!ST.power || typeof ST.power !== 'object') ST.power = {};
  let saved = {};
  try{
    const raw = localStorage.getItem(POWER_STORAGE_KEY);
    if(raw){
      const parsed = JSON.parse(raw);
      if(parsed && typeof parsed === 'object' && !Array.isArray(parsed)) saved = parsed;
    }
  }catch(_){ saved = {}; }
  STATION_KEYS.forEach(function(key){
    if(typeof saved[key] === 'boolean') ST.power[key] = saved[key];
    else if(typeof ST.power[key] !== 'boolean') ST.power[key] = true;
  });
  saveStationPower();
}

function saveStationPower(){
  try{ localStorage.setItem(POWER_STORAGE_KEY, JSON.stringify(ST.power)); }catch(_){ }
}

function isStationPowered(key){
  if(!ST.power || typeof ST.power !== 'object') return true;
  if(!Object.prototype.hasOwnProperty.call(ST.power, key)) return true;
  return ST.power[key] !== false;
}

function setStationPower(key, enabled){
  if(!ST.power || typeof ST.power !== 'object') ST.power = {};
  ST.power[key] = !!enabled;
  saveStationPower();
  if(!enabled && Voice.activeStation === key){
    Voice.stop();
  }
  renderStationSwitches();
  updateToolbarPowerClasses();
  if((ST.active || 'NAV') === key){
    render(window._status || {});
  }
}

const Voice = {
  supported: false,
  recognition: null,
  activeStation: null,
  listening: false,
  mode: 'webspeech',
  processing: false,
  deviceId: null,
  deviceMeta: null,
  devices: [],
  deviceSelect: null,
  stream: null,
  _enumerating: false,
  _hasDevicePermission: false,
  recorder: null,
  recorderMime: 'audio/webm',
  recordChunks: [],
  recordStopTimer: null,
  pendingStation: null,
  init(){
    this.mode = 'webspeech';
    this.processing = false;
    this.recorder = null;
    this.recordChunks = [];
    this.pendingStation = null;
    this._loadStoredDevice();
    if(navigator.mediaDevices && navigator.mediaDevices.enumerateDevices){
      const media = navigator.mediaDevices;
      try{
        media.addEventListener('devicechange', () => { this._refreshDeviceList(); });
      }catch(_){
        try{ media.ondevicechange = () => { this._refreshDeviceList(); }; }catch(__){}
      }
      this._refreshDeviceList();
    }
    this.supported = false;
    try{
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if(SR){
        this.recognition = new SR();
        this.recognition.lang = 'en-US';
        this.recognition.interimResults = false;
        this.recognition.maxAlternatives = 1;
        this.recognition.onresult = (ev) => {
          if(!this.listening) return;
          try{
            const res = ev.results && ev.results[0] && ev.results[0][0];
            const transcript = res ? String(res.transcript || '').trim() : '';
            if(transcript){
              pushEvent('voice', `Heard: ${transcript}`);
              this.handleTranscript(transcript);
            }
          }catch(err){ console.warn('[voice] result error', err); }
        };
        this.recognition.onerror = (ev) => {
          console.warn('[voice] error', ev && ev.error);
          pushEvent('voice', `Voice error: ${(ev && ev.error) || 'unknown'}`);
          this.stop();
        };
        this.recognition.onend = () => {
          if(this.listening){
            try{ this.recognition.start(); }
            catch(err){ console.warn('[voice] restart failed', err); this.stop(); }
          }
        };
        this.mode = 'webspeech';
        this.supported = true;
      }
    }catch(err){
      console.warn('[voice] init failed', err);
      this.supported = false;
    }
    if(!this.supported){
      if(typeof MediaRecorder !== 'undefined' && navigator.mediaDevices && navigator.mediaDevices.getUserMedia){
        this.mode = 'recorder';
        this.supported = true;
      }
    }
    this.updateButtons();
  },
  _loadStoredDevice(){
    try{
      const saved = localStorage.getItem(VOICE_DEVICE_STORAGE_KEY);
      if(!saved){
        this.deviceId = null;
        this.deviceMeta = null;
        return;
      }
      let meta = null;
      try{
        meta = JSON.parse(saved);
      }catch(_parseErr){
        meta = saved && typeof saved === 'string' ? { id: saved } : null;
      }
      if(meta && typeof meta === 'object'){
        const id = typeof meta.id === 'string' && meta.id.trim() ? String(meta.id) : null;
        const label = typeof meta.label === 'string' ? meta.label : null;
        const groupId = typeof meta.groupId === 'string' && meta.groupId.trim() ? meta.groupId : null;
        this.deviceId = id;
        this.deviceMeta = id || label || groupId ? { id, label, groupId } : null;
      }else{
        this.deviceId = null;
        this.deviceMeta = null;
      }
    }catch(_){
      this.deviceId = this.deviceId || null;
      if(!this.deviceMeta && this.deviceId){
        this.deviceMeta = { id: this.deviceId };
      }
    }
  },
  _storeDevicePref(meta){
    try{
      if(meta && (meta.id || meta.label || meta.groupId)){
        const payload = {};
        if(meta.id){ payload.id = String(meta.id); }
        if(meta.label){ payload.label = String(meta.label); }
        if(meta.groupId){ payload.groupId = String(meta.groupId); }
        localStorage.setItem(VOICE_DEVICE_STORAGE_KEY, JSON.stringify(payload));
      }else{
        localStorage.removeItem(VOICE_DEVICE_STORAGE_KEY);
      }
    }catch(_){ }
  },
  async _refreshDeviceList(){
    if(this._enumerating) return;
    if(!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    this._enumerating = true;
    try{
      const list = await navigator.mediaDevices.enumerateDevices();
      const inputs = Array.isArray(list) ? list.filter((d)=>d && d.kind === 'audioinput') : [];
      this.devices = inputs;
      const pref = this._resolvePreferredDevice(inputs);
      this.deviceId = pref.id;
      this.deviceMeta = pref;
      this._storeDevicePref(pref);
      this._syncDeviceSelect();
    }catch(err){
      console.warn('[voice] enumerate devices failed', err);
    }finally{
      this._enumerating = false;
      this.updateButtons();
    }
  },
  _deviceLabel(device, index){
    if(!device) return `Mic ${index+1}`;
    if(device.label){ return device.label; }
    if(device.deviceId === 'default'){ return 'System default'; }
    if(device.deviceId === 'communications'){ return 'Communications mic'; }
    return `Mic ${index+1}`;
  },
  _resolvePreferredDevice(list){
    const inputs = Array.isArray(list) ? list.filter((d)=>!!d && d.kind === 'audioinput') : [];
    if(!inputs.length){
      return { id: null, label: null, groupId: null };
    }
    const matchById = (id)=> inputs.find((d)=>d && d.deviceId === id) || null;
    if(this.deviceId){
      const exact = matchById(this.deviceId);
      if(exact){
        return {
          id: exact.deviceId || null,
          label: exact.label || null,
          groupId: exact.groupId || null
        };
      }
    }
    const storedId = this.deviceMeta && this.deviceMeta.id ? String(this.deviceMeta.id) : null;
    if(storedId){
      const exactStored = matchById(storedId);
      if(exactStored){
        return {
          id: exactStored.deviceId || null,
          label: exactStored.label || null,
          groupId: exactStored.groupId || null
        };
      }
    }
    const storedGroup = this.deviceMeta && this.deviceMeta.groupId ? String(this.deviceMeta.groupId) : null;
    if(storedGroup){
      const grp = inputs.find((d)=> d && d.groupId && String(d.groupId) === storedGroup);
      if(grp){
        return {
          id: grp.deviceId || null,
          label: grp.label || null,
          groupId: grp.groupId || null
        };
      }
    }
    const storedLabel = this.deviceMeta && this.deviceMeta.label ? String(this.deviceMeta.label).trim().toLowerCase() : null;
    if(storedLabel){
      const lbl = inputs.find((d)=> (d.label || '').trim().toLowerCase() === storedLabel);
      if(lbl){
        return {
          id: lbl.deviceId || null,
          label: lbl.label || null,
          groupId: lbl.groupId || null
        };
      }
    }
    const preferred = inputs.find((d)=> d && d.deviceId && d.deviceId !== 'communications') || inputs[0];
    if(preferred){
      return {
        id: preferred.deviceId || null,
        label: preferred.label || null,
        groupId: preferred.groupId || null
      };
    }
    return { id: null, label: null, groupId: null };
  },
  _syncDeviceSelect(){
    const select = this.deviceSelect;
    if(!select) return;
    while(select.firstChild){ select.removeChild(select.firstChild); }
    if(!this.devices.length){
      const opt = document.createElement('option');
      opt.value = '';
      const canRequest = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
      opt.textContent = canRequest ? 'Allow microphone access…' : 'No microphone available';
      select.appendChild(opt);
      select.disabled = true;
      return;
    }
    this.devices.forEach((dev, idx)=>{
      const opt = document.createElement('option');
      opt.value = dev.deviceId || '';
      opt.textContent = this._deviceLabel(dev, idx);
      select.appendChild(opt);
    });
    let wanted = null;
    if(this.deviceId && this.devices.some((d)=>d.deviceId === this.deviceId)){
      wanted = this.deviceId;
    }else if(this.devices.length){
      const pref = this._resolvePreferredDevice(this.devices);
      if(pref && (pref.id || pref.label || pref.groupId)){
        if(pref.id !== this.deviceId || !this.deviceMeta || pref.groupId !== this.deviceMeta.groupId || pref.label !== this.deviceMeta.label){
          this.deviceId = pref.id;
          this.deviceMeta = pref;
          this._storeDevicePref(pref);
        }
        wanted = pref.id || null;
      }
      if(!wanted && this.devices[0]){
        wanted = this.devices[0].deviceId || '';
      }
    }
    if(wanted){ select.value = wanted; }
    else { select.selectedIndex = 0; }
    select.disabled = false;
  },
  attachDeviceSelect(select){
    this.deviceSelect = select;
    this._syncDeviceSelect();
  },
  setDevice(id){
    const newId = id ? String(id) : '';
    if(newId === (this.deviceId || '')) return;
    const wasListening = this.listening;
    const station = this.activeStation;
    if(wasListening && station){
      this.stop();
    }
    this.deviceId = newId || null;
    if(this.deviceId){
      const match = this.devices.find((d)=>d && d.deviceId === this.deviceId);
      if(match){
        this.deviceMeta = {
          id: match.deviceId || null,
          label: match.label || null,
          groupId: match.groupId || null
        };
      }else{
        this.deviceMeta = { id: this.deviceId };
      }
    }else{
      this.deviceMeta = null;
    }
    this._storeDevicePref(this.deviceMeta);
    this._syncDeviceSelect();
    if(wasListening && station){
      this.start(station).catch((err)=>{ console.warn('[voice] restart failed after device change', err); });
    }
  },
  async _openStream(){
    if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return null;
    this.releaseStream();
    const constraint = this.deviceId ? { deviceId: { exact: this.deviceId } } : true;
    try{
      const stream = await navigator.mediaDevices.getUserMedia({ audio: constraint, video: false });
      this.stream = stream;
      this._hasDevicePermission = true;
      this._refreshDeviceList();
      return stream;
    }catch(err){
      console.warn('[voice] preferred mic failed', err);
      if(this.deviceId){
        try{
          const fallback = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
          this.stream = fallback;
          this._hasDevicePermission = true;
          this.deviceId = null;
          this.deviceMeta = null;
          this._storeDevicePref(null);
          pushEvent('voice', 'Falling back to system microphone');
          this._refreshDeviceList();
          return fallback;
        }catch(ex){
          console.warn('[voice] fallback mic failed', ex);
        }
      }
      throw err;
    }
  },
  releaseStream(){
    if(!this.stream) return;
    try{
      this.stream.getTracks().forEach((track)=>{ try{ track.stop(); }catch(_){ } });
    }catch(_){ }
    this.stream = null;
  },
  toggle(station){
    if(!this.supported){
      pushEvent('voice', 'Voice commands not supported in this browser');
      return;
    }
    if(this.processing){
      pushEvent('voice', 'Voice command processing – please wait');
      return;
    }
    if(!isStationPowered(station)){
      pushEvent('voice', `${stationLabel(station)} is powered off`);
      return;
    }
    if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
      pushEvent('voice', 'Microphone access unavailable');
      return;
    }
    if(this.listening && this.activeStation === station){
      this.stop();
    }else{
      this.start(station).catch((err)=>{
        console.warn('[voice] start error', err);
        pushEvent('voice', 'Unable to start voice mode');
      });
    }
  },
  async start(station){
    if(!this.supported || this.processing) return;
    if(this.listening && this.activeStation === station) return;
    if(this.listening){
      this.stop();
    }
    this.activeStation = station;
    this.pendingStation = station;
    try{
      await this._openStream();
    }catch(err){
      console.warn('[voice] microphone unavailable', err);
      this.activeStation = null;
      this.pendingStation = null;
      this.updateButtons();
      throw err;
    }
    try{
      if(this.mode === 'recorder'){
        this._startRecorder();
      }else if(this.recognition){
        this.recognition.start();
      }else{
        throw new Error('Voice mode unavailable');
      }
    }catch(err){
      if(this.mode !== 'recorder'){
        this.releaseStream();
      }
      this.activeStation = null;
      this.pendingStation = null;
      this.listening = false;
      console.warn('[voice] start failed', err);
      throw err;
    }
    this.listening = true;
    const suffix = this.mode === 'recorder' ? ' (auto-stop after 8s)' : '';
    pushEvent('voice', `Listening on ${stationLabel(station)}${suffix}`);
    this.updateButtons();
  },
  stop(){
    const station = this.activeStation || this.pendingStation;
    if(this.listening){
      if(this.mode === 'recorder'){
        this.processing = true;
        this._stopRecorder();
      }else if(this.recognition){
        try{ this.recognition.abort(); }catch(_){ }
      }
      pushEvent('voice', 'Voice listening stopped');
    }
    this.listening = false;
    this.activeStation = null;
    if(this.mode !== 'recorder'){
      this.releaseStream();
    }
    this.pendingStation = station;
    this.updateButtons();
  },
  updateButtons(){
    const hasMic = Array.isArray(this.devices) && this.devices.length > 0;
    const canRequest = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    $$('.voice-btn').forEach((btn)=>{
      const st = btn.dataset ? btn.dataset.station : null;
      const powered = st ? isStationPowered(st) : true;
      const listening = powered && this.listening && this.activeStation === st;
      btn.classList.toggle('listening', listening);
      if(!this.supported){
        btn.textContent = 'VOICE N/A';
        btn.disabled = true;
        btn.classList.remove('listening');
      }else if(!powered){
        btn.textContent = 'VOICE OFF';
        btn.disabled = true;
        btn.classList.remove('listening');
      }else if(!hasMic && !canRequest){
        btn.textContent = 'MIC N/A';
        btn.disabled = true;
        btn.classList.remove('listening');
      }else{
        btn.disabled = false;
        btn.textContent = listening ? 'STOP VOICE' : 'VOICE MODE';
      }
    });
  },
  handleTranscript(text){
    const cleaned = String(text || '').trim().toLowerCase();
    const normalized = cleaned.replace(/[?!.,]+$/g, '').replace(/\s+/g, ' ').trim();
    if(!normalized) return;
    if(['stop listening','cancel listening','end voice'].includes(normalized)){
      this.stop();
      return;
    }
    const station = this.activeStation;
    if(!station){
      pushEvent('voice', 'No station active for voice command');
      return;
    }
    const handlers = VOICE_COMMANDS[station] || [];
    for(const entry of handlers){
      const match = normalized.match(entry.pattern);
      if(match){
        try{
          entry.action(match);
        }catch(err){
          console.warn('[voice] handler error', err);
          pushEvent('voice', 'Command failed');
        }
        return;
      }
    }
    pushEvent('voice', `Unrecognized command: ${text}`);
  }
};

async function voiceFetch(url, body){
  try{
    const resp = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const data = await resp.json().catch(()=>({}));
    if(resp.ok && data && data.ok !== false){
      return { ok: true, data };
    }
    return { ok: false, data };
  }catch(err){
    console.warn('[voice] fetch error', err);
    return { ok: false, data: null };
  }
}

async function voiceNavUpdate(field, value){
  const payload = {};
  if(field === 'heading'){
    let hdg = Number(value);
    if(!Number.isFinite(hdg)) return pushEvent('voice', 'Invalid heading');
    while(hdg < 0) hdg += 360;
    hdg = hdg % 360;
    payload.heading = hdg;
  }else if(field === 'speed'){
    let spd = Number(value);
    if(!Number.isFinite(spd)) return pushEvent('voice', 'Invalid speed');
    spd = Math.max(0, Math.min(35, spd));
    payload.speed = spd;
  }
  const res = await voiceFetch('/api/nav/set', payload);
  pushEvent('voice', res.ok ? `NAV set ${field} ${payload[field]}` : 'Failed to send NAV order');
}

const VOICE_WEAPON_MAP = {
  'sea dart': 'Sea Dart SAM',
  'dart': 'Sea Dart SAM',
  'exocet': 'MM38 Exocet',
  'missile': 'MM38 Exocet',
  'main gun': '4.5 inch Mk.8 gun',
  'gun': '4.5 inch Mk.8 gun',
  'oerlikon': '20mm Oerlikon',
  'gam': '20mm GAM-BO1',
  'gam bo1': '20mm GAM-BO1',
  'chaff': 'Corvus chaff',
};

function voiceResolveWeapon(name){
  const key = String(name || '').trim().toLowerCase();
  if(VOICE_WEAPON_MAP[key]) return VOICE_WEAPON_MAP[key];
  for(const alias in VOICE_WEAPON_MAP){
    if(key.includes(alias)) return VOICE_WEAPON_MAP[alias];
  }
  return name;
}

async function voiceArmWeapon(spoken, armed){
  const name = voiceResolveWeapon(spoken);
  const res = await voiceFetch('/weapons/arm', { name, state: armed ? 'Armed' : 'Safe' });
  pushEvent('voice', res.ok ? `${armed ? 'Armed' : 'Safed'} ${name}` : `Failed to ${armed ? 'arm' : 'safe'} ${name}`);
}

async function voiceFireWeapon(spoken){
  const name = voiceResolveWeapon(spoken);
  const res = await voiceFetch('/weapons/fire', { name, mode: 'real' });
  pushEvent('voice', res.ok ? `Firing ${name}` : `Failed to fire ${name}`);
}

async function voiceAuthorizeMission(id, authorize){
  const res = await voiceFetch('/cap/authorize', { id, authorize });
  pushEvent('voice', res.ok ? `${authorize ? 'Authorized' : 'Held'} mission ${id}` : `Failed to update mission ${id}`);
}

const VOICE_COMMANDS = {
  NAV: [
    { pattern: /^(?:set\s+(?:course|heading)|course)\s*(?:to\s*)?(\d{1,3})(?:\s*(?:deg|degree|degrees))?$/, action: (m)=>voiceNavUpdate('heading', parseInt(m[1], 10)) },
    { pattern: /^(?:set\s+)?speed\s*(?:to\s*)?(\d{1,3})(?:\s*(?:knots?|kts?))?$/, action: (m)=>voiceNavUpdate('speed', parseInt(m[1], 10)) },
  ],
  WPN: [
    { pattern: /^(arm) (.+)$/, action: (m)=>voiceArmWeapon(m[2], true) },
    { pattern: /^(safe) (.+)$/, action: (m)=>voiceArmWeapon(m[2], false) },
    { pattern: /^(fire|launch|shoot) (.+)$/, action: (m)=>voiceFireWeapon(m[2]) },
  ],
  RADIO: [
    { pattern: /^(authorize|engage) mission (\d{1,3})$/, action: (m)=>voiceAuthorizeMission(parseInt(m[2], 10), true) },
    { pattern: /^(hold|cancel) mission (\d{1,3})$/, action: (m)=>voiceAuthorizeMission(parseInt(m[2], 10), false) },
  ],
};

const STATION_CONFIG = {
  NAV: { channel: 1, voice: 'Navigation' },
  RADAR: { channel: 2, voice: 'Radar' },
  WPN: { channel: 3, voice: 'Weapons' },
  RADIO: { channel: 4, voice: 'Pilot' },
  ENG: { channel: 5, voice: 'Engineering' },
  LOG: { channel: 4, voice: 'Bridge' },
  SYS: { channel: 4, voice: 'Bridge' }
};

try{
  loadStationPower();
}catch(_){
  STATION_KEYS.forEach(function(key){ if(typeof ST.power[key] !== 'boolean') ST.power[key] = true; });
}

function stationInfo(key){
  const cfg = STATION_CONFIG[key];
  return cfg ? { channel: cfg.channel, voice: cfg.voice } : { channel: 4, voice: 'Bridge' };
}

function updateStationGlobals(id){
  const cfg = stationInfo(id);
  try{
    window.__activeStation = id;
    window.__activeChannel = cfg.channel;
    window.__activeVoiceRole = cfg.voice;
    window.dispatchEvent(new CustomEvent('station:changed', { detail: { station: id, channel: cfg.channel, voice: cfg.voice } }));
  }catch(_){ }
}

updateStationGlobals(ST.active);

function setActive(id){
  const key = STATION_CONFIG[id] ? id : 'NAV';
  if(Voice.listening && Voice.activeStation && Voice.activeStation !== key){
    Voice.stop();
  }
  ST.active = key;
  updateStationGlobals(ST.active);
  $$('.toolbar .btn').forEach(b=> b.classList.toggle('active', b.dataset.st===ST.active));
  updateToolbarPowerClasses();
  renderStationSwitches();
  render(window._status||{});
}

function updateToolbarPowerClasses(){
  $$('.toolbar .btn').forEach(function(btn){
    const key = btn.dataset ? btn.dataset.st : null;
    if(!key) return;
    btn.classList.toggle('powered-off', !isStationPowered(key));
  });
}

function renderStationSwitches(){
  const bar = $('#station-switches');
  if(!bar) return;
  bar.innerHTML='';
  STATION_KEYS.forEach(function(key){
    const wrap=document.createElement('div'); wrap.className='station-switch';
    if(ST.active === key) wrap.classList.add('active');
    const label=document.createElement('div'); label.className='station-switch-label'; label.textContent=stationLabel(key);
    wrap.appendChild(label);
    const btn=document.createElement('button'); btn.className='btn station-toggle-btn';
    const powered = isStationPowered(key);
    if(!powered) btn.classList.add('off');
    btn.textContent = powered ? 'TURN OFF' : 'TURN ON';
    btn.onclick = function(){ setStationPower(key, !isStationPowered(key)); };
    wrap.appendChild(btn);
    bar.appendChild(wrap);
  });
  updateToolbarPowerClasses();
}

function _normalizeHeading(val){
  let hdg = Number(val);
  if(!Number.isFinite(hdg)) return null;
  hdg = Math.round(hdg);
  while(hdg < 0) hdg += 360;
  hdg = hdg % 360;
  return hdg;
}

function _planLooksValid(plan){
  if(!plan || typeof plan !== 'object' || Array.isArray(plan)) return false;
  try{
    if(Object.prototype.hasOwnProperty.call(plan, 'actions')){
      const actions = plan.actions;
      if(!Array.isArray(actions)) return false;
      for(const a of actions){
        if(!a || typeof a !== 'object' || Array.isArray(a)) return false;
      }
    }
  }catch(_){ return false; }
  return true;
}

async function _postJSON(url, body){
  try{
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    let data = null;
    try{ data = await resp.json(); }catch(_){ data = null; }
    const ok = resp.ok && data && data.ok !== false;
    return { ok, data, status: resp.status };
  }catch(err){
    console.warn('[voice] postJSON error', err);
    return { ok: false, data: null, status: 0 };
  }
}

async function _runNavOrder(order){
  const payload = {};
  if(order.heading !== undefined && order.heading !== null){
    const hdg = _normalizeHeading(order.heading);
    if(hdg !== null) payload.heading = hdg;
  }
  if(order.speed !== undefined && order.speed !== null){
    const spd = Number(order.speed);
    if(Number.isFinite(spd) && spd >= 0){
      payload.speed = Math.round(spd);
    }
  }
  if(!Object.keys(payload).length) return false;
  const res = await _postJSON('/api/nav/set', payload);
  if(!res.ok){
    console.warn('[voice] nav order rejected', res.data && res.data.error);
    return false;
  }
  try{
    await forceRefreshStatus();
  }catch(_){ }
  return true;
}

async function handleLocalVoiceCommand(text, context){
  const raw = (text || '').trim();
  if(!raw) return false;
  const lower = raw.toLowerCase();
  const station = (context && context.station) || ST.active || 'NAV';
  const navContext = station === 'NAV' || lower.startsWith('nav') || lower.startsWith('navigation');
  try{
    appendConsole(`[voice] heard: ${raw}`);
  }catch(_){
    try{
      if(window.console){ console.log('[voice] heard:', raw); }
    }catch(_ignore){}
  }
  if(navContext){
    const order = {};
    const headingMatch = lower.match(/(?:heading|course|bearing)[^0-9]*([0-9]{1,3})/);
    if(headingMatch){ order.heading = Number(headingMatch[1]); }
    if(order.heading === undefined){
      const turnMatch = lower.match(/(?:turn|steer)[^0-9]*([0-9]{1,3})/);
      if(turnMatch) order.heading = Number(turnMatch[1]);
    }
    const speedMatch = lower.match(/speed[^0-9]*([0-9]{1,3})/);
    if(speedMatch){ order.speed = Number(speedMatch[1]); }
    if(Object.keys(order).length){
      const ok = await _runNavOrder(order);
      if(ok) return true;
    }
  }
  return false;
}

async function executeVoiceEndpoint(url, body){
  try{
    if(body && typeof body === 'object' && !Array.isArray(body) && body.plan){
      const plan = body.plan;
      if(!_planLooksValid(plan)){
        console.warn('[voice] skipping exec, invalid plan payload', plan);
        return false;
      }
    }
    if(body && typeof body === 'object' && !Array.isArray(body) && body.actions){
      const actions = body.actions;
      if(!Array.isArray(actions) || actions.some(a=>!a || typeof a !== 'object' || Array.isArray(a))){
        console.warn('[voice] skipping exec, invalid actions payload', actions);
        return false;
      }
    }
  }catch(err){ console.warn('[voice] exec body inspect failed', err); }
  const res = await _postJSON(url, body);
  if(!res.ok){
    console.warn('[voice] exec endpoint failed', url, res.data && res.data.error);
    return false;
  }
  return true;
}

async function handleVoiceResponse(resp, meta){
  if(!resp) return { success: false, payload: null };
  let payload = null;
  try{
    payload = await resp.json();
  }catch(err){
    console.warn('[voice] invalid /radio/voice response', err);
    return { success: false, payload: null };
  }
  if(!resp.ok || !payload || payload.ok === false){
    console.warn('[voice] /radio/voice error', payload && payload.error, resp.status);
    return { success: false, payload };
  }
  const voiceRole = (meta && meta.voiceRole) || 'Bridge';
  const station = (meta && meta.station) || ST.active || 'NAV';
  const chanDefault = STATION_CONFIG[station] ? STATION_CONFIG[station].channel : 4;
  const channel = (meta && meta.channel != null) ? meta.channel : chanDefault;

  let executed = false;
  try{
    const affirm = payload.affirm;
    if(affirm && affirm.endpoint){
      const params = new URLSearchParams();
      params.set('speak','1');
      params.set('voice_role', voiceRole);
      const execUrl = `${affirm.endpoint}?${params.toString()}`;
      const execBody = Object.assign({}, affirm.body || {}, {
        confirm: true,
        speak: true,
        voice_role: voiceRole,
        station,
        channel
      });
      if(execBody.confirm !== true) execBody.confirm = true;
      if(execBody.plan && !_planLooksValid(execBody.plan)){
        console.warn('[voice] affirm plan invalid', execBody.plan);
        executed = false;
      }else if(execBody.actions && (!Array.isArray(execBody.actions) || execBody.actions.some(a=>!a || typeof a !== 'object' || Array.isArray(a)))){
        console.warn('[voice] affirm actions invalid', execBody.actions);
        executed = false;
      }else{
        executed = await executeVoiceEndpoint(execUrl, execBody);
      }
    }else{
      const ai = payload.ai || {};
      const parsed = ai.parsed;
      const validation = ai.validation;
      const parsedIsObject = parsed && typeof parsed === 'object' && !Array.isArray(parsed);
      if(parsedIsObject && _planLooksValid(parsed) && (!validation || validation.ok !== false)){
        const execParams = new URLSearchParams();
        execParams.set('speak','1');
        execParams.set('voice_role', voiceRole);
        const execUrl = '/radio/exec?' + execParams.toString();
        const execBody = {
          plan: parsed,
          confirm: true,
          speak: true,
          voice_role: voiceRole,
          station,
          channel
        };
        executed = await executeVoiceEndpoint(execUrl, execBody);
      }
    }
  }catch(err){
    console.warn('[voice] execution error', err);
    executed = false;
  }

  return { success: executed, payload };
}

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
    if(!ST.eventKeys) ST.eventKeys = { launch: null, result: null, cap: null, capRecover: null };
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
    const capRecovery = audio.cap_recovery;
    if(capRecovery && capRecovery.ts){
      const key = 'capRecover:'+String(capRecovery.ts);
      if(ST.eventKeys.capRecover !== key){
        ST.eventKeys.capRecover = key;
        pushEvent('cap','Aircraft recovered (CAP)');
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

  const missionData = (j && j.mission && typeof j.mission === 'object' && Object.keys(j.mission).length) ? j.mission : null;
  const missionSettings = currentMissionSettings(j) || {};
  const missionId = missionData && missionData.id ? String(missionData.id) : '';
  const missionSequence = (missionData && missionData.sequence && typeof missionData.sequence === 'object') ? missionData.sequence : null;

  if(!ST._missionPowerApplied || typeof ST._missionPowerApplied !== 'object') ST._missionPowerApplied = {};
  if(ST._missionActiveId !== missionId){
    ST._missionActiveId = missionId || '';
    ST._missionPowerApplied = {};
  }
  if(missionId){
    applyMissionPowerPreset(missionId, missionSettings);
  }

  function fmtDuration(sec){
    const n = Number(sec);
    if(!Number.isFinite(n) || n < 0) return '—';
    if(n < 60) return `${Math.round(n)}s`;
    if(n < 3600){
      const mins = Math.floor(n / 60);
      const secs = Math.round(n % 60);
      return secs ? `${mins}m ${secs}s` : `${mins}m`;
    }
    const hrs = Math.floor(n / 3600);
    const mins = Math.round((n % 3600) / 60);
    return mins ? `${hrs}h ${mins}m` : `${hrs}h`;
  }

  function titleCase(str){
    return String(str || '')
      .replace(/_/g, ' ')
      .split(' ')
      .filter(Boolean)
      .map(function(part){ return part.charAt(0).toUpperCase() + part.slice(1); })
      .join(' ');
  }

  function cleanText(val){
    if(val === null || val === undefined) return '';
    if(typeof val === 'string') return val.trim();
    if(typeof val === 'number') return String(val);
    return '';
  }

  const waveInfo = (j && j.wave && typeof j.wave === 'object') ? j.wave : null;
  const waveLabel = cleanText(waveInfo && waveInfo.label);
  const waveIndex = (waveInfo && typeof waveInfo.index === 'number') ? waveInfo.index : null;
  const waveCount = (waveInfo && typeof waveInfo.count === 'number') ? waveInfo.count : null;
  const waveDirection = cleanText(waveInfo && waveInfo.direction);

  function statusClassFor(key){
    switch(key){
      case 'in_progress':
      case 'active':
        return 'active';
      case 'complete':
      case 'completed':
      case 'success':
        return 'complete';
      case 'failed':
      case 'failure':
      case 'aborted':
        return 'failed';
      case 'paused':
      case 'hold':
        return 'paused';
      default:
        return 'idle';
    }
  }

  const missionCard=document.createElement('div'); missionCard.className='nav-mission-card';
  if(missionData){
    const head=document.createElement('div'); head.className='nav-mission-head';
    const title=document.createElement('div'); title.className='nav-mission-title';
    const labelText = waveLabel ? `Mission: ${waveLabel}` : (cleanText(missionData.label) || 'Mission');
    title.textContent = labelText;
    head.appendChild(title);

    const statusKey = String(missionData.status || '').toLowerCase();
    const statusLabel = statusKey ? titleCase(statusKey) : 'Active';
    const status=document.createElement('span');
    status.className='status-badge nav-mission-status ' + statusClassFor(statusKey);
    status.textContent = statusLabel;
    head.appendChild(status);
    missionCard.appendChild(head);

    const meta=document.createElement('div'); meta.className='nav-mission-meta mono';
    const elapsedSpan=document.createElement('span'); elapsedSpan.textContent=`Elapsed ${fmtDuration(missionData.elapsed_s)}`; meta.appendChild(elapsedSpan);
    const leftValue = Number(missionData.time_left_s);
    const leftSpan=document.createElement('span');
    if(Number.isFinite(leftValue) && leftValue >= 0){
      leftSpan.textContent = `Time left ${fmtDuration(leftValue)}`;
    }else{
      leftSpan.textContent = 'No mission timer';
    }
    meta.appendChild(leftSpan);
    if(missionSequence && Array.isArray(missionSequence.order) && missionSequence.order.length){
      const idx = (typeof missionSequence.index === 'number' && missionSequence.index >= 0) ? missionSequence.index : null;
      const seqSpan=document.createElement('span');
      if(idx !== null && idx < missionSequence.order.length){
        seqSpan.textContent = `Stage ${idx+1} of ${missionSequence.order.length}`;
      }else{
        seqSpan.textContent = `${missionSequence.order.length} mission sequence`;
      }
      meta.appendChild(seqSpan);
    }
    if(missionData.id !== undefined && missionData.id !== null){
      const idSpan=document.createElement('span'); idSpan.textContent=`ID ${missionData.id}`; meta.appendChild(idSpan);
    }
    if(waveLabel){
      const waveSpan=document.createElement('span');
      if(waveIndex !== null && waveCount !== null){
        waveSpan.textContent = `Wave ${waveIndex + 1} of ${waveCount}`;
      }else{
        waveSpan.textContent = waveLabel;
      }
      meta.appendChild(waveSpan);
    }
    if(waveDirection){
      const dirSpan=document.createElement('span'); dirSpan.textContent=`Heading ${waveDirection}`;
      meta.appendChild(dirSpan);
    }
    missionCard.appendChild(meta);

    if(missionSettings.stations_offline){
      const trainingNotice=document.createElement('div'); trainingNotice.className='nav-mission-alert';
      trainingNotice.textContent='Training mode: stations start powered down and hostile contacts are suppressed.';
      missionCard.appendChild(trainingNotice);
    }else if(missionSettings.hostile_spawns === false){
      const quietNotice=document.createElement('div'); quietNotice.className='nav-mission-alert';
      quietNotice.textContent='Hostile spawns disabled for this mission.';
      missionCard.appendChild(quietNotice);
    }

    const desc = cleanText(missionData.description);
    if(desc){
      const descEl=document.createElement('div'); descEl.className='nav-mission-desc'; descEl.textContent=desc;
      missionCard.appendChild(descEl);
    }

    const alertText = cleanText(missionData.alert);
    if(alertText){
      const alertEl=document.createElement('div'); alertEl.className='nav-mission-alert'; alertEl.textContent=alertText;
      missionCard.appendChild(alertEl);
    }

    const outcomeText = cleanText(missionData.outcome);
    if(outcomeText){
      const outcomeEl=document.createElement('div'); outcomeEl.className='nav-mission-outcome';
      outcomeEl.textContent=`Outcome: ${outcomeText}`;
      const lower=outcomeText.toLowerCase();
      if(lower.includes('success') || lower.includes('complete')) outcomeEl.classList.add('ok');
      if(lower.includes('fail') || lower.includes('loss') || lower.includes('abort')) outcomeEl.classList.add('err');
      missionCard.appendChild(outcomeEl);
    }

    const decision = missionData.pending_decision;
    if(decision && typeof decision === 'object'){
      const promptText = cleanText(decision.prompt || decision.text);
      const options = Array.isArray(decision.options) ? decision.options.map(function(opt){
        if(!opt) return '';
        if(typeof opt === 'string') return opt.trim();
        if(typeof opt === 'object') return cleanText(opt.label || opt.text || opt.id);
        return '';
      }).filter(Boolean) : [];
      if(promptText || options.length){
        const decisionEl=document.createElement('div'); decisionEl.className='nav-mission-decision';
        decisionEl.textContent = options.length ? `${promptText || 'Decision required'} (${options.join(' / ')})` : promptText;
        missionCard.appendChild(decisionEl);
      }
    }
  }else{
    const emptyTitle=document.createElement('div'); emptyTitle.className='nav-mission-title'+(waveLabel? '':' muted');
    emptyTitle.textContent= waveLabel ? `Mission: ${waveLabel}` : 'No active mission';
    missionCard.appendChild(emptyTitle);
    if(waveLabel){
      const waveMeta=document.createElement('div'); waveMeta.className='nav-mission-meta mono';
      if(waveIndex !== null && waveCount !== null){
        const seq=document.createElement('span'); seq.textContent=`Wave ${waveIndex + 1} of ${waveCount}`; waveMeta.appendChild(seq);
      }
      if(waveDirection){
        const dir=document.createElement('span'); dir.textContent=`Heading ${waveDirection}`; waveMeta.appendChild(dir);
      }
      missionCard.appendChild(waveMeta);
    }else{
      const emptyNote=document.createElement('div'); emptyNote.className='nav-mission-empty'; emptyNote.textContent='Mission updates will appear here once an operation begins.';
      missionCard.appendChild(emptyNote);
    }
  }

  p.appendChild(missionCard);

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
        await forceRefreshStatus();
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
  let voiceRow = null;
  try{
    voiceRow = p.querySelector('.voice-toggle-row');
    if(voiceRow){
      p.removeChild(voiceRow);
    }
  }catch(_){ voiceRow = null; }
  p.innerHTML='';
  if(voiceRow){
    p.appendChild(voiceRow);
    Voice.updateButtons();
  }
  const pane=document.createElement('div'); pane.className='station-offline'; pane.textContent=label || 'SYSTEM OFFLINE';
  p.appendChild(pane);
  return true;
}

function currentMissionSettings(j){
  if(!j || !j.mission) return null;
  const settings=j.mission.settings;
  return settings && typeof settings==='object' ? settings : null;
}

function missionPowerPreset(settings){
  if(!settings || typeof settings!=='object') return null;
  if(settings.station_power_defaults && typeof settings.station_power_defaults==='object'){
    return settings.station_power_defaults;
  }
  if(settings.stations_offline){
    const defaults={};
    STATION_KEYS.forEach(function(key){ defaults[key] = false; });
    return defaults;
  }
  return null;
}

function applyMissionPowerPreset(missionId, settings){
  if(!missionId) return;
  const preset = missionPowerPreset(settings);
  if(!preset) return;
  if(!ST._missionPowerApplied) ST._missionPowerApplied = {};
  if(ST._missionPowerApplied.id === missionId) return;
  if(!ST.power || typeof ST.power !== 'object') ST.power = {};
  let changed = false;
  STATION_KEYS.forEach(function(key){
    if(key==='NAV'){
      if(ST.power[key] !== true){
        ST.power[key] = true;
        changed = true;
      }
      return;
    }
    let desired;
    if(Object.prototype.hasOwnProperty.call(preset, key)) desired = !!preset[key];
    else if(settings && settings.stations_offline) desired = false;
    else return;
    if(ST.power[key] !== desired){
      ST.power[key] = desired;
      changed = true;
    }
  });
  if(changed){
    saveStationPower();
    renderStationSwitches();
    updateToolbarPowerClasses();
  }
  ST._missionPowerApplied.id = missionId;
}

function renderRADAR(j){
  const p=$('#station-panel'); p.innerHTML='';
  const radarStatus = engSystemStatus(j,'Radar');
  if(radarStatus && radarStatus.toLowerCase()!=='ok'){
    renderStationOffline(p,'SYSTEM OFFLINE','RADAR');
    return;
  }
  // Own-fleet toggle (persist via localStorage)
  let showOwnFleet = true;
  let showFriendlies=true, showHostiles=true;
  try{
    const rawFriendly = localStorage.getItem('radar_show_friendlies');
    if(rawFriendly!==null){ showFriendlies = rawFriendly==='1'; }
  }catch(_){ showFriendlies = true; }
  try{
    const rawHostile = localStorage.getItem('radar_show_hostiles');
    if(rawHostile!==null){ showHostiles = rawHostile==='1'; }
  }catch(_){ showHostiles = true; }

  const toggleWrap=document.createElement('div'); toggleWrap.className='row section';
  const lblFriend=document.createElement('span'); lblFriend.textContent='Friendlies'; lblFriend.style.marginRight='6px';
  const btnFriend=document.createElement('button'); btnFriend.className='btn'; btnFriend.textContent = showFriendlies ? 'ON' : 'OFF';
  btnFriend.onclick=function(){ showFriendlies=!showFriendlies; btnFriend.textContent=showFriendlies?'ON':'OFF'; try{ localStorage.setItem('radar_show_friendlies', showFriendlies?'1':'0'); }catch(_){} render(j); };
  const spacer=document.createElement('span'); spacer.textContent=' ';
  const lblHost=document.createElement('span'); lblHost.textContent='Hostiles'; lblHost.style.margin='0 6px';
  const btnHost=document.createElement('button'); btnHost.className='btn'; btnHost.textContent = showHostiles ? 'ON' : 'OFF';
  btnHost.onclick=function(){ showHostiles=!showHostiles; btnHost.textContent=showHostiles?'ON':'OFF'; try{ localStorage.setItem('radar_show_hostiles', showHostiles?'1':'0'); }catch(_){} render(j); };
  toggleWrap.appendChild(lblFriend); toggleWrap.appendChild(btnFriend); toggleWrap.appendChild(spacer);
  toggleWrap.appendChild(lblHost); toggleWrap.appendChild(btnHost);
  p.appendChild(toggleWrap);

  const lockedId = extractLockedId(j.radar);
  const primary = (j.primary && typeof j.primary==='object')? j.primary : null;
  if(!ST.radar || typeof ST.radar !== 'object') ST.radar = {};
  let contacts = Array.isArray(j.contacts)? j.contacts.slice(): [];
  contacts = contacts.filter(function(c){
    const allegiance = String(c.allegiance || c.type || '').toLowerCase();
    const isFriendly = String(c.id||'').startsWith('fleet:') || allegiance==='friendly';
    const isHostile = allegiance === 'hostile';
    if(isFriendly && !showFriendlies) return false;
    if(isHostile && !showHostiles) return false;
    return true;
  });
  const lockedContact = contacts.find(c=>Number(c.id)===lockedId) || null;
  const primaryBox=document.createElement('div'); primaryBox.className='primary-box';
  const lockedRangeDecimals = (lockedContact && String(lockedContact.type||'').toLowerCase()==='hostile') ? 2 : 1;
  const primaryRangeDecimals = (primary && String(primary.type||'').toLowerCase()==='hostile') ? 2 : 1;
  const primFields=[
    ['#ID', lockedContact ? String(lockedContact.id).padStart(2,'0') : '—'],
    ['Name', lockedContact ? String(lockedContact.name||'') : (primary? String(primary.name||''): '—')],
    ['Range', lockedContact && lockedContact.range_nm!=null ? `${fmt(lockedContact.range_nm,lockedRangeDecimals)} nm` : (primary&&primary.range_nm!=null? `${fmt(primary.range_nm,primaryRangeDecimals)} nm` : '—')],
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
    .sort(function(a,b){ return (a.range_nm||1e9)-(b.range_nm||1e9); });
  const lockContact = async (id)=>{
    if(id===undefined || id===null) return;
    const cmdUrl = '/api/command?cmd='+encodeURIComponent(`/radar lock ${id}`);
    try{
      const res = await fetch(cmdUrl, {cache:'no-store'});
      let data=null;
      try{ data = await res.json(); }catch(_){ data = null; }
      const ok = res.ok && data && data.ok !== false;
      if(!ok){
        const msg = data && (data.message || data.error) ? String(data.message || data.error) : `${res.status} lock failed`;
        appendConsole(`[radar] lock ERR ${msg}`);
        return;
      }
      const numericId = Number(id);
      if(!ST.radar || typeof ST.radar!=='object') ST.radar = {};
      ST.radar.lockedId = Number.isFinite(numericId) ? numericId : null;
      appendConsole(`[radar] lock OK ${data && data.result ? data.result : id}`);
      await forceRefreshStatus();
    }catch(err){
      appendConsole(`[radar] lock ERR ${err}`);
    }
  };
  function readNumber(obj, keys){
    for(const key of keys){
      if(obj && Object.prototype.hasOwnProperty.call(obj, key)){
        const val = Number(obj[key]);
        if(!Number.isNaN(val)) return val;
      }
    }
    return null;
  }

  list.forEach(function(c, idx){
    const tr=document.createElement('tr');
    if(lockedId!==null && Number(c.id)===lockedId){ tr.classList.add('locked-row'); }
    const tdIdx=document.createElement('td'); tdIdx.className='num'; tdIdx.textContent=String(idx+1);
    const tdStatus=document.createElement('td'); tdStatus.appendChild(colorTag(String(c.type||'—')));
    const tdType=document.createElement('td'); tdType.textContent=String(c.class||c.meta_class||c.meta?.cap?.class||'—');
    const tdName=document.createElement('td'); tdName.textContent=String(c.name||'—');
    const tdCell=document.createElement('td'); tdCell.textContent = c.cell ? String(c.cell) : '—';
    const hostileRow = String(c.type||'').toLowerCase()==='hostile';
    const tdRange=document.createElement('td'); tdRange.className='num';
    const rangeDecimals = hostileRow ? 2 : 1;
    const rangeVal = readNumber(c, ['range_nm','Range','range','distance_nm','distance']);
    const hasRange = rangeVal !== null;
    tdRange.textContent = hasRange ? `${fmt(rangeVal, rangeDecimals)} nm` : '—';
    const speedVal = readNumber(c, ['speed','speed_kts','SPD']);
    const tdSpeed=document.createElement('td'); tdSpeed.className='num'; tdSpeed.textContent=(speedVal!==null)?`${fmt(speedVal,0)} kn`:'—';
    const tdTTI=document.createElement('td'); tdTTI.className='num';
    const ttiLegacy = computeTTI(c);
    tdTTI.textContent = (ttiLegacy!==null)? `${ttiLegacy}s` : '—';
    const tdId=document.createElement('td'); tdId.className='num'; tdId.textContent=(c.id!==undefined&&c.id!==null)?String(c.id).padStart(2,'0'):'—';
    const tdLock=document.createElement('td');
    const btn=document.createElement('button'); btn.className='btn'; btn.textContent='LOCK';
    const numericId = Number(c.id);
    const isNumericId = Number.isFinite(numericId);
    if(!isNumericId){ btn.disabled = true; }
    btn.onclick=function(){
      if(!isNumericId) return;
      lockContact(numericId);
    };
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
  scanBtn.onclick=async function(){ await fetch('/api/command?cmd='+encodeURIComponent('/radar scan')); await forceRefreshStatus(); };
  const lockNearest=document.createElement('button'); lockNearest.className='btn'; lockNearest.textContent='GO';
  lockNearest.onclick=async function(){ await fetch('/api/command?cmd='+encodeURIComponent('/radar lock nearest')); await forceRefreshStatus(); };
  const unlockBtn=document.createElement('button'); unlockBtn.className='btn'; unlockBtn.textContent='UNLOCK';
  unlockBtn.onclick=async function(){ await fetch('/api/command?cmd='+encodeURIComponent('/radar unlock')); await forceRefreshStatus(); };
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
  const weaponsStatus = engSystemStatus(j,'FireControl_Weapons');
  if(weaponsStatus && weaponsStatus.toLowerCase()!=='ok'){
    renderStationOffline(p,'SYSTEM OFFLINE','WPN');
    return;
  }
  if(!ST.wpn) ST.wpn = { lockInput: '' };

  const contacts = Array.isArray(j.contacts)? j.contacts.slice(): [];
  const lockedId = extractLockedId(j.radar);
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
  const primaryRangeDecimals = (primaryContact && String(primaryContact.type||'').toLowerCase()==='hostile') ? 2 : 1;
  const rangeCell=document.createElement('td'); rangeCell.className='num'; rangeCell.textContent=primaryContact && primaryContact.range_nm!=null? `${fmt(primaryContact.range_nm,primaryRangeDecimals)} nm`:'—'; dataRow.appendChild(rangeCell);
  const speedCell=document.createElement('td'); speedCell.className='num'; speedCell.textContent=primaryContact && primaryContact.speed!=null? `${fmt(primaryContact.speed,0)} kn`:'—'; dataRow.appendChild(speedCell);
  const ttiCell=document.createElement('td'); ttiCell.className='num';
  try{
    const tti = primaryContact? computeTTI(primaryContact):null;
    ttiCell.textContent = (tti!==null)? `${tti}s`:'—';
  }catch(_){ ttiCell.textContent='—'; }
  dataRow.appendChild(ttiCell);
  infoTable.appendChild(dataRow);
  primaryBox.appendChild(infoTable);

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
    if(Number(w.ammo||0) <= 0) return false;
    if(ST.test) return true;
    let canJudgeRange = false;
    let rangeOk = true;
    try{
      if(primaryContact && primaryContact.range_nm!=null){
        const rng = Number(primaryContact.range_nm);
        if(Number.isFinite(rng)){
          canJudgeRange = true;
          const minRaw = w.min_nm;
          const maxRaw = w.max_nm;
          const min = Number(minRaw != null ? minRaw : 0);
          const max = maxRaw == null ? Number.POSITIVE_INFINITY : Number(maxRaw);
          if(Number.isFinite(min) && rng < min) rangeOk = false;
          if(rangeOk && Number.isFinite(max) && rng > max) rangeOk = false;
        }
      }
    }catch(_){ canJudgeRange = false; rangeOk = true; }
    w._range_can_judge = canJudgeRange;
    w._range_ok = canJudgeRange ? rangeOk : (w.in_range !== false);
    if(canJudgeRange) return rangeOk;
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
    const clientRangeFlag = (w && typeof w._range_ok === 'boolean') ? Boolean(w._range_ok) : undefined;
    const inRange = (clientRangeFlag !== undefined) ? clientRangeFlag : !!w.in_range;

    const tdN=document.createElement('td'); tdN.textContent=String(w.name||'—');
    const tdA=document.createElement('td'); tdA.className='num'; tdA.textContent=String(ammo);
    const tdR=document.createElement('td'); tdR.className='num'; tdR.textContent=fmt(w.min_nm,0)+'–'+fmt(w.max_nm,0);

    const tdStatus=document.createElement('td');
    const statusBadge=document.createElement('span'); statusBadge.className='status-badge '+(inRange?'on':'off');
    statusBadge.textContent=inRange?'IN RANGE':'OUT OF RANGE';
    if(w._range_can_judge && primaryContact && primaryContact.range_nm!=null){
      statusBadge.title = `Range ${fmt(primaryContact.range_nm,1)} nm (limits ${fmt(w.min_nm,0)}–${fmt(w.max_nm,0)})`;
    }
    tdStatus.appendChild(statusBadge);

    const msg=document.createElement('div'); msg.className='wpn-msg muted';

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
          await forceRefreshStatus();
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
      await forceRefreshStatus();
    };
    tdF.appendChild(fb);
    tdF.appendChild(msg);
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
        await forceRefreshStatus();
      }else{
        setHermesMsg((data && data.error)? String(data.error) : 'Command failed','err');
      }
    }catch(_){
      setHermesMsg('Command failed','err');
    }
  }
  moveCloserBtn.onclick=function(){ adjustHermes('in'); };
  moveAwayBtn.onclick=function(){ adjustHermes('out'); };

  const lockedId = extractLockedId(j.radar);
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
        if(!['awaiting_pair','queued','airborne','onstation','rtb','recovering'].includes(status)) return sum;
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
        return ['awaiting_pair','queued','airborne','onstation','rtb','recovering'].includes(status);
      }).length;
      return active * 2;
    }
    return 0;
  })();

  const pendingRequests = (function(){
    let raw = 0;
    try{
      if(cap && cap.readiness && typeof cap.readiness==='object' && cap.readiness.pending_requests!=null){
        raw = cap.readiness.pending_requests;
      }else if(cap && cap.pending_requests!=null){
        raw = cap.pending_requests;
      }
    }catch(_){ raw = 0; }
    const num = Number(raw);
    return Number.isFinite(num) ? num : 0;
  })();

  const queuedCount = (function(){
    if(cap && cap.readiness && typeof cap.readiness==='object' && cap.readiness.queued_count != null){
      const val = Number(cap.readiness.queued_count);
      if(Number.isFinite(val)) return Math.max(0, val);
    }
    if(Array.isArray(tasks)){
      return tasks.filter(function(t){
        return t && String(t.status || '').toLowerCase() === 'queued';
      }).length;
    }
    return 0;
  })();

  const launchIntervalRaw = (function(){
    if(cap && cap.readiness && typeof cap.readiness==='object' && cap.readiness.launch_interval_left_s!=null){
      return Number(cap.readiness.launch_interval_left_s);
    }
    if(cap && cap.launch_interval_left_s!=null){
      return Number(cap.launch_interval_left_s);
    }
    return 0;
  })();

  const deckHoldActive = (Number.isFinite(launchIntervalRaw) && launchIntervalRaw > 0) || queuedCount > 0;

  const readyTag=document.createElement('span');
  readyTag.className='shar-ready ' + ((readyPairsNum > 0 && airframesNum >= 2 && !hasCooldown && !deckHoldActive)? 'ok':'err');
  readyTag.textContent = (readyPairsNum > 0 && airframesNum >= 2 && !hasCooldown && !deckHoldActive)? 'READY':'STANDBY';
  sharSummary.appendChild(readyTag);
  const summaryItems=[
    `pairs: ${readyPairsNum}`,
    `airframes: ${airframesNum}`,
    `sidewinders: ${Math.max(0, Math.round(sidewindersCount))}`,
    `cooldown: ${hasCooldown ? fmtDuration(cooldownRaw) : '0s'}`,
    `committed airframes: ${Math.max(0, Math.round(committedCount))}`,
    `pending launches: ${Math.max(0, Math.round(pendingRequests))}`,
    `queued deck: ${Math.max(0, Math.round(queuedCount))}`,
    `deck hold: ${deckHoldActive ? fmtDuration(Math.max(0, launchIntervalRaw)) : '0s'}`
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
  ['Flight','AIM9','Status','POS','Target','RNG','TOT','TOS','Engage','Reassign','RTB'].forEach(function(label){
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

  function capStationRadius(){
    try{
      const readiness = (j.cap && j.cap.readiness) || {};
      const val = Number(readiness.station_radius_nm);
      if(Number.isFinite(val) && val > 0) return val;
    }catch(_){ }
    return 10;
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
    capRadius: capStationRadius(),
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
      const radiusLabel = (cfg.capRadius && Number.isFinite(cfg.capRadius)) ? `${cfg.capRadius} nm radius` : 'CAP station';
      pieces.push(`${radiusLabel} circle`);
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
    const canLaunch = missionValid && airframesNum >= 2 && readyPairsNum > 0 && !deckHoldActive;
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
      return {ok:true, data:{missionType:'cap', loadout: 'aim9', target:'hermes', cell:flagshipCell, follow:'hermes', capRadius: capStationRadius()}};
    }
    if(!cfg.capCellNorm) return {ok:false, error:'Enter CAP grid cell.'};
    return {ok:true, data:{missionType:'cap', loadout: cfg.loadout, target:'cap_cell', cell: cfg.capCellNorm, capRadius: capStationRadius()}};
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
          await forceRefreshStatus();
        }else{
          setStatus((data && (data.message || data.error))? String(data.message || data.error) : 'Intercept request failed','err');
        }
      }else{
        const payload={ cell: mission.cell, station_minutes: 10, radius_nm: mission.capRadius || capStationRadius(), loadout: mission.loadout };
        if(mission.follow==='hermes') payload.follow='hermes';
        const res=await fetch('/cap/launch_to',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
        const data=await res.json();
        if(data && data.ok){
          const msg = (data.message && String(data.message)) || (summaryLabel || (mission.follow==='hermes'? 'CAP launching to defend Hermes' : `CAP launching to ${mission.cell}`));
          setStatus(msg,'ok');
          await forceRefreshStatus();
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
        await forceRefreshStatus();
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
        if(mission.primaryContact){
          const pc = mission.primaryContact;
          const lockId = (pc.id!=null ? pc.id : (pc.ID!=null ? pc.ID : null));
          if(lockId!=null){
            try{
              const lockCmd = `/api/command?cmd=${encodeURIComponent('/radar lock '+lockId)}`;
              await fetch(lockCmd);
            }catch(_){ }
          }
        }
        const body = { mission_id: missionId, loadout: mission.loadout };
        res = await fetch('/cap/vector',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
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
      await forceRefreshStatus();
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
    const missilesTd=document.createElement('td'); missilesTd.className='num';
    const missilesLeftRaw = (t && t.missiles_left != null) ? Number(t.missiles_left) : Number.NaN;
    missilesTd.textContent = Number.isFinite(missilesLeftRaw) ? Math.max(0, Math.round(missilesLeftRaw)) : '—';
    tr.appendChild(missilesTd);
    const statusTd=document.createElement('td'); statusTd.textContent = String(t.status || '—').toUpperCase(); tr.appendChild(statusTd);
    const posTd=document.createElement('td'); posTd.textContent = String(t.cur_cell || t.origin_cell || '—'); tr.appendChild(posTd);
    const tgtTd=document.createElement('td'); tgtTd.textContent = targetDescription(t); tr.appendChild(tgtTd);
    const rngTd=document.createElement('td'); rngTd.className='num';
    rngTd.textContent = (t.range_nm!=null && Number.isFinite(Number(t.range_nm)))? `${fmt(t.range_nm,1)} nm` : '—';
    tr.appendChild(rngTd);
    const totTd=document.createElement('td'); totTd.className='num';
    const launchIn = (t.launch_in_s!=null && Number.isFinite(Number(t.launch_in_s))) ? Math.max(0, Number(t.launch_in_s)) : null;
    if(statusKey==='queued'){
      totTd.textContent = launchIn!=null ? fmtDuration(launchIn) : '—';
      totTd.title = 'Time until launch';
    }else if(statusKey==='onstation'){
      totTd.textContent = 'ON STN';
      totTd.title = '';
    }else{
      totTd.textContent = t.tot_s!=null ? fmtDuration(t.tot_s) : '—';
      totTd.title = '';
    }
    tr.appendChild(totTd);
    const tosTd=document.createElement('td'); tosTd.className='num';
    tosTd.textContent = t.tos_s!=null ? fmtDuration(t.tos_s) : '—';
    tr.appendChild(tosTd);

    const permTd=document.createElement('td'); permTd.className='action';
    const perm = (t && t.permission && typeof t.permission==='object')? t.permission : {};
    if(perm.required){
      let authorized = Boolean(perm.authorized);
      const engageBtn=document.createElement('button');
      engageBtn.className='btn engage-toggle';
      engageBtn.type='button';
      engageBtn.textContent='ENGAGE';
      const syncEngageState = function(){
        engageBtn.classList.toggle('engaged', !!authorized);
        engageBtn.setAttribute('aria-pressed', authorized ? 'true' : 'false');
      };
      syncEngageState();
      engageBtn.onclick = async function(){
        if(!missionId){ setStatus('Unknown mission id','err'); return; }
        const prevAuthorized = authorized;
        const nextAuthorize = !authorized;
        authorized = nextAuthorize;
        syncEngageState();
        engageBtn.disabled = true;
        engageBtn.classList.add('pending');
        engageBtn.setAttribute('aria-busy', 'true');
        setStatus(nextAuthorize ? 'Authorizing engagement…' : 'Revoking engagement…','muted');
        try{
          const res=await fetch('/cap/authorize',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: missionId, authorize: nextAuthorize})});
          const data=await res.json();
          if(data && data.ok){
            authorized = (typeof data.authorized === 'boolean') ? data.authorized : nextAuthorize;
            syncEngageState();
            setStatus(authorized ? 'Engagement authorized' : 'Engagement revoked', 'ok');
            await forceRefreshStatus();
          }else{
            authorized = prevAuthorized;
            syncEngageState();
            setStatus((data && data.error)? String(data.error) : 'Authorization failed','err');
          }
        }catch(_){
          authorized = prevAuthorized;
          syncEngageState();
          setStatus('Authorization failed','err');
        }finally{
          engageBtn.disabled = false;
          engageBtn.classList.remove('pending');
          engageBtn.removeAttribute('aria-busy');
          syncEngageState();
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
          await forceRefreshStatus();
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
    const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=11; td.textContent='No active SHAR flights'; tr.appendChild(td); commitTable.appendChild(tr);
  }

  const resupply = j.resupply || {};
  if(resupply && resupply.active){
    const tr=document.createElement('tr'); tr.className='resupply-row';
    const cols=[
      'Sea King',
      '—',
      String(resupply.stage || 'ACTIVE').toUpperCase(),
      String(resupply.cell || resupply.pos || '—'),
      'Sheffield (Resupply)',
      (resupply.range_nm!=null && Number.isFinite(Number(resupply.range_nm)))? `${fmt(resupply.range_nm,2)} nm` : '—',
      '—',
      resupply.left_s!=null ? fmtDuration(resupply.left_s) : '—',
      '—',
      '—',
      '—'
    ];
    cols.forEach(function(text, idx){
      const td=document.createElement('td');
      if(idx>=5 && idx<=7) td.className='num';
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
    await forceRefreshStatus();
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
  if(!ST.sys) ST.sys = {};

  // Controls removed per kiosk requirements; system station remains read-only.

  const contacts = Array.isArray(j.contacts) ? j.contacts.slice() : [];
  const gridInfo = typeof j.grid === 'object' && j.grid ? j.grid : {};
  const parsedCols = Number(gridInfo.cols);
  const parsedRows = Number(gridInfo.rows);
  const totalCols = Math.max(1, Number.isFinite(parsedCols) ? Math.trunc(parsedCols) : 40);
  const totalRows = Math.max(1, Number.isFinite(parsedRows) ? Math.trunc(parsedRows) : 40);
  const rowWidth = Math.max(1, String(Math.max(0, totalRows - 1)).length);

  function indexToColumn(idx){
    const hi = Math.floor(idx / 26);
    const lo = idx % 26;
    return String.fromCharCode(65 + hi) + String.fromCharCode(65 + lo);
  }

  function columnToIndex(label){
    if(!label || typeof label !== 'string' || label.length !== 2) return null;
    const hi = label.charCodeAt(0) - 65;
    const lo = label.charCodeAt(1) - 65;
    if(hi < 0 || hi >= 26 || lo < 0 || lo >= 26) return null;
    const idx = hi * 26 + lo;
    return (idx >= 0 && idx < totalCols) ? idx : null;
  }

  const columns = Array.from({length: totalCols}, (_,i)=>indexToColumn(i));
  const grid = Array.from({length: totalRows}, ()=>Array.from({length: totalCols},()=>[]));

  function coordFromCell(raw){
    if(!raw || typeof raw !== 'string') return null;
    const match = /^([A-Z]{2})(\d{1,3})$/.exec(raw.trim().toUpperCase());
    if(!match) return null;
    const colIdx = columnToIndex(match[1]);
    const rowIdx = Number.parseInt(match[2],10);
    if(colIdx===null || !Number.isFinite(rowIdx) || rowIdx < 0 || rowIdx >= totalRows) return null;
    return [rowIdx, colIdx];
  }

  function isHostile(contact){
    return String(contact.allegiance || contact.type || '').toLowerCase() === 'hostile';
  }

  function isFriendly(contact){
    return String(contact.allegiance || contact.type || '').toLowerCase() === 'friendly';
  }

  function isSheffield(contact){
    const id = String(contact.id || '').toLowerCase();
    if(id === 'fleet:own' || id === 'fleet:sheffield') return true;
    const meta = contact && typeof contact === 'object' ? contact.meta || {} : {};
    if(meta && meta.own_ship) return true;
    const name = String(contact.name || '').toLowerCase();
    return name.includes('sheffield');
  }

  function isHarrier(contact){
    if(contact && (contact.cap_flight || contact.cap_callsign)) return true;
    const name = String(contact.name || '').toLowerCase();
    const pennant = String(contact.pennant || '').toLowerCase();
    if(name.includes('harrier')) return true;
    if(pennant.startsWith('cap-')) return true;
    return false;
  }

  function isHermes(contact){
    const id = String(contact.id || '').toLowerCase();
    const name = String(contact.name || '').toLowerCase();
    return id === 'fleet:hermes' || name.includes('hms hermes');
  }

  contacts.forEach(function(c){
    const cell = String(c.cell || c.grid || '').trim().toUpperCase();
    const xy = coordFromCell(cell);
    if(!xy) return;
    const [rowIdx, colIdx] = xy;
    grid[rowIdx][colIdx].push(c);
  });

  const friendlyTotal = contacts.filter(function(c){
    return String(c.allegiance || c.type || '').toLowerCase() === 'friendly';
  }).length;
  const hostileTotal = contacts.filter(function(c){
    return String(c.allegiance || c.type || '').toLowerCase() === 'hostile';
  }).length;

  const wrap=document.createElement('div'); wrap.className='sys-grid-wrap';
  const header=document.createElement('div'); header.className='sys-grid-header mono';
  header.textContent = `Contacts — Friendlies: ${friendlyTotal}   Enemies: ${hostileTotal}`;
  wrap.appendChild(header);

  const table=document.createElement('table'); table.className='sys-grid';
  const headRow=document.createElement('tr');
  const corner=document.createElement('th'); corner.className='sys-grid-corner'; corner.textContent='';
  headRow.appendChild(corner);
  columns.forEach(function(colLabel){
    const th=document.createElement('th'); th.textContent=colLabel; th.className='sys-grid-col';
    headRow.appendChild(th);
  });
  table.appendChild(headRow);

  grid.forEach(function(row, rowIdx){
    const tr=document.createElement('tr');
    const th=document.createElement('th'); th.textContent=String(rowIdx).padStart(rowWidth,'0'); th.className='sys-grid-row';
    tr.appendChild(th);
    row.forEach(function(cellContacts){
      const td=document.createElement('td'); td.className='sys-cell';
      if(cellContacts.length){
        const hostiles = cellContacts.filter(isHostile);
        const sheffieldContacts = cellContacts.filter(isSheffield);
        const hermesContacts = cellContacts.filter(isHermes);
        const harriers = cellContacts.filter(function(c){ return isFriendly(c) && !isHermes(c) && isHarrier(c); });
        const harrierCalls = harriers.map(function(h){
          return String(h.cap_callsign || '').trim();
        }).filter(Boolean);
        function computeHarrierLabel(){
          if(!harriers.length) return '';
          if(harriers.length === 1 && harrierCalls.length === 1){
            return harrierCalls[0];
          }
          if(harrierCalls.length >= 2){
            const joined = harrierCalls.slice(0, 2).join('');
            return joined.length > 3 ? (harrierCalls[0] + '+') : joined;
          }
          if(harrierCalls.length === 1){
            return harrierCalls[0];
          }
          if(harriers.length > 1){
            return `S${Math.min(harriers.length, 9)}`;
          }
          return 'S1';
        }
        const friendlies = cellContacts.filter(function(c){ return isFriendly(c) && !isHarrier(c) && !isHermes(c); });
        const friendlyCount = friendlies.length + harriers.length + hermesContacts.length + sheffieldContacts.length;
        let label='•';
        if(hostiles.length && friendlyCount){
          td.classList.add('sys-cell--mixed');
          label='X';
        }else if(hostiles.length){
          td.classList.add('sys-cell--hostile');
          label = hostiles.length>1 ? String(Math.min(hostiles.length,9)) : 'E';
        }else if(sheffieldContacts.length){
          td.classList.add('sys-cell--sheffield');
          label = '*';
        }else if(hermesContacts.length && harriers.length){
          td.classList.add('sys-cell--hermes');
          td.classList.add('sys-cell--harrier');
          const harrierLabel = computeHarrierLabel();
          label = harrierLabel || 'S';
        }else if(hermesContacts.length){
          td.classList.add('sys-cell--hermes');
          label = hermesContacts.length>1 ? '=' : '=';
        }else if(harriers.length){
          td.classList.add('sys-cell--harrier');
          const harrierLabel = computeHarrierLabel();
          label = harrierLabel || 'S';
        }else if(friendlies.length){
          td.classList.add('sys-cell--friendly');
          label = friendlies.length>1 ? String(Math.min(friendlies.length,9)) : 'F';
        }
        td.textContent = label;
        const names = cellContacts.map(function(c){
          const allegiance = String(c.allegiance || c.type || '').toUpperCase();
          const call = String(c.cap_callsign || '').trim();
          const base = c.name || 'Contact';
          const display = call ? `${base} (${call})` : base;
          return `${allegiance}: ${display}`;
        });
        td.title = names.join('\n');
      }else{
        td.textContent = '';
      }
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
  wrap.appendChild(table);

  const legend=document.createElement('div'); legend.className='sys-grid-legend';
  const legendItems=[
    {cls:'sys-cell--sheffield', label:'Sheffield (*)'},
    {cls:'sys-cell--friendly', label:'Friendly (F)'},
    {cls:'sys-cell--hermes', label:'Hermes (=)'},
    {cls:'sys-cell--harrier', label:'Harrier (S#)'},
    {cls:'sys-cell--hostile', label:'Enemy (E)'},
    {cls:'sys-cell--mixed', label:'Mixed (X)'},
    {cls:'', label:'Multiple = digit count'}
  ];
  legendItems.forEach(function(item){
    const span=document.createElement('span'); span.className='sys-legend-item';
    const swatch=document.createElement('span'); swatch.className='sys-legend-swatch';
    if(item.cls) swatch.classList.add(item.cls.replace('sys-cell','sys-legend'));
    const text=document.createElement('span'); text.textContent=item.label;
    span.appendChild(swatch);
    span.appendChild(text);
    legend.appendChild(span);
  });
  wrap.appendChild(legend);

  if(!contacts.length){
    const empty=document.createElement('div'); empty.className='sys-grid-empty mono muted';
    empty.textContent='No radar contacts within range.';
    wrap.appendChild(empty);
  }

  p.appendChild(wrap);
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
  const activeStation = ST.active || 'NAV';
  if(!isStationPowered(activeStation)){
    renderStationOffline($('#station-panel'), `${stationLabel(activeStation)} STATION OFFLINE`, activeStation);
    return;
  }
  if(ST.active==='NAV') return renderNAV(j);
  if(ST.active==='RADAR') return renderRADAR(j);
  if(ST.active==='WPN') return renderWPN(j);
  if(ST.active==='RADIO') return renderRADIO(j);
  if(ST.active==='ENG') return renderENG(j);
  if(ST.active==='LOG') return renderLOG();
  if(ST.active==='SYS') return renderSYS(j);
}

function playKlik(){ try{ const a=new Audio('/data/sounds/klik.m4a'); a.volume=0.6; a.play().catch(function(){}); }catch(e){} }


function wire(){
  Voice.init();
  $$('.toolbar .btn').forEach(function(b){ b.addEventListener('click', function(ev){
    playKlik();
    const st = (b && b.dataset) ? b.dataset.st : undefined;
    if(st) setActive(st);
  }); });
  renderStationSwitches();
  updateToolbarPowerClasses();
  // Global KLIK on all button presses
  document.addEventListener('click', function(ev){ const t=ev.target; if(t && t.matches && t.matches('button.btn')){ playKlik(); } }, true);
  renderEventConsole();
  setActive('NAV');
  startPolling();
}

document.addEventListener('DOMContentLoaded', wire);
// Bootstrap marker to help verify the right asset is served
try { window.__stations_loaded = true; window.__stations_loaded_ts = Date.now(); console.log('stations.js loaded', new Date(window.__stations_loaded_ts).toISOString()); } catch (_e) {}
  function extractLockedId(snapshot){
    if(!snapshot) return null;
    const raw = snapshot.locked_id ?? snapshot.lockedId ?? snapshot.locked_contact_id ?? snapshot.lockedContactId;
    const num = Number(raw);
    return Number.isFinite(num) ? num : null;
  }
