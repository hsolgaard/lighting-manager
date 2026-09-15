/**
 * Lighting Manager panel — Table + Inbox views (PRD §7, §8, §16).
 *
 * Replaces the earlier placeholder (see git history / frontend/README.md
 * for what that proved). Talks to the backend exclusively through the
 * `lighting_manager/*` websocket commands in api.py, per that module's
 * own docstring ("all side effects stay in one place").
 *
 * DELIBERATE CHOICE — plain JS, no Lit, no build step, no CDN:
 * The natural approach for a HA custom panel today is LitElement, and
 * that's what was recommended before this file was written. In practice,
 * getting Lit into a build-step-free custom_components panel means
 * either (a) importing it from a CDN (unpkg/jsdelivr) at runtime in the
 * browser, which makes this panel's *availability* depend on Hans's HA
 * server's browser being able to reach an external CDN — a fragile,
 * un-self-hosted dependency for something that should work offline like
 * the rest of a self-hosted HA instance — or (b) vendoring a prebuilt
 * Lit bundle into this repo, which this sandbox's own network policy
 * blocked when attempted (cdn.jsdelivr.net / unpkg.com both refused).
 * Rather than ship an unverified vendored file or a hidden CDN
 * dependency, this panel is built in vanilla JS, using Home Assistant's
 * OWN already-registered custom elements directly — `ha-data-table` for
 * the table, `ha-area-picker` for area selection — via
 * `document.createElement` + property assignment, exactly like the
 * placeholder already did for its own markup. This gets native HA look
 * and behaviour with zero external dependencies. The trade-off: those
 * two components are long-standing but *internal* HA frontend APIs, not
 * a documented public contract, so each has a defensive fallback below
 * (`_ensureNativeDataTable` / `_ensureAreaField`) that degrades to plain
 * HTML if the component isn't present in some future frontend build.
 *
 * TWO VALUES DUPLICATED FROM THE BACKEND — keep in sync by hand:
 * LIGHT_TYPES must match custom_components/lighting_manager/const.py's
 * LIGHT_TYPES list exactly (there is no websocket command that returns
 * it — it's a small, rarely-changed controlled taxonomy, so duplicating
 * it here was judged not worth a new backend surface for). Likewise
 * COUNTDOWN_PRESETS_MIN. If either list changes in const.py, update the
 * matching constant below in the same change.
 */

// Keep in sync with const.py LIGHT_TYPES.
const LIGHT_TYPES = [
  "ceiling",
  "pendant",
  "spotlights",
  "wall_light",
  "table_lamp",
  "floor_lamp",
  "cabinet_light",
  "outdoor",
  "flood_light",
  "lamp_plug",
  "decorative",
  "other",
];

const LIGHT_TYPE_LABELS = {
  ceiling: "Ceiling",
  pendant: "Pendant",
  spotlights: "Spotlights",
  wall_light: "Wall light",
  table_lamp: "Table lamp",
  floor_lamp: "Floor lamp",
  cabinet_light: "Cabinet light",
  outdoor: "Outdoor",
  flood_light: "Flood light",
  lamp_plug: "Lamp plug",
  decorative: "Decorative",
  other: "Other",
};

// Keep in sync with const.py COUNTDOWN_PRESETS_MIN.
const COUNTDOWN_PRESETS_MIN = [15, 30, 60, 120];

const STATUS_LABELS = {
  ok: "OK",
  needs_setup: "Needs setup",
  unassigned: "Unassigned",
  area_mismatch: "Area mismatch",
  unavailable: "Unavailable",
};

// Inbox conditions per const.py. broken_schedule_reference and
// scheduler_provider_unavailable are defined there but not currently
// produced by coordinator.async_build_inbox() (checked directly against
// that file while building this) - included here anyway, with a plain
// fallback for anything not in this map, so the Inbox doesn't silently
// drop an item if/when the backend starts emitting one of those.
const INBOX_LABELS = {
  new_light: "New",
  unassigned: "Unassigned",
  missing: "Missing",
  area_mismatch: "Area mismatch",
  broken_schedule_reference: "Broken schedule reference",
  scheduler_provider_unavailable: "Scheduler unavailable",
};

const REFRESH_INTERVAL_MS = 15000;

// Friendly toast text per websocket action type. Added 2026-09-14: live
// testing found several buttons (e.g. "Set type") gave no acknowledgement
// at all that anything happened - only a silent list refresh. Falls back
// to a generic "Done" for anything not listed here rather than requiring
// every call site to remember to pass one.
const ACTION_SUCCESS_MESSAGES = {
  adopt_light: "Light adopted",
  ignore_light: "Light ignored",
  unignore_light: "Light un-ignored — back in the Inbox",
  rename_light: "Name updated",
  move_light: "Area updated",
  set_light_type: "Type updated",
  set_dashboard_included: "Dashboard setting updated",
  start_countdown: "Countdown started",
  cancel_countdown: "Countdown cancelled",
  promote_switch: "Switch added as a light",
};

const CSS_TEXT = `
  /* Real bug found 2026-09-14: setting an element's .hidden property only
   * works via the browser's User-Agent stylesheet rule "[hidden] {
   * display: none}" - and author CSS (ours) always wins over UA CSS
   * regardless of selector specificity. Several elements here (e.g.
   * .lm-checkbox-row, which sets display:flex) have their own author
   * "display" rule, which silently defeated .hidden - this is exactly
   * why the "Show ignored" toggle kept showing on the Inbox tab even
   * though _updateToolbarVisibility() was setting .hidden = true on it
   * the whole time. This single !important rule makes .hidden reliably
   * win everywhere in this panel, instead of patching each individual
   * class that happens to set its own display. */
  [hidden] { display: none !important; }
  /* This panel has no
   * Shadow DOM (deliberately - see the file header), so ":host" here
   * was always dead code (it only means anything inside a shadow
   * root). Nothing was actually setting height on the custom element
   * itself, so it collapsed to its content's height instead of filling
   * the available viewport - the "lots of scrolling" bug Hans hit
   * live. The real host element is the custom element tag itself. */
  lighting-manager-panel { display: block; height: 100%; }
  .lm-root {
    display: flex;
    flex-direction: column;
    height: 100%;
    font-family: var(--paper-font-body1_-_font-family, sans-serif);
    color: var(--primary-text-color);
    background: var(--primary-background-color);
    box-sizing: border-box;
  }
  .lm-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--divider-color);
    flex-wrap: wrap;
  }
  .lm-tabs { display: flex; gap: 4px; }
  .lm-tab-button {
    font: inherit;
    font-weight: 500;
    padding: 8px 14px;
    border: none;
    border-radius: 20px;
    background: transparent;
    color: var(--secondary-text-color);
    cursor: pointer;
  }
  .lm-tab-button.active {
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .lm-spacer { flex: 1; }
  .lm-filter {
    font: inherit;
    padding: 6px 10px;
    border: 1px solid var(--divider-color);
    border-radius: 4px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    min-width: 160px;
  }
  .lm-btn {
    font: inherit;
    padding: 6px 12px;
    border: 1px solid var(--divider-color);
    border-radius: 4px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    cursor: pointer;
  }
  .lm-btn:hover { background: var(--secondary-background-color); }
  .lm-btn.primary {
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
    border-color: var(--primary-color);
  }
  .lm-btn:disabled { opacity: 0.5; cursor: default; }
  .lm-error {
    margin: 8px 16px 0;
    padding: 8px 12px;
    border-radius: 4px;
    background: var(--error-color, #db4437);
    color: #fff;
    font-size: 0.9em;
  }
  .lm-main {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr;
    overflow: hidden;
    min-height: 0;
  }
  @media (min-width: 900px) {
    .lm-main.has-detail { grid-template-columns: 1fr 380px; }
  }
  .lm-body { overflow: auto; padding: 8px 16px 16px; }
  .lm-detail {
    overflow: auto;
    border-top: 1px solid var(--divider-color);
    padding: 16px;
    background: var(--card-background-color);
  }
  @media (min-width: 900px) {
    .lm-detail { border-top: none; border-left: 1px solid var(--divider-color); }
  }
  .lm-table { width: 100%; border-collapse: collapse; font-size: 0.95em; }
  .lm-table th, .lm-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--divider-color); }
  .lm-table th { cursor: pointer; color: var(--secondary-text-color); font-weight: 500; user-select: none; white-space: nowrap; }
  .lm-table th .sort-arrow { font-size: 0.8em; opacity: 0.6; }
  .lm-table tbody tr { cursor: pointer; }
  .lm-table tbody tr:hover { background: var(--secondary-background-color); }
  .lm-table tbody tr.selected { background: rgba(var(--rgb-primary-color, 3,169,244), 0.12); }
  .lm-empty { padding: 24px; text-align: center; color: var(--secondary-text-color); }
  .lm-inbox-group { margin-bottom: 20px; }
  .lm-inbox-group h3 { font-size: 1em; margin: 0 0 8px; color: var(--secondary-text-color); }
  .lm-inbox-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
    border: 1px solid var(--divider-color);
    border-radius: 6px;
    margin-bottom: 6px;
    cursor: pointer;
    flex-wrap: wrap;
  }
  .lm-inbox-item:hover { background: var(--secondary-background-color); }
  .lm-inbox-item .name { flex: 1; min-width: 140px; font-weight: 500; }
  /* Fixed width (not flex:1) so this reads as an aligned column down the
   * list rather than a variable-length aside next to the name - the
   * "Area should be a column" feedback (2026-09-14). */
  .lm-inbox-item .area { width: 140px; flex: none; color: var(--secondary-text-color); font-size: 0.85em; }
  .lm-inbox-item .meta { color: var(--secondary-text-color); font-size: 0.85em; }
  .lm-detail h2 { font-size: 1.1em; margin: 0 0 2px; word-break: break-word; }
  .lm-detail .entity-id { font-family: monospace; font-size: 0.8em; color: var(--secondary-text-color); margin-bottom: 16px; word-break: break-all; }
  .lm-field { margin-bottom: 16px; }
  .lm-field label { display: block; font-size: 0.85em; color: var(--secondary-text-color); margin-bottom: 4px; }
  .lm-field-row { display: flex; gap: 6px; align-items: center; }
  .lm-field-row input[type=text], .lm-field-row input[type=number], .lm-field-row select {
    font: inherit;
    flex: 1;
    padding: 6px 8px;
    border: 1px solid var(--divider-color);
    border-radius: 4px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    min-width: 0;
  }
  .lm-warning { background: rgba(var(--rgb-warning-color, 255,152,0), 0.15); border: 1px solid var(--warning-color, #ff9800); border-radius: 4px; padding: 8px 10px; margin-bottom: 16px; font-size: 0.9em; }
  .lm-close-btn { background: none; border: none; font-size: 1.3em; line-height: 1; cursor: pointer; color: var(--secondary-text-color); float: right; }
  .lm-preset-row { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
  .lm-section-title { font-weight: 500; margin: 20px 0 8px; border-top: 1px solid var(--divider-color); padding-top: 16px; }
  .lm-section-title:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
  .lm-checkbox-row { display: flex; align-items: center; gap: 8px; }
  .lm-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.5);
    display: flex; align-items: center; justify-content: center;
    z-index: 10; padding: 16px;
  }
  .lm-overlay-box {
    background: var(--card-background-color);
    border-radius: 8px;
    padding: 20px;
    max-width: 480px;
    width: 100%;
    max-height: 80vh;
    overflow: auto;
  }
  .lm-overlay-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
  .lm-badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.75em; background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .lm-toast {
    position: fixed;
    left: 50%;
    bottom: 24px;
    transform: translateX(-50%);
    background: var(--primary-text-color, #333);
    color: var(--primary-background-color, #fff);
    padding: 10px 18px;
    border-radius: 6px;
    font-size: 0.9em;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    z-index: 20;
    opacity: 0;
    transition: opacity 0.15s ease-in-out;
    pointer-events: none;
    max-width: calc(100% - 32px);
    text-align: center;
  }
  .lm-toast.visible { opacity: 1; }
  .lm-checkbox-row.lm-toolbar-toggle { gap: 4px; font-size: 0.9em; color: var(--secondary-text-color); }
`;

class LightingManagerPanel extends HTMLElement {
  constructor() {
    super();
    this._view = "table"; // "table" | "inbox"
    this._lights = [];
    this._inbox = [];
    this._lightsById = new Map();
    this._selectedEntityId = null;
    this._sortColumn = "name";
    this._sortDirection = "asc";
    this._filterText = "";
    this._pendingAreaId = undefined; // undefined = "not touched yet" for the detail form
    this._showIgnored = false; // Table view hides ignored lights by default (2026-09-14 feedback)
    this._toastTimer = null;
    this._detailDirty = false; // true while the open drawer has an unsaved edit (2026-09-14 fix)
  }

  setConfig() {}

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._initialize();
    } else {
      // Cheap: just keep native components' live `.hass` reference fresh.
      // Does NOT trigger a data refresh - list_lights/list_inbox are
      // polled on their own interval (see REFRESH_INTERVAL_MS) so this
      // setter, which HA calls very often, doesn't hit the websocket API
      // on every unrelated state change in the house.
      if (this._dataTableEl) this._dataTableEl.hass = hass;
      if (this._areaPickerEl) this._areaPickerEl.hass = hass;
    }
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    if (this._hass && !this._root) {
      this._initialize();
    }
  }

  disconnectedCallback() {
    if (this._pollHandle) {
      clearInterval(this._pollHandle);
      this._pollHandle = null;
    }
  }

  async _initialize() {
    this._render();
    await this._refreshAll();
    this._pollHandle = setInterval(() => this._refreshAll(), REFRESH_INTERVAL_MS);
  }

  // -- data -----------------------------------------------------------

  async _refreshAll() {
    try {
      const [lightsResult, inboxResult] = await Promise.all([
        this._hass.callWS({ type: "lighting_manager/list_lights" }),
        this._hass.callWS({ type: "lighting_manager/list_inbox" }),
      ]);
      this._lights = lightsResult.lights;
      this._inbox = inboxResult.items;
      this._lightsById = new Map(this._lights.map((l) => [l.entity_id, l]));
      this._clearError();
      this._renderView();
      if (this._selectedEntityId) {
        const light = this._lightsById.get(this._selectedEntityId);
        if (light) {
          // Real bug found 2026-09-14: this ran on every 15s poll
          // unconditionally, rebuilding the Name/Type/Area controls from
          // fresh server data - which silently threw away anything typed
          // or selected but not yet saved (Hans hit this with the Type
          // dropdown reverting to "-- none --" after ~10-15s). Skip the
          // rebuild while the user has an unsaved edit in progress;
          // _callAction clears the dirty flag right before its own
          // refresh, so a successful save still picks up fresh data.
          if (!this._detailDirty) {
            this._renderDetail(light);
          }
        } else {
          // Light disappeared (e.g. entity removed) - close the drawer.
          this._selectedEntityId = null;
          this._detailDirty = false;
          this._renderDetail(null);
        }
      }
    } catch (err) {
      this._showError(`Could not refresh: ${this._formatError(err)}`);
    }
  }

  async _callAction(type, extra) {
    try {
      await this._hass.callWS({ type: `lighting_manager/${type}`, ...extra });
      this._clearError();
      // Whatever was "dirty" has just been saved (or the action wasn't a
      // field edit at all, in which case this is a harmless no-op) - the
      // refresh below should rebuild the drawer from the now-current
      // server data rather than being skipped as if there were still an
      // unsaved edit sitting in it.
      this._detailDirty = false;
      await this._refreshAll();
      this._showToast(ACTION_SUCCESS_MESSAGES[type] || "Done");
      return true;
    } catch (err) {
      this._showError(`${type} failed: ${this._formatError(err)}`);
      return false;
    }
  }

  _showToast(message) {
    if (!this._toastEl) {
      this._toastEl = document.createElement("div");
      this._toastEl.className = "lm-toast";
      this.appendChild(this._toastEl);
    }
    this._toastEl.textContent = message;
    // Restart the fade-in even if a toast is already visible, so back-to-
    // back actions (e.g. Adopt, then Ignore on the next item) each get
    // their own visible confirmation rather than the timer silently
    // extending.
    this._toastEl.classList.remove("visible");
    // eslint-disable-next-line no-unused-expressions
    this._toastEl.offsetHeight; // force reflow so the class removal takes effect before re-adding
    this._toastEl.classList.add("visible");
    if (this._toastTimer) clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => {
      this._toastEl.classList.remove("visible");
    }, 2500);
  }

  _formatError(err) {
    if (!err) return "unknown error";
    if (typeof err === "string") return err;
    if (err.message) return err.message;
    try {
      return JSON.stringify(err);
    } catch {
      return String(err);
    }
  }

  _areaName(areaId) {
    if (!areaId) return "—";
    const area = this._hass.areas && this._hass.areas[areaId];
    return area ? area.name : areaId;
  }

  _sortedAreaEntries() {
    const areas = (this._hass.areas && Object.values(this._hass.areas)) || [];
    return areas.slice().sort((a, b) => a.name.localeCompare(b.name));
  }

  _formatCountdown(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  // -- shell / chrome ---------------------------------------------------

  _render() {
    this.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = CSS_TEXT;
    this.appendChild(style);

    const root = document.createElement("div");
    root.className = "lm-root";
    this.appendChild(root);
    this._root = root;

    // Toolbar
    const toolbar = document.createElement("div");
    toolbar.className = "lm-toolbar";
    root.appendChild(toolbar);

    const tabs = document.createElement("div");
    tabs.className = "lm-tabs";
    this._tabButtons = {
      table: this._makeTabButton("Table", "table"),
      inbox: this._makeTabButton("Inbox", "inbox"),
    };
    tabs.append(this._tabButtons.table, this._tabButtons.inbox);
    toolbar.appendChild(tabs);

    const spacer = document.createElement("div");
    spacer.className = "lm-spacer";
    toolbar.appendChild(spacer);

    // Show-ignored toggle: Table-view only. Added 2026-09-14 - ignoring a
    // light previously left it sitting in the table forever with no way
    // to tell it had been dealt with; it now disappears by default but
    // stays reachable here for un-ignoring later.
    this._showIgnoredWrap = document.createElement("div");
    this._showIgnoredWrap.className = "lm-checkbox-row lm-toolbar-toggle";
    this._showIgnoredCheckbox = document.createElement("input");
    this._showIgnoredCheckbox.type = "checkbox";
    this._showIgnoredCheckbox.id = "lm-show-ignored";
    this._showIgnoredCheckbox.checked = this._showIgnored;
    this._showIgnoredCheckbox.addEventListener("change", () => {
      this._showIgnored = this._showIgnoredCheckbox.checked;
      this._renderView();
    });
    const showIgnoredLabel = document.createElement("label");
    showIgnoredLabel.htmlFor = "lm-show-ignored";
    showIgnoredLabel.textContent = "Show ignored";
    this._showIgnoredWrap.append(this._showIgnoredCheckbox, showIgnoredLabel);
    toolbar.appendChild(this._showIgnoredWrap);

    this._filterInput = document.createElement("input");
    this._filterInput.type = "search";
    this._filterInput.placeholder = "Filter…";
    this._filterInput.className = "lm-filter";
    this._filterInput.addEventListener("input", (e) => {
      this._filterText = e.target.value.toLowerCase();
      this._renderView();
    });
    toolbar.appendChild(this._filterInput);

    toolbar.appendChild(
      this._makeButton("Add a switch as a light", () => this._openPromoteSwitchDialog())
    );
    toolbar.appendChild(this._makeButton("Refresh", () => this._refreshAll()));

    // Error banner
    this._errorEl = document.createElement("div");
    this._errorEl.className = "lm-error";
    this._errorEl.hidden = true;
    root.appendChild(this._errorEl);

    // Main: body (table/inbox) + detail drawer, side by side on wide screens
    const main = document.createElement("div");
    main.className = "lm-main";
    root.appendChild(main);
    this._mainEl = main;

    this._bodyEl = document.createElement("div");
    this._bodyEl.className = "lm-body";
    main.appendChild(this._bodyEl);

    this._detailEl = document.createElement("div");
    this._detailEl.className = "lm-detail";
    this._detailEl.hidden = true;
    main.appendChild(this._detailEl);

    this._updateTabButtons();
  }

  _makeTabButton(label, view) {
    const btn = document.createElement("button");
    btn.className = "lm-tab-button";
    btn.textContent = label;
    btn.addEventListener("click", () => {
      this._view = view;
      this._updateTabButtons();
      this._renderView();
    });
    return btn;
  }

  _updateTabButtons() {
    for (const [view, btn] of Object.entries(this._tabButtons)) {
      btn.classList.toggle("active", view === this._view);
    }
    this._updateToolbarVisibility();
  }

  _updateToolbarVisibility() {
    // "Show ignored" only makes sense on the Table view - ignored lights
    // never appear in the Inbox regardless (they're steady-state, not
    // something needing attention).
    this._showIgnoredWrap.hidden = this._view !== "table";
    // ha-data-table has its own built-in search box (visible once rows
    // are rendered into it); the toolbar filter next to it was a second,
    // confusingly-duplicate search box on the Table view (2026-09-14
    // feedback). Keep the toolbar filter for the Inbox view (which has
    // no native search of its own) and for the Table view's plain-<table>
    // fallback, which also has no built-in search.
    const nativeTableActive = this._view === "table" && !!customElements.get("ha-data-table");
    this._filterInput.hidden = nativeTableActive;
  }

  _makeButton(label, onClick, { primary = false, disabled = false } = {}) {
    const btn = document.createElement("button");
    btn.className = primary ? "lm-btn primary" : "lm-btn";
    btn.textContent = label;
    btn.disabled = disabled;
    btn.addEventListener("click", onClick);
    return btn;
  }

  _showError(message) {
    this._errorEl.textContent = message;
    this._errorEl.hidden = false;
  }

  _clearError() {
    this._errorEl.hidden = true;
    this._errorEl.textContent = "";
  }

  _renderView() {
    if (this._view === "table") {
      this._renderTable();
    } else {
      this._renderInbox();
    }
  }

  // -- Table view --------------------------------------------------------

  _applyFilter(lights) {
    if (!this._filterText) return lights;
    const needle = this._filterText;
    return lights.filter((l) => {
      const haystack = [
        l.name,
        l.entity_id,
        this._areaName(l.area_id),
        l.light_type ? LIGHT_TYPE_LABELS[l.light_type] || l.light_type : "",
        l.state,
        STATUS_LABELS[l.status] || l.status,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }

  _applySort(lights) {
    const col = this._sortColumn;
    const dir = this._sortDirection === "desc" ? -1 : 1;
    const valueFor = (l) => {
      switch (col) {
        case "area":
          return this._areaName(l.area_id);
        case "light_type":
          return l.light_type ? LIGHT_TYPE_LABELS[l.light_type] || l.light_type : "";
        case "status":
          return STATUS_LABELS[l.status] || l.status;
        case "schedule_count":
          return l.schedule_count;
        default:
          return l[col] ?? "";
      }
    };
    return lights.slice().sort((a, b) => {
      const va = valueFor(a);
      const vb = valueFor(b);
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
  }

  _renderTable() {
    this._bodyEl.innerHTML = "";
    // Real feedback (2026-09-14): ignoring a light (e.g. a camera's IR
    // "light") left it sitting in the table forever, indistinguishable
    // from anything else - ignoring needs to actually remove it from the
    // main view, with "Show ignored" as the way back.
    const visibleLights = this._showIgnored
      ? this._lights
      : this._lights.filter((l) => !l.ignored);
    const filtered = this._applyFilter(visibleLights);

    if (filtered.length === 0) {
      const empty = document.createElement("div");
      empty.className = "lm-empty";
      empty.textContent = visibleLights.length === 0
        ? (this._showIgnored ? "No lights found yet." : "No lights found yet (or everything is ignored — try “Show ignored”).")
        : "No lights match your filter.";
      this._bodyEl.appendChild(empty);
      return;
    }

    if (customElements.get("ha-data-table")) {
      this._renderNativeDataTable(filtered);
    } else {
      this._renderFallbackTable(this._applySort(filtered));
    }
  }

  _tableColumns() {
    return {
      name: { title: "Name", sortable: true, filterable: true, direction: "asc", grows: true },
      area: { title: "Area", sortable: true, filterable: true },
      light_type: { title: "Type", sortable: true, filterable: true },
      state: { title: "State", sortable: true },
      status: { title: "Status", sortable: true },
      schedule_count: { title: "Schedules", sortable: true, type: "numeric" },
      countdown: { title: "Countdown", sortable: false },
    };
  }

  _toRow(light) {
    return {
      id: light.entity_id,
      name: light.name,
      area: this._areaName(light.area_id),
      light_type: light.light_type ? LIGHT_TYPE_LABELS[light.light_type] || light.light_type : "—",
      state: light.state,
      status: STATUS_LABELS[light.status] || light.status,
      schedule_count: light.schedule_count,
      countdown: this._formatCountdown(light.countdown_remaining_seconds),
    };
  }

  _ensureNativeDataTable() {
    if (this._dataTableEl) return this._dataTableEl;
    const table = document.createElement("ha-data-table");
    table.clickable = true;
    table.hass = this._hass;
    table.addEventListener("row-click", (ev) => {
      const id = ev.detail && ev.detail.id;
      if (id) this._selectEntity(id);
    });
    this._dataTableEl = table;
    return table;
  }

  _renderNativeDataTable(lights) {
    const table = this._ensureNativeDataTable();
    this._bodyEl.appendChild(table);
    table.columns = this._tableColumns();
    table.data = lights.map((l) => this._toRow(l));
    table.filter = ""; // filtering already applied above; avoid double-filtering
    table.noDataText = "No lights found.";
  }

  _renderFallbackTable(lights) {
    // Used only if a future HA frontend build removes ha-data-table.
    const table = document.createElement("table");
    table.className = "lm-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    const columns = [
      ["name", "Name"],
      ["area", "Area"],
      ["light_type", "Type"],
      ["state", "State"],
      ["status", "Status"],
      ["schedule_count", "Schedules"],
      ["countdown", "Countdown"],
    ];
    for (const [key, label] of columns) {
      const th = document.createElement("th");
      const arrow =
        this._sortColumn === key
          ? `<span class="sort-arrow">${this._sortDirection === "asc" ? "▲" : "▼"}</span>`
          : "";
      th.innerHTML = `${label} ${arrow}`;
      th.addEventListener("click", () => {
        if (this._sortColumn === key) {
          this._sortDirection = this._sortDirection === "asc" ? "desc" : "asc";
        } else {
          this._sortColumn = key;
          this._sortDirection = "asc";
        }
        this._renderTable();
      });
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (const light of lights) {
      const row = this._toRow(light);
      const tr = document.createElement("tr");
      if (light.entity_id === this._selectedEntityId) tr.classList.add("selected");
      tr.addEventListener("click", () => this._selectEntity(light.entity_id));
      for (const [key] of columns) {
        const td = document.createElement("td");
        td.textContent = String(row[key]);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    this._bodyEl.appendChild(table);
  }

  // -- Inbox view ---------------------------------------------------------

  _applyInboxFilter(items) {
    // The toolbar filter previously only applied to the Table view -
    // typing into it while on the Inbox silently did nothing
    // (2026-09-14 feedback: the Inbox needs area shown *and* to actually
    // be filterable, since it's often the longer of the two lists).
    if (!this._filterText) return items;
    const needle = this._filterText;
    return items.filter((item) => {
      const light = this._lightsById.get(item.entity_id);
      const haystack = [
        light ? light.name : "",
        item.entity_id,
        light ? this._areaName(light.area_id) : "",
        INBOX_LABELS[item.condition] || item.condition,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }

  _renderInbox() {
    this._bodyEl.innerHTML = "";
    const inboxItems = this._applyInboxFilter(this._inbox);

    if (inboxItems.length === 0) {
      const empty = document.createElement("div");
      empty.className = "lm-empty";
      empty.textContent = this._inbox.length === 0
        ? "Inbox is empty — nothing needs attention."
        : "No Inbox items match your filter.";
      this._bodyEl.appendChild(empty);
      return;
    }

    const groups = new Map();
    for (const item of inboxItems) {
      if (!groups.has(item.condition)) groups.set(item.condition, []);
      groups.get(item.condition).push(item);
    }

    // Stable, sensible order rather than insertion order of whatever the
    // backend happened to emit first.
    const order = [
      "new_light",
      "unassigned",
      "area_mismatch",
      "missing",
      "broken_schedule_reference",
      "scheduler_provider_unavailable",
    ];
    const orderedConditions = [
      ...order.filter((c) => groups.has(c)),
      ...[...groups.keys()].filter((c) => !order.includes(c)),
    ];

    for (const condition of orderedConditions) {
      const items = groups.get(condition);
      const section = document.createElement("div");
      section.className = "lm-inbox-group";
      const heading = document.createElement("h3");
      heading.textContent = `${INBOX_LABELS[condition] || condition} (${items.length})`;
      section.appendChild(heading);

      for (const item of items) {
        section.appendChild(this._renderInboxItem(item));
      }
      this._bodyEl.appendChild(section);
    }
  }

  _renderInboxItem(item) {
    const light = this._lightsById.get(item.entity_id);
    const row = document.createElement("div");
    row.className = "lm-inbox-item";
    row.addEventListener("click", (e) => {
      // Let inner buttons handle their own clicks without also opening
      // the drawer redundantly (harmless either way, but avoids a
      // double round-trip when a button already triggers one).
      if (e.target.closest("button")) return;
      this._selectEntity(item.entity_id);
    });

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = (light && light.name) || item.entity_id;
    row.appendChild(name);

    // Real feedback (2026-09-14): area was previously folded into the
    // free-text "meta" span, so it never lined up between rows. Broken
    // out into its own fixed-width span (.area, styled as a column
    // above) so it actually reads as a column down the list, same idea
    // as the Table view's Area column.
    const area = document.createElement("span");
    area.className = "area";
    area.textContent = light ? this._areaName(light.area_id) : "—";
    row.appendChild(area);

    const meta = document.createElement("span");
    meta.className = "meta";
    if (item.condition === "area_mismatch" && item.detail) {
      meta.textContent = `device: ${this._areaName(item.detail.device_area)} · entity: ${this._areaName(item.detail.entity_area)}`;
    } else if (item.condition === "missing" && item.detail && item.detail.since) {
      meta.textContent = `since ${new Date(item.detail.since).toLocaleString()}`;
    } else if (!light) {
      meta.textContent = item.entity_id;
    }
    if (meta.textContent) row.appendChild(meta);

    if (item.condition === "new_light") {
      row.appendChild(
        // "Adopt…" read as truncated text rather than a label (2026-09-14
        // feedback); this opens the detail drawer to review/edit the
        // light before adopting it, so "Review" says what actually
        // happens on click.
        this._makeButton("Review", (e) => {
          e.stopPropagation();
          this._selectEntity(item.entity_id);
        }, { primary: true })
      );
      row.appendChild(
        this._makeButton("Ignore", async (e) => {
          e.stopPropagation();
          await this._callAction("ignore_light", { entity_id: item.entity_id });
        })
      );
    } else if (item.condition === "area_mismatch" && item.detail) {
      row.appendChild(
        this._makeButton("Use device area", async (e) => {
          e.stopPropagation();
          await this._callAction("move_light", {
            entity_id: item.entity_id,
            area_id: item.detail.device_area,
          });
        })
      );
    } else if (item.condition === "unassigned") {
      row.appendChild(
        this._makeButton("Set area", (e) => {
          e.stopPropagation();
          this._selectEntity(item.entity_id);
        })
      );
    }

    return row;
  }

  // -- Detail drawer --------------------------------------------------------

  _selectEntity(entityId) {
    this._selectedEntityId = entityId;
    this._pendingAreaId = undefined;
    this._detailDirty = false;
    const light = this._lightsById.get(entityId);
    this._renderDetail(light || null);
    // Re-render the current list so the fallback table's "selected" row
    // highlight stays correct (native ha-data-table has no equivalent
    // concept here, which is fine - it's a nice-to-have, not load-bearing).
    if (this._view === "table" && !customElements.get("ha-data-table")) {
      this._renderTable();
    }
  }

  _closeDetail() {
    this._selectedEntityId = null;
    this._pendingAreaId = undefined;
    this._detailDirty = false;
    this._renderDetail(null);
    if (this._view === "table" && !customElements.get("ha-data-table")) {
      this._renderTable();
    }
  }

  _renderDetail(light) {
    this._detailEl.innerHTML = "";
    this._areaPickerEl = null;

    if (!light) {
      this._detailEl.hidden = true;
      this._mainEl.classList.remove("has-detail");
      return;
    }
    this._detailEl.hidden = false;
    this._mainEl.classList.add("has-detail");

    const closeBtn = document.createElement("button");
    closeBtn.className = "lm-close-btn";
    closeBtn.textContent = "×";
    closeBtn.title = "Close";
    closeBtn.addEventListener("click", () => this._closeDetail());
    this._detailEl.appendChild(closeBtn);

    const heading = document.createElement("h2");
    heading.textContent = light.name;
    this._detailEl.appendChild(heading);

    const entityIdEl = document.createElement("div");
    entityIdEl.className = "entity-id";
    entityIdEl.textContent = light.entity_id;
    this._detailEl.appendChild(entityIdEl);

    if (!light.adopted) {
      const badge = document.createElement("div");
      badge.className = "lm-warning";
      badge.textContent = light.ignored
        ? "This light is ignored — it won't appear in the Inbox until un-ignored."
        : "This light hasn't been adopted yet. Fill in the fields below, then Adopt.";
      this._detailEl.appendChild(badge);
    }

    if (light.area_mismatch) {
      const [deviceArea, entityArea] = light.area_mismatch;
      const warn = document.createElement("div");
      warn.className = "lm-warning";
      warn.textContent = `Area mismatch — device is in ${this._areaName(deviceArea)}, this entity is overridden to ${this._areaName(entityArea)}.`;
      this._detailEl.appendChild(warn);
    }

    // -- Name field --
    const nameField = this._makeFieldRow(
      "Name",
      () => {
        const input = document.createElement("input");
        input.type = "text";
        input.value = light.name;
        return input;
      },
      "Rename",
      async (input) => {
        const value = input.value.trim();
        if (!value) return;
        await this._callAction("rename_light", { entity_id: light.entity_id, name: value });
      }
    );
    this._nameInput = nameField.input;
    this._detailEl.appendChild(nameField.row);

    // -- Area field --
    const areaField = this._makeAreaFieldRow(light);
    this._detailEl.appendChild(areaField.row);

    // -- Light type field --
    const typeField = this._makeFieldRow(
      "Type",
      () => {
        const select = document.createElement("select");
        const noneOpt = document.createElement("option");
        noneOpt.value = "";
        noneOpt.textContent = "— none —";
        select.appendChild(noneOpt);
        for (const t of LIGHT_TYPES) {
          const opt = document.createElement("option");
          opt.value = t;
          opt.textContent = LIGHT_TYPE_LABELS[t] || t;
          select.appendChild(opt);
        }
        select.value = light.light_type || "";
        return select;
      },
      "Set type",
      async (select) => {
        await this._callAction("set_light_type", {
          entity_id: light.entity_id,
          light_type: select.value || null,
        });
      }
    );
    this._typeSelect = typeField.input;
    this._detailEl.appendChild(typeField.row);

    // -- Adopt / Ignore / Un-ignore --
    if (!light.adopted) {
      const adoptRow = document.createElement("div");
      adoptRow.className = "lm-field";
      adoptRow.appendChild(
        this._makeButton(
          "Adopt",
          async () => {
            const areaId = this._pendingAreaId !== undefined ? this._pendingAreaId : light.area_id;
            await this._callAction("adopt_light", {
              entity_id: light.entity_id,
              name: this._nameInput.value.trim() || undefined,
              area_id: areaId || undefined,
              light_type: this._typeSelect.value || undefined,
            });
          },
          { primary: true }
        )
      );
      if (light.ignored) {
        adoptRow.appendChild(
          this._makeButton("Un-ignore", async () => {
            await this._callAction("unignore_light", { entity_id: light.entity_id });
          })
        );
      } else {
        adoptRow.appendChild(
          this._makeButton("Ignore", async () => {
            await this._callAction("ignore_light", { entity_id: light.entity_id });
          })
        );
      }
      this._detailEl.appendChild(adoptRow);
    }

    // -- Dashboard included --
    const dashRow = document.createElement("div");
    dashRow.className = "lm-checkbox-row lm-field";
    const dashCheckbox = document.createElement("input");
    dashCheckbox.type = "checkbox";
    dashCheckbox.id = "lm-dash-included";
    dashCheckbox.checked = light.dashboard_included;
    dashCheckbox.addEventListener("change", async () => {
      await this._callAction("set_dashboard_included", {
        entity_id: light.entity_id,
        included: dashCheckbox.checked,
      });
    });
    const dashLabel = document.createElement("label");
    dashLabel.htmlFor = "lm-dash-included";
    dashLabel.textContent = "Include on dashboard";
    dashRow.append(dashCheckbox, dashLabel);
    this._detailEl.appendChild(dashRow);

    // -- Countdown --
    const countdownTitle = document.createElement("div");
    countdownTitle.className = "lm-section-title";
    countdownTitle.textContent = "Countdown";
    this._detailEl.appendChild(countdownTitle);

    if (light.countdown_remaining_seconds !== null && light.countdown_remaining_seconds !== undefined) {
      const activeRow = document.createElement("div");
      activeRow.className = "lm-field";
      activeRow.textContent = `Active — ${this._formatCountdown(light.countdown_remaining_seconds)} remaining`;
      this._detailEl.appendChild(activeRow);
      const cancelBtn = this._makeButton("Cancel countdown", async () => {
        await this._callAction("cancel_countdown", { entity_id: light.entity_id });
      });
      this._detailEl.appendChild(cancelBtn);
    } else {
      const presetRow = document.createElement("div");
      presetRow.className = "lm-preset-row";
      for (const minutes of COUNTDOWN_PRESETS_MIN) {
        presetRow.appendChild(
          this._makeButton(`${minutes} min`, async () => {
            await this._callAction("start_countdown", {
              entity_id: light.entity_id,
              minutes,
              turn_on_first: this._turnOnFirstCheckbox.checked,
            });
          })
        );
      }
      this._detailEl.appendChild(presetRow);

      const customRow = document.createElement("div");
      customRow.className = "lm-field-row";
      const customInput = document.createElement("input");
      customInput.type = "number";
      customInput.min = "1";
      customInput.placeholder = "Custom minutes";
      customRow.appendChild(customInput);
      customRow.appendChild(
        this._makeButton("Start", async () => {
          const minutes = parseFloat(customInput.value);
          if (!minutes || minutes <= 0) return;
          await this._callAction("start_countdown", {
            entity_id: light.entity_id,
            minutes,
            turn_on_first: this._turnOnFirstCheckbox.checked,
          });
        })
      );
      this._detailEl.appendChild(customRow);

      const turnOnRow = document.createElement("div");
      turnOnRow.className = "lm-checkbox-row lm-field";
      turnOnRow.style.marginTop = "8px";
      this._turnOnFirstCheckbox = document.createElement("input");
      this._turnOnFirstCheckbox.type = "checkbox";
      this._turnOnFirstCheckbox.id = "lm-turn-on-first";
      this._turnOnFirstCheckbox.checked = true;
      const turnOnLabel = document.createElement("label");
      turnOnLabel.htmlFor = "lm-turn-on-first";
      turnOnLabel.textContent = "Turn on first";
      turnOnRow.append(this._turnOnFirstCheckbox, turnOnLabel);
      this._detailEl.appendChild(turnOnRow);
    }

    if (light.schedule_count > 0) {
      const schedInfo = document.createElement("div");
      schedInfo.className = "lm-field";
      schedInfo.style.marginTop = "16px";
      schedInfo.innerHTML = `<span class="lm-badge">${light.schedule_count} schedule${light.schedule_count === 1 ? "" : "s"}</span>`;
      this._detailEl.appendChild(schedInfo);
    }
  }

  _makeFieldRow(labelText, buildInput, buttonText, onSave) {
    const row = document.createElement("div");
    row.className = "lm-field";
    const label = document.createElement("label");
    label.textContent = labelText;
    row.appendChild(label);

    const inputRow = document.createElement("div");
    inputRow.className = "lm-field-row";
    const input = buildInput();
    // Mark the drawer dirty on any edit so the 15s poll doesn't clobber
    // it mid-edit (see the _refreshAll fix above) - covers both a text
    // input (Name) and a <select> (Type) via the two events that fire
    // for user-driven changes on each.
    input.addEventListener("input", () => { this._detailDirty = true; });
    input.addEventListener("change", () => { this._detailDirty = true; });
    inputRow.appendChild(input);
    inputRow.appendChild(this._makeButton(buttonText, () => onSave(input)));
    row.appendChild(inputRow);

    return { row, input };
  }

  _makeAreaFieldRow(light) {
    const row = document.createElement("div");
    row.className = "lm-field";
    const label = document.createElement("label");
    label.textContent = "Area";
    row.appendChild(label);

    const inputRow = document.createElement("div");
    inputRow.className = "lm-field-row";

    let getValue;
    if (customElements.get("ha-area-picker")) {
      const picker = document.createElement("ha-area-picker");
      picker.hass = this._hass;
      picker.value = light.area_id || "";
      picker.addEventListener("value-changed", (ev) => {
        this._pendingAreaId = ev.detail.value || null;
        this._detailDirty = true;
      });
      this._areaPickerEl = picker;
      inputRow.appendChild(picker);
      getValue = () => (this._pendingAreaId !== undefined ? this._pendingAreaId : light.area_id);
    } else {
      const select = document.createElement("select");
      const noneOpt = document.createElement("option");
      noneOpt.value = "";
      noneOpt.textContent = "— none —";
      select.appendChild(noneOpt);
      for (const area of this._sortedAreaEntries()) {
        const opt = document.createElement("option");
        opt.value = area.area_id;
        opt.textContent = area.name;
        select.appendChild(opt);
      }
      select.value = light.area_id || "";
      select.addEventListener("change", () => {
        this._pendingAreaId = select.value || null;
        this._detailDirty = true;
      });
      inputRow.appendChild(select);
      getValue = () => select.value || null;
    }

    inputRow.appendChild(
      this._makeButton("Move", async () => {
        const areaId = getValue();
        if (!areaId) return; // move_light requires a target area
        await this._callAction("move_light", { entity_id: light.entity_id, area_id: areaId });
      })
    );
    row.appendChild(inputRow);
    return { row };
  }

  // -- Promote-switch dialog --------------------------------------------

  async _openPromoteSwitchDialog() {
    let entityIds = [];
    try {
      const result = await this._hass.callWS({ type: "lighting_manager/list_promotable_switches" });
      entityIds = result.entity_ids;
    } catch (err) {
      this._showError(`Could not load switches: ${this._formatError(err)}`);
      return;
    }

    // Switches already managed (promoted) shouldn't be offered again.
    const alreadyManaged = new Set(this._lights.map((l) => l.entity_id));
    entityIds = entityIds.filter((id) => !alreadyManaged.has(id));

    const overlay = document.createElement("div");
    overlay.className = "lm-overlay";
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });

    const box = document.createElement("div");
    box.className = "lm-overlay-box";
    overlay.appendChild(box);

    const heading = document.createElement("h2");
    heading.textContent = "Add a switch as a light";
    box.appendChild(heading);

    const hint = document.createElement("p");
    hint.className = "meta";
    hint.style.color = "var(--secondary-text-color)";
    hint.textContent =
      "Only switches you explicitly promote here show up as lights (Lighting Manager never assumes a switch controls a light).";
    box.appendChild(hint);

    if (entityIds.length === 0) {
      const empty = document.createElement("div");
      empty.className = "lm-empty";
      empty.textContent = "No unmanaged switch entities found.";
      box.appendChild(empty);
    }

    for (const entityId of entityIds) {
      const row = document.createElement("div");
      row.className = "lm-overlay-row";
      const state = this._hass.states[entityId];
      const name = document.createElement("span");
      name.textContent = (state && state.attributes && state.attributes.friendly_name) || entityId;
      row.appendChild(name);
      row.appendChild(
        this._makeButton(
          "Promote",
          async () => {
            const ok = await this._callAction("promote_switch", { entity_id: entityId });
            if (ok) overlay.remove();
          },
          { primary: true }
        )
      );
      box.appendChild(row);
    }

    box.appendChild(this._makeButton("Close", () => overlay.remove()));
    this.appendChild(overlay);
  }
}

customElements.define("lighting-manager-panel", LightingManagerPanel);
