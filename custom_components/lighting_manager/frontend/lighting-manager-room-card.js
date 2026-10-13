/**
 * Lighting Manager Room Card  v1.2.0
 * (PRD Revision - Lighting-Aware Dashboard Automation, section 4.6.)
 *
 * Drop this into any dashboard, in any view, next to any other cards:
 *
 *   type: custom:lighting-manager-room-card
 *   area: living_room          # Area id OR name ("Lounge Big")
 *
 * It asks Lighting Manager for the Area's lights and draws a tile for
 * each one - the same native tile + double-tap countdown as
 * custom:lighting-manager-countdown-card. Nothing is generated or saved:
 * add, rename, move, reorder or hide a light in Lighting Manager and
 * every dashboard using this card follows on its own.
 *
 * Per-light settings come from Lighting Manager (edited in its panel):
 *   - Include on dashboard  (off = the light is not drawn here)
 *   - Name                  (the light's Home Assistant name)
 *   - Tile width            (half = two per row, full = one per row)
 *   - Brightness slider     (a slider under the tile; off by default)
 *   - Order                 (the manual order, else alphabetical)
 *
 * Optional: `columns: 3` (default 2) for a wider grid.
 *
 * Reordering (administrators only): a small "Reorder" button above the tiles
 * switches the card to a list with up/down arrows. The order is saved in
 * Lighting Manager (the same order the light list card uses), so it follows
 * the room everywhere. Everyone else sees only the tiles.
 *
 * Only adopted, non-ignored lights appear, and only lights - other
 * domains in the room stay as your own cards. Any user can view and use
 * the tiles; editing the settings needs an admin in the panel.
 */
const ROOM_REFRESH_MS = 60000;

class LightingManagerRoomCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this.config = null;
    this._lights = [];
    this._loaded = false;
    this._error = null;
    this._fetchToken = 0;
    this._fetchedArea = null;
    this._refreshTimer = null;
    this._tiles = [];
    this._renderedKey = null;
    this._renderToken = 0;
    this._reorderMode = false;
    this._order = [];
    this._areaId = null;
    this._saving = false;
    this._notice = null;
    this._admin = false;
  }

  setConfig(config) {
    if (!config || typeof config.area !== 'string' || !config.area) {
      throw new Error(
        'area is required - an Area id (e.g. living_room) or Area name (e.g. "Lounge Big").'
      );
    }
    const columns = config.columns === undefined ? 2 : Number(config.columns);
    if (!Number.isInteger(columns) || columns < 1 || columns > 6) {
      throw new Error('columns must be a whole number from 1 to 6.');
    }
    this.config = { ...config, columns };
    this._fetchedArea = null;
    this._fetchingArea = null;
    this._loaded = false;
    this._error = null;
    this._lights = [];
    this._renderedKey = null;
    this._reorderMode = false;
    if (this._hass) this._fetchLights();
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (
      this.config &&
      this._fetchedArea !== this.config.area &&
      this._fetchingArea !== this.config.area
    ) {
      this._fetchLights();
    }
    for (const tile of this._tiles) tile.hass = hass;

    // The Reorder button is for administrators; redraw if that is now known.
    const admin = !!hass?.user?.is_admin;
    if (admin !== this._admin) {
      this._admin = admin;
      if (this._loaded) this._render();
    }
  }

  connectedCallback() {
    if (this._refreshTimer) return;
    this._refreshTimer = setInterval(() => {
      if (this._hass && this.config) this._fetchLights();
    }, ROOM_REFRESH_MS);
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  getCardSize() {
    const cols = this.config?.columns || 2;
    return Math.max(1, Math.ceil(this._lights.length / cols)) * 1;
  }

  getGridOptions() {
    return { columns: 'full' };
  }

  _resolveArea() {
    const value = this.config?.area;
    const areas = this._hass?.areas || {};
    if (!value || !Object.keys(areas).length) return { id: value };
    if (areas[value]) return { id: value };

    const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '');
    const hit = Object.values(areas).find(a => norm(a.name) === norm(value));
    if (hit) return { id: hit.area_id };

    const known = Object.values(areas).map(a => `${a.name} (${a.area_id})`).sort().join(', ');
    return { id: null, error: `Area "${value}" not found. Known Areas: ${known}` };
  }

  async _fetchLights() {
    if (!this._hass || !this.config) return;
    if (this._saving) return; // do not overwrite an order being saved

    const token = ++this._fetchToken;
    const areaBeingFetched = this.config.area;
    this._fetchingArea = areaBeingFetched;
    const resolved = this._resolveArea();

    if (!resolved.id) {
      this._fetchedArea = areaBeingFetched;
      this._loaded = true;
      this._error = resolved.error;
      this._lights = [];
      this._render();
      return;
    }

    try {
      const result = await this._hass.callWS({
        type: 'lighting_manager/list_area_lights',
        area_id: resolved.id
      });
      if (token !== this._fetchToken) return;
      this._areaId = resolved.id;
      this._order = (result.lights || []).map(l => l.entity_id);
      this._lights = (result.lights || [])
        .filter(l => l.dashboard_included !== false)
        .map(l => ({
          entity_id: l.entity_id,
          width: l.tile_width === 'full' ? 'full' : 'half',
          slider: l.brightness_slider === true
        }));
      this._error = null;
    } catch (err) {
      if (token !== this._fetchToken) return;
      this._error =
        err?.message || String(err) ||
        'Could not reach Lighting Manager - is the integration installed and running?';
    }

    this._fetchedArea = areaBeingFetched;
    this._loaded = true;
    this._render();
  }

  _escape(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[c]));
  }

  _message(text, isError = false) {
    this._tiles = [];
    this._renderedKey = null;
    this.innerHTML = `
      <ha-card>
        <div style="padding:14px 16px;font-size:13px;color:${
          isError ? 'var(--error-color)' : 'var(--secondary-text-color)'
        }">${isError ? '⚠️ ' : ''}${this._escape(text)}</div>
      </ha-card>`;
  }

  async _render() {
    if (!this.config) return;

    if (this._error) return this._message(this._error, true);
    if (!this._loaded) return this._message('Loading…');
    if (!this._lights.length) {
      return this._message('No lights to show for this Area yet - adopt some in Lighting Manager.');
    }

    // Rebuild only when the lights, their order, widths or the column
    // count change - not on every refresh tick - so an open countdown
    // popup or a mid-tap tile is never torn down by the poll.
    const reorder = this._reorderMode && this._admin;
    const key =
      this.config.columns + '|' + (reorder ? 'R' : this._admin ? 'A' : '-') + '|' +
      (this._notice || '') + '|' +
      this._lights.map(l => `${l.entity_id}:${l.width}:${l.slider ? 's' : ''}`).join(',');
    if (this._renderedKey === key) return;

    if (reorder) {
      this._renderToken++; // cancels any tile build still waiting
      this._renderReorder();
      this._renderedKey = key;
      return;
    }

    const token = ++this._renderToken;

    // The countdown card is bundled and loaded alongside this one, but
    // script order is not guaranteed - wait for its definition.
    try {
      await Promise.race([
        customElements.whenDefined('lighting-manager-countdown-card'),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000))
      ]);
    } catch (err) {
      if (token === this._renderToken) {
        this._message('The Lighting Manager countdown card did not load - reload the page.', true);
      }
      return;
    }
    if (token !== this._renderToken) return;

    const grid = document.createElement('div');
    grid.style.cssText =
      `display:grid;grid-template-columns:repeat(${this.config.columns},minmax(0,1fr));` +
      'gap:var(--lm-room-gap, 8px);';

    const tiles = [];
    for (const light of this._lights) {
      const cell = document.createElement('div');
      cell.style.cssText = light.width === 'full' ? 'grid-column:1 / -1;min-width:0;' : 'min-width:0;';

      const tile = document.createElement('lighting-manager-countdown-card');
      const tileConfig = { type: 'custom:lighting-manager-countdown-card', entity: light.entity_id };
      if (light.slider) {
        // Home Assistant's own brightness slider, under the tile. It only
        // appears for lights that can dim.
        tileConfig.features = [{ type: 'light-brightness' }];
        tileConfig.features_position = 'bottom';
      }
      tile.setConfig(tileConfig);
      if (this._hass) tile.hass = this._hass;

      cell.appendChild(tile);
      grid.appendChild(cell);
      tiles.push(tile);
    }

    this._tiles = tiles;
    this._renderedKey = key;
    this.innerHTML = '';

    // Administrators get a small Reorder button above the tiles.
    if (this._admin) {
      const bar = document.createElement('div');
      bar.style.cssText = 'display:flex;justify-content:flex-end;margin:0 0 4px;';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.setAttribute('aria-label', 'Reorder lights');
      btn.style.cssText =
        'display:inline-flex;align-items:center;gap:4px;border:0;background:transparent;' +
        'color:var(--secondary-text-color);font:inherit;font-size:12px;cursor:pointer;padding:2px 4px;';
      btn.innerHTML = '<ha-icon icon="mdi:swap-vertical" style="--mdc-icon-size:16px;display:flex"></ha-icon>Reorder';
      btn.addEventListener('click', () => {
        this._reorderMode = true;
        this._notice = null;
        this._render();
      });
      bar.appendChild(btn);
      this.appendChild(bar);
    }
    this.appendChild(grid);
  }

  // ---- reordering (administrators) -----------------------------------

  _lightName(entityId) {
    return this._hass?.states?.[entityId]?.attributes?.friendly_name || entityId;
  }

  _renderReorder() {
    this._tiles = [];
    const last = this._lights.length - 1;
    const rows = this._lights
      .map(
        (l, i) => `
        <div class="lm-ro-row">
          <div class="lm-ro-name">${this._escape(this._lightName(l.entity_id))}</div>
          <button class="lm-ro-btn" data-up="${i}" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>
            <ha-icon icon="mdi:chevron-up"></ha-icon>
          </button>
          <button class="lm-ro-btn" data-down="${i}" aria-label="Move down" ${i === last ? 'disabled' : ''}>
            <ha-icon icon="mdi:chevron-down"></ha-icon>
          </button>
        </div>`
      )
      .join('');

    this.innerHTML = `
      <ha-card>
        <style>
          .lm-ro-head { display:flex; align-items:center; justify-content:space-between;
            padding:12px 16px 4px; font-weight:500; }
          .lm-ro-done { border:0; border-radius:12px; padding:6px 14px; font:inherit; font-size:14px;
            cursor:pointer; background:var(--primary-color); color:var(--text-primary-color,#fff); }
          .lm-ro-help { padding:0 16px 8px; font-size:12px; color:var(--secondary-text-color); }
          .lm-ro-row { display:flex; align-items:center; gap:8px; padding:4px 8px 4px 16px;
            border-top:1px solid var(--divider-color); }
          .lm-ro-name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .lm-ro-btn { width:40px; height:40px; border:0; border-radius:50%; background:transparent;
            color:var(--primary-text-color); cursor:pointer; display:flex; align-items:center;
            justify-content:center; }
          .lm-ro-btn[disabled] { opacity:.3; cursor:default; }
          .lm-ro-notice { padding:8px 16px; font-size:13px; color:var(--error-color); }
        </style>
        <div class="lm-ro-head"><span>Reorder lights</span>
          <button class="lm-ro-done" data-done>Done</button></div>
        <div class="lm-ro-help">Most used at the top. Saved for this room in Lighting Manager.</div>
        ${this._notice ? `<div class="lm-ro-notice">⚠️ ${this._escape(this._notice)}</div>` : ''}
        ${rows}
      </ha-card>`;

    this.querySelector('[data-done]')?.addEventListener('click', () => {
      this._reorderMode = false;
      this._notice = null;
      this._render();
    });
    this.querySelectorAll('[data-up]').forEach(el =>
      el.addEventListener('click', () => this._move(Number(el.getAttribute('data-up')), -1))
    );
    this.querySelectorAll('[data-down]').forEach(el =>
      el.addEventListener('click', () => this._move(Number(el.getAttribute('data-down')), 1))
    );
  }

  async _move(index, delta) {
    const target = index + delta;
    if (this._saving || target < 0 || target >= this._lights.length) return;
    const lights = [...this._lights];
    [lights[index], lights[target]] = [lights[target], lights[index]];
    this._lights = lights;
    this._notice = null;
    this._render();
    await this._saveOrder();
  }

  async _saveOrder() {
    if (!this._hass || !this._areaId) return;
    const visible = this._lights.map(l => l.entity_id);
    // Lights hidden from the dashboard keep a place at the end of the saved order.
    const rest = this._order.filter(id => !visible.includes(id));
    this._saving = true;
    let failed = false;
    try {
      await this._hass.callWS({
        type: 'lighting_manager/set_area_light_order',
        area_id: this._areaId,
        entity_ids: [...visible, ...rest]
      });
      this._order = [...visible, ...rest];
    } catch (err) {
      failed = true;
      this._notice = 'Could not save the order: ' + (err?.message || String(err));
    }
    this._saving = false;
    if (failed) {
      this._fetchedArea = null; // reload the order Lighting Manager actually has
      this._fetchLights();
    }
  }
}

if (!customElements.get('lighting-manager-room-card')) {
  customElements.define('lighting-manager-room-card', LightingManagerRoomCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some(x => x.type === 'lighting-manager-room-card')) {
  window.customCards.push({
    type: 'lighting-manager-room-card',
    name: 'Lighting Manager Room Card',
    description:
      "Draws an Area's lights as tiles using each light's Lighting Manager settings, with a double-tap countdown."
  });
}

console.info(
  '%c LIGHTING-MANAGER-ROOM-CARD %c v1.2.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
