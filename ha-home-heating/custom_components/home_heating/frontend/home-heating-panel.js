// Home Heating sidebar page: live status and all settings.
// The settings form is generated from the schema in settings.py (websocket home_heating/config),
// so a new setting only has to be added there.

const WEEKDAYS = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };
const STATE_COLORS = { ok: "var(--success-color, #43a047)", warning: "var(--warning-color, #ffa600)", error: "var(--error-color, #db4437)" };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const fmt = (v, unit = "") => (v === null || v === undefined || v === "" ? "–" : `${typeof v === "number" ? Math.round(v * 100) / 100 : v}${unit ? " " + unit : ""}`);

class HomeHeatingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "status";
    this._settings = null; // saved settings
    this._draft = null; // edited settings
    this._sections = [];
    this._status = {};
    this._message = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
  }

  set narrow(value) {
    this._narrow = value;
  }

  connectedCallback() {
    this._timer = setInterval(() => this._refreshStatus(), 10000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  async _load() {
    try {
      const config = await this._hass.callWS({ type: "home_heating/config" });
      this._sections = config.sections;
      this._settings = config.settings;
      this._draft = structuredClone(config.settings);
      await this._refreshStatus(false);
    } catch (err) {
      this._message = { error: true, text: `Could not load Home Heating: ${err.message || err}` };
    }
    this._render();
  }

  async _refreshStatus(render = true) {
    if (!this._hass) return;
    try {
      this._status = await this._hass.callWS({ type: "home_heating/status" });
    } catch (err) {
      return;
    }
    if (render && this._tab === "status") this._renderContent();
  }

  get _dirty() {
    return JSON.stringify(this._draft) !== JSON.stringify(this._settings);
  }

  async _save() {
    try {
      const result = await this._hass.callWS({ type: "home_heating/save", settings: this._draft });
      this._settings = result.settings;
      this._draft = structuredClone(result.settings);
      this._message = { error: false, text: "Saved. The new settings are used directly." };
      setTimeout(() => this._refreshStatus(), 1500);
    } catch (err) {
      this._message = { error: true, text: err.message || String(err) };
    }
    this._render();
  }

  async _action(action, label) {
    this._message = { error: false, text: `${label}…` };
    this._renderMessage();
    try {
      this._status = await this._hass.callWS({ type: "home_heating/action", action });
      this._message = { error: false, text: `${label}: done` };
    } catch (err) {
      this._message = { error: true, text: `${label}: ${err.message || err}` };
    }
    this._render();
  }

  // ------------------------------------------------------------------------------------------ layout

  _render() {
    const root = this.shadowRoot;
    root.innerHTML = "";
    root.append(el("style", {}, STYLE));
    const tabs = [["status", "Status"], ...this._sections.map((s) => [s.id, s.title])];
    root.append(
      el("div", { class: "header" },
        el("div", { class: "title" }, "Home Heating"),
        el("div", { class: "tabs" }, tabs.map(([id, title]) =>
          el("button", { class: `tab ${this._tab === id ? "active" : ""}`, onclick: () => { this._tab = id; this._render(); } }, title)))),
      el("div", { class: "message-area" }),
      el("div", { class: "content" }),
    );
    this._renderMessage();
    this._renderContent();
  }

  _renderMessage() {
    const area = this.shadowRoot.querySelector(".message-area");
    if (!area) return;
    area.innerHTML = "";
    const bar = [];
    if (this._message) bar.push(el("div", { class: `message ${this._message.error ? "error" : ""}` }, this._message.text));
    if (this._draft && this._dirty) {
      bar.push(el("div", { class: "savebar" }, "Unsaved changes",
        el("button", { class: "primary", onclick: () => this._save() }, "Save"),
        el("button", { onclick: () => { this._draft = structuredClone(this._settings); this._message = null; this._render(); } }, "Undo")));
    }
    area.append(...bar);
  }

  _renderContent() {
    const content = this.shadowRoot.querySelector(".content");
    if (!content) return;
    content.innerHTML = "";
    if (!this._settings) return;
    if (this._tab === "status") content.append(this._statusView());
    else content.append(this._sectionView(this._sections.find((s) => s.id === this._tab)));
  }

  // ------------------------------------------------------------------------------------------ status

  _statusView() {
    const d = this._status || {};
    const ff = d.feedforward || {};
    const b = d.boiler || {};
    const wd = d.watchdog || { state: "–", checks: {} };
    const problems = Object.entries(wd.checks || {}).filter(([, c]) => c.state !== "ok");

    const card = (title, ...rows) => el("div", { class: "card" }, el("h2", {}, title), ...rows);
    const row = (label, value) => el("div", { class: "row" }, el("span", { class: "label" }, label), el("span", {}, value));
    const badge = (state) => el("span", { class: "badge", style: `background:${STATE_COLORS[state] || "var(--disabled-text-color)"}` }, state);

    const watchdog = card("Watchdog",
      row("State", badge(wd.state)),
      problems.length
        ? el("div", {}, problems.map(([id, c]) => el("div", { class: "problem" }, badge(c.state), " ", c.message)))
        : el("div", { class: "muted" }, "Everything works."),
      d.outputs_enabled ? null : el("div", { class: "note" }, "Dry run: 'Write to devices' is off, nothing is switched."));

    const heat = card("Heat pump",
      row("Mode", `${fmt(ff.mode)} (${fmt(ff.demand)})`),
      row("Water temperature", fmt(ff.water_temp, "°C")),
      row("Heating curve", fmt(ff.curve_source)),
      row("Outside used", `${fmt(ff.outside_temp_used, "°C")} (${fmt(ff.outside_source)})`),
      row("Measured outside", fmt(ff.outside_temp, "°C")),
      row("Forecast", ff.forecast_temp === null || ff.forecast_temp === undefined ? "–" : `${fmt(ff.forecast_temp, "°C")} (bias ${fmt(ff.forecast_bias, "°C")})`),
      row("Average for simple schedule", fmt(ff.outside_temp_avg_used, "°C")),
      row("Expected", `${fmt(ff.expected_power, "W")}, COP ${fmt(ff.expected_cop)}`),
      row("Ventilation boost", `${(d.ventilation || {}).active ? "on" : "off"} (${fmt((d.ventilation || {}).reason)})`),
      ff.available === false ? el("div", { class: "note" }, ff.reason) : null);

    const plan = b.price_plan;
    const boiler = card("Boiler",
      row("Element", `${b.element ? "on" : "off"} (${fmt(b.element_reason)})`),
      row("Doorstroomboiler", `${b.instant ? "on" : "off"} (${fmt(b.instant_reason)})`),
      row("Valve", `${fmt(b.valve_setpoint, "°C")} (${fmt(b.valve_reason)})`),
      row("Legionella run", b.legionella ? "active" : "–"),
      row("Price plan", plan ? `${plan.reason}` : "–"),
      plan && plan.slots && plan.slots.length
        ? el("div", { class: "muted" }, plan.slots.map((s) => `${s[0].slice(11, 16)}–${s[1].slice(11, 16)} €${s[2]}`).join(", "))
        : null,
      b.available === false ? el("div", { class: "note" }, b.reason) : null);

    const inputs = card("Inputs",
      el("table", {},
        el("tr", {}, el("th", {}, "Input"), el("th", {}, "Value"), el("th", {}, "Source")),
        Object.entries(d.inputs || {}).filter(([, r]) => r.status !== "not configured").map(([key, r]) =>
          el("tr", {},
            el("td", {}, this._label(key)),
            el("td", {}, fmt(r.value)),
            el("td", { class: r.status === "master" ? "" : "warn" }, r.source ? `${r.source} (${r.status})` : r.status)))));

    const hist = d.history || {};
    const water = (hist.fit || {}).water_target_temp || {};
    const history = card("History",
      row("Days used", `${fmt(hist.days_used)} (${fmt(hist.first_day)} – ${fmt(hist.last_day)})`),
      row("Water curve", water.slope !== undefined ? `${water.slope} × outside + ${water.intercept}` : "–"),
      row("Average COP", fmt((hist.averages || {}).cop_avg)),
      d.history_error ? el("div", { class: "note" }, d.history_error) : null);

    const actions = card("Recent actions",
      (d.actions || []).length
        ? el("div", {}, (d.actions || []).map((a) => el("div", { class: "muted" }, `${(a.time || "").slice(11, 16)} ${a.action}${a.dry_run ? " (dry run)" : ""}`)))
        : el("div", { class: "muted" }, "None yet."),
      el("div", { class: "buttons" },
        el("button", { onclick: () => this._action("recalculate_history", "Recalculate history") }, "Recalculate history"),
        el("button", { onclick: () => this._action("refresh_forecast", "Refresh forecast") }, "Refresh forecast"),
        el("button", { onclick: () => this._action("plan_prices", "Plan prices") }, "Plan prices"),
        el("button", { onclick: () => this._action("start_legionella", "Start legionella run") }, "Start legionella run")));

    return el("div", { class: "grid" }, watchdog, heat, boiler, inputs, history, actions,
      el("div", { class: "muted footer" }, `Updated ${fmt((d.updated || "").slice(11, 19))}`));
  }

  _label(key) {
    for (const section of this._sections) for (const f of section.fields) if (f.key === key) return f.label;
    return key;
  }

  // ------------------------------------------------------------------------------------------ settings

  _sectionView(section) {
    return el("div", { class: "card wide" },
      el("h2", {}, section.title),
      section.help ? el("p", { class: "muted" }, section.help) : null,
      section.fields.map((f) => this._field(f)));
  }

  _set(key, value) {
    this._draft[key] = value;
    this._message = null;
    this._renderMessage();
  }

  _field(f) {
    const value = this._draft[f.key];
    let input;
    switch (f.type) {
      case "bool":
        input = el("input", { type: "checkbox", checked: !!value, onchange: (e) => this._set(f.key, e.target.checked) });
        break;
      case "number":
        input = el("input", { type: "number", value, min: f.min, max: f.max, step: f.step ?? "any",
          onchange: (e) => this._set(f.key, e.target.value === "" ? f.default : Number(e.target.value)) });
        break;
      case "time":
        input = el("input", { type: "time", value, onchange: (e) => this._set(f.key, e.target.value) });
        break;
      case "select":
      case "weekday": {
        const options = f.type === "weekday" ? Object.entries(WEEKDAYS) : f.options.map((o) => [o, o]);
        input = el("select", { onchange: (e) => this._set(f.key, e.target.value) },
          options.map(([v, label]) => el("option", { value: v, selected: v === value }, label)));
        break;
      }
      case "entity":
        input = this._entityInput(f, value, (v) => this._set(f.key, v));
        break;
      case "entities":
        input = this._entitiesInput(f);
        break;
      case "table":
        input = this._tableInput(f);
        break;
      default:
        input = el("input", { type: "text", value: value ?? "", onchange: (e) => this._set(f.key, e.target.value) });
    }
    return el("div", { class: `field ${f.type}` },
      el("label", {}, f.label, f.unit ? el("span", { class: "unit" }, ` (${f.unit})`) : null),
      el("div", { class: "control" }, input, f.help ? el("div", { class: "help" }, f.help) : null));
  }

  _entityIds(domains) {
    return Object.keys(this._hass.states).filter((id) => !domains || domains.includes(id.split(".")[0])).sort();
  }

  _entityInput(f, value, onchange) {
    const listId = `list-${f.key}`;
    const state = value ? this._hass.states[value] : null;
    return el("span", { class: "entity" },
      el("input", { type: "text", value: value || "", list: listId, placeholder: "entity_id",
        onchange: (e) => { onchange(e.target.value.trim()); this._renderContent(); } }),
      el("datalist", { id: listId }, this._entityIds(f.domains).map((id) => el("option", { value: id }))),
      value ? el("span", { class: state ? "muted" : "warn" }, state ? ` ${state.state}${state.attributes.unit_of_measurement ? " " + state.attributes.unit_of_measurement : ""}` : " does not exist") : null);
  }

  _entitiesInput(f) {
    const list = [...(this._draft[f.key] || [])];
    const update = (newList) => { this._set(f.key, newList); this._renderContent(); };
    return el("div", { class: "entities" },
      list.map((id, i) => el("div", { class: "entity-row" },
        el("span", { class: "role" }, i === 0 ? "master" : `backup ${i}`),
        this._entityInput({ ...f, key: `${f.key}-${i}` }, id, (v) => { const n = [...list]; n[i] = v; update(n.filter(Boolean)); }),
        i > 0 ? el("button", { title: "Make master", onclick: () => { const n = [...list]; n.splice(i, 1); n.unshift(id); update(n); } }, "▲ master") : null,
        el("button", { title: "Remove", onclick: () => { const n = [...list]; n.splice(i, 1); update(n); } }, "✕"))),
      el("button", { onclick: () => update([...list, ""]) }, list.length ? "+ backup sensor" : "+ sensor"));
  }

  _tableInput(f) {
    const rows = structuredClone(this._draft[f.key] || []);
    const update = (newRows) => { this._set(f.key, newRows); this._renderContent(); };
    const cell = (i, key) => el("input", { type: "number", step: "any", value: rows[i][key],
      onchange: (e) => { rows[i][key] = Number(e.target.value); this._set(f.key, structuredClone(rows)); } });
    return el("div", {},
      el("table", { class: "points" },
        el("tr", {}, el("th", {}, "Outside °C"), el("th", {}, "Water °C"), el("th", {})),
        rows.map((r, i) => el("tr", {}, el("td", {}, cell(i, "outside")), el("td", {}, cell(i, "water")),
          el("td", {}, el("button", { onclick: () => { rows.splice(i, 1); update(rows); } }, "✕"))))),
      el("button", { onclick: () => update([...rows, { outside: (rows.at(-1)?.outside ?? 0) + 5, water: rows.at(-1)?.water ?? 30 }]) }, "+ point"));
  }
}

const STYLE = `
  :host { display: block; background: var(--primary-background-color); color: var(--primary-text-color); min-height: 100vh;
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .header { background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff);
    padding: 12px 16px 0; position: sticky; top: 0; z-index: 2; }
  .title { font-size: 20px; margin-bottom: 8px; }
  .tabs { display: flex; gap: 4px; overflow-x: auto; }
  .tab { background: none; border: none; color: inherit; opacity: .75; padding: 8px 12px; cursor: pointer; font-size: 14px;
    border-bottom: 2px solid transparent; white-space: nowrap; }
  .tab.active { opacity: 1; border-bottom-color: currentColor; }
  .content { padding: 16px; max-width: 1200px; margin: 0 auto; box-sizing: border-box; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(340px, 100%), 1fr)); gap: 16px; }
  .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); padding: 16px;
    box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); min-width: 0; overflow-wrap: anywhere; }
  .card.wide { max-width: 900px; margin: 0 auto; }
  h2 { font-size: 16px; margin: 0 0 12px; font-weight: 500; }
  .row { display: flex; justify-content: space-between; gap: 12px; padding: 3px 0; }
  .row > span:last-child { text-align: right; }
  .label, .muted, .help, .unit { color: var(--secondary-text-color); }
  .help { font-size: 12px; margin-top: 4px; }
  .note { margin-top: 8px; padding: 8px; border-radius: 6px; background: var(--secondary-background-color); font-size: 13px; }
  .warn { color: var(--warning-color, #ffa600); }
  .badge { color: #fff; border-radius: 10px; padding: 1px 8px; font-size: 12px; }
  .problem { padding: 4px 0; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--divider-color); vertical-align: middle; }
  table.points { width: auto; }
  table.points input { width: 80px; }
  .field { display: grid; grid-template-columns: minmax(180px, 1fr) 2fr; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--divider-color); }
  .field label { padding-top: 6px; }
  input, select, button { font: inherit; color: var(--primary-text-color); background: var(--secondary-background-color);
    border: 1px solid var(--divider-color); border-radius: 6px; padding: 6px 8px; }
  input[type=checkbox] { width: 18px; height: 18px; }
  input[type=text] { width: min(420px, 100%); box-sizing: border-box; }
  button { cursor: pointer; }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .entity-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }
  .role { width: 70px; font-size: 12px; color: var(--secondary-text-color); }
  .buttons { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .message-area { position: sticky; top: 88px; z-index: 1; padding: 0 16px; }
  .message { margin: 8px auto 0; max-width: 1200px; padding: 8px 12px; border-radius: 6px; background: var(--secondary-background-color); }
  .message.error { background: var(--error-color, #db4437); color: #fff; }
  .savebar { margin: 8px auto 0; max-width: 1200px; display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 6px;
    background: var(--card-background-color); border: 1px solid var(--primary-color); }
  .footer { grid-column: 1 / -1; font-size: 12px; }
  @media (max-width: 600px) {
    .field { grid-template-columns: 1fr; }
    .content { padding: 12px; }
    .entity-row .entity { flex: 1 1 100%; }
  }
`;

customElements.define("home-heating-panel", HomeHeatingPanel);
