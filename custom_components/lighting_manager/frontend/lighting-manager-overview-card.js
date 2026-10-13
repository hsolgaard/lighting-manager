/**
 * Lighting Manager Overview Card  v1.0.0
 * (PRD Revision - Lighting-Aware Dashboard Automation, section 4.8.)
 *
 * One card, a whole lighting page:
 *
 *   type: custom:lighting-manager-overview-card
 *
 * Optional: `title: Lights` (default), `columns: 2` (1-6), and
 * `show_schedules: false` to hide the per-light clock.
 *
 * What it draws, with nothing to maintain:
 *   - "On now": every light that is on, with its room, and Turn all off.
 *   - One section per room, listing every light Lighting Manager has
 *     adopted (the "Include on dashboard" tick is not used here: this is
 *     the complete picture of the house). Each room has its own on/off
 *     buttons and can be folded away (remembered on this device).
 *   - Each light is the same native tile as everywhere else (tap toggles,
 *     double-tap opens the countdown), with the light's Lighting Manager
 *     tile width and brightness slider. A small clock on the tile shows
 *     how many schedules the light has and opens its schedule dialog.
 *
 * Add a room, adopt a light, rename or reorder in Lighting Manager and the
 * card follows. Any user can use it; there is no editing here.
 */
const OVERVIEW_REFRESH_MS = 60000;
const OVERVIEW_COLLAPSED_KEY = 'lighting-manager-overview-collapsed';

class LightingManagerOverviewCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this.config = null;
    this._lights = [];
    this._loaded = false;
    this._error = null;
    this._started = false;
    this._fetchToken = 0;
    this._refreshTimer = null;
    this._renderedKey = null;
    this._renderToken = 0;
    this._ready = false;
    this._roomTiles = [];
    this._nowTiles = [];
    this._nowKey = null;
    this._rooms = [];
    this._helper = null;
    this._collapsed = this._loadCollapsed();
  }

  setConfig(config) {
    const columns = config?.columns === undefined ? 2 : Number(config.columns);
    if (!Number.isInteger(columns) || columns < 1 || columns > 6) {
      throw new Error('columns must be a whole number from 1 to 6.');
    }
    this.config = {
      ...(config || {}),
      columns,
      title: config?.title === undefined ? 'Lights' : config.title,
      show_schedules: config?.show_schedules !== false
    };
    this._renderedKey = null;
    this._ready = false;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._fetchLights();
    }
    for (const tile of this._roomTiles) tile.hass = hass;
    for (const tile of this._nowTiles) tile.hass = hass;
    this._updateLive();
  }

  connectedCallback() {
    if (this._refreshTimer) return;
    this._refreshTimer = setInterval(() => {
      if (this._hass) this._fetchLights();
    }, OVERVIEW_REFRESH_MS);
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
    if (this._helper && this._helper._closeScheduler) this._helper._closeScheduler();
  }

  getCardSize() {
    return Math.max(4, Math.ceil(this._lights.length / (this.config?.columns || 2)) + this._rooms.length);
  }

  getGridOptions() {
    return { columns: 'full' };
  }

  // ---- data ------------------------------------------------------------

  async _fetchLights() {
    if (!this._hass) return;
    const token = ++this._fetchToken;
    try {
      const result = await this._hass.callWS({ type: 'lighting_manager/list_lights' });
      if (token !== this._fetchToken) return;
      this._lights = (result.lights || [])
        .filter(l => l.adopted && !l.ignored)
        .map(l => ({
          entity_id: l.entity_id,
          name: l.name,
          area_id: l.area_id || '',
          width: l.tile_width === 'full' ? 'full' : 'half',
          slider: l.brightness_slider === true,
          schedules: Number(l.schedule_count) || 0,
          schedulable: l.scheduling_included !== false,
          order: l.area_order === null || l.area_order === undefined ? Infinity : l.area_order
        }));
      this._error = null;
    } catch (err) {
      if (token !== this._fetchToken) return;
      this._error =
        err?.message || String(err) ||
        'Could not reach Lighting Manager - is the integration installed and running?';
    }
    this._loaded = true;
    this._render();
  }

  _loadCollapsed() {
    try {
      const raw = window.localStorage.getItem(OVERVIEW_COLLAPSED_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(list) ? list : []);
    } catch (err) {
      return new Set();
    }
  }

  _saveCollapsed() {
    try {
      window.localStorage.setItem(OVERVIEW_COLLAPSED_KEY, JSON.stringify([...this._collapsed]));
    } catch (err) {
      /* remembering the folded rooms is a convenience only */
    }
  }

  _lightName(light) {
    return this._hass?.states?.[light.entity_id]?.attributes?.friendly_name || light.name || light.entity_id;
  }

  _areaName(areaId) {
    if (!areaId) return 'Other lights';
    return this._hass?.areas?.[areaId]?.name || areaId;
  }

  _isOn(entityId) {
    return this._hass?.states?.[entityId]?.state === 'on';
  }

  _escape(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[c]));
  }

  _message(text, isError = false) {
    this._ready = false;
    this._renderedKey = null;
    this._roomTiles = [];
    this._nowTiles = [];
    this.innerHTML = `
      <ha-card>
        <div style="padding:14px 16px;font-size:13px;color:${
          isError ? 'var(--error-color)' : 'var(--secondary-text-color)'
        }">${isError ? '⚠️ ' : ''}${this._escape(text)}</div>
      </ha-card>`;
  }

  _groupRooms() {
    const byArea = new Map();
    for (const light of this._lights) {
      if (!byArea.has(light.area_id)) byArea.set(light.area_id, []);
      byArea.get(light.area_id).push(light);
    }
    const rooms = [...byArea.entries()].map(([areaId, lights]) => ({
      areaId,
      name: this._areaName(areaId),
      lights: lights.sort(
        (a, b) =>
          (a.order === b.order ? 0 : a.order < b.order ? -1 : 1) ||
          this._lightName(a).localeCompare(this._lightName(b))
      )
    }));
    rooms.sort((a, b) => {
      if (!a.areaId) return 1;
      if (!b.areaId) return -1;
      return a.name.localeCompare(b.name);
    });
    return rooms;
  }

  // ---- drawing -----------------------------------------------------------

  _makeTile(entityId, slider) {
    const tile = document.createElement('lighting-manager-countdown-card');
    const tileConfig = { type: 'custom:lighting-manager-countdown-card', entity: entityId };
    if (slider) {
      tileConfig.features = [{ type: 'light-brightness' }];
      tileConfig.features_position = 'bottom';
    }
    tile.setConfig(tileConfig);
    if (this._hass) tile.hass = this._hass;
    return tile;
  }

  _iconButton(icon, label, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'lm-ov-icon-btn';
    btn.setAttribute('aria-label', label);
    btn.title = label;
    btn.innerHTML = `<ha-icon icon="${icon}"></ha-icon>`;
    btn.addEventListener('click', onClick);
    return btn;
  }

  async _render() {
    if (!this.config) return;
    if (this._error) return this._message(this._error, true);
    if (!this._loaded) return this._message('Loading…');
    if (!this._lights.length) {
      return this._message('No lights yet - adopt some in Lighting Manager.');
    }

    const rooms = this._groupRooms();
    const key =
      this.config.columns + '|' + this.config.title + '|' + this.config.show_schedules + '|' +
      rooms
        .map(
          r =>
            r.areaId + ':' + r.name + '=' +
            r.lights
              .map(l => `${l.entity_id}:${l.width}:${l.slider ? 's' : ''}:${l.schedules}:${l.schedulable ? '' : 'x'}`)
              .join(',')
        )
        .join('|');
    if (this._renderedKey === key) return;

    const token = ++this._renderToken;
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

    this._rooms = rooms;
    this._roomTiles = [];
    this._nowTiles = [];
    this._nowKey = null;
    this._renderedKey = key;

    const root = document.createElement('div');
    root.className = 'lm-ov';
    const style = document.createElement('style');
    style.textContent = `
      .lm-ov { display:flex; flex-direction:column; gap:20px; }
      .lm-ov-title { font-size:20px; font-weight:500; margin:0; padding:0 2px; }
      .lm-ov-head { display:flex; align-items:center; gap:8px; margin:0 0 8px; min-height:36px; }
      .lm-ov-head-main { display:flex; align-items:center; gap:8px; flex:1; min-width:0;
        cursor:pointer; background:transparent; border:0; padding:0; font:inherit;
        color:var(--primary-text-color); text-align:left; }
      .lm-ov-head-main ha-icon { --mdc-icon-size:22px; color:var(--secondary-text-color); flex:none; }
      .lm-ov-room-name { font-size:16px; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .lm-ov-chip { flex:none; font-size:12px; padding:2px 9px; border-radius:10px;
        background:var(--secondary-background-color); color:var(--secondary-text-color); }
      .lm-ov-chip.on { background:var(--state-light-active-color, var(--primary-color)); color:#fff; }
      .lm-ov-icon-btn { flex:none; width:36px; height:36px; border:0; border-radius:50%;
        background:transparent; color:var(--secondary-text-color); cursor:pointer;
        display:flex; align-items:center; justify-content:center; }
      .lm-ov-icon-btn:hover:not(:disabled) { background:var(--secondary-background-color); color:var(--primary-text-color); }
      .lm-ov-icon-btn:disabled { opacity:.3; cursor:default; }
      .lm-ov-alloff { flex:none; border:0; border-radius:16px; padding:7px 14px; font:inherit; font-size:14px;
        cursor:pointer; background:var(--primary-color); color:var(--text-primary-color,#fff); }
      .lm-ov-alloff:disabled { opacity:.35; cursor:default; }
      .lm-ov-grid { display:grid; gap:var(--lm-room-gap, 8px); }
      .lm-ov-cell { position:relative; min-width:0; }
      .lm-ov-area { font-size:11px; color:var(--secondary-text-color); padding:0 4px 2px;
        overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .lm-ov-sched { position:absolute; top:4px; right:6px; z-index:1; display:inline-flex; align-items:center;
        gap:2px; border:0; border-radius:12px; padding:2px 6px 2px 4px; font:inherit; font-size:11px;
        cursor:pointer; background:var(--card-background-color); color:var(--secondary-text-color);
        box-shadow:0 0 0 1px var(--divider-color); opacity:.75; }
      .lm-ov-sched.has { color:var(--primary-color); opacity:1; }
      .lm-ov-sched ha-icon { --mdc-icon-size:14px; display:flex; }
      .lm-ov-empty { font-size:13px; color:var(--secondary-text-color); padding:4px 4px 8px; }
    `;
    root.appendChild(style);

    if (this.config.title) {
      const h = document.createElement('h2');
      h.className = 'lm-ov-title';
      h.textContent = this.config.title;
      root.appendChild(h);
    }

    // On now ---------------------------------------------------------------
    const now = document.createElement('section');
    const nowHead = document.createElement('div');
    nowHead.className = 'lm-ov-head';
    const nowTitle = document.createElement('div');
    nowTitle.className = 'lm-ov-head-main';
    nowTitle.style.cursor = 'default';
    nowTitle.innerHTML =
      '<ha-icon icon="mdi:lightbulb-on-outline"></ha-icon><span class="lm-ov-room-name">On now</span>';
    this._nowChip = document.createElement('span');
    this._nowChip.className = 'lm-ov-chip';
    nowTitle.appendChild(this._nowChip);
    nowHead.appendChild(nowTitle);
    this._allOffBtn = document.createElement('button');
    this._allOffBtn.type = 'button';
    this._allOffBtn.className = 'lm-ov-alloff';
    this._allOffBtn.textContent = 'Turn all off';
    this._allOffBtn.addEventListener('click', () => this._turnOff(this._lights.map(l => l.entity_id)));
    nowHead.appendChild(this._allOffBtn);
    now.appendChild(nowHead);
    this._nowGrid = document.createElement('div');
    this._nowGrid.className = 'lm-ov-grid';
    this._nowGrid.style.gridTemplateColumns = `repeat(${this.config.columns},minmax(0,1fr))`;
    now.appendChild(this._nowGrid);
    this._nowEmpty = document.createElement('div');
    this._nowEmpty.className = 'lm-ov-empty';
    this._nowEmpty.textContent = 'No lights are on.';
    now.appendChild(this._nowEmpty);
    root.appendChild(now);

    // Rooms ------------------------------------------------------------------
    this._roomRefs = [];
    for (const room of rooms) {
      root.appendChild(this._buildRoom(room));
    }

    this.innerHTML = '';
    this.appendChild(root);
    this._ready = true;
    this._updateLive();
  }

  _buildRoom(room) {
    const section = document.createElement('section');
    const collapseKey = room.areaId || '__none__';

    const head = document.createElement('div');
    head.className = 'lm-ov-head';
    const main = document.createElement('button');
    main.type = 'button';
    main.className = 'lm-ov-head-main';
    const area = this._hass?.areas?.[room.areaId];
    const chevron = document.createElement('ha-icon');
    const icon = document.createElement('ha-icon');
    icon.setAttribute('icon', area?.icon || 'mdi:lightbulb-group-outline');
    const name = document.createElement('span');
    name.className = 'lm-ov-room-name';
    name.textContent = room.name;
    const chip = document.createElement('span');
    chip.className = 'lm-ov-chip';
    main.append(chevron, icon, name, chip);
    head.appendChild(main);

    const ids = room.lights.map(l => l.entity_id);
    const offBtn = this._iconButton('mdi:lightbulb-off-outline', `Turn off all lights in ${room.name}`, () =>
      this._turnOff(ids)
    );
    const onBtn = this._iconButton('mdi:lightbulb-on-outline', `Turn on all lights in ${room.name}`, () =>
      this._turnOn(ids)
    );
    head.append(offBtn, onBtn);
    section.appendChild(head);

    const grid = document.createElement('div');
    grid.className = 'lm-ov-grid';
    grid.style.gridTemplateColumns = `repeat(${this.config.columns},minmax(0,1fr))`;
    for (const light of room.lights) {
      const cell = document.createElement('div');
      cell.className = 'lm-ov-cell';
      if (light.width === 'full') cell.style.gridColumn = '1 / -1';
      const tile = this._makeTile(light.entity_id, light.slider);
      this._roomTiles.push(tile);
      cell.appendChild(tile);
      if (this.config.show_schedules && light.schedulable) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'lm-ov-sched' + (light.schedules > 0 ? ' has' : '');
        btn.setAttribute('aria-label', `Schedules for ${this._lightName(light)}`);
        btn.title =
          light.schedules > 0
            ? `${light.schedules} schedule${light.schedules === 1 ? '' : 's'} - tap to view or edit`
            : 'No schedules - tap to add one';
        btn.innerHTML =
          '<ha-icon icon="mdi:clock-outline"></ha-icon>' + (light.schedules > 0 ? `<span>${light.schedules}</span>` : '');
        btn.addEventListener('click', ev => {
          ev.stopPropagation();
          this._openSchedules(light);
        });
        cell.appendChild(btn);
      }
      grid.appendChild(cell);
    }
    section.appendChild(grid);

    const applyCollapsed = () => {
      const folded = this._collapsed.has(collapseKey);
      grid.hidden = folded;
      chevron.setAttribute('icon', folded ? 'mdi:chevron-right' : 'mdi:chevron-down');
      main.setAttribute('aria-expanded', String(!folded));
    };
    main.addEventListener('click', () => {
      if (this._collapsed.has(collapseKey)) this._collapsed.delete(collapseKey);
      else this._collapsed.add(collapseKey);
      this._saveCollapsed();
      applyCollapsed();
    });
    applyCollapsed();

    this._roomRefs.push({ ids, chip, offBtn, onBtn });
    return section;
  }

  // Cheap live refresh on every state change: counts, button states and the
  // "On now" tiles. Nothing here rebuilds a room's tiles.
  _updateLive() {
    if (!this._ready || !this._hass) return;
    const onIds = this._lights.filter(l => this._isOn(l.entity_id)).map(l => l.entity_id);

    this._nowChip.textContent = String(onIds.length);
    this._nowChip.classList.toggle('on', onIds.length > 0);
    this._allOffBtn.disabled = onIds.length === 0;

    for (const ref of this._roomRefs) {
      const on = ref.ids.filter(id => this._isOn(id)).length;
      ref.chip.textContent = on > 0 ? `${on} on` : `${ref.ids.length}`;
      ref.chip.classList.toggle('on', on > 0);
      ref.offBtn.disabled = on === 0;
      ref.onBtn.disabled = on === ref.ids.length;
    }

    const nowKey = onIds.slice().sort().join(',');
    if (nowKey === this._nowKey) return;
    this._nowKey = nowKey;

    const byId = new Map(this._lights.map(l => [l.entity_id, l]));
    const items = onIds
      .map(id => byId.get(id))
      .sort(
        (a, b) =>
          this._areaName(a.area_id).localeCompare(this._areaName(b.area_id)) ||
          this._lightName(a).localeCompare(this._lightName(b))
      );
    this._nowGrid.innerHTML = '';
    this._nowTiles = [];
    for (const light of items) {
      const cell = document.createElement('div');
      cell.className = 'lm-ov-cell';
      const caption = document.createElement('div');
      caption.className = 'lm-ov-area';
      caption.textContent = this._areaName(light.area_id);
      cell.appendChild(caption);
      const tile = this._makeTile(light.entity_id, false);
      this._nowTiles.push(tile);
      cell.appendChild(tile);
      this._nowGrid.appendChild(cell);
    }
    this._nowEmpty.hidden = items.length > 0;
  }

  // ---- actions -------------------------------------------------------------

  async _callDomains(service, ids) {
    if (!this._hass || !ids.length) return;
    const byDomain = {};
    for (const id of ids) {
      const domain = id.split('.')[0];
      (byDomain[domain] = byDomain[domain] || []).push(id);
    }
    try {
      await Promise.all(
        Object.entries(byDomain).map(([domain, entityIds]) =>
          this._hass.callService(domain, service, { entity_id: entityIds })
        )
      );
    } catch (err) {
      console.error('Lighting Manager overview:', err);
    }
  }

  _turnOff(ids) {
    return this._callDomains('turn_off', ids.filter(id => this._isOn(id)));
  }

  _turnOn(ids) {
    return this._callDomains(
      'turn_on',
      ids.filter(id => {
        const state = this._hass?.states?.[id]?.state;
        return state !== 'on' && state !== 'unavailable';
      })
    );
  }

  async _openSchedules(light) {
    try {
      await Promise.race([
        customElements.whenDefined('light-scheduler-list-card'),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000))
      ]);
      if (!this._helper) this._helper = document.createElement('light-scheduler-list-card');
      await this._helper.openSchedulerDialog(
        this._hass,
        `${this._lightName(light)} schedules`,
        [light.entity_id]
      );
    } catch (err) {
      console.error('Lighting Manager overview: could not open the schedule dialog', err);
    }
  }
}

if (!customElements.get('lighting-manager-overview-card')) {
  customElements.define('lighting-manager-overview-card', LightingManagerOverviewCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some(x => x.type === 'lighting-manager-overview-card')) {
  window.customCards.push({
    type: 'lighting-manager-overview-card',
    name: 'Lighting Manager Overview Card',
    description:
      'A whole lighting page: lights on now with Turn all off, then every room with its lights, countdown tiles and schedules.'
  });
}

console.info(
  '%c LIGHTING-MANAGER-OVERVIEW-CARD %c v1.0.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
