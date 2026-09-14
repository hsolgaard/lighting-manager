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

- Table view (§8) and Inbox (§16) frontend: sortable/filterable table of
  all lights plus a grouped inbox with condition-specific quick actions,
  both backed by a shared detail drawer (rename, move, set type, adopt/
  ignore/un-ignore, dashboard toggle, countdown start/cancel, promote a
  switch). Built in plain JS against HA's own native components
  (`ha-data-table`, `ha-area-picker`) rather than Lit - see the header
  comment in `frontend/lighting-manager-panel.js` for why, and
  `frontend/README.md` for what still needs live-instance verification.
- `unignore_light` action (backend + websocket command) - added while
  building the Inbox UI; not in the original PRD, but an Ignore button
  with no way back would be a one-way trap.

### Not yet built

- Rooms view (§7) - Area-grouped cards, drag/drop. Table + Inbox
  (immediately above) covers the same ground for now; Rooms is a
  different presentation of the same data, not new backend work.
- First-run bulk adoption UI (§10) - backend actions (`adopt_light`,
  `ignore_light`) exist and the Inbox surfaces new lights one at a time;
  there's no dedicated bulk-review screen yet.
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

### Testing

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
pytest
```

56 tests across `registry.py`, `storage.py`, `countdown.py`,
`providers/scheduler/niels_faber.py`, `coordinator.py`, and a websocket-API
smoke-test layer (`api.py`, including an admin-permission check manual
testing didn't cover). See `tests/conftest.py` for the version caveat: this
suite runs against `homeassistant==2024.3.1` (the newest release this
project's package index could resolve), not the current core - it validates
the integration's own logic, and would have caught both real bugs found so
far (the Label id scheme, and `countdown.async_start()` never calling
`light.turn_on`), but it isn't a substitute for testing against a live,
current instance.

One real, currently-unhandled gap the suite surfaces rather than hides:
`registry.async_rename_entity` / `async_set_entity_area` call
`entity_registry.async_update_entity()` directly, which raises `KeyError` for
an entity with no entity-registry entry at all (e.g. some template/YAML
lights - `stable_key_for_entity`'s own docstring acknowledges these exist).
Every entity tested against Hans's real instance has had a registry entry,
so this hasn't been hit in practice. Decision (2026-09-14): left as-is for
now rather than fixed, since it doesn't affect any light Hans actually has.
Consequence if it is hit: `ws_rename_light`/`ws_move_light` are wrapped in
`@websocket_api.async_response`, so the `KeyError` doesn't crash HA or the
integration - it's caught by HA's own websocket message-handling wrapper,
logged as an unhandled exception in the HA log, and the frontend gets back
a generic `unknown_error` for that one command (the new panel's error
banner will at least show *something* went wrong, rather than nothing).
See `tests/test_coordinator.py::test_rename_on_registry_less_entity_raises`
if this needs revisiting later.

Two more added while building the frontend, same "flag, don't silently
guess" spirit:

- `unignore_light` (mirrors `ignore_light` exactly) - not in the original
  PRD, added because the Inbox's Ignore action needed a way back.
- `const.py`'s `INBOX_BROKEN_SCHEDULE_REF` / `INBOX_PROVIDER_UNAVAILABLE`
  are defined but `coordinator.async_build_inbox()` never actually emits
  them yet - the Inbox UI handles them generically (falls back to the raw
  condition string) in case that changes, but nothing produces them today.
