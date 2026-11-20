from aquagrid import config, db as dbm, seed_data

SCENARIOS = ["baseline", "peak_2pm", "s4_drift", "attack"]


def _series(conn, scenario, sensor):
    return [r[0] for r in conn.execute(
        "SELECT value FROM telemetry WHERE scenario=? AND sensor_id=? ORDER BY ts", (scenario, sensor))]


def test_build_is_deterministic(tmp_path, monkeypatch):
    out = []
    for name in ("a", "b"):
        monkeypatch.setattr(config, "DB_PATH", str(tmp_path / f"{name}.sqlite"))
        seed_data.build()
        out.append(_series(dbm.connect(ro=True), "peak_2pm", "P-J17"))
    assert out[0] == out[1] and len(out[0]) == 121


def test_peak_p_j07_impossible(db):
    assert _series(dbm.connect(ro=True), "peak_2pm", "P-J07")[-1] == -12.0


def test_peak_intake_turbidity_flatlined_at_042(db):
    s = _series(dbm.connect(ro=True), "peak_2pm", "T-INTAKE")
    assert len(set(s[-15:])) == 1 and s[-1] == 0.42


def test_peak_leak_visible_on_s4_pressures_and_flow(db):
    c = dbm.connect(ro=True)
    for sensor, drop in (("P-J17", 14), ("P-J18", 9), ("P-J16", 4)):
        s = _series(c, "peak_2pm", sensor)
        assert abs((sum(s[:60]) / 60 - s[-1]) - drop) < 1.5
    f = _series(c, "peak_2pm", "F-S4")
    assert f[-1] - sum(f[:60]) / 60 > 35


def test_s4_drift_ramps_then_flatlines_at_37(db):
    s = _series(dbm.connect(ro=True), "s4_drift", "P-J17")
    assert s[-15:] == [37.0] * 15
    assert s[89] - s[105] > 5  # idx 89 = 13:29, idx 105 = 13:45: fell ~0.5 m/min from 13:30


def test_injection_text_only_in_attack(db):
    c = dbm.connect(ro=True)
    for sc in SCENARIOS:
        n = c.execute("SELECT count(*) FROM quality_alerts WHERE scenario=? AND message LIKE '%IGNORE PREVIOUS%'",
                      (sc,)).fetchone()[0]
        assert n == (1 if sc == "attack" else 0)


def test_baseline_has_no_alerts_and_no_faults(db):
    c = dbm.connect(ro=True)
    assert c.execute("SELECT count(*) FROM quality_alerts WHERE scenario='baseline'").fetchone()[0] == 0
    assert min(_series(c, "baseline", "P-J07")) > 30


def test_topology_s4_loop_keeps_hospital_supplied_but_inlet_does_not(db):
    from collections import deque
    c = dbm.connect(ro=True)
    pipes = [(r["id"], r["from_node"], r["to_node"], r["valve_id"]) for r in c.execute("SELECT * FROM pipes")]

    def reachable(closed_valves):
        adj = {}
        for _, a, b, v in pipes:
            if v in closed_valves:
                continue
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        seen, q = {"R1"}, deque(["R1"])
        while q:
            for n in adj.get(q.popleft(), []):
                if n not in seen:
                    seen.add(n)
                    q.append(n)
        return seen

    assert "J19" in reachable({"V-S4-17-18"})
    assert "J19" not in reachable({"V-S4-IN"})


def test_reset_state_restores_valves_and_r1(db):
    conn = dbm.connect()
    conn.execute("UPDATE valves SET position_pct=0")
    seed_data.set_scenario(conn, "s4_drift")
    assert conn.execute("SELECT min(position_pct) FROM valves").fetchone()[0] == 100
    assert conn.execute("SELECT level_pct FROM reservoir").fetchone()[0] == 60.0
