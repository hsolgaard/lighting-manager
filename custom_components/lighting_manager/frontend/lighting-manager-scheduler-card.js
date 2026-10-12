/**
 * Lighting Manager Scheduler Card  v1.1.0
 *
 * A thin, Area-aware wrapper around Niels Faber's Scheduler Card
 * (https://github.com/nielsfaber/scheduler-card, which must be installed).
 * Instead of hand-maintaining an `include:` list per room, you give it an
 * Area and Lighting Manager supplies the lights:
 *
 *   type: custom:lighting-manager-scheduler-card
 *   area: living_room          # Area id OR name ("Lounge Big")
 *   title: Lounge Big schedules
 *
 * Every other option (sort_by, display_options, exclude, time_step, tags,
 * show_add_button, ...) is passed straight through to the Scheduler Card.
 * Only lights adopted (and not ignored) in Lighting Manager are offered,
 * minus any with "Include in room scheduling" switched off in its panel.
 * Nothing of the Scheduler Card's code is copied - this builds the real
 * `custom:scheduler-card` at runtime and hands it the resolved list.
 *
 * (PRD Revision - Lighting-Aware Dashboard Automation, build step 4.)
 */
class LightingManagerSchedulerCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._lights = [];
    this._loaded = false;
    this._error = null;
    this._fetchToken = 0;
    this._fetchedArea = null;
    this._refreshTimer = null;
    this._inner = null;
    this._innerKey = null;
    this._building = false;
  }

  setConfig(config) {
    if (!config || typeof config.area !== 'string' || !config.area) {
      throw new Error(
        'area is required - an Area id (e.g. living_room) or Area name (e.g. "Lounge Big").'
      );
    }
    this.config = config;
    this._fetchedArea = null;
    this._loaded = false;
    this._error = null;
    this._lights = [];
    this._inner = null;
    this._innerKey = null;
    if (this._hass) this._fetchLights();
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this.config && this._fetchedArea !== this.config.area) {
      this._fetchLights();
    }
    if (this._inner) this._inner.hass = hass;
  }

  connectedCallback() {
    if (this._refreshTimer) return;
    // Adoption changes rarely; a slow poll keeps the include list in sync
    // without a dashboard reload (and without hammering the websocket
    // from the hass setter, which fires on every state change).
    this._refreshTimer = setInterval(() => {
      if (this._hass && this.config) this._fetchLights();
    }, 60000);
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  getCardSize() {
    return this._inner?.getCardSize ? this._inner.getCardSize() : 4;
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
      const result = await this._hass.connection.sendMessagePromise({
        type: 'lighting_manager/list_area_lights',
        area_id: resolved.id
      });
      if (token !== this._fetchToken) return;
      // Lights switched off for room scheduling in Lighting Manager are left out.
      this._lights = (result.lights || [])
        .filter(l => l.scheduling_included !== false)
        .map(l => l.entity_id);
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

  _message(text, isError = false) {
    this._inner = null;
    this._innerKey = null;
    this.innerHTML = `
      <ha-card>
        <div style="padding:14px 16px;font-size:13px;color:${
          isError ? 'var(--error-color)' : 'var(--secondary-text-color)'
        }">${isError ? '⚠️ ' : ''}${this._escape(text)}</div>
      </ha-card>`;
  }

  _escape(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[c]));
  }

  async _render() {
    if (!this.config) return;

    if (this._error) return this._message(this._error, true);
    if (!this._loaded) return this._message('Loading…');
    if (!this._lights.length) {
      return this._message(
        'No lights available for scheduling in this Area - adopt some in Lighting Manager (and check "Include in room scheduling").'
      );
    }

    // Rebuild the inner Scheduler Card only when its configuration actually
    // changes (different light set), not on every refresh tick.
    const key = this._lights.join('|');
    if (this._inner && this._innerKey === key) return;
    if (this._building) return;
    this._building = true;

    try {
      const { type, area, include, ...passthrough } = this.config; // eslint-disable-line no-unused-vars
      const innerConfig = {
        discover_existing: false,
        ...passthrough,
        type: 'custom:scheduler-card',
        include: this._lights
      };

      const helpers = window.loadCardHelpers ? await window.loadCardHelpers() : null;
      if (!helpers) throw new Error('Home Assistant card helpers are not available.');

      const el = await helpers.createCardElement(innerConfig);
      if (this._hass) el.hass = this._hass;

      this._inner = el;
      this._innerKey = key;
      this.innerHTML = '';
      this.appendChild(el);
    } catch (err) {
      this._message(
        `Could not create the Scheduler Card - is it installed? (${err?.message || err})`,
        true
      );
    } finally {
      this._building = false;
    }
  }
}

if (!customElements.get('lighting-manager-scheduler-card')) {
  customElements.define('lighting-manager-scheduler-card', LightingManagerSchedulerCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some(x => x.type === 'lighting-manager-scheduler-card')) {
  window.customCards.push({
    type: 'lighting-manager-scheduler-card',
    name: 'Lighting Manager Scheduler Card',
    description:
      "Area-aware wrapper around Niels Faber's Scheduler Card: lights come from Lighting Manager."
  });
}

console.info(
  '%c LIGHTING-MANAGER-SCHEDULER-CARD %c v1.1.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
