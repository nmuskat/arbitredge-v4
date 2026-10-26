/*
 * web/static/app.js
 *
 * Pure presentation layer: fetches already-computed data from /api/slate
 * and renders it. No inference, no threshold, no regime logic lives here
 * -- every value shown was decided server-side by the book_need engine.
 */

const AUTO_REFRESH_MS = 90 * 1000;
const DIRECTIONAL = new Set(["STRONG", "MODERATE", "LEAN"]);

let currentSport = "cfb";
let currentFilter = "ALL";
let latestGames = [];
let latestExcluded = [];
let refreshTimer = null;

function fmtPct(v) {
  return v === null || v === undefined ? "—" : `${Math.round(v)}th pct`;
}

function fmtDirection(direction, team, magnitudePct, magnitudeUndefined) {
  if (!direction || direction === "even") return "Even / N/A";
  if (magnitudeUndefined) return `${team} (undefined — degenerate slate)`;
  return `${team} (${fmtPct(magnitudePct)})`;
}

let activeSlateController = null;
let slateRequestId = 0;

async function fetchSlate() {
  const requestedSport = currentSport;
  const requestId = ++slateRequestId;

  if (activeSlateController) {
    activeSlateController.abort();
  }
  const controller = new AbortController();
  activeSlateController = controller;

  const btn = document.getElementById("refresh-btn");
  btn.classList.add("spinning");
  const banner = document.getElementById("error-banner");

  try {
    const res = await fetch(`/api/slate?sport=${requestedSport}`, {
      signal: controller.signal
    });
    const data = await res.json();

    if (requestId !== slateRequestId || requestedSport !== currentSport) {
      return;
    }

    if (!res.ok) {
      showError(data.error || `Request failed (${res.status})`);
      return;
    }

    banner.classList.add("hidden");
    latestGames = data.games;
    latestExcluded = data.excluded;
    renderSummary(data.counts);
    renderAsOf(data.as_of_central, data.cache_age_seconds);
    renderGames();
  } catch (err) {
    if (err.name !== "AbortError" && requestId === slateRequestId) {
      showError(`Could not reach the ArbitrEdge backend: ${err}`);
    }
  } finally {
    if (requestId === slateRequestId) {
      btn.classList.remove("spinning");
      activeSlateController = null;
    }
  }
}

function showError(msg) {
  const banner = document.getElementById("error-banner");
  banner.textContent = `⚠ ${msg} — showing the last successfully loaded data, if any.`;
  banner.classList.remove("hidden");
}

function renderAsOf(asOfCentral, cacheAge) {
  document.getElementById("as-of-label").textContent =
    `Data as of ${asOfCentral} (${Math.round(cacheAge)}s old)`;
}

function renderSummary(counts) {
  document.getElementById("cnt-upcoming").textContent = counts.upcoming_games;
  document.getElementById("cnt-evaluable").textContent = counts.currently_evaluable;
  document.getElementById("cnt-not-yet").textContent = counts.not_yet_evaluable;
  document.getElementById("cnt-insufficient").textContent = counts.insufficient_evidence;
  document.getElementById("cnt-noclear").textContent = counts.no_clear_need;
  document.getElementById("cnt-directional").textContent = counts.directional_book_needs;
  document.getElementById("cnt-excluded").textContent = counts.excluded;
}

function renderExcludedPanel() {
  const panel = document.getElementById("excluded-panel");
  if (latestExcluded.length === 0) {
    panel.innerHTML = `<div>No games are currently excluded by eligibility.</div>`;
    return;
  }
  panel.innerHTML = latestExcluded.map(e =>
    `<div class="excluded-row"><span class="excluded-status">${e.status}</span>${e.away_team} @ ${e.home_team} — ${e.reason}</div>`
  ).join("");
}

function matchesFilter(game) {
  if (currentFilter === "ALL") return true;
  if (currentFilter === "CANDIDATES") return DIRECTIONAL.has(game.card_state);
  return game.card_state === currentFilter;
}

function renderGames() {
  const grid = document.getElementById("game-grid");
  grid.innerHTML = "";
  const filtered = latestGames.filter(matchesFilter);

  if (filtered.length === 0) {
    grid.innerHTML = `<div class="loading">No games match this filter right now.</div>`;
    return;
  }

  const template = document.getElementById("game-card-template");
  for (const game of filtered) {
    const node = template.content.cloneNode(true);
    const card = node.querySelector(".game-card");

    node.querySelector(".matchup").textContent = `${game.away_team} @ ${game.home_team}`;
    node.querySelector(".kickoff").textContent = game.kickoff_central;

    const badge = node.querySelector(".card-state-badge");
    badge.textContent = game.card_state.replace(/_/g, " ");
    badge.classList.add(game.card_state);

    const needSide = node.querySelector(".need-side");
    const signalSpread = game.signal_spread == null
      ? ""
      : ` ${game.signal_spread > 0 ? "+" : ""}${game.signal_spread}`;

    if (DIRECTIONAL.has(game.card_state)) {
      needSide.textContent = `${game.inferred_book_need_team}${signalSpread}`;
    } else {
      needSide.textContent = game.card_state.replace(/_/g, " ");
      needSide.classList.add("non-directional");
    }

    node.querySelector(".need-strength-pill").textContent = game.need_strength || "—";
    node.querySelector(".data-quality-pill").textContent = `Data: ${game.data_quality}`;

    node.querySelector(".hi-val").textContent = fmtPct(game.handle_intensity_percentile);
    node.querySelector(".sp-val").textContent = fmtDirection(
      game.side_preference_direction, game.side_preference_team,
      game.side_preference_magnitude_percentile, game.side_preference_magnitude_undefined
    );
    node.querySelector(".regime-val").textContent = game.current_regime || "—";
    node.querySelector(".segment-val").textContent = game.governing_segment
      ? `${game.governing_segment}`
      : (game.card_state === "NOT_YET_EVALUABLE" ? "Not started" : "—");

    node.querySelector(".why-text").textContent = game.why;
    node.querySelector(".as-of-mini").textContent = `as_of: ${game.as_of}`;

    buildExpanded(node, game);

    card.addEventListener("click", () => {
      card.querySelector(".card-expanded").classList.toggle("hidden");
    });

    grid.appendChild(node);
  }
}

function buildExpanded(node, game) {
  const signalSpread = game.signal_spread == null
    ? ""
    : ` ${game.signal_spread > 0 ? "+" : ""}${game.signal_spread}`;

  const pressureDetail = node.querySelector(".pressure-detail");
  pressureDetail.innerHTML = `
    <div class="k">Handle Intensity</div><div class="v">${fmtPct(game.handle_intensity_percentile)}</div>
    <div class="k">Recreational Lean</div><div class="v">${fmtDirection(game.side_preference_direction, game.side_preference_team, game.side_preference_magnitude_percentile, game.side_preference_magnitude_undefined)}</div>
  `;

  const syntheticSection = node.querySelector(".synthetic-splits-section");
  const syntheticDetail = node.querySelector(".synthetic-splits-detail");
  const sportsbook = game.synthetic_sportsbook;
  const ledger = sportsbook?.synthetic_bettor_ledger;

  if ((game.sport === "nfl" || game.sport === "cfb") && sportsbook) {
    syntheticSection.classList.remove("hidden");

    if (ledger) {
      const bookNeedTeam = sportsbook.book_need_side === "home"
        ? game.home_team
        : (sportsbook.book_need_side === "away" ? game.away_team : "—");

      const money = (v) => v == null
        ? "—"
        : new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: "USD",
            maximumFractionDigits: 0
          }).format(v);

      syntheticDetail.innerHTML = `
        <div class="k">${game.away_team} Tickets</div><div class="v">${ledger.away_ticket_pct == null ? "—" : ledger.away_ticket_pct.toFixed(1) + "%"}</div>
        <div class="k">${game.home_team} Tickets</div><div class="v">${ledger.home_ticket_pct == null ? "—" : ledger.home_ticket_pct.toFixed(1) + "%"}</div>
        <div class="k">${game.away_team} Money</div><div class="v">${ledger.away_money_pct == null ? "—" : ledger.away_money_pct.toFixed(1) + "%"}</div>
        <div class="k">${game.home_team} Money</div><div class="v">${ledger.home_money_pct == null ? "—" : ledger.home_money_pct.toFixed(1) + "%"}</div>
        <div class="k">Synthetic Tickets</div><div class="v">${ledger.total_tickets ?? "—"}</div>
        <div class="k">Synthetic Handle</div><div class="v">${money(ledger.total_handle)}</div>
        <div class="k">P&L if ${game.away_team} Covers</div><div class="v">${money(ledger.pnl_if_away_covers)}</div>
        <div class="k">P&L if ${game.home_team} Covers</div><div class="v">${money(ledger.pnl_if_home_covers)}</div>
        <div class="k">P&L Swing</div><div class="v">${money(ledger.pnl_swing)}</div>
        <div class="k">Modeled Need</div><div class="v">${bookNeedTeam}</div>
        <div class="k">Decision</div><div class="v">${sportsbook.decision || "—"}</div>
      `;
    } else {
      syntheticDetail.innerHTML = `
        <div class="k">Modeled Side Split</div><div class="v">Unavailable — no directional public-pressure signal</div>
        <div class="k">Decision</div><div class="v">${sportsbook.decision || "PASS"}</div>
      `;
    }
  }

  // Phase 2B: V3 totals are simulated shadow exposure, not real book liability.
  const totals = game.synthetic_v3_totals;
  if (totals && totals.total_points != null) {
    syntheticSection.classList.remove("hidden");

    const dollars = (v) => v == null ? "—" :
      new Intl.NumberFormat("en-US", {
        style: "currency", currency: "USD", maximumFractionDigits: 0
      }).format(v);
    const percent = (v) => v == null ? "—" : `${Number(v).toFixed(1)}%`;

    const totalsBlock = document.createElement("div");
    totalsBlock.style.gridColumn = "1 / -1";
    totalsBlock.style.width = "100%";
    totalsBlock.innerHTML = `
      <div class="seg-header">V3 TOTALS — SIMULATED SHADOW</div>
      <div class="k">Offered Total</div><div class="v">${totals.total_points}</div>
      <div class="k">Over Tickets</div><div class="v">${percent(totals.over_ticket_pct)}</div>
      <div class="k">Under Tickets</div><div class="v">${percent(totals.under_ticket_pct)}</div>
      <div class="k">Over Money</div><div class="v">${percent(totals.over_money_pct)}</div>
      <div class="k">Under Money</div><div class="v">${percent(totals.under_money_pct)}</div>
      <div class="k">Total Tickets</div><div class="v">${totals.total_tickets}</div>
      <div class="k">Simulated Handle</div><div class="v">${dollars(totals.total_handle)}</div>
      <div class="k">P&amp;L if Over</div><div class="v">${dollars(totals.pnl_if_over)}</div>
      <div class="k">P&amp;L if Under</div><div class="v">${dollars(totals.pnl_if_under)}</div>
      <div class="k">P&amp;L Swing</div><div class="v">${dollars(totals.pnl_swing)}</div>
      <div class="k">Modeled Preference</div><div class="v">${
        totals.total_tickets === 0 ? "No accepted wagers" :
        totals.ledger_implied_need_tie ? "Tie" :
        (totals.ledger_implied_need_side || "—").toUpperCase()
      }</div>
      <div class="k">Push Possible</div><div class="v">${totals.push_possible ? "Yes" : "No"}</div>
      <div class="k">Exposure Type</div><div class="v">Synthetic only — not verified sportsbook liability</div>
    `;
    syntheticDetail.appendChild(totalsBlock);
  }

  const segDetail = node.querySelector(".segments-detail");
  segDetail.innerHTML = "";
  for (const seg of game.segments) {
    const block = document.createElement("div");
    block.className = "segment-block";
    let html = `<div class="seg-header">Segment ${seg.name}: ${seg.status}`
      + (seg.aggregate_regime ? ` — ${seg.aggregate_regime} (${seg.breadth})` : "") + "</div>";
    for (const [book, r] of Object.entries(seg.per_book || {})) {
      if (r.regime === "INSUFFICIENT") {
        html += `<div class="book-row">${book}: insufficient (${r.reason || "n/a"})</div>`;
      } else {
        const flags = [];
        if (r.contradiction) flags.push("contradiction");
        if (r.intra_segment_reversal) flags.push("reversal");
        html += `<div class="book-row">${book}: ${r.regime}${r.key_number_state ? " · " + r.key_number_state : ""}${flags.length ? " · " + flags.join(", ") : ""}</div>`;
      }
    }
    block.innerHTML = html;
    segDetail.appendChild(block);
  }

  const needDetail = node.querySelector(".need-detail");
  needDetail.innerHTML = `
    <div class="k">Side</div><div class="v">${DIRECTIONAL.has(game.card_state) ? `${game.inferred_book_need_team}${signalSpread}` : game.card_state.replace(/_/g, " ")}</div>
    <div class="k">Need Strength</div><div class="v">${game.need_strength || "—"}</div>
    <div class="k">Data Quality</div><div class="v">${game.data_quality}</div>
    <div class="k">Contradiction Flag</div><div class="v">${game.contradiction_flag}</div>
    <div class="k">Fallback Occurred</div><div class="v">${game.fallback_occurred}</div>
  `;
}

function setupControls() {
  document.getElementById("sport-toggle").addEventListener("click", (e) => {
    const btn = e.target.closest(".toggle-btn");
    if (!btn) return;
    document.querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentSport = btn.dataset.sport;
    fetchSlate();
  });

  document.getElementById("filter-bar").addEventListener("click", (e) => {
    const btn = e.target.closest(".filter-btn");
    if (!btn) return;
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.dataset.filter;
    renderGames();
  });

  document.getElementById("refresh-btn").addEventListener("click", fetchSlate);

  document.getElementById("cnt-excluded-wrap").addEventListener("click", () => {
    const panel = document.getElementById("excluded-panel");
    const willShow = panel.classList.contains("hidden");
    if (willShow) renderExcludedPanel();
    panel.classList.toggle("hidden");
  });
}

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(fetchSlate, AUTO_REFRESH_MS);
}

setupControls();
fetchSlate();
startAutoRefresh();
