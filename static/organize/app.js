const workDirEl = document.getElementById("work-dir");
const btnScan = document.getElementById("btn-scan");
const btnStop = document.getElementById("btn-stop");
const btnApply = document.getElementById("btn-apply");
const btnDismiss = document.getElementById("btn-dismiss");
const scanMeta = document.getElementById("scan-meta");
const summaryEl = document.getElementById("summary");
const folderSection = document.getElementById("folder-section");
const folderList = document.getElementById("folder-list");
const similarSection = document.getElementById("similar-section");
const similarList = document.getElementById("similar-list");
const fileSection = document.getElementById("file-section");
const fileList = document.getElementById("file-list");
const logEl = document.getElementById("log");
const actionsEl = document.getElementById("actions");
const actionsMeta = document.getElementById("actions-meta");
const runRecap = document.getElementById("run-recap");
const statusBadge = document.getElementById("status-badge");
const savingsBadge = document.getElementById("savings-badge");
const themeSelect = document.getElementById("theme-select");

let jobId = null;
let pollTimer = null;
let lastResult = null;
/** @type {Set<string>} */
const selectedDeletes = new Set();

function formatSize(bytes) {
  const n = Math.abs(Number(bytes) || 0);
  if (n < 1024) return `${n} o`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} Ko`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} Mo`;
  return `${(n / 1024 ** 3).toFixed(2)} Go`;
}

function formatSaved(bytes) {
  const n = Number(bytes) || 0;
  return `−${formatSize(Math.abs(n))}`;
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function applyTheme(t) {
  const theme = t === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("jaj-organize-theme", theme);
  } catch (_) {}
  if (themeSelect) themeSelect.value = theme;
}

function initTheme() {
  let saved = "dark";
  try {
    saved = localStorage.getItem("jaj-organize-theme") || "dark";
  } catch (_) {}
  applyTheme(saved);
  if (themeSelect) {
    themeSelect.addEventListener("change", () => applyTheme(themeSelect.value));
  }
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch (_) {}
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

function setLog(lines, message) {
  const text =
    (lines && lines.length ? lines.join("\n") : "") +
    (message ? `\n› ${message}` : "");
  logEl.textContent = text || "—";
  logEl.scrollTop = logEl.scrollHeight;
}

function setRunning(running) {
  btnScan.disabled = running;
  btnStop.classList.toggle("hidden", !running);
  statusBadge.textContent = running ? "Analyse…" : "Prêt";
  statusBadge.classList.toggle("ok", running);
}

function clearResults() {
  lastResult = null;
  selectedDeletes.clear();
  summaryEl.classList.add("hidden");
  folderSection.classList.add("hidden");
  similarSection.classList.add("hidden");
  fileSection.classList.add("hidden");
  folderList.innerHTML = "";
  similarList.innerHTML = "";
  fileList.innerHTML = "";
  actionsEl.classList.add("hidden");
}

function toggleSel(path, on) {
  if (on) selectedDeletes.add(path);
  else selectedDeletes.delete(path);
  refreshActionsMeta();
}

function refreshActionsMeta() {
  if (!lastResult) {
    actionsEl.classList.add("hidden");
    return;
  }
  let bytes = 0;
  const folders = new Set();
  (lastResult.folder_groups || []).forEach((g) => {
    (g.delete || []).forEach((d) => {
      if (selectedDeletes.has(d.path)) {
        bytes += d.bytes_total || 0;
        folders.add(d.path);
      }
    });
  });
  (lastResult.file_groups || []).forEach((g) => {
    (g.delete || []).forEach((d) => {
      if (selectedDeletes.has(d.path)) bytes += d.size || 0;
    });
  });
  const n = selectedDeletes.size;
  if (n === 0) {
    actionsEl.classList.add("hidden");
    return;
  }
  actionsEl.classList.remove("hidden");
  actionsMeta.textContent = `${n} élément(s) sélectionné(s) · ${formatSaved(bytes)} récupérables`;
}

function renderResult(data) {
  lastResult = data;
  selectedDeletes.clear();
  const reclaim = (data.bytes_reclaimable || 0);
  summaryEl.classList.remove("hidden");
  summaryEl.innerHTML = `
    <strong>${data.files_scanned || 0}</strong> media
    (${data.photos || 0} photos · ${data.videos || 0} vidéos)<br>
    Doublons fichiers: <strong>${data.file_group_count || 0}</strong> groupes
    (${data.file_delete_count || 0} fichiers · ${formatSaved(data.bytes_reclaimable_files || 0)})<br>
    Doublons dossiers: <strong>${data.folder_group_count || 0}</strong> groupes
    (${data.folder_delete_count || 0} dossiers · ${formatSaved(data.bytes_reclaimable_folders || 0)})<br>
    Total potentiel: <strong>${formatSaved(reclaim)}</strong>
  `;
  scanMeta.textContent = `Analyse de ${data.work_dir}`;

  // Folders
  const fgs = data.folder_groups || [];
  if (fgs.length) {
    folderSection.classList.remove("hidden");
    folderList.innerHTML = "";
    fgs.forEach((g, idx) => {
      const keep = g.keep || {};
      const card = document.createElement("div");
      card.className = "card";
      const dels = (g.delete || [])
        .map((d) => {
          const id = `fd-${idx}-${escapeHtml(d.path)}`;
          selectedDeletes.add(d.path);
          return `<label class="row"><input type="checkbox" data-path="${escapeHtml(d.path)}" data-kind="folder" checked>
            <span class="del">Supprimer dossier<br><span class="path">${escapeHtml(d.path)}</span>
            · ${d.file_count} fichiers · ${formatSize(d.bytes_total)} · maj ${new Date((d.mtime_max || 0) * 1000).toLocaleString()}</span></label>`;
        })
        .join("");
      card.innerHTML = `
        <div><strong>Groupe dossier</strong> · ${g.file_count} media · ${formatSize(g.bytes_total)}</div>
        <div class="keep">✓ Garder<br><span class="path">${escapeHtml(keep.path || "")}</span>
        · maj ${new Date((keep.mtime_max || 0) * 1000).toLocaleString()}</div>
        ${dels}
        <div class="meta" style="margin-top:0.35rem">${escapeHtml(g.reason || "")}</div>
      `;
      folderList.appendChild(card);
    });
  } else folderSection.classList.add("hidden");

  // Similar
  const sims = data.similar_folders || [];
  if (sims.length) {
    similarSection.classList.remove("hidden");
    similarList.innerHTML = "";
    sims.forEach((g) => {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <div class="keep">A (plus récent / propre)<br><span class="path">${escapeHtml((g.keep && g.keep.path) || "")}</span></div>
        <div class="del" style="margin-top:0.35rem">B (similaire — non coché par défaut)<br><span class="path">${escapeHtml((g.other && g.other.path) || "")}</span>
        · ${formatSize((g.other && g.other.bytes_total) || 0)}</div>
        <div class="meta">${escapeHtml(g.reason || "")}</div>
      `;
      similarList.appendChild(card);
    });
  } else similarSection.classList.add("hidden");

  // Files
  const figs = data.file_groups || [];
  if (figs.length) {
    fileSection.classList.remove("hidden");
    fileList.innerHTML = "";
    figs.forEach((g, idx) => {
      const keep = g.keep || {};
      const card = document.createElement("div");
      card.className = "card";
      const dels = (g.delete || [])
        .map((d) => {
          selectedDeletes.add(d.path);
          return `<label class="row"><input type="checkbox" data-path="${escapeHtml(d.path)}" data-kind="file" checked>
            <span class="del">Supprimer<br><span class="path">${escapeHtml(d.path)}</span> · ${formatSize(d.size)}</span></label>`;
        })
        .join("");
      card.innerHTML = `
        <div><strong>${escapeHtml(keep.name || g.name)}</strong> · ${g.kind || "?"} · ${formatSize(g.size)} · ${g.count}×</div>
        <div class="keep">✓ Garder<br><span class="path">${escapeHtml(keep.path || "")}</span></div>
        ${dels}
      `;
      fileList.appendChild(card);
    });
  } else fileSection.classList.add("hidden");

  document.querySelectorAll('input[type="checkbox"][data-path]').forEach((cb) => {
    cb.addEventListener("change", () => toggleSel(cb.getAttribute("data-path"), cb.checked));
  });
  refreshActionsMeta();
}

async function pollStatus() {
  try {
    const q = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    const st = await api(`/api/organize/status${q}`);
    setLog(st.log || [], st.message || "");
    if (st.id) jobId = st.id;
    if (st.state === "running") {
      setRunning(true);
      return;
    }
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    setRunning(false);
    if (st.state === "done" && st.result) {
      if (
        !(st.result.file_group_count > 0) &&
        !(st.result.folder_group_count > 0) &&
        !(st.result.similar_folders || []).length
      ) {
        clearResults();
        scanMeta.textContent = `Aucun doublon trouvé (${st.result.files_scanned || 0} media scannés).`;
        alert(`Aucun doublon trouvé.\n(${st.result.files_scanned || 0} media)`);
      } else {
        renderResult(st.result);
      }
    } else if (st.state === "error") {
      alert(`Organize: ${st.error || st.message}`);
    } else if (st.state === "cancelled") {
      scanMeta.textContent = "Analyse annulée.";
    }
  } catch (e) {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    setRunning(false);
    alert(`Status: ${e.message}`);
  }
}

btnScan.addEventListener("click", async () => {
  clearResults();
  runRecap.classList.add("hidden");
  setRunning(true);
  setLog([], "Démarrage…");
  try {
    const job = await api("/api/organize/scan", {
      method: "POST",
      body: JSON.stringify({ work_dir: workDirEl.value.trim() }),
    });
    jobId = job.id || null;
    setLog(job.log || [], job.message || "");
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollStatus, 800);
    await pollStatus();
  } catch (e) {
    setRunning(false);
    alert(`Analyse: ${e.message}`);
  }
});

btnStop.addEventListener("click", async () => {
  try {
    await api("/api/organize/cancel", { method: "POST", body: "{}" });
    setLog(null, "Annulation demandée…");
  } catch (e) {
    alert(`Stop: ${e.message}`);
  }
});

btnDismiss.addEventListener("click", () => {
  clearResults();
  scanMeta.textContent = "Récap ignoré.";
});

btnApply.addEventListener("click", async () => {
  if (!selectedDeletes.size) return;
  const folderPaths = [];
  const filePaths = [];
  (lastResult.folder_groups || []).forEach((g) => {
    (g.delete || []).forEach((d) => {
      if (selectedDeletes.has(d.path)) folderPaths.push(d.path);
    });
  });
  (lastResult.file_groups || []).forEach((g) => {
    (g.delete || []).forEach((d) => {
      if (selectedDeletes.has(d.path)) filePaths.push(d.path);
    });
  });
  const msg =
    `Supprimer ${folderPaths.length} dossier(s) et ${filePaths.length} fichier(s) ?\n` +
    `Irréversible.`;
  if (!confirm(msg)) return;
  btnApply.disabled = true;
  try {
    const res = await api("/api/organize/delete", {
      method: "POST",
      body: JSON.stringify({
        work_dir: workDirEl.value.trim(),
        folders: folderPaths,
        files: filePaths,
      }),
    });
    clearResults();
    if (res.savings) {
      savingsBadge.textContent = `Économisé: ${formatSaved(res.savings.bytes_saved || 0)} · ${res.savings.files_replaced || 0}`;
      savingsBadge.classList.add("ok");
    }
    runRecap.classList.remove("hidden");
    runRecap.innerHTML = `<strong>Dédoublonnage OK</strong> — ${res.deleted || 0} élément(s) · ${formatSaved(res.bytes_freed || 0)}`;
    alert(`OK: ${res.deleted || 0} supprimé(s) · ${formatSaved(res.bytes_freed || 0)}`);
  } catch (e) {
    alert(`Suppression: ${e.message}`);
  } finally {
    btnApply.disabled = false;
  }
});

initTheme();
api("/api/config")
  .then((cfg) => {
    if (cfg.work_dir) workDirEl.value = cfg.work_dir;
  })
  .catch(() => {});
api("/api/session")
  .then((s) => {
    if (s.savings && s.savings.bytes_saved) {
      savingsBadge.textContent = `Économisé: ${formatSaved(s.savings.bytes_saved)} · ${s.savings.files_replaced || 0}`;
      savingsBadge.classList.add("ok");
    }
    if (s.work_dir && !workDirEl.value) workDirEl.value = s.work_dir;
  })
  .catch(() => {});
