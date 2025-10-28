from pathlib import Path

from falklandv3.services.persistence import PersistenceConfig, StateRepository


def test_state_repository_write_and_read(tmp_path: Path):
    repo = StateRepository(PersistenceConfig(tmp_path))

    repo.write_json("status.json", {"ok": True})
    assert repo.read_json("status.json") == {"ok": True}

    repo.append_jsonl("events.log", [{"event": 1}, {"event": 2}])
    contents = (tmp_path / "events.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(contents) == 2
