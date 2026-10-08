const API_BASE = resolveApiBase();

function resolveApiBase() {
  const override = window.ATMOS_API_BASE || "";

  if (override) {
    return override.replace(/\/$/, "");
  }

  if (["localhost", "127.0.0.1"].includes(window.location.hostname)) {
    return "http://localhost:8000";
  }

  const configured = localStorage.getItem("ATMOS_API_BASE") || window.ATMOS_CONFIG?.API_BASE || "";

  if (configured) {
    return configured.replace(/\/$/, "");
  }

  return "";
}

async function apiGet(path, params = {}) {
  if (!API_BASE) {
    throw new Error("Atmos FC data is unavailable right now. Try again shortly.");
  }
  const url = new URL(`${API_BASE}${path}`);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  const response = await fetchApi(url);
  return readResponse(response);
}

async function apiPost(path, body = {}) {
  if (!API_BASE) {
    throw new Error("Atmos FC data is unavailable right now. Try again shortly.");
  }
  const response = await fetchApi(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readResponse(response);
}

async function fetchApi(url, options) {
  try {
    return await fetch(url, options);
  } catch (error) {
    throw new Error("Atmos FC data is unavailable right now. Try again shortly.");
  }
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

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

function storeMatch(match) {
  sessionStorage.setItem("selectedMatch", JSON.stringify(match));
  sessionStorage.setItem("resultsReturnUrl", window.location.href);
  window.location.href = `analysis.html?match_id=${encodeURIComponent(match.id)}`;
}
