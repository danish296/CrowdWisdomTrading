// CRYPTO·ADS dashboard front-end. Vanilla JS, no framework, intentionally
// terse — the editorial design carries the weight, not the JS.

const $ = (sel) => document.querySelector(sel);

const fmtUsd = (n) =>
  (n >= 0 ? "+$" : "-$") + Math.abs(Number(n)).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

const fmtPct = (n) => (Number(n) * 100).toFixed(1) + "%";

const fmtTime = (ts) =>
  new Date(typeof ts === "number" ? ts * 1000 : ts).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

function setDateline() {
  const d = new Date();
  $("#dateline").textContent = d.toUTCString().replace("GMT", "UTC");
}

function modelBadge(name) {
  // Pretty + colour-coded chip for the prediction model.
  const m = String(name || "?").toLowerCase();
  let cls = "model-other";
  let label = name || "?";
  if (m.startsWith("kronos")) {
    cls = "model-kronos";
    label = name; // already e.g. "kronos-small"
  } else if (m.includes("markov") || m.includes("ema") || m === "statistical" || m.includes("fallback")) {
    cls = "model-stat";
    label = "statistical";
  } else if (m === "insufficient-data") {
    cls = "model-warn";
    label = "no-data";
  }
  return `<span class="model-chip ${cls}" title="${escapeHtml(name || '')}">${escapeHtml(label)}</span>`;
}

function renderDecisions(cycle) {
  const root = $("#decisions");
  root.innerHTML = "";

  const decisions = (cycle && cycle.decisions) || [];
  if (!decisions.length) {
    root.innerHTML = `<div class="empty">No actionable signals this cycle. The desk waits.</div>`;
    return;
  }
  for (const d of decisions) {
    const evClass = d.expected_value_usd > 0 ? "pos" : d.expected_value_usd < 0 ? "neg" : "";
    const pred = d.prediction || {};
    const horizon = pred.horizon_minutes ? `${pred.horizon_minutes}m` : "?";
    const probUp = typeof pred.prob_up === "number" ? (pred.prob_up * 100).toFixed(1) + "%" : "—";
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <div class="card-head">
        <span class="venue-tag">${d.market.venue} · ${d.market.asset}</span>
        <span class="side ${d.side}">${d.side}</span>
      </div>
      <p class="q"><span class="asset">${d.market.asset}/USD</span> — ${escapeHtml(d.market.question)}</p>
      <div class="card-pred">
        ${modelBadge(pred.model_name)}
        <span class="pred-meta mono">h=${horizon} · P(up)=${probUp} · ${escapeHtml(pred.direction || "?")}</span>
      </div>
      <div class="metrics">
        <div class="metric"><span class="metric-label">edge</span>
          <span class="metric-val ${d.edge >= 0 ? 'pos' : 'neg'}">${(d.edge * 100).toFixed(2)}%</span></div>
        <div class="metric"><span class="metric-label">stake</span>
          <span class="metric-val">$${Number(d.stake_usd).toFixed(2)}</span></div>
        <div class="metric"><span class="metric-label">EV</span>
          <span class="metric-val ${evClass}">${fmtUsd(d.expected_value_usd)}</span></div>
      </div>
      <div class="notes">${escapeHtml(d.notes || "")}</div>
    `;
    root.appendChild(card);
  }
}

function setSelectToKnownOption(selectEl, value) {
  if (!selectEl) return;
  const want = value || "auto";
  const has = Array.from(selectEl.options).some((o) => o.value === want);
  selectEl.value = has ? want : "auto";
}

function renderPredictorKpi(pred) {
  if (!pred) return;
  const status = $("#kpi-predictor");
  const sel = $("#predictor-switch");
  if (status) {
    const isKronos =
      pred.active.startsWith("kronos") || pred.active.startsWith("ensemble:kronos");
    const live = isKronos || pred.kronos_available;
    status.textContent = `${live ? "●" : "○"} ${pred.active}`;
    status.classList.toggle("live", live);
    status.classList.toggle("off", !live);
  }
  const card = $("#kpi-predictor-card");
  if (card) card.title = pred.reason || "";

  if (sel) {
    // Refresh option availability every poll: Kronos may have just been
    // installed (or removed) on disk.
    const opts = pred.options || [];
    if (opts.length && sel.dataset.signature !== JSON.stringify(opts)) {
      sel.innerHTML = "";
      for (const opt of opts) {
        const o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label + (opt.available ? "" : " (install kronos)");
        if (!opt.available) o.disabled = true;
        sel.appendChild(o);
      }
      sel.dataset.signature = JSON.stringify(opts);
    }
    // `effective` can be e.g. "kronos" or "stat" but the <select> only has
    // kronos-* / statistical — use `select_value` (server-normalised) or
    // the closed control stays blank in Chrome / Edge.
    if (document.activeElement !== sel) {
      const v = pred.select_value != null && pred.select_value !== ""
        ? pred.select_value
        : pred.effective;
      setSelectToKnownOption(sel, v);
    }
  }
}

async function switchPredictor(value) {
  const sel = $("#predictor-switch");
  if (sel) sel.disabled = true;
  try {
    const res = await fetch("/api/predictor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (_) {}
      alert("Could not switch predictor: " + msg);
      // Re-pull the real state so the dropdown snaps back.
      const cur = await (await fetch("/api/predictor")).json();
      renderPredictorKpi(cur);
      return;
    }
    const next = await res.json();
    renderPredictorKpi(next);
  } catch (e) {
    alert("Network error switching predictor: " + e);
  } finally {
    if (sel) sel.disabled = false;
  }
}

function renderStats(stats) {
  $("#stat-hit").textContent = fmtPct(stats.hit_rate || 0);
  $("#stat-pnl").textContent = fmtUsd(stats.total_pnl_usd || 0);
  $("#stat-n").textContent   = String(stats.n_feedback || 0);
}

function renderFeedback(cycle) {
  if (cycle && cycle.feedback) {
    $("#feedback-q").textContent = "“" + cycle.feedback + "”";
  }
}

function describeEvent(ev) {
  // Pull structured fields out of audit records so the ticker tells a story
  // instead of just printing the event name.
  switch (ev.event) {
    case "prediction":
    case "prediction_leg": {
      const tag = ev.event === "prediction_leg" ? "leg" : "prediction";
      return `<span class="ev">${tag}</span>`
        + ` · <span class="mono">${escapeHtml(ev.asset || "?")}@${ev.horizon || "?"}m</span>`
        + ` · <span class="ev-dir ${escapeHtml(String(ev.direction || "").toLowerCase())}">${escapeHtml(ev.direction || "?")}</span>`
        + ` · p=${typeof ev.prob_up === "number" ? ev.prob_up.toFixed(3) : "?"}`
        + ` · ${modelBadge(ev.model)}`;
    }
    case "prediction_leg_error":
      return `<span class="ev">leg-error</span>`
        + ` · <span class="mono">${escapeHtml(ev.asset || "?")}@${ev.horizon || "?"}m</span>`
        + ` · ${escapeHtml(ev.leg || "?")}`
        + ` · <span class="ev-dir down">${escapeHtml(ev.error || "")}</span>`;
    case "decision":
      return `<span class="ev">${escapeHtml(ev.event)}</span>`
        + ` · <span class="mono">${escapeHtml(ev.asset || "?")}</span>`
        + ` · ${escapeHtml(ev.side || "?")} edge=${typeof ev.edge === "number" ? (ev.edge * 100).toFixed(2) + "%" : "?"}`;
    case "cycle_done":
      return `<span class="ev">${escapeHtml(ev.event)}</span>`
        + ` · ${ev.n_decisions || 0} signals · ${ev.n_errors || 0} errors`;
    default:
      return `<span class="ev">${escapeHtml(ev.event || "?")}</span>`;
  }
}

function renderTicker(audit) {
  const ul = $("#ticker");
  ul.innerHTML = "";
  for (const ev of audit.slice().reverse()) {
    const li = document.createElement("li");
    li.innerHTML = `<time>${fmtTime(ev.ts)}</time>${describeEvent(ev)}`;
    ul.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function refresh() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok) return;
    const data = await res.json();
    renderStats(data.stats);
    renderPredictorKpi(data.predictor);
    renderDecisions(data.latest_cycle);
    renderFeedback(data.latest_cycle);
    renderTicker(data.audit_tail || []);
  } catch (e) {
    console.warn("refresh failed", e);
  }
}

async function runCycle() {
  const btn = $("#btn-run");
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "running…";
  try {
    const res = await fetch("/api/run");
    const cycle = await res.json();
    renderDecisions(cycle);
    renderFeedback(cycle);
    await refresh();
  } catch (e) {
    console.error(e);
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

setDateline();
setInterval(setDateline, 1000);
$("#btn-run").addEventListener("click", runCycle);

const predSwitch = $("#predictor-switch");
if (predSwitch) {
  predSwitch.addEventListener("change", (ev) => switchPredictor(ev.target.value));
}

refresh();
setInterval(refresh, 7000);
