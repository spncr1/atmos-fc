const DATA_MODE = resolveDataMode();
const API_BASE = DATA_MODE === "live" ? resolveApiBase() : "";
const STATIC_PATHS = {
  manifest: "data/showcase/manifest.json",
  metadata: "data/showcase/metadata.json",
  matches: "data/showcase/matches.json",
  teams: "data/showcase/teams.json",
  competitions: "data/showcase/competitions.json",
  analyses: "data/showcase/analyses.json",
  current: "data/current/current-season.json",
  recent: "data/current/recent.json",
};
let showcasePromise;
let showcaseTeamsPromise;
let showcaseCompetitionsPromise;
let showcaseAnalysesPromise;

function resolveDataMode() {
  const explicit = window.ATMOS_DATA_MODE || localStorage.getItem("ATMOS_DATA_MODE") || window.ATMOS_CONFIG?.DATA_MODE;
  if (explicit === "live" || explicit === "showcase") return explicit;
  return ["localhost", "127.0.0.1"].includes(window.location.hostname) ? "live" : "showcase";
}

function resolveApiBase() {
  const override = window.ATMOS_API_BASE || "";
  if (override) return override.replace(/\/$/, "");
  if (["localhost", "127.0.0.1"].includes(window.location.hostname)) return "http://localhost:8000";
  const configured = localStorage.getItem("ATMOS_API_BASE") || window.ATMOS_CONFIG?.API_BASE || "";
  return configured.replace(/\/$/, "");
}

async function apiGet(path, params = {}) {
  if (DATA_MODE === "showcase") return showcaseGet(path, params);
  if (!API_BASE) throw new Error("Atmos FC data is unavailable right now. Try again shortly.");
  const url = new URL(`${API_BASE}${path}`);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  });
  return readResponse(await fetchApi(url));
}

async function apiPost(path, body = {}) {
  if (DATA_MODE === "showcase") return showcasePost(path, body);
  if (!API_BASE) throw new Error("Atmos FC data is unavailable right now. Try again shortly.");
  return readResponse(await fetchApi(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

async function fetchApi(url, options) {
  try { return await fetch(url, options); }
  catch (_) { throw new Error("Atmos FC data is unavailable right now. Try again shortly."); }
}

async function readResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : "";
    const exposesInternalSource = /api-football|wikidata|provider/i.test(detail);
    throw new Error(detail && !exposesInternalSource ? detail : "Atmos FC data could not be loaded.");
  }
  return payload;
}

async function optionalJson(path, fallback) {
  try {
    const response = await fetch(path, { cache: "no-cache" });
    return response.ok ? await response.json() : fallback;
  } catch (_) {
    return fallback;
  }
}

async function loadShowcase() {
  if (!showcasePromise) showcasePromise = Promise.all([
    requiredJson(STATIC_PATHS.manifest), requiredJson(STATIC_PATHS.metadata), requiredJson(STATIC_PATHS.matches),
    optionalJson(STATIC_PATHS.current, { matches: [], teams: {}, competitions: {} }),
  ]).then(([manifest, metadata, archived, current]) => ({
    manifest, metadata, matches: mergeMatches(archived.matches || [], current.matches || []), current,
  }));
  return showcasePromise;
}

async function loadShowcaseTeams() {
  if (!showcaseTeamsPromise) showcaseTeamsPromise = requiredJson(STATIC_PATHS.teams).then(payload => payload.teams || {});
  return showcaseTeamsPromise;
}

async function loadShowcaseCompetitions() {
  if (!showcaseCompetitionsPromise) showcaseCompetitionsPromise = requiredJson(STATIC_PATHS.competitions).then(payload => payload.competitions || {});
  return showcaseCompetitionsPromise;
}

async function loadShowcaseAnalyses() {
  if (!showcaseAnalysesPromise) showcaseAnalysesPromise = requiredJson(STATIC_PATHS.analyses).then(payload => payload.analyses || {});
  return showcaseAnalysesPromise;
}

async function requiredJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error("Atmos FC showcase data could not be loaded.");
  return response.json();
}

function fixtureIdentity(match) {
  const kickoff = new Date(match.date).toISOString().slice(0, 16);
  return [match.competition_code, match.season_year, kickoff, match.home_team_key || match.home_team_id || normaliseName(match.home), match.away_team_key || match.away_team_id || normaliseName(match.away)].join("|");
}

function mergeMatches(archive, current) {
  const merged = new Map();
  archive.forEach(match => merged.set(fixtureIdentity(match), match));
  current.forEach(match => {
    const key = fixtureIdentity(match);
    const archived = merged.get(key);
    if (archived?.analysis_available) {
      merged.set(key, { ...archived, ...match, id: archived.id, analysis_available: true });
    } else {
      merged.set(key, match);
    }
  });
  return [...merged.values()].sort((a, b) => String(b.date).localeCompare(String(a.date)));
}

async function showcaseGet(path, params) {
  const data = await loadShowcase();
  if (path === "/metadata") return data.metadata;
  if (path === "/matches/recent") return showcaseRecent(data, params);
  if (path === "/matches/search") return showcaseSearch(data, params);
  const teamMatch = path.match(/^\/teams\/([^/]+)$/);
  if (teamMatch) return showcaseTeam(data, await loadShowcaseTeams(), decodeURIComponent(teamMatch[1]), params.season);
  const competitionMatch = path.match(/^\/competitions\/(\d+)$/);
  if (competitionMatch) return showcaseCompetition(data, await loadShowcaseCompetitions(), competitionMatch[1], params.season);
  throw new Error("Atmos FC showcase data could not be loaded.");
}

async function showcasePost(path, body) {
  if (path !== "/analyse") throw new Error("This action is not available in showcase mode.");
  const data = await loadShowcase();
  const analyses = await loadShowcaseAnalyses();
  const analysis = analyses[String(body.match_id)];
  if (!analysis) throw new Error("A completed Atmos FC match analysis is not available for this match.");
  return analysis;
}

function showcaseRecent(data, params) {
  const limit = positiveInt(params.limit, 18);
  const code = String(params.competition || "");
  const refreshed = new Set(data.manifest.refreshed_competitions || []);
  const currentMatches = data.matches.filter(match => refreshed.has(match.competition_code) && (!code || match.competition_code === code));
  const statuses = data.current.competitions || {};
  const dates = Object.values(statuses).map(row => row?.updated_at).filter(Boolean).sort();
  return {
    matches: currentMatches.slice(0, limit),
    sync: { status: "fresh", is_fresh: true, updated_at: dates.at(-1) || data.current.generated_at || data.manifest.generated_at, results_through: data.manifest.results_through, coverage_mode: code && !refreshed.has(code) ? "archive" : "refreshed" },
  };
}

function showcaseSearch(data, params) {
  const q = normaliseSearch(String(params.q || ""));
  const competition = String(params.competition || "");
  const season = params.season === undefined || params.season === null || params.season === "" ? null : Number(params.season);
  const analysesOnly = String(params.analysis_available || "").toLowerCase() === "true";
  const page = positiveInt(params.page, 1);
  const pageSize = Math.min(100, positiveInt(params.page_size, 15));
  const filtered = data.matches.filter(match => {
    if (competition && match.competition_code !== competition) return false;
    if (Number.isInteger(season) && Number(match.season_year) !== season) return false;
    if (analysesOnly && !match.analysis_available) return false;
    if (!q) return true;
    return normaliseSearch([match.home, match.away, match.competition, match.round, match.season].join(" ")).includes(q);
  });
  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);
  return { matches: filtered.slice((safePage - 1) * pageSize, safePage * pageSize), pagination: { page: safePage, page_size: pageSize, total, total_pages: totalPages, has_previous: safePage > 1, has_next: safePage < totalPages }, notices: [] };
}

function showcaseTeam(data, teams, requestedKey, requestedSeason) {
  const record = teams[requestedKey] || Object.values(teams).find(row => (row.aliases || []).includes(requestedKey));
  const currentIdentity = data.current.teams?.[requestedKey];
  const seasonYear = Number(requestedSeason) || latestTeamSeason(data.matches, requestedKey);
  if (!record && !currentIdentity) throw new Error("Team profile was not found.");
  const available = record?.profiles || {};
  const base = available[String(seasonYear)] || available[Object.keys(available).sort((a, b) => Number(b) - Number(a))[0]] || basicTeamProfile(currentIdentity, seasonYear);
  return mergeTeamSeason(base, data.matches, requestedKey, seasonYear, currentIdentity);
}

function latestTeamSeason(matches, key) {
  return matches.find(match => teamMatchesKey(match, key))?.season_year || new Date().getFullYear();
}

function teamMatchesKey(match, key) {
  return [match.home_team_key, match.away_team_key, String(match.home_team_id || ""), String(match.away_team_id || "")].includes(String(key));
}

function basicTeamProfile(identity = {}, year) {
  const label = seasonLabel(year);
  return { provider_team_id: identity.provider_team_id || 0, team_key: identity.team_key, name: identity.name || "Club", code: identity.code, country_name: identity.country, logo_url: identity.crest, venue_label: null, visual: null, summary: `${identity.name || "This club"} is represented by current finished results in the Atmos FC showcase.`, profile_sections: [], has_manual_profile: false, official_website: null, data_source: "showcase", selected_season_year: year, selected_season_label: label, current_players: [], major_trophy_count: null, major_achievements: [], major_honours_status: "unavailable", kits: { season_label: label, home: { label: "Home kit", image_url: null, status: "unavailable" }, away: { label: "Away kit", image_url: null, status: "unavailable" } } };
}

function mergeTeamSeason(base, matches, key, year, identity) {
  const result = structuredClone(base);
  if (identity) {
    result.team_key = identity.team_key;
    result.name ||= identity.name;
    result.code ||= identity.code;
    result.logo_url ||= identity.crest;
  }
  const rows = matches.filter(match => Number(match.season_year) === Number(year) && teamMatchesKey(match, key));
  const outcomes = rows.map(match => teamOutcome(match, key));
  const wins = outcomes.filter(row => row.result === "W").length;
  const draws = outcomes.filter(row => row.result === "D").length;
  const losses = outcomes.filter(row => row.result === "L").length;
  const goalsFor = outcomes.reduce((sum, row) => sum + row.scored, 0);
  const goalsAgainst = outcomes.reduce((sum, row) => sum + row.conceded, 0);
  result.selected_season_year = Number(year);
  result.selected_season_label = seasonLabel(Number(year));
  result.season_snapshot = { season_year: Number(year), season_label: seasonLabel(Number(year)), overview: rows.length ? `Across matches stored by Atmos FC for ${seasonLabel(Number(year))}, ${result.name} have recorded ${wins} ${plural(wins, "win")}, ${draws} ${plural(draws, "draw")} and ${losses} ${plural(losses, "loss")}, scoring ${goalsFor} and conceding ${goalsAgainst}.` : `Atmos FC has no finished ${result.name} matches stored for the ${seasonLabel(Number(year))} season yet.`, matches_played: rows.length, wins, draws, losses, goals_for: goalsFor, goals_against: goalsAgainst, form: outcomes.slice(0, 5).map(row => row.result), latest_matches: rows.slice(0, 5) };
  result.latest_match_analysis = rows[0]?.analysis_available ? result.latest_match_analysis : null;
  return result;
}

function teamOutcome(match, key) {
  const [home, away] = String(match.score || "0-0").match(/\d+/g)?.map(Number) || [0, 0];
  const isHome = [match.home_team_key, String(match.home_team_id || "")].includes(String(key));
  const scored = isHome ? home : away, conceded = isHome ? away : home;
  return { scored, conceded, result: scored > conceded ? "W" : scored < conceded ? "L" : "D" };
}

function showcaseCompetition(data, competitions, id, requestedSeason) {
  const profiles = competitions[String(id)];
  if (!profiles) throw new Error("Competition is not supported by Atmos FC.");
  const year = Number(requestedSeason);
  const base = profiles[String(year)] || profiles[Object.keys(profiles).sort((a, b) => Number(b) - Number(a))[0]];
  if (!base) throw new Error("Competition season was not found.");
  const result = structuredClone(base);
  const rows = data.matches.filter(match => String(match.competition_id) === String(id) && Number(match.season_year) === Number(year));
  const clubs = new Map();
  rows.forEach(match => {
    clubs.set(match.home_team_key || String(match.home_team_id), { provider_team_id: match.home_team_id || 0, team_key: match.home_team_key || String(match.home_team_id), name: match.home, logo_url: match.home_crest });
    clubs.set(match.away_team_key || String(match.away_team_id), { provider_team_id: match.away_team_id || 0, team_key: match.away_team_key || String(match.away_team_id), name: match.away, logo_url: match.away_crest });
  });
  result.finished_match_count = rows.length;
  result.goals_scored = rows.reduce((sum, match) => sum + (String(match.score || "").match(/\d+/g)?.map(Number).reduce((a, b) => a + b, 0) || 0), 0);
  result.club_count = clubs.size;
  result.latest_result_at = rows[0]?.date || null;
  result.latest_matches = rows.slice(0, 6);
  result.clubs = [...clubs.values()].sort((a, b) => a.name.localeCompare(b.name));
  result.coverage_status = competitionCoverage(data, result.code, rows);
  return result;
}

function competitionCoverage(data, code, rows) {
  const refreshed = (data.manifest.refreshed_competitions || []).includes(code);
  const status = data.current.competitions?.[code];
  return refreshed && status?.updated_at ? { mode: "refreshed", label: `Latest results updated ${formatDate(status.updated_at)}` } : { mode: "archive", label: `Results available through ${formatDate(rows[0]?.date || data.manifest.results_through)}` };
}

function positiveInt(value, fallback) { const parsed = Number.parseInt(value, 10); return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback; }
function normaliseName(value) { return String(value || "").toLocaleLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, ""); }
function normaliseSearch(value) { return String(value || "").trim().toLocaleLowerCase(); }
function seasonLabel(year) { return `${year}/${String(year + 1).slice(-2)}`; }
function plural(count, word) { return count === 1 ? word : `${word}s`; }

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("en", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

function storeMatch(match) {
  if (!match?.analysis_available) return;
  sessionStorage.setItem("selectedMatch", JSON.stringify(match));
  sessionStorage.setItem("resultsReturnUrl", window.location.href);
  window.location.href = `analysis.html?match_id=${encodeURIComponent(match.id)}`;
}
