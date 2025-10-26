# Testing Guide

## Smoke Tests

To run the backend smoke tests (requires Flask app importable) use:

```
PYTHONPATH=. pytest projects/falklandV2/tests/test_smoke.py
```

This exercises:
- `/api/status` schema conformance and required fields
- `/api/command` for `/radar scan` and `/radar unlock`

Both checks verify that the schema version matches `1.0.0`. Adjust if you bump schemas.

## Full Suite

```
PYTHONPATH=. pytest
```

Ensure the environment has any required data files and audio assets accessible. On macOS, the default python may warn about LibreSSL; consider using a venv with a modern OpenSSL build for production testing.
