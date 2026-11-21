# Product

## Register

product

## Users
Primary: hackathon judges watching on a projector, and behind them the persona the demo plays: a Coastal Water Authority control-room operator supervising a desalination plant and a four-sector distribution network. The operator's job is to notice trouble early, tell a real leak from a faulty sensor, and approve or reject every valve or plant change the AI agent proposes.

## Product Purpose
AquaGrid Mission Control is the live operating picture for the Water Network Orchestration Agent: tariff-aware desalination scheduling plus leak detection, with hard read/write separation. Success is a viewer understanding within seconds what is happening on the network, what the agent is doing, what code blocked, and how many peak-tariff kWh were saved without breaching the strategic reserve.

## Brand Personality
Credible, calm, exact. Three words: instrument-grade, composed, unflinching. Quiet when nominal, unmistakable when something is wrong. Voice is terse and technical: timestamps, identifiers, units, no marketing copy.

## Anti-references
- Neon sci-fi / hacker-movie UI (glow everywhere, green-on-black cosplay).
- Generic SaaS dashboard (rounded KPI cards, gradient accents, hero-metric tiles).
Draws from: Flightradar24 and ATC radar (live tracks, tight data tags), NASA/SpaceX mission control (telemetry, playhead clock, calm until failure), Bloomberg terminal (dense monospace tape), SCADA/NOC wall displays (alarm banners, status matrices, severity vocabulary).

## Design Principles
1. Quiet until it matters: nominal state is low-contrast and still; anomalies are the only thing that moves or glows.
2. Show the guard, not just the result: every block, quarantine and veto is visible with its reason, because safety beating cost is the story.
3. Code states facts, the agent narrates: every number on screen is computed in code; the agent feed only explains.
4. Density with hierarchy: many live readouts, but one dominant status readable across a room.
5. Alarm semantics never rely on colour alone: severity pairs colour with icon, label and position.

## Accessibility & Inclusion
Target WCAG AA contrast. Severity encoded by shape, label and position as well as colour (colour-blind safe). All animation (radar sweep, flashing alarms, tickers) has a prefers-reduced-motion fallback to static indicators. Legible at projector distance.
