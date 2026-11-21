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
