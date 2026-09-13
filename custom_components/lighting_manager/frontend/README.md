# Frontend panel - current status

`lighting-manager-panel.js` is a placeholder, not the product. It proves the
plumbing (sidebar registration, static path serving, websocket round-trip)
works, and shows a live count of discovered/adopted lights so there's
something to verify against while the backend is being built out.

Not built yet, in PRD order:

- Rooms view (§7): Area-grouped cards, drag/drop, mobile "Move to room..."
- Table view (§8): sortable/filterable/searchable, inline editing, bulk ops
- First-run bulk adoption (§10)
- Inbox (§16)
- Light detail view (§27)
- Countdown controls (§20) and schedule presentation (§19)

Suggested approach when this work starts: a small Lit-based single-page app
served from this same static path, using the websocket commands already
implemented in `api.py` - no new backend work should be required to build
the Rooms/Table/Inbox views themselves, only new frontend code.
