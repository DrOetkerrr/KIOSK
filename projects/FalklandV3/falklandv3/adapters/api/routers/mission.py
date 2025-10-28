"""Mission command endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from falklandv3.adapters.api.auth import require_api_key
from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.commands import MissionDecisionCommand
from falklandv3.api.schemas.status import StatusSnapshot
from falklandv3.services.runtime import GameRuntime


router = APIRouter(prefix="/api/mission", tags=["mission"])


@router.post("/decision", response_model=StatusSnapshot, dependencies=[Depends(require_api_key)])
def post_decision(
    cmd: MissionDecisionCommand,
    runtime: GameRuntime = Depends(runtime_dep),
) -> StatusSnapshot:
    current = runtime.snapshot()["mission"].get("decision")
    if not current or current.get("status") != "pending":
        raise HTTPException(status_code=409, detail="No mission decision pending")
    expected = str(current.get("id", ""))
    if expected and cmd.decision_id and cmd.decision_id != expected:
        raise HTTPException(status_code=409, detail="Decision id mismatch")
    try:
        snapshot = runtime.resolve_mission_decision(cmd.decision_id, cmd.choice)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StatusSnapshot.model_validate(snapshot)
