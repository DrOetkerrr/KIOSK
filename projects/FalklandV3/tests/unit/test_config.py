from pathlib import Path

from falklandv3.config import Settings, load_settings


def test_load_settings_defaults():
    settings = load_settings(env={})
    assert settings == Settings()


def test_load_settings_env_overrides(tmp_path):
    env = {
        "FALKLANDV3_TICK_DT": "2.5",
        "FALKLANDV3_LOOP_SLEEP": "0.2",
        "FALKLANDV3_AUDIO_MAX_EVENTS": "25",
        "FALKLANDV3_LOG_DIR": str(tmp_path / "logs"),
        "FALKLANDV3_BUILD": "test-build",
        "FALKLANDV3_API_KEY": "abc123",
        "FALKLANDV3_RNG_SEED": "777",
    }
    settings = load_settings(env=env)
    assert settings.tick_dt == 2.5
    assert settings.loop_sleep == 0.2
    assert settings.audio_max_events == 25
    assert settings.log_dir == Path(env["FALKLANDV3_LOG_DIR"])
    assert settings.build_label == "test-build"
    assert settings.api_key == "abc123"
    assert settings.rng_seed == 777
