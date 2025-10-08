# Webdash App Factory Refactor — Design Notes

Draft outline for reshaping `projects/falklandV2/webdash.py` into an app factory with smaller, testable modules. This document anchors Phase 2 of the refactor checklist.

---

## Goals
- Provide a `create_app(config: dict | None = None) -> Flask` factory so runners/tests can build an isolated instance.
- Reduce top-level side effects (e.g., global runtime instantiation, engine thread spawning) and make imports cheaper.
- Separate responsibilities: blueprint registration, runtime binding, fallbacks, background threads, and HTTP entrypoints.
- Preserve existing routes/behaviour while refactoring in small verified slices.

## Current Pain Points
- `webdash.py` (≈1.8 K LOC) mixes Flask app creation, runtime orchestration, request handlers, and fallback logic.
- Global `GameRuntime` instantiation happens at import time; tests/runners cannot swap configuration easily.
- Fallback routes and helper functions live alongside HTTP endpoints, making it hard to reason about dependencies.
- Engine thread bootstrapping (`_ensure_engine_thread`) fires on import, complicating unit tests.

## Proposed Architecture
1. **Module Layout**
   - `projects/falklandV2/web/app.py`: holds `create_app`, blueprint registration, default config, and CLI helpers.
   - `projects/falklandV2/web/runtime.py`: wraps `GameRuntime` creation/reset, exposes hooks for tests.
   - `projects/falklandV2/web/engine_loop.py`: owns the engine thread lifecycle and background tasks.
   - `projects/falklandV2/web/fallbacks.py`: contains fallback handlers (e.g., CAP request) so they can be registered conditionally.
   - `projects/falklandV2/web/__init__.py`: lightweight shim exporting `create_app` and `runtime` helpers.

2. **Factory Contract**
   ```python
   def create_app(config: dict | None = None) -> Flask:
       app = Flask(__name__, template_folder=TPL_DIR)
       app.config.from_mapping(DEFAULTS)
       if config:
           app.config.update(config)
       runtime = runtime_module.init_runtime(app.config)
       register_blueprints(app, runtime)
       fallbacks.install(app, runtime)
       engine_loop.ensure_thread(runtime)
       return app
   ```

3. **Runtime Binding**
   - Replace global `RUNTIME = GameRuntime(...)` with a lazy getter (`runtime_module.get_runtime()`).
   - Blueprints that need runtime access should depend on a shared registry (e.g., `current_app.extensions['runtime']`).
   - Maintain compatibility for modules that import `webdash.RUNTIME` by providing a shim during transition (e.g., `projects/falklandV2/webdash/__init__.py` re-exporting runtime reference).

4. **Background Tasks**
   - Move `_ensure_engine_thread` and related helpers into `engine_loop`.
   - Hook into Flask signals (`app.before_request`, `app.teardown_appcontext`) in the factory rather than registering at module import time.

5. **Fallbacks & Diagnostics**
   - Group fallback endpoints (CAP, audio) into a single module with clear dependency injection.
   - Document which fallbacks can be removed once their real blueprints are stable.

## Incremental Task Breakdown
1. **Introduce Factory Skeleton**
   - Add new module with `create_app` returning the existing Flask `app`.
   - Update `run_falkland.sh` to call `create_app()` via `flask run` surrogate (`python -m projects.falklandV2.app`).
   - Keep legacy `app` and `RUNTIME` exports to avoid breaking imports; flag for deprecation.

2. **Runtime Extraction**
   - Move runtime instantiation/reset into `web/runtime.py`.
   - Store reference on `app.extensions['runtime']`.
   - Update blueprints/fallbacks to fetch runtime from `current_app`.

3. **Engine Thread Service**
   - Encapsulate thread management; expose `ensure_started(app)` and `shutdown()` helpers.
   - Replace global `_ensure_engine_thread` and `@app.before_request` wiring with factory-managed hooks.

4. **Fallback Module**
   - Relocate CAP fallbacks and other defensive routes.
   - Provide registration helper returning a list of `Rule` objects or callables.

5. **Clean Legacy Shims**
   - Remove direct imports of `webdash.py` from other modules once the factory is adopted.
   - Delete compatibility exports when routes/tests are updated.

6. **Testing & Verification**
   - Add unit tests for `create_app` (e.g., ensure runtime attached, blueprints registered).
   - Update `tools/verify_suite.sh` if needed to use factory entrypoint.
   - Run `make check` and the verify suite after each significant slice.

## Notes & Open Questions
- Decide whether to split blueprint registration into a manifest (list of module paths) to keep configuration declarative.
- Investigate moving audio/voice helpers into dedicated subsystem modules as part of later phases.
- Track the impact on desktop launcher — confirm it still hits the now factory-based web server.
- Determine when to retire the `main.py` bootstrap script once CLI/test entrypoints settle.
