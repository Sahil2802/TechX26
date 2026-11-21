# Multi-plant token login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Token login gate; admin picks/switches between two plants; each plant has its own SQLite file and its own leak/bloom story.

**Architecture:** One SQLite file per plant. `db.py` holds a `ContextVar` for the current plant; `db.connect()` / `db.db_path()` resolve the file from it (plant A = `config.DB_PATH`, plant B = sibling `plant_b.sqlite`). An async FastAPI dependency authenticates the token, checks the role against `X-Plant`, and sets the ContextVar. A per-plant profile in `aquagrid/profiles.py` replaces the hardcoded S4 literals.

**Tech Stack:** Python, FastAPI, SQLite, pytest, React + TypeScript (Vite).

**Spec:** `docs/superpowers/specs/2026-11-21-multi-plant-login-design.md`

## Global Constraints
- Plant A demo numbers must not change: `tests/test_demo_numbers.py` and all existing tests stay green.
- No new write path, no auto-approve; the LLM keeps exactly three tools; gateway still re-validates everything.
- Tokens come from env only (`ADMIN_TOKEN`, `PLANT_A_TOKEN`, `PLANT_B_TOKEN`); never in code. Compare with `hmac.compare_digest`.
- `OPERATOR_TOKEN` keeps working: it is the admin token when `ADMIN_TOKEN` is unset. Read all token config at call time (tests monkeypatch `config`).
- No tokens configured at all = open demo mode (role admin, both plants), as today.
- Header names: `X-Operator-Token`, `X-Plant` (values `A` / `B`). Default plant `A`.
- Commit after each stage; never push; no Claude attribution in commits.

## Review Focus
- Wrong/empty/whitespace token on any `/api/*` route -> 401, nothing changed.
- Plant-A token sending `X-Plant: B` -> 403; unknown plant value (`C`, `../x`) -> 422, never turned into a file path.
- Write on plant B must not touch plant A's file (and vice versa): an action staged on B is invisible on A.
- `/api/mission` is loaded in an iframe and cannot send headers: it must accept `token`/`plant` query params.
- ContextVar must reach sync endpoints (thread pool): the dependency must be `async`.

---

## Stage 1 — auth, routing, picker, switcher

### Task 1: Plant routing in db.py + token config
**Files:** Modify `aquagrid/config.py`, `aquagrid/db.py`; Test `tests/test_plants.py`
**Interfaces:**
- Produces: `config.ADMIN_TOKEN`, `config.PLANT_A_TOKEN`, `config.PLANT_B_TOKEN` (`os.getenv`, default `None`); `config.PLANTS = {"A": "Coastal Plant", "B": "Gulf Plant"}`.
- Produces: `db.set_plant(plant: str) -> None` (raises `ValueError` unless in `config.PLANTS`), `db.current_plant() -> str` (default `"A"`), `db.db_path(plant: str | None = None) -> str` (A -> `config.DB_PATH`; B -> `Path(config.DB_PATH).with_name("plant_b.sqlite")`). `connect()` uses `db_path()`.
- [ ] Test `test_db_path_routes_by_plant`: with `config.DB_PATH` monkeypatched to `tmp/t.sqlite`, `db_path("A")` ends `t.sqlite`, `db_path("B")` ends `plant_b.sqlite`, same dir; `set_plant("C")` raises `ValueError`; default `current_plant() == "A"`.
- [ ] Run (FAIL), implement, run (PASS), run full `pytest -q` (all green), commit `feat: per-plant db routing`.

### Task 2: Seed profile + `seed_data.build(plant)`
**Files:** Create `aquagrid/profiles.py`; Modify `aquagrid/seed_data.py`; Test `tests/test_plants.py`
**Interfaces:**
- Produces: `profiles.PROFILES: dict[str, Profile]` where `Profile` is a frozen dataclass with `name, capacity_m3, r1_start_pct: dict[str, float], scenarios: dict[str, tuple[str, str]]`. Plant A profile = current values (`config.RESERVOIR_CAPACITY_M3`, `R1_START_PCT`, `SCENARIOS`). Plant B (stage 1) = same scenarios, `capacity_m3` 18000.0, otherwise a copy of A. `profiles.get(plant: str | None = None) -> Profile` (defaults to `db.current_plant()`).
- Produces: `seed_data.build(plant: str = "A") -> None` seeds `db.db_path(plant)` using that profile. Keep `seed_data.SCENARIOS` / `R1_START_PCT` module names as plant A aliases so existing tests/imports work.
- [ ] Test `test_build_plant_b_creates_separate_file`: `build("B")` creates `plant_b.sqlite` beside A's; reservoir capacity in B == 18000; A's file untouched.
- [ ] Test: `tests/test_demo_numbers.py` still passes unchanged.
- [ ] Implement, run `pytest -q`, commit `feat: plant profiles and per-plant seeding`.

### Task 3: Auth + plant dependency in web.py
**Files:** Modify `aquagrid/web.py` (and `aquagrid/cli.py` only if it breaks); Test `tests/test_auth.py`; adjust `tests/test_app.py` where it asserts open reads with a token set.
**Interfaces:**
- Consumes: Tasks 1-2 (`db.set_plant`, `seed_data.build(plant)`).
- Produces: `POST /api/login` body `{token: str}` -> `{role: "admin"|"plant", plants: [{id, name}]}`; 401 on bad token. In open demo mode returns admin + both plants.
- Produces: `async def plant_context(x_operator_token: str | None = Header(None), x_plant: str = Header("A"), token: str | None = Query(None), plant: str | None = Query(None)) -> None` — resolves role (`ADMIN_TOKEN or OPERATOR_TOKEN` -> admin; `PLANT_A_TOKEN` -> only A; `PLANT_B_TOKEN` -> only B), 401 if tokens are configured and none matches, 403 if the plant is not allowed, 422 if the plant is not in `config.PLANTS`; then `db.set_plant`. Applied to every `/api/*` route except `/api/login` and `/api/health`; header wins, query params are the iframe fallback (needed by `/api/mission`).
- `_last_run` becomes `dict[str, dict | None]` keyed by plant; `_ensure_db`/`_load_scenario` seed the current plant's file; `Path(config.DB_PATH)` uses in `web.py` become `db.db_path()`; `/api/state` adds `plant`, `plants`, `role`.
- Write routes (`require_token`) accept any token valid for the current plant by the same role resolution, not just `OPERATOR_TOKEN`.
- [ ] Tests (`tests/test_auth.py`, monkeypatch tokens `adm`, `ta`, `tb`): login ok for each; login bad -> 401; `/api/state` with no header -> 401; `ta` + `X-Plant: B` -> 403; `X-Plant: C` -> 422; admin + `X-Plant: B` -> 200 and `state["plant"] == "B"`; stage an action on B, then A's `/api/state` shows no pending actions; `/api/mission?token=adm&plant=B&scenario=baseline` -> 200; open demo mode (no tokens) `/api/state` -> 200.
- [ ] Run (FAIL), implement, `pytest -q` all green (fix existing token tests to send the header), commit `feat: token login and per-request plant routing`.

### Task 4: Frontend gate, picker, switcher
**Files:** Create `frontend/src/Login.tsx`; Modify `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/ShellParts.tsx` (TopBar switcher; remove the old in-rail token field), `frontend/src/types.ts`, `frontend/src/index.css`.
**Interfaces:**
- Produces in `api.ts`: `login(token: string) -> Promise<{role: 'admin'|'plant'; plants: {id: string; name: string}[]}>`, `setSession(token: string, plant: string)`; every request (GET included) sends both headers; `missionUrl` appends `token` and `plant` query params.
- `Login.tsx`: `<Login onReady={(session: {token: string; role: string; plants: {id: string; name: string}[]}) => void} />` — password input, error message; a single-plant token skips the picker; an admin sees a two-card plant picker.
- `App.tsx`: renders `<Login>` until a session and plant are chosen; header dropdown (admin only) calls `setSession`, clears `state` and refetches. A 401 from any call returns to login.
- [ ] `npm run build` and `npm run lint` in `frontend/` — both clean.
- [ ] Manual check with `uvicorn` + `ADMIN_TOKEN=adm PLANT_A_TOKEN=ta`: wrong token rejected; `ta` lands on A with no switcher; `adm` shows the picker, and switching reloads state and the Mission iframe.
- [ ] Commit `feat: login gate, plant picker and switcher`.

## Stage 2 — plant B's own story

### Task 5: Remove S4 literals; plant-aware topology
**Files:** Modify `aquagrid/profiles.py`, `aquagrid/seed_data.py`, `aquagrid/tools.py:13`, `aquagrid/live.py:62`, `aquagrid/agent.py:41,117-122`, `aquagrid/web.py:21`, `aquagrid/config.py` (CRITICAL_NODES), `aquagrid/guards.py:282`, `frontend/src/*` (`CL-S4` / sector labels).
**Interfaces:** `Profile` gains `leak_sector: str`, `loop_sector: str`, `critical_node: str`, `chlorine_sensor: str`, `fire_sectors: list[str]`, `intake_valve: str` (e.g. `V-S4-IN`), `plant_id: str` (`PLANT-1`). Everything that used a literal reads `profiles.get()`.
- [ ] Characterization: all existing tests pass before and after (plant A profile reproduces the literals exactly).
- [ ] Test: with plant B selected, `tools.SECTORS`, the agent sector enum and `fire_sectors` come from B's profile. Commit `refactor: plant profile replaces S4 literals`.

### Task 6: Plant B scenarios + tests
**Files:** Modify `aquagrid/profiles.py`, `aquagrid/seed_data.py`; Create `tests/test_plant_b_scenarios.py`
- Plant B profile: leak in **S2** with a bypass loop and a school as the critical node; drifting **chlorine** sensor (CL-S2) instead of the pressure fault; red-tide bloom alert at ~13:15; `r1_start_pct` ~52 for the peak scenario; scenario ids and names per profile.
- [ ] Tests (written first, run against plant B): (1) S2 leak localized and the drifting chlorine sensor quarantined, not treated as a leak; (2) peak throttle aborted because projected level < floor during the red-tide warning; (3) injection in B's bloom alert -> LLM bypass blocked, nothing changes without operator approval.
- [ ] Plant A scenario tests and demo numbers still green. Commit `feat: plant B leak and red-tide scenarios`.

### Task 7: Docs
- [ ] README: env tokens, seeding plant B, login flow. Commit `docs: multi-plant login`.
