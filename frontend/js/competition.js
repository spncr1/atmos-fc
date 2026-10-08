const backLinkEl = document.querySelector("[data-back-link]");
const statusEl = document.querySelector("[data-status]");
const nameEl = document.querySelector("[data-competition-name]");
const summaryEl = document.querySelector("[data-competition-summary]");
const logoShellEl = document.querySelector("[data-competition-logo-shell]");
const logoEl = document.querySelector("[data-competition-logo]");
const factsEl = document.querySelector("[data-competition-facts]");
const metricsEl = document.querySelector("[data-competition-metrics]");
const matchesEl = document.querySelector("[data-latest-matches]");
const clubsEl = document.querySelector("[data-competition-clubs]");
const sourcesEl = document.querySelector("[data-competition-sources]");
const params = new URLSearchParams(window.location.search);

init();

async function init() {
  setupBackLink();
  renderAmbientSignal();
  const competitionId = positiveInteger(params.get("competition_id"));
  const season = positiveInteger(params.get("season"));
  if (!competitionId || !season) {
    renderError("Competition and season details are required to open this database page.");
    return;
  }

  setStatus("Loading competition database...");
  try {
    const competition = await apiGet(`/competitions/${encodeURIComponent(competitionId)}`, { season });
    renderCompetition(competition);
    setStatus("");
  } catch (error) {
    renderError(error.message || "This competition database could not be loaded.");
  }
}

function setupBackLink() {
  if (!backLinkEl) return;
  backLinkEl.href = sessionStorage.getItem("competitionSourceUrl") || "index.html";
}

function renderCompetition(competition) {
  document.title = `${competition.name} ${competition.season_label} - Atmos FC Competition Database`;
  nameEl.textContent = competition.name;
  summaryEl.textContent = competition.summary;
  setCompetitionLogo(competition.logo_url, competition.name);
  factsEl.innerHTML = [
    fact("Country/region", competition.country_name || "Unavailable"),
    fact("Competition type", competition.competition_type || "Unavailable"),
    fact("Season", competition.season_label),
    fact("Season dates", seasonDates(competition)),
  ].join("");
  metricsEl.innerHTML = [
    metric("Finished matches", competition.finished_match_count),
    metric("Total Goals scored", competition.goals_scored),
    metric("Total clubs", competition.club_count),
    metric("Latest match", competition.latest_result_at ? formatDate(competition.latest_result_at) : "None yet"),
  ].join("");
  renderMatches(competition.latest_matches || []);
  renderClubs(competition.clubs || [], competition.season_year);
  renderSources(competition);
}

function renderMatches(matches) {
  if (!matches.length) {
    matchesEl.innerHTML = emptyState("No finished matches are stored for this competition and season yet.");
    return;
  }
  matchesEl.innerHTML = matches.map((match) => `
    <button class="competition-result" type="button" data-match="${escapeAttr(encodeURIComponent(JSON.stringify(match)))}">
      <span class="competition-result-meta">
        <span>${escapeHtml(match.round || "Stage unavailable")}</span>
        <span>${escapeHtml(formatDate(match.date))}</span>
      </span>
      <span class="competition-result-teams">
        <span class="competition-result-team">${crest(match.home_crest, match.home)}<span>${escapeHtml(match.home)}</span></span>
        <strong class="competition-result-score">${escapeHtml(displayScore(match))}</strong>
        <span class="competition-result-team"><span>${escapeHtml(match.away)}</span>${crest(match.away_crest, match.away)}</span>
      </span>
      <span class="competition-result-action">Analyse</span>
    </button>
  `).join("");
  matchesEl.querySelectorAll("[data-match]").forEach((button) => {
    button.addEventListener("click", () => openMatch(JSON.parse(decodeURIComponent(button.dataset.match))));
  });
}

function renderClubs(clubs, seasonYear) {
  if (!clubs.length) {
    clubsEl.innerHTML = emptyState("No clubs are represented by stored fixtures for this season yet.");
    return;
  }
  clubsEl.style.setProperty("--club-columns", clubColumnCount(clubs.length));
  clubsEl.innerHTML = clubs.map((club) => `
    <a class="competition-club" href="team.html?team_id=${encodeURIComponent(club.provider_team_id)}&season=${encodeURIComponent(seasonYear)}&from=competition" data-club-link>
      ${crest(club.logo_url, club.name)}
      <span>${escapeHtml(club.name)}</span>
    </a>
  `).join("");
  clubsEl.querySelectorAll("[data-club-link]").forEach((link) => {
    link.addEventListener("click", storeCompetitionReturnUrl);
  });
}

function clubColumnCount(clubCount) {
  if (clubCount === 18 || clubCount <= 12) return 3;
  if (clubCount <= 24) return 4;
  if (clubCount <= 40) return 5;
  return 6;
}

function renderSources(competition) {
  const officialUrl = safeUrl(competition.official_website);
  const wikipediaUrl = safeUrl(competition.wikipedia_url);
  const links = [
    officialUrl ? `<li><a href="${escapeAttr(officialUrl)}" target="_blank" rel="noopener noreferrer">Official competition website</a></li>` : "",
    wikipediaUrl ? `<li><a href="${escapeAttr(wikipediaUrl)}" target="_blank" rel="noopener noreferrer">Wikipedia</a></li>` : "",
  ].filter(Boolean).join("");
  sourcesEl.innerHTML = `
    <h2>Sources</h2>
    <ul class="competition-source-list">${links}</ul>
  `;
}

function openMatch(match) {
  sessionStorage.setItem("selectedMatch", JSON.stringify(match));
  storeCompetitionReturnUrl();
  window.location.href = `analysis.html?match_id=${encodeURIComponent(match.id)}&from=competition`;
}

function storeCompetitionReturnUrl() {
  sessionStorage.setItem("competitionReturnUrl", window.location.href);
}

function renderError(message) {
  setStatus(message);
  nameEl.textContent = "Competition database unavailable";
  summaryEl.textContent = "Atmos FC could not load this season-specific competition snapshot.";
  setCompetitionLogo(null, "");
  factsEl.innerHTML = "";
  metricsEl.innerHTML = emptyState("Database metrics are unavailable.");
  matchesEl.innerHTML = emptyState("Latest results are unavailable.");
  clubsEl.innerHTML = emptyState("Club data is unavailable.");
  sourcesEl.innerHTML = "";
}

function setCompetitionLogo(src, name) {
  if (!logoEl || !logoShellEl) return;
  if (!safeUrl(src)) {
    logoEl.removeAttribute("src");
    logoShellEl.hidden = true;
    return;
  }
  logoShellEl.hidden = false;
  logoEl.hidden = false;
  logoEl.src = src;
  logoEl.alt = name ? `${name} logo` : "";
  logoEl.onerror = () => {
    logoEl.hidden = true;
    logoShellEl.hidden = true;
  };
}

function fact(label, value) {
  return `<article class="competition-fact"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`;
}

function metric(label, value) {
  return `<article class="competition-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`;
}

function emptyState(message) {
  return `<p class="competition-empty">${escapeHtml(message)}</p>`;
}

function crest(src, name) {
  const url = safeUrl(src);
  return url
    ? `<img src="${escapeAttr(url)}" alt="${escapeAttr(name ? `${name} badge` : "")}" loading="lazy" onerror="this.remove()">`
    : "";
}

function displayScore(match) {
  return [match.score, match.score_note].filter(Boolean).join(" ") || "Score unavailable";
}

function seasonDates(competition) {
  const start = competition.season_start_date ? formatDate(competition.season_start_date) : "";
  const end = competition.season_end_date ? formatDate(competition.season_end_date) : "";
  return start && end ? `${start} - ${end}` : start || end || "Unavailable";
}

function positiveInteger(value) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function renderAmbientSignal() {
  if (!window.AtmosAmbientSignal) return;
  window.AtmosAmbientSignal.render({ mode: "bloom", variant: "team", color: "var(--accent)" });
}

function setStatus(message) {
  statusEl.textContent = message;
  statusEl.hidden = !message;
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
