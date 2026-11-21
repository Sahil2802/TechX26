from pathlib import Path

import pytest

from aquagrid import config, db


def test_db_path_routes_by_plant(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.sqlite"))
    a, b = Path(db.db_path("A")), Path(db.db_path("B"))
    assert a.name == "t.sqlite"
    assert b.name == "plant_b.sqlite"
    assert a.parent == b.parent
    with pytest.raises(ValueError):
        db.set_plant("C")
    assert db.current_plant() == "A"


@pytest.fixture(autouse=True)
def _reset_plant():
    yield
    db.set_plant("A")


@pytest.mark.parametrize("bad", ["C", "../x", ""])
def test_db_path_rejects_unknown_plant(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.sqlite"))
    with pytest.raises(ValueError):
        db.db_path(bad)


def test_set_plant_b_routes_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.sqlite"))
    db.set_plant("B")
    assert db.current_plant() == "B"
    conn = db.connect()
    conn.execute("CREATE TABLE x(a)")
    conn.close()
    assert (tmp_path / "plant_b.sqlite").exists()
    assert not (tmp_path / "t.sqlite").exists()


def test_init_db_b_leaves_a_untouched(tmp_path, monkeypatch):
    a = tmp_path / "t.sqlite"
    monkeypatch.setattr(config, "DB_PATH", str(a))
    a.write_bytes(b"keep")
    db.init_db("B")
    assert (tmp_path / "plant_b.sqlite").exists()
    assert a.read_bytes() == b"keep"
    with pytest.raises(ValueError):
        db.init_db("C")


def test_plant_is_isolated_per_thread(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.sqlite"))
    db.set_plant("B")
    seen = []
    t = threading.Thread(target=lambda: seen.append(db.current_plant()))
    t.start(); t.join()
    assert seen == ["A"]


def test_build_plant_b_creates_separate_file(tmp_path, monkeypatch):
    import sqlite3
    from aquagrid import seed_data
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.sqlite"))
    seed_data.build("A")
    a_bytes = (tmp_path / "t.sqlite").read_bytes()
    seed_data.build("B")
    assert (tmp_path / "plant_b.sqlite").exists()
    assert db.current_plant() == "A"
    cap = lambda p: sqlite3.connect(p).execute("SELECT capacity_m3 FROM reservoir").fetchone()[0]
    assert cap(tmp_path / "plant_b.sqlite") == 18000.0
    assert cap(tmp_path / "t.sqlite") == config.RESERVOIR_CAPACITY_M3
    assert (tmp_path / "t.sqlite").read_bytes() == a_bytes


def test_profiles_get_unknown_raises():
    from aquagrid import profiles
    with pytest.raises(ValueError):
        profiles.get("C")
    assert profiles.get().name == profiles.PROFILES["A"].name
