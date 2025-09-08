#!/usr/bin/env python3
"""
Audio subsystem — Falklands V2
--------------------------------
Purpose
- Central, stateless-ish manager for *what audio should be playing now*.
- Loads sound definitions from data/audio_config.json.
- Game code calls `play()`, `stop()`, `schedule()`, and `tick()`.
- Web UI polls `/api/status` and uses `audio.snapshot()` to decide which files
  to play (HTML <audio> in the browser). Python does not play audio.

Public surface (call from engine/webdash/etc.)
- AudioManager(data_path: Path)
- play(sound_id: str, now: float | None = None, *, replace: bool = False,
       cooldown_s: float = 0.0, gain: float | None = None) -> bool
- stop(sound_id: str, now: float | None = None) -> int
- schedule(sound_id: str, start_in_s: float, now: float | None = None,
           *, gain: float | None = None) -> bool
- tick(now: float | None = None) -> None
- snapshot(now: float | None = None) -> dict
- clear() -> None

Config file (data/audio_config.json)
-----------------------------------
{
  "sounds": {
    "bridge_ambience": { "file": "bridge.wav", "loop": true,  "volume": 0.25 },
    "weapon_ready":    { "file": "beep.wav",   "loop": false, "duration_s": 1.2, "volume": 0.9 },
    "gun_fire":        { "file": "gun.wav",    "loop": false, "duration_s": 2.5 },
    "seacat_launch":   { "file": "seacat_launch.wav", "loop": false, "duration_s": 3.0 },
    "missile_track":   { "file": "tracking.wav", "loop": false, "duration_s": 2.0 },
    "hit":             { "file": "hit.wav",    "loop": false, "duration_s": 1.2 },
    "splash":          { "file": "splash.wav", "loop": false, "duration_s": 1.5 },
    "chaff":           { "file": "chaff.wav",  "loop": false, "duration_s": 1.0 },
    "flyby":           { "file": "flyby.wav",  "loop": false, "duration_s": 1.8 },
    "exocet_launch":   { "file": "exocet_launch.wav", "loop": false, "duration_s": 2.5 },
    "exocet_terminal": { "file": "exocet_terminal.wav", "loop": false, "duration_s": 2.0 }
  },
  "defaults": {
    "volume": 0.8
  }
}

Notes
- `duration_s` is required for non-looping sounds to auto-stop.
- `volume` is a 0..1 float. You can override at play() time with `gain=...`.
- If config is missing, a small built-in fallback is used so nothing crashes.

Typical integration points
- When a weapon becomes READY → play("weapon_ready")
- When firing:
    gun → play("gun_fire")
    seacat → play("seacat_launch"); schedule("missile_track", +1.0)
    exocet → play("exocet_launch"); schedule("missile_track", +2.0); schedule("exocet_terminal", +10.0)
- On result: hit → play("hit"); miss → play("splash")
- On chaff → play("chaff")
- On close overflight → play("flyby")
- Ambient loop: call play("bridge_ambience") once on start; it loops

UI usage
- Include `audio` in your `/api/status` payload: `audio.snapshot()`.
- The snapshot lists active and scheduled sounds with absolute/remaining times.
- Frontend: ensure each active `sound_id+started_at` has an <audio> element.
"""

from __future__ import annotations
import time, json, itertools
from pathlib import Path
from typing import Any, Dict, List, Optional

def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

class AudioManager:
    def __init__(self, data_path: Path):
        self.data_path = data_path
        self.cfg = self._load_cfg()

        # playing: list of dicts {id, sound_id, file, loop, volume, started_at, ends_at?}
        self.playing: List[Dict[str, Any]] = []

        # scheduled: list of dicts {sound_id, file, loop, volume, start_at, duration_s?}
        self.scheduled: List[Dict[str, Any]] = []

        # last_played: sound_id -> timestamp (for cooldown)
        self.last_played: Dict[str, float] = {}

        # unique id counter for play instances
        self._uid = itertools.count(1)

    # ---------- config

    def _load_cfg(self) -> Dict[str, Any]:
        p = self.data_path / "audio_config.json"
        if not p.exists():
            # Fallback minimal config so the system is usable out of the box
            return {
                "sounds": {
                    "bridge_ambience": {"file": "bridge.wav", "loop": True, "volume": 0.25},
                    "weapon_ready": {"file": "beep.wav", "loop": False, "duration_s": 1.0, "volume": 0.9},
                    "gun_fire": {"file": "gun.wav", "loop": False, "duration_s": 2.5},
                    "seacat_launch": {"file": "seacat_launch.wav", "loop": False, "duration_s": 3.0},
                    "missile_track": {"file": "tracking.wav", "loop": False, "duration_s": 2.0},
                    "hit": {"file": "hit.wav", "loop": False, "duration_s": 1.2},
                    "splash": {"file": "splash.wav", "loop": False, "duration_s": 1.5},
                    "chaff": {"file": "chaff.wav", "loop": False, "duration_s": 1.0},
                    "flyby": {"file": "flyby.wav", "loop": False, "duration_s": 1.8},
                    "exocet_launch": {"file": "exocet_launch.wav", "loop": False, "duration_s": 2.5},
                    "exocet_terminal": {"file": "exocet_terminal.wav", "loop": False, "duration_s": 2.0},
                },
                "defaults": {"volume": 0.8}
            }
        try:
            return _read_json(p)
        except Exception:
            # Safe fallback if JSON is malformed
            return {"sounds": {}, "defaults": {"volume": 0.8}}

    def _sound_def(self, sound_id: str) -> Optional[Dict[str, Any]]:
        s = (self.cfg.get("sounds") or {}).get(sound_id)
        if not s:
            return None
        # normalize fields
        return {
            "file": s.get("file"),
            "loop": bool(s.get("loop", False)),
            "duration_s": float(s["duration_s"]) if "duration_s" in s else None,
            "volume": float(s.get("volume", self.cfg.get("defaults", {}).get("volume", 0.8)))
        }

    # ---------- core controls

    def play(self, sound_id: str, now: Optional[float] = None, *,
             replace: bool = False, cooldown_s: float = 0.0, gain: Optional[float] = None) -> bool:
        """
        Start (or queue) a sound immediately.
        - replace=True: stops any currently playing instance(s) of the same sound_id.
        - cooldown_s: ignore play if the same sound_id was started within this window.
        - gain: override volume (0..1) for this instance only.
        Returns True if started.
        """
        t = now or time.time()
        sdef = self._sound_def(sound_id)
        if not sdef or not sdef.get("file"):
            return False

        # cooldown
        last = self.last_played.get(sound_id)
        if last is not None and (t - last) < max(0.0, float(cooldown_s)):
            return False

        if replace:
            self.stop(sound_id, now=t)

        vol = float(gain) if gain is not None else float(sdef.get("volume", 0.8))
        loop = bool(sdef.get("loop", False))
        dur = sdef.get("duration_s")

        item = {
            "id": next(self._uid),
            "sound_id": sound_id,
            "file": sdef["file"],
            "loop": loop,
            "volume": max(0.0, min(1.0, vol)),
            "started_at": float(t),
            # For loops we leave ends_at=None; UI should loop it client-side.
            "ends_at": (float(t) + float(dur)) if (dur and not loop) else None,
        }
        self.playing.append(item)
        self.last_played[sound_id] = t
        return True

    def stop(self, sound_id: str, now: Optional[float] = None) -> int:
        """Stop all currently playing instances for a sound_id. Returns #stopped."""
        count_before = len(self.playing)
        self.playing = [p for p in self.playing if p.get("sound_id") != sound_id]
        return count_before - len(self.playing)

    def schedule(self, sound_id: str, start_in_s: float, now: Optional[float] = None, *,
                 gain: Optional[float] = None) -> bool:
        """Schedule a one-shot to begin in +start_in_s seconds."""
        t = now or time.time()
        sdef = self._sound_def(sound_id)
        if not sdef or not sdef.get("file"):
            return False
        vol = float(gain) if gain is not None else float(sdef.get("volume", 0.8))
        self.scheduled.append({
            "sound_id": sound_id,
            "file": sdef["file"],
            "loop": bool(sdef.get("loop", False)),
            "volume": max(0.0, min(1.0, vol)),
            "start_at": float(t) + float(max(0.0, start_in_s)),
            "duration_s": sdef.get("duration_s"),
        })
        return True

    def clear(self) -> None:
        """Hard stop everything and clear schedules."""
        self.playing.clear()
        self.scheduled.clear()
        self.last_played.clear()

    # ---------- time advance + snapshot

    def tick(self, now: Optional[float] = None) -> None:
        """Advance time, move due scheduled items into playing, drop finished one-shots."""
        t = now or time.time()

        # Promote due schedules
        due = [s for s in self.scheduled if s["start_at"] <= t]
        if due:
            still = [s for s in self.scheduled if s["start_at"] > t]
            self.scheduled = still
            for s in due:
                item = {
                    "id": next(self._uid),
                    "sound_id": s["sound_id"],
                    "file": s["file"],
                    "loop": s["loop"],
                    "volume": s["volume"],
                    "started_at": float(t),
                    "ends_at": (float(t) + float(s["duration_s"])) if (s.get("duration_s") and not s["loop"]) else None
                }
                self.playing.append(item)
                self.last_played[s["sound_id"]] = t

        # Drop finished one-shots
        if self.playing:
            self.playing = [
                p for p in self.playing
                if (p.get("loop") is True) or (p.get("ends_at") is None) or (p["ends_at"] > t)
            ]

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        """
        Returns a JSON-friendly view:
        {
          "now": 1234567.0,
          "playing": [
            {"id": 12, "sound_id":"gun_fire","file":"gun.wav","volume":0.8,"loop":false,
             "started_at": 1234567.0, "ends_at": 1234569.5}
          ],
          "scheduled": [
            {"sound_id":"missile_track","file":"tracking.wav","start_at": 1234571.0, "volume":0.8, "loop":false}
          ]
        }
        """
        t = now or time.time()
        # Note: call tick() in your engine loop; snapshot does not mutate state.
        return {
            "now": float(t),
            "playing": [
                {
                    "id": p["id"],
                    "sound_id": p["sound_id"],
                    "file": p["file"],
                    "volume": p["volume"],
                    "loop": p["loop"],
                    "started_at": p["started_at"],
                    "ends_at": p.get("ends_at"),
                } for p in self.playing
            ],
            "scheduled": [
                {
                    "sound_id": s["sound_id"],
                    "file": s["file"],
                    "start_at": s["start_at"],
                    "volume": s["volume"],
                    "loop": s["loop"],
                } for s in self.scheduled
            ]
        }