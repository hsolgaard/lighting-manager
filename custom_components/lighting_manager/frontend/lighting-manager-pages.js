/**
 * Lighting Manager sidebar pages  v1.0.0
 * (PRD Revision - Lighting-Aware Dashboard Automation, section 4.10.)
 *
 * Two ready-made pages the integration puts in the sidebar by itself, so a
 * new user needs no YAML and no dashboard:
 *
 *   Lights           the whole house: what is on now, every light by room,
 *                    with a folded Schedules row under each room.
 *                    (The overview card, full page.)
 *   Schedules        one schedule list per room, for everyone in the house.
 *                    (The area-aware light scheduler card, once per room.)
 *
 * Both follow Lighting Manager: adopt a light or give a room a new light
 * and the page changes with no editing. They are ordinary Home Assistant
 * panels, so each person can hide them from their own sidebar
 * (Profile > Sidebar) and the same cards can still be dropped into any
 * dashboard by hand.
 */
const PAGE_REFRESH_MS = 60000;

class LightingManagerPageBase extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._narrow = false;
    this._built = false;
    this._menu = null;
    this._content = null;
  }

  get pageTitle() {
    return 'Lighting Manager';
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (this._menu) this._menu.hass = hass;
    this._pushHass(hass);
  }

  set narrow(narrow) {
    this._narrow = !!narrow;
    if (this._menu) this._menu.narrow = this._narrow;
  }

  // Home Assistant also sets these on every panel; nothing needs them.
  set route(_value) {}
  set panel(_value) {}

  _build() {
    this._built = true;
    this.style.cssText = 'display:block;min-height:100%;background:var(--primary-background-color);';

    const bar = document.createElement('div');
    bar.style.cssText =
      'display:flex;align-items:center;gap:8px;height:56px;padding:0 12px;box-sizing:border-box;' +
      'background:var(--app-header-background-color,var(--primary-color));' +
      'color:var(--app-header-text-color,var(--text-primary-color,#fff));';
    this._menu = document.createElement('ha-menu-button');
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    bar.appendChild(this._menu);
    const title = document.createElement('div');
    title.textContent = this.pageTitle;
    title.style.cssText = 'font-size:20px;font-weight:500;';
    // Say where the page comes from, matching its sidebar entry.
    const origin = document.createElement('span');
    origin.textContent = ' · Lighting Manager';
    origin.style.cssText = 'font-size:14px;font-weight:400;opacity:0.75;';
    title.appendChild(origin);
    bar.appendChild(title);
    this.appendChild(bar);

    this._content = document.createElement('div');
    this._content.style.cssText = 'max-width:960px;margin:0 auto;padding:16px;box-sizing:border-box;';
    this.appendChild(this._content);
    this._buildContent();
  }

  _buildContent() {}
  _pushHass(_hass) {}
}

class LightingManagerLightsPage extends LightingManagerPageBase {
  get pageTitle() {
    return 'Lights';
  }

  _buildContent() {
    this._card = document.createElement('lighting-manager-overview-card');
    try {
      this._card.setConfig({ title: '' });
    } catch (err) {
      this._content.textContent = `Lighting Manager could not draw this page: ${err.message || err}`;
      this._card = null;
      return;
    }
    this._content.appendChild(this._card);
  }

  _pushHass(hass) {
    if (this._card) this._card.hass = hass;
  }
}

class LightingManagerSchedulesPage extends LightingManagerPageBase {
  constructor() {
    super();
    this._cards = [];
    this._signature = null;
    this._timer = null;
    this._fetchToken = 0;
    this._loaded = false;
    this._error = null;
    this._areas = [];
  }

  get pageTitle() {
    return 'Schedules';
  }

  connectedCallback() {
    if (this._timer) return;
    this._timer = setInterval(() => {
      if (this._hass) this._fetch();
    }, PAGE_REFRESH_MS);
  }

  disconnectedCallback() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  _buildContent() {
    this._fetch();
  }

  _pushHass(hass) {
    for (const card of this._cards) card.hass = hass;
  }

  async _fetch() {
    if (!this._hass) return;
    const token = ++this._fetchToken;
    try {
      const result = await this._hass.callWS({ type: 'lighting_manager/list_lights' });
      if (token !== this._fetchToken) return;
      const areaIds = new Set();
      for (const light of result.lights || []) {
        if (light.adopted && !light.ignored && light.area_id) areaIds.add(light.area_id);
      }
      const areas = this._hass.areas || {};
      this._areas = [...areaIds]
        .map(id => ({ id, name: areas[id]?.name || id }))
        .sort((a, b) => a.name.localeCompare(b.name));
      this._error = null;
    } catch (err) {
      if (token !== this._fetchToken) return;
      this._error = err?.message || String(err);
    }
    this._loaded = true;
    this._render();
  }

  _render() {
    const signature = JSON.stringify([this._error, this._areas.map(a => a.id)]);
    if (signature === this._signature) return;
    this._signature = signature;
    this._content.innerHTML = '';
    this._cards = [];

    if (this._error) {
      this._message(`Could not load your lights: ${this._error}`);
      return;
    }
    if (this._areas.length === 0) {
      this._message(
        'No lights are set up yet. Open Lighting Manager, go to the Inbox tab, and adopt your lights ' +
        '(give each a room). They appear here automatically.'
      );
      return;
    }
    for (const area of this._areas) {
      const card = document.createElement('light-scheduler-list-card');
      try {
        card.setConfig({ area: area.id, title: area.name });
      } catch (err) {
        continue;
      }
      card.hass = this._hass;
      card.style.cssText = 'display:block;margin-bottom:16px;';
      this._content.appendChild(card);
      this._cards.push(card);
    }
  }

  _message(text) {
    const p = document.createElement('p');
    p.textContent = text;
    p.style.cssText = 'color:var(--secondary-text-color);line-height:1.5;';
    this._content.appendChild(p);
  }
}

if (!customElements.get('lighting-manager-lights-page')) {
  customElements.define('lighting-manager-lights-page', LightingManagerLightsPage);
}
if (!customElements.get('lighting-manager-schedules-page')) {
  customElements.define('lighting-manager-schedules-page', LightingManagerSchedulesPage);
}
