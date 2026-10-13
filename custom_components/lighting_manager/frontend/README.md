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
- **Rooms view**: one section per room with state, type, schedule and
  attention badges; arrows change the dashboard order. A room selector in
  the toolbar narrows Table, Rooms and Inbox to one room.
  (The panel's old Dashboard tab was removed: the room card and its
  countdown popup do that job on a real dashboard.)
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

- Rooms view drag and drop and per-room bulk settings (arrows exist today).

## Dashboard cards (auto-registered)

Two cards ship with the integration and are loaded globally via
`frontend.add_extra_js_url` - **no Lovelace Resources entry and no file in
`www/` is needed**; deploying the integration updates them:

- `custom:lighting-manager-countdown-card` - `entity: light.x`; a native
  Home Assistant tile (same look as every other tile; any tile option such
  as `name` or `icon` is passed through). Tap toggles; double-tap opens a
  countdown popup (setup, or - when one is running - the live clock with
  Change / Cancel countdown / OK). A running countdown also shows a small
  timer badge on the tile.
- `custom:lighting-manager-room-card` - `area: living_room` (id or name);
  optional `columns: 2`. A light can also show a brightness slider under its
  tile (tick "Show brightness slider on dashboard" in the panel).
  Administrators also see a small "Reorder" button above the tiles: it
  switches the card to a list with up/down arrows, and the order is saved
  per room in Lighting Manager (shared with the light list card). Draws every adopted, shown-on-dashboard light in
  the Area as a countdown tile, using each light's Lighting Manager
  settings (name, order, tile width half/full). Nothing to maintain: add
  or hide a light in Lighting Manager and the card follows.
- `custom:light-scheduler-list-card` - `area: <area_id>` (Lighting Manager
  resolves the adopted lights, manual order) or the legacy `lights:` list.
  Still uses Niels Faber's Scheduler component for the schedules
  themselves and Browser Mod for the edit popup.

- `custom:lighting-manager-scheduler-card` - `area: <id or name>` plus any
  Scheduler Card option (`title`, `sort_by`, `exclude`, ...). A thin wrapper
  that asks Lighting Manager for the Area's adopted lights and builds Niels
  Faber's `custom:scheduler-card` with that `include:` list (Scheduler Card
  must be installed; none of its code is copied).

`area:` on both Area-aware cards accepts the Area id (`living_room`) or its
name (`Lounge Big`), resolved against Home Assistant's own Area registry.

If you previously loaded `light-scheduler-list-card.js` from
`/local/` as a Lovelace resource, remove that resource and the file in
`www/`; otherwise whichever copy loads first wins.

## Loading the cards (recommended: one Lovelace resource)

All dashboard cards are built into one script, `lighting-manager-cards.js`
(`python3 scripts/build_bundle.py` rebuilds it from the individual card
files; a test fails if it is stale). Add it once:

Settings > Dashboards > three dots > Resources > Add resource
URL `/lighting_manager_static/lighting-manager-cards.js`, type JavaScript module.

Home Assistant waits for Lovelace resources before drawing a dashboard, so
every card is defined in time. The integration also registers the bundle
automatically, but Home Assistant does not wait for those, so on some page
loads a card can show "Custom element doesn't exist" - the resource avoids
that. Having both is harmless (only the first load runs). Remove any older
resource that points at an individual card file.

The schedules list card opens its edit dialog itself, so Browser Mod is no
longer needed. The card scripts are served uncached (they can be referenced
by a fixed address); the panel is cached under a `?v=<file mtime>` URL, so
after deploying restart Home Assistant to register the new version.
