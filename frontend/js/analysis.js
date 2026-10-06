const statusEl = document.querySelector("[data-status]");
const matchCardEl = document.querySelector(".analysis-match-card");
const badgesEl = document.querySelector("[data-match-badges]");
const matchDetailEl = document.querySelector("[data-match-detail]");
const homeTeamLinkEl = document.querySelector("[data-home-team-link]");
const awayTeamLinkEl = document.querySelector("[data-away-team-link]");
const homeCrestEl = document.querySelector("[data-home-crest]");
const awayCrestEl = document.querySelector("[data-away-crest]");
const homeShortEl = document.querySelector("[data-home-short]");
const awayShortEl = document.querySelector("[data-away-short]");
const homeFullEl = document.querySelector("[data-home-full]");
const awayFullEl = document.querySelector("[data-away-full]");
const scoreEl = document.querySelector("[data-match-score]");
const halfTimeEl = document.querySelector("[data-half-time]");
const summaryEl = document.querySelector("[data-summary]");
const eventsEl = document.querySelector("[data-events]");
const commentsEl = document.querySelector("[data-comments]");
const legendEl = document.querySelector("[data-chart-legend]");
const chartTitleEl = document.querySelector(".analysis-chart-card .analysis-card-label");
const chartWrapEl = document.querySelector(".analysis-chart-wrap");
const chartHelpButton = document.querySelector("[data-chart-help-button]");
const chartHelpModal = document.querySelector("[data-chart-help-modal]");
const chartHelpClose = document.querySelector("[data-chart-help-close]");
const backLinkEl = document.querySelector("[data-back-link]");
const params = new URLSearchParams(window.location.search);
let pulseChart;

init();

async function init() {
  setupBackLink();
  setupChartHelp();
  renderAmbientSignal();
  const stored = JSON.parse(sessionStorage.getItem("selectedMatch") || "null");
  if (stored) renderMatchHeader(stored);

  const matchId = params.get("match_id") || stored?.id;
  if (!matchId) {
    setStatus("Choose a match first.");
    return;
  }

  try {
    setStatus("Analysing match...");
    const analysis = await apiPost("/analyse", { match_id: matchId });
    setStatus("");
    renderAnalysis(analysis);
  } catch (error) {
    setStatus(error.message);
  }
}

function renderAnalysis(data) {
  renderMatchHeader(data.match);
  renderSummary(data.meta);
  renderChart(data.reaction_intensity || [], data.meta || {});
  renderEvents(data.events || [], data.match?.event_feed_status);
  renderComments(data.top_comments || [], data.meta.youtube_video_url, data.match?.youtube_comment_status);
}

function renderMatchHeader(match) {
  applyMatchVisuals(match);
  const competitionMarkup = competitionLinkMarkup(match);
  badgesEl.innerHTML = `
    ${competitionMarkup}
    <span class="analysis-pill analysis-pill-muted">${escapeHtml(stageWithSeason(match))}</span>
  `;
  const competitionLink = badgesEl.querySelector("[data-competition-link]");
  if (competitionLink) competitionLink.addEventListener("click", storeCompetitionSourceUrl);
  matchDetailEl.textContent = [formatDate(match.date), match.venue || "Venue unavailable"].filter(Boolean).join(" · ");
  setCrest(homeCrestEl, match.home_crest);
  setCrest(awayCrestEl, match.away_crest);
  setTeamLink(homeTeamLinkEl, match.home_team_id, match.home);
  setTeamLink(awayTeamLinkEl, match.away_team_id, match.away);
  homeShortEl.textContent = displayTeamName(match, "home");
  awayShortEl.textContent = displayTeamName(match, "away");
  homeFullEl.textContent = `${match.home} (H)`;
  awayFullEl.textContent = `${match.away} (A)`;
  scoreEl.textContent = displayScore(match);
  const scoreContext = scoreContextLines(match);
  if (scoreContext.length) {
    halfTimeEl.innerHTML = scoreContext.map((line) => `<span>${escapeHtml(line)}</span>`).join("");
    halfTimeEl.hidden = false;
  } else {
    halfTimeEl.innerHTML = "";
    halfTimeEl.hidden = true;
  }
}

function competitionLinkMarkup(match) {
  const competitionId = Number.parseInt(match.competition_id, 10);
  const seasonYear = Number.parseInt(match.season_year, 10);
  const label = escapeHtml(match.competition || "Competition");
  if (!Number.isInteger(competitionId) || competitionId <= 0 || !Number.isInteger(seasonYear) || seasonYear <= 0) {
    return `<span class="analysis-pill">${label}</span>`;
  }
  const href = `competition.html?competition_id=${encodeURIComponent(competitionId)}&season=${encodeURIComponent(seasonYear)}`;
  return `<a class="analysis-pill analysis-pill-link" href="${href}" data-competition-link>${label}</a>`;
}

function storeCompetitionSourceUrl() {
  sessionStorage.setItem("competitionSourceUrl", window.location.href);
}

function displayScore(match) {
  return [match.score, match.score_note].filter(Boolean).join(" ");
}

function scoreContextLines(match) {
  return [
    match.half_time_score ? `HT ${match.half_time_score.replace(" - ", " - ")}` : "",
    match.penalty_score,
    match.aggregate_score,
  ].filter(Boolean);
}

function stageWithSeason(match) {
  return [match.round || "Stage unavailable", match.season].filter(Boolean).join(" · ");
}

function displayTeamName(match, side) {
  const shortName = match[`${side}_short_name`];
  return shortName || shortTeamName(match[side]);
}

function renderSummary(meta) {
  const peak = meta.peak_window || { hour_start: 0, hour_end: 1 };
  const isCached = meta.analysis_mode === "cached_youtube_sentiment";
  const isUnavailable = meta.analysis_mode === "youtube_unavailable";
  const peakLabel = isUnavailable ? "Unavailable" : `Hours ${peak.hour_start} – ${peak.hour_end}`;
  const peakSubtext = isUnavailable ? reactionUnavailableMessage() : "Strongest reaction window";
  const commentsSubtext = isUnavailable
    ? reactionUnavailableMessage()
    : isCached
      ? "Loaded from saved YouTube analysis"
      : "First 24 hours after full time";
  summaryEl.innerHTML = `
    ${metricCard("Overall vibe", analysisLabel(meta.overall_vibe), analysisSubtext(meta.overall_vibe), true)}
    ${metricCard("Crowd energy", analysisLabel(meta.crowd_energy), analysisSubtext(meta.crowd_energy), true)}
    ${metricCard("Peak reaction window", peakLabel, peakSubtext, false)}
    ${metricCard("Comments analysed", formatNumber(meta.total_comments), commentsSubtext, false)}
  `;
}

function analysisLabel(value) {
  return value?.label || "Unavailable";
}

function analysisSubtext(value) {
  return value?.subtext || "Analysis output unavailable";
}

function metricCard(label, value, subtext, accent) {
  return `
    <article class="analysis-metric">
      <span>${label}</span>
      <strong class="${accent ? "is-accent" : ""}">${escapeHtml(value)}</strong>
      <small>${escapeHtml(subtext || "")}</small>
    </article>
  `;
}

function renderChart(buckets, meta = {}) {
  if (chartTitleEl) {
    chartTitleEl.textContent = "Atmos reaction intensity - 24hrs after full time";
  }
  restoreChartCanvas();
  const ctx = document.querySelector("#pulseChart");
  if (!ctx) return;
  if (pulseChart) pulseChart.destroy();
  if (!window.Chart) {
    showChartFallback("Atmos reaction intensity chart could not load.");
    return;
  }
  if (!buckets.length) {
    showChartFallback(reactionUnavailableMessage());
    return;
  }
  const xMax = 24;
  const xStep = 3;
  const xTitle = "Hours after full time";
  const datasetLabel = "Atmos reaction intensity";
  const yTitle = "Reaction intensity score";

  const peakIndex = buckets.reduce((best, bucket, index) => {
    if (best === -1) return index;
    const current = buckets[best];
    return bucket.intensity > current.intensity ? index : best;
  }, -1);
  renderAmbientSignal(buckets, peakIndex);
  const chartPoints = buckets.map((bucket, index) => ({
    x: bucket.hour_offset,
    y: bucket.intensity,
    bucketIndex: index,
  }));
  const finalBucket = buckets[buckets.length - 1];
  chartPoints.push({
    x: xMax,
    y: finalBucket.intensity,
    bucketIndex: buckets.length - 1,
    terminal: true,
  });

  pulseChart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        {
          label: datasetLabel,
          data: chartPoints,
          borderColor: "#C8FF47",
          backgroundColor: chartFillGradient,
          borderWidth: 2.4,
          pointBackgroundColor: "#C8FF47",
          pointBorderColor: "#C8FF47",
          pointRadius: chartPoints.map((point) => {
            if (point.terminal) return 0;
            return point.bucketIndex === peakIndex ? 6 : 3;
          }),
          pointHoverRadius: chartPoints.map((point) => {
            if (point.terminal) return 0;
            return point.bucketIndex === peakIndex ? 7 : 4;
          }),
          tension: 0.35,
          clip: false,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: {
        padding: {
          top: 8,
          right: 10,
          bottom: 0,
          left: 4,
        },
      },
      interaction: {
        mode: "nearest",
        intersect: false,
      },
      scales: {
        y: {
          min: 0,
          max: 100,
          border: { display: false },
          title: { display: true, text: yTitle, color: "#5d5d66" },
          ticks: { color: "#55555f", font: { size: 11 } },
          grid: { color: "rgba(255, 255, 255, 0.045)" },
        },
        x: {
          type: "linear",
          min: 0,
          max: xMax,
          border: { display: false },
          title: { display: true, text: xTitle, color: "#5d5d66" },
          ticks: {
            color: "#55555f",
            font: { size: 11 },
            stepSize: xStep,
            callback(value) {
              return value === 0 ? "FT" : `${value}h`;
            },
          },
          grid: { color: "rgba(255, 255, 255, 0.04)" },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          padding: {
            top: 10,
            right: 14,
            bottom: 10,
            left: 14,
          },
          boxPadding: 6,
          titleMarginBottom: 8,
          bodySpacing: 5,
          titleFont: {
            size: 13,
            weight: "800",
          },
          bodyFont: {
            size: 13,
            weight: "600",
          },
          callbacks: {
            title(items) {
              const point = items[0]?.raw;
              const bucket = buckets[point?.bucketIndex];
              if (!bucket) return "";
              return intensityWindowLabel(bucket);
            },
            label(context) {
              const bucket = buckets[context.raw.bucketIndex];
              return `Atmos reaction score: ${Math.round(bucket.intensity)} / 100`;
            },
            afterLabel(context) {
              const bucket = buckets[context.raw.bucketIndex];
              const lines = [intensityDataLabel(bucket)];
              if (context.raw.bucketIndex === peakIndex) lines.push("Peak intensity window");
              return lines;
            },
          },
        },
      },
    },
    plugins: [intensityZonePlugin],
  });

  legendEl.innerHTML = `
    ${legendItem("legend-line", "Atmos reaction intensity", "A 0-100 score estimating how strong the post-match reaction was in each window.")}
    ${legendItem("legend-fill-positive", "High intensity zone", "The upper band where reaction volume and/or match events suggest a major spike.")}
    ${legendItem("legend-fill-negative", "Low intensity zone", "The lower band where the reaction was quieter or the match had fewer major signals.")}
    ${legendItem("legend-dot", "Peak window", "The strongest window on the chart for that match.")}
  `;
}

function legendItem(iconClass, label, description) {
  return `<span class="legend-help" title="${escapeAttr(description)}"><i class="${escapeAttr(iconClass)}"></i>${escapeHtml(label)}</span>`;
}

function setupChartHelp() {
  if (!chartHelpButton || !chartHelpModal || !chartHelpClose) return;
  chartHelpButton.addEventListener("click", () => {
    chartHelpModal.hidden = false;
    chartHelpClose.focus();
  });
  chartHelpClose.addEventListener("click", closeChartHelp);
  chartHelpModal.addEventListener("click", (event) => {
    if (event.target === chartHelpModal) closeChartHelp();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !chartHelpModal.hidden) closeChartHelp();
  });
}

function closeChartHelp() {
  if (!chartHelpModal) return;
  chartHelpModal.hidden = true;
  chartHelpButton?.focus();
}

function intensityWindowLabel(bucket) {
  const start = Number(bucket.hour_offset) || 0;
  return start === 0 ? "FT" : `${start}h after FT`;
}

function intensityDataLabel(bucket) {
  const count = formatNumber(bucket.comment_count);
  return `${count} comments analysed`;
}

function showChartFallback(message) {
  if (chartWrapEl) {
    chartWrapEl.innerHTML = `<p class="analysis-empty">${escapeHtml(message)}</p>`;
  }
  if (legendEl) {
    legendEl.innerHTML = "";
  }
}

function restoreChartCanvas() {
  if (!chartWrapEl || chartWrapEl.querySelector("#pulseChart")) return;
  chartWrapEl.innerHTML = `
    <canvas id="pulseChart" role="img" aria-label="Line chart showing Atmos reaction intensity over the 24 hours after full time">
    </canvas>
  `;
}

function applyMatchVisuals(match) {
  if (!matchCardEl || !window.AtmosTeamVisuals || !match) return;
  const home = window.AtmosTeamVisuals.fromServer(match.home_visual, match.home_team_id, match.home);
  const away = window.AtmosTeamVisuals.fromServer(match.away_visual, match.away_team_id, match.away);
  matchCardEl.style.setProperty("--home-primary", home.primary);
  matchCardEl.style.setProperty("--home-glow", home.glow);
  matchCardEl.style.setProperty("--home-soft-glow", home.softGlow);
  matchCardEl.style.setProperty("--away-primary", away.primary);
  matchCardEl.style.setProperty("--away-glow", away.glow);
  matchCardEl.style.setProperty("--away-soft-glow", away.softGlow);
  setCrestVisual(homeTeamLinkEl, home);
  setCrestVisual(awayTeamLinkEl, away);
}

function renderAmbientSignal(buckets = [], peakIndex = null) {
  if (!window.AtmosAmbientSignal) return;

  window.AtmosAmbientSignal.render({
    mode: "signal",
    variant: "analysis",
    color: "var(--accent)",
    buckets,
    peakIndex,
  });
}

function setCrestVisual(target, visual) {
  if (!target) return;
  target.style.setProperty("--team-primary", visual.primary);
  target.style.setProperty("--team-glow", visual.glow);
  target.style.setProperty("--team-border", visual.border);
  target.style.setProperty("--team-shadow", visual.shadow);
}

function chartFillGradient(context) {
  const { chart } = context;
  const { ctx, chartArea } = chart;
  if (!chartArea) return "rgba(200, 255, 71, 0.16)";
  const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
  gradient.addColorStop(0, "rgba(200, 255, 71, 0.34)");
  gradient.addColorStop(0.42, "rgba(200, 255, 71, 0.14)");
  gradient.addColorStop(1, "rgba(200, 255, 71, 0)");
  return gradient;
}

const intensityZonePlugin = {
  id: "intensityZone",
  beforeDatasetsDraw(chart) {
    const { ctx, chartArea, scales } = chart;
    if (!chartArea) return;
    const midY = scales.y.getPixelForValue(50);
    ctx.save();
    ctx.fillStyle = "rgba(80, 80, 88, 0.08)";
    ctx.fillRect(chartArea.left, midY, chartArea.right - chartArea.left, chartArea.bottom - midY);
    ctx.restore();
  },
};

function renderEvents(items, status = "unchecked") {
  const fallback = eventTimelineMessage(status);
  eventsEl.innerHTML = items.length
    ? items.map((event) => `
      <li>
        <strong>${escapeHtml(event.display_minute || `${event.minute}'`)}</strong>
        <span>${escapeHtml(event.description)}</span>
        <em class="event-pill event-pill-${event.type}">${escapeHtml(eventLabel(event.type))}</em>
      </li>
    `).join("")
    : `<li class="analysis-empty">${escapeHtml(fallback)}</li>`;
}

function eventTimelineMessage(status = "unchecked") {
  if (status === "unavailable") return "Match event timeline is unavailable from API-Football.";
  if (status === "failed") return "Match event data could not be refreshed right now.";
  if (status === "pending") return "Match event timeline is still syncing. Try again shortly.";
  return "Match event timeline has not been fetched yet. Try again shortly.";
}

function reactionUnavailableMessage() {
  return "24-hour reaction data unavailable for this match.";
}

function renderComments(items, videoUrl, status = "unchecked") {
  commentsEl.innerHTML = items.length
    ? items.slice(0, 3).map((item) => {
      const sourceUrl = item.source_url || videoUrl;
      return `
      <article class="analysis-comment">
        <span>↑ ${formatNumber(item.score)} likes</span>
        <p>"${escapeHtml(item.text)}"</p>
        ${item.source_label ? `<small>From ${escapeHtml(item.source_label)}</small>` : ""}
        ${sourceUrl ? `<a href="${sourceUrl}" target="_blank" rel="noreferrer">View source video ↗</a>` : ""}
      </article>
    `;
    }).join("")
    : `<p class="analysis-empty">${escapeHtml(emptyCommentMessage(status))}</p>`;
}

function emptyCommentMessage(status) {
  if (status === "rate_limited") return "YouTube comment analysis is temporarily rate limited.";
  if (status === "unavailable") return "No suitable YouTube highlight videos found.";
  if (status === "no_comments") return "No public comments found in selected highlight videos.";
  if (status === "failed") return "YouTube comment analysis is unavailable right now.";
  return "No usable YouTube comments found for this match.";
}

function setCrest(target, src) {
  if (!src) {
    target.removeAttribute("src");
    target.hidden = true;
    return;
  }
  target.hidden = false;
  target.src = src;
  target.onerror = () => { target.hidden = true; };
}

function setTeamLink(target, teamId, teamName) {
  if (!target) return;
  if (!teamId) {
    target.removeAttribute("href");
    target.removeAttribute("title");
    target.setAttribute("aria-disabled", "true");
    target.onclick = null;
    return;
  }
  target.href = `team.html?team_id=${encodeURIComponent(teamId)}`;
  target.title = `Open ${teamName || "team"} profile`;
  target.removeAttribute("aria-disabled");
  target.onclick = storeAnalysisReturnUrl;
}

function storeAnalysisReturnUrl() {
  sessionStorage.setItem("analysisReturnUrl", window.location.href);
}

// will add more to this as the list expands
function shortTeamName(name) {
  const clean = String(name || "").trim();
  const replacements = {
    "ACF Fiorentina": "Fiorentina",
    "Athletic Club": "Athletic Bilbao",
    "Bayer 04 Leverkusen": "Leverkusen",
    "Brighton & Hove Albion": "Brighton",
    "Borussia Mönchengladbach": "Gladbach",
    "Cagliari Calcio": "Cagliari",
    "Club Atlético de Madrid": "Atlético Madrid",
    "Club Atletico de Madrid": "Atlético Madrid",
    "Deportivo Alavés": "Alavés",
    "FC Bayern München": "Bayern Munich",
    "FC Internazionale Milano": "Inter Milan",
    "FC Nantes": "Nantes",
    "Feyenoord Rotterdam": "Feyenoord",
    "Genoa CFC": "Genoa",
    "Hellas Verona FC": "Hellas Verona",
    "Internazionale": "Inter Milan",
    "Leeds United": "Leeds",
    "Newcastle United": "Newcastle",
    "Olympique Lyon": "Lyon",
    "Olympique Lyonnais": "Lyon",
    "Olympique de Marseille": "Marseille",
    "Paris Saint-Germain FC": "PSG",
    "RC Lens": "Lens",
    "Real Sociedad de Fútbol": "Real Sociedad",
    "SSC Napoli": "Napoli",
    "Sunderland AFC": "Sunderland",
    "Tottenham Hotspur": "Tottenham",
    "Udinese Calcio": "Udinese",
    "US Cremonese": "Cremonese",
    "US Lecce": "Lecce",
    "Wolverhampton Wanderers": "Wolves"
  };
  if (replacements[clean]) return replacements[clean];
  return clean
    .replace(/^FC\s+/i, "")
    .replace(/\s+FC$/i, "")
    .replace(/\s+CF$/i, "")
    .replace(/\s+AFC$/i, "")
    .replace("Paris Saint-Germain", "PSG");
}

function eventLabel(type) {
  if (type === "penalty-goal") return "Penalty goal";
  if (type === "missed-penalty") return "Missed penalty";
  if (type === "own-goal") return "Own goal";
  if (type === "yellow-card") return "Yellow card";
  if (type === "second-yellow-card") return "Second yellow";
  if (type === "red-card") return "Red card";
  if (type === "substitution") return "Substitution";
  if (type === "var") return "VAR";
  if (type === "penalty") return "Penalty";
  return "Goal";
}

function formatNumber(value) {
  return new Intl.NumberFormat("en").format(value || 0);
}

function setStatus(message) {
  statusEl.textContent = message;
  statusEl.hidden = !message;
}

function setupBackLink() {
  if (!backLinkEl) return;
  backLinkEl.href = params.get("from") === "competition"
    ? sessionStorage.getItem("competitionReturnUrl") || "index.html"
    : sessionStorage.getItem("resultsReturnUrl") || "results.html";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
