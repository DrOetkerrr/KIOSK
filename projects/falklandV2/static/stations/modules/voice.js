import { $, $$ } from '../lib/dom.js';
import {
  ST,
  stationLabel,
  VOICE_DEVICE_STORAGE_KEY,
  stationInfo,
  isStationPowered
} from '../core/state.js';
import { pushEvent } from './events.js';
import { log } from '../core/logger.js';

let forceRefreshStatus = async () => {};

export function configureVoice({ forceRefresh } = {}){
  if(typeof forceRefresh === 'function'){
    forceRefreshStatus = forceRefresh;
  }
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
    log(`[voice] heard: ${raw}`);
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
  const chanDefault = stationInfo(station).channel;
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
    const station = this.activeStation || ST.active || 'NAV';
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
  },
  async _startRecorder(){
    if(!window.MediaRecorder || !this.stream){
      pushEvent('voice', 'Recorder unavailable');
      throw new Error('Recorder unavailable');
    }
    try{
      this.recordChunks = [];
      this.recordStopTimer && clearTimeout(this.recordStopTimer);
      const recorder = new MediaRecorder(this.stream, { mimeType: this.recorderMime });
      recorder.ondataavailable = (ev)=>{ if(ev && ev.data) this.recordChunks.push(ev.data); };
      recorder.onstop = async ()=>{
        this.processing = false;
        try{
          if(!this.recordChunks.length){
            pushEvent('voice', 'No audio captured');
            return;
          }
          const blob = new Blob(this.recordChunks, { type: this.recorderMime });
          const form = new FormData();
          form.append('station', this.activeStation || ST.active || 'NAV');
          form.append('audio', blob, 'voice.webm');
          const resp = await fetch('/radio/voice', { method:'POST', body: form });
          const result = await handleVoiceResponse(resp, {
            station: this.activeStation || ST.active || 'NAV',
            voiceRole: stationInfo(this.activeStation || ST.active || 'NAV').voice
          });
          if(result.success){
            pushEvent('voice', 'Voice command executed');
          }else{
            pushEvent('voice', 'Voice command failed');
          }
        }catch(err){
          console.warn('[voice] recorder stop error', err);
          pushEvent('voice', 'Voice processing error');
        }finally{
          this.recordChunks = [];
        }
      };
      recorder.start();
      this.recorder = recorder;
      this.recordStopTimer = setTimeout(()=>{
        try{ recorder.stop(); }catch(_){}
      }, 8000);
    }catch(err){
      console.warn('[voice] recorder start error', err);
      pushEvent('voice', 'Recorder failed');
      throw err;
    }
  },
  _stopRecorder(){
    const recorder = this.recorder;
    if(!recorder){
      this.processing = false;
      return;
    }
    try{
      recorder.stop();
    }catch(err){
      console.warn('[voice] recorder stop failed', err);
      this.processing = false;
    }
  }
};

export default Voice;
