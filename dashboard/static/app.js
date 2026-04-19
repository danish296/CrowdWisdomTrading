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
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <div class="card-head">
        <span class="venue-tag">${d.market.venue} · ${d.market.asset}</span>
        <span class="side ${d.side}">${d.side}</span>
      </div>
      <p class="q"><span class="asset">${d.market.asset}/USD</span> — ${escapeHtml(d.market.question)}</p>
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

function renderTicker(audit) {
  const ul = $("#ticker");
  ul.innerHTML = "";
  for (const ev of audit.slice().reverse()) {
    const li = document.createElement("li");
    li.innerHTML = `<time>${fmtTime(ev.ts)}</time><span class="ev">${escapeHtml(ev.event)}</span>`;
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
refresh();
setInterval(refresh, 7000);
