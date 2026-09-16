/**
 * Lighting Manager countdown card (PRD Revision - Lighting-Aware
 * Dashboard Automation §4.3).
 *
 * One light per card. Replaces Simple Timer / custom:timer-card for a
 * light entirely - same countdown engine already built and tested in
 * countdown.py (start_countdown/cancel_countdown, already exposed via
 * api.py), just a native frontend for it.
 *
 * Interaction model, decided with Hans (round 2) after explicitly NOT
 * assuming it up front:
 * - Single tap toggles the light, same as any other light card.
 * - Double tap opens the countdown panel (presets + custom duration, or
 *   the live countdown + Cancel if one is already running).
 * - The collapsed row shows nothing about countdown state, active or
 *   not - "on a dashboard I don't need to see the countdown timer."
 *   The live, smoothly-ticking countdown only appears once the panel is
 *   open ("shown smoothly - but only if it's set").
 *
 * DELIBERATE CHOICE - same as lighting-manager-panel.js and
 * light-scheduler-list-card.js: plain JS, no Lit, no build step, no
 * CDN. See lighting-manager-panel.js's header comment for the full
 * rationale; it applies identically here.
 *
 * Basic display (icon/name/on-off) reads live from hass.states directly
 * and has NO dependency on the Lighting Manager backend being up - only
 * opening the countdown panel calls the lighting_manager/* websocket
 * API, so a light still toggles normally even if the integration is
 * briefly unavailable; only the countdown feature itself degrades.
 *
 * KNOWN LIMITATION, not fixed here: countdown.py's turn_on_first/expiry
 * logic calls the `light` domain's turn_on/turn_off unconditionally,
 * so a countdown started against a promoted switch.* entity (PRD §11.1)
 * will not actually do anything at expiry - that's a pre-existing gap
 * in the countdown engine itself, out of scope for this card.
 *
 * TWO VALUES DUPLICATED FROM THE BACKEND - keep in sync by hand, same
 * caveat as lighting-manager-panel.js:
 */
const COUNTDOWN_PRESETS_MIN = [15, 30, 60, 120];

const DOUBLE_TAP_WINDOW_MS = 300;

class LightingManagerCountdownCard extends HTMLElement {
  setConfig(config) {
    if (!config || typeof config.entity !== 'string' || !config.entity) {
      throw new Error('entity is required - the light (or promoted switch) this card controls.');
    }

    this.config = config;
    this._expanded = this._expanded ?? false;
    this._active = this._active ?? false;
    this._countdownExpiresAt = this._countdownExpiresAt ?? null;
    this._customMinutes = this._customMinutes ?? '';
    this._busy = this._busy ?? false;
    this._lastTap = this._lastTap ?? 0;
    this._singleTapTimer = this._singleTapTimer ?? null;
    this._tickTimer = this._tickTimer ?? null;

    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  disconnectedCallback() {
    this._stopTick();
    if (this._singleTapTimer) {
      clearTimeout(this._singleTapTimer);
      this._singleTapTimer = null;
    }
  }

  getCardSize() {
    return this._expanded ? 2 : 1;
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

  _formatRemaining(ms) {
    const totalSeconds = Math.max(0, Math.round(ms / 1000));
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  async _fetchCountdownState() {
    if (!this._hass || !this.config?.entity) return;

    try {
      const result = await this._hass.callWS({ type: 'lighting_manager/list_lights' });
      const light = (result.lights || []).find(l => l.entity_id === this.config.entity);

      this._active = !!light?.countdown_expires_at;
      this._countdownExpiresAt = light?.countdown_expires_at || null;
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
      this._active = false;
      this._countdownExpiresAt = null;
    }

    if (this._active) {
      this._startTick();
    } else {
      this._stopTick();
    }

    this._render();
  }

  _startTick() {
    if (this._tickTimer) return;

    this._tickTimer = setInterval(() => {
      if (!this._countdownExpiresAt) {
        this._stopTick();
        return;
      }

      const remaining = new Date(this._countdownExpiresAt).getTime() - Date.now();
      if (remaining <= 0) {
        this._stopTick();
        this._active = false;
        this._countdownExpiresAt = null;
        // Confirm with the backend rather than just trusting the local
        // clock - the actual turn-off is whatever countdown.py did.
        this._fetchCountdownState();
        return;
      }

      this._render();
    }, 1000);
  }

  _stopTick() {
    if (this._tickTimer) {
      clearInterval(this._tickTimer);
      this._tickTimer = null;
    }
  }

  async _toggleExpanded() {
    this._expanded = !this._expanded;

    if (this._expanded) {
      await this._fetchCountdownState();
    } else {
      this._stopTick();
      this._render();
    }
  }

  _handleRowTap(e) {
    e.stopPropagation();
    const now = Date.now();

    if (this._lastTap && now - this._lastTap < DOUBLE_TAP_WINDOW_MS) {
      clearTimeout(this._singleTapTimer);
      this._lastTap = 0;
      this._toggleExpanded();
      return;
    }

    this._lastTap = now;
    this._singleTapTimer = setTimeout(() => {
      this._lastTap = 0;
      this._hass?.callService('light', 'toggle', { entity_id: this.config.entity });
    }, DOUBLE_TAP_WINDOW_MS);
  }

  async _startCountdown(minutes) {
    if (!this._hass || !this.config?.entity || this._busy) return;
    if (!(minutes > 0)) return;

    const state = this._hass.states?.[this.config.entity];
    const turnOnFirst = !state || state.state !== 'on';

    this._busy = true;
    this._render();

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
    await this._fetchCountdownState();
  }

  async _cancelCountdown() {
    if (!this._hass || !this.config?.entity || this._busy) return;

    this._busy = true;
    this._render();

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
    await this._fetchCountdownState();
  }

  _render() {
    if (!this.config) return;

    const hass = this._hass;
    const entityId = this.config.entity;
    const st = hass?.states?.[entityId];

    const state = st?.state || 'unknown';
    const on = state === 'on';
    const bad = state === 'unavailable' || state === 'unknown';

    const icon = this.config.icon || st?.attributes?.icon || 'mdi:lightbulb-outline';
    const name = this.config.name || st?.attributes?.friendly_name || entityId;

    let panelHtml = '';

    if (this._expanded) {
      if (this._active && this._countdownExpiresAt) {
        const remaining = new Date(this._countdownExpiresAt).getTime() - Date.now();

        panelHtml = `
          <div class="lm-countdown-panel">
            <div class="lm-countdown-remaining">
              Off in ${this._escape(this._formatRemaining(remaining))}
            </div>
            <button class="lm-btn lm-cancel-btn" data-cancel ${this._busy ? 'disabled' : ''}>
              Cancel countdown
            </button>
          </div>
        `;
      } else {
        panelHtml = `
          <div class="lm-countdown-panel">
            <div class="lm-preset-row">
              ${COUNTDOWN_PRESETS_MIN.map(
                m => `
                  <button class="lm-btn lm-preset-btn" data-preset="${m}" ${this._busy ? 'disabled' : ''}>
                    ${m}m
                  </button>
                `
              ).join('')}
            </div>
            <div class="lm-custom-row">
              <input
                type="number"
                min="1"
                class="lm-custom-input"
                placeholder="Custom minutes"
                value="${this._escape(this._customMinutes)}"
                ${this._busy ? 'disabled' : ''}
              />
              <button class="lm-btn lm-start-btn" data-start-custom ${this._busy ? 'disabled' : ''}>
                Start
              </button>
            </div>
          </div>
        `;
      }

      if (this._error) {
        panelHtml += `<div class="lm-countdown-error">⚠️ ${this._escape(this._error)}</div>`;
      }
    }

    this.innerHTML = `
      <ha-card>

        <style>

          ha-card {
            padding: 0;
            margin: 0;
            border-radius: 14px;
            overflow: hidden;
          }

          button {
            font: inherit;
            -webkit-tap-highlight-color: transparent;
          }

          .lm-row {
            height: 48px;

            display: flex;
            align-items: center;

            padding: 0 8px;

            width: 100%;

            border: 0;
            background: transparent;
            cursor: pointer;

            text-align: left;

            color: var(--primary-text-color);
          }

          .lm-icon {
            --mdc-icon-size: 27px;

            width: 34px;
            flex: 0 0 34px;

            text-align: center;
          }

          .lm-icon.on {
            color: var(--amber-color, #ffc107);
          }

          .lm-icon.off {
            color: var(--disabled-text-color);
          }

          .lm-icon.bad {
            color: var(--error-color);
          }

          .lm-name {
            font-size: 16px;
            font-weight: 650;

            margin-left: 9px;

            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .lm-countdown-panel {
            padding: 4px 12px 14px 12px;

            border-top: 1px solid var(--divider-color);
          }

          .lm-countdown-remaining {
            font-size: 20px;
            font-weight: 650;

            padding: 10px 0 8px 0;

            color: var(--primary-text-color);
          }

          .lm-btn {
            border: 0;
            border-radius: 8px;

            cursor: pointer;

            background: var(--secondary-background-color);
            color: var(--primary-text-color);

            padding: 8px 12px;
          }

          .lm-btn:disabled {
            opacity: .5;
            cursor: default;
          }

          .lm-preset-row {
            display: flex;
            flex-wrap: wrap;

            gap: 6px;

            padding-top: 8px;
          }

          .lm-preset-btn {
            flex: 1 1 auto;
            min-width: 52px;
          }

          .lm-custom-row {
            display: flex;

            gap: 6px;

            margin-top: 8px;
          }

          .lm-custom-input {
            flex: 1;
            min-width: 0;

            border-radius: 8px;
            border: 1px solid var(--divider-color);

            background: var(--card-background-color);
            color: var(--primary-text-color);

            padding: 8px 10px;

            font: inherit;
          }

          .lm-start-btn {
            background: var(--primary-color);
            color: var(--text-primary-color, white);
          }

          .lm-cancel-btn {
            background: var(--error-color);
            color: var(--text-primary-color, white);

            width: 100%;
          }

          .lm-countdown-error {
            margin-top: 10px;

            color: var(--error-color);
            font-size: 13px;
          }

        </style>

        <button class="lm-row" data-tap>
          <ha-icon
            class="lm-icon ${on ? 'on' : bad ? 'bad' : 'off'}"
            icon="${this._escape(icon)}"
          ></ha-icon>
          <span class="lm-name">${this._escape(name)}</span>
        </button>

        ${panelHtml}

      </ha-card>
    `;

    this.querySelector('[data-tap]')?.addEventListener('click', e => this._handleRowTap(e));

    this.querySelectorAll('[data-preset]').forEach(el => {
      el.addEventListener('click', e => {
        e.stopPropagation();
        this._startCountdown(Number(el.getAttribute('data-preset')));
      });
    });

    const customInput = this.querySelector('.lm-custom-input');
    customInput?.addEventListener('input', e => {
      this._customMinutes = e.target.value;
    });

    this.querySelector('[data-start-custom]')?.addEventListener('click', e => {
      e.stopPropagation();
      this._startCountdown(Number(this._customMinutes));
    });

    this.querySelector('[data-cancel]')?.addEventListener('click', e => {
      e.stopPropagation();
      this._cancelCountdown();
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
    description: 'One-light power toggle with a double-tap countdown timer, backed by Lighting Manager.'
  });
}

console.info(
  '%c LIGHTING-MANAGER-COUNTDOWN-CARD %c v1.0.0 ',
  'color: white; background: #03a9f4; font-weight: 700;',
  'color: #03a9f4; background: white; font-weight: 700;'
);
