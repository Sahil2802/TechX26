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


@pytest.fixture(autouse=True)
def _no_tokens(monkeypatch):
    """A developer's .env tokens must not leak into tests; tests that need tokens set them afterwards."""
    for name in ("ADMIN_TOKEN", "PLANT_A_TOKEN", "PLANT_B_TOKEN", "OPERATOR_TOKEN"):
        monkeypatch.setattr(config, name, None)
