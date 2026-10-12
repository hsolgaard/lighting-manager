/**
 * Lighting Manager Room Card  v1.0.0
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
 *   - Order                 (the manual order, else alphabetical)
 *
 * Optional: `columns: 3` (default 2) for a wider grid.
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
      this._lights = (result.lights || [])
        .filter(l => l.dashboard_included !== false)
        .map(l => ({ entity_id: l.entity_id, width: l.tile_width === 'full' ? 'full' : 'half' }));
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
    const key =
      this.config.columns + '|' + this._lights.map(l => `${l.entity_id}:${l.width}`).join(',');
    if (this._renderedKey === key) return;

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
      tile.setConfig({ type: 'custom:lighting-manager-countdown-card', entity: light.entity_id });
      if (this._hass) tile.hass = this._hass;

      cell.appendChild(tile);
      grid.appendChild(cell);
      tiles.push(tile);
    }

    this._tiles = tiles;
    this._renderedKey = key;
    this.innerHTML = '';
    this.appendChild(grid);
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
  '%c LIGHTING-MANAGER-ROOM-CARD %c v1.0.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
