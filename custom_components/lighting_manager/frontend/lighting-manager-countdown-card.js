/**
 * Lighting Manager countdown card  v2.1.0
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
        // Bottom-right, level with the tile's state line ("On"/"Off"), so it
        // never covers the light's name on the line above.
        'position:absolute;bottom:5px;right:10px;' +
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
  '%c LIGHTING-MANAGER-COUNTDOWN-CARD %c v2.1.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
