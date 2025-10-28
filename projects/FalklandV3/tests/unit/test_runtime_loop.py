import time

from falklandv3.services.runtime import GameRuntime
from falklandv3.services.runtime_loop import LoopConfig, RuntimeLoop


def test_runtime_loop_ticks_and_stops():
    runtime = GameRuntime()
    loop = RuntimeLoop(runtime, LoopConfig(dt_seconds=0.01, stop_after_ticks=3))

    loop.start()
    time.sleep(0.05)
    loop.stop(timeout=1.0)

    assert loop.ticks() >= 3
    snap = runtime.snapshot()
    assert snap["ship"]["hud"].startswith("Ship")


def test_runtime_loop_stop_manual():
    runtime = GameRuntime()
    loop = RuntimeLoop(runtime, LoopConfig(dt_seconds=0.01))
    loop.start()
    time.sleep(0.05)
    loop.stop(timeout=1.0)
    assert loop.ticks() > 0
