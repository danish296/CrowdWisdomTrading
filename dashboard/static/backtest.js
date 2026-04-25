// Backtest workbench front-end. Vanilla JS + Chart.js.
// Talks to /api/backtest/* endpoints in api.py.

console.log("[backtest.js] script loaded at", new Date().toISOString());

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

// ----------------- visible error reporting -----------------
// If anything in this script blows up, surface it in the UI instead
// of silently letting the browser fall back to a native form GET.
function showBanner(kind, msg) {
  const el = document.getElementById("bt-error");
  if (!el) {
    console.error("[backtest.js]", kind, msg);
    return;
  }
  el.hidden = false;
  el.innerHTML = `<strong>${kind}</strong>\n${String(msg)}`;
}

window.addEventListener("error", (ev) => {
  showBanner("script error", `${ev.message}\n${ev.filename}:${ev.lineno}:${ev.colno}`);
});
window.addEventListener("unhandledrejection", (ev) => {
  showBanner("promise error", String(ev.reason && ev.reason.stack ? ev.reason.stack : ev.reason));
});

const PALETTE = [
  "#161616",   // ink
  "#f5b400",   // amber
  "#2e7d32",   // green
  "#b3261e",   // red
  "#4fc3f7",   // sky
  "#9c27b0",   // violet
];

let charts = {};
let activeRunId = null;
let pollTimer = null;

function setDateline() {
  const el = $("#dateline");
  if (!el) return;
  el.textContent = new Date().toUTCString().replace("GMT", "UTC");
}

function fmtTime(ts) {
  const ms = (typeof ts === "number" ? ts * 1000 : Date.parse(ts));
  const d = new Date(ms);
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtNum(n, digits = 3) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (typeof n !== "number") n = Number(n);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtUsd(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n >= 0 ? "+$" : "-$";
  return sign + Math.abs(Number(n)).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// ----------------- form submission -----------------

async function submitForm(ev) {
  if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
  if (ev && typeof ev.stopPropagation === "function") ev.stopPropagation();
  console.log("[backtest.js] submitForm fired", ev && ev.type);

  const banner = document.getElementById("bt-error");
  if (banner) banner.hidden = true;

  const form = $("#bt-form");
  if (!form) {
    showBanner("form missing", "#bt-form was not found in the DOM");
    return false;
  }
  const fd = new FormData(form);

  const models = $$('input[name="models"]:checked').map((i) => i.value);
  if (models.length === 0) {
    showBanner("input error", "Pick at least one model checkbox to compare.");
    return false;
  }

  const body = {
    asset: fd.get("asset"),
    interval: fd.get("interval"),
    horizon_minutes: Number(fd.get("horizon_minutes")),
    days: Number(fd.get("days")),
    warmup_bars: Number(fd.get("warmup_bars")),
    step_bars: Number(fd.get("step_bars")),
    source: fd.get("source"),
    models,
    market_price_anchor: Number(fd.get("market_price_anchor")),
    market_price_noise: Number(fd.get("market_price_noise")),
    label: fd.get("label") || "",
    seed: 42,
  };
  console.log("[backtest.js] POST /api/backtest/run", body);

  const btn = $("#bt-run");
  if (btn) { btn.disabled = true; btn.textContent = "running…"; }

  try {
    const res = await fetch("/api/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (_) { data = {}; }
    if (!res.ok) {
      showBanner("server " + res.status, data.detail || res.statusText || "request failed");
      if (btn) { btn.disabled = false; btn.textContent = "Run backtest ↗"; }
      return false;
    }
    showProgress();
    pollJob(data.job_id);
  } catch (e) {
    showBanner("network error", e && e.stack ? e.stack : String(e));
    if (btn) { btn.disabled = false; btn.textContent = "Run backtest ↗"; }
  }
  return false;
}

function showProgress() {
  $("#bt-progress").hidden = false;
  $("#bt-bar-fill").style.width = "0%";
  $("#bt-progress-msg").textContent = "queued…";
}

function setProgress(pct, msg) {
  $("#bt-bar-fill").style.width = (pct * 100).toFixed(1) + "%";
  $("#bt-progress-msg").textContent = msg || "";
}

async function pollJob(jobId) {
  console.log("[backtest.js] pollJob start", jobId);
  if (pollTimer) clearInterval(pollTimer);
  const start = Date.now();
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/backtest/jobs/${jobId}`);
      if (!res.ok) return;
      const job = await res.json();
      setProgress(job.progress || 0, job.message || job.status);
      if (job.status === "done" || job.status === "error") {
        clearInterval(pollTimer); pollTimer = null;
        const btn = $("#bt-run");
        if (btn) { btn.disabled = false; btn.textContent = "Run backtest ↗"; }
        if (job.status === "error") {
          $("#bt-progress-msg").textContent = "error: " + job.message;
          showBanner("backtest error", job.message || "(no message)");
          return;
        }
        $("#bt-progress-msg").textContent =
          `done in ${((Date.now() - start) / 1000).toFixed(1)}s`;
        await refreshRuns();
        if (job.result_id) loadRun(job.result_id);
      }
    } catch (e) {
      console.warn("poll tick failed", e);
    }
  }, 700);
}

// ----------------- run history -----------------

async function refreshRuns() {
  try {
    const res = await fetch("/api/backtest/list");
    const data = await res.json();
    const ul = $("#bt-runs");
    ul.innerHTML = "";
    if (!data.runs || data.runs.length === 0) {
      ul.innerHTML = `<li style="border-style:dashed;color:var(--muted);">No runs yet.</li>`;
      return;
    }
    for (const r of data.runs) {
      const li = document.createElement("li");
      if (r.id === activeRunId) li.classList.add("active");
      const spec = r.spec || {};
      const models = (spec.models || []).join(", ");
      const label = spec.label ? ` — ${escapeHtml(spec.label)}` : "";
      li.innerHTML = `
        <div class="run-line">${escapeHtml(spec.asset || "?")} · ${escapeHtml(spec.interval || "?")} · ${spec.days || "?"}d · h=${spec.horizon_minutes || "?"}m</div>
        <div class="run-name">${escapeHtml(models)}${label}</div>
        <div class="run-line">${r.started_at_iso || ""} · ${r.elapsed_s || 0}s</div>
      `;
      li.addEventListener("click", () => loadRun(r.id));
      ul.appendChild(li);
    }
  } catch (e) {
    console.warn("refreshRuns failed", e);
  }
}

async function loadRun(runId) {
  activeRunId = runId;
  await refreshRuns();
  try {
    const res = await fetch(`/api/backtest/${runId}`);
    if (!res.ok) {
      alert("Run not found: " + runId);
      return;
    }
    const payload = await res.json();
    renderReport(payload);
  } catch (e) {
    alert("Load failed: " + e);
  }
}

// ----------------- report rendering -----------------

function renderReport(payload) {
  console.log("[backtest.js] renderReport", payload && payload.id);
  $("#bt-empty").hidden = true;
  $("#bt-report").hidden = false;

  const spec = payload.spec || {};
  const title = spec.label
    ? spec.label
    : `${spec.asset} · ${spec.interval} · h=${spec.horizon_minutes}m`;
  $("#bt-title").textContent = title;
  $("#bt-meta").textContent =
    `id=${payload.id} · ${payload.n_bars} real bars from ${spec.source} · `
    + `warmup=${spec.warmup_bars} · step=${spec.step_bars} · `
    + `started ${payload.started_at_iso} · elapsed ${payload.elapsed_s}s · `
    + `bankroll=$${spec.bankroll_usd} · max-Kelly=${(spec.max_kelly_fraction * 100).toFixed(0)}% · `
    + `min-edge=${(spec.min_edge * 100).toFixed(2)}%`;

  const dl = $("#bt-download");
  dl.href = `/api/backtest/${payload.id}`;
  dl.download = `backtest_${payload.id}.json`;

  $("#bt-delete").onclick = async () => {
    if (!confirm("Delete this backtest run?")) return;
    await fetch(`/api/backtest/${payload.id}`, { method: "DELETE" });
    activeRunId = null;
    $("#bt-report").hidden = true;
    $("#bt-empty").hidden = false;
    await refreshRuns();
  };

  // Render each section independently so one failure (e.g. Chart.js missing)
  // doesn't wipe the rest of the report.
  const safe = (name, fn) => {
    try { fn(payload); } catch (e) {
      console.error(`[backtest.js] ${name} failed`, e);
      showBanner(`${name} failed`, e && e.stack ? e.stack : String(e));
    }
  };
  safe("summary", renderSummary);
  safe("pairwise", renderPairwise);
  safe("equity", renderEquity);
  safe("calibration", renderCalibration);
  safe("track", renderTrack);
}

function renderSummary(payload) {
  const sums = payload.summary_per_model || {};
  const models = Object.keys(sums);
  const t = $("#bt-summary");

  // pick best model by lowest brier as the "winner" highlight
  let winner = null;
  let bestBrier = Infinity;
  for (const m of models) {
    const b = sums[m].brier;
    if (typeof b === "number" && b < bestBrier) { bestBrier = b; winner = m; }
  }

  t.innerHTML = `
    <thead>
      <tr>
        <th>Model</th>
        <th>N</th>
        <th>Trades</th>
        <th>Hit</th>
        <th>Conf-Hit (n)</th>
        <th>Brier ↓</th>
        <th>LogLoss ↓</th>
        <th>PnL $</th>
        <th>Sharpe</th>
        <th>MaxDD %</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  const tbody = t.querySelector("tbody");
  for (const m of models) {
    const s = sums[m];
    const tr = document.createElement("tr");
    if (m === winner) tr.classList.add("win");
    const pnlClass = s.total_pnl_usd > 0 ? "pos" : s.total_pnl_usd < 0 ? "neg" : "";
    tr.innerHTML = `
      <td>${escapeHtml(m)}${m === winner ? " ★" : ""}</td>
      <td>${s.n_predictions || 0}</td>
      <td>${s.n_trades || 0}</td>
      <td>${fmtNum(s.hit_rate, 3)}</td>
      <td>${fmtNum(s.confident_hit_rate, 3)} (${s.confident_n || 0})</td>
      <td>${fmtNum(s.brier, 4)}</td>
      <td>${fmtNum(s.log_loss, 4)}</td>
      <td class="${pnlClass}">${fmtUsd(s.total_pnl_usd)}</td>
      <td>${fmtNum(s.sharpe_annualised, 2)}</td>
      <td>${fmtNum(s.max_drawdown_pct, 2)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderPairwise(payload) {
  const root = $("#bt-pairwise");
  root.innerHTML = "";
  const pw = payload.pairwise || [];
  if (pw.length === 0) {
    root.innerHTML = `<div class="hint">Add a second model to see Diebold-Mariano significance tests.</div>`;
    return;
  }
  for (const p of pw) {
    const div = document.createElement("div");
    div.className = "pw";
    div.innerHTML = `<strong>${escapeHtml(p.a)}</strong> vs <strong>${escapeHtml(p.b)}</strong> — ${escapeHtml(p.interpretation)} · DM=${fmtNum(p.dm_stat, 3)} · n=${p.n}`;
    root.appendChild(div);
  }
}

function ensureChart() {
  if (typeof window.Chart === "undefined") {
    throw new Error("Chart.js failed to load — check the CDN <script> tags in <head> or your network/ad-blocker.");
  }
}

function renderEquity(payload) {
  ensureChart();
  const eqs = payload.equity_per_model || {};
  const datasets = [];
  let i = 0;
  for (const [model, curve] of Object.entries(eqs)) {
    if (!curve || curve.length === 0) continue;
    datasets.push({
      label: model,
      data: curve.map((c) => ({ x: c.ts * 1000, y: c.equity })),
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: PALETTE[i % PALETTE.length] + "22",
      borderWidth: 2, pointRadius: 0, tension: 0.05, fill: false,
    });
    i++;
  }
  destroyChart("equity");
  const ctx = document.getElementById("chart-equity").getContext("2d");
  charts.equity = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false, parsing: false,
      interaction: { mode: "nearest", intersect: false },
      scales: {
        x: { type: "time", time: { unit: "day" }, ticks: { color: "#6b6356" }, grid: { color: "rgba(0,0,0,.06)" } },
        y: { ticks: { color: "#6b6356", callback: (v) => "$" + Number(v).toFixed(0) }, grid: { color: "rgba(0,0,0,.06)" } },
      },
      plugins: {
        legend: { labels: { font: { family: "JetBrains Mono", size: 11 } } },
      },
    },
  });
}

function renderCalibration(payload) {
  ensureChart();
  const sums = payload.summary_per_model || {};
  const datasets = [{
    type: "line",
    label: "perfect calibration",
    data: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
    borderColor: "rgba(0,0,0,.4)", borderDash: [6, 6],
    borderWidth: 1, pointRadius: 0, fill: false,
  }];
  let i = 0;
  for (const [model, s] of Object.entries(sums)) {
    const bins = (s.calibration_bins || []).filter((b) => b.n > 0);
    if (bins.length === 0) continue;
    datasets.push({
      type: "bubble",
      label: model,
      data: bins.map((b) => ({
        x: b.avg_predicted,
        y: b.frequency_up,
        r: 4 + Math.sqrt(b.n),
      })),
      backgroundColor: PALETTE[(i + 1) % PALETTE.length] + "aa",
      borderColor: PALETTE[(i + 1) % PALETTE.length],
    });
    i++;
  }
  destroyChart("calibration");
  const ctx = document.getElementById("chart-calibration").getContext("2d");
  charts.calibration = new Chart(ctx, {
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      scales: {
        x: { min: 0, max: 1, title: { display: true, text: "predicted P(up)", color: "#6b6356" }, ticks: { color: "#6b6356" }, grid: { color: "rgba(0,0,0,.06)" } },
        y: { min: 0, max: 1, title: { display: true, text: "realised frequency of UP", color: "#6b6356" }, ticks: { color: "#6b6356" }, grid: { color: "rgba(0,0,0,.06)" } },
      },
      plugins: { legend: { labels: { font: { family: "JetBrains Mono", size: 11 } } } },
    },
  });
}

function renderTrack(payload) {
  ensureChart();
  const sams = payload.samples_per_model || {};
  const datasets = [];
  let i = 0;
  // To keep things light, downsample to <= 600 dots per model.
  for (const [model, samples] of Object.entries(sams)) {
    if (!samples || samples.length === 0) continue;
    const stride = Math.max(1, Math.ceil(samples.length / 600));
    const view = samples.filter((_, j) => j % stride === 0);
    const color = PALETTE[i % PALETTE.length];
    datasets.push({
      type: "line",
      label: `${model} P(up)`,
      data: view.map((s) => ({ x: s.ts_unix * 1000, y: s.prob_up })),
      borderColor: color,
      borderWidth: 1, pointRadius: 0, tension: 0,
      yAxisID: "y", fill: false,
    });
    datasets.push({
      type: "scatter",
      label: `${model} hits`,
      data: view
        .filter((s) => (s.prob_up > 0.5) === (s.realised_up === 1))
        .map((s) => ({ x: s.ts_unix * 1000, y: s.prob_up })),
      backgroundColor: "rgba(46,125,50,.55)",
      borderColor: "rgba(46,125,50,.8)",
      pointRadius: 2.5, showLine: false, yAxisID: "y",
    });
    i++;
  }
  destroyChart("track");
  const ctx = document.getElementById("chart-track").getContext("2d");
  charts.track = new Chart(ctx, {
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
      interaction: { mode: "nearest", intersect: false },
      scales: {
        x: { type: "time", ticks: { color: "#6b6356" }, grid: { color: "rgba(0,0,0,.06)" } },
        y: { min: 0, max: 1, ticks: { color: "#6b6356" }, grid: { color: "rgba(0,0,0,.06)" } },
      },
      plugins: { legend: { labels: { font: { family: "JetBrains Mono", size: 11 } } } },
    },
  });
}

function destroyChart(name) {
  if (charts[name]) {
    try { charts[name].destroy(); } catch (e) { /* */ }
    charts[name] = null;
  }
}

// ----------------- bootstrap -----------------

function init() {
  console.log("[backtest.js] init() running, readyState=", document.readyState);
  try {
    setDateline();
    setInterval(setDateline, 1000);
  } catch (e) {
    console.warn("dateline failed", e);
  }

  const form = document.getElementById("bt-form");
  const btn = document.getElementById("bt-run");

  if (!form) showBanner("init error", "#bt-form not found in DOM at init time");
  if (!btn) showBanner("init error", "#bt-run button not found in DOM at init time");

  if (form) {
    form.addEventListener("submit", submitForm);
    console.log("[backtest.js] submit handler attached to #bt-form");
  }
  if (btn) {
    // Defensive: also bind directly to the button click. The button is
    // type="button" so it cannot trigger a native form submit even if this
    // handler somehow fails to attach.
    btn.addEventListener("click", (ev) => { submitForm(ev); });
    console.log("[backtest.js] click handler attached to #bt-run");
  }

  try { refreshRuns(); } catch (e) { console.warn("refreshRuns failed", e); }
  console.log("[backtest.js] init() complete");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  // Defer to a microtask so any sync errors above don't kill init().
  Promise.resolve().then(init);
}
