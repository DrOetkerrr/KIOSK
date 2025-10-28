"""Lightweight radar simulator for Falkland V3."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from typing import List, Optional, Sequence

from falklandv3.data.catalog import ContactCatalog, ContactType
from falklandv3.data.waves import AttackWave, SpawnOption, WaveSchedule
from falklandv3.utils.grid import world_to_label

WORLD_SIZE_NM = 40.0


def clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def bearing_between(ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = ay - by  # screen Y increases southwards
    angle = math.degrees(math.atan2(dx, dy))
    return (angle + 360.0) % 360.0


def distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


@dataclass(frozen=True)
class RadarContact:
    """Single radar track."""

    id: int
    name: str
    allegiance: str  # "Friendly" | "Hostile"
    x_nm: float
    y_nm: float
    heading_deg: float
    speed_kts: float
    category: Optional[str] = None
    primary_weapon: Optional[str] = None
    min_range_nm: Optional[float] = None
    max_range_nm: Optional[float] = None

    def strike_range_nm(self, fallback: float) -> float:
        """Return engagement radius for this contact."""

        strike = fallback
        cat = (self.category or "").lower()
        weapon = (self.primary_weapon or "").lower()

        if self.min_range_nm is not None and self.min_range_nm > 0:
            if cat in {"missile", "ship"} or "exocet" in weapon:
                strike = max(fallback, self.min_range_nm)
            else:
                strike = min(fallback, self.min_range_nm)
        elif self.max_range_nm is not None:
            if cat in {"missile", "ship"} or "exocet" in weapon:
                strike = max(fallback, self.max_range_nm)
            else:
                strike = min(fallback, self.max_range_nm)

        if "exocet" in weapon:
            upper = self.max_range_nm if self.max_range_nm is not None else strike
            if upper is not None:
                strike = max(strike, min(upper, 22.0))

        return max(strike, 0.25)

    def tick(self, dt_seconds: float) -> "RadarContact":
        if dt_seconds <= 0 or self.speed_kts <= 0:
            return self
        nm = self.speed_kts * (dt_seconds / 3600.0)
        radians = math.radians(self.heading_deg)
        dx = math.sin(radians) * nm
        dy = -math.cos(radians) * nm
        return replace(
            self,
            x_nm=clamp(self.x_nm + dx, 0.0, WORLD_SIZE_NM),
            y_nm=clamp(self.y_nm + dy, 0.0, WORLD_SIZE_NM),
        )


@dataclass(frozen=True)
class RadarContactView:
    id: int
    label: str
    allegiance: str
    range_nm: float
    bearing_deg: float
    heading_deg: float
    speed_kts: float
    category: Optional[str]
    primary_weapon: Optional[str]
    cell: str


class RadarSimulator:
    """Generates and updates radar contacts near the player ship."""

    def __init__(
        self,
        *,
        rng: random.Random | None = None,
        max_contacts: int = 8,
        spawn_interval_s: float = 45.0,
        catalog: Optional[ContactCatalog] = None,
        wave_schedule: Optional[WaveSchedule] = None,
    ) -> None:
        self._rng = rng or random.Random()
        self._max_contacts = max_contacts
        self._spawn_interval_s = spawn_interval_s
        self._spawn_cooldown_s = spawn_interval_s
        self._contacts: List[RadarContact] = []
        self._next_id = 1
        self._catalog = catalog
        self._wave_schedule = wave_schedule
        self._elapsed_total = 0.0

    # ----- public surface -------------------------------------------------
    @property
    def contacts(self) -> Sequence[RadarContact]:
        return tuple(self._contacts)

    @property
    def max_contacts(self) -> int:
        return self._max_contacts

    def ensure_seed_contacts(self, own_x: float, own_y: float) -> None:
        if self._contacts:
            return
        slots = max(0, min(3, self._max_contacts))
        for _ in range(slots):
            if len(self._contacts) >= self._max_contacts:
                break
            self._spawn_random_contact(own_x, own_y, allegiance="Friendly")

    def intercept_hostile(self, own_x: float, own_y: float) -> Optional[RadarContact]:
        hostiles = [
            contact for contact in self._contacts if contact.allegiance.lower() == "hostile"
        ]
        if not hostiles:
            return None
        target = min(
            hostiles,
            key=lambda contact: distance(own_x, own_y, contact.x_nm, contact.y_nm),
        )
        self._contacts = [contact for contact in self._contacts if contact.id != target.id]
        return target

    def tick(self, dt_seconds: float, own_x: float, own_y: float) -> None:
        dt = max(0.0, float(dt_seconds))
        self._elapsed_total += dt
        self._contacts = [contact.tick(dt) for contact in self._contacts]
        self._contacts = [c for c in self._contacts if self._in_bounds(c)]
        wave = self._current_wave()
        if wave and len(self._contacts) < self._max_contacts and wave.spawn_rate_per_min > 0:
            probability = wave.spawn_rate_per_min * (dt / 60.0)
            if self._rng.random() < probability:
                allegiance = "Friendly" if self._rng.random() < wave.friendly_prob else "Hostile"
                self._spawn_random_contact(own_x, own_y, allegiance=allegiance, wave=wave)
        self._spawn_cooldown_s -= dt
        if (
            self._spawn_cooldown_s <= 0.0
            and len(self._contacts) < self._max_contacts
        ):
            self._spawn_random_contact(own_x, own_y, allegiance="Hostile", wave=wave)
            self._spawn_cooldown_s = self._spawn_interval_s

    def force_spawn(
        self,
        *,
        name: str,
        allegiance: str,
        x_nm: float,
        y_nm: float,
        heading_deg: float,
        speed_kts: float,
        category: Optional[str] = None,
        primary_weapon: Optional[str] = None,
        min_range_nm: Optional[float] = None,
        max_range_nm: Optional[float] = None,
    ) -> RadarContact:
        contact = RadarContact(
            id=self._next_id,
            name=name,
            allegiance=allegiance,
            x_nm=clamp(x_nm, 0.0, WORLD_SIZE_NM),
            y_nm=clamp(y_nm, 0.0, WORLD_SIZE_NM),
            heading_deg=float(heading_deg % 360.0),
            speed_kts=max(0.0, float(speed_kts)),
            category=category,
            primary_weapon=primary_weapon,
            min_range_nm=min_range_nm,
            max_range_nm=max_range_nm,
        )
        self._contacts.append(contact)
        self._next_id += 1
        return contact

    def views(self, own_x: float, own_y: float) -> List[RadarContactView]:
        entries: List[RadarContactView] = []
        for contact in self._contacts:
            rng_nm = distance(own_x, own_y, contact.x_nm, contact.y_nm)
            bearing = bearing_between(own_x, own_y, contact.x_nm, contact.y_nm)
            entries.append(
                RadarContactView(
                    id=contact.id,
                    label=contact.name,
                    allegiance=contact.allegiance,
                    range_nm=rng_nm,
                    bearing_deg=bearing,
                    heading_deg=contact.heading_deg,
                    speed_kts=contact.speed_kts,
                    category=contact.category,
                    primary_weapon=contact.primary_weapon,
                    cell=world_to_label(contact.x_nm, contact.y_nm),
                )
            )
        entries.sort(key=lambda item: item.range_nm)
        return entries

    # ----- internals ------------------------------------------------------
    def _spawn_random_contact(self, own_x: float, own_y: float, allegiance: str, wave: Optional[AttackWave] = None) -> None:
        bearing = wave.direction_bearing if wave else self._rng.uniform(0.0, 360.0)
        bearing += self._rng.uniform(-15.0, 15.0)
        range_min = 12.0
        option = self._select_option(wave.options) if wave and wave.options else None
        contact_type = self._pick_contact(allegiance, wave, option)
        if option and option.min_range_nm is not None:
            range_min = max(range_min, option.min_range_nm)
        range_nm = self._rng.uniform(range_min, range_min + 10.0)
        rad = math.radians(bearing)
        x_nm = clamp(own_x + math.sin(rad) * range_nm, 0.0, WORLD_SIZE_NM)
        y_nm = clamp(own_y - math.cos(rad) * range_nm, 0.0, WORLD_SIZE_NM)
        heading_deg = (bearing + 180.0 + self._rng.uniform(-20.0, 20.0)) % 360.0
        speed_kts = self._rng.uniform(180.0, 420.0)
        name = "Patrol" if allegiance == "Friendly" else "Bogey"
        category = None
        primary_weapon = None
        min_range_nm = None
        max_range_nm = None
        if contact_type is not None:
            name = contact_type.name or name
            if contact_type.speed_kts > 0:
                speed_kts = contact_type.speed_kts
            category = contact_type.category or contact_type.klass
            primary_weapon = contact_type.primary_weapon
            min_range_nm = contact_type.min_range_nm
            max_range_nm = contact_type.max_range_nm
        self.force_spawn(
            name=name,
            allegiance=allegiance,
            x_nm=x_nm,
            y_nm=y_nm,
            heading_deg=heading_deg,
            speed_kts=speed_kts,
            category=category,
            primary_weapon=primary_weapon,
            min_range_nm=min_range_nm,
            max_range_nm=max_range_nm,
        )

    def _in_bounds(self, contact: RadarContact) -> bool:
        margin = 1.0
        return (
            margin <= contact.x_nm <= WORLD_SIZE_NM - margin
            and margin <= contact.y_nm <= WORLD_SIZE_NM - margin
        )

    def consume_hostile_within_range(self, own_x: float, own_y: float, max_range_nm: float) -> Optional[RadarContact]:
        hostiles = [contact for contact in self._contacts if contact.allegiance.lower() == "hostile"]
        if not hostiles:
            return None
        target: Optional[RadarContact] = None
        target_range = float("inf")
        for contact in hostiles:
            strike_range = contact.strike_range_nm(max_range_nm)
            rng_nm = distance(own_x, own_y, contact.x_nm, contact.y_nm)
            if rng_nm <= strike_range and rng_nm < target_range:
                target = contact
                target_range = rng_nm
        if target is None:
            return None
        self._contacts = [contact for contact in self._contacts if contact.id != target.id]
        return target

    def _pick_contact(self, allegiance: str, wave: Optional[AttackWave], option: Optional[SpawnOption]) -> Optional[ContactType]:
        if self._catalog is None:
            return None
        if option is not None and allegiance.lower() == "hostile":
            contact = self._catalog.get(option.name)
            if contact is not None:
                return contact
        candidates = self._catalog.items(allegiance)
        if not candidates:
            return None
        total = sum(item.weight for item in candidates)
        if total <= 0:
            return self._rng.choice(candidates)
        target = self._rng.uniform(0, total)
        accum = 0.0
        for item in candidates:
            accum += item.weight
            if target <= accum:
                return item
        return candidates[-1]

    def _select_option(self, options: List[SpawnOption]) -> Optional[SpawnOption]:
        total = sum(option.chance for option in options)
        if total <= 0:
            return None
        pick = self._rng.uniform(0, total)
        accum = 0.0
        for option in options:
            accum += option.chance
            if pick <= accum:
                return option
        return options[-1]

    def _current_wave(self) -> Optional[AttackWave]:
        if self._wave_schedule is None:
            return None
        return self._wave_schedule.current(self._elapsed_total)

    def wave_summary(self) -> Optional[dict]:
        if self._wave_schedule is None:
            return None
        progress = self._wave_schedule.progress(self._elapsed_total)
        if progress is None:
            return None
        wave, elapsed = progress
        remaining = wave.duration_s - elapsed if wave.duration_s > 0 else None
        return {
            "label": wave.label,
            "elapsed_s": max(0.0, elapsed),
            "duration_s": wave.duration_s,
            "remaining_s": max(0.0, remaining) if remaining is not None else None,
            "spawn_rate_per_min": wave.spawn_rate_per_min,
            "friendly_prob": wave.friendly_prob,
            "direction_bearing": wave.direction_bearing,
        }
