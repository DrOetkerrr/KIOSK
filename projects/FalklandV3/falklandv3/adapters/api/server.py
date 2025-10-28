"""FastAPI app factory exposing runtime queries and commands."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.adapters.api.routers import (
    cap,
    health,
    mission,
    nav,
    radar,
    stations_nav,
    stations_radio,
    stations_radar,
    stations_engineering,
    stations_weapons,
    status,
    weapons,
)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_dep.startup()
        try:
            yield
        finally:
            runtime_dep.shutdown()

    app = FastAPI(title="Falkland V3", lifespan=lifespan)
    for router in (
        status.router,
        nav.router,
        cap.router,
        weapons.router,
        mission.router,
        health.router,
        radar.router,
        stations_nav.router,
        stations_radio.router,
        stations_radar.router,
        stations_engineering.router,
        stations_weapons.router,
    ):
        app.include_router(router)
    return app
