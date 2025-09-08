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

  // ---- Weapon launch playback (edge-trigger on audio.last_launch) ----
  let lastStamp = null;

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

      // 2) Fly-by trigger (any aircraft within 0.3 nm crossing inward)
      updateFlyby(j);
    } catch (_) {
      // never break the UI
    }
  }

  function playOne(file) {
    try {
      const a = new Audio(BASE + file);
      a.volume = 1.0;
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