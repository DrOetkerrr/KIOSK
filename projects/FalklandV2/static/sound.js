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
            playOne('radio_on.wav');
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
          if (unlocked) playWithFade(cap.file || 'SHAR.wav', Number(cap.vol || 0.1), Number(cap.fade_s || 2.0));
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
      const isAircraft = (c.type || "").toLowerCase() === "aircraft";
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
