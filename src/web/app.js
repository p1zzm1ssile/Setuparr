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

const SEVERITY = {
  problem: { label: "Problem", badge: "bad", tile: "bad", many: "Problems" },
  warning: { label: "Warning", badge: "warn", tile: "warn", many: "Warnings" },
  tip: { label: "Tip", badge: "info", tile: "info", many: "Tips" },
};
const AREAS = { all: "Everything", unraid: "Unraid", downloads: "Downloads", library: "Library", arr: "Sonarr & Radarr", tags: "Tags", anime: "Anime" };
let dashArea = "all";
let showDismissed = false;

function findingCard(f, reload) {
  const result = el("span", { class: "result", role: "status" });
  const actions = [];
  if (f.fix && !f.dismissed) {
    const button = el("button", { type: "button", class: "primary", text: f.fix.label });
    button.addEventListener("click", async () => {
      if (!confirm(f.fix.confirm)) return;
      button.disabled = true;
      try {
        flash(result, true, (await post("/api/fix", { id: f.id })).message);
        setTimeout(reload, 1500);
      } catch (e) {
        flash(result, false, e.message);
        button.disabled = false;
      }
    });
    actions.push(button);
  }
  const dismiss = el("button", { type: "button", text: f.dismissed ? "Show again" : "Dismiss" });
  dismiss.addEventListener("click", async () => {
    await post("/api/dismiss", { id: f.id, dismissed: !f.dismissed });
    reload();
  });
  actions.push(dismiss);
  if (f.link) actions.push(el("a", { href: f.link, target: "_blank", rel: "noopener", text: "TRaSH guide" }));

  return el("div", { class: "panel" },
    el("div", { class: "row-wrap" },
      el("span", { class: `badge ${SEVERITY[f.severity].badge}`, text: SEVERITY[f.severity].label }),
      el("span", { class: "badge", text: f.app })),
    el("h2", { text: f.title }),
    el("p", { class: "muted", text: f.detail }),
    f.unraid_sent ? el("p", { class: "muted small" }, `From the Unraid settings sent ${ago(f.unraid_sent)}. Changed a container since? `,
      el("a", { href: "#/unraid", text: "Send them again" }), " and this updates.") : null,
    f.items.length ? el("details", {},
      el("summary", { text: f.item_count > f.items.length ? `Show ${f.items.length} of ${f.item_count}` : `Show ${f.item_count}` }),
      el("ul", {}, f.items.map((i) => el("li", { text: i })))) : null,
    f.steps.length ? el("div", {}, el("strong", { text: "How to fix it" }),
      el("ol", {}, f.steps.map((s) => el("li", { text: s })))) : null,
    el("div", { class: "card-actions" }, actions, result));
}

async function showDashboard() {
  const d = await api("/api/dashboard");
  const r = d.results;
  const reload = () => showDashboard();
  const visible = r.findings.filter((f) => (dashArea === "all" || f.area === dashArea) && f.dismissed === showDismissed);
  const dismissedCount = r.findings.filter((f) => f.dismissed).length;
  const active = r.findings.filter((f) => !f.dismissed);
  const connected = d.connections.filter((c) => c.enabled);

  const run = el("button", { type: "button", class: "primary", text: r.running ? "Checking…" : "Run checks", disabled: r.running });
  run.addEventListener("click", async () => {
    run.disabled = true;
    run.textContent = "Checking…";
    try { await post("/api/run"); } catch (e) { alert(e.message); }
    const wait = setInterval(async () => {
      const s = await api("/api/dashboard");
      if (!s.results.running) { clearInterval(wait); reload(); }
    }, 1500);
  });

  const setupNeeded = [];
  if (!connected.length) {
    setupNeeded.push(el("li", {}, "Connect Sonarr, Radarr, Prowlarr, Seerr or your download clients under ",
      el("a", { href: "#/settings/sonarr", text: "Settings" }), " to check them."));
  }
  if (r.checked && !r.checked.unraid) {
    setupNeeded.push(el("li", {}, "Add your Unraid Docker templates and share settings on the ",
      el("a", { href: "#/unraid", text: "Unraid page" }), " to check folder mappings and shares."));
  }

  // replaceChildren doesn't skip empty parts or flatten lists the way el() does
  view.replaceChildren(...[
    el("div", { class: "toolbar" },
      el("h1", { text: "Dashboard" }),
      el("span", { class: "spacer" }),
      el("span", { class: "muted small", text: r.ran_at ? `Last checked ${ago(r.ran_at)}` : "Not checked yet" }),
      run),
    setupNeeded.length ? el("div", { class: "notice" }, el("strong", { text: "To check everything" }), el("ul", {}, setupNeeded)) : null,
    r.errors.length ? el("div", { class: "notice bad" }, el("strong", { text: "Some checks couldn't run" }),
      el("ul", {}, r.errors.map((e) => el("li", { text: e })))) : null,
    el("div", { class: "tiles" },
      Object.entries(SEVERITY).map(([key, s]) => el("div", { class: `tile ${s.tile}` },
        el("span", { class: "num", text: String(active.filter((f) => f.severity === key).length) }),
        el("span", { class: "label", text: s.many })))),
    el("div", { class: "toolbar" },
      el("div", { class: "segmented", role: "group", "aria-label": "Show" },
        Object.entries(AREAS).map(([key, label]) => el("button", {
          type: "button", class: key === dashArea ? "active" : "",
          text: `${label} (${active.filter((f) => key === "all" || f.area === key).length})`,
          onclick: () => { dashArea = key; reload(); },
        }))),
      dismissedCount ? el("label", { class: "chip" },
        el("input", { type: "checkbox", checked: showDismissed, onchange: (e) => { showDismissed = e.target.checked; reload(); } }),
        `Show dismissed (${dismissedCount})`) : null),
    visible.length ? visible.map((f) => findingCard(f, reload))
      : el("div", { class: "panel empty", text: !r.ran_at ? "Press Run checks to start."
        : showDismissed ? "Nothing dismissed here." : "Nothing found here. All good!" }),
    d.fixes.length ? el("div", { class: "panel" },
      el("h2", { text: "Recent fixes" }),
      el("table", {}, el("tbody", {}, d.fixes.map((fix) => {
        const undo = el("button", { type: "button", text: fix.undone ? "Undone" : "Undo", disabled: fix.undone });
        undo.addEventListener("click", async () => {
          if (!confirm(`Undo "${fix.message}"?`)) return;
          try { alert((await post("/api/undo", { id: fix.id })).message); reload(); } catch (e) { alert(e.message); }
        });
        return el("tr", {}, el("td", { class: "muted small", text: when(fix.at) }), el("td", { text: fix.message }), el("td", {}, undo));
      })))) : null,
    el("div", { class: "panel" },
      el("h2", { text: "Coming next" }),
      d.planned.map((group) => el("div", {}, el("strong", { text: group.title }),
        el("ul", {}, group.items.map((item) => el("li", { text: item })))))),
    el("p", { class: "muted small" }, "Something you'd like it to check? ",
      el("a", { href: "#/contact", text: "Suggest it" }), "."),
  ].flat().filter(Boolean));
  if (r.running) every(2000, async () => { if (!(await api("/api/dashboard")).results.running) reload(); });
}

// ---------- Unraid ----------

const ROLE_LABEL = { arr: "Library app", client: "Download client", server: "Media server" };

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error(`${file.name} couldn't be read`));
    reader.readAsDataURL(file);
  });
}

// Only the files Setuparr reads are sent, whatever folder is chosen
const WANTED = /(\.xml|\.cfg|\.plg|compose\.ya?ml|appdata\.backup[^/]*\/config\.json|\.zip|\.tar|\.tar\.gz|\.tgz)$/i;

async function showUnraid(message) {
  const d = await api("/api/unraid");
  const s = d.summary;
  const result = el("div", { class: "result", role: "status" });
  if (message) flash(result, true, message);
  const files = el("input", { type: "file", multiple: true, accept: ".zip,.tar,.gz,.tgz,.xml,.cfg,.plg,.yml,.yaml" });
  const folder = el("input", { type: "file", webkitdirectory: true, multiple: true });

  async function upload(list) {
    const chosen = [...list].filter((f) => WANTED.test(f.webkitRelativePath || f.name));
    if (!chosen.length) { flash(result, false, "None of those are Docker templates, share settings or compose files"); return; }
    result.className = "result muted";
    result.textContent = `Reading ${chosen.length} file(s)…`;
    try {
      const payload = await Promise.all(chosen.map(async (f) => ({ name: f.webkitRelativePath || f.name, data: await readAsBase64(f) })));
      const r = await post("/api/unraid/upload", { files: payload });
      showUnraid(`Imported ${r.summary.containers.length} containers and ${r.summary.shares.length} shares. The checks are running.`);
    } catch (e) {
      flash(result, false, e.message);
    }
  }
  files.addEventListener("change", () => upload(files.files));
  folder.addEventListener("change", () => upload(folder.files));

  // Option 1: a one-time link, so the files go straight from Unraid to Setuparr and nothing is written to disk
  const commandBox = el("input", { type: "text", readonly: true, "aria-label": "Command" });
  const copy = el("button", { type: "button", text: "Copy", disabled: true });
  const commandRow = el("div", { class: "copy-row hidden" }, commandBox, copy);
  const getCommand = el("button", { type: "button", class: "primary", text: "Get the command" });
  const waiting = el("p", { class: "muted small", role: "status" });
  getCommand.addEventListener("click", async () => {
    try {
      const t = await post("/api/unraid/token");
      // Also sends the Unraid version and lspci -n's PCI IDs (class:vendor:device, no names), for the version and
      // hardware checks, and the disks' state and the containers' start order from outside /boot/config
      commandBox.value = "cd /boot/config && tar -cz plugins/dockerMan/templates-user shares " +
        "$(ls -d docker.cfg share.cfg network.cfg ident.cfg plugins/dynamix/dynamix.cfg plugins/*.plg " +
        "plugins/appdata.backup*/config.json plugins/compose.manager/projects 2>/dev/null) " +
        "$(test -f /var/local/emhttp/disks.ini && echo -C /var/local/emhttp disks.ini) " +
        "$(test -f /var/lib/docker/unraid-autostart && echo -C /var/lib/docker unraid-autostart) | " +
        `curl -sS --data-binary @- -H "X-Unraid-Version: $(cat /etc/unraid-version)" ` +
        `-H "X-Hardware: $(lspci -n | awk '{print $2 $3}' | tr '\\n' ' ')" ` +
        `'${location.origin}/api/unraid/push?token=${t.token}'`;
      commandRow.classList.remove("hidden");
      copy.disabled = false;
      getCommand.textContent = "Get a new command";
      waiting.textContent = `Works once, for ${t.minutes} minutes. Waiting for Unraid to send the files…`;
      const before = s ? s.imported_at : null;
      every(3000, async () => {
        const now = await api("/api/unraid");
        if (now.summary && now.summary.imported_at !== before) {
          showUnraid(`Read ${now.summary.containers.length} containers and ${now.summary.shares.length} shares. The checks are running.`);
        }
      });
    } catch (e) { flash(result, false, e.message); }
  });
  copy.addEventListener("click", async () => {
    commandBox.select();
    try { await navigator.clipboard.writeText(commandBox.value); } catch { document.execCommand("copy"); }
    copy.textContent = "Copied";
    setTimeout(() => (copy.textContent = "Copy"), 2000);
  });

  const content = [
    el("h1", { text: "Unraid" }),
    el("p", { class: "muted", text: "Setuparr checks your folder mappings and shares from Unraid's own settings files. It never looks around your server: it reads only what you give it here." }),
    d.leftover ? el("div", { class: "notice warn" },
      el("strong", { text: `${d.leftover} is still in Setuparr's config folder. ` }),
      "It was made by an earlier version's instructions and can hold passwords. Read it and delete it, or just delete it.",
      el("div", { class: "card-actions" },
        el("button", { type: "button", class: "primary", text: "Read it, then delete it", onclick: async () => {
          try {
            const r = await post("/api/unraid/import-folder");
            showUnraid(`Read ${r.summary.containers.length} containers and ${r.summary.shares.length} shares, and deleted the file.`);
          } catch (e) { flash(result, false, e.message); }
        } }),
        el("button", { type: "button", class: "danger", text: "Delete it", onclick: async () => {
          await post("/api/unraid/delete-leftover");
          showUnraid("Deleted.");
        } }))) : null,
    el("div", { class: "notice" },
      el("strong", { text: "Passwords, API keys and personal details are removed. " }),
      "Setuparr reads the files in memory and keeps only what the checks need: each container's name, image, folder mappings and host ports, its PUID, PGID and UMASK, whether it's given a GPU or its own IP address, which container's network it uses, and whether its time zone matches Unraid's; ",
      "the few Unraid settings it checks; each disk's slot, state, size, free space and error count; the names of your plugins, where they came from and the Unraid versions they're made for; Appdata Backup's schedule, destination and skipped containers; your Unraid version; and the PCI IDs of your devices, which say what kind of GPU and network card you have. ",
      "Everything else (passwords, API keys, VPN details, email settings, IP addresses, disk serial numbers, the server's name, other settings, network names and time zones) is dropped as the files are read and never saved. ",
      "Folder and container names are kept because the checks need them, so if one is named after a person, that name is kept too."),
    el("div", { class: "panel" },
      el("h2", { text: "Option 1: from Unraid's terminal (easiest)" }),
      el("ol", {},
        el("li", {}, "Press Get the command, then Copy."),
        el("li", {}, "In Unraid, click the ", el("strong", { text: ">_" }), " terminal icon at the top right, paste it and press Enter."),
        el("li", {}, "The files go straight from Unraid to Setuparr: nothing is written to disk on the way, and this page updates by itself.")),
      el("div", { class: "card-actions" }, getCommand), commandRow, waiting),
    el("div", { class: "panel" },
      el("h2", { text: "Option 2: choose the files" }),
      el("p", { class: "muted small" }, "Turn on the flash share (Shares > flash > SMB export, read-only is fine), open ",
        el("code", { text: "\\\\tower\\flash\\config" }), " from your computer, and choose the ",
        el("code", { text: "plugins\\dockerMan\\templates-user" }), " and ", el("code", { text: "shares" }),
        " folders, or a zip of them. Add ", el("code", { text: "docker.cfg" }), ", ", el("code", { text: "share.cfg" }), ", ",
        el("code", { text: "network.cfg" }), ", ", el("code", { text: "ident.cfg" }), ", ",
        el("code", { text: "plugins\\dynamix\\dynamix.cfg" }), ", ", el("code", { text: "plugins\\appdata.backup" }),
        " and the ", el("code", { text: ".plg" }), " files in ", el("code", { text: "plugins" }),
        " for the Docker, share, time, alert, backup and plugin checks. Only those files are sent; everything else is skipped. ",
        "The disk, start order, GPU, network card and Unraid version checks need the terminal command."),
      el("div", { class: "row-wrap" },
        el("label", { class: "button" }, "Choose files…", files),
        el("label", { class: "button" }, "Choose a folder…", folder))),
    result,
  ];

  if (s) {
    const ignore = {};
    content.push(el("div", { class: "panel" },
      el("h2", { text: "What was imported" }),
      el("p", { class: "muted small" }, `Imported ${when(s.imported_at)}. `,
        "Unraid keeps templates for containers you've removed, so untick any you don't use, or they'll be checked too."),
      el("table", {},
        el("thead", {}, el("tr", {}, el("th", { text: "Check" }), el("th", { text: "Container" }), el("th", { text: "Type" }), el("th", { text: "Folder mappings" }))),
        el("tbody", {}, s.containers.map((c) => el("tr", {},
          el("td", {}, ignore[c.name] = el("input", { type: "checkbox", checked: !c.ignored, "aria-label": `Check ${c.name}` })),
          el("td", {}, el("strong", { text: c.name }), el("div", { class: "muted small", text: c.image })),
          el("td", { text: c.app ? `${c.app} (${ROLE_LABEL[c.role]})` : "Other" }),
          el("td", { class: "small" }, c.mounts.map((m) => el("div", { text: m }))))))),
      el("p", { class: "muted small", text: s.shares.length ? `Shares: ${s.shares.join(", ")}` : "No share settings were included." }),
      el("p", { class: "muted small", text: [
        s.settings && s.settings.length ? `Unraid settings: ${s.settings.join(", ")}.` : "No Docker, share or network settings were included.",
        s.plugins && s.plugins.length ? `${s.plugins.length} plugins.` : "No plugins were included.",
        s.hardware != null ? `${s.hardware} devices.` : "No hardware list (it's only sent by the terminal command).",
        s.disks != null ? `${s.disks} disks.` : "",
        s.autostart != null ? `${s.autostart} containers start by themselves.` : "",
        s.backup ? "Appdata Backup's settings." : "",
        s.unraid_version ? `Unraid ${s.unraid_version}.` : "",
      ].filter(Boolean).join(" ") }),
      el("div", { class: "card-actions" },
        el("button", { type: "button", class: "primary", text: "Save choices", onclick: async () => {
          await post("/api/unraid/ignore", { names: Object.entries(ignore).filter(([, box]) => !box.checked).map(([n]) => n) });
          showUnraid("Saved. The checks are running.");
        } }),
        el("button", { type: "button", class: "danger", text: "Forget imported settings", onclick: async () => {
          if (!confirm("Forget the imported Unraid settings? The Unraid checks stop until you import them again.")) return;
          await post("/api/unraid/forget");
          showUnraid("Forgotten.");
        } }))));
  }
  view.replaceChildren(...content.filter(Boolean));
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
      min: field.min, max: field.max, placeholder: field.placeholder, spellcheck: "false" });
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
    else if (name === "unraid") await showUnraid();
    else await showDashboard();
  } catch (e) {
    view.replaceChildren(el("div", { class: "notice bad", text: e.message }));
  }
}

window.addEventListener("hashchange", route);
route();
refreshHealth();
setInterval(refreshHealth, 15000);
