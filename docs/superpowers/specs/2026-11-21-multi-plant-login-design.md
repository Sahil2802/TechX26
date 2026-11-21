# Multi-plant token login — design (approved 2026-11-21)

## Intent
Demo that the console is multi-plant: token login gate; admin token picks between 2 plants and can switch; each plant has its own telemetry and its own leak/bloom story.

## Goals / non-goals / anti-goals
- Goals: login gate; roles from env tokens; per-request plant routing; plant B with different leak (S2) and bloom (early red tide ~13:15); plant switcher.
- Non-goals: user accounts, sessions/JWT, plant_id column in every table, >2 plants.
- Anti-goals: plant A demo numbers change (`tests/test_demo_numbers.py` must stay green); any new write path or auto-approve; client-only gating.

## Approaches (picked: A)
A. One SQLite file per plant + ContextVar in `db.py` (chosen). B. plant_id column on ~15 tables (too heavy). C. Same data relabelled (fake).

## Design
1. Tokens (env, in `config.py`): `ADMIN_TOKEN` (both plants), `PLANT_A_TOKEN`, `PLANT_B_TOKEN` (locked to one). Existing `OPERATOR_TOKEN` stays the write token; unset = unauthenticated demo mode (keeps tests passing). Compare with `hmac.compare_digest`.
2. `POST /api/login` {token} -> {role, plants}. Every `/api/*` needs the token (reads too) when tokens are configured; a non-admin asking for another plant -> 403.
3. `db.py`: `ContextVar` current plant; `connect()` resolves `data/<plant>.sqlite`. FastAPI dependency sets it from `X-Plant` after the role check. CLI/agent default to plant A.
4. `seed_data.py`: per-plant profile dict (leak sector + bypass-loop valves, critical node, sensor-fault story, bloom time/source/message, capacity, start levels, scenario list). Plant A profile reproduces today's data exactly. Remove S4 / `V-S4-IN` / `CL-S4` literals in tools/live/agent/web by reading the profile.
5. Plant B "Gulf Plant": S2 leak with loop + school as critical node, drifting chlorine sensor, red-tide warning ~13:15, reservoir ~52% with different capacity. Own 3 scenarios as automated tests.
6. Frontend: login gate, plant picker (admin only), header switcher; token + plant in memory, sent as `X-Operator-Token` / `X-Plant`.

## Stages (commit after each)
- Stage 1: auth, routing, picker, switcher; plant B seeded from profile with A's story.
- Stage 2: plant B's real story + tests.

## Left to builder
Exact profile field names, endpoint error wording, picker styling.
