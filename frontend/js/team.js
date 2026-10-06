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
  summaryEl.textContent = "Open a team profile from a match page once crest navigation is wired in.";
  setTeamLogo(null, "");
  factsEl.innerHTML = "";
  sectionsEl.innerHTML = "";
}

async function renderTeamProfile(teamId) {
  renderLoadingTeam(teamId);
  try {
    const team = await apiGet(`/teams/${encodeURIComponent(teamId)}`);
    renderTeamFacts(team);
  } catch (error) {
    renderTeamError(teamId, error);
  }
}

function renderLoadingTeam(teamId) {
  applyTeamVisual(teamId, "");
  statusEl.textContent = "Loading team profile";
  nameEl.textContent = `Team ${escapeHtml(teamId)}`;
  summaryEl.textContent = "Loading API-Football team facts...";
  setTeamLogo(null, "");
  factsEl.innerHTML = [
    fact("API-Football ID", teamId),
    fact("Country", "Loading"),
    fact("Founded", "Loading"),
    fact("Home ground", "Loading"),
  ].join("");
  sectionsEl.innerHTML = "";
}

function renderTeamFacts(team) {
  applyTeamVisual(team.provider_team_id, team.name, team.visual);
  statusEl.textContent = profileStatus(team);
  nameEl.textContent = team.name || `Team ${team.provider_team_id}`;
  summaryEl.textContent = team.summary || summaryText(team);
  setTeamLogo(team.logo_url, team.name);
  factsEl.innerHTML = [
    fact("API-Football ID", team.provider_team_id),
    fact("Country", team.country_name || "Unavailable"),
    fact("Founded", team.founded || "Unavailable"),
    fact("Home ground", team.venue_label || "Unavailable"),
    team.official_website ? fact("Official website", "Visit site", team.official_website) : "",
    team.wikidata_qid && !team.profile_needs_review
      ? fact("Wikidata", team.wikidata_qid, team.source_attribution_url)
      : "",
  ].join("");
  sectionsEl.innerHTML = renderProfileSections(team);
}

function renderTeamError(teamId, error) {
  applyTeamVisual(teamId, "");
  statusEl.textContent = "Team profile unavailable";
  nameEl.textContent = `Team ${escapeHtml(teamId)}`;
  summaryEl.textContent = error.message || "This team profile could not be loaded.";
  setTeamLogo(null, "");
  factsEl.innerHTML = [
    fact("API-Football ID", teamId),
    fact("Country", "Unavailable"),
    fact("Founded", "Unavailable"),
    fact("Home ground", "Unavailable"),
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
  if (team.wikidata_qid && !team.profile_needs_review) {
    return "Atmos FC team profile";
  }
  if (team.profile_needs_review) {
    return "Source match needs review";
  }
  if (String(team.data_source || "").includes("api_football")) {
    return "API-Football profile";
  }
  return "Stored team profile";
}

function renderProfileSections(team) {
  const cards = [];
  if (Array.isArray(team.profile_sections)) {
    cards.push(
      ...team.profile_sections
        .filter((section) => section && section.title && section.body)
        .map((section) => profileCard(section.title, section.body))
    );
  }

  const sourceLinks = sourceLinksFor(team);
  if (sourceLinks.length || team.license_label) {
    cards.push(sourceCard(sourceLinks, team.license_label));
  }
  return cards.join("");
}

function sourceLinksFor(team) {
  const links = [];
  if (team.official_website) {
    links.push({ label: "Official website", href: team.official_website });
  }
  if (team.wikipedia_url) {
    links.push({ label: "Wikipedia", href: team.wikipedia_url });
  }
  if (team.source_attribution_url) {
    links.push({ label: "Wikidata", href: team.source_attribution_url });
  }
  return links.filter((link) => safeUrl(link.href));
}

function fact(label, value, href = "") {
  const link = safeUrl(href);
  const content = link
    ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`
    : `<strong>${escapeHtml(value)}</strong>`;
  return `
    <article class="team-fact">
      <span>${escapeHtml(label)}</span>
      ${content}
    </article>
  `;
}

function profileCard(title, body, isEmpty = false) {
  return `
    <article class="team-profile-card">
      <h2>${escapeHtml(title)}</h2>
      <p class="${isEmpty ? "team-empty" : ""}">${escapeHtml(body)}</p>
    </article>
  `;
}

function sourceCard(links, licenseLabel) {
  const linkItems = links
    .map((link) => `
      <li>
        <a href="${escapeHtml(safeUrl(link.href))}" target="_blank" rel="noopener noreferrer">
          ${escapeHtml(link.label)}
        </a>
      </li>
    `)
    .join("");
  const license = licenseLabel
    ? `<p class="team-source-license">Data license: ${escapeHtml(licenseLabel)}</p>`
    : "";
  return `
    <article class="team-profile-card team-source-card">
      <h2>Sources</h2>
      ${linkItems ? `<ul class="team-source-list">${linkItems}</ul>` : ""}
      ${license}
    </article>
  `;
}

function setTeamLogo(src, teamName) {
  if (!logoEl) return;
  if (!src) {
    logoEl.removeAttribute("src");
    logoEl.hidden = true;
    if (logoShellEl) logoShellEl.hidden = true;
    return;
  }
  if (logoShellEl) logoShellEl.hidden = false;
  logoEl.hidden = false;
  logoEl.src = src;
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

  window.AtmosAmbientSignal.render({
    mode: "bloom",
    variant: "team",
    color: "var(--accent)",
  });
}

function safeUrl(value) {
  const url = String(value || "").trim();
  if (!url) return "";
  try {
    const parsed = new URL(url, window.location.origin);
    if (!["http:", "https:"].includes(parsed.protocol)) return "";
    return parsed.href;
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
