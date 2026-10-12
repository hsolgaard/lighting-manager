class LightSchedulerListCard extends HTMLElement {
  constructor() {
    super();
    this._collapsed = true;
    this._resolvedLights = [];
    this._loaded = false;
    this._error = null;
    this._reorderMode = false;
    this._fetchToken = 0;
    this._fetchedArea = null;
    this._refreshTimer = null;
    this._legacyLights = [];
  }

  // Two ways to configure this card (v5.1.0):
  //   area:   "living_room"   - Lighting Manager resolves the adopted lights
  //                             in that Area (ordered, live). Preferred.
  //   lights: [ {entity, name, icon} ... ]
  //                           - the original v4.x hand-written list. Still
  //                             works exactly as before, with no dependency
  //                             on Lighting Manager, so existing dashboards
  //                             keep working until you migrate them.
  // If both are given, area: wins.
  _isAreaMode() {
    return !!(this.config && typeof this.config.area === 'string' && this.config.area);
  }

  setConfig(config) {
    const hasArea = !!(config && typeof config.area === 'string' && config.area);
    const hasLights = !!(config && Array.isArray(config.lights));

    if (!hasArea && !hasLights) {
      throw new Error(
        "Either area: (recommended - the Home Assistant Area id, Lighting Manager " +
        "resolves the lights) or lights: (legacy hand-written list) is required."
      );
    }

    this.config = config;

    // Reset area-mode state if the card is reconfigured (e.g. in the editor).
    this._fetchedArea = null;
    this._resolvedLights = [];
    this._error = null;
    this._reorderMode = false;

    if (hasArea) {
      this._loaded = false;
      this._legacyLights = [];
    } else {
      this._loaded = true;
      this._legacyLights = this._normaliseLegacyLights(config.lights);
    }

    if (this._hass && hasArea) {
      this._fetchAreaLights();
    }

    this._render();
  }

  _normaliseLegacyLights(list) {
    // Accept {entity, name, icon} objects (v4.x format) and bare entity
    // id strings. Alphabetical by display name, as v4.x did.
    const items = (list || [])
      .map(x => (typeof x === 'string' ? { entity: x } : x))
      .filter(x => x && typeof x.entity === 'string' && x.entity);

    const label = x =>
      String(x.name || this._hass?.states?.[x.entity]?.attributes?.friendly_name || x.entity)
        .toLowerCase();

    return items.sort((a, b) => label(a).localeCompare(label(b)));
  }

  _lights() {
    return this._isAreaMode() ? this._resolvedLights : this._legacyLights;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;

    if (this._isAreaMode()) {
      if (this._fetchedArea !== this.config.area) {
        this._fetchAreaLights();
      }
    } else if (first && this.config) {
      // Friendly names weren't available when setConfig sorted - re-sort now.
      this._legacyLights = this._normaliseLegacyLights(this.config.lights);
    }

    this._render();
  }

  connectedCallback() {
    if (this._refreshTimer) return;
    if (this.config && !this._isAreaMode()) return; // legacy mode: nothing to poll

    // Adoption/order changes rarely, but this keeps the card in sync
    // with Lighting Manager without needing a manual dashboard reload -
    // matches the "eliminate configuration, don't just automate its
    // maintenance" goal without re-fetching from the hass setter, which
    // fires on every state tick and would hammer the websocket.
    this._refreshTimer = setInterval(() => {
      if (this._hass && this._isAreaMode()) {
        this._fetchAreaLights();
      }
    }, 60000);
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  async _fetchAreaLights() {
    if (!this._hass || !this._isAreaMode()) return;

    const token = ++this._fetchToken;
    const areaBeingFetched = this.config.area;

    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: 'lighting_manager/list_area_lights',
        area_id: areaBeingFetched
      });

      if (token !== this._fetchToken) return; // superseded by a newer fetch

      this._resolvedLights = (result.lights || []).map(l => ({
        entity: l.entity_id,
        name: l.name
      }));
      this._loaded = true;
      this._fetchedArea = areaBeingFetched;
      this._error = null;
    } catch (err) {
      if (token !== this._fetchToken) return;

      this._loaded = true;
      this._error =
        err?.message ||
        String(err) ||
        'Could not reach Lighting Manager - is the integration installed and running?';
    }

    this._render();
  }

  _moveLight(idx, delta) {
    if (!this._isAreaMode()) return;

    const target = idx + delta;
    if (target < 0 || target >= this._resolvedLights.length) return;

    const next = [...this._resolvedLights];
    [next[idx], next[target]] = [next[target], next[idx]];
    this._resolvedLights = next;
    this._render();
    this._persistOrder();
  }

  async _persistOrder() {
    if (!this._hass || !this._isAreaMode()) return;

    try {
      await this._hass.connection.sendMessagePromise({
        type: 'lighting_manager/set_area_light_order',
        area_id: this.config.area,
        entity_ids: this._resolvedLights.map(x => x.entity)
      });
      this._error = null;
    } catch (err) {
      this._error = `Order not saved: ${err?.message || err}`;
      this._render();
    }
  }

  getCardSize() {
    return this._collapsed
      ? 1
      : Math.max(1, (this._lights().length || 1) * 2);
  }

  _escape(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[c]));
  }

  _addTarget(out, value) {
    if (!value) return;

    if (Array.isArray(value)) {
      value.forEach(v => this._addTarget(out, v));
    } else if (typeof value === 'string') {
      out.push(value);
    }
  }

  _targets(state) {
    const a = state?.attributes || {};
    const out = [];

    this._addTarget(out, a.entities);
    this._addTarget(out, a.entity_id);

    if (Array.isArray(a.actions)) {
      for (const action of a.actions) {
        this._addTarget(out, action?.entity_id);
        this._addTarget(out, action?.target?.entity_id);
        this._addTarget(out, action?.service_data?.entity_id);
        this._addTarget(out, action?.data?.entity_id);
      }
    }

    if (Array.isArray(a.timeslots)) {
      for (const slot of a.timeslots) {
        if (!Array.isArray(slot?.actions)) continue;

        for (const action of slot.actions) {
          this._addTarget(out, action?.entity_id);
          this._addTarget(out, action?.target?.entity_id);
          this._addTarget(out, action?.service_data?.entity_id);
          this._addTarget(out, action?.data?.entity_id);
        }
      }
    }

    return [...new Set(out)];
  }

  _schedulesFor(lightId) {
    if (!this._hass) return [];

    return Object.values(this._hass.states)
      .filter(s => s.entity_id.startsWith('switch.schedule_'))
      .filter(s => this._targets(s).includes(lightId))
      .sort((a, b) =>
        this._sortKey(a).localeCompare(this._sortKey(b))
      );
  }

  _sortKey(schedule) {
    const a = schedule.attributes || {};
    const slots = Array.isArray(a.timeslots) ? a.timeslots : [];
    const first = slots[0] || a.timeslots || null;

    return `${this._rawStart(first)}-${schedule.entity_id}`;
  }

  _rawStart(slot) {
    if (!slot) return '99:99';

    if (typeof slot === 'object') {
      return String(
        slot.start ||
        slot.start_time ||
        slot.time ||
        slot.timestamp ||
        '99:99'
      );
    }

    return String(slot);
  }

  _cleanTime(value) {
    let s = String(value ?? '').trim();

    if (!s) return '—';

    if (/^\d{1,2}:\d{2}:\d{2}$/.test(s)) {
      s = s.slice(0, 5);
    }

    s = s
      .replace(/sunset/gi, '🌙')
      .replace(/sunrise/gi, '☀️')
      .replace(/\+00:00/g, '')
      .replace(/-00:00/g, '');

    return s;
  }

  _timeText(slot) {
    if (!slot) return 'Scheduled';

    if (typeof slot === 'object') {
      const start =
        slot.start ||
        slot.start_time ||
        slot.time ||
        slot.timestamp;

      const stop =
        slot.stop ||
        slot.end ||
        slot.stop_time ||
        slot.end_time;

      if (start && stop) {
        return `${this._cleanTime(start)} → ${this._cleanTime(stop)}`;
      }

      if (start) {
        return this._cleanTime(start);
      }

      return 'Scheduled';
    }

    return this._cleanTime(slot);
  }

  _servicesFromActions(actions) {
    if (!Array.isArray(actions)) return [];

    return actions
      .map(x =>
        String(x?.service || x?.action || '')
          .split('.')
          .pop()
      )
      .filter(Boolean);
  }

  _actionText(schedule, slot = null) {
    const a = schedule.attributes || {};
    let services = [];

    if (slot?.actions) {
      services = this._servicesFromActions(slot.actions);
    }

    if (!services.length) {
      services = this._servicesFromActions(a.actions);
    }

    const unique = [...new Set(services)];

    if (unique.length === 1) {
      if (unique[0] === 'turn_on') return 'ON';
      if (unique[0] === 'turn_off') return 'OFF';

      return unique[0]
        .replaceAll('_', ' ')
        .toUpperCase();
    }

    if (unique.length > 1) return '';

    const name = String(
      a.friendly_name || ''
    ).toLowerCase();

    if (
      name.includes('turn on') ||
      name.endsWith(' on')
    ) {
      return 'ON';
    }

    if (
      name.includes('turn off') ||
      name.endsWith(' off')
    ) {
      return 'OFF';
    }

    return '';
  }

  _days(schedule) {
    const w = schedule.attributes?.weekdays;

    if (
      !w ||
      !Array.isArray(w) ||
      !w.length ||
      w.includes('daily')
    ) {
      return [1, 1, 1, 1, 1, 1, 1];
    }

    if (
      w.includes('workday') ||
      w.includes('workdays')
    ) {
      return [1, 1, 1, 1, 1, 0, 0];
    }

    if (w.includes('weekend')) {
      return [0, 0, 0, 0, 0, 1, 1];
    }

    const aliases = {
      mon: 0,
      monday: 0,
      '1': 0,

      tue: 1,
      tues: 1,
      tuesday: 1,
      '2': 1,

      wed: 2,
      wednesday: 2,
      '3': 2,

      thu: 3,
      thur: 3,
      thurs: 3,
      thursday: 3,
      '4': 3,

      fri: 4,
      friday: 4,
      '5': 4,

      sat: 5,
      saturday: 5,
      '6': 5,

      sun: 6,
      sunday: 6,
      '7': 6,
      '0': 6
    };

    const out = [0, 0, 0, 0, 0, 0, 0];

    for (const day of w) {
      const idx =
        aliases[String(day).toLowerCase()];

      if (idx !== undefined) {
        out[idx] = 1;
      }
    }

    return out;
  }

  _daysHtml(schedule) {
    const flags = this._days(schedule);
    const letters = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

    return letters
      .map(
        (letter, i) =>
          `<span class="day ${
            flags[i] ? 'active' : 'inactive'
          }">${letter}</span>`
      )
      .join('');
  }

  _scheduleLines(schedule) {
    const a = schedule.attributes || {};

    const slots =
      Array.isArray(a.timeslots) &&
      a.timeslots.length
        ? a.timeslots
        : [null];

    return slots.map(slot => ({
      time: this._timeText(slot),
      action: this._actionText(schedule, slot)
    }));
  }

  _roomLights() {
    // Already resolved and ordered server-side (adopted-only, manual
    // override then alphabetical fallback) by
    // coordinator.async_list_area_lights - no client-side sort needed.
    return this._lights().map(x => x.entity);
  }

  _onCount() {
    if (!this._hass) return 0;

    return this._roomLights()
      .filter(
        id =>
          this._hass.states?.[id]?.state === 'on'
      )
      .length;
  }

  _scheduledLightCount() {
    if (!this._hass) return 0;

    return this._roomLights()
      .filter(
        id =>
          this._schedulesFor(id).length > 0
      )
      .length;
  }

  _openScheduler(title, include = null) {
    if (!this._hass) return;

    const entities =
      include ||
      this._roomLights();

    this.dispatchEvent(
      new CustomEvent('ll-custom', {
        bubbles: true,
        composed: true,
        detail: {
          browser_mod: {
            service: 'browser_mod.popup',

            data: {
              title:
                title ||
                `${this.config.title || 'Lights'} schedules`,

              size: 'wide',

              content: {
                type: 'custom:scheduler-card',

                title: false,

                include: entities,

                discover_existing: false,

                show_header_toggle: false,

                show_toggle_switches: true,

                show_add_button: true,

                display_options: {
                  primary_info: [
                    '{entity}: {action}'
                  ],

                  secondary_info: [
                    'days',
                    'time'
                  ]
                },

                sort_by: [
                  'relative-time',
                  'state'
                ]
              }
            }
          }
        }
      })
    );
  }

  _render() {
    if (!this.config) return;

    const hass = this._hass;
    const areaMode = this._isAreaMode();
    // Reordering only exists in area mode (the order is stored by Lighting Manager).
    const canReorder = areaMode && !!this._hass?.user?.is_admin;
    const area = areaMode ? this._hass?.areas?.[this.config.area] : null;

    const roomTitle = this._escape(
      this.config.title ||
      area?.name ||
      'Lights'
    );

    const roomIcon = this._escape(
      this.config.icon ||
      area?.icon ||
      'mdi:lightbulb-group-outline'
    );

    const onCount =
      this._onCount();

    const scheduledCount =
      this._scheduledLightCount();

    const lights =
      this._lights();

    const rows =
      lights.map((light, idx) => {
        const st =
          hass?.states?.[light.entity];

        const state =
          st?.state ||
          'unknown';

        const on =
          state === 'on';

        const bad =
          state === 'unavailable' ||
          state === 'unknown';

        const icon =
          light.icon ||
          st?.attributes?.icon ||
          'mdi:lightbulb-outline';

        const name =
          light.name ||
          st?.attributes?.friendly_name ||
          light.entity;

        const schedules =
          this._schedulesFor(light.entity);

        let scheduleHtml = '';

        if (!schedules.length) {
          scheduleHtml = `
            <div class="schedule empty">
              <div class="empty-text">
                No schedule
              </div>
            </div>
          `;
        } else {
          for (const schedule of schedules) {
            const targets =
              this._targets(schedule)
                .filter(
                  id =>
                    id.startsWith('light.')
                );

            const shared =
              Math.max(
                1,
                targets.length
              );

            const enabled =
              schedule.state === 'on' ||
              schedule.state === 'triggered';

            const lines =
              this._scheduleLines(schedule);

            const scheduleName =
              schedule.attributes?.friendly_name ||
              schedule.entity_id;

            lines.forEach((line, lineIdx) => {
              scheduleHtml += `
                <div
                  class="schedule ${
                    lineIdx
                      ? 'continued'
                      : ''
                  }"

                  data-schedule="${
                    this._escape(
                      schedule.entity_id
                    )
                  }"

                  data-light-entity="${
                    this._escape(
                      light.entity
                    )
                  }"

                  data-light-name="${
                    this._escape(
                      name
                    )
                  }"

                  title="${
                    this._escape(
                      scheduleName
                    )
                  } — tap to edit"
                >

                  <div class="days">
                    ${
                      lineIdx === 0
                        ? this._daysHtml(
                            schedule
                          )
                        : ''
                    }
                  </div>

                  <div class="when">
                    ${
                      this._escape(
                        line.time
                      )
                    }

                    ${
                      line.action
                        ? `
                          <span
                            class="action ${
                              line.action === 'ON'
                                ? 'onact'
                                : line.action === 'OFF'
                                ? 'offact'
                                : ''
                            }"
                          >
                            ${
                              this._escape(
                                line.action
                              )
                            }
                          </span>
                        `
                        : ''
                    }
                  </div>

                  <div class="shared">
                    ${
                      lineIdx === 0 &&
                      shared > 1
                        ? `
                          <span
                            title="Shared by ${
                              shared
                            } lights"
                          >
                            👥${shared}
                          </span>
                        `
                        : ''
                    }
                  </div>

                  ${
                    lineIdx === 0
                      ? `
                        <button
                          class="sched-switch ${
                            enabled
                              ? 'enabled'
                              : ''
                          }"

                          data-toggle="${
                            this._escape(
                              schedule.entity_id
                            )
                          }"

                          aria-label="${
                            enabled
                              ? 'Disable'
                              : 'Enable'
                          } schedule"

                          title="${
                            enabled
                              ? 'Disable'
                              : 'Enable'
                          } schedule"
                        >
                          <span
                            class="switch-thumb"
                          ></span>
                        </button>
                      `
                      : '<div></div>'
                  }

                </div>
              `;
            });
          }
        }

        return `
          <div class="light-block">

            <div class="light-row">

              <button
                class="light-main"

                data-light-toggle="${
                  this._escape(
                    light.entity
                  )
                }"

                title="Toggle ${
                  this._escape(
                    name
                  )
                }"
              >

                <ha-icon
                  class="light-icon ${
                    on
                      ? 'on'
                      : bad
                      ? 'bad'
                      : 'off'
                  }"

                  icon="${
                    this._escape(
                      icon
                    )
                  }"
                ></ha-icon>

                <span class="light-name">
                  ${
                    this._escape(
                      name
                    )
                  }
                </span>

              </button>

              ${
                canReorder && this._reorderMode
                  ? `
                    <div class="reorder-btns">
                      <button
                        data-reorder-up="${idx}"
                        ${idx === 0 ? 'disabled' : ''}
                        aria-label="Move ${this._escape(name)} up"
                        title="Move up"
                      >
                        <ha-icon icon="mdi:chevron-up"></ha-icon>
                      </button>
                      <button
                        data-reorder-down="${idx}"
                        ${idx === lights.length - 1 ? 'disabled' : ''}
                        aria-label="Move ${this._escape(name)} down"
                        title="Move down"
                      >
                        <ha-icon icon="mdi:chevron-down"></ha-icon>
                      </button>
                    </div>
                  `
                  : ''
              }

              <button
                class="edit-light"

                data-edit-light="${
                  this._escape(
                    light.entity
                  )
                }"

                data-edit-name="${
                  this._escape(
                    name
                  )
                }"

                aria-label="Manage schedules for ${
                  this._escape(
                    name
                  )
                }"

                title="Manage schedules"
              >

                <ha-icon
                  icon="mdi:calendar-clock-outline"
                ></ha-icon>

              </button>

            </div>

            <div class="schedule-wrap">
              ${scheduleHtml}
            </div>

          </div>
        `;
      })
      .join('');

    const bodyContent = this._error
      ? `<div class="state-message error">⚠️ ${this._escape(this._error)}</div>`
      : !this._loaded
      ? `<div class="state-message">Loading…</div>`
      : !lights.length
      ? `<div class="state-message">${
          areaMode
            ? 'No lights adopted by Lighting Manager in this Area yet.'
            : 'No lights configured.'
        }</div>`
      : rows;

    this.innerHTML = `
      <ha-card>

        <style>

          ha-card {
            overflow: hidden;
            padding: 0;
            margin: 0;
            border-radius: 14px;
          }

          button {
            font: inherit;
            -webkit-tap-highlight-color: transparent;
          }

          .room-head {
            min-height: 44px;

            display: flex;
            align-items: center;

            padding:
              0 8px
              0 10px;

            border-bottom:
              ${
                this._collapsed
                  ? '0'
                  : '1px solid var(--divider-color)'
              };

            gap: 7px;
          }

          .room-toggle {
            flex: 1;
            min-width: 0;
            min-height: 44px;

            display: flex;
            align-items: center;

            gap: 8px;

            border: 0;
            background: transparent;

            color:
              var(--primary-text-color);

            padding: 0;

            cursor: pointer;

            text-align: left;
          }

          .chevron {
            --mdc-icon-size: 20px;

            color:
              var(--secondary-text-color);

            flex:
              0 0 20px;
          }

          .room-icon {
            --mdc-icon-size: 21px;

            color:
              var(--secondary-text-color);

            flex:
              0 0 22px;
          }

          .room-title {
            font-size: 16px;
            font-weight: 650;

            min-width: 0;

            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .room-summary {
            margin-left: auto;

            display: flex;
            align-items: center;

            gap: 9px;

            white-space: nowrap;

            font-size: 12px;
            font-weight: 650;
          }

          .summary-on {
            color:
              var(
                --amber-color,
                #ffc107
              );
          }

          .summary-scheduled {
            color:
              var(
                --primary-color
              );
          }

          .room-add,
          .edit-light,
          .sched-switch,
          .light-main {
            border: 0;
            background: transparent;
            cursor: pointer;
          }

          .room-add {
            width: 38px;
            height: 38px;

            display: flex;
            align-items: center;
            justify-content: center;

            border-radius: 19px;

            color:
              var(--primary-color);

            padding: 0;

            flex:
              0 0 38px;
          }

          .room-add ha-icon {
            color:
              var(--primary-color);

            --mdc-icon-size: 23px;
          }

          .room-add.reorder-toggle.active {
            background:
              var(--secondary-background-color);
          }

          .room-body {
            display: block;
          }

          .state-message {
            padding: 14px 16px;

            color:
              var(--secondary-text-color);

            font-size: 13px;
          }

          .state-message.error {
            color:
              var(--error-color);
          }

          .light-block {
            border-bottom:
              1px solid
              var(--divider-color);
          }

          .light-block:last-child {
            border-bottom: 0;
          }

          .light-row {
            height: 48px;

            display: flex;
            align-items: center;

            padding:
              0 8px;
          }

          .light-main {
            height: 44px;

            display: flex;
            align-items: center;

            gap: 9px;

            flex: 1;
            min-width: 0;

            text-align: left;

            color:
              var(--primary-text-color);

            padding:
              0 3px;

            border-radius: 8px;
          }

          .light-icon {
            --mdc-icon-size: 27px;

            width: 34px;

            flex:
              0 0 34px;

            text-align: center;
          }

          .light-icon.on {
            color:
              var(
                --amber-color,
                #ffc107
              );
          }

          .light-icon.off {
            color:
              var(
                --disabled-text-color
              );
          }

          .light-icon.bad {
            color:
              var(
                --error-color
              );
          }

          .light-name {
            font-size: 16px;
            font-weight: 650;

            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .reorder-btns {
            display: flex;
            flex-direction: column;

            flex:
              0 0 28px;
          }

          .reorder-btns button {
            width: 28px;
            height: 21px;

            display: flex;
            align-items: center;
            justify-content: center;

            border: 0;
            background: transparent;
            cursor: pointer;

            color:
              var(--secondary-text-color);

            padding: 0;
          }

          .reorder-btns button[disabled] {
            opacity: .25;
            cursor: default;
          }

          .reorder-btns ha-icon {
            --mdc-icon-size: 18px;
          }

          .edit-light {
            width: 38px;
            height: 38px;

            padding: 0;

            color:
              var(
                --secondary-text-color
              );

            display: flex;
            align-items: center;
            justify-content: center;

            border-radius: 19px;
          }

          .edit-light ha-icon {
            --mdc-icon-size: 21px;
          }

          .schedule-wrap {
            padding:
              0 8px
              6px 53px;
          }

          .schedule {
            min-height: 31px;

            display: grid;

            grid-template-columns:
              108px
              minmax(92px, 1fr)
              40px
              42px;

            align-items: center;

            column-gap: 3px;

            font-size: 13px;

            cursor: pointer;

            border-radius: 6px;
          }

          .schedule.continued {
            min-height: 26px;
          }

          .schedule.empty {
            display: block;

            min-height: 25px;

            cursor: default;
          }

          .empty-text {
            line-height: 25px;

            color:
              var(
                --secondary-text-color
              );

            font-size: 13px;
          }

          .days {
            display: flex;

            gap: 3px;

            align-items: center;

            white-space: nowrap;
          }

          .day {
            width: 11px;

            text-align: center;

            font-size: 11px;
            font-weight: 700;

            line-height: 18px;
          }

          .day.active {
            color:
              var(
                --primary-text-color
              );

            opacity: 1;
          }

          .day.inactive {
            color:
              var(
                --disabled-text-color
              );

            opacity: .28;

            font-weight: 500;
          }

          .when {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;

            color:
              var(
                --secondary-text-color
              );
          }

          .action {
            font-size: 10px;
            font-weight: 750;

            margin-left: 4px;

            border-radius: 5px;

            padding:
              1px 4px;

            background:
              var(
                --secondary-background-color
              );

            color:
              var(
                --secondary-text-color
              );
          }

          .action.onact {
            color:
              var(
                --amber-color,
                #ffc107
              );
          }

          .action.offact {
            color:
              var(
                --secondary-text-color
              );
          }

          .shared {
            text-align: right;

            color:
              var(
                --secondary-text-color
              );

            font-size: 11px;

            white-space: nowrap;
          }

          .sched-switch {
            position: relative;

            width: 38px;
            height: 22px;

            border-radius: 11px;

            padding: 0;

            background:
              var(
                --disabled-color,
                #9e9e9e
              );

            opacity: .55;

            transition:
              background 140ms ease,
              opacity 140ms ease;
          }

          .sched-switch .switch-thumb {
            position: absolute;

            top: 3px;
            left: 3px;

            width: 16px;
            height: 16px;

            border-radius: 50%;

            background:
              var(
                --card-background-color,
                white
              );

            box-shadow:
              0 1px 2px
              rgba(0,0,0,.28);

            transition:
              left 140ms ease;
          }

          .sched-switch.enabled {
            background:
              var(--primary-color);

            opacity: 1;
          }

          .sched-switch.enabled
          .switch-thumb {
            left: 19px;
          }

          @media
            (hover: hover)
            and
            (pointer: fine) {

            .schedule:hover,
            .light-main:hover,
            .edit-light:hover,
            .room-add:hover,
            .room-toggle:hover {

              background:
                var(
                  --secondary-background-color
                );
            }
          }

          @media
            (max-width: 380px) {

            .schedule {
              grid-template-columns:
                98px
                minmax(78px, 1fr)
                34px
                40px;
            }

            .schedule-wrap {
              padding-left: 49px;
            }

            .day {
              width: 10px;
              font-size: 10px;
            }
          }

        </style>

        <div class="room-head">

          <button
            class="room-toggle"

            aria-expanded="${
              this._collapsed
                ? 'false'
                : 'true'
            }"

            title="${
              this._collapsed
                ? 'Expand'
                : 'Collapse'
            } ${roomTitle}"
          >

            <ha-icon
              class="chevron"

              icon="${
                this._collapsed
                  ? 'mdi:chevron-right'
                  : 'mdi:chevron-down'
              }"
            ></ha-icon>

            <ha-icon
              class="room-icon"
              icon="${roomIcon}"
            ></ha-icon>

            <span class="room-title">
              ${roomTitle}
            </span>

            <span class="room-summary">

              ${
                onCount > 0
                  ? `
                    <span class="summary-on">
                      💡 ${onCount} on
                    </span>
                  `
                  : ''
              }

              ${
                scheduledCount > 0
                  ? `
                    <span class="summary-scheduled">
                      📅 ${scheduledCount}
                    </span>
                  `
                  : ''
              }

            </span>

          </button>

          ${
            canReorder
              ? `
                <button
                  class="room-add reorder-toggle ${
                    this._reorderMode ? 'active' : ''
                  }"

                  title="${
                    this._reorderMode
                      ? 'Done reordering'
                      : 'Reorder lights'
                  }"

                  aria-label="${
                    this._reorderMode
                      ? 'Done reordering'
                      : 'Reorder lights'
                  }"
                >
                  <ha-icon
                    icon="${
                      this._reorderMode
                        ? 'mdi:check'
                        : 'mdi:sort'
                    }"
                  ></ha-icon>
                </button>
              `
              : ''
          }

          <button
            class="room-add"

            title="Add or edit room schedules"

            aria-label="Add or edit room schedules"
          >

            <ha-icon
              icon="mdi:calendar-plus"
            ></ha-icon>

          </button>

        </div>

        ${
          this._collapsed
            ? ''
            : `
              <div class="room-body">
                ${bodyContent}
              </div>
            `
        }

      </ha-card>
    `;

    this
      .querySelector(
        '.room-toggle'
      )
      ?.addEventListener(
        'click',
        e => {
          e.stopPropagation();

          this._collapsed =
            !this._collapsed;

          this._render();
        }
      );

    this
      .querySelector(
        '.reorder-toggle'
      )
      ?.addEventListener(
        'click',
        e => {
          e.stopPropagation();

          this._reorderMode =
            !this._reorderMode;

          if (this._reorderMode) {
            this._collapsed = false;
          }

          this._render();
        }
      );

    this
      .querySelectorAll(
        '[data-reorder-up]'
      )
      .forEach(el => {
        el.addEventListener(
          'click',
          e => {
            e.stopPropagation();

            this._moveLight(
              Number(
                el.getAttribute(
                  'data-reorder-up'
                )
              ),
              -1
            );
          }
        );
      });

    this
      .querySelectorAll(
        '[data-reorder-down]'
      )
      .forEach(el => {
        el.addEventListener(
          'click',
          e => {
            e.stopPropagation();

            this._moveLight(
              Number(
                el.getAttribute(
                  'data-reorder-down'
                )
              ),
              1
            );
          }
        );
      });

    this
      .querySelector(
        '.room-add:not(.reorder-toggle)'
      )
      ?.addEventListener(
        'click',
        e => {
          e.stopPropagation();

          this._openScheduler(
            `${
              this.config.title ||
              'Room'
            } schedules`
          );
        }
      );

    this
      .querySelectorAll(
        '[data-light-toggle]'
      )
      .forEach(el => {
        el.addEventListener(
          'click',
          e => {
            e.stopPropagation();

            const id =
              el.getAttribute(
                'data-light-toggle'
              );

            this._hass?.callService(
              'light',
              'toggle',
              {
                entity_id: id
              }
            );
          }
        );
      });

    this
      .querySelectorAll(
        '[data-edit-light]'
      )
      .forEach(el => {
        el.addEventListener(
          'click',
          e => {
            e.stopPropagation();

            const entity =
              el.getAttribute(
                'data-edit-light'
              );

            const name =
              el.getAttribute(
                'data-edit-name'
              ) ||
              'Light';

            this._openScheduler(
              `${name} schedules`,
              [entity]
            );
          }
        );
      });

    this
      .querySelectorAll(
        '[data-toggle]'
      )
      .forEach(el => {
        el.addEventListener(
          'click',
          e => {
            e.stopPropagation();

            const id =
              el.getAttribute(
                'data-toggle'
              );

            this._hass?.callService(
              'switch',
              'toggle',
              {
                entity_id: id
              }
            );
          }
        );
      });

    this
      .querySelectorAll(
        '[data-schedule]'
      )
      .forEach(el => {
        el.addEventListener(
          'click',
          e => {
            if (
              e.target.closest(
                '[data-toggle]'
              )
            ) {
              return;
            }

            e.stopPropagation();

            const entity =
              el.getAttribute(
                'data-light-entity'
              );

            const name =
              el.getAttribute(
                'data-light-name'
              ) ||
              'Light';

            if (!entity) return;

            this._openScheduler(
              `${name} — edit schedule`,
              [entity]
            );
          }
        );
      });
  }
}

if (
  !customElements.get(
    'light-scheduler-list-card'
  )
) {
  customElements.define(
    'light-scheduler-list-card',
    LightSchedulerListCard
  );
}

window.customCards =
  window.customCards || [];

if (
  !window.customCards.some(
    x =>
      x.type ===
      'light-scheduler-list-card'
  )
) {
  window.customCards.push({
    type:
      'light-scheduler-list-card',

    name:
      'Light Scheduler List Card',

    description:
      'Compact light-centric view of Scheduler component schedules. Use area: for Lighting Manager-resolved lights, or lights: for a hand-written list.'
  });
}

console.info(
  '%c LIGHT-SCHEDULER-LIST-CARD %c v5.1.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
