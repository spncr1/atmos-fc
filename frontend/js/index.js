const recentGrid = document.querySelector("[data-recent]");
const recentHeading = document.querySelector("[data-recent-heading]");
const recentSyncNotice = document.querySelector("[data-recent-sync]");
const recentAnalysesGrid = document.querySelector("[data-recent-analyses]");
const analysisHeading = document.querySelector("[data-analysis-heading]");
const searchForm = document.querySelector("[data-search-form]");
const competitionFilters = document.querySelector("[data-competition-filters]");
const seasonFilters = document.querySelector("[data-season-filters]");
const POPULAR_COMPETITION_CODES = ["PL", "PD", "CL", "EL"];
const VISIBLE_SEASON_COUNT = 5;
const AMERICAS_PLACEHOLDERS = [
  { code: "BRA1", name: "Brazil Serie A", short_name: "Brazil Serie A", logo_url: "https://media.api-sports.io/football/leagues/71.png", disabled: true },
  { code: "ARG1", name: "Argentina Primera Division", short_name: "Argentina Primera", logo_url: "https://media.api-sports.io/football/leagues/128.png", disabled: true },
  { code: "MLS", name: "Major League Soccer", short_name: "MLS", logo_url: "https://media.api-sports.io/football/leagues/253.png", disabled: true },
  { code: "MEX1", name: "Liga MX", short_name: "Liga MX", logo_url: "https://media.api-sports.io/football/leagues/262.png", disabled: true },
];
const VISIBLE_SYNC_STATUSES = new Set(["stale", "missing", "failed", "running"]);
let competitionLabels = {};

init();

async function init() {
  renderHomeAmbientSignal();
  const metadata = await loadMetadata();
  renderFilters(metadata);
  competitionLabels = labelsByCode(metadata.competitions);
  searchForm.addEventListener("submit", handleSearch);
  searchForm.querySelectorAll('input[name="competition"]').forEach((input) => {
    input.addEventListener("change", () => {
      loadRecentAnalyses(input.value);
      loadRecentMatches(input.value);
    });
  });
  await Promise.all([loadRecentAnalyses(), loadRecentMatches()]);
}

function renderHomeAmbientSignal() {
  if (!window.AtmosAmbientSignal) return;

  window.AtmosAmbientSignal.render({
    mode: "signal",
    variant: "home",
    color: "var(--accent)",
    buckets: [34, 42, 38, 58, 46, 66, 54, 70, 62],
    peakIndex: 7,
  });
}

async function loadRecentMatches(competition = selectedCompetition()) {
  updateRecentHeading(competition);
  setStatus(recentGrid, loadingRecentMessage(competition));
  try {
    const { matches, sync } = await apiGet("/matches/recent", { limit: 12, competition });
    renderRecentSyncNotice(sync);
    renderMatches(recentGrid, matches);
  } catch (error) {
    renderRecentSyncNotice(null);
    setStatus(recentGrid, error.message || "Could not load recent matches.");
  }
}

async function loadRecentAnalyses(competition = selectedCompetition()) {
  updateAnalysisHeading(competition);
  setStatus(recentAnalysesGrid, loadingAnalysisMessage(competition));
  try {
    const { matches } = await apiGet("/matches/search", {
      competition,
      analysis_available: true,
      page: 1,
      page_size: 12,
    });
    renderMatches(recentAnalysesGrid, matches, "No match analyses are available for this competition.");
  } catch (error) {
    setStatus(recentAnalysesGrid, error.message || "Could not load match analyses.");
  }
}

function renderRecentSyncNotice(sync) {
  if (!recentSyncNotice) return;
  if (sync?.coverage_mode === "refreshed" && sync.updated_at) {
    recentSyncNotice.textContent = `Latest results updated ${formatDate(sync.updated_at)}.`;
    recentSyncNotice.hidden = false;
    return;
  }
  if (sync?.coverage_mode === "archive" && sync.results_through) {
    recentSyncNotice.textContent = `Results available through ${formatDate(sync.results_through)}.`;
    recentSyncNotice.hidden = false;
    return;
  }
  if (!sync || sync.is_fresh || !VISIBLE_SYNC_STATUSES.has(sync.status)) {
    recentSyncNotice.hidden = true;
    recentSyncNotice.textContent = "";
    return;
  }
  recentSyncNotice.textContent = syncNoticeMessage(sync);
  recentSyncNotice.hidden = false;
}

function syncNoticeMessage(sync) {
  if (sync.status === "stale") {
    return `Recent matches may be behind. Last fixture sync finished ${formatSyncAge(sync.seconds_since_success)} ago.`;
  }
  if (sync.status === "running") {
    return "Fixture sync is running now. Saved recent matches are shown until it finishes.";
  }
  if (sync.status === "failed") {
    return "Recent matches may be behind. The latest fixture sync failed, so saved matches are being shown.";
  }
  return "Recent matches may be behind. The fixture sync has not completed yet.";
}

function formatSyncAge(seconds) {
  if (!Number.isFinite(seconds)) return "an unknown time";
  if (seconds < 60) return "under a minute";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"}`;
}

async function loadMetadata() {
  try {
    const metadata = await apiGet("/metadata");
    if (Array.isArray(metadata.competitions) && Array.isArray(metadata.seasons)) {
      return metadata;
    }
  } catch (error) {
    console.warn("Falling back to bundled filter metadata.", error);
  }
  return fallbackMetadata();
}

function renderFilters(metadata) {
  renderCompetitionFilters(metadata.competitions);
  renderSeasonFilters(metadata.seasons);
}

function renderCompetitionFilters(competitions) {
  const competitionsByCode = mapByCode(competitions);
  const popularCompetitions = POPULAR_COMPETITION_CODES
    .map((code) => competitionsByCode[code])
    .filter(Boolean);
  const europeanDomestic = ["PL", "PD", "BL1", "SA", "FL1", "NED1", "POR1", "BEL1", "TUR1"]
    .map((code) => competitionsByCode[code])
    .filter(Boolean);
  const europeanClub = ["CL", "EL", "UECL"]
    .map((code) => competitionsByCode[code])
    .filter(Boolean);

  competitionFilters.innerHTML = [
    filterRadio("competition", "", "All", null, true),
    ...popularCompetitions.map((competition) => filterRadio(
      "competition",
      competition.code,
      competition.short_name || competition.name,
      competition.logo_url,
      false,
    )),
    moreFilter("More competitions", [
      filterGroup("European domestic leagues", europeanDomestic),
      filterGroup("European competitions", europeanClub),
      filterGroup("Americas", AMERICAS_PLACEHOLDERS),
    ]),
  ].join("");
}

function renderSeasonFilters(seasons) {
  const visibleSeasons = seasons.slice(0, VISIBLE_SEASON_COUNT);
  const olderSeasons = seasons.slice(VISIBLE_SEASON_COUNT);

  seasonFilters.innerHTML = [
    filterRadio("season", "", "All", null, true),
    ...visibleSeasons.map((season) => filterRadio("season", String(season.year), compactSeasonLabel(season.label), null, false)),
    olderSeasons.length ? moreFilter("More seasons", [
      filterGroup("Archive seasons", olderSeasons.map((season) => ({
        code: String(season.year),
        name: season.label,
        short_name: compactSeasonLabel(season.label),
      })), "season"),
    ]) : "",
  ].join("");
}

function filterRadio(name, value, label, icon, checked, disabled = false) {
  const iconMarkup = icon ? `<img class="filter-emblem" src="${escapeAttr(icon)}" alt="" loading="lazy" onerror="this.remove()">` : "";
  return `
    <label class="filter-option ${disabled ? "is-unavailable" : ""}">
      <input type="radio" name="${escapeAttr(name)}" value="${escapeAttr(value)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
      <span>${iconMarkup}${escapeHtml(label)}</span>
    </label>
  `;
}

function filterGroup(label, items, name = "competition") {
  if (!items.length) return "";
  return `
    <div class="filter-panel-group">
      <strong>${escapeHtml(label)}</strong>
      <div class="filter-panel-options">
        ${items.map((item) => filterRadio(
          name,
          item.code,
          item.short_name || item.name,
          item.logo_url,
          false,
          Boolean(item.disabled),
        )).join("")}
      </div>
    </div>
  `;
}

function moreFilter(label, groups) {
  return `
    <details class="filter-more">
      <summary>${escapeHtml(label)}</summary>
      <div class="filter-panel">
        ${groups.filter(Boolean).join("")}
      </div>
    </details>
  `;
}

function mapByCode(items) {
  return items.reduce((mapped, item) => {
    mapped[item.code] = item;
    return mapped;
  }, {});
}

function labelsByCode(competitions) {
  return competitions.reduce((labels, competition) => {
    labels[competition.code] = competition.name;
    return labels;
  }, {});
}

function fallbackMetadata() {
  return {
    competitions: [
      { code: "PL", name: "Premier League", short_name: "PL", logo_url: "https://media.api-sports.io/football/leagues/39.png" },
      { code: "PD", name: "La Liga", short_name: "La Liga", logo_url: "https://media.api-sports.io/football/leagues/140.png" },
      { code: "BL1", name: "Bundesliga", short_name: "Bundesliga", logo_url: "https://media.api-sports.io/football/leagues/78.png" },
      { code: "SA", name: "Serie A", short_name: "Serie A", logo_url: "https://media.api-sports.io/football/leagues/135.png" },
      { code: "FL1", name: "Ligue 1", short_name: "Ligue 1", logo_url: "https://media.api-sports.io/football/leagues/61.png" },
      { code: "NED1", name: "Eredivisie", short_name: "Eredivisie", logo_url: "https://media.api-sports.io/football/leagues/88.png" },
      { code: "POR1", name: "Liga Portugal", short_name: "Liga Portugal", logo_url: "https://media.api-sports.io/football/leagues/94.png" },
      { code: "BEL1", name: "Belgian Pro League", short_name: "Belgian Pro League", logo_url: "https://media.api-sports.io/football/leagues/144.png" },
      { code: "TUR1", name: "Turkish Super Lig", short_name: "Super Lig", logo_url: "https://media.api-sports.io/football/leagues/203.png" },
      { code: "CL", name: "UEFA Champions League", short_name: "UCL", logo_url: "https://media.api-sports.io/football/leagues/2.png" },
      { code: "EL", name: "UEFA Europa League", short_name: "UEL", logo_url: "https://media.api-sports.io/football/leagues/3.png" },
      { code: "UECL", name: "UEFA Conference League", short_name: "UECL", logo_url: "https://media.api-sports.io/football/leagues/848.png" },
    ],
    seasons: [
      { year: 2026, label: "2026/27", is_current: true },
      { year: 2025, label: "2025/26", is_current: false },
      { year: 2024, label: "2024/25", is_current: false },
      { year: 2023, label: "2023/24", is_current: false },
      { year: 2022, label: "2022/23", is_current: false },
      { year: 2021, label: "2021/22", is_current: false },
      { year: 2020, label: "2020/21", is_current: false },
      { year: 2019, label: "2019/20", is_current: false },
      { year: 2018, label: "2018/19", is_current: false },
      { year: 2017, label: "2017/18", is_current: false },
      { year: 2016, label: "2016/17", is_current: false },
      { year: 2015, label: "2015/16", is_current: false },
      { year: 2014, label: "2014/15", is_current: false },
      { year: 2013, label: "2013/14", is_current: false },
      { year: 2012, label: "2012/13", is_current: false },
      { year: 2011, label: "2011/12", is_current: false },
      { year: 2010, label: "2010/11", is_current: false },
    ],
  };
}

function compactSeasonLabel(label) {
  const parts = String(label || "").split("/");
  if (parts.length !== 2) return label;
  return `${parts[0].slice(-2)}/${parts[1]}`;
}

function handleSearch(event) {
  event.preventDefault();
  const data = new FormData(searchForm);
  const params = new URLSearchParams(data);
  window.location.href = `results.html?${params.toString()}`;
}

function renderMatches(target, matches, emptyMessage = "No matches found.") {
  if (!matches.length) {
    setStatus(target, emptyMessage);
    return;
  }
  target.innerHTML = matches.map(matchCard).join("");
  target.querySelectorAll("[data-match]").forEach((button) => {
    button.addEventListener("click", () => storeMatch(JSON.parse(decodeURIComponent(button.dataset.match))));
  });
}

function matchCard(match) {
  const tag = match.analysis_available ? "button" : "article";
  const matchData = match.analysis_available
    ? ` data-match="${encodeURIComponent(JSON.stringify(match))}"`
    : "";
  return `
    <${tag} class="match-card"${matchData}>
      <span class="match-card__meta">${match.competition} - ${formatDate(match.date)}</span>
      <span class="match-card__teams">
        <span class="match-card__team">${teamCrest(match.home_crest, match.home)}${displayTeamName(match, "home")}</span>
        <span class="match-card__score-stack">
          <strong>${displayScore(match)}</strong>
        </span>
        <span class="match-card__team">${displayTeamName(match, "away")}${teamCrest(match.away_crest, match.away)}</span>
        ${scoreContextMarkup(match)}
      </span>
      <span class="match-card__round">${match.round || match.season || ""}</span>
    </${tag}>
  `;
}

function displayScore(match) {
  return [match.score, match.score_note].filter(Boolean).join(" ");
}

function scoreDetail(match) {
  return [match.penalty_score, match.aggregate_score].filter(Boolean).join(" · ");
}

function scoreContextMarkup(match) {
  const detail = scoreDetail(match);
  return detail ? `<span class="match-card__score-context">${detail}</span>` : "";
}

function displayTeamName(match, side) {
  const tla = match[`${side}_tla`];
  return tla || compactTeamName(match[`${side}_short_name`] || match[side]);
}

function compactTeamName(name) {
  const clean = String(name || "").trim();
  const replacements = {
    "AFC Bournemouth": "BOU",
    "ACF Fiorentina": "Fiorentina",
    "Arsenal": "ARS",
    "Aston Villa": "AVL",
    "Athletic Club": "Athletic Bilbao",
    "Bayer 04 Leverkusen": "Leverkusen",
    "Brighton Hove": "BHA",
    "Brighton & Hove Albion": "BHA",
    "Borussia Mönchengladbach": "Gladbach",
    "Cagliari Calcio": "Cagliari",
    "Chelsea": "CHE",
    "Club Atlético de Madrid": "Atlético Madrid",
    "Club Atletico de Madrid": "Atlético Madrid",
    "Crystal Palace": "CRY",
    "Deportivo Alavés": "Alavés",
    "Everton": "EVE",
    "FC Bayern München": "Bayern Munich",
    "FC Internazionale Milano": "Inter Milan",
    "FC Nantes": "Nantes",
    "Fulham": "FUL",
    "Internazionale": "Inter Milan",
    "Feyenoord Rotterdam": "Feyenoord",
    "Genoa CFC": "Genoa",
    "Hellas Verona FC": "Hellas Verona",
    "Liverpool": "LIV",
    "Manchester City": "MCI",
    "Manchester United": "MUN",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "NFO",
    "Olympique Lyon": "Lyon",
    "Olympique Lyonnais": "Lyon",
    "Olympique de Marseille": "Marseille",
    "Paris Saint-Germain FC": "PSG",
    "RC Lens": "Lens",
    "Real Sociedad de Fútbol": "Real Sociedad",
    "SSC Napoli": "Napoli",
    "Sunderland AFC": "Sunderland",
    "Tottenham Hotspur": "Tottenham",
    "West Ham United": "WHU",
    "Wolverhampton": "WOL",
    "Wolverhampton Wanderers": "WOL",
    "Udinese Calcio": "Udinese",
    "US Cremonese": "Cremonese",
    "US Lecce": "Lecce",
  };
  if (replacements[clean]) return replacements[clean];
  return clean
    .replace(/^FC\s+/i, "")
    .replace(/\s+FC$/i, "")
    .replace(/\s+CF$/i, "")
    .replace(/\s+AFC$/i, "")
    .replace("Paris Saint-Germain", "PSG");
}

function teamCrest(src, team) {
  if (!src) return "";
  return `<img class="team-crest" src="${src}" alt="" loading="lazy" onerror="this.remove()">`;
}

function setStatus(target, message) {
  target.innerHTML = `<p class="status">${message}</p>`;
}

function selectedCompetition() {
  return searchForm.elements.competition?.value || "";
}

function updateRecentHeading(competition) {
  const label = competitionLabels[competition];
  recentHeading.textContent = label ? `Latest ${label} results` : "Latest results";
}

function loadingRecentMessage(competition) {
  const label = competitionLabels[competition];
  return label ? `Loading latest ${label} results...` : "Loading latest results...";
}

function updateAnalysisHeading(competition) {
  const label = competitionLabels[competition];
  analysisHeading.textContent = label ? `Latest ${label} match analyses` : "Latest match analyses";
}

function loadingAnalysisMessage(competition) {
  const label = competitionLabels[competition];
  return label ? `Loading ${label} match analyses...` : "Loading match analyses...";
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
