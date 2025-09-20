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
  function _muteAllRadio(){ if(IGNORE_MUTE) return false; try{ return localStorage.getItem('MUTE_ALL_RADIO')==='1'; }catch(_){ return false; } }

  function getMuteMap(){
    try {
      if (window.__stationMute) return window.__stationMute;
      const raw = localStorage.getItem('muteRoles');
      if (raw) return JSON.parse(raw) || {};
    } catch (_) {}
    return {};
  }

  function roleMuted(role){
    if(!role) return false;
    try{
      const u=new URL(window.location.href);
      const v=(u.searchParams.get('nomute')||'').toLowerCase();
      if(v==='1'||v==='true'||v==='yes'||v==='on') return false;
    }catch(_){ }
    try {
      const map = getMuteMap();
      return !!map[String(role)];
    } catch (_) {
      return false;
    }
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
    startBridge();
    window.removeEventListener("pointerdown", unlockOnce);
    window.removeEventListener("keydown", unlockOnce);
  };
  window.addEventListener("pointerdown", unlockOnce, { once: true });
  window.addEventListener("keydown", unlockOnce, { once: true });

  // Keep ambience alive across visibility changes
  document.addEventListener("visibilitychange", () => {
    if (!unlocked) return;
    if (document.visibilityState === "visible") startBridge();
    else stopBridge();
  });

  // ---- Weapon launch/result playback (edge-trigger) ----
  let lastStamp = null;
  let lastResult = null;
  let lastRadio = null;
  let lastAlarm = null;
  let alarmAudio = null;
  let lastCapLaunch = null;
  let lastEnemyBomb = null;
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

  function playRadio(file, opts) {
    try {
      const ctx = ensureCtx();
      const url = file.startsWith('/') ? file : (BASE + file);
      const el = new Audio(url);
      // Accessibility: hide programmatic audio elements from screen readers to prevent
      // announcements like reading field names (e.g., "text") before playback.
      try{ el.setAttribute('aria-hidden','true'); el.setAttribute('role','presentation'); el.setAttribute('tabindex','-1'); }catch(_){ }
      el.crossOrigin = 'anonymous';
      const src = (ctx && ctx.createMediaElementSource) ? ctx.createMediaElementSource(el) : null;
      if (!ctx || !src) {
        // Fallback: normal playback
        const targetVol = Math.max(0, Math.min(1, Number((opts&&opts.vol)!=null?opts.vol:0.6)));
        const fadeInMs = Math.max(0, Number((opts&&opts.fadeInMs)!=null?opts.fadeInMs:0));
        if (fadeInMs > 0) { el.volume = 0.0001; } else { el.volume = targetVol; }
        // Gentle fade-out
        el.addEventListener('loadedmetadata', ()=>{
          const dur = el.duration || 0;
          const fadeMs = Math.max(100, Number((opts&&opts.fadeOutMs)!=null?opts.fadeOutMs:300));
          const startMs = Math.max(0, (dur*1000)-fadeMs);
          setTimeout(()=>{
            let i=0; const steps=Math.max(4, Math.floor(fadeMs/50)); const v0=el.volume;
            const id=setInterval(()=>{ i++; el.volume=Math.max(0, v0*(1 - i/steps)); if(i>=steps||el.paused) clearInterval(id); }, 50);
          }, startMs);
          if (fadeInMs > 0) {
            let j=0; const jsteps=Math.max(4, Math.floor(fadeInMs/50));
            const id2=setInterval(()=>{ j++; const t=j/jsteps; el.volume = Math.max(0, Math.min(targetVol, targetVol*t)); if(j>=jsteps||el.paused) clearInterval(id2); }, 50);
          }
        });
        // Completion callback
        try { if (opts && opts.onEndUrl) { el.addEventListener('ended', ()=>{ try{ fetch(String(opts.onEndUrl), { method:'POST' }); }catch(_){ } }); } } catch(_){}
        el.play().catch(()=>{});
        return;
      }
      // Filters: HPF ~300 Hz, LPF ~3400 Hz, light compression, gain
      const hpf = ctx.createBiquadFilter(); hpf.type = 'highpass'; hpf.frequency.value = (opts&&opts.hp)||300;
      const lpf = ctx.createBiquadFilter(); lpf.type = 'lowpass'; lpf.frequency.value = (opts&&opts.lp)||3400;
      const comp = ctx.createDynamicsCompressor();
      try { comp.threshold.value = -20; comp.knee.value = 20; comp.ratio.value = 3; comp.attack.value = 0.01; comp.release.value = 0.25; } catch(_){ }
      const targetVol = Math.max(0, Math.min(1, Number((opts&&opts.vol)!=null?opts.vol:0.6)));
      const fadeInMs = Math.max(0, Number((opts&&opts.fadeInMs)!=null?opts.fadeInMs:0));
      const gain = ctx.createGain(); gain.gain.value = fadeInMs > 0 ? 0.0001 : targetVol;
      src.connect(hpf); hpf.connect(lpf); lpf.connect(comp); comp.connect(gain); gain.connect(ctx.destination);
      // Fade-out near end via gain ramp
      el.addEventListener('loadedmetadata', ()=>{
        const dur = el.duration || 0; const fadeMs = Math.max(100, Number((opts&&opts.fadeOutMs)!=null?opts.fadeOutMs:300));
        const startMs = Math.max(0, (dur*1000)-fadeMs);
        setTimeout(()=>{
          try{
            const v0 = gain.gain.value; const steps=Math.max(4, Math.floor(fadeMs/50)); let i=0;
            const id=setInterval(()=>{ i++; const t=i/steps; gain.gain.value=Math.max(0, v0*(1-t)); if(i>=steps||el.paused) clearInterval(id); }, 50);
          }catch(_){ }
        }, startMs);
        if (fadeInMs > 0) {
          try {
            let j=0; const jsteps=Math.max(4, Math.floor(fadeInMs/50));
            const id2=setInterval(()=>{ j++; const t=j/jsteps; gain.gain.value=Math.max(0, targetVol * t); if(j>=jsteps||el.paused) clearInterval(id2); }, 50);
          } catch(_){}
        }
      });
      try { if (opts && opts.onEndUrl) { el.addEventListener('ended', ()=>{ try{ fetch(String(opts.onEndUrl), { method:'POST' }); }catch(_){ } }); } } catch(_){}
      el.play().catch(()=>{});
    } catch (_) {}
  }

  async function pollLaunchAndPlay() {
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
          if (unlocked) playOne(SOUND_MAP[key] || SOUND_MAP.weapon_launch);
        }
      }

      // 2) Result cues (hit/miss)
      const res = j?.audio?.last_result;
      if (res) {
        const evt = res.event || "";
        const ts2 = res.ts || 0;
        if (!lastResult || lastResult.ts !== ts2 || lastResult.event !== evt) {
          lastResult = { event: evt, ts: ts2 };
          if (unlocked && evt === 'hit') {
            playOne(SOUND_MAP.hit);
          }
        }
      }

      // 3) Radio speech (serialized)
      const rs = j?.audio?.radio;
      if (rs) {
        const ts3 = rs.ts || 0;
        const durMs = Math.max(200, Math.min(8000, Number(rs.dur||1.2)*1000));
        if (!lastRadio || lastRadio.ts !== ts3) {
          const roleLabel = String(rs.role || '').trim();
          lastRadio = { ts: ts3, role: roleLabel };
          if (!_muteAllRadio() && !roleMuted(roleLabel) && unlocked) {
            if (rs.file) {
              // Play synthesized voice via radio filter for realism
              playRadio(rs.file, {vol: 0.8, fadeOutMs: 250});
              // Duck SFX while radio speaks, then restore
              setDucking(true, durMs/1000);
              setTimeout(()=>{ maybeRestoreDuck(); }, durMs + 300);
            }
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
          if (unlocked) playRadio(cap.file || 'SHAR.wav', {vol: Number(cap.vol || 0.1), fadeOutMs: Number(cap.fade_s || 2.0)*1000, fadeInMs: Number(cap.fade_in_ms || 0), onEndUrl: (cap.on_end_url || null)});
        }
      }

      const enemyBomb = j?.audio?.enemy_bomb;
      if (enemyBomb) {
        const ts6 = enemyBomb.ts || 0;
        const evtList = Array.isArray(enemyBomb.events) ? enemyBomb.events : [{ event: enemyBomb.event || 'miss', attempt: 1 }];
        if (!lastEnemyBomb || lastEnemyBomb.ts !== ts6) {
          lastEnemyBomb = { ts: ts6 };
          if (unlocked) {
            evtList.forEach((ev, idx) => {
              const kind = (ev && (ev.event || ev.result) || '').toLowerCase();
              const delay = idx * 250;
              setTimeout(() => {
                if (kind === 'hit') playOne(SOUND_MAP.enemy_bomb_hit || SOUND_MAP.hit);
                else playOne(SOUND_MAP.enemy_bomb_miss || SOUND_MAP.miss);
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
          playOne(SOUND_MAP.flyby);
        }
      }
    }
    lastNear = nowNear;
  }

  // Poll on the same cadence as the UI (1s)
  setInterval(pollLaunchAndPlay, 1000);
})();
