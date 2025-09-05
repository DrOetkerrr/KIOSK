from __future__ import annotations
from typing import Dict, List, Optional
from pathlib import Path

from .state import FalklandsState
from .router import CommandRouter
from .io_openai import ChatIO
from .timer import GameTimer

from ..systems.nav import NavSystem
from ..systems.radar import RadarSystem
from ..systems.weapons import WeaponsSystem
from ..systems.targets import TargetsSystem
from ..systems.mapgrid import MapGridSystem

SYSTEM_PROMPT = (
    "You are ENSIGN WATTS, a calm, competent naval ensign on a Royal Navy frigate "
    "in a Falklands-era exercise. Stay concise under stress. "
    "Address the user as your commanding officer ('Sir' is fine). "
    "Report clearly, use short sentences, offer one next action when useful. "
    "You may suggest slash-commands like '/nav set heading=270 speed=18' or "
    "'/map where' when actions are needed."
)

class Engine:
    """NPC + game systems glue: routes /commands and chats via OpenAI."""
    def __init__(self, state_path: Path, model: str = "gpt-4.1-mini"):
        self.st = FalklandsState(state_path).load()
        self.router = CommandRouter()
        self.io = ChatIO(model=model)
        self._nav_sys: Optional[NavSystem] = None
        self.timer = GameTimer(self._tick)   # background game timer
        self._register_systems()

    # --------- system wiring & command registration ---------
    def _register_systems(self):
        # Instantiate systems with shared state
        nav = NavSystem(self.st)
        self._nav_sys = nav
        radar = RadarSystem(self.st)
        weapons = WeaponsSystem(self.st)
        targets = TargetsSystem(self.st)
        mapgrid = MapGridSystem(self.st)

        # NAV
        self.router.register("nav", "show", nav.show)
        self.router.register("nav", "set",  nav.set)
        if hasattr(nav, "advance"): self.router.register("nav", "advance", nav.advance)
        if hasattr(nav, "goto"):    self.router.register("nav", "goto",    nav.goto)

        # RADAR
        self.router.register("radar", "show", radar.show)
        self.router.register("radar", "add",  radar.add_contact)

        # WEAPONS
        self.router.register("weapons", "show",   weapons.show)
        self.router.register("weapons", "arm",    weapons.arm)
        self.router.register("weapons", "safe",   weapons.safe)
        self.router.register("weapons", "select", weapons.select)

        # TARGETS
        self.router.register("targets", "list", targets.list)
        self.router.register("targets", "add",  targets.add)

        # MAP
        self.router.register("map", "place",  mapgrid.place)
        self.router.register("map", "where",  mapgrid.where)
        self.router.register("map", "show",   mapgrid.show)

        # TICK (single-step sim advance)
        self.router.register("tick", "run", self._cmd_tick)

        # TIMER (background ticking)
        self.router.register("timer", "start",  self._cmd_timer_start)
        self.router.register("timer", "stop",   self._cmd_timer_stop)
        self.router.register("timer", "status", self._cmd_timer_status)

    # --------- public chat entrypoint ---------
    def ask(self, user_text: str) -> str:
        """If user_text is a /command, execute it. Otherwise chat via OpenAI and maybe execute a suggested /command."""
        # 1) Direct command path
        routed = self.router.handle(user_text) if user_text.startswith("/") else None
        if routed is not None:
            self._log("user", user_text); self._log("assistant", routed); self.st.save()
            return routed

        # Build context so Ensign knows the sector & nav state
        m = self.st.data.get("map", {})
        n = self.st.data.get("nav", {})
        sector = f"{m.get('col','?')}-{int(m.get('row',0)):02d}" if m else "unknown"
        nav_brief = f"lat {n.get('lat','?')} lon {n.get('lon','?')} hdg {n.get('heading','?')} spd {n.get('speed','?')} kn"

        msgs: List[Dict[str, str]] = (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + [{"role": "system", "content": f"Current sector: {sector}. Nav: {nav_brief}."}]
            + self.st.history
            + [{"role": "user", "content": user_text}]
        )

        stream = self.io.stream(msgs)
        buf: List[str] = []
        for ev in stream:
            delta = ev.choices[0].delta
            if delta and (tok := delta.get("content")):
                buf.append(tok)
        reply = "".join(buf).strip()

        executed = self._maybe_exec_first_command_in(reply)
        if executed:
            reply += f"\n\n[Executed] {executed}"

        self._log("user", user_text); self._log("assistant", reply); self.st.save()
        return reply

    # --------- helpers ---------
    def _log(self, role: str, content: str):
        self.st.add_message(role, content)

    def _maybe_exec_first_command_in(self, text: str) -> Optional[str]:
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("/"):
                return self.router.handle(s)
        return None

    # --------- tick command (single-step) ---------
    def _cmd_tick(self, args):
        try:
            dt = float(args.get("dt", "60"))
        except ValueError:
            return "TICK: invalid dt"

        if self._nav_sys and hasattr(self._nav_sys, "advance"):
            out = self._nav_sys.advance({"dt": str(dt)})
        else:
            out = self.router.handle(f"/nav advance dt={int(dt)}")

        self.st.save()
        return f"TICK +{int(dt)}s -> {out}"

    # --------- timer commands (background) ---------
    def _cmd_timer_start(self, args):
        try:
            interval = float(args.get("interval", "2"))
            dt = float(args.get("dt", "30"))
        except ValueError:
            return "TIMER: invalid interval/dt"
        self.timer.start(interval_s=interval, dt_s=dt)
        cfg = self.timer.config()
        return f"TIMER: started (interval {cfg['interval']}s, dt {cfg['dt']}s)"

    def _cmd_timer_stop(self, args):
        self.timer.stop()
        return "TIMER: stopped"

    def _cmd_timer_status(self, args):
        run = self.timer.is_running()
        cfg = self.timer.config()
        return f"TIMER: {'running' if run else 'stopped'} (interval {cfg['interval']}s, dt {cfg['dt']}s)"

    # --------- background tick callback ---------
    def _tick(self, dt_s: float):
        try:
            if self._nav_sys and hasattr(self._nav_sys, "advance"):
                out = self._nav_sys.advance({"dt": str(dt_s)})
                self.st.save()
                print(f"\n[TICK +{int(dt_s)}s] {out}")
                print("You: ", end="", flush=True)
        except Exception as e:
            print(f"[TICK] error: {e}")