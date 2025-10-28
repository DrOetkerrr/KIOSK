import os
import subprocess
import sys

import pytest

uvicorn = pytest.importorskip("uvicorn")


def test_serve_cli_invokes_uvicorn(tmp_path, monkeypatch):
    called = {}

    class DummyServer:
        def __init__(self, config):
            called["server_config"] = config

        def run(self):
            called["run"] = True
            return 0

    class DummyConfig:
        def __init__(self, *args, **kwargs):
            called["config_args"] = (args, kwargs)

    monkeypatch.setattr(uvicorn, "Config", DummyConfig)
    monkeypatch.setattr(uvicorn, "Server", DummyServer)

    from falklandv3.cli import serve_api

    result = serve_api.main(["--host", "127.0.0.1", "--port", "9000"])
    assert result == 0
    assert called.get("run") is True
