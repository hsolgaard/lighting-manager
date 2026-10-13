/**
 * Lighting Manager cards - single bundle (GENERATED, do not edit).
 * Source: the individual *-card.js files in this folder;
 * rebuild with: python3 scripts/build_bundle.py
 * Loading it more than once (as a Lovelace resource AND by the
 * integration) is harmless: every card only defines itself if its
 * element name is not already defined. Each card loads in its own
 * protected section, so one failure cannot stop the others, and the
 * last line reports which cards ended up defined.
 */

/* ===== lighting-manager-countdown-card.js ===== */
try {
{
/**
 * Lighting Manager countdown card  v2.1.1
 * (PRD Revision - Lighting-Aware Dashboard Automation, section 4.3.)
 *
 * One light per card. It is Home Assistant's own tile card, so it looks
 * and behaves exactly like every other tile on the dashboard (icon,
 * name, state, Unavailable/Unknown, theming), with one addition:
 *
 *   - single tap   toggles the light (the native tile behaviour)
 *   - double tap   opens a countdown popup. No countdown running: presets
 *                  + custom minutes. One running: the live clock with
 *                  Change, Cancel countdown and OK.
 *   - while a countdown runs, a small timer badge shows the time left on
 *     the tile itself, so you can see it with the popup closed.
 *
 *   type: custom:lighting-manager-countdown-card
 *   entity: light.garage_lights
 *   name: Garage lights        # optional, any tile-card option works
 *
 * Every option other than `type` is passed straight to the native tile
 * card (name, icon, color, vertical, features, ...). Only the double
 * tap action is ours. The countdown engine itself is unchanged: this
 * card calls lighting_manager/start_countdown and cancel_countdown.
 *
 * The popup is a plain overlay on document.body (no dependency on
 * Home Assistant dialog internals) so it works unchanged on iPhone
 * Safari, and it does not resize neighbouring tiles.
 *
 * Plain JS, no Lit, no build step - same rationale as
 * lighting-manager-panel.js.
 *
 * Which lights have a countdown is read from Lighting Manager with one
 * small call (lighting_manager/list_countdowns) that every tile on the
 * page shares, refreshed every 30 seconds and straight after a start,
 * cancel or the light being switched off. The seconds are ticked locally.
 *
 * Presets are duplicated from the backend; keep in sync by hand.
 */
const COUNTDOWN_PRESETS_MIN = [15, 30, 60, 120];
const COUNTDOWN_REFRESH_MS = 30000;

// One shared answer to "which lights have a countdown running", used by
// every tile on the page, so ten tiles cost one call, not ten.
const lmCountdowns = {
  hass: null,
  map: new Map(), // entity_id -> expiry (ISO string)
  loaded: false,
  failed: false,
  listeners: new Set(),
  inflight: null,
  pending: false,
  timer: null,

  expiresAt(entityId) {
    return this.map.get(entityId) || null;
  },

  attach(card) {
    this.listeners.add(card);
    if (!this.timer) {
      this.timer = setInterval(() => this.refresh(), COUNTDOWN_REFRESH_MS);
    }
    if (!this.loaded) this.refresh();
  },

  detach(card) {
    this.listeners.delete(card);
    if (!this.listeners.size && this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  },

  refresh() {
    if (!this.hass) return Promise.resolve();
    if (this.inflight) {
      // A change happened while a request was in flight: ask again after it.
      this.pending = true;
      return this.inflight;
    }
    this.inflight = (async () => {
      try {
        const result = await this.hass.callWS({ type: 'lighting_manager/list_countdowns' });
        this.map = new Map(Object.entries(result?.countdowns || {}));
        this.loaded = true;
        this.failed = false;
      } catch (err) {
        this.failed = true;
      }
      this.inflight = null;
      for (const card of this.listeners) card._countdownChanged();
      if (this.pending) {
        this.pending = false;
        return this.refresh();
      }
      return undefined;
    })();
    return this.inflight;
  }
};

class LightingManagerCountdownCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this.config = null;
    this._inner = null;
    this._innerKey = null;
    this._buildToken = 0;
    this._error = null;

    // popup state
    this._overlay = null;
    this._mode = 'auto'; // 'auto' (clock if running, else setup) | 'change'
    this._renderedSig = null;
    this._customMinutes = '';
    this._badge = null;
    this._lastState = null;
    this._busy = false;
    this._tickTimer = null;
    this._onKey = e => {
      if (e.key === 'Escape') this._closePopup();
    };

    this._onCustom = e => this._handleCustomEvent(e);
    this.addEventListener('ll-custom', this._onCustom);
  }

  setConfig(config) {
    if (!config || typeof config.entity !== 'string' || !config.entity) {
      throw new Error('entity is required - the light (or promoted switch) this card controls.');
    }
    this.config = config;
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    lmCountdowns.hass = hass;
    if (this._inner) this._inner.hass = hass;

    if (!lmCountdowns.loaded && !lmCountdowns.failed && !lmCountdowns.inflight) {
      lmCountdowns.refresh();
    }

    // The light was switched off while a countdown was showing: Lighting
    // Manager cancels the countdown, so re-check rather than wait 30 s.
    const state = hass?.states?.[this.config?.entity]?.state ?? null;
    if (this._lastState !== null && state === 'off' && this._lastState !== 'off' && this._expiry()) {
      setTimeout(() => lmCountdowns.refresh(), 800);
    }
    this._lastState = state;
  }

  connectedCallback() {
    lmCountdowns.attach(this);
    this._updateBadge();
  }

  disconnectedCallback() {
    lmCountdowns.detach(this);
    this._closePopup();
    this._stopTick();
  }

  getCardSize() {
    return this._inner?.getCardSize ? this._inner.getCardSize() : 1;
  }

  getGridOptions() {
    return this._inner?.getGridOptions ? this._inner.getGridOptions() : undefined;
  }

  // ---- the tile -------------------------------------------------------

  async _build() {
    if (!this.config) return;

    const key = JSON.stringify(this.config);
    if (this._inner && this._innerKey === key) return;

    const token = ++this._buildToken;
    this._building = true;

    try {
      const { type, ...tileOptions } = this.config; // eslint-disable-line no-unused-vars
      const tileConfig = {
        tap_action: { action: 'toggle' },
        ...tileOptions,
        type: 'tile',
        // Always ours, whatever the author put in the YAML.
        double_tap_action: { action: 'fire-dom-event', lighting_manager_countdown: true }
      };

      const helpers = window.loadCardHelpers ? await window.loadCardHelpers() : null;
      if (!helpers) throw new Error('Home Assistant card helpers are not available.');

      const el = await helpers.createCardElement(tileConfig);
      if (token !== this._buildToken) return;
      if (this._hass) el.hass = this._hass;

      this._inner = el;
      this._innerKey = key;
      this.innerHTML = '';
      this.appendChild(el);
      this._updateBadge();
    } catch (err) {
      if (token !== this._buildToken) return;
      this._inner = null;
      this._innerKey = null;
      this.innerHTML = `
        <ha-card>
          <div style="padding:12px 16px;font-size:13px;color:var(--error-color)">
            ⚠️ ${this._escape(err?.message || String(err))}
          </div>
        </ha-card>`;
    } finally {
      if (token === this._buildToken) this._building = false;
    }
  }

  _handleCustomEvent(e) {
    if (!e.detail || !e.detail.lighting_manager_countdown) return;
    e.stopPropagation();
    this._openPopup();
  }

  // ---- helpers --------------------------------------------------------

  _escape(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[c]));
  }

  _formatRemaining(ms) {
    const totalSeconds = Math.max(0, Math.round(ms / 1000));
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    const mm = h ? String(m).padStart(2, '0') : String(m);
    return `${h ? h + ':' : ''}${mm}:${String(s).padStart(2, '0')}`;
  }

  _displayName() {
    const st = this._hass?.states?.[this.config.entity];
    return this.config.name || st?.attributes?.friendly_name || this.config.entity;
  }

  // ---- countdown state -------------------------------------------------

  // Absolute expiry (ms since epoch) of this light's countdown, or null.
  _expiry() {
    const iso = lmCountdowns.expiresAt(this.config?.entity);
    if (!iso) return null;
    const ms = new Date(iso).getTime();
    return Number.isFinite(ms) && ms > Date.now() ? ms : null;
  }

  // Called by the shared store after every refresh.
  _countdownChanged() {
    this._updateBadge();
    if (this._expiry()) this._startTick();
    else this._stopTick();
    if (this._overlay) {
      // Redraw only if something the popup shows has changed, so a poll
      // never steals focus from the custom-minutes box.
      if (this._popupSignature() !== this._renderedSig) this._renderPopup();
    }
  }

  _popupSignature() {
    return [lmCountdowns.loaded, lmCountdowns.failed, this._expiry() !== null, this._mode].join('|');
  }

  _startTick() {
    if (this._tickTimer) return;
    this._tickTimer = setInterval(() => {
      const expiry = this._expiry();
      if (!expiry) {
        this._stopTick();
        // The local clock says it has finished; confirm with Lighting Manager.
        lmCountdowns.refresh();
        return;
      }
      this._paintClock(expiry - Date.now());
    }, 1000);
  }

  _stopTick() {
    if (this._tickTimer) {
      clearInterval(this._tickTimer);
      this._tickTimer = null;
    }
  }

  // Update only the clock text (badge and popup) so typing is never disturbed.
  _paintClock(ms) {
    const text = this._formatRemaining(ms);
    const badgeText = this._badge?.querySelector('[data-badge-text]');
    if (badgeText) badgeText.textContent = text;
    const clock = this._overlay?.querySelector('[data-remaining]');
    if (clock) clock.textContent = text;
  }

  // ---- the in-tile timer badge ----------------------------------------

  _updateBadge() {
    const expiry = this._expiry();

    if (!expiry) {
      if (this._badge) {
        this._badge.remove();
        this._badge = null;
      }
      return;
    }

    if (!this._badge) {
      this.style.position = 'relative';
      this.style.display = 'block';
      const badge = document.createElement('div');
      badge.className = 'lm-cd-badge';
      badge.setAttribute('aria-label', 'Countdown running');
      badge.style.cssText =
        // Right-hand side, level with the tile's state line ("On"/"Off"), so it
        // never covers the light's name on the line above. A tile with a
        // feature row (the brightness slider) is taller, so measure from the
        // top there; otherwise from the bottom.
        (this.config?.features?.length
          ? 'position:absolute;top:30px;right:10px;'
          : 'position:absolute;bottom:5px;right:10px;') +
        'display:flex;align-items:center;gap:2px;padding:1px 7px 1px 5px;' +
        'border-radius:11px;font-size:11.5px;font-weight:600;line-height:18px;' +
        'font-variant-numeric:tabular-nums;pointer-events:none;z-index:1;' +
        'background:var(--primary-color,#03a9f4);color:var(--text-primary-color,#fff);';
      badge.innerHTML =
        '<ha-icon icon="mdi:timer-outline" style="--mdc-icon-size:13px;display:flex"></ha-icon>' +
        '<span data-badge-text></span>';
      this._badge = badge;
    }

    if (this._badge.parentNode !== this) this.appendChild(this._badge);
    this._paintClock(expiry - Date.now());
    this._startTick();
  }

  // ---- countdown backend ---------------------------------------------

  async _start(minutes) {
    if (!this._hass || this._busy || !(minutes > 0)) return;
    const st = this._hass.states?.[this.config.entity];
    const turnOnFirst = !st || st.state !== 'on';

    this._busy = true;
    this._renderPopup();
    try {
      await this._hass.callWS({
        type: 'lighting_manager/start_countdown',
        entity_id: this.config.entity,
        minutes,
        turn_on_first: turnOnFirst
      });
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._busy = false;
    this._mode = 'auto';
    await lmCountdowns.refresh();
    this._renderPopup();
  }

  async _cancel() {
    if (!this._hass || this._busy) return;
    this._busy = true;
    this._renderPopup();
    try {
      await this._hass.callWS({
        type: 'lighting_manager/cancel_countdown',
        entity_id: this.config.entity
      });
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._busy = false;
    this._mode = 'auto';
    await lmCountdowns.refresh();
    this._renderPopup();
  }

  // ---- popup ----------------------------------------------------------

  _openPopup() {
    if (this._overlay) return;
    this._customMinutes = '';
    this._error = null;
    this._mode = 'auto';

    const overlay = document.createElement('div');
    overlay.className = 'lm-cd-overlay';
    overlay.addEventListener('click', e => {
      if (e.target === overlay) this._closePopup();
    });
    document.body.appendChild(overlay);
    document.addEventListener('keydown', this._onKey);
    this._overlay = overlay;

    // Draw from what is already known (no flash of the setup view for a
    // light that has a countdown), then confirm with Lighting Manager.
    this._renderPopup();
    lmCountdowns.refresh();
  }

  _closePopup() {
    document.removeEventListener('keydown', this._onKey);
    if (this._overlay) {
      this._overlay.remove();
      this._overlay = null;
    }
    this._mode = 'auto';
    this._renderedSig = null;
    // The clock keeps ticking for the badge if a countdown is running.
    if (!this._expiry()) this._stopTick();
  }

  _renderPopup() {
    const overlay = this._overlay;
    if (!overlay) return;

    const busy = this._busy ? 'disabled' : '';
    const expiry = this._expiry();
    this._renderedSig = this._popupSignature();
    if (expiry) this._startTick();

    const setup = (label, withBack) => `
        ${withBack ? '<button class="lm-cd-back" data-back>‹ Back</button>' : ''}
        <div class="lm-cd-label">${label}</div>
        <div class="lm-cd-presets">
          ${COUNTDOWN_PRESETS_MIN.map(
            m => `<button class="lm-cd-btn" data-preset="${m}" ${busy}>${m} min</button>`
          ).join('')}
        </div>
        <div class="lm-cd-custom">
          <input type="number" inputmode="numeric" min="1" max="1440"
                 class="lm-cd-input" data-custom
                 placeholder="Custom (minutes)"
                 value="${this._escape(this._customMinutes)}" ${busy} />
          <button class="lm-cd-btn lm-cd-start" data-start ${busy}>Start</button>
        </div>`;

    let body;
    if (!lmCountdowns.loaded && !lmCountdowns.failed) {
      body = '<div class="lm-cd-label">Loading…</div>';
    } else if (expiry && this._mode === 'change') {
      body = setup('Change the countdown to', true);
    } else if (expiry) {
      body = `
        <div class="lm-cd-label">Turns off in</div>
        <div class="lm-cd-clock" data-remaining>${this._escape(this._formatRemaining(expiry - Date.now()))}</div>
        <div class="lm-cd-row">
          <button class="lm-cd-btn" data-change ${busy}>Change</button>
          <button class="lm-cd-btn lm-cd-cancel" data-cancel ${busy}>Cancel countdown</button>
        </div>
        <button class="lm-cd-btn lm-cd-start lm-cd-ok" data-ok>OK</button>`;
    } else {
      body = setup('Turn off after', false);
    }

    if (this._error) {
      body += `<div class="lm-cd-error">⚠️ ${this._escape(this._error)}</div>`;
    }

    overlay.innerHTML = `
      <style>
        .lm-cd-overlay {
          position: fixed; inset: 0; z-index: 9999;
          display: flex; align-items: center; justify-content: center;
          padding: 16px; box-sizing: border-box;
          background: rgba(0, 0, 0, 0.5);
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
        }
        .lm-cd-dialog {
          width: 100%; max-width: 340px; box-sizing: border-box;
          padding: 16px 16px 18px;
          border-radius: 16px;
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #212121);
          box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        }
        .lm-cd-head {
          display: flex; align-items: center; justify-content: space-between;
          gap: 8px; margin-bottom: 12px;
        }
        .lm-cd-title {
          font-size: 18px; font-weight: 600;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .lm-cd-close {
          flex: 0 0 auto; width: 32px; height: 32px; border: 0; border-radius: 50%;
          background: transparent; color: var(--secondary-text-color, #727272);
          font-size: 22px; line-height: 1; cursor: pointer;
        }
        .lm-cd-label {
          font-size: 13px; color: var(--secondary-text-color, #727272);
          margin-bottom: 8px;
        }
        .lm-cd-clock {
          font-size: 40px; font-weight: 600; text-align: center;
          font-variant-numeric: tabular-nums; padding: 8px 0 16px;
        }
        .lm-cd-presets {
          display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
        }
        .lm-cd-custom {
          display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px;
          margin-top: 8px;
        }
        .lm-cd-btn {
          font: inherit; font-size: 15px; min-height: 44px; padding: 0 16px;
          border: 0; border-radius: 12px; cursor: pointer;
          background: var(--secondary-background-color, #e5e5e5);
          color: var(--primary-text-color, #212121);
          -webkit-tap-highlight-color: transparent;
        }
        .lm-cd-btn:disabled { opacity: .5; cursor: default; }
        .lm-cd-start {
          background: var(--primary-color, #03a9f4);
          color: var(--text-primary-color, #fff);
        }
        .lm-cd-cancel {
          background: var(--error-color, #db4437);
          color: var(--text-primary-color, #fff);
        }
        .lm-cd-row {
          display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
        }
        .lm-cd-ok { width: 100%; margin-top: 8px; }
        .lm-cd-back {
          font: inherit; font-size: 14px; border: 0; background: transparent;
          color: var(--primary-color, #03a9f4); cursor: pointer;
          padding: 0 0 8px; min-height: 32px;
        }
        .lm-cd-input {
          font: inherit; font-size: 16px; /* 16px stops iOS zooming on focus */
          width: 100%; min-width: 0; min-height: 44px; box-sizing: border-box;
          padding: 0 12px; border-radius: 12px;
          border: 1px solid var(--divider-color, #ccc);
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #212121);
        }
        .lm-cd-error { margin-top: 12px; font-size: 13px; color: var(--error-color, #db4437); }
      </style>
      <div class="lm-cd-dialog" role="dialog" aria-modal="true">
        <div class="lm-cd-head">
          <div class="lm-cd-title">${this._escape(this._displayName())}</div>
          <button class="lm-cd-close" data-close aria-label="Close">×</button>
        </div>
        ${body}
      </div>`;

    overlay.querySelector('[data-close]')?.addEventListener('click', () => this._closePopup());

    overlay.querySelectorAll('[data-preset]').forEach(el => {
      el.addEventListener('click', () => this._start(Number(el.getAttribute('data-preset'))));
    });

    const input = overlay.querySelector('[data-custom]');
    input?.addEventListener('input', e => {
      this._customMinutes = e.target.value;
    });
    input?.addEventListener('keydown', e => {
      if (e.key === 'Enter') this._start(Number(this._customMinutes));
    });

    overlay.querySelector('[data-start]')?.addEventListener('click', () => {
      this._start(Number(this._customMinutes));
    });

    overlay.querySelector('[data-cancel]')?.addEventListener('click', () => this._cancel());

    overlay.querySelector('[data-ok]')?.addEventListener('click', () => this._closePopup());

    overlay.querySelector('[data-change]')?.addEventListener('click', () => {
      this._mode = 'change';
      this._renderPopup();
    });

    overlay.querySelector('[data-back]')?.addEventListener('click', () => {
      this._mode = 'auto';
      this._renderPopup();
    });
  }
}

if (!customElements.get('lighting-manager-countdown-card')) {
  customElements.define('lighting-manager-countdown-card', LightingManagerCountdownCard);
}

window.customCards = window.customCards || [];

if (!window.customCards.some(x => x.type === 'lighting-manager-countdown-card')) {
  window.customCards.push({
    type: 'lighting-manager-countdown-card',
    name: 'Lighting Manager Countdown Card',
    description: 'A native tile for one light, with a double-tap countdown timer backed by Lighting Manager.'
  });
}

console.info(
  '%c LIGHTING-MANAGER-COUNTDOWN-CARD %c v2.1.1 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
}
} catch (error) {
  console.error('Lighting Manager: lighting-manager-countdown-card.js failed to load:', error);
}

/* ===== light-scheduler-list-card.js ===== */
try {
{
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
  // area: may be an Area id (living_room) or its name (Lounge Big), matched
  // case/punctuation-insensitively against Home Assistant's own Area
  // registry - Lighting Manager keeps no Area list of its own.
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
    if (this._schedulerCard) this._schedulerCard.hass = hass;

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
    this._closeScheduler();
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  async _fetchAreaLights() {
    if (!this._hass || !this._isAreaMode()) return;

    const token = ++this._fetchToken;
    const areaBeingFetched = this.config.area;

    const resolved = this._resolveArea();
    if (!resolved.id) {
      this._loaded = true;
      this._fetchedArea = areaBeingFetched;
      this._resolvedLights = [];
      this._error = resolved.error;
      this._render();
      return;
    }

    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: 'lighting_manager/list_area_lights',
        area_id: resolved.id
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
        area_id: this._resolveArea().id,
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

  // Opens the Scheduler Card in a dialog that Lighting Manager draws itself
  // (v5.2.0 - replaces the Browser Mod popup, so Browser Mod is no longer
  // needed). The dialog is attached to Home Assistant's own root element,
  // next to its dialogs, at a z-index just below them, so the Scheduler
  // Card's own confirmation dialogs (e.g. delete) still appear on top.
  async _openScheduler(title, include = null) {
    if (!this._hass) return;
    this._closeScheduler();

    const entities = include || this._roomLights();
    const heading = title || `${this.config.title || 'Lights'} schedules`;

    const host =
      document.querySelector('home-assistant')?.shadowRoot || document.body;

    const overlay = document.createElement('div');
    overlay.className = 'lsl-dialog-overlay';
    overlay.innerHTML = `
      <style>
        .lsl-dialog-overlay {
          position: fixed; inset: 0; z-index: 7;
          display: flex; align-items: flex-start; justify-content: center;
          padding: 5vh 12px 12px; box-sizing: border-box;
          background: rgba(0, 0, 0, 0.5);
          overflow-y: auto; -webkit-overflow-scrolling: touch;
        }
        .lsl-dialog {
          width: 100%; max-width: 640px; box-sizing: border-box;
          border-radius: 16px; overflow: hidden;
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #212121);
          box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        }
        .lsl-dialog-head {
          display: flex; align-items: center; justify-content: space-between;
          gap: 8px; padding: 12px 8px 4px 16px;
        }
        .lsl-dialog-title {
          font-size: 18px; font-weight: 600;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .lsl-dialog-close {
          flex: 0 0 auto; width: 36px; height: 36px; border: 0; border-radius: 50%;
          background: transparent; color: var(--secondary-text-color, #727272);
          font-size: 24px; line-height: 1; cursor: pointer;
        }
        .lsl-dialog-body { padding: 0 0 8px; }
        .lsl-dialog-msg { padding: 16px; font-size: 14px; color: var(--secondary-text-color, #727272); }
      </style>
      <div class="lsl-dialog" role="dialog" aria-modal="true">
        <div class="lsl-dialog-head">
          <div class="lsl-dialog-title"></div>
          <button class="lsl-dialog-close" aria-label="Close">&times;</button>
        </div>
        <div class="lsl-dialog-body">
          <div class="lsl-dialog-msg">Loading…</div>
        </div>
      </div>`;
    overlay.querySelector('.lsl-dialog-title').textContent = heading;

    overlay.addEventListener('click', e => {
      if (e.target === overlay) this._closeScheduler();
    });
    overlay.querySelector('.lsl-dialog-close').addEventListener('click', () => this._closeScheduler());

    this._onSchedulerKey = e => {
      if (e.key === 'Escape') this._closeScheduler();
    };
    document.addEventListener('keydown', this._onSchedulerKey);

    host.appendChild(overlay);
    this._schedulerOverlay = overlay;

    const body = overlay.querySelector('.lsl-dialog-body');

    try {
      const helpers = window.loadCardHelpers ? await window.loadCardHelpers() : null;
      if (!helpers) throw new Error('Home Assistant card helpers are not available.');

      const card = await helpers.createCardElement({
        type: 'custom:scheduler-card',
        title: false,
        include: entities,
        discover_existing: false,
        show_header_toggle: false,
        show_toggle_switches: true,
        show_add_button: true,
        display_options: {
          primary_info: ['{entity}: {action}'],
          secondary_info: ['days', 'time']
        },
        sort_by: ['relative-time', 'state']
      });

      // The dialog may have been closed while the card was being created.
      if (this._schedulerOverlay !== overlay) return;

      card.hass = this._hass;
      body.innerHTML = '';
      body.appendChild(card);
      this._schedulerCard = card;
    } catch (err) {
      if (this._schedulerOverlay !== overlay) return;
      body.innerHTML = '';
      const msg = document.createElement('div');
      msg.className = 'lsl-dialog-msg';
      msg.style.color = 'var(--error-color)';
      msg.textContent = `Could not open the Scheduler Card - is it installed? (${err?.message || err})`;
      body.appendChild(msg);
    }
  }

  _closeScheduler() {
    if (this._onSchedulerKey) {
      document.removeEventListener('keydown', this._onSchedulerKey);
      this._onSchedulerKey = null;
    }
    if (this._schedulerOverlay) {
      this._schedulerOverlay.remove();
      this._schedulerOverlay = null;
    }
    this._schedulerCard = null;
  }

  _render() {
    if (!this.config) return;

    const hass = this._hass;
    const areaMode = this._isAreaMode();
    // Reordering only exists in area mode (the order is stored by Lighting Manager).
    const canReorder = areaMode && !!this._hass?.user?.is_admin;
    const area = areaMode ? this._hass?.areas?.[this._resolveArea().id] : null;

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
  '%c LIGHT-SCHEDULER-LIST-CARD %c v5.2.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
}
} catch (error) {
  console.error('Lighting Manager: light-scheduler-list-card.js failed to load:', error);
}

/* ===== lighting-manager-scheduler-card.js ===== */
try {
{
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
}
} catch (error) {
  console.error('Lighting Manager: lighting-manager-scheduler-card.js failed to load:', error);
}

/* ===== lighting-manager-room-card.js ===== */
try {
{
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
}
} catch (error) {
  console.error('Lighting Manager: lighting-manager-room-card.js failed to load:', error);
}

/* ===== lighting-manager-overview-card.js ===== */
try {
{
/**
 * Lighting Manager Overview Card  v1.1.0
 * (PRD Revision - Lighting-Aware Dashboard Automation, section 4.8.)
 *
 * One card, a whole lighting page:
 *
 *   type: custom:lighting-manager-overview-card
 *
 * Optional: `title: Lights` (default), `columns: 2` (1-6), and
 * `show_schedules: false` to leave out the schedules under each room.
 *
 * What it draws, with nothing to maintain:
 *   - "On now": every light that is on, with its room, and Turn all off.
 *   - One section per room, listing every light Lighting Manager has
 *     adopted (the "Include on dashboard" tick is not used here: this is
 *     the complete picture of the house). Each room has its own on/off
 *     buttons and can be folded away (remembered on this device).
 *   - Each light is the same native tile as everywhere else (tap toggles,
 *     double-tap opens the countdown), with the light's Lighting Manager
 *     tile width and brightness slider.
 *   - Under each room's tiles, a "Schedules" row opens Niels Faber's
 *     Scheduler Card for that room's lights (view, edit and add schedules
 *     right on the page). It starts open for rooms that have schedules; the
 *     choice is remembered on this device.
 *
 * Add a room, adopt a light, rename or reorder in Lighting Manager and the
 * card follows. Any user can use it; there is no editing here.
 */
const OVERVIEW_REFRESH_MS = 60000;
const OVERVIEW_COLLAPSED_KEY = 'lighting-manager-overview-collapsed';
const OVERVIEW_SCHEDULES_KEY = 'lighting-manager-overview-schedules';

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
    this._schedCards = [];
    this._collapsed = this._loadCollapsed();
    this._schedOpen = this._loadSchedOpen();
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
    for (const card of this._schedCards) card.hass = hass;
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

  _loadSchedOpen() {
    try {
      const raw = window.localStorage.getItem(OVERVIEW_SCHEDULES_KEY);
      const obj = raw ? JSON.parse(raw) : {};
      return obj && typeof obj === 'object' ? obj : {};
    } catch (err) {
      return {};
    }
  }

  _saveSchedOpen() {
    try {
      window.localStorage.setItem(OVERVIEW_SCHEDULES_KEY, JSON.stringify(this._schedOpen));
    } catch (err) {
      /* remembering this is a convenience only */
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
              .map(l => `${l.entity_id}:${l.width}:${l.slider ? 's' : ''}:${l.schedulable ? '' : 'x'}`)
              .join(',')
        )
        .join('|');
    if (this._renderedKey === key) {
      this._updateScheduleCounts(rooms);
      return;
    }

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
    this._schedCards = [];
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
      .lm-ov-cell > lighting-manager-countdown-card { display:block; }
      .lm-ov-now { border:1px solid var(--divider-color); border-radius:16px; padding:12px;
        background:color-mix(in srgb, var(--state-light-active-color, var(--primary-color)) 7%, transparent); }
      .lm-ov-divider { display:flex; align-items:center; gap:12px; margin:4px 0 0;
        color:var(--secondary-text-color); font-size:12px; letter-spacing:.08em; text-transform:uppercase; }
      .lm-ov-divider::before, .lm-ov-divider::after { content:''; flex:1; height:1px; background:var(--divider-color); }
      .lm-ov-room { padding-bottom:4px; }
      .lm-ov-schedhead { display:flex; align-items:center; gap:8px; width:100%; margin:8px 0 0; padding:8px 4px;
        border:0; border-top:1px solid var(--divider-color); background:transparent; cursor:pointer;
        font:inherit; font-size:14px; color:var(--secondary-text-color); text-align:left; }
      .lm-ov-schedhead ha-icon { --mdc-icon-size:18px; display:flex; }
      .lm-ov-schedbody { padding:4px 0 8px; }
      .lm-ov-schedmsg { padding:8px 4px; font-size:13px; color:var(--secondary-text-color); }
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
    now.className = 'lm-ov-now';
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

    // Divider between "On now" and the full list ---------------------------------
    const divider = document.createElement('div');
    divider.className = 'lm-ov-divider';
    divider.textContent = 'All lights by room';
    root.appendChild(divider);

    // Rooms ------------------------------------------------------------------
    this._roomRefs = [];
    for (const room of rooms) {
      root.appendChild(this._buildRoom(room));
    }

    this.innerHTML = '';
    this.appendChild(root);
    this._updateScheduleCounts(rooms);
    this._ready = true;
    this._updateLive();
  }

  _buildRoom(room) {
    const section = document.createElement('section');
    section.className = 'lm-ov-room';
    const collapseKey = room.areaId || '__none__';

    const head = document.createElement('div');
    head.className = 'lm-ov-head';
    const main = document.createElement('button');
    main.type = 'button';
    main.className = 'lm-ov-head-main';
    const area = this._hass?.areas?.[room.areaId];
    const chevron = document.createElement('ha-icon');
    const icon = document.createElement('ha-icon');
    icon.setAttribute('icon', area?.icon || 'mdi:lightbulb-outline');
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
      grid.appendChild(cell);
    }
    section.appendChild(grid);

    // Schedules row: Niels Faber's Scheduler Card for this room's lights.
    const schedulable = room.lights.filter(l => l.schedulable).map(l => l.entity_id);
    let schedChip = null;
    let schedBody = null;
    let schedHead = null;
    let applySched = () => {};
    if (this.config.show_schedules && schedulable.length) {
      schedHead = document.createElement('button');
      schedHead.type = 'button';
      schedHead.className = 'lm-ov-schedhead';
      const schedChev = document.createElement('ha-icon');
      const schedIcon = document.createElement('ha-icon');
      schedIcon.setAttribute('icon', 'mdi:calendar-clock-outline');
      const schedLabel = document.createElement('span');
      schedLabel.textContent = 'Schedules';
      schedChip = document.createElement('span');
      schedChip.className = 'lm-ov-chip';
      schedHead.append(schedChev, schedIcon, schedLabel, schedChip);
      schedBody = document.createElement('div');
      schedBody.className = 'lm-ov-schedbody';
      section.append(schedHead, schedBody);

      const isOpen = () => {
        const saved = this._schedOpen[collapseKey];
        if (typeof saved === 'boolean') return saved;
        return room.lights.some(l => l.schedulable && l.schedules > 0);
      };
      applySched = () => {
        const open = isOpen();
        schedBody.hidden = !open;
        schedChev.setAttribute('icon', open ? 'mdi:chevron-down' : 'mdi:chevron-right');
        schedHead.setAttribute('aria-expanded', String(open));
        if (open && !schedBody.dataset.built) {
          schedBody.dataset.built = '1';
          this._buildSchedulerCard(schedBody, schedulable);
        }
      };
      schedHead.addEventListener('click', () => {
        this._schedOpen[collapseKey] = !isOpen();
        this._saveSchedOpen();
        applySched();
      });
      applySched();
    }

    const applyCollapsed = () => {
      const folded = this._collapsed.has(collapseKey);
      grid.hidden = folded;
      if (schedHead) schedHead.hidden = folded;
      if (schedBody) {
        if (folded) schedBody.hidden = true;
        else applySched();
      }
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

    this._roomRefs.push({ ids, chip, offBtn, onBtn, schedChip, areaKey: room.areaId, lights: room.lights });
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

  async _buildSchedulerCard(body, entities) {
    body.innerHTML = '<div class="lm-ov-schedmsg">Loading schedules…</div>';
    try {
      const helpers = window.loadCardHelpers ? await window.loadCardHelpers() : null;
      if (!helpers) throw new Error('Home Assistant card helpers are not available.');
      const card = await helpers.createCardElement({
        type: 'custom:scheduler-card',
        title: false,
        include: entities,
        discover_existing: false,
        show_header_toggle: false,
        show_toggle_switches: true,
        show_add_button: true,
        display_options: {
          primary_info: ['{entity}: {action}'],
          secondary_info: ['days', 'time']
        },
        sort_by: ['relative-time', 'state']
      });
      if (this._hass) card.hass = this._hass;
      body.innerHTML = '';
      body.appendChild(card);
      this._schedCards.push(card);
    } catch (err) {
      body.innerHTML = '';
      const msg = document.createElement('div');
      msg.className = 'lm-ov-schedmsg';
      msg.style.color = 'var(--error-color)';
      msg.textContent = `Could not open the Scheduler Card - is it installed? (${err?.message || err})`;
      body.appendChild(msg);
    }
  }

  _updateScheduleCounts(rooms) {
    const byArea = new Map((rooms || []).map(r => [r.areaId, r]));
    for (const ref of this._roomRefs || []) {
      if (!ref.schedChip) continue;
      const room = byArea.get(ref.areaKey);
      const n = room ? room.lights.filter(l => l.schedulable && l.schedules > 0).length : 0;
      ref.schedChip.textContent = n > 0 ? `${n} scheduled` : 'none';
      ref.schedChip.classList.toggle('on', n > 0);
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
  '%c LIGHTING-MANAGER-OVERVIEW-CARD %c v1.1.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
}
} catch (error) {
  console.error('Lighting Manager: lighting-manager-overview-card.js failed to load:', error);
}

/* ===== load report ===== */
console.info('LIGHTING-MANAGER-CARDS bundle ran:', ['lighting-manager-countdown-card', 'light-scheduler-list-card', 'lighting-manager-scheduler-card', 'lighting-manager-room-card', 'lighting-manager-overview-card'].map(n => n + (customElements.get(n) ? ' ok' : ' MISSING')).join(', '));
