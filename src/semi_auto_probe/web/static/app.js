const els = {
  clock: document.querySelector("#clock"),
  token: document.querySelector("#token"),
  saveToken: document.querySelector("#saveToken"),
  sessionCount: document.querySelector("#sessionCount"),
  fileCount: document.querySelector("#fileCount"),
  jsonCount: document.querySelector("#jsonCount"),
  sizeCount: document.querySelector("#sizeCount"),
  rootPath: document.querySelector("#rootPath"),
  refreshSessions: document.querySelector("#refreshSessions"),
  sessionList: document.querySelector("#sessionList"),
  detailTitle: document.querySelector("#detailTitle"),
  detailSubtitle: document.querySelector("#detailSubtitle"),
  sessionStatus: document.querySelector("#sessionStatus"),
  detailStats: document.querySelector("#detailStats"),
  categoryStrip: document.querySelector("#categoryStrip"),
  previewGrid: document.querySelector("#previewGrid"),
  categoryFilter: document.querySelector("#categoryFilter"),
  fileSearch: document.querySelector("#fileSearch"),
  fileRows: document.querySelector("#fileRows"),
  jsonIndexSubtitle: document.querySelector("#jsonIndexSubtitle"),
  jsonList: document.querySelector("#jsonList"),
  jsonTitle: document.querySelector("#jsonTitle"),
  jsonSubtitle: document.querySelector("#jsonSubtitle"),
  jsonPreview: document.querySelector("#jsonPreview"),
  copyJson: document.querySelector("#copyJson"),
  connectionSummary: document.querySelector("#connectionSummary"),
  log: document.querySelector("#log"),
};

const state = {
  sessions: [],
  detail: null,
  selectedSessionId: null,
  selectedJsonPath: "",
  lastJsonText: "",
};

let tokenSaveTimer = null;
const initialParams = new URLSearchParams(window.location.search);
if (initialParams.get("token")) {
  localStorage.setItem("probeWebToken", initialParams.get("token"));
  initialParams.delete("token");
  const cleanQuery = initialParams.toString();
  history.replaceState(null, "", `${location.pathname}${cleanQuery ? `?${cleanQuery}` : ""}`);
}
els.token.value = localStorage.getItem("probeWebToken") || "";

function accessToken() {
  return localStorage.getItem("probeWebToken") || "";
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (accessToken()) headers["X-Access-Token"] = accessToken();
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || response.statusText);
  }
  return data;
}

function encodedPath(path) {
  return String(path)
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

function fileUrl(sessionId, path, { download = false } = {}) {
  const query = new URLSearchParams();
  if (download) query.set("download", "true");
  if (accessToken()) query.set("token", accessToken());
  return `/api/autotest/sessions/${encodeURIComponent(sessionId)}/files/${encodedPath(path)}?${query.toString()}`;
}

function log(message, data) {
  const detail = data ? ` ${JSON.stringify(data)}` : "";
  els.log.textContent = `[${new Date().toLocaleTimeString()}] ${message}${detail}\n${els.log.textContent}`;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = value / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function statusTone(status) {
  if (status === "active") return "good";
  if (status === "empty") return "warn";
  return "neutral";
}

function setStatus(text, tone = "neutral") {
  els.sessionStatus.textContent = text;
  els.sessionStatus.dataset.tone = tone;
}

function metric(label, value, subtext = "") {
  return `<div class="statCell"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${subtext ? `<small>${escapeHtml(subtext)}</small>` : ""}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function refreshSessions({ keepSelection = true } = {}) {
  try {
    const data = await api("/api/autotest/sessions?limit=120");
    state.sessions = data.sessions || [];
    els.rootPath.textContent = data.root_exists ? data.root : `${data.root} (missing)`;
    els.sessionCount.textContent = String(data.total_session_count ?? state.sessions.length);
    els.fileCount.textContent = String(data.totals?.files ?? "-");
    els.jsonCount.textContent = String(data.totals?.json ?? "-");
    els.sizeCount.textContent = formatBytes(data.totals?.size_bytes ?? 0);
    renderSessionList();

    const keepId = keepSelection && state.selectedSessionId && state.sessions.some((item) => item.id === state.selectedSessionId);
    if (!keepId && state.sessions.length > 0) {
      await selectSession(state.sessions[0].id);
    } else if (state.sessions.length === 0) {
      clearDetail("No AutoTest sessions found", "Run AutoTest locally to populate the session folder.");
    }
  } catch (error) {
    log("Session refresh failed", { message: error.message });
    clearDetail("Access required", "Enter the configured token to inspect AutoTest sessions.");
    setStatus("Locked", "bad");
  }
}

function renderSessionList() {
  if (!state.sessions.length) {
    els.sessionList.innerHTML = `<div class="emptyPanel">No sessions available</div>`;
    return;
  }
  els.sessionList.innerHTML = state.sessions
    .map((session) => {
      const selected = session.id === state.selectedSessionId ? " selected" : "";
      const counts = session.counts || {};
      return `
        <button class="sessionItem${selected}" type="button" data-session-id="${escapeHtml(session.id)}">
          <span class="sessionName">${escapeHtml(session.id)}</span>
          <span class="sessionMeta">${formatDate(session.modified_at)} · ${counts.json || 0} JSON · ${formatBytes(session.size_bytes)}</span>
          <span class="sessionFoot">
            <span class="dot" data-tone="${statusTone(session.status)}"></span>
            <span>${escapeHtml(session.status)}</span>
            <span>${session.file_count || 0} files</span>
          </span>
        </button>
      `;
    })
    .join("");
}

async function selectSession(sessionId) {
  state.selectedSessionId = sessionId;
  renderSessionList();
  setStatus("Loading", "warn");
  try {
    const detail = await api(`/api/autotest/sessions/${encodeURIComponent(sessionId)}?file_limit=8000&json_limit=500`);
    state.detail = detail;
    renderDetail();
    const firstJson = detail.json_documents?.[0];
    if (firstJson) {
      await loadJson(firstJson.path);
    } else {
      state.selectedJsonPath = "";
      state.lastJsonText = "";
      els.jsonTitle.textContent = "JSON Preview";
      els.jsonSubtitle.textContent = "No JSON metadata in this session";
      els.jsonPreview.textContent = "";
    }
  } catch (error) {
    log("Session detail failed", { sessionId, message: error.message });
    clearDetail("Unable to load session", error.message);
    setStatus("Error", "bad");
  }
}

function clearDetail(title, subtitle) {
  state.detail = null;
  els.detailTitle.textContent = title;
  els.detailSubtitle.textContent = subtitle;
  els.detailStats.innerHTML = "";
  els.categoryStrip.innerHTML = "";
  els.previewGrid.innerHTML = "";
  els.fileRows.innerHTML = "";
  els.jsonList.innerHTML = "";
  els.jsonIndexSubtitle.textContent = "No metadata loaded";
  els.jsonPreview.textContent = "";
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) return;
  const summary = detail.summary;
  const counts = summary.counts || {};
  const devices = detail.devices || {};
  els.detailTitle.textContent = summary.id;
  els.detailSubtitle.textContent = `${formatDate(summary.created_at)} · ${summary.relative_path}`;
  setStatus(summary.status, statusTone(summary.status));
  els.detailStats.innerHTML = [
    metric("Files", String(summary.file_count || 0), formatBytes(summary.size_bytes)),
    metric("Devices", String(devices.count || 0), `${devices.rows || 0} rows x ${devices.cols || 0} cols`),
    metric("Images", String(counts.images || 0), "microscope captures"),
    metric("CSV", String(counts.csv || 0), "measurement tables"),
  ].join("");
  renderCategories();
  renderPreviewGrid();
  renderJsonList();
  renderFileTable();
}

function renderCategories() {
  const categories = state.detail?.summary?.categories || {};
  const names = ["images", "iv", "wobb", "b1500", "other"];
  els.categoryStrip.innerHTML = names
    .map((name) => {
      const item = categories[name] || { file_count: 0, size_bytes: 0 };
      return `
        <button class="categoryChip" type="button" data-filter="${name}">
          <strong>${escapeHtml(name)}</strong>
          <span>${item.file_count || 0} files · ${formatBytes(item.size_bytes || 0)}</span>
        </button>
      `;
    })
    .join("");
}

function renderPreviewGrid() {
  const detail = state.detail;
  if (!detail) return;
  const files = detail.files || [];
  const images = files.filter((file) => file.kind === "image").slice(0, 4);
  const resultCounts = Object.entries(detail.result_counts || {}).slice(0, 5);
  const imageHtml = images
    .map(
      (file) => `
        <a class="imagePreview" href="${fileUrl(detail.summary.id, file.path)}" target="_blank" rel="noreferrer">
          <img src="${fileUrl(detail.summary.id, file.path)}" alt="${escapeHtml(file.name)}" loading="lazy" />
          <span>${escapeHtml(file.name)}</span>
        </a>
      `,
    )
    .join("");
  const resultHtml = resultCounts
    .map(([name, count]) => `<div class="resultChip"><span>${escapeHtml(name)}</span><strong>${count}</strong></div>`)
    .join("");
  els.previewGrid.innerHTML = imageHtml || resultHtml || `<div class="emptyPanel">No previews available</div>`;
}

function renderJsonList() {
  const docs = state.detail?.json_documents || [];
  els.jsonIndexSubtitle.textContent = `${docs.length} loaded · ${state.detail?.json_total || 0} total`;
  if (!docs.length) {
    els.jsonList.innerHTML = `<div class="emptyPanel">No JSON metadata</div>`;
    return;
  }
  els.jsonList.innerHTML = docs
    .map((doc) => {
      const device = doc.device?.name || doc.device?.order || "-";
      const selected = doc.path === state.selectedJsonPath ? " selected" : "";
      return `
        <button class="jsonItem${selected}" type="button" data-json-path="${escapeHtml(doc.path)}">
          <strong>${escapeHtml(device)}</strong>
          <span>${escapeHtml(doc.result_type || "json")} · ${escapeHtml(doc.name)}</span>
        </button>
      `;
    })
    .join("");
}

function filteredFiles() {
  const detail = state.detail;
  if (!detail) return [];
  const category = els.categoryFilter.value;
  const search = els.fileSearch.value.trim().toLowerCase();
  return (detail.files || []).filter((file) => {
    const categoryOk = category === "all" || file.category === category;
    const searchOk = !search || file.path.toLowerCase().includes(search);
    return categoryOk && searchOk;
  });
}

function renderFileTable() {
  const files = filteredFiles();
  if (!files.length) {
    els.fileRows.innerHTML = `<tr><td colspan="5" class="emptyCell">No files match the current filter</td></tr>`;
    return;
  }
  const visible = files.slice(0, 300);
  els.fileRows.innerHTML = visible
    .map((file) => {
      const primaryAction = file.kind === "json" ? "Preview" : file.kind === "image" ? "Open" : file.kind === "csv" || file.kind === "text" ? "View" : "Download";
      const primaryKind = file.kind === "json" ? "json" : file.kind === "image" ? "open" : file.kind === "csv" || file.kind === "text" ? "text" : "download";
      return `
        <tr>
          <td><span class="fileName">${escapeHtml(file.name)}</span><small>${escapeHtml(file.path)}</small></td>
          <td><span class="typePill">${escapeHtml(file.category)} · ${escapeHtml(file.kind)}</span></td>
          <td>${formatBytes(file.size_bytes)}</td>
          <td>${formatDate(file.modified_at)}</td>
          <td class="actionCell">
            <button type="button" data-action="${primaryKind}" data-path="${escapeHtml(file.path)}">${primaryAction}</button>
            <button type="button" data-action="download" data-path="${escapeHtml(file.path)}">Download</button>
          </td>
        </tr>
      `;
    })
    .join("");
  if (files.length > visible.length) {
    els.fileRows.insertAdjacentHTML("beforeend", `<tr><td colspan="5" class="emptyCell">Showing 300 of ${files.length} matching files</td></tr>`);
  }
}

async function loadJson(path) {
  if (!state.selectedSessionId) return;
  try {
    const data = await api(`/api/autotest/sessions/${encodeURIComponent(state.selectedSessionId)}/json/${encodedPath(path)}`);
    state.selectedJsonPath = path;
    state.lastJsonText = JSON.stringify(data.content, null, 2);
    els.jsonTitle.textContent = data.summary?.device?.name || data.summary?.name || "JSON Preview";
    els.jsonSubtitle.textContent = `${data.path} · ${formatBytes(data.size_bytes)}`;
    els.jsonPreview.innerHTML = syntaxHighlight(state.lastJsonText);
    renderJsonList();
  } catch (error) {
    log("JSON preview failed", { path, message: error.message });
  }
}

async function loadText(path) {
  if (!state.selectedSessionId) return;
  try {
    const data = await api(`/api/autotest/sessions/${encodeURIComponent(state.selectedSessionId)}/text/${encodedPath(path)}`);
    state.selectedJsonPath = "";
    state.lastJsonText = data.content;
    els.jsonTitle.textContent = data.path.split("/").pop();
    els.jsonSubtitle.textContent = `${data.path} · ${formatBytes(data.size_bytes)}`;
    els.jsonPreview.textContent = data.content;
    renderJsonList();
  } catch (error) {
    log("Text preview failed", { path, message: error.message });
  }
}

function syntaxHighlight(jsonText) {
  const escaped = escapeHtml(jsonText);
  return escaped.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "jsonNumber";
      if (match.startsWith('"')) cls = match.endsWith(":") ? "jsonKey" : "jsonString";
      else if (match === "true" || match === "false") cls = "jsonBoolean";
      else if (match === "null") cls = "jsonNull";
      return `<span class="${cls}">${match}</span>`;
    },
  );
}

async function refreshConnections() {
  try {
    const data = await api("/api/connections");
    els.connectionSummary.textContent = `${data.active_http_requests} active · ${data.total_http_requests} requests · ${data.total_file_downloads} downloads`;
  } catch {
    els.connectionSummary.textContent = "Connection telemetry unavailable";
  }
}

function saveTokenNow() {
  localStorage.setItem("probeWebToken", els.token.value);
  log("Token saved");
  refreshSessions({ keepSelection: false });
  refreshConnections();
}

els.saveToken.addEventListener("click", saveTokenNow);
els.token.addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveTokenNow();
});
els.token.addEventListener("input", () => {
  window.clearTimeout(tokenSaveTimer);
  tokenSaveTimer = window.setTimeout(() => {
    localStorage.setItem("probeWebToken", els.token.value);
  }, 300);
});

els.refreshSessions.addEventListener("click", () => refreshSessions({ keepSelection: false }));
els.sessionList.addEventListener("click", (event) => {
  const item = event.target.closest("[data-session-id]");
  if (item) selectSession(item.dataset.sessionId);
});
els.categoryStrip.addEventListener("click", (event) => {
  const item = event.target.closest("[data-filter]");
  if (!item) return;
  els.categoryFilter.value = item.dataset.filter;
  renderFileTable();
});
els.categoryFilter.addEventListener("change", renderFileTable);
els.fileSearch.addEventListener("input", renderFileTable);
els.jsonList.addEventListener("click", (event) => {
  const item = event.target.closest("[data-json-path]");
  if (item) loadJson(item.dataset.jsonPath);
});
els.fileRows.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || !state.selectedSessionId) return;
  const path = button.dataset.path;
  const action = button.dataset.action;
  if (action === "json") loadJson(path);
  if (action === "text") loadText(path);
  if (action === "open") window.open(fileUrl(state.selectedSessionId, path), "_blank", "noreferrer");
  if (action === "download") window.open(fileUrl(state.selectedSessionId, path, { download: true }), "_blank", "noreferrer");
});
els.copyJson.addEventListener("click", async () => {
  if (!state.lastJsonText) return;
  await navigator.clipboard.writeText(state.lastJsonText);
  log("Preview copied");
});

setInterval(() => {
  els.clock.textContent = new Date().toLocaleTimeString();
}, 1000);

refreshSessions({ keepSelection: false });
refreshConnections();
setInterval(() => refreshSessions({ keepSelection: true }), 15000);
setInterval(refreshConnections, 7000);
