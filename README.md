# Lighting Manager

A Home Assistant custom integration that brings routine lighting administration
- naming, room assignment, countdowns, and (when installed) recurring
schedules - into one workflow, instead of spread across Settings, dashboards,
and separate scheduler/timer integrations.

> Manage the light, not the plumbing.

Full product rationale, competitive analysis and decision history live in the
PRD and its companion Feasibility Review (tracked in the project, not this
repo) rather than duplicated here.

## Status: initial backend scaffold (2026-09)

This is the first commit after PRD v0.3 and its technical-spike sign-off.
What's implemented vs. still to build:

### Implemented

- Discovery of `light.*` entities and explicitly promoted `switch.*` entities
  (§11, §11.1)
- Effective-Area resolution and mismatch detection, entity-level Area
  override read/write (§14)
- Inline rename via the entity registry (§13)
- Controlled light-type taxonomy backed by native HA Labels, single-label
  enforced (§15)
- Persisted managed-light metadata with schema versioning (§25)
- Native countdown engine: absolute-expiry persistence, restart restoration,
  external-action cancellation (§20)
- Niels Faber Scheduler adapter with a maintained reverse index (entity to
  schedules), confirmed against a real `switch.schedule_*` entity's attribute
  shape (§18)
- Inbox condition computation: new, unassigned, missing, area mismatch (§16)
- Selective HA Repairs integration for storage corruption / provider failure
  only (§17)
- Websocket API surface for all of the above, admin-gated on every mutating
  command (§29, §38 investigation #6)
- Config flow (single-instance, no setup questions) (§28)

### Not yet built

- The actual Rooms / Table / Inbox frontend (currently a placeholder panel
  that proves the websocket plumbing works - see
  `custom_components/lighting_manager/frontend/README.md`)
- First-run bulk adoption UI (§10) - backend actions (`adopt_light`,
  `ignore_light`) exist; there's no bulk review screen yet
- Bulk operations (§9)
- Schedule presentation UI (§19) and the specialist lighting dashboard (§22)
- Hardware/entity replacement workflow (§24) - explicitly P1
- Configurable table columns, saved filters (§26) - P1

### Open before the schedule UI specifically is finalized

The Niels Faber adapter's reverse-index logic is confirmed correct for a
single-timeslot, single-action schedule (verified against a live instance).
Not yet confirmed: whether a schedule with multiple distinct triggers (e.g.
an ON at 18:30 and an OFF at 23:15 for the same light) is one
`switch.schedule_*` entity with parallel `timeslots`/`weekdays`/`actions`
arrays, or multiple sibling entities. See the adapter's module docstring
(`providers/scheduler/niels_faber.py`) for detail - worth checking against a
real multi-trigger schedule before building the schedule-presentation UI.

## Development

```
custom_components/lighting_manager/
  __init__.py          entry point: wires everything together per config entry
  const.py
  config_flow.py
  coordinator.py        orchestration: discovery + storage + countdown + scheduler -> LightView/InboxItem
  registry.py            HA entity/device/area/label registry helpers
  storage.py              persisted metadata (schema versioned)
  countdown.py            native per-light countdown engine
  repairs.py               selective HA Repairs integration
  api.py                    websocket commands for the frontend panel
  providers/scheduler/
    base.py                 provider interface
    niels_faber.py           Niels Faber Scheduler Component adapter
  frontend/
    lighting-manager-panel.js   placeholder panel (see its own README)
```

No automated tests yet. Next step for anyone picking this up: install
`pytest-homeassistant-custom-component` and add coverage for `coordinator.py`
and `countdown.py` in particular, since those carry the actual product logic.
