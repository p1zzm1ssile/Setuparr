"use strict";

// ---------- Helpers ----------

const view = document.getElementById("view");
const APP = document.title;
const REPO = `https://github.com/p1zzm1ssile/${APP}`;
const EMAIL = "p1zzm1ssilerepo@gmail.com";

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", [`X-${APP}`]: "1", ...(options.headers || {}) },
  });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Please log in");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.data = data;
    throw error;
  }
  return data;
}

const post = (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });

// Builds elements. Text always goes in as text, never as HTML.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") node.style.cssText = value; // CSP allows styles set from script, not style attributes
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function ago(timestamp) {
  const seconds = Math.round(Date.now() / 1000 - timestamp);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400 * 2) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

const when = (timestamp) => new Date(timestamp * 1000).toLocaleString();


let refreshTimer = null;
function every(ms, fn) {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(fn, ms);
}

function flash(node, ok, message) {
  node.className = `result ${ok ? "ok" : "bad"}`;
  node.textContent = (ok ? "✓ " : "✗ ") + message;
}

// ---------- Header ----------

async function refreshHealth() {
  const pill = document.getElementById("health");
  const text = document.getElementById("health-text");
  try {
    const h = await api("/api/status");
    pill.className = "pill " + (h.healthy ? "ok" : "bad");
    text.textContent = h.healthy ? "Healthy" : "Needs attention";
    pill.title = [...h.problems, `Version ${h.version}`].join("\n");
    return h;
  } catch {
    pill.className = "pill bad";
    text.textContent = "Not responding";
    return null;
  }
}

document.getElementById("logout").addEventListener("click", async () => {
  await post("/api/logout").catch(() => {});
  window.location.href = "/login";
});

api("/api/session").then((s) => {
  document.getElementById("whoami").textContent = s.user || "";
  if (s.user === "(local network)") document.getElementById("logout").textContent = "Log in";
});

// ---------- Dashboard ----------

async function showDashboard() {
  const d = await api("/api/dashboard");
  const connected = d.connections.filter((c) => c.enabled);
  view.replaceChildren(
    el("h1", { text: "Dashboard" }),
    el("p", { class: "muted", text: d.tagline }),
    el("div", { class: "panel" },
      el("h2", { text: "Connections" }),
      connected.length
        ? el("p", {}, "Connected to ", connected.map((c) => c.label).join(", "), ". ",
          el("a", { href: "#/settings/general", text: "Change in Settings" }), ".")
        : el("p", { class: "muted" }, "Nothing is connected yet. Add your apps under ",
          el("a", { href: `#/settings/${d.connections[0].key}`, text: "Settings" }),
          " and press Test connection on each.")),
    ...d.planned.map((group) => el("div", { class: "panel" },
      el("h2", { text: group.title }),
      el("ul", {}, group.items.map((item) => el("li", { text: item }))))),
    el("p", { class: "muted small" }, "Something you'd like it to check? ",
      el("a", { href: "#/contact", text: "Suggest it" }), "."));
}

// ---------- Logs ----------

async function showLogs() {
  let kind = "all";
  const box = el("div", { class: "log", role: "log", "aria-live": "polite" });
  const search = el("input", { type: "search", placeholder: "Search logs", "aria-label": "Search logs" });
  const auto = el("input", { type: "checkbox", checked: true });
  const buttons = {};
  const segmented = el("div", { class: "segmented", role: "group", "aria-label": "Show" },
    ["all", "issues", "actions"].map((k) => (buttons[k] = el("button", {
      type: "button", class: k === kind ? "active" : "",
      text: { all: "All", issues: "Issues", actions: "Actions" }[k],
      onclick: () => { kind = k; Object.values(buttons).forEach((b) => b.classList.remove("active")); buttons[k].classList.add("active"); load(true); },
    }))));

  view.replaceChildren(
    el("h1", { text: "Logs" }),
    el("p", { class: "muted small", text: `Issues shows errors and warnings. Actions shows every change ${APP} made.` }),
    el("div", { class: "toolbar" }, segmented,
      el("div", { style: "flex:1;min-width:180px" }, search),
      el("label", { class: "chip" }, auto, "Auto refresh"),
      el("a", { class: "button", href: "/api/logs/download", text: "Download all logs" })),
    box);

  let lines = [];
  function render(scroll) {
    const term = search.value.trim().toLowerCase();
    const filtered = lines.filter((l) => !term || l.message.toLowerCase().includes(term));
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.replaceChildren(...(filtered.length ? filtered.map((l) => el("div", { class: "line" },
      el("span", { class: "time", text: l.time }), el("span", { class: `lvl lvl-${l.level}`, text: l.level }), l.message))
      : [el("div", { class: "empty", text: kind === "issues" ? "No issues. All good!" : "Nothing to show yet." })]));
    if (scroll || atBottom) box.scrollTop = box.scrollHeight;
  }

  async function load(scroll) {
    try {
      lines = (await api(`/api/logs?filter=${kind}`)).lines;
      render(scroll);
    } catch (e) {
      box.replaceChildren(el("div", { class: "error-text", text: e.message }));
    }
  }

  search.addEventListener("input", () => render(false));
  await load(true);
  every(5000, () => { if (auto.checked) load(false); });
}

// ---------- Settings ----------

let settings = null;

function fieldInput(field, value, onChange) {
  const id = `f-${field.key}`;
  const help = field.help ? el("div", { class: "help", text: field.help }) : null;
  const error = el("div", { class: "error-text", id: `${id}-error` });

  if (field.type === "bool") {
    const input = el("input", { type: "checkbox", id, checked: !!value, onchange: onChange });
    return { node: el("label", { class: "switch", for: id }, input, el("span", { class: "track" }),
      el("span", { class: "text" }, field.label, help, error)), get: () => input.checked, input };
  }

  let input;
  if (field.type === "select") {
    input = el("select", { id, onchange: onChange }, field.options.map((o) => el("option", { value: o, text: o, selected: o === value })));
  } else if (field.type === "secret") {
    input = el("input", { type: "password", id, autocomplete: "new-password", oninput: onChange,
      placeholder: value && value.set ? "•••••••• saved (type to replace)" : "Not set" });
  } else {
    input = el("input", { type: field.type === "int" ? "number" : "text", id, value: value ?? "", oninput: onChange,
      min: field.min, max: field.max, spellcheck: "false" });
  }
  return {
    node: el("label", { class: "field", for: id }, el("span", { class: "label", text: field.label }), input, help, error),
    get: () => field.type === "secret" ? (input.value === "" ? null : input.value) : input.value,
    input,
  };
}

async function showSettings(tab, savedMessage) {
  if (!settings) settings = await api("/api/settings");
  if (!settings.tabs[tab]) tab = "general";

  const fields = settings.fields.filter((f) => f.tab === tab);
  const getters = {};
  const status = el("span", { class: "muted small", role: "status" });
  let dirty = false;
  const markDirty = () => { dirty = true; status.textContent = "Unsaved changes"; status.style.color = "var(--warn)"; };

  const formValues = () => Object.fromEntries(Object.entries(getters).map(([k, g]) => [k, g()]));

  function testButton(target) {
    const result = el("span", { class: "result" });
    const button = el("button", { type: "button", text: "Test connection" });
    button.addEventListener("click", async () => {
      button.disabled = true;
      result.className = "result muted";
      result.textContent = "Testing…";
      try {
        const r = await post("/api/test", { target, values: formValues() });
        flash(result, r.ok, r.message);
      } catch (e) {
        flash(result, false, e.message);
      } finally {
        button.disabled = false;
      }
    });
    return el("div", { class: "card-actions" }, button, result);
  }

  const sections = [];
  for (const field of fields) {
    let section = sections.find((s) => s.title === field.section);
    if (!section) sections.push(section = { title: field.section, fields: [] });
    section.fields.push(field);
  }

  const content = sections.map((section) => {
    const rendered = section.fields.map((f) => {
      const input = fieldInput(f, settings.values[f.key], markDirty);
      getters[f.key] = input.get;
      return input;
    });
    return el("div", { class: "panel" },
      el("h2", { text: section.title }),
      el("div", { class: "grid-2" }, rendered.map((r) => r.node)),
      tab !== "general" && section.title === "Connection" ? testButton(tab) : null);
  });

  if (tab !== "general") content.unshift(el("p", { class: "muted", text: `Optional. ${APP} only reads from ${settings.tabs[tab]} until you choose to change something.` }));
  if (tab === "general") content.push(...accountPanels());

  const saveButton = el("button", { class: "primary", type: "button", text: `Save ${settings.tabs[tab]}` });
  saveButton.addEventListener("click", async () => {
    saveButton.disabled = true;
    view.querySelectorAll(".error-text[id$='-error']").forEach((e) => (e.textContent = ""));
    try {
      const r = await api("/api/settings", { method: "PUT", body: JSON.stringify({ values: formValues() }) });
      settings.values = r.values;
      dirty = false;
      status.style.color = "var(--ok)";
      status.textContent = "✓ Saved. Changes are in effect now.";
      refreshHealth();
    } catch (e) {
      status.style.color = "var(--bad)";
      const errors = (e.data && e.data.errors) || {};
      for (const [key, message] of Object.entries(errors)) {
        const box = document.getElementById(`f-${key}-error`);
        if (box) box.textContent = message;
      }
      status.textContent = Object.keys(errors).length ? "Please fix the highlighted settings." : e.message;
    } finally {
      saveButton.disabled = false;
    }
  });

  view.replaceChildren(
    el("h1", { text: "Settings" }),
    el("div", { class: "tabs", role: "tablist" }, Object.entries(settings.tabs).map(([key, label]) =>
      el("button", { type: "button", role: "tab", class: key === tab ? "active" : "", "aria-selected": String(key === tab), text: label,
        onclick: () => {
          if (dirty && !confirm("You have unsaved changes on this tab. Leave without saving?")) return;
          location.hash = `#/settings/${key}`;
        } }))),
    ...content,
    el("div", { class: "savebar" }, saveButton, status));

  if (savedMessage) {
    status.style.color = "var(--ok)";
    status.textContent = savedMessage;
  }
  window.onbeforeunload = () => (dirty ? true : undefined);
}

function accountPanels() {
  const current = el("input", { type: "password", autocomplete: "current-password" });
  const user = el("input", { type: "text", value: settings.username, autocomplete: "username" });
  const password = el("input", { type: "password", autocomplete: "new-password" });
  const confirmBox = el("input", { type: "password", autocomplete: "new-password" });
  const rules = el("ul", { class: "rules" });
  const result = el("div", { class: "result", role: "status" });
  const RULES = [
    ["At least 8 characters", (p) => p.length >= 8],
    ["An uppercase letter", (p) => /[A-Z]/.test(p)],
    ["A lowercase letter", (p) => /[a-z]/.test(p)],
    ["A number", (p) => /[0-9]/.test(p)],
    ["A special character, like ! ? # or @", (p) => /[^A-Za-z0-9]/.test(p)],
  ];
  const renderRules = () => rules.replaceChildren(...RULES.map(([t, c]) => el("li", { class: c(password.value) ? "met" : "", text: t })));
  password.addEventListener("input", renderRules);
  renderRules();

  const change = el("button", { type: "button", text: "Change password" });
  change.addEventListener("click", async () => {
    result.className = "result bad";
    const missing = RULES.filter(([, c]) => !c(password.value));
    if (missing.length) { result.textContent = "The new password still needs: " + missing.map(([t]) => t.toLowerCase()).join(", "); return; }
    if (password.value !== confirmBox.value) { result.textContent = "The new passwords don't match"; return; }
    change.disabled = true;
    try {
      await post("/api/password", { current: current.value, username: user.value.trim(), password: password.value });
      result.className = "result ok";
      result.textContent = "✓ Password changed. Other devices have been logged out.";
      settings.username = user.value.trim();
      current.value = password.value = confirmBox.value = "";
      renderRules();
    } catch (e) {
      result.textContent = e.message;
    } finally {
      change.disabled = false;
    }
  });

  return [
    el("div", { class: "panel" },
      el("h2", { text: "Account" }),
      el("div", { class: "notice warn small" },
        el("strong", { text: `Keep ${APP} on your home network. ` }),
        "We don't recommend putting it behind a reverse proxy (Nginx Proxy Manager, SWAG, Traefik, Cloudflare Tunnel and the like) for access from outside. Use a VPN such as WireGuard or Tailscale instead. The local network login bypass above never applies to requests that come through a proxy."),
      el("div", { class: "grid-2" },
        el("label", { class: "field" }, el("span", { class: "label", text: "Username" }), user),
        el("label", { class: "field" }, el("span", { class: "label", text: "Current password" }), current),
        el("label", { class: "field" }, el("span", { class: "label", text: "New password" }), password, rules),
        el("label", { class: "field" }, el("span", { class: "label", text: "Confirm new password" }), confirmBox)),
      el("div", { class: "card-actions" }, change, result)),
    el("div", { class: "panel" },
      el("h2", { text: "Log files" }),
      el("p", { class: "muted small", text: `Everything ${APP} has logged, including older rotated files, as one zip. Handy for reporting a problem.` }),
      el("a", { class: "button", href: "/api/logs/download", text: "Download all logs" })),
  ];
}

// ---------- Contact ----------

function showContact() {
  view.replaceChildren(
    el("h1", { text: "Contact" }),
    el("div", { class: "panel" },
      el("h2", { text: "Report a problem or suggest an idea" }),
      el("p", { class: "muted", text: "Bugs and ideas go on GitHub, where you can follow the fix and others with the same problem can find it. Saying which versions of Unraid and your apps you run, and what you expected to happen, helps a lot. Logs are under Settings > General > Log files." }),
      el("div", { class: "row-wrap" },
        el("a", { class: "button primary", href: `${REPO}/issues/new?title=%5B${APP}%5D%20`, target: "_blank", rel: "noopener", text: "Report an issue" }),
        el("a", { class: "button", href: `${REPO}/issues`, target: "_blank", rel: "noopener", text: "See open issues" }))),
    el("div", { class: "panel" },
      el("h2", { text: "Contact me" }),
      el("p", { class: "muted" }, "For anything that doesn't belong on a public page, email ",
        el("a", { href: `mailto:${EMAIL}?subject=${APP}`, text: EMAIL }), ".")));
}

// ---------- Router ----------

async function route() {
  window.onbeforeunload = null;
  clearInterval(refreshTimer);
  const [, page = "", sub = ""] = (location.hash || "#/").split("/");
  const name = page || "dashboard";
  document.querySelectorAll("nav.main a").forEach((a) => a.classList.toggle("active", a.dataset.page === name));
  try {
    if (name === "logs") await showLogs();
    else if (name === "settings") await showSettings(sub || "general");
    else if (name === "contact") showContact();
    else await showDashboard();
  } catch (e) {
    view.replaceChildren(el("div", { class: "notice bad", text: e.message }));
  }
}

window.addEventListener("hashchange", route);
route();
refreshHealth();
setInterval(refreshHealth, 15000);
