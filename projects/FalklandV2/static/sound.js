// Sound driver (stable) — no UI changes required.
// - Plays weapon launch sounds when /api/status stamps audio.last_launch
// - Starts a looping "bridge" ambience once the user interacts
// - Triggers a "flyby" when any aircraft crosses within 0.3 nm
// Files are served from /data/sounds/<file> (webdash.py provides the route)

(function () {
  // Match your actual filenames under data/sounds/
  const SOUND_MAP = {
    // weapons
    exocet_mm38: "missile_launch.wav",
    seacat: "missile_launch.wav",
    gun_4_5in: "4.5cmgun.wav",
    oerlikon_20mm: "gunfire.mp3",
    gam_bo1_20mm: "gunfire.mp3",
    corvus_chaff: "chaff.wav",

    // atmospheric / cues
    bridge_loop: "bridge.wav",
    flyby: "flyby.wav",

    // generic
    weapon_launch: "missile_launch.wav",
    hit: "hit.wav",
    miss: "miss.wav",
    enemy_bomb_hit: "incoming.wav",
    enemy_bomb_miss: "miss.wav",
    // alarms
    red_alert: "red-alert.wav",
  };

  const BASE = "/data/sounds/";
  let unlocked = false;

  // Feature flags via URL or localStorage
  function _qsFlag(name){
    try{
      const u=new URL(window.location.href);
      const v=(u.searchParams.get(name)||'').toLowerCase();
      return v==='1'||v==='true'||v==='yes'||v==='on';
    }catch(_){ return false; }
  }
  const DISABLE_BRIDGE = _qsFlag('nobridge') || (function(){ try{ return localStorage.getItem('DISABLE_BRIDGE')==='1'; }catch(_){ return false; } })();
  const IGNORE_MUTE = _qsFlag('nomute');

  const STATION_CHANNELS = {
    NAV: 1,
    RADAR: 2,
    WPN: 3,
    RADIO: 4,
    ENG: 5,
    LOG: 4,
    SYS: 4,
  };

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

  let activeStation = 'NAV';
  let activeChannel = STATION_CHANNELS[activeStation] || 1;
  const DEFER_LIMIT = 3;
  const MAX_QUEUE = 6;
  const STALE_MS = 6000;
  const GUARD_STALE_MS = 12000;
  const RADIO_FADE_MS = 150;
  const RADIO_BUFFER_CACHE = new Map();
  const deferredRadio = new Map();

  function _stationChannel(id){
    try{
      return STATION_CHANNELS[id] || 4;
    }catch(_){ return 4; }
  }

  function _roleChannel(role){
    if(!role) return 4;
    try{
      const ch = ROLE_CHANNELS[String(role)];
      return (typeof ch === 'number' && ch >=1 && ch <=6) ? ch : 4;
    }catch(_){
      return 4;
    }
  }

  function _syncActiveStation(){
    try{
      const st = window.__activeStation || activeStation;
      activeStation = st in STATION_CHANNELS ? st : 'NAV';
      const ch = window.__activeChannel;
      if(typeof ch === 'number' && ch >=1 && ch <=6){
        activeChannel = ch;
      }else{
        activeChannel = _stationChannel(activeStation);
      }
    }catch(_){
      activeStation = 'NAV';
      activeChannel = 1;
    }
    flushDeferred(activeChannel);
  }

  _syncActiveStation();
  try{
    window.addEventListener('station:changed', function(ev){
      if(ev && ev.detail){
        activeStation = ev.detail.station || activeStation;
        if(typeof ev.detail.channel === 'number'){ activeChannel = ev.detail.channel; }
        else activeChannel = _stationChannel(activeStation);
      }else{
        _syncActiveStation();
      }
      flushDeferred(activeChannel);
    });
  }catch(_){ }

  function _shouldPlay(channel, guard){
    if(introActive) return false;
    if(IGNORE_MUTE) return true;
    if(guard) return true;
    if(channel === 6) return true;
    return channel === activeChannel;
  }

  function deferRadioItem(channel, payload){
    if(!channel || channel === activeChannel) return;
    try{
      const key = Number(channel);
      if(!Number.isFinite(key)) return;
      const existing = deferredRadio.get(key) || [];
      if(existing.some(it=>it.ts === payload.ts)) return;
      const copy = Object.assign({}, payload, { enqueueTs: Date.now() });
      existing.push(copy);
      while(existing.length > DEFER_LIMIT){ existing.shift(); }
      deferredRadio.set(key, existing);
    }catch(_){ }
  }

  function flushDeferred(channel){
    try{
      const key = Number(channel);
      if(!Number.isFinite(key)) return;
      const queue = deferredRadio.get(key);
      if(!queue || !queue.length) return;
      deferredRadio.delete(key);
      queue.forEach(enqueueRadioMessage);
    }catch(_){ }
  }

  // ---- Ambient bridge loop (starts on first user gesture) ----
  let bridgeAudio = null;
  function startBridge() {
    if (DISABLE_BRIDGE) { try{ stopBridge(); }catch(_){ } return; }
    try {
      if (bridgeAudio) return;
      bridgeAudio = new Audio(BASE + SOUND_MAP.bridge_loop);
      bridgeAudio.loop = true;
      bridgeAudio.volume = 0.5 * SFX_GAIN;    // gentle bed, adjust later if you like
      bridgeAudio.play().catch(() => {});
    } catch (_) {}
  }
  function stopBridge() {
    try {
      if (!bridgeAudio) return;
      bridgeAudio.pause();
      bridgeAudio.currentTime = 0;
      bridgeAudio = null;
    } catch (_) {}
  }

  // Require a user gesture once to satisfy autoplay policies
  const unlockOnce = () => {
    unlocked = true;
    if(queuedIntro){
      try{ queuedIntro(); }catch(_){ }
      queuedIntro = null;
    }else if(!introActive && bridgeReady){
      startBridge();
    }
    drainRadioQueue();
    window.removeEventListener("pointerdown", unlockOnce);
    window.removeEventListener("keydown", unlockOnce);
  };
  window.addEventListener("pointerdown", unlockOnce, { once: true });
  window.addEventListener("keydown", unlockOnce, { once: true });

  // Keep ambience alive across visibility changes
  document.addEventListener("visibilitychange", () => {
    if (!unlocked) return;
    if (document.visibilityState === "visible") {
      if (!introActive && !queuedIntro && bridgeReady) startBridge();
    } else {
      stopBridge();
    }
  });

  // ---- Weapon launch/result playback (edge-trigger) ----
  let lastStamp = null;
  let lastResult = null;
  let lastRadio = null;
  const radioQueue = [];
  let radioBusy = false;
  let lastAlarm = null;
  let alarmAudio = null;
  let lastCapLaunch = null;
  let lastCapRecovery = null;
  let lastIntro = null;
  let queuedIntro = null;
  let lastEnemyBomb = null;
  let introActive = Boolean(window.__introActive);
  let bridgeReady = true;
  let introAudio = null;
  if (typeof window.__introActive !== 'boolean') {
    window.__introActive = introActive;
  }
  let duckUntil = 0;
  let SFX_GAIN = 1.0; // global multiplier for SFX/ambience

  function updateAmbientVolumes(){
    try{ if(bridgeAudio) bridgeAudio.volume = 0.5 * SFX_GAIN; }catch(_){ }
    try{ if(alarmAudio) alarmAudio.volume = 1.0 * SFX_GAIN; }catch(_){ }
  }

  function maybeRestoreDuck(){
    try{
      if(Date.now() >= duckUntil){ SFX_GAIN = 1.0; updateAmbientVolumes(); }
    }catch(_){ }
  }

  function setDucking(active, seconds){
    try{
      const now = Date.now();
      if(active){
        const extraMs = Math.max(0, Math.floor((seconds||0)*1000));
        duckUntil = Math.max(duckUntil, now + extraMs + 250);
        SFX_GAIN = 0.6; // gentle duck, not mute
        updateAmbientVolumes();
        setTimeout(()=>{ maybeRestoreDuck(); }, extraMs + 300);
      }else{
        duckUntil = 0; SFX_GAIN = 1.0; updateAmbientVolumes();
      }
    }catch(_){ }
  }

  // ---- Web Audio context + radio filter helper ----
  let ACtx = null;
  function ensureCtx() {
    try{
      if(!ACtx) ACtx = new (window.AudioContext || window.webkitAudioContext)();
      if(ACtx && ACtx.state === 'suspended') ACtx.resume().catch(()=>{});
    }catch(_){ ACtx = null; }
    return ACtx;
  }

  async function loadRadioBuffer(url, ctx){
    if(RADIO_BUFFER_CACHE.has(url)) return RADIO_BUFFER_CACHE.get(url);
    const res = await fetch(url, { cache: 'force-cache' });
    if(!res.ok) throw new Error('radio fetch failed');
    const ab = await res.arrayBuffer();
    const buf = await ctx.decodeAudioData(ab);
    RADIO_BUFFER_CACHE.set(url, buf);
    return buf;
  }

  function playRadio(file, opts) {
    const ctx = ensureCtx();
    const url = file.startsWith('/') ? file : (BASE + file);
    const targetVol = Math.max(0, Math.min(1, Number((opts&&opts.vol)!=null?opts.vol:0.6)));
    const fadeInMs = Math.max(0, Number((opts&&opts.fadeInMs)!=null?opts.fadeInMs:0));
    const fadeOutMs = Math.max(80, Number((opts&&opts.fadeOutMs)!=null?opts.fadeOutMs:RADIO_FADE_MS));

    if(ctx && ctx.createBufferSource){
      loadRadioBuffer(url, ctx).then(buffer => {
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        const hpf = ctx.createBiquadFilter(); hpf.type = 'highpass'; hpf.frequency.value = (opts&&opts.hp)||300;
        const lpf = ctx.createBiquadFilter(); lpf.type = 'lowpass'; lpf.frequency.value = (opts&&opts.lp)||3400;
        const comp = ctx.createDynamicsCompressor();
        try { comp.threshold.value = -20; comp.knee.value = 20; comp.ratio.value = 3; comp.attack.value = 0.01; comp.release.value = 0.25; } catch(_){ }
        const gain = ctx.createGain();
        const now = ctx.currentTime;
        gain.gain.setValueAtTime(fadeInMs > 0 ? 0.0001 : targetVol, now);
        if(fadeInMs > 0){
          gain.gain.linearRampToValueAtTime(targetVol, now + fadeInMs/1000);
        }
        const endTime = now + buffer.duration;
        const fadeStart = Math.max(now, endTime - (fadeOutMs/1000));
        gain.gain.setValueAtTime(targetVol, fadeStart);
        gain.gain.linearRampToValueAtTime(0.0001, endTime);

        const finishOnce = (() => {
          let called = false;
          return () => {
            if(called) return;
            called = true;
            try {
              if (opts && typeof opts.onDone === 'function') { opts.onDone(); }
            } catch (_) {}
          };
        })();

        source.onended = () => {
          if(opts && opts.onEndUrl){ try{ fetch(String(opts.onEndUrl), { method:'POST' }); }catch(_){ } }
          finishOnce();
        };

        source.connect(hpf); hpf.connect(lpf); lpf.connect(comp); comp.connect(gain); gain.connect(ctx.destination);
        source.start();
      }).catch(err => {
        console.warn('[sound] radio buffer load failed', err);
        playRadioElement(url, targetVol, fadeInMs, fadeOutMs, opts);
      });
      return;
    }

    playRadioElement(url, targetVol, fadeInMs, fadeOutMs, opts);
  }

  function playRadioElement(url, targetVol, fadeInMs, fadeOutMs, opts){
    try {
      const el = new Audio(url);
      try{ el.setAttribute('aria-hidden','true'); el.setAttribute('role','presentation'); el.setAttribute('tabindex','-1'); }catch(_){ }
      el.crossOrigin = 'anonymous';
      const finishOnce = (() => {
        let called = false;
        return () => {
          if(called) return;
          called = true;
          try {
            if (opts && typeof opts.onDone === 'function') { opts.onDone(); }
          } catch (_) {}
        };
      })();
      el.addEventListener('ended', finishOnce, { once: true });
      el.addEventListener('error', finishOnce, { once: true });
      el.addEventListener('abort', finishOnce, { once: true });
      if (fadeInMs > 0) { el.volume = 0.0001; } else { el.volume = targetVol; }
      el.addEventListener('loadedmetadata', ()=>{
        const dur = el.duration || 0;
        const startMs = Math.max(0, (dur*1000)-fadeOutMs);
        setTimeout(()=>{
          let i=0; const steps=Math.max(4, Math.floor(fadeOutMs/40)); const v0=el.volume;
          const id=setInterval(()=>{ i++; el.volume=Math.max(0, v0*(1 - i/steps)); if(i>=steps||el.paused) clearInterval(id); }, 40);
        }, startMs);
        if (fadeInMs > 0) {
          let j=0; const jsteps=Math.max(4, Math.floor(fadeInMs/40));
          const id2=setInterval(()=>{ j++; const t=j/jsteps; el.volume = Math.max(0, Math.min(targetVol, targetVol*t)); if(j>=jsteps||el.paused) clearInterval(id2); }, 40);
        }
      });
      if(opts && opts.onEndUrl){ try{ el.addEventListener('ended', ()=>{ try{ fetch(String(opts.onEndUrl), { method:'POST' }); }catch(_){ } }); }catch(_){ } }
      el.play().catch(()=>{ finishOnce(); });
    } catch (err) {
      console.warn('[sound] radio playback failed', err);
      try { if (opts && typeof opts.onDone === 'function') { opts.onDone(); } } catch (_) {}
    }
  }

  function drainRadioQueue(){
    if(introActive) return;
    if(radioBusy) return;
    if(!radioQueue.length) return;
    const item = radioQueue.shift();
    if(!item){
      drainRadioQueue();
      return;
    }
    if(!unlocked){
      radioQueue.unshift(item);
      return;
    }
    if(!item.file){
      drainRadioQueue();
      return;
    }
    const guard = !!item.guard;
    let channelId = parseInt(item.channel, 10);
    if(!Number.isFinite(channelId)) channelId = _roleChannel(item.role);
    const enq = Number(item.enqueueTs || Date.now());
    const age = Date.now() - enq;
    const staleLimit = guard ? GUARD_STALE_MS : STALE_MS;
    if(age > staleLimit){
      if(!guard){
        drainRadioQueue();
        return;
      }
      // guard but too old → drop to keep pace
      if(age > GUARD_STALE_MS * 2){
        drainRadioQueue();
        return;
      }
    }
    if(!_shouldPlay(channelId, guard)){
      if(!guard){
        try{
          const cloned = Object.assign({}, item, { channel: channelId, guard, enqueueTs: enq });
          deferRadioItem(channelId, cloned);
        }catch(_){ }
      }
      drainRadioQueue();
      return;
    }
    radioBusy = true;
    const durationSec = Math.max(0.5, Number(item.duration) || 1.6);
    const vol = Number.isFinite(Number(item.vol)) ? Math.max(0, Math.min(1, Number(item.vol))) : 0.8;
    const fadeOut = Math.max(80, Number(item.fadeOutMs) || RADIO_FADE_MS);
    setDucking(true, Math.max(0.5, durationSec - 0.15));
    playRadio(item.file, {
      vol,
      fadeOutMs: fadeOut,
      onEndUrl: item.onEndUrl,
      onDone: () => {
        radioBusy = false;
        maybeRestoreDuck();
        drainRadioQueue();
      }
    });
  }

  function enqueueRadioMessage(payload){
    const entry = Object.assign({}, payload, { enqueueTs: Date.now() });
    radioQueue.push(entry);
    if(radioQueue.length > MAX_QUEUE){
      let removed = false;
      for(let i=0; i<radioQueue.length; i+=1){
        if(!radioQueue[i].guard){
          radioQueue.splice(i,1);
          removed = true;
          break;
        }
      }
      if(!removed){
        radioQueue.shift();
      }
    }
    drainRadioQueue();
  }

  async function pollLaunchAndPlay() {
    _syncActiveStation();
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      const j = await r.json();

      // 1) Weapon / chaff launches
      const stamp = j?.audio?.last_launch;
      if (stamp) {
        const key = stamp.weapon || "weapon_launch";
        const ts = stamp.ts || 0;
        if (!lastStamp || lastStamp.ts !== ts || lastStamp.weapon !== key) {
          lastStamp = { weapon: key, ts };
          if (!introActive && unlocked) playOne(SOUND_MAP[key] || SOUND_MAP.weapon_launch);
        }
      }

      // 2) Result cues (hit/miss)
      const res = j?.audio?.last_result;
      if (res) {
        const evt = res.event || "";
        const ts2 = res.ts || 0;
        if (!lastResult || lastResult.ts !== ts2 || lastResult.event !== evt) {
          lastResult = { event: evt, ts: ts2 };
          if (!introActive && unlocked && evt === 'hit') {
            playOne(SOUND_MAP.hit);
          }
        }
      }

      // Intro sequence (one-shot)
      const intro = j?.audio?.intro;
      if (intro) {
        const tsIntro = intro.ts || 0;
        if (!lastIntro || lastIntro.ts !== tsIntro) {
          const file = intro.file || '/data/sounds/intro.wav';
          const vol = Number.isFinite(Number(intro.vol)) ? Number(intro.vol) : 0.8;
          const onEndUrl = intro.on_end_url || null;
          const startBridgeFlag = intro.start_bridge !== false;
          const playIntro = () => {
            introActive = true;
            bridgeReady = false;
            window.__introActive = true;
            try{ window.dispatchEvent(new CustomEvent('intro:start')); }catch(_){ }
            stopBridge();
            try { if (introAudio) { introAudio.pause(); } } catch(_) {}
            introAudio = new Audio(file.startsWith('/') ? file : (BASE + file));
            introAudio.volume = Math.max(0, Math.min(1, vol));
            introAudio.loop = false;
            const finish = (() => {
              let called = false;
              return () => {
                if (called) return;
                called = true;
                introActive = false;
                bridgeReady = true;
                if (introAudio) {
                  try { introAudio.pause(); introAudio.currentTime = 0; } catch(_) {}
                  introAudio = null;
                }
                window.__introActive = false;
                try{ window.dispatchEvent(new CustomEvent('intro:end')); }catch(_){ }
                if (startBridgeFlag) startBridge();
                if (onEndUrl) { try { fetch(String(onEndUrl), { method:'POST' }); } catch(_) {} }
                drainRadioQueue();
              };
            })();
            introAudio.addEventListener('ended', finish, { once:true });
            introAudio.addEventListener('error', finish, { once:true });
            const playPromise = introAudio.play();
            if (playPromise && typeof playPromise.catch === 'function') {
              playPromise.catch(() => finish());
            }
          };
          if (unlocked) {
            playIntro();
          } else {
            window.__introActive = true;
            try { window.dispatchEvent(new CustomEvent('intro:start')); } catch(_){}
            queuedIntro = playIntro;
          }
          lastIntro = { ts: tsIntro };
        }
      } else {
        const waveInfo = j?.wave;
        if (waveInfo) {
          bridgeReady = true;
          if (window.__introActive) {
            window.__introActive = false;
            window.dispatchEvent(new CustomEvent('intro:end'));
          }
        } else if (!introActive) {
          bridgeReady = true;
          if (window.__introActive) {
            window.__introActive = false;
            window.dispatchEvent(new CustomEvent('intro:end'));
          }
        }
      }

      if (queuedIntro && unlocked && !introActive) {
        queuedIntro();
        queuedIntro = null;
      }

      // 3) Radio speech (serialized)
      const rs = j?.audio?.radio;
      if (rs) {
        const ts3 = rs.ts || 0;
        const durSec = Math.max(1.8, Math.min(8.0, Number(rs.dur || 1.2)));
        if (!lastRadio || lastRadio.ts !== ts3) {
          const roleLabel = String(rs.role || '').trim();
          let channelId = parseInt(rs.channel, 10);
          if(!Number.isFinite(channelId)) channelId = _roleChannel(roleLabel);
          const guardFlag = !!(rs.guard || channelId === 6);
          lastRadio = { ts: ts3, role: roleLabel, channel: channelId };
          if (rs.file){
            const payload = {
              ts: ts3,
              role: roleLabel,
              file: rs.file,
              duration: durSec,
              channel: channelId,
              guard: guardFlag,
              vol: Number((rs && rs.vol)!=null ? rs.vol : 0.8),
              fadeOutMs: Number((rs && rs.fade_ms)!=null ? rs.fade_ms : 250),
              onEndUrl: rs.on_end_url || null
            };
            if(_shouldPlay(channelId, guardFlag)) enqueueRadioMessage(payload);
            else if(!guardFlag) deferRadioItem(channelId, payload);
            else enqueueRadioMessage(payload);
          }
        }
      }

      // 4) Alarm (server-stamped)
      const alarm = j?.audio?.alarm;
      if (alarm) {
        const ts4 = alarm.ts || 0;
        if (!lastAlarm || lastAlarm.ts !== ts4) {
          lastAlarm = { ts: ts4 };
          const stop = !!alarm.stop;
          if (stop) {
            try { if (alarmAudio) { alarmAudio.pause(); alarmAudio.currentTime = 0; alarmAudio = null; } } catch (_) {}
          } else if (unlocked) {
            try {
              const file = alarm.file || SOUND_MAP[alarm.sound || 'red_alert'] || 'red-alert.wav';
              if (alarmAudio) { try { alarmAudio.pause(); } catch(_){}; alarmAudio = null; }
              alarmAudio = new Audio(file.startsWith('/')? file : (BASE + file));
              // Always one-shot: do not loop alarms
              alarmAudio.loop = false;
              alarmAudio.volume = 1.0 * SFX_GAIN;
              alarmAudio.play().catch(()=>{});
            } catch (_) {}
          }
        }
      }

      // 5) CAP launch cue (one-shot, low volume, fade-out)
      const cap = j?.audio?.cap_launch;
      if (cap) {
        const ts5 = cap.ts || 0;
        if (!lastCapLaunch || lastCapLaunch.ts !== ts5) {
          lastCapLaunch = { ts: ts5 };
          if (!introActive && unlocked) playRadio(cap.file || 'SHAR.wav', {vol: Number(cap.vol || 0.1), fadeOutMs: Number(cap.fade_s || 2.0)*1000, fadeInMs: Number(cap.fade_in_ms || 0), onEndUrl: (cap.on_end_url || null)});
        }
      }

      const capRecovery = j?.audio?.cap_recovery;
      if (capRecovery) {
        const tsRecovery = capRecovery.ts || 0;
        if (!lastCapRecovery || lastCapRecovery.ts !== tsRecovery) {
          lastCapRecovery = { ts: tsRecovery };
          if (!introActive && unlocked) playRadio(capRecovery.file || 'SHAR_landing.wav', {vol: Number(capRecovery.vol || 0.12), fadeOutMs: Number(capRecovery.fade_s || 2.0)*1000, fadeInMs: Number(capRecovery.fade_in_ms || 0), onEndUrl: (capRecovery.on_end_url || null)});
        }
      }

      const enemyBomb = j?.audio?.enemy_bomb;
      if (enemyBomb) {
        const ts6 = enemyBomb.ts || 0;
        const evtList = Array.isArray(enemyBomb.events) ? enemyBomb.events : [{ event: enemyBomb.event || 'miss', attempt: 1 }];
        if (!lastEnemyBomb || lastEnemyBomb.ts !== ts6) {
          lastEnemyBomb = { ts: ts6 };
          if (!introActive && unlocked) {
            evtList.forEach((ev, idx) => {
              const kind = (ev && (ev.event || ev.result) || '').toLowerCase();
              const delay = idx * 250;
              setTimeout(() => {
                if (kind === 'hit') {
                  const first = SOUND_MAP.enemy_bomb_hit || SOUND_MAP.hit;
                  playOne(first);
                  setTimeout(() => playOne(SOUND_MAP.hit || first), 2000);
                } else {
                  playOne(SOUND_MAP.enemy_bomb_miss || SOUND_MAP.miss);
                }
              }, delay);
            });
         }
       }
     }

      // 6) Fly-by trigger (any aircraft within 0.3 nm crossing inward)
      updateFlyby(j);
    } catch (_) {
      // never break the UI
    }
  }

  function playOne(file) {
    try {
      const a = new Audio(file.startsWith('/')? file : (BASE + file));
      a.volume = Math.max(0, Math.min(1, 1.0 * SFX_GAIN));
      a.play().catch(() => {});
    } catch (_) {}
  }

  function playWithFade(file, volume, fadeSeconds) {
    try {
      const a = new Audio(file.startsWith('/')? file : (BASE + file));
      a.volume = Math.max(0, Math.min(1, (isFinite(volume)? volume : 0.1) * SFX_GAIN));
      a.loop = false;
      const doFade = (sec) => {
        const duration = a.duration || 0;
        const startInMs = Math.max(0, (duration - sec) * 1000);
        setTimeout(() => {
          try {
            const steps = Math.max(4, Math.floor(sec * 20)); // 50ms steps
            let i = 0;
            const v0 = a.volume;
            const id = setInterval(() => {
              i += 1;
              const t = i / steps;
              a.volume = Math.max(0, v0 * (1 - t));
              if (i >= steps || a.paused) { clearInterval(id); }
            }, 50);
          } catch (_) {}
        }, startInMs);
      };
      a.addEventListener('loadedmetadata', () => doFade(Math.max(0, isFinite(fadeSeconds)? fadeSeconds : 2.0)));
      a.play().catch(() => {});
    } catch (_) {}
  }

  // ---- Fly-by detector (client-side threshold from status contacts) ----
  const FLY_THRESH = 0.3; // nm
  let lastNear = new Set(); // ids that were <= thresh last tick

  function updateFlyby(statusJson) {
    if (!unlocked) return;
    const list = Array.isArray(statusJson?.contacts) ? statusJson.contacts : [];
    const nowNear = new Set();

    for (const c of list) {
      if (!c) continue;
      const kind = (c.class || c.category || "").toLowerCase();
      const isAircraft = (kind === 'aircraft' || kind === 'helicopter');
      if (!isAircraft) continue;
      const allegiance = String(c.type || c.allegiance || '').toLowerCase();
      if (allegiance !== 'hostile') continue;
      const d = typeof c.range_nm === "number" ? c.range_nm : Number(c.range_nm);
      if (!isFinite(d)) continue;
      if (d <= FLY_THRESH) {
        nowNear.add(c.id);
        if (!lastNear.has(c.id)) {
          // crossed inward through the threshold: cue flyby
          if (!introActive && unlocked) playOne(SOUND_MAP.flyby);
        }
      }
    }
    lastNear = nowNear;
  }

  // Poll on the same cadence as the UI (1s)
  setInterval(pollLaunchAndPlay, 1000);
})();
