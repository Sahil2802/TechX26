"""AquaGrid operator console. Run with: streamlit run app.py

CODE decides what is true and safe, the LLM reads and explains, a HUMAN approves every valve or setpoint
change. This page calls the gateway and agent modules directly; the agent itself can never approve.
"""
import hmac
from pathlib import Path

import pandas as pd
import streamlit as st

from aquagrid import agent, config, db, gateway, llm, seed_data, view

st.set_page_config(page_title="AquaGrid Operator Console", page_icon="💧", layout="wide")
st.markdown("""<style>
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.78rem;font-weight:700;
      letter-spacing:.05em;margin-right:6px}
.p-code{background:#0f766e;color:#ecfeff}.p-llm{background:#6d28d9;color:#f5f3ff}
.p-human{background:#b45309;color:#fffbeb}
div[data-testid="stMetricValue"]{font-size:1.55rem}
</style>""", unsafe_allow_html=True)

WHO_ICON = {"CODE": "🟦 CODE", "LLM": "🟪 LLM", "HUMAN": "🟧 HUMAN", "ATTACKER": "🟥 ATTACKER"}


# ---- actions (the only writes this page performs) -------------------------------------------------

def load_scenario(scenario: str) -> None:
    conn = db.connect()
    try:
        seed_data.set_scenario(conn, scenario)
    finally:
        conn.close()
    st.session_state.pop("agent_run", None)


def run_agent(scenario: str, scripted: bool) -> None:
    load_scenario(scenario)
    with st.spinner("Agent running: reading the network through its tools, staging proposals..."):
        st.session_state["agent_run"] = agent.run_scenario(scenario, llm=agent.ScriptedAttackLLM() if scripted else llm.chat)


def flash(kind: str, message: str) -> None:
    st.session_state.setdefault("flash", []).append((kind, message))


def operator_ok(operator: str, token: str) -> bool:
    if not operator.strip():
        flash("error", "Enter your operator ID before deciding on an action.")
        return False
    expected = config.OPERATOR_TOKEN
    if expected and not hmac.compare_digest(token, expected):
        flash("error", "Invalid operator token. Nothing was changed.")
        return False
    return True


def decide(action, verb: str, operator: str, token: str, note: str) -> None:
    if not operator_ok(operator, token):
        st.rerun()  # re-draw so the queued error shows now, not after the next click
    try:
        fn = {"approve": gateway.approve_and_execute, "reject": gateway.reject, "revert": gateway.revert}[verb]
        rec = fn(action.id, operator.strip(), note)
    except ValueError as e:
        flash("error", str(e))
        st.rerun()
    messages = {"EXECUTED": ("success", f"#{rec.id} EXECUTED by {rec.approver}: {rec.target_id} set to {rec.to_value:g}."),
                "DENIED": ("warning", f"#{rec.id} not executed: {rec.note or 'rejected by operator'}"),
                "REVERTED": ("success", f"#{rec.id} REVERTED: {rec.target_id} restored to {rec.from_value:g}."),
                "EXECUTION_FAILED": ("error", f"#{rec.id} failed and was rolled back: {rec.note}")}
    flash(*messages[rec.status])
    st.rerun()


# ---- header + controls ------------------------------------------------------------------------------

if not Path(config.DB_PATH).exists():
    seed_data.build()

st.title("💧 AquaGrid operator console")
st.markdown('<span class="pill p-code">CODE decides</span><span class="pill p-llm">LLM explains</span>'
            '<span class="pill p-human">HUMAN approves</span> &nbsp; synthetic demo data · simulated clock '
            f'<b>{config.DEMO_NOW.replace("T", " ")}</b>', unsafe_allow_html=True)

loaded = view.loaded_scenario() or "peak_2pm"
names = list(seed_data.SCENARIOS)
c_scn, c_load, c_run, c_mode = st.columns([2.2, 1, 1, 2.2], vertical_alignment="bottom")
scenario = c_scn.selectbox("Scenario", names, index=names.index(loaded), key="scenario",
                           format_func=lambda k: f"{k}: {seed_data.SCENARIOS[k][0]}")
mode = c_mode.radio("Model", ["Real LLM chain", "Scripted attacker"], horizontal=True, key="mode")
if c_load.button("Load scenario", key="load", width="stretch"):
    load_scenario(scenario)
if c_run.button("Run agent", key="run", type="primary", width="stretch"):
    run_agent(scenario, scripted=mode == "Scripted attacker")
chain = [p for p in llm.available()] or ["none"]
st.caption(f"{seed_data.SCENARIOS[scenario][1]}  ·  LLM chain with keys: {' → '.join(chain)} (Ollama is local, not probed)")

# U-5.2: ensure fire_reserves table exists (idempotent migration for existing DBs)
_mig_conn = db.connect()
try:
    db.migrate(_mig_conn)
finally:
    _mig_conn.close()

snap = view.load_snapshot()
pre, feeds = snap["pre"], snap["feeds"]

# ---- U-5.2: Civil Defence fire reserve panel ------------------------------------------------
with st.expander("🚒 Civil Defence emergency fire reserve (U-5.2)", expanded=feeds.fire_reserve_m3 > 0):
    st.caption(
        "A fire request locks this volume on top of the 35 % strategic reserve. "
        "The scheduler veto and all guard arithmetic update immediately."
    )
    fd_col1, fd_col2, fd_col3 = st.columns([2, 1, 1])
    fire_vol = fd_col1.number_input(
        "Requested volume (m³)", min_value=0, max_value=10000, step=500,
        value=int(feeds.fire_reserve_m3), key="fire_vol",
        help="Civil Defence request. 0 = no active emergency."
    )
    fire_sector = fd_col2.selectbox("Sector / location", ["S6", "S1", "S2", "S3", "S4"], key="fire_sector",
                                    index=["S6","S1","S2","S3","S4"].index(feeds.fire_reserve_sector)
                                    if feeds.fire_reserve_sector in ["S6","S1","S2","S3","S4"] else 0)
    if fd_col3.button("Apply request", key="fire_apply", type="primary", width="stretch"):
        _fc = db.connect()
        try:
            db.set_fire_reserve(_fc, float(fire_vol), fire_sector, "Civil Defence",
                                note=f"set via operator console")
        finally:
            _fc.close()
        st.rerun()

    if feeds.fire_reserve_m3 > 0:
        fire_pct = feeds.fire_reserve_m3 / snap["reservoir"]["capacity_m3"] * 100
        st.warning(
            f"🚒 **Fire reserve ACTIVE** — {feeds.fire_reserve_m3:,.0f} m³ ({fire_pct:.1f}%) locked for "
            f"Civil Defence (Sector {feeds.fire_reserve_sector}).  "
            f"Effective floor raised to **{feeds.effective_floor_pct:.0f}%** "
            f"(35% base + {feeds.effective_floor_pct - 35:.0f}% combined margin)."
        )
    else:
        st.info("No active fire request. Enter a volume and click Apply request to simulate the curveball.")

floor_note = f"{feeds.effective_floor_pct:.0f}% = 35% base"
if feeds.effective_floor_pct > config.RESERVE_FLOOR_PCT:
    extras = []
    if feeds.bloom_active or not pre["intake_trusted"]:
        extras.append("5% bloom/intake")
    fire_pct_note = feeds.fire_reserve_m3 / snap["reservoir"]["capacity_m3"] * 100 if feeds.fire_reserve_m3 > 0 else 0
    if fire_pct_note > 0:
        extras.append(f"{fire_pct_note:.1f}% fire")
    floor_note = "35% base + " + " + ".join(extras) if extras else f"{feeds.effective_floor_pct:.0f}%"

m = st.columns(5)
m[0].metric("Tariff band", feeds.current_band.upper(), f"{feeds.tariff[0]['aed_per_kwh']:.2f} AED/kWh", delta_color="off", delta_arrow="off")
m[1].metric("Reservoir R1", f"{feeds.reservoir_level_pct:.1f}%", f"{snap['reservoir']['capacity_m3']:,.0f} m3 capacity",
            delta_color="off", delta_arrow="off")
m[2].metric("Effective floor", f"{feeds.effective_floor_pct:.0f}%", floor_note, delta_color="off", delta_arrow="off")
m[3].metric("Intake sensor", "TRUSTED" if pre["intake_trusted"] else "UNTRUSTED",
            "quality readings believed" if pre["intake_trusted"] else "assume worst case", delta_color="off", delta_arrow="off")
m[4].metric("Bloom alert", "ACTIVE" if feeds.bloom_active else "none", f"plant {snap['plant']['setpoint_m3h']:,.0f} m3/h",
            delta_color="off", delta_arrow="off")

# ---- sensors + leak findings ---------------------------------------------------------------------

left, right = st.columns([3, 2])
with left:
    st.subheader("Network & sensors")
    rows = view.sensor_rows(pre)
    n_bad = sum(r["Trust"] == "QUARANTINED" for r in rows)
    st.caption(f"{len(rows) - n_bad} trusted · {n_bad} QUARANTINED (excluded from every calculation)")
    df = pd.DataFrame(rows)
    df["Trust"] = df["Trust"].map(lambda t: "🔴 QUARANTINED" if t == "QUARANTINED" else "🟢 trusted")
    st.dataframe(df, hide_index=True, width="stretch", height=300)
    moved = {v: p for v, p in snap["valves"].items() if p < 100}
    st.caption(f"Valves: {'all ' + str(len(snap['valves'])) + ' fully open' if not moved else 'NOT fully open: ' + str(moved)}")
with right:
    st.subheader("Leak vs sensor fault")
    quiet = []
    for f in pre["findings"]:
        card = view.finding_card(f)
        if card["tone"] == "ok":
            quiet.append(f.sector)
            continue
        (st.error if card["tone"] == "error" else st.warning)(card["headline"])
        st.markdown("\n".join(f"- {e}" for e in card["evidence"]))
        st.caption(card["decided_by"])
    if quiet:
        st.success(f"No anomaly in {', '.join(quiet)}")

# ---- schedule + the floor veto -----------------------------------------------------------------

st.subheader("Tariff-aware schedule and the reserve floor")
chart, why = st.columns([3, 2])
with chart:
    curves = pd.DataFrame(view.level_curves(snap)).set_index("hour")
    st.line_chart(curves, x_label="Hour of day", y_label="Reservoir level (%)")
    s = feeds.schedule
    st.caption(f"Scheduled cost {s.cost_aed:,.0f} AED vs {s.baseline_cost_aed:,.0f} AED at flat production · "
               f"{s.kwh_saved:,.0f} kWh of peak-hour energy avoided · always above floor + "
               f"{config.SCHEDULE_BUFFER_PCT:.0f}%")
with why:
    demand = pre["demand_forecast"]
    level, cap = snap["reservoir"]["level_pct"], snap["reservoir"]["capacity_m3"]
    for cand in s.rejected_candidates:
        st.error(f"**REJECTED BY GUARD:** {cand.name}\n\n{cand.reason}")
    veto = view.reservoir_arithmetic(level, cap, config.THROTTLE_CANDIDATE_M3H, demand, feeds.effective_floor_pct, feeds.fire_reserve_m3)
    hold = view.reservoir_arithmetic(level, cap, snap["plant"]["setpoint_m3h"], demand, feeds.effective_floor_pct, feeds.fire_reserve_m3)
    st.caption(f"The arithmetic: throttle to {config.THROTTLE_CANDIDATE_M3H:.0f} m3/h")
    st.code("\n".join(veto["lines"]), language=None)
    st.caption(f"versus holding {snap['plant']['setpoint_m3h']:,.0f} m3/h")
    st.code("\n".join(hold["lines"]), language=None)
with st.expander("Schedule detail"):
    st.dataframe(pd.DataFrame([x.model_dump() for x in s.slots]), hide_index=True, width="stretch")

# ---- the human gate ----------------------------------------------------------------------------

st.subheader("Human approval gate")
st.caption("Nothing moves until a human approves. Approval re-checks every guard against the live state first.")
for kind, text in st.session_state.pop("flash", []):
    getattr(st, kind)(text)
ident = st.columns([1, 1, 2])
operator = ident[0].text_input("Operator ID", value="operator", key="operator_id")
token = ident[1].text_input("Operator token", type="password", key="operator_token") if config.OPERATOR_TOKEN else ""

actions = gateway.list_actions()
pending = [a for a in actions if a.status == "PENDING_APPROVAL"]
if not pending:
    st.info("No actions are waiting for approval.")
for a in pending:
    with st.container(border=True):
        info, buttons = st.columns([4, 1.4])
        info.markdown(f"**#{a.id} · {a.kind.replace('_', ' ')} · `{a.target_id}`**: {a.from_value:g} → **{a.to_value:g}**"
                      f"  \nProposed by `{a.proposed_by}`: {a.rationale}")
        info.dataframe(pd.DataFrame(view.guard_rows(a)), hide_index=True, width="stretch")
        note = buttons.text_input("Note", key=f"note_{a.id}", placeholder="optional")
        if buttons.button("Approve & execute", key=f"approve_{a.id}", type="primary", width="stretch"):
            decide(a, "approve", operator, token, note)
        if buttons.button("Reject", key=f"reject_{a.id}", width="stretch"):
            decide(a, "reject", operator, token, note)

decided = [a for a in actions if a.status != "PENDING_APPROVAL"]
with st.expander(f"Decided and blocked actions ({len(decided)})", expanded=bool(decided)):
    if decided:
        st.dataframe(pd.DataFrame([{"#": a.id, "Status": a.status, "Target": a.target_id,
                                    "From → to": f"{a.from_value:g} → {a.to_value:g}", "Proposed by": a.proposed_by,
                                    "Approver": a.approver or "", "Note": a.note or ""} for a in decided]),
                     hide_index=True, width="stretch")
    for a in (a for a in decided if a.status == "EXECUTED"):
        if st.button(f"Revert #{a.id} ({a.target_id} back to {a.from_value:g})", key=f"revert_{a.id}"):
            decide(a, "revert", operator, token, "")

# ---- brief + trace -----------------------------------------------------------------------------

st.subheader("Agent briefing and decision trace")
run = st.session_state.get("agent_run")
if run is None:
    st.info("Click **Run agent** to get the LLM briefing and the full decision trace. The panels above are "
            "already computed by code.")
else:
    (st.warning if run.status != "COMPLETED" else st.success)(
        f"Run {run.run_id}: {run.status} · model {run.provider or 'none (code-only brief)'} · {run.steps} steps · "
        f"{run.blocked_attempts} blocked attempt(s)")
    brief_tab, trace_tab = st.tabs(["Operator brief", "Decision trace"])
    brief_tab.markdown(run.brief_md)
    trace = pd.DataFrame(view.trace_rows(run.run_id))
    trace["Who"] = trace["Who"].map(lambda w: WHO_ICON.get(w, w))
    trace_tab.dataframe(trace, hide_index=True, width="stretch")
st.caption("The LLM reads through two read-only tools and can only queue proposals. Code validates every proposal; "
           "only a human can approve, reject or revert.")
