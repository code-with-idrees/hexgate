// Hexgate dashboard. No framework, no build step. Everything is same-origin.

const state = {
  view: "overview",
  token: localStorage.getItem("hexgate.token") || "",
  config: null,
  providers: [],
  overview: null,
  requests: [],
  models: [],
  plan: null,
  pollTimer: null,
};

const VIEW_META = {
  overview: ["Overview", "A live view of every request that crosses the gateway."],
  providers: ["Providers", "Bring your own keys. Nothing leaves this machine except the upstream call."],
  routing: ["Routing", "Decide which model a tier resolves to, and what happens when it fails."],
  activity: ["Activity", "Every routed request, with latency, tokens and where it actually ran."],
  playground: ["Playground", "Send a prompt through the real routing path without touching a coding agent."],
  settings: ["Settings", "Server, security, token optimization and diagnostics."],
};

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const fmtInt = (n) => Number(n || 0).toLocaleString();
const fmtMoney = (n) => {
  if (n === null || n === undefined) return "—";
  const value = Number(n);
  if (value === 0) return "$0.00";
  if (value < 0.01) return "<$0.01";
  return "$" + value.toFixed(value >= 1 ? 2 : 4);
};
const fmtMs = (n) => (n === null || n === undefined ? "—" : n >= 1000 ? (n / 1000).toFixed(1) + "s" : Math.round(n) + "ms");
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const fmtDateTime = (ts) => new Date(ts * 1000).toLocaleString();
const pct = (n) => `${Number(n || 0).toFixed(1)}%`;
const initials = (label) => (label || "?").replace(/[^A-Za-z0-9]/g, " ").trim().split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

function toast(message, kind = "ok") {
  const box = document.createElement("div");
  box.className = `toast ${kind}`;
  box.textContent = message;
  $("#toasts").appendChild(box);
  setTimeout(() => {
    box.style.opacity = "0";
    box.style.transform = "translateY(6px)";
    box.style.transition = "all 180ms ease";
    setTimeout(() => box.remove(), 200);
  }, 3200);
}

async function api(path, options = {}) {
  const headers = { "content-type": "application/json", ...(options.headers || {}) };
  if (state.token) headers["x-hexgate-token"] = state.token;
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    showTokenPrompt();
    throw new Error("Admin token required");
  }
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const message = typeof data === "object" ? data?.error?.message || data?.error || data?.detail : data;
    throw new Error(message || `HTTP ${res.status}`);
  }
  return data;
}

function withButton(button, task) {
  if (!button) return task();
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";
  return task().finally(() => {
    button.disabled = false;
    button.textContent = original;
  });
}

window.addEventListener("error", (event) => console.error(event.error || event.message));

// ---------------------------------------------------------------------------
// navigation
// ---------------------------------------------------------------------------
function setView(view) {
  state.view = view;
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("is-active", section.id === `view-${view}`));
  const [title, sub] = VIEW_META[view] || ["Hexgate", ""];
  $("#view-title").textContent = title;
  $("#view-sub").textContent = sub;
  $("#top-actions").innerHTML = "";
  location.hash = view;
  const loaders = {
    overview: loadOverview,
    providers: loadProviders,
    routing: loadRouting,
    activity: loadActivity,
    playground: loadPlayground,
    settings: loadSettings,
  };
  const loader = loaders[view];
  if (loader) loader().catch((error) => toast(error.message, "err"));
}

function setHealthy(ok, text) {
  const pill = $("#side-status");
  pill.classList.toggle("ok", ok === true);
  pill.classList.toggle("bad", ok === false);
  $("#side-status-text").textContent = text;
}

// ---------------------------------------------------------------------------
// overview
// ---------------------------------------------------------------------------
async function loadOverview(silent = false) {
  const data = await api("/api/overview");
  state.overview = data;
  $("#version-label").textContent = `v${data.version}`;
  if (!silent) $("#top-actions").innerHTML = "";
  renderOverview();
  setHealthy(true, "gateway online");
}

function renderOverview() {
  const data = state.overview;
  if (!data) return;
  const s = data.stats;
  const host = data.server.local_url;
  const configured = data.providers.configured;
  const setup = data.first_run
    ? `<div class="notice" style="margin-bottom:14px">
         <div><strong>Finish setup:</strong> add an API key for at least one provider, then pick a model in Routing. Free tiers are enough to start.</div>
         <button class="btn btn-sm" data-goto="providers" style="margin-left:auto">Open providers</button>
       </div>`
    : "";

  $("#view-overview").innerHTML = `
    ${setup}
    <div class="stat-grid">
      ${statCard("Requests · 24h", fmtInt(s.requests), `${s.streams} streamed · ${pct(s.error_rate)} errors`, s.errors > 0 ? "warn" : "")}
      ${statCard("Tokens · 24h", fmtInt(s.total_tokens), `${fmtInt(s.prompt_tokens)} in / ${fmtInt(s.completion_tokens)} out`, "")}
      ${statCard("Estimated spend", fmtMoney(s.cost_usd), `${configured}/${data.providers.enabled} providers ready`, "")}
      ${statCard("Saved vs frontier", fmtMoney(s.saved_usd), `${fmtInt(s.optimized_tokens)} tokens shaped by the optimizer`, "up")}
    </div>
    <div class="split">
      <div class="stack">
        <div class="panel">
          <div class="panel-head">
            <div><h2>Traffic</h2><p>Requests per bucket over the last 24 hours</p></div>
            <span class="badge ${s.errors ? "badge-warn" : "badge-ok"}">${pct(s.error_rate)} error rate</span>
          </div>
          <div class="chart-wrap"><canvas class="chart" id="traffic-chart"></canvas></div>
          <div class="chart-legend">
            <span><i class="legend-dot" style="background:var(--accent)"></i>requests</span>
            <span><i class="legend-dot" style="background:var(--red)"></i>errors</span>
            <span style="margin-left:auto">p50 ${fmtMs(s.latency.p50)} · p95 ${fmtMs(s.latency.p95)}</span>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head">
            <div><h2>Recent requests</h2><p>Newest first</p></div>
            <button class="btn btn-sm btn-ghost" data-goto="activity">All activity</button>
          </div>
          <div class="panel-body tight">${recentTable(data.recent)}</div>
        </div>
      </div>
      <div class="stack">
        <div class="panel">
          <div class="panel-head"><div><h2>Connect an agent</h2><p>Same machine, any client</p></div></div>
          <div class="panel-body">
            <p class="hint" style="margin:0 0 10px;color:var(--muted);font-size:12px">Point any Anthropic-compatible client at Hexgate:</p>
            ${codeBlock(`ANTHROPIC_BASE_URL=${host}\nANTHROPIC_API_KEY=${data.security.proxy_auth ? "your-proxy-token" : "any-non-empty-value"}`, "env")}
            <p class="hint" style="margin:12px 0 6px;color:var(--muted);font-size:12px">Or let Hexgate launch Claude Code with the right env:</p>
            ${codeBlock("hexgate launch claude", "cmd")}
            <p class="hint" style="margin:12px 0 0;color:var(--faint);font-size:11px">OpenAI-compatible clients use <code>${host}/v1</code> as their base URL.</p>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><div><h2>Route health</h2><p>Circuit breakers</p></div></div>
          <div class="panel-body tight">${circuitList(data.circuits)}</div>
        </div>
        <div class="panel">
          <div class="panel-head"><div><h2>Gateway</h2><p>Runtime</p></div></div>
          <div class="panel-body">
            <dl class="kv">
              <dt>Version</dt><dd>${esc(data.version)}</dd>
              <dt>Uptime</dt><dd>${uptime(data.uptime_seconds)}</dd>
              <dt>Strategy</dt><dd>${esc(data.strategy)}</dd>
              <dt>Providers</dt><dd>${data.providers.enabled} enabled · ${configured} ready</dd>
              <dt>Proxy auth</dt><dd>${data.security.proxy_auth ? "bearer token" : "off (loopback only)"}</dd>
              <dt>Config</dt><dd>${esc(data.config_path)}</dd>
            </dl>
          </div>
        </div>
      </div>
    </div>`;

  $$("[data-goto]", $("#view-overview")).forEach((button) =>
    button.addEventListener("click", () => setView(button.dataset.goto)),
  );
  bindCopies($("#view-overview"));
  const canvas = $("#traffic-chart");
  if (canvas) drawChart(canvas, s.timeline);
}

function statCard(label, value, sub, tone) {
  return `<div class="stat">
    <div class="stat-label">${esc(label)}</div>
    <div class="stat-value">${esc(value)}</div>
    <div class="stat-sub ${tone || ""}">${esc(sub)}</div>
  </div>`;
}

function uptime(seconds) {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function recentTable(rows) {
  if (!rows || !rows.length) {
    return `<div class="empty"><strong>No requests yet</strong><span>Send a prompt from the Playground to see it here.</span></div>`;
  }
  return `<table class="table">
    <thead><tr><th>Time</th><th>Model</th><th>Route</th><th>Status</th><th class="num">Latency</th></tr></thead>
    <tbody>${rows
      .map(
        (r) => `<tr>
          <td class="muted mono">${fmtTime(r.ts)}</td>
          <td class="truncate">${esc(r.requested_model || "—")}</td>
          <td class="truncate mono muted">${esc(r.slug || "—")}</td>
          <td>${r.ok ? '<span class="badge badge-ok">ok</span>' : `<span class="badge badge-err">${r.status || "err"}</span>`}</td>
          <td class="num">${fmtMs(r.latency_ms)}</td>
        </tr>`,
      )
      .join("")}</tbody></table>`;
}

function circuitList(rows) {
  if (!rows || !rows.length) {
    return `<div class="empty"><strong>Nothing to break yet</strong><span>Circuits appear after the first upstream call.</span></div>`;
  }
  return `<table class="table"><tbody>${rows
    .map((c) => {
      const cls = c.state === "open" ? "badge-err" : c.state === "half-open" ? "badge-warn" : "badge-ok";
      return `<tr>
        <td class="truncate mono">${esc(c.slug)}</td>
        <td><span class="badge ${cls}">${esc(c.state)}</span></td>
        <td class="num muted">${c.success_rate}% · ${fmtMs(c.latency_ms)}</td>
      </tr>`;
    })
    .join("")}</tbody></table>`;
}

function drawChart(canvas, timeline) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  canvas.width = Math.floor(rect.width * dpr);
  canvas.height = Math.floor(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);

  const w = rect.width;
  const h = rect.height;
  const pad = 8;
  const points = timeline && timeline.length ? timeline : [{ requests: 0, errors: 0 }];
  const max = Math.max(1, ...points.map((p) => p.requests));
  const stepX = (w - pad * 2) / Math.max(points.length - 1, 1);
  const y = (value) => h - pad - (value / max) * (h - pad * 2);

  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const gy = pad + ((h - pad * 2) / 3) * i;
    ctx.beginPath();
    ctx.moveTo(pad, gy);
    ctx.lineTo(w - pad, gy);
    ctx.stroke();
  }

  const path = new Path2D();
  points.forEach((point, index) => {
    const px = pad + stepX * index;
    const py = y(point.requests);
    if (index === 0) path.moveTo(px, py);
    else path.lineTo(px, py);
  });

  const fill = new Path2D(path);
  fill.lineTo(w - pad, h - pad);
  fill.lineTo(pad, h - pad);
  fill.closePath();
  const gradient = ctx.createLinearGradient(0, 0, 0, h);
  gradient.addColorStop(0, "rgba(184,255,60,0.22)");
  gradient.addColorStop(1, "rgba(184,255,60,0)");
  ctx.fillStyle = gradient;
  ctx.fill(fill);

  ctx.strokeStyle = "#b8ff3c";
  ctx.lineWidth = 1.7;
  ctx.lineJoin = "round";
  ctx.stroke(path);

  ctx.fillStyle = "#ff5f6d";
  points.forEach((point, index) => {
    if (!point.errors) return;
    const px = pad + stepX * index;
    const py = y(point.requests);
    ctx.beginPath();
    ctx.arc(px, py, 2.2, 0, Math.PI * 2);
    ctx.fill();
  });
}

// ---------------------------------------------------------------------------
// providers
// ---------------------------------------------------------------------------
async function loadProviders() {
  const data = await api("/api/providers");
  state.providers = data.providers;
  renderProviders();
}

function renderProviders() {
  const configured = state.providers.filter((p) => p.configured).length;
  const filter = state.providerFilter || { q: "", mode: "all" };
  $("#top-actions").innerHTML = `<span class="badge badge-muted">${configured} / ${state.providers.length} configured</span>`;

  $("#view-providers").innerHTML = `
    <div class="toolbar">
      <label class="search">
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="9" cy="9" r="5.5"/><path d="m13 13 4 4"/></svg>
        <input id="provider-search" placeholder="Search providers, models, tiers…" value="${esc(filter.q)}" />
      </label>
      <select class="select" id="provider-mode" style="width:180px">
        ${["all", "configured", "missing", "free", "local"].map((m) => `<option value="${m}" ${filter.mode === m ? "selected" : ""}>${m === "all" ? "All providers" : m === "configured" ? "Ready to use" : m === "missing" ? "Needs a key" : m === "free" ? "Free tier" : "Local"}</option>`).join("")}
      </select>
      <button class="btn btn-ghost" id="provider-refresh">Refresh</button>
    </div>
    <div class="provider-grid" id="provider-grid"></div>
    <div id="provider-empty"></div>`;

  const paint = () => {
    const q = ($("#provider-search").value || "").toLowerCase();
    const mode = $("#provider-mode").value;
    state.providerFilter = { q, mode };
    const rows = state.providers.filter((p) => {
      const haystack = `${p.label} ${p.id} ${p.tier} ${p.models.join(" ")} ${p.default_model}`.toLowerCase();
      if (q && !haystack.includes(q)) return false;
      if (mode === "configured") return p.configured && p.enabled;
      if (mode === "missing") return !p.configured || !p.enabled;
      if (mode === "free") return p.tier === "free" || p.tier === "trial";
      if (mode === "local") return p.tier === "local";
      return true;
    });
    $("#provider-grid").innerHTML = rows.map(providerCard).join("");
    $$("[data-provider]", $("#provider-grid")).forEach((card) =>
      card.addEventListener("click", () => openProvider(card.dataset.provider)),
    );
    $("#provider-empty").innerHTML = rows.length
      ? ""
      : `<div class="empty"><strong>No matches</strong><span>Try a different search or filter.</span></div>`;
  };

  $("#provider-search").addEventListener("input", paint);
  $("#provider-mode").addEventListener("change", paint);
  $("#provider-refresh").addEventListener("click", () => loadProviders().catch((e) => toast(e.message, "err")));
  paint();
}

function providerCard(p) {
  const state = p.circuit ? p.circuit.state : "closed";
  const dotColor = !p.enabled ? "var(--faint)" : state === "open" ? "var(--red)" : p.configured ? "var(--green)" : "var(--amber)";
  const tierLabel = p.tier === "subscription" ? "plan" : p.tier;
  return `<button class="provider-card ${p.enabled ? "" : "is-disabled"}" data-provider="${esc(p.id)}">
    <div class="pc-top">
      <div class="pc-icon" style="--pc-color:${esc(p.color)}">${esc(initials(p.label))}</div>
      <div style="min-width:0">
        <div class="pc-name">${esc(p.label)}</div>
        <div class="pc-meta"><span class="tier-${esc(p.tier)}">${esc(tierLabel)}</span><span>·</span><span>${esc(p.api)}</span></div>
      </div>
      <span style="margin-left:auto;color:${dotColor};font-size:16px;line-height:1">●</span>
    </div>
    <div class="pc-models">${esc(p.default_model || p.models[0] || "no model selected")}</div>
    <div class="pc-foot">
      <span class="badge ${p.configured ? "badge-ok" : "badge-warn"}">${p.key_count ? `${p.key_count} key${p.key_count > 1 ? "s" : ""}` : p.configured ? "no key needed" : "needs key"}</span>
      <span class="muted" style="font-size:11px">${p.models.length} models</span>
    </div>
  </button>`;
}

function openProvider(id) {
  const p = state.providers.find((row) => row.id === id);
  if (!p) return;
  openDrawer(`
    <div class="drawer-head">
      <div class="pc-icon" style="--pc-color:${esc(p.color)}">${esc(initials(p.label))}</div>
      <div style="min-width:0">
        <h2>${esc(p.label)}</h2>
        <p>${esc(p.id)} · ${esc(p.tier)} · ${esc(p.api)} protocol</p>
      </div>
      <button class="icon-btn" data-close aria-label="Close">✕</button>
    </div>
    <div class="drawer-body">
      <div id="provider-test"></div>
      <label class="check">
        <input type="checkbox" id="pf-enabled" ${p.enabled ? "checked" : ""} />
        <span>Enabled<small>Disabled providers are skipped by the router.</small></span>
      </label>
      <div class="row">
        <div class="field">
          <label for="pf-api">Protocol</label>
          <select class="select" id="pf-api">
            <option value="openai" ${p.api === "openai" ? "selected" : ""}>OpenAI compatible</option>
            <option value="anthropic" ${p.api === "anthropic" ? "selected" : ""}>Anthropic native</option>
          </select>
        </div>
        <div class="field">
          <label for="pf-default">Default model</label>
          <input class="input" id="pf-default" value="${esc(p.default_model)}" placeholder="provider model id" />
        </div>
      </div>
      <div class="field">
        <label for="pf-base">Base URL</label>
        <input class="input" id="pf-base" value="${esc(p.base_url)}" placeholder="https://api.provider.com/v1" />
        ${p.notes ? `<span class="hint">${esc(p.notes)}</span>` : ""}
      </div>
      <div class="field">
        <label for="pf-models">Model IDs <span style="color:var(--faint)">(one per line)</span></label>
        <textarea class="textarea" id="pf-models" rows="5">${esc(p.models.join("\n"))}</textarea>
        <span class="hint">Used for the model picker and <code>provider/model</code> routes. Fetch them from the provider, or type them.</span>
      </div>
      <div class="divider"></div>
      <div class="field">
        <label for="pf-keys">API keys <span style="color:var(--faint)">(one per line, optional)</span></label>
        <textarea class="textarea" id="pf-keys" rows="3" placeholder="${p.key_count ? `${p.key_count} key(s) stored — type to replace` : "paste a key"}"></textarea>
        <span class="hint">${
          p.key_count
            ? `Stored: ${p.keys.map((k) => `<code>${esc(k)}</code>`).join(", ")}. The first key is used first; failures rotate to the next.`
            : p.requires_key
              ? "Encrypted at rest with AES-256-GCM. Never written to config.toml."
              : "This provider does not need a key."
        }</span>
      </div>
      ${p.signup ? `<a class="btn btn-sm" href="${esc(p.signup)}" target="_blank" rel="noreferrer noopener">Get an API key ↗</a>` : ""}
      ${p.docs ? `<a class="btn btn-sm btn-ghost" href="${esc(p.docs)}" target="_blank" rel="noreferrer noopener">Docs ↗</a>` : ""}
    </div>
    <div class="drawer-foot">
      <button class="btn" id="pf-test">Test</button>
      <button class="btn" id="pf-fetch">Fetch models</button>
      <button class="btn btn-primary" id="pf-save">Save</button>
    </div>`);

  const saveKeys = async () => {
    const raw = $("#pf-keys").value.trim();
    if (!raw) return false;
    const keys = raw.split("\n").map((line) => line.trim()).filter(Boolean);
    await api(`/api/providers/${p.id}/keys`, { method: "PUT", body: JSON.stringify({ keys }) });
    return true;
  };

  $("#pf-save").addEventListener("click", (event) =>
    withButton(event.currentTarget, async () => {
      try {
        await api(`/api/providers/${p.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            enabled: $("#pf-enabled").checked,
            api: $("#pf-api").value,
            base_url: $("#pf-base").value.trim(),
            default_model: $("#pf-default").value.trim(),
            models: $("#pf-models").value.split("\n").map((s) => s.trim()).filter(Boolean),
          }),
        });
        await saveKeys();
        closeDrawer();
        await loadProviders();
        toast(`${p.label} saved`);
      } catch (error) {
        toast(error.message, "err");
      }
    }),
  );

  $("#pf-test").addEventListener("click", (event) =>
    withButton(event.currentTarget, async () => {
      const box = $("#provider-test");
      box.innerHTML = `<div class="notice info">Testing upstream…</div>`;
      try {
        await saveKeys();
        const result = await api(`/api/providers/${p.id}/test`, { method: "POST" });
        box.innerHTML = `<div class="notice ${result.ok ? "info" : ""}" style="${result.ok ? "border-color:rgba(61,220,132,.35);background:rgba(61,220,132,.06);color:#bdf3d3" : ""}">
          <span>${result.ok ? "✓" : "✕"}</span>
          <div><strong>${esc(result.ok ? "Reachable" : "Failed")}</strong> · ${esc(result.detail || "")}${
            result.latency_ms ? ` · ${fmtMs(result.latency_ms)}` : ""
          }</div>
        </div>`;
      } catch (error) {
        box.innerHTML = `<div class="notice"><span>✕</span><div>${esc(error.message)}</div></div>`;
      }
    }),
  );

  $("#pf-fetch").addEventListener("click", (event) =>
    withButton(event.currentTarget, async () => {
      const box = $("#provider-test");
      box.innerHTML = `<div class="notice info">Discovering models…</div>`;
      try {
        await api(`/api/providers/${p.id}`, {
          method: "PATCH",
          body: JSON.stringify({ base_url: $("#pf-base").value.trim(), api: $("#pf-api").value }),
        });
        await saveKeys();
        const result = await api(`/api/providers/${p.id}/models`);
        const models = result.models || [];
        if (!models.length) {
          box.innerHTML = `<div class="notice"><span>!</span><div>No models returned. The provider may not expose <code>/models</code>; type IDs manually.</div></div>`;
          return;
        }
        $("#pf-models").value = models.join("\n");
        box.innerHTML = `<div class="notice info"><span>✓</span><div>Found ${models.length} models. Review the list, then Save.</div></div>`;
      } catch (error) {
        box.innerHTML = `<div class="notice"><span>✕</span><div>${esc(error.message)}</div></div>`;
      }
    }),
  );
}

// ---------------------------------------------------------------------------
// routing
// ---------------------------------------------------------------------------
async function loadRouting() {
  const [chains, models] = await Promise.all([api("/api/routes/chains"), api("/api/routes/models")]);
  state.models = models.models;
  state.chains = chains;
  renderRouting();
}

function renderRouting() {
  const { chains, routing } = state.chains;
  const options = state.models.map((m) => `<option value="${esc(m.slug)}">`).join("");
  const chainField = (tier, label, hint) => `
    <div class="field">
      <label>${label}</label>
      <textarea class="textarea" id="chain-${tier}" rows="3" list="route-models" placeholder="provider/model-id">${esc((chains[tier] || []).join("\n"))}</textarea>
      <span class="hint">${hint}</span>
    </div>`;

  $("#view-routing").innerHTML = `
    <datalist id="route-models">${options}</datalist>
    <div class="split">
      <div class="stack">
        <div class="panel">
          <div class="panel-head">
            <div><h2>Tier chains</h2><p>First healthy route wins. Later entries are failover.</p></div>
            <button class="btn btn-primary btn-sm" id="routing-save">Save routing</button>
          </div>
          <div class="panel-body">
            ${chainField("default", "Default", "Every request that is not a recognised tier lands here.")}
            <div class="row">
              ${chainField("opus", "Opus tier", "Matches models containing “opus”.")}
              ${chainField("sonnet", "Sonnet tier", "Matches models containing “sonnet”.")}
            </div>
            <div class="row">
              ${chainField("haiku", "Haiku tier", "Matches models containing “haiku”.")}
              ${chainField("fable", "Fable tier", "Matches models containing “fable”.")}
            </div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><div><h2>Failover</h2><p>How hard the gateway tries before giving up</p></div></div>
          <div class="panel-body">
            <div class="row-3">
              <div class="field"><label for="rt-strategy">Strategy</label>
                <select class="select" id="rt-strategy">
                  ${["quality", "fastest", "cheapest", "round_robin"].map((s) => `<option value="${s}" ${routing.strategy === s ? "selected" : ""}>${s.replace("_", " ")}</option>`).join("")}
                </select>
              </div>
              <div class="field"><label for="rt-attempts">Max attempts</label><input class="input" type="number" min="1" max="12" id="rt-attempts" value="${routing.max_attempts}" /></div>
              <div class="field"><label for="rt-timeout">Read timeout (s)</label><input class="input" type="number" min="5" max="900" id="rt-timeout" value="${routing.timeout}" /></div>
            </div>
            <div class="row-3">
              <div class="field"><label for="rt-cooldown">Circuit cooldown (s)</label><input class="input" type="number" min="5" max="3600" id="rt-cooldown" value="${routing.cooldown_seconds}" /></div>
              <div class="field"><label for="rt-threshold">Failures to open</label><input class="input" type="number" min="1" max="20" id="rt-threshold" value="${routing.failure_threshold}" /></div>
              <div class="field"><label for="rt-connect">Connect timeout (s)</label><input class="input" type="number" min="1" max="60" id="rt-connect" value="${routing.connect_timeout}" /></div>
            </div>
            <span class="hint">Quality keeps your order. Fastest sorts by observed latency. Cheapest sorts by list price. Round-robin spreads load.</span>
          </div>
        </div>
      </div>
      <div class="panel" style="align-self:start">
        <div class="panel-head"><div><h2>Resolve a model</h2><p>Dry run — no tokens spent</p></div></div>
        <div class="panel-body">
          <div class="field">
            <label for="plan-input">Model a client would request</label>
            <input class="input" id="plan-input" list="route-models" placeholder="claude-sonnet-4-5 or hexgate/opus" value="${esc(state.planInput || "")}" />
          </div>
          <button class="btn" id="plan-run" style="width:100%">Show route plan</button>
          <div id="plan-result" style="margin-top:14px">${state.plan ? planMarkup(state.plan) : `<p class="hint">Run a plan to see exactly which providers would be tried, in order.</p>`}</div>
        </div>
      </div>
    </div>`;

  $("#routing-save").addEventListener("click", (event) =>
    withButton(event.currentTarget, async () => {
      try {
        const payload = {
          chains: {},
          routing: {
            strategy: $("#rt-strategy").value,
            max_attempts: Number($("#rt-attempts").value),
            timeout: Number($("#rt-timeout").value),
            cooldown_seconds: Number($("#rt-cooldown").value),
            failure_threshold: Number($("#rt-threshold").value),
            connect_timeout: Number($("#rt-connect").value),
          },
        };
        for (const tier of ["default", "fable", "opus", "sonnet", "haiku"]) {
          payload.chains[tier] = ($(`#chain-${tier}`).value || "").split("\n").map((s) => s.trim()).filter(Boolean);
        }
        const result = await api("/api/routes/chains", { method: "PUT", body: JSON.stringify(payload) });
        state.chains = result;
        toast("Routing saved");
      } catch (error) {
        toast(error.message, "err");
      }
    }),
  );

  const runPlan = async () => {
    state.planInput = $("#plan-input").value;
    try {
      const result = await api(`/api/routes/plan?model=${encodeURIComponent(state.planInput)}`);
      state.plan = result.plan;
      $("#plan-result").innerHTML = planMarkup(result.plan);
    } catch (error) {
      toast(error.message, "err");
    }
  };
  $("#plan-run").addEventListener("click", runPlan);
  $("#plan-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runPlan();
  });
}

function planMarkup(plan) {
  const rows = plan.candidates || [];
  const skipped = plan.skipped || [];
  if (!rows.length) {
    return `<div class="notice"><span>!</span><div>No route. ${esc(skipped.map((s) => `${s.ref}: ${s.reason}`).join("; ") || "Add a provider key or a chain entry.")}</div></div>`;
  }
  return `<div class="pill-row" style="margin-bottom:10px">
      <span class="badge badge-muted">source: ${esc(plan.source)}</span>
      ${plan.tier ? `<span class="badge badge-blue">tier: ${esc(plan.tier)}</span>` : ""}
      <span class="badge badge-muted">${rows.length} candidate${rows.length === 1 ? "" : "s"}</span>
    </div>
    <div class="plan-list">${rows
      .map(
        (c, index) => `<div class="plan-item">
          <span class="plan-rank">${index + 1}</span>
          <span class="plan-slug">${esc(c.slug)}</span>
          ${c.key_index > 1 ? `<span class="badge badge-muted">key ${c.key_index}</span>` : ""}
          <span class="plan-state badge ${index === 0 ? "badge-accent" : "badge-muted"}">${index === 0 ? "primary" : "fallback"}</span>
        </div>`,
      )
      .join("")}</div>
    ${skipped.length ? `<p class="hint" style="margin:10px 0 0">Skipped: ${esc(skipped.map((s) => `${s.ref} (${s.reason})`).join(", "))}</p>` : ""}`;
}

// ---------------------------------------------------------------------------
// activity
// ---------------------------------------------------------------------------
async function loadActivity(silent = false) {
  const data = await api("/api/requests?limit=200");
  state.requests = data.requests;
  renderActivity();
  if (!silent) {
    $("#top-actions").innerHTML = `<button class="btn btn-ghost btn-sm" id="activity-clear">Clear log</button>`;
    $("#activity-clear").addEventListener("click", async () => {
      try {
        await api("/api/requests", { method: "DELETE" });
        await loadActivity();
        toast("Request log cleared");
      } catch (error) {
        toast(error.message, "err");
      }
    });
  }
}

function renderActivity() {
  const filter = state.activityFilter || { q: "", mode: "all" };
  const rows = state.requests.filter((r) => {
    if (filter.mode === "ok" && !r.ok) return false;
    if (filter.mode === "err" && r.ok) return false;
    const hay = `${r.requested_model} ${r.slug} ${r.error || ""}`.toLowerCase();
    return !filter.q || hay.includes(filter.q.toLowerCase());
  });

  $("#view-activity").innerHTML = `
    <div class="toolbar">
      <label class="search">
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="9" cy="9" r="5.5"/><path d="m13 13 4 4"/></svg>
        <input id="activity-search" placeholder="Filter by model, provider or error…" value="${esc(filter.q)}" />
      </label>
      <select class="select" id="activity-mode" style="width:150px">
        <option value="all" ${filter.mode === "all" ? "selected" : ""}>All</option>
        <option value="ok" ${filter.mode === "ok" ? "selected" : ""}>Successful</option>
        <option value="err" ${filter.mode === "err" ? "selected" : ""}>Failed</option>
      </select>
      <button class="btn btn-ghost" id="activity-refresh">Refresh</button>
    </div>
    <div class="panel"><div class="panel-body tight">
      ${
        rows.length
          ? `<table class="table">
              <thead><tr><th>Time</th><th>Requested</th><th>Routed to</th><th>Status</th><th class="num">Latency</th><th class="num">Tokens</th><th class="num">Cost</th><th class="num">Tries</th></tr></thead>
              <tbody>${rows
                .map(
                  (r) => `<tr class="clickable" data-request="${esc(r.id)}">
                    <td class="muted mono" style="white-space:nowrap">${fmtTime(r.ts)}</td>
                    <td class="truncate">${esc(r.requested_model || "—")}</td>
                    <td class="truncate mono muted">${esc(r.slug || "—")}</td>
                    <td>${r.ok ? '<span class="badge badge-ok">ok</span>' : `<span class="badge badge-err">${esc(r.status)}</span>`}</td>
                    <td class="num">${fmtMs(r.latency_ms)}</td>
                    <td class="num">${fmtInt(r.total_tokens)}</td>
                    <td class="num">${fmtMoney(r.cost_usd)}</td>
                    <td class="num">${r.attempts}</td>
                  </tr>`,
                )
                .join("")}</tbody></table>`
          : `<div class="empty"><strong>Nothing here yet</strong><span>Requests appear as soon as a client or the playground calls the gateway.</span></div>`
      }
    </div></div>`;

  $("#activity-search").addEventListener("input", (event) => {
    state.activityFilter = { ...filter, q: event.target.value };
    renderActivity();
  });
  $("#activity-mode").addEventListener("change", (event) => {
    state.activityFilter = { ...filter, mode: event.target.value };
    renderActivity();
  });
  $("#activity-refresh").addEventListener("click", () => loadActivity().catch((e) => toast(e.message, "err")));
  $$("[data-request]", $("#view-activity")).forEach((row) =>
    row.addEventListener("click", () => openRequest(row.dataset.request)),
  );
}

function openRequest(id) {
  const record = state.requests.find((r) => r.id === id);
  if (!record) return;
  openDrawer(`
    <div class="drawer-head">
      <div>
        <h2>${esc(record.requested_model || "Request")}</h2>
        <p>${fmtDateTime(record.ts)} · ${esc(record.protocol)} · ${record.stream ? "streamed" : "non-stream"}</p>
      </div>
      <button class="icon-btn" data-close aria-label="Close">✕</button>
    </div>
    <div class="drawer-body">
      <dl class="kv" style="margin-bottom:18px">
        <dt>Status</dt><dd>${record.ok ? "200 ok" : esc(record.status)}</dd>
        <dt>Route</dt><dd>${esc(record.slug || "—")}</dd>
        <dt>Attempts</dt><dd>${record.attempts}</dd>
        <dt>Latency</dt><dd>${fmtMs(record.latency_ms)}${record.ttft_ms ? ` (first byte ${fmtMs(record.ttft_ms)})` : ""}</dd>
        <dt>Tokens</dt><dd>${fmtInt(record.prompt_tokens)} in / ${fmtInt(record.completion_tokens)} out</dd>
        <dt>Cost</dt><dd>${fmtMoney(record.cost_usd)}</dd>
        <dt>Saved</dt><dd>${fmtMoney(record.saved_usd)}</dd>
        <dt>Optimizer</dt><dd>${fmtInt(record.optimized_tokens)} tokens</dd>
      </dl>
      ${record.route && record.route.length ? `<p class="section-title">Plan</p><div class="plan-list" style="margin-bottom:16px">${record.route.map((slug, i) => `<div class="plan-item"><span class="plan-rank">${i + 1}</span><span class="plan-slug">${esc(slug)}</span></div>`).join("")}</div>` : ""}
      ${record.error ? `<p class="section-title">Error</p><div class="code-block">${esc(record.error)}</div>` : ""}
      <p class="section-title" style="margin-top:16px">Raw record</p>
      <div class="code-block">${esc(JSON.stringify(record, null, 2))}</div>
    </div>`);
}

// ---------------------------------------------------------------------------
// playground
// ---------------------------------------------------------------------------
async function loadPlayground() {
  if (!state.models.length) {
    try {
      const data = await api("/api/routes/models");
      state.models = data.models;
    } catch (error) {
      /* models are optional */
    }
  }
  renderPlayground();
}

function renderPlayground() {
  const models = state.models.map((m) => `<option value="${esc(m.slug)}"></option>`).join("");
  const previous = state.playground || {};
  $("#view-playground").innerHTML = `
    <datalist id="pg-models">${models}</datalist>
    <div class="split">
      <div class="panel">
        <div class="panel-head"><div><h2>Prompt</h2><p>Runs through the real router, failover and billing estimates.</p></div></div>
        <div class="panel-body">
          <div class="field">
            <label for="pg-model">Model</label>
            <input class="input" id="pg-model" list="pg-models" placeholder="claude-sonnet-4-5, hexgate/opus, or provider/model" value="${esc(previous.model || "claude-sonnet-4-5")}" />
            <span class="hint">Leave a tier name to exercise the routing chain instead of one provider.</span>
          </div>
          <div class="field">
            <label for="pg-system">System prompt <span style="color:var(--faint)">(optional)</span></label>
            <textarea class="textarea" id="pg-system" rows="3" placeholder="You are a concise assistant.">${esc(previous.system || "")}</textarea>
          </div>
          <div class="field">
            <label for="pg-prompt">User message</label>
            <textarea class="textarea" id="pg-prompt" rows="7" placeholder="Explain the difference between a process and a thread in two sentences.">${esc(previous.prompt || "")}</textarea>
          </div>
          <div class="row">
            <div class="field"><label for="pg-temp">Temperature</label><input class="input" type="number" step="0.1" min="0" max="2" id="pg-temp" value="${esc(previous.temperature ?? 0.7)}" /></div>
            <div class="field"><label for="pg-max">Max tokens</label><input class="input" type="number" min="16" max="8192" id="pg-max" value="${esc(previous.max_tokens ?? 512)}" /></div>
          </div>
          <button class="btn btn-primary" id="pg-run" style="width:100%">Send through gateway</button>
        </div>
      </div>
      <div class="panel" style="align-self:start">
        <div class="panel-head">
          <div><h2>Response</h2><p id="pg-route">${previous.route ? `via ${esc(previous.route)}` : "Not sent yet"}</p></div>
          <span id="pg-meta" class="badge badge-muted">idle</span>
        </div>
        <div class="panel-body">
          <div class="output" id="pg-output">${previous.text ? esc(previous.text) : '<span class="placeholder">The model response will appear here.</span>'}</div>
          <div class="metric-inline" style="margin-top:12px" id="pg-metrics">
            ${
              previous.metrics
                ? Object.entries(previous.metrics)
                    .map(([key, value]) => `<span>${esc(key)} <b>${esc(value)}</b></span>`)
                    .join("")
                : ""
            }
          </div>
        </div>
      </div>
    </div>`;

  const run = () =>
    withButton($("#pg-run"), async () => {
      const payload = {
        model: $("#pg-model").value.trim(),
        system: $("#pg-system").value,
        prompt: $("#pg-prompt").value,
        temperature: Number($("#pg-temp").value),
        max_tokens: Number($("#pg-max").value),
      };
      if (!payload.prompt.trim()) {
        toast("Write a prompt first", "err");
        return;
      }
      state.playground = { ...payload };
      $("#pg-meta").textContent = "running…";
      $("#pg-output").innerHTML = '<span class="placeholder">Waiting for the first token…</span>';
      try {
        const result = await api("/api/playground", { method: "POST", body: JSON.stringify(payload) });
        state.playground = { ...payload, text: result.text, route: result.route, metrics: {
          route: result.route,
          latency: fmtMs(result.record.latency_ms),
          tokens: `${fmtInt(result.usage.prompt_tokens)} in / ${fmtInt(result.usage.completion_tokens)} out`,
          cost: fmtMoney(result.record.cost_usd),
        } };
        $("#pg-route").textContent = `via ${result.route}`;
        $("#pg-meta").textContent = "200 ok";
        $("#pg-meta").className = "badge badge-ok";
        $("#pg-output").textContent = result.text || "(empty response)";
        $("#pg-metrics").innerHTML = Object.entries(state.playground.metrics)
          .map(([key, value]) => `<span>${esc(key)} <b>${esc(value)}</b></span>`)
          .join("");
      } catch (error) {
        $("#pg-meta").textContent = "failed";
        $("#pg-meta").className = "badge badge-err";
        $("#pg-output").innerHTML = `<span class="placeholder">${esc(error.message)}</span>`;
        toast(error.message, "err");
      }
    });

  $("#pg-run").addEventListener("click", run);
  $("#pg-prompt").addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") run();
  });
}

// ---------------------------------------------------------------------------
// settings
// ---------------------------------------------------------------------------
async function loadSettings() {
  const [config, diagnostics] = await Promise.all([api("/api/config"), api("/api/diagnostics")]);
  state.config = config.config;
  state.diagnostics = diagnostics;
  renderSettings();
  $("#top-actions").innerHTML = `<button class="btn btn-primary btn-sm" id="settings-save">Save settings</button>`;
  $("#settings-save").addEventListener("click", (event) => saveSettings(event.currentTarget));
}

function renderSettings() {
  const cfg = state.config;
  if (!cfg) return;
  const d = state.diagnostics || {};
  $("#view-settings").innerHTML = `
    <div class="split">
      <div class="stack">
        <div class="panel">
          <div class="panel-head"><div><h2>Server</h2><p>Changes to host or port need a restart.</p></div></div>
          <div class="panel-body">
            <div class="row">
              <div class="field"><label for="st-host">Host</label><input class="input" id="st-host" value="${esc(cfg.server.host)}" /><span class="hint">Keep 127.0.0.1 unless you really need LAN access.</span></div>
              <div class="field"><label for="st-port">Port</label><input class="input" type="number" min="1" max="65535" id="st-port" value="${cfg.server.port}" /></div>
            </div>
            <div class="row">
              <div class="field"><label for="st-log">Log level</label>
                <select class="select" id="st-log">${["info", "debug", "warning", "error"].map((l) => `<option ${cfg.server.log_level === l ? "selected" : ""}>${l}</option>`).join("")}</select>
              </div>
              <div class="field"><label>Browser</label>
                <label class="check" style="padding-top:6px"><input type="checkbox" id="st-browser" ${cfg.server.open_browser ? "checked" : ""} /><span>Open dashboard on start</span></label>
              </div>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head"><div><h2>Security</h2><p>Bearer tokens. Empty means “no token required”.</p></div></div>
          <div class="panel-body">
            <div class="field">
              <label for="st-proxy-token">Proxy token <span class="badge badge-muted">/v1</span></label>
              <input class="input" type="password" id="st-proxy-token" placeholder="${cfg.auth.proxy_token ? `stored: ${esc(cfg.auth.proxy_token)}` : "not set"}" autocomplete="new-password" />
              <span class="hint">Agents send this as <code>Authorization: Bearer</code> or <code>x-api-key</code>. Leave blank to keep the current value.</span>
            </div>
            <div class="field">
              <label for="st-admin-token">Admin token <span class="badge badge-muted">/api</span></label>
              <input class="input" type="password" id="st-admin-token" placeholder="${cfg.auth.admin_token ? `stored: ${esc(cfg.auth.admin_token)}` : "not set"}" autocomplete="new-password" />
              <span class="hint">The dashboard stores this in local storage only. Leave blank to keep the current value.</span>
            </div>
            <label class="check">
              <input type="checkbox" id="st-remote" ${cfg.auth.allow_remote ? "checked" : ""} />
              <span>Allow remote connections<small>Off by default. Non-loopback requests are rejected even with a token.</small></span>
            </label>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head"><div><h2>Token optimizer</h2><p>Shape prompts before they hit the provider.</p></div></div>
          <div class="panel-body">
            <label class="check">
              <input type="checkbox" id="st-opt-enabled" ${cfg.optimizer.enabled ? "checked" : ""} />
              <span>Enabled<small>Every rule is reported per request in Activity.</small></span>
            </label>
            <div class="field" style="margin-top:8px">
              <label for="st-opt-tools">Truncate tool output above (characters)</label>
              <input class="input" type="number" min="0" max="200000" step="500" id="st-opt-tools" value="${cfg.optimizer.max_tool_result_chars}" />
              <span class="hint">Keeps the head and tail of oversized tool results. 0 disables truncation.</span>
            </div>
            <label class="check"><input type="checkbox" id="st-opt-system" ${cfg.optimizer.dedupe_system ? "checked" : ""} /><span>Merge duplicate system messages</span></label>
            <label class="check"><input type="checkbox" id="st-opt-empty" ${cfg.optimizer.strip_empty_messages ? "checked" : ""} /><span>Drop empty messages without tool calls</span></label>
            <label class="check"><input type="checkbox" id="st-opt-ws" ${cfg.optimizer.trim_trailing_whitespace ? "checked" : ""} /><span>Trim trailing whitespace</span></label>
          </div>
        </div>
      </div>

      <div class="stack">
        <div class="panel">
          <div class="panel-head"><div><h2>Telemetry</h2><p>In-memory only. Nothing is written to disk.</p></div></div>
          <div class="panel-body">
            <div class="field"><label for="st-retain">Keep last N requests</label><input class="input" type="number" min="50" max="5000" step="50" id="st-retain" value="${cfg.telemetry.retain}" /></div>
            <label class="check"><input type="checkbox" id="st-costs" ${cfg.telemetry.estimate_costs ? "checked" : ""} /><span>Estimate cost and savings<small>Uses a built-in list-price table. Unknown models show “—”.</small></span></label>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head"><div><h2>Diagnostics</h2><p>Paste this into a bug report.</p></div></div>
          <div class="panel-body">
            <dl class="kv">
              <dt>Version</dt><dd>${esc(d.version)}</dd>
              <dt>Python</dt><dd>${esc(d.python)}</dd>
              <dt>Platform</dt><dd>${esc(d.platform)}</dd>
              <dt>Home</dt><dd>${esc(d.home)}</dd>
              <dt>Config</dt><dd>${esc(d.config_path)}</dd>
              <dt>Secrets</dt><dd>${d.secrets_present ? "encrypted store present" : "none"}</dd>
              <dt>Providers w/ keys</dt><dd>${esc(d.providers_with_keys)}</dd>
              <dt>Uptime</dt><dd>${uptime(d.uptime_seconds || 0)}</dd>
            </dl>
            <div class="divider"></div>
            <div class="pill-row">
              <button class="btn btn-sm" id="st-export">Download config</button>
              <button class="btn btn-sm" id="st-reset-circuits">Reset circuits</button>
              <button class="btn btn-sm btn-danger" id="st-clear">Clear telemetry</button>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  $("#st-export").addEventListener("click", async () => {
    try {
      const text = await api("/api/config/export");
      const blob = new Blob([text], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "hexgate-config.toml";
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast(error.message, "err");
    }
  });
  $("#st-reset-circuits").addEventListener("click", async () => {
    try {
      await api("/api/health/reset", { method: "POST", body: "{}" });
      toast("Circuits reset");
    } catch (error) {
      toast(error.message, "err");
    }
  });
  $("#st-clear").addEventListener("click", async () => {
    try {
      await api("/api/requests", { method: "DELETE" });
      toast("Telemetry cleared");
    } catch (error) {
      toast(error.message, "err");
    }
  });
}

async function saveSettings(button) {
  return withButton(button, async () => {
    const proxyToken = $("#st-proxy-token").value;
    const adminToken = $("#st-admin-token").value;
    const payload = {
      server: {
        host: $("#st-host").value.trim(),
        port: Number($("#st-port").value),
        log_level: $("#st-log").value,
        open_browser: $("#st-browser").checked,
      },
      auth: {
        allow_remote: $("#st-remote").checked,
        ...(proxyToken ? { proxy_token: proxyToken } : {}),
        ...(adminToken ? { admin_token: adminToken } : {}),
      },
      optimizer: {
        enabled: $("#st-opt-enabled").checked,
        max_tool_result_chars: Number($("#st-opt-tools").value),
        dedupe_system: $("#st-opt-system").checked,
        strip_empty_messages: $("#st-opt-empty").checked,
        trim_trailing_whitespace: $("#st-opt-ws").checked,
      },
      telemetry: {
        retain: Number($("#st-retain").value),
        estimate_costs: $("#st-costs").checked,
      },
    };
    try {
      const result = await api("/api/config", { method: "PUT", body: JSON.stringify(payload) });
      if (adminToken) {
        state.token = adminToken;
        localStorage.setItem("hexgate.token", adminToken);
      }
      state.config = result.config;
      toast(result.restart_required ? "Saved. Restart to change host or port." : "Settings saved");
      await loadSettings();
    } catch (error) {
      toast(error.message, "err");
    }
  });
}

// ---------------------------------------------------------------------------
// drawer, token prompt, copy
// ---------------------------------------------------------------------------
function openDrawer(html) {
  const drawer = $("#drawer");
  drawer.innerHTML = html;
  drawer.hidden = false;
  $("#drawer-backdrop").hidden = false;
  document.body.style.overflow = "hidden";
  $$("[data-close]", drawer).forEach((button) => button.addEventListener("click", closeDrawer));
}

function closeDrawer() {
  $("#drawer").hidden = true;
  $("#drawer-backdrop").hidden = true;
  document.body.style.overflow = "";
}

function showTokenPrompt() {
  openDrawer(`
    <div class="drawer-head"><div><h2>Admin token required</h2><p>The API is protected by a bearer token.</p></div></div>
    <div class="drawer-body">
      <div class="field">
        <label for="token-input">Token</label>
        <input class="input" type="password" id="token-input" placeholder="paste your admin token" autocomplete="current-password" />
        <span class="hint">Found in <code>~/.hexgate/config.toml</code> under <code>[auth] admin_token</code>.</span>
      </div>
      <button class="btn btn-primary" id="token-save" style="width:100%">Unlock dashboard</button>
    </div>`);
  const save = () => {
    const value = $("#token-input").value.trim();
    if (!value) return;
    state.token = value;
    localStorage.setItem("hexgate.token", value);
    closeDrawer();
    setView(state.view);
  };
  $("#token-save").addEventListener("click", save);
  $("#token-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") save();
  });
  $("#token-input").focus();
}

function codeBlock(text, label) {
  return `<div class="code-block"><button class="copy" data-copy="${esc(text)}">copy ${esc(label || "")}</button>${esc(text)}</div>`;
}

function bindCopies(root) {
  $$("[data-copy]", root).forEach((button) =>
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        toast("Copied to clipboard");
      } catch (error) {
        toast("Copy failed — select the text manually", "err");
      }
    }),
  );
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
function boot() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
  $("#drawer-backdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
  bindCopies(document);

  const initial = (location.hash || "#overview").replace("#", "");
  setView(VIEW_META[initial] ? initial : "overview");

  state.pollTimer = setInterval(() => {
    if (document.visibilityState !== "visible") return;
    if (state.view === "overview") loadOverview(true).catch(() => setHealthy(false, "disconnected"));
    if (state.view === "activity") loadActivity(true).catch(() => {});
  }, 8000);

  window.addEventListener("resize", () => {
    const canvas = $("#traffic-chart");
    if (canvas && state.overview) drawChart(canvas, state.overview.stats.timeline);
  });

  api("/healthz")
    .then(() => setHealthy(true, "gateway online"))
    .catch(() => setHealthy(false, "unreachable"));
}

boot();
