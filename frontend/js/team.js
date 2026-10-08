const backLinkEl = document.querySelector("[data-back-link]");
const statusEl = document.querySelector("[data-team-status]");
const nameEl = document.querySelector("[data-team-name]");
const summaryEl = document.querySelector("[data-team-summary]");
const heroEl = document.querySelector(".team-hero");
const logoShellEl = document.querySelector("[data-team-logo-shell]");
const logoEl = document.querySelector("[data-team-logo]");
const factsEl = document.querySelector("[data-team-facts]");
const sectionsEl = document.querySelector("[data-team-sections]");
const params = new URLSearchParams(window.location.search);

init();

async function init() {
  setupBackLink();
  const teamId = params.get("team_id");
  if (!teamId) {
    renderMissingTeam();
    return;
  }
  await renderTeamProfile(teamId);
}

function setupBackLink() {
  if (!backLinkEl) return;
  backLinkEl.href = params.get("from") === "competition"
    ? sessionStorage.getItem("competitionReturnUrl") || "index.html"
    : sessionStorage.getItem("analysisReturnUrl")
    || sessionStorage.getItem("resultsReturnUrl")
    || "index.html";
}

function renderMissingTeam() {
  applyTeamVisual("", "");
  statusEl.textContent = "No team selected";
  nameEl.textContent = "Team profile unavailable";
  summaryEl.textContent = "Open a team profile from a stored Atmos FC match.";
  setTeamLogo(null, "");
  factsEl.innerHTML = "";
  sectionsEl.innerHTML = "";
}

async function renderTeamProfile(teamId) {
  renderLoadingTeam(teamId);
  try {
    const team = await apiGet(`/teams/${encodeURIComponent(teamId)}`, {
      season: params.get("season"),
    });
    renderTeamFacts(team);
  } catch (error) {
    renderTeamError(teamId, error);
  }
}

function renderLoadingTeam(teamId) {
  applyTeamVisual(teamId, "");
  statusEl.textContent = "Loading team profile";
  nameEl.textContent = `Team ${escapeHtml(teamId)}`;
  summaryEl.textContent = "Loading club information and stored Atmos FC matches...";
  setTeamLogo(null, "");
  factsEl.innerHTML = [
    fact("Country", "Loading"),
    fact("Home ground", "Loading"),
    fact("Season", "Loading"),
    fact("Matches tracked", "Loading"),
    fact("Major trophies", "Loading"),
    fact("Official website", "Loading"),
  ].join("");
  sectionsEl.innerHTML = "";
}

function renderTeamFacts(team) {
  document.title = `${team.name || "Team"} ${team.selected_season_label || ""} - Atmos FC`.trim();
  applyTeamVisual(team.provider_team_id, team.name, team.visual);
  statusEl.textContent = profileStatus(team);
  nameEl.textContent = team.name || `Team ${team.provider_team_id}`;
  summaryEl.textContent = team.summary || summaryText(team);
  setTeamLogo(team.logo_url, team.name);
  factsEl.innerHTML = [
    fact("Country", team.country_name || "Unavailable"),
    fact("Home ground", team.venue_label || "Unavailable"),
    fact("Season", team.selected_season_label || "Unavailable"),
    fact("Matches tracked", team.season_snapshot?.matches_played ?? 0),
    fact("Major trophies", Number.isInteger(team.major_trophy_count) ? team.major_trophy_count : "Unavailable"),
    fact("Official website", team.official_website ? "Visit site" : "Unavailable", team.official_website),
  ].join("");
  sectionsEl.innerHTML = renderProfileSections(team);
  bindMatchActions();
}

function renderTeamError(teamId, error) {
  applyTeamVisual(teamId, "");
  statusEl.textContent = "Team profile unavailable";
  nameEl.textContent = `Team ${escapeHtml(teamId)}`;
  summaryEl.textContent = error.message || "This team profile could not be loaded.";
  setTeamLogo(null, "");
  factsEl.innerHTML = [
    fact("Country", "Unavailable"),
    fact("Home ground", "Unavailable"),
    fact("Season", "Unavailable"),
    fact("Matches tracked", "Unavailable"),
    fact("Major trophies", "Unavailable"),
    fact("Official website", "Unavailable"),
  ].join("");
  sectionsEl.innerHTML = "";
}

function summaryText(team) {
  const type = team.is_national ? "national team" : "club";
  const country = team.country_name ? ` from ${team.country_name}` : "";
  const founded = team.founded ? ` Founded in ${team.founded}.` : "";
  return `${team.name} is a ${type}${country}.${founded}`;
}

function profileStatus(team) {
  return team.is_national ? "Atmos FC national team profile" : "Atmos FC team profile";
}

function renderProfileSections(team) {
  const cards = [
    `<div class="team-feature-row">${latestMatchCard(team.latest_match_analysis, team.season_snapshot?.latest_matches || [])}${kitsCard(team.kits)}</div>`,
    seasonCard(team.season_snapshot),
    playersCard(team.current_players || [], team.current_players_updated_at),
    achievementsCard(team),
  ];

  const sourceLinks = sourceLinksFor(team);
  if (sourceLinks.length) {
    cards.push(sourceCard(sourceLinks));
  }
  return cards.join("");
}

function seasonCard(snapshot) {
  if (!snapshot) return sectionCard("Atmos this season", emptyState("Season data is unavailable."), "team-section-wide");
  const metrics = [
    ["Matches", snapshot.matches_played],
    ["Wins", snapshot.wins],
    ["Draws", snapshot.draws],
    ["Losses", snapshot.losses],
    ["Goals scored", snapshot.goals_for],
    ["Goals conceded", snapshot.goals_against],
  ].map(([label, value]) => `
    <div class="team-season-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");
  const form = Array.isArray(snapshot.form) && snapshot.form.length
    ? `<div class="team-form" aria-label="Latest form">${snapshot.form.map((result) => `<span class="team-form-${escapeAttr(result.toLowerCase())}">${escapeHtml(result)}</span>`).join("")}</div>`
    : `<span class="team-empty">No form available</span>`;
  const matches = Array.isArray(snapshot.latest_matches) && snapshot.latest_matches.length
    ? `<div class="team-results">${snapshot.latest_matches.map(resultRow).join("")}</div>`
    : emptyState("No finished matches are stored for this club and season yet.");
  return sectionCard(
    "Atmos this season",
    `<p class="team-section-eyebrow">${escapeHtml(snapshot.season_label)}</p>
     <p class="team-season-overview">${escapeHtml(snapshot.overview)}</p>
     <div class="team-season-metrics">${metrics}</div>
     <div class="team-form-row"><strong>Last 5</strong>${form}</div>
     <div class="team-subsection-heading"><h3>Latest results</h3><span>Powered by Atmos FC</span></div>
     ${matches}`,
    "team-section-wide",
  );
}

function resultRow(match) {
  return `
    <button class="team-result" type="button" data-team-match="${escapeAttr(encodeURIComponent(JSON.stringify(match)))}">
      <span class="team-result-meta"><strong>${escapeHtml(match.competition)}</strong><span>${escapeHtml(formatDate(match.date))}</span></span>
      <span class="team-result-team">${crest(match.home_crest)}<span>${escapeHtml(match.home)}</span></span>
      <strong class="team-result-score">${escapeHtml(displayScore(match))}</strong>
      <span class="team-result-team team-result-away"><span>${escapeHtml(match.away)}</span>${crest(match.away_crest)}</span>
      <span class="team-result-action">Analyse</span>
    </button>
  `;
}

function latestMatchCard(analysis, latestMatches) {
  const latest = latestMatches[0];
  if (!latest) {
    return sectionCard(
      "Latest match",
      emptyState("No finished match is stored for this club and season yet."),
      "team-atmos-card",
    );
  }
  if (!analysis) {
    return sectionCard(
      "Latest match",
      `${latestMatchScore(latest)}
       <button class="team-primary-action team-latest-action" type="button" data-team-match="${escapeAttr(encodeURIComponent(JSON.stringify(latest)))}">Analyse match</button>`,
      "team-atmos-card team-latest-match-card",
    );
  }
  const vibe = analysis.overall_vibe || {};
  const energy = analysis.crowd_energy || {};
  const peak = analysis.peak_window;
  const peakLabel = peak && Number.isFinite(peak.hour_start) && Number.isFinite(peak.hour_end)
    ? `${peak.hour_start}-${peak.hour_end} hours after full time`
    : "Unavailable";
  return sectionCard(
    "Latest match",
    `${latestMatchScore(analysis.match)}
     <div class="team-atmos-metrics">
       ${atmosMetric("Overall vibe", vibe.label || "Unavailable", vibe.subtext || "")}
       ${atmosMetric("Crowd energy", energy.label || "Unavailable", energy.subtext || "")}
       ${atmosMetric("Strongest reaction", peakLabel, "")}
       ${atmosMetric("Reaction volume", formatNumber(analysis.total_comments), "comments analysed")}
     </div>
     <button class="team-primary-action team-latest-action" type="button" data-team-match="${escapeAttr(encodeURIComponent(JSON.stringify(analysis.match)))}">View match analysis</button>`,
    "team-atmos-card team-latest-match-card team-latest-match-analysed",
  );
}

function latestMatchScore(match) {
  return `<p class="team-latest-match-meta">${escapeHtml(match.competition)} · ${escapeHtml(formatDate(match.date))}</p>
    <div class="team-latest-score">
      <span class="team-latest-club team-latest-home">${crest(match.home_crest)}<strong>${escapeHtml(match.home)}</strong></span>
      <strong class="team-latest-scoreline">${escapeHtml(displayScore(match))}</strong>
      <span class="team-latest-club team-latest-away"><strong>${escapeHtml(match.away)}</strong>${crest(match.away_crest)}</span>
    </div>`;
}

function atmosMetric(label, value, detail) {
  return `<div class="team-atmos-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</div>`;
}

function playersCard(players, updatedAt) {
  if (!players.length) {
    return sectionCard("Current players", emptyState("Current squad information is unavailable."), "team-section-wide");
  }
  const groups = groupPlayers(players);
  const content = Object.entries(groups).map(([position, rows]) => `
    <section class="team-player-group">
      <h3>${escapeHtml(position)}</h3>
      <div class="team-player-grid">${rows.map(playerCard).join("")}</div>
    </section>
  `).join("");
  const freshness = updatedAt ? `As of ${formatDate(updatedAt)}` : "Current squad";
  return sectionCard(
    "Current players",
    `<p class="team-section-note">${escapeHtml(freshness)}</p><div class="team-player-groups">${content}</div>`,
    "team-section-wide",
  );
}

function groupPlayers(players) {
  const groups = { Goalkeepers: [], Defenders: [], Midfielders: [], Forwards: [] };
  players.forEach((player) => {
    const position = String(player.position || "").toLowerCase();
    if (position.includes("goal")) groups.Goalkeepers.push(player);
    else if (position.includes("def")) groups.Defenders.push(player);
    else if (position.includes("mid")) groups.Midfielders.push(player);
    else groups.Forwards.push(player);
  });
  return Object.fromEntries(Object.entries(groups).filter(([, rows]) => rows.length));
}

function playerCard(player) {
  const photo = safeUrl(player.photo_url);
  return `
    <article class="team-player">
      ${photo ? `<img src="${escapeAttr(photo)}" alt="" loading="lazy" onerror="this.hidden=true">` : `<span class="team-player-placeholder" aria-hidden="true"></span>`}
      <span class="team-player-copy">
        <strong>${escapeHtml(player.name)}</strong>
        <small>${player.number ? `#${escapeHtml(player.number)} · ` : ""}${escapeHtml(player.position || "Position unavailable")}</small>
      </span>
    </article>
  `;
}

function kitsCard(kits) {
  const season = kits?.season_label || "Selected season";
  return sectionCard(
    `${season} kits`,
    `<div class="team-kit-grid">${kitPlaceholder(kits?.home?.label || "Home kit")}${kitPlaceholder(kits?.away?.label || "Away kit")}</div>`,
    "team-kits-card",
  );
}

function kitPlaceholder(label) {
  return `
    <div class="team-kit-placeholder">
      <span class="team-shirt" aria-hidden="true"><span></span></span>
      <strong>${escapeHtml(label)}</strong>
      <small>Image unavailable</small>
    </div>
  `;
}

function achievementsCard(team) {
  const count = team.major_trophy_count;
  const achievements = Array.isArray(team.major_achievements) ? team.major_achievements : [];
  const status = team.major_honours_status || "unavailable";
  let content = "";
  if (status === "verified_zero" || (Number.isInteger(count) && count === 0)) {
    content = emptyState("No major senior trophies are recorded in Atmos FC's major-honours categories.");
  } else if (status === "complete" && Number.isInteger(count) && achievements.length) {
    content = `<div class="team-honours-grid">${achievements.map(honourTile).join("")}</div>`;
  } else {
    content = emptyState("Major-honours information is temporarily unavailable.");
  }
  return sectionCard(
    "Major achievements",
    `<p class="team-achievements-intro">Major senior honours across top-flight leagues, principal domestic cups and super cups, plus major continental and world competitions.</p>${content}`,
    "team-section-wide team-achievements-card",
  );
}

function honourTile(achievement) {
  const match = String(achievement || "").trim().match(/^(\d+)\s+(.+)$/);
  const count = match ? Number(match[1]) : 1;
  const title = match ? match[2] : achievement;
  const label = count > 1 ? `${count}x ${title}` : title;
  return `
    <article class="team-honour">
      <span class="team-honour-icon" aria-hidden="true">
        <svg viewBox="0 0 64 64" focusable="false">
          <path d="M20 9h24v9c0 11-4.8 18.2-12 21-7.2-2.8-12-10-12-21V9Z"></path>
          <path d="M20 15H10v5c0 8 4.7 13 12 14M44 15h10v5c0 8-4.7 13-12 14M32 39v9M23 55h18M27 48h10v7H27z"></path>
        </svg>
      </span>
      <strong>${escapeHtml(label)}</strong>
    </article>
  `;
}

function sourceLinksFor(team) {
  const links = [];
  if (team.official_website) links.push({ label: "Official website", href: team.official_website });
  if (team.achievements_source_url) links.push({ label: "Honours source", href: team.achievements_source_url });
  return links.filter((link) => safeUrl(link.href));
}

function fact(label, value, href = "") {
  const link = safeUrl(href);
  const content = link
    ? `<a href="${escapeAttr(link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`
    : `<strong>${escapeHtml(value)}</strong>`;
  return `<article class="team-fact"><span>${escapeHtml(label)}</span>${content}</article>`;
}

function sectionCard(title, body, classes = "") {
  return `<section class="team-profile-card ${escapeAttr(classes)}"><h2>${escapeHtml(title)}</h2>${body}</section>`;
}

function sourceCard(links) {
  const linkItems = links.map((link) => `
    <li><a href="${escapeAttr(safeUrl(link.href))}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.label)}</a></li>
  `).join("");
  return sectionCard("Sources", `<ul class="team-source-list">${linkItems}</ul>`, "team-section-wide team-source-card");
}

function emptyState(message) {
  return `<p class="team-empty">${escapeHtml(message)}</p>`;
}

function bindMatchActions() {
  sectionsEl.querySelectorAll("[data-team-match]").forEach((button) => {
    button.addEventListener("click", () => {
      const match = JSON.parse(decodeURIComponent(button.dataset.teamMatch));
      sessionStorage.setItem("selectedMatch", JSON.stringify(match));
      sessionStorage.setItem("teamReturnUrl", window.location.href);
      window.location.href = `analysis.html?match_id=${encodeURIComponent(match.id)}&from=team`;
    });
  });
}

function displayScore(match) {
  return match?.score || "-";
}

function formatNumber(value) {
  return new Intl.NumberFormat("en").format(Number(value) || 0);
}

function crest(src) {
  const url = safeUrl(src);
  return url ? `<img src="${escapeAttr(url)}" alt="" loading="lazy" onerror="this.hidden=true">` : `<span class="team-result-crest-placeholder" aria-hidden="true"></span>`;
}

function setTeamLogo(src, teamName) {
  if (!logoEl) return;
  const url = safeUrl(src);
  if (!url) {
    logoEl.removeAttribute("src");
    logoEl.hidden = true;
    if (logoShellEl) logoShellEl.hidden = true;
    return;
  }
  if (logoShellEl) logoShellEl.hidden = false;
  logoEl.hidden = false;
  logoEl.src = url;
  logoEl.alt = teamName ? `${teamName} logo` : "";
  logoEl.onerror = () => {
    logoEl.hidden = true;
    if (logoShellEl) logoShellEl.hidden = true;
  };
}

function applyTeamVisual(teamId, teamName, serverVisual = null) {
  if (!heroEl || !window.AtmosTeamVisuals) return;
  const visual = window.AtmosTeamVisuals.fromServer(serverVisual, teamId, teamName);
  heroEl.style.setProperty("--team-primary", visual.primary);
  heroEl.style.setProperty("--team-secondary", visual.secondary);
  heroEl.style.setProperty("--team-glow", visual.glow);
  heroEl.style.setProperty("--team-soft-glow", visual.softGlow);
  heroEl.style.setProperty("--team-border", visual.border);
  heroEl.style.setProperty("--team-shadow", visual.shadow);
  renderTeamAmbientSignal();
}

function renderTeamAmbientSignal() {
  if (!window.AtmosAmbientSignal) return;
  window.AtmosAmbientSignal.render({ mode: "bloom", variant: "team", color: "var(--accent)" });
}

function safeUrl(value) {
  const url = String(value || "").trim();
  if (!url) return "";
  try {
    const parsed = new URL(url, window.location.origin);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch {
    return "";
  }
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
