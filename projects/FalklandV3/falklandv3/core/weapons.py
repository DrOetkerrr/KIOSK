"""Weapons inventory and readiness modelling for Falkland V3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple


@dataclass
class WeaponSlot:
    """Mutable weapon slot state."""

    name: str
    state: str
    ammo: int
    max_ammo: int
    min_range_nm: Optional[float]
    max_range_nm: Optional[float]
    supports: Tuple[str, ...]
    ammo_per_shot: int = 1
    category: str = ""

    def copy(self) -> "WeaponSlot":
        return WeaponSlot(
            name=self.name,
            state=self.state,
            ammo=self.ammo,
            max_ammo=self.max_ammo,
            min_range_nm=self.min_range_nm,
            max_range_nm=self.max_range_nm,
            supports=tuple(self.supports),
            ammo_per_shot=self.ammo_per_shot,
            category=self.category,
        )

    @property
    def armed(self) -> bool:
        return self.state.lower() == "armed"

    def set_state(self, state: str) -> None:
        if state:
            self.state = state.title()

    def set_ammo(self, value: int) -> None:
        self.ammo = max(0, min(int(value), self.max_ammo))

    def consume_ammo(self, bursts: int = 1) -> bool:
        cost = max(1, bursts) * max(1, self.ammo_per_shot)
        if self.ammo < cost:
            return False
        self.ammo -= cost
        return True


def _slot(
    name: str,
    *,
    state: str = "Safe",
    ammo: int,
    max_ammo: int,
    min_range_nm: Optional[float],
    max_range_nm: Optional[float],
    supports: Iterable[str],
    ammo_per_shot: int = 1,
    category: str = "",
) -> WeaponSlot:
    return WeaponSlot(
        name=name,
        state=state,
        ammo=ammo,
        max_ammo=max_ammo,
        min_range_nm=min_range_nm,
        max_range_nm=max_range_nm,
        supports=tuple(supports),
        ammo_per_shot=max(1, ammo_per_shot),
        category=category,
    )


DEFAULT_WEAPON_SLOTS: Tuple[WeaponSlot, ...] = (
    _slot(
        "MM38 Exocet",
        ammo=4,
        max_ammo=4,
        min_range_nm=8.0,
        max_range_nm=22.0,
        supports=("Ship",),
        category="Missile",
    ),
    _slot(
        "Sea Dart Fwd",
        ammo=13,
        max_ammo=13,
        min_range_nm=2.0,
        max_range_nm=35.0,
        supports=("Aircraft", "Ship"),
        category="SAM",
    ),
    _slot(
        "Sea Dart Aft",
        ammo=13,
        max_ammo=13,
        min_range_nm=2.0,
        max_range_nm=35.0,
        supports=("Aircraft", "Ship"),
        category="SAM",
    ),
    _slot(
        "4.5 inch Mk.8 gun",
        ammo=550,
        max_ammo=550,
        min_range_nm=1.0,
        max_range_nm=8.0,
        supports=("Ship", "Shore"),
        ammo_per_shot=1,
        category="Gun",
    ),
    _slot(
        "20mm Oerlikon",
        ammo=500,
        max_ammo=500,
        min_range_nm=0.25,
        max_range_nm=0.5,
        supports=("Aircraft", "SmallCraft"),
        ammo_per_shot=50,
        category="CIWS",
    ),
    _slot(
        "20mm GAM-BO1 (twin)",
        ammo=500,
        max_ammo=500,
        min_range_nm=0.3,
        max_range_nm=2.5,
        supports=("Aircraft", "SmallCraft"),
        ammo_per_shot=50,
        category="CIWS",
    ),
    _slot(
        "Corvus chaff",
        ammo=15,
        max_ammo=15,
        min_range_nm=0.0,
        max_range_nm=1.0,
        supports=("Aircraft",),
        ammo_per_shot=1,
        category="Decoy",
    ),
)


class WeaponInventory:
    """Tracks weapon readiness, ammunition, and engagement envelopes."""

    def __init__(
        self,
        state_overrides: Mapping[str, str] | None = None,
        ammo_overrides: Mapping[str, int] | None = None,
    ) -> None:
        self._slots: Dict[str, WeaponSlot] = {
            slot.name: slot.copy() for slot in DEFAULT_WEAPON_SLOTS
        }
        self._order: Tuple[str, ...] = tuple(slot.name for slot in DEFAULT_WEAPON_SLOTS)
        if state_overrides:
            for name, state in state_overrides.items():
                slot = self._get_slot(name)
                if slot:
                    slot.set_state(state)
        if ammo_overrides:
            for name, amount in ammo_overrides.items():
                slot = self._get_slot(name)
                if slot:
                    slot.set_ammo(amount)

    # ----- public surface -------------------------------------------------
    def slots(self) -> Dict[str, WeaponSlot]:
        """Return a name→slot copy suitable for snapshotting."""

        return {name: self._slots[name].copy() for name in self._order}

    def arm(self, name: str) -> Optional[str]:
        slot = self._get_slot(name)
        if slot is None:
            return None
        slot.set_state("Armed")
        return slot.name

    def safe(self, name: str) -> Optional[str]:
        slot = self._get_slot(name)
        if slot is None:
            return None
        slot.set_state("Safe")
        return slot.name

    def ammo(self, name: str) -> Optional[int]:
        slot = self._get_slot(name)
        return slot.ammo if slot else None

    def set_ammo(self, name: str, value: int) -> None:
        slot = self._get_slot(name)
        if slot:
            slot.set_ammo(value)

    def adjust_ammo(self, name: str, delta: int) -> None:
        slot = self._get_slot(name)
        if slot:
            slot.set_ammo(slot.ammo + delta)

    def is_armed(self, name: str) -> bool:
        slot = self._get_slot(name)
        return slot.armed if slot else False

    def fire(self, name: str, *, bursts: int = 1) -> bool:
        """Consume ammunition for a fire event."""

        slot = self._get_slot(name)
        if slot is None:
            return False
        return slot.consume_ammo(bursts)

    def slot(self, name: str) -> Optional[WeaponSlot]:
        slot = self._get_slot(name)
        return slot.copy() if slot else None

    def _get_slot(self, name: str) -> Optional[WeaponSlot]:
        if not name:
            return None
        needle = name.strip().lower()
        for key, slot in self._slots.items():
            if key.lower() == needle:
                return slot
        return self._slots.get(name.strip())
