/**
 * Lighting Manager panel - placeholder shell.
 *
 * This is deliberately minimal: it proves the panel_custom registration
 * in __init__.py works end-to-end (sidebar entry, static path, custom
 * element loading, websocket connectivity) without yet building the
 * real Rooms / Table / Inbox UI from PRD §7-§9. That's the next major
 * chunk of work - this file is the seam it plugs into.
 *
 * Confirms it can talk to the backend by calling lighting_manager/list_lights
 * over the same websocket connection the rest of the HA frontend uses,
 * and rendering a minimal count so there's something visible to verify
 * against during development.
 */
class LightingManagerPanel extends HTMLElement {
  setConfig() {}

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._render();
      this._loadLights();
    }
  }

  _render() {
    this.innerHTML = `
      <style>
        :host { display: block; padding: 16px; font-family: var(--paper-font-body1_-_font-family, sans-serif); }
        h1 { font-size: 1.4em; }
        #status { color: var(--secondary-text-color, #666); }
      </style>
      <h1>Lighting Manager</h1>
      <div id="status">Loading...</div>
    `;
  }

  async _loadLights() {
    const statusEl = this.querySelector("#status");
    try {
      const result = await this._hass.callWS({ type: "lighting_manager/list_lights" });
      const total = result.lights.length;
      const adopted = result.lights.filter((l) => l.adopted).length;
      statusEl.textContent =
        `Backend connected: ${total} eligible light${total === 1 ? "" : "s"} found, ` +
        `${adopted} adopted. Rooms / Table / Inbox views are not built yet in this scaffold.`;
    } catch (err) {
      statusEl.textContent = `Could not reach the Lighting Manager backend: ${err}`;
    }
  }
}

customElements.define("lighting-manager-panel", LightingManagerPanel);
