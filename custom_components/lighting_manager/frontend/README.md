# Frontend panel - current status

`lighting-manager-panel.js` is the real Table + Inbox + Dashboard UI (PRD
§7/8/16/22), built 2026-09-14/15 to replace the earlier placeholder that
only proved the plumbing (sidebar registration, static path serving,
websocket round-trip).

## What's implemented

- **Table view**: every discovered light, sortable and filterable by name,
  area, type, state, status and schedule count. Uses HA's own
  `ha-data-table` component when present, with a hand-built plain-`<table>`
  fallback if it isn't (see the file's header comment for why this matters).
- **Inbox view**: grouped by condition (new, unassigned, area mismatch,
  missing, plus the two conditions defined in `const.py` but not yet
  produced by the backend), each with condition-specific quick actions.
- **Dashboard view** (added 2026-09-15, PRD §22): the daily-use control
  surface, deliberately separate from Table/Inbox's admin purpose. Shows
  only lights with `dashboard_included=true`, grouped by area, each as a
  card with a power toggle (calls `light.turn_on`/`turn_off` or
  `switch.turn_on`/`turn_off` directly via `hass.callService` - not a
  Lighting Manager websocket command, this is a live HA entity action) plus
  inline countdown presets/custom-minutes/cancel. This is the "click a
  light on the TV lounge dashboard, turn it off in 20 minutes" workflow
  Hans described as his actual replacement for Simple Timer - not a config
  screen, so it has no detail-drawer integration; a "Manage" button on each
  card jumps to that light's Table entry instead.
- **Detail drawer**: opens on clicking any row/item in Table or Inbox.
  Handles rename, move (area), set light type, adopt / ignore / un-ignore,
  dashboard toggle, and countdown start (presets + custom minutes, with a
  "turn on first" toggle) / cancel. Shared between Table and Inbox rather
  than duplicated per view; not used by Dashboard (see above).
- **Promote-a-switch dialog**: lists `list_promotable_switches` results not
  already managed, with a one-click Promote button per entity.
- Polls `list_lights`/`list_inbox` every 15s and after every mutating
  action; a dismissible error banner surfaces any failed websocket call.

## Built without Lit, without a build step, without a CDN

See the long comment at the top of `lighting-manager-panel.js` for the full
reasoning - short version: getting Lit into a no-build-step custom panel
means either a runtime CDN dependency (fragile for something on a
self-hosted instance) or vendoring a prebuilt bundle (blocked when this was
attempted - the sandbox this was built in couldn't reach unpkg/jsdelivr to
verify one). Instead this uses HA's own already-registered elements
(`ha-data-table`, `ha-area-picker`) directly via `document.createElement`,
same technique the original placeholder used for its own markup, with a
defensive fallback for each in case a future HA frontend build removes
them (they're long-standing but internal, not a documented public API).

## Needs live-instance verification

This was built by reading `coordinator.py`/`api.py`/`registry.py` closely
and cannot be visually tested outside a real browser against a real HA
frontend - there's no JS test harness in this project (the 58 Python tests
cover the backend only). Before relying on it day to day, worth checking,
same iterative loop as the backend's live testing:

- Hard-refresh the panel page after restarting HA (static files are served
  with `cache_headers=False`, but the browser's module cache can still hold
  an old copy on a plain reload).
- Open the browser console while testing - `ha-data-table`/`ha-area-picker`
  property names are believed correct from how HA's own Settings pages use
  them, but haven't been confirmed against Hans's actual frontend build.
- Try each action at least once: rename, move, set type, adopt a new
  light, ignore then un-ignore it, toggle dashboard-included, start and
  cancel a countdown, promote a switch.
- `hass.areas` (used for area names/pickers) is assumed present on the
  `hass` object passed to the panel - long-standing in HA's frontend, but
  worth confirming areas actually render by name and not by raw ID.

## Not built yet, in PRD order

- Rooms view (§7): Area-grouped cards, drag/drop, mobile "Move to room..."
  for the *admin* actions - not to be confused with the Dashboard view
  above, which is grouped by area too but is a control surface, not an
  editing one. Table + Inbox cover the same underlying admin actions for
  now; Rooms would be a different presentation of the same websocket API,
  not new backend work.
- First-run bulk adoption (§10): the Inbox surfaces new lights one at a
  time; no dedicated multi-select bulk-review screen yet.
- Light detail view (§27) beyond what the shared drawer above covers
- Schedule presentation UI (§19): real schedule details per light (not
  just the count badge) plus a deep link into the Scheduler Card - the
  `deep_link_for_schedule` stub in the Niels Faber provider currently
  always returns `None` since Scheduler Card doesn't expose a stable
  per-schedule URL. A hover tooltip on the schedule badge explaining where
  to actually edit a schedule (requested 2026-09-15) is a cheap partial
  step here, not yet built either.
