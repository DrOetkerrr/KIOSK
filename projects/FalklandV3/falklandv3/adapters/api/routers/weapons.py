"""Weapon endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.commands import WeaponCommand, WeaponFireCommand
from falklandv3.api.schemas.status import StatusSnapshot
from falklandv3.services.runtime import GameRuntime


router = APIRouter(prefix="/api/weapons", tags=["weapons"])


@router.post("/arm", response_model=StatusSnapshot)
def post_weapon_arm(cmd: WeaponCommand, runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    runtime.arm_weapon(cmd.name)
    return StatusSnapshot.model_validate(runtime.snapshot())


@router.post("/safe", response_model=StatusSnapshot)
def post_weapon_safe(cmd: WeaponCommand, runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    runtime.safe_weapon(cmd.name)
    return StatusSnapshot.model_validate(runtime.snapshot())


@router.post("/fire", response_model=StatusSnapshot)
def post_weapon_fire(cmd: WeaponFireCommand, runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    result = runtime.fire_weapon(cmd.name, mode=cmd.mode)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "Fire failed"))
    return StatusSnapshot.model_validate(runtime.snapshot())
