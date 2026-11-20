import pytest

from aquagrid import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh seeded DB in a tmp dir; yields its path."""
    from aquagrid import seed_data

    path = str(tmp_path / "t.sqlite")
    monkeypatch.setattr(config, "DB_PATH", path)
    seed_data.build()
    yield path
