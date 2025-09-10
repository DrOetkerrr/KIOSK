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
    // alarms
    red_alert: "red-alert.wav",
  };

  const BASE = "/data/sounds/";
  let unlocked = false;

  // ---- Ambient bridge loop (starts on first user gesture) ----
  let bridgeAudio = null;
  function startBridge() {
    try {
      if (bridgeAudio) return;
      bridgeAudio = new Audio(BASE + SOUND_MAP.bridge_loop);
      bridgeAudio.loop = true;
      bridgeAudio.volume = 0.5;    // gentle bed, adjust later if you like
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
      el.crossOrigin = 'anonymous';
      const src = (ctx && ctx.createMediaElementSource) ? ctx.createMediaElementSource(el) : null;
      if (!ctx || !src) {
        // Fallback: normal playback
        el.volume = Math.max(0, Math.min(1, Number((opts&&opts.vol)!=null?opts.vol:0.6)));
        // Gentle fade-out
        el.addEventListener('loadedmetadata', ()=>{
          const dur = el.duration || 0;
          const fadeMs = Math.max(100, Number((opts&&opts.fadeOutMs)!=null?opts.fadeOutMs:300));
          const startMs = Math.max(0, (dur*1000)-fadeMs);
          setTimeout(()=>{
            let i=0; const steps=Math.max(4, Math.floor(fadeMs/50)); const v0=el.volume;
            const id=setInterval(()=>{ i++; el.volume=Math.max(0, v0*(1 - i/steps)); if(i>=steps||el.paused) clearInterval(id); }, 50);
          }, startMs);
        });
        el.play().catch(()=>{});
        return;
      }
      // Filters: HPF ~300 Hz, LPF ~3400 Hz, light compression, gain
      const hpf = ctx.createBiquadFilter(); hpf.type = 'highpass'; hpf.frequency.value = (opts&&opts.hp)||300;
      const lpf = ctx.createBiquadFilter(); lpf.type = 'lowpass'; lpf.frequency.value = (opts&&opts.lp)||3400;
      const comp = ctx.createDynamicsCompressor();
      try { comp.threshold.value = -20; comp.knee.value = 20; comp.ratio.value = 3; comp.attack.value = 0.01; comp.release.value = 0.25; } catch(_){ }
      const gain = ctx.createGain(); gain.gain.value = Math.max(0, Math.min(1, Number((opts&&opts.vol)!=null?opts.vol:0.6)));
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
      });
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
          if (unlocked) {
            if (evt === 'hit') playOne(SOUND_MAP.hit);
            else if (evt === 'miss') playOne(SOUND_MAP.miss);
          }
        }
      }

      // 3) Radio speech (serialized)
      const rs = j?.audio?.radio;
      if (rs) {
        const ts3 = rs.ts || 0;
        const durMs = Math.max(200, Math.min(8000, Number(rs.dur||1.2)*1000));
        if (!lastRadio || lastRadio.ts !== ts3) {
          lastRadio = { ts: ts3 };
          if (unlocked) {
            // Always use radio beeps around the transmission for immersion
            playOne('radio_on.wav');
            if (rs.file) {
              // Play synthesized voice via radio filter for realism
              playRadio(rs.file, {vol: 0.8, fadeOutMs: 250});
            }
            setTimeout(()=> playOne('radio_off.wav'), durMs);
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
              alarmAudio.volume = 1.0;
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
          if (unlocked) playRadio(cap.file || 'SHAR.wav', {vol: Number(cap.vol || 0.1), fadeOutMs: Number(cap.fade_s || 2.0)*1000});
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
      a.volume = 1.0;
      a.play().catch(() => {});
    } catch (_) {}
  }

  function playWithFade(file, volume, fadeSeconds) {
    try {
      const a = new Audio(file.startsWith('/')? file : (BASE + file));
      a.volume = Math.max(0, Math.min(1, isFinite(volume)? volume : 0.1));
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
