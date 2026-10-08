const workDirEl = document.getElementById("work-dir");
const btnScan = document.getElementById("btn-scan");
const btnStop = document.getElementById("btn-stop");
const btnApply = document.getElementById("btn-apply");
const btnDismiss = document.getElementById("btn-dismiss");
const btnToggleMaybe = document.getElementById("btn-toggle-maybe");
const scanMeta = document.getElementById("scan-meta");
const summaryEl = document.getElementById("summary");
const cleanSection = document.getElementById("clean-section");
const cleanList = document.getElementById("clean-list");
const maybeSection = document.getElementById("maybe-section");
const maybeBody = document.getElementById("maybe-body");
const maybeList = document.getElementById("maybe-list");
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
/** @type {Map<string, "file"|"folder">} */
const selectedDeletes = new Map();

function formatSize(bytes) {
  const n = Math.abs(Number(bytes) || 0);
  if (n < 1024) return `${n} o`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} Ko`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} Mo`;
  return `${(n / 1024 ** 3).toFixed(2)} Go`;
}

function formatSaved(bytes) {
  return `−${formatSize(Math.abs(Number(bytes) || 0))}`;
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
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
  cleanSection.classList.add("hidden");
  maybeSection.classList.add("hidden");
  maybeBody.classList.add("hidden");
  cleanList.innerHTML = "";
  maybeList.innerHTML = "";
  actionsEl.classList.add("hidden");
}

function refreshActionsMeta() {
  if (!lastResult || !selectedDeletes.size) {
    actionsEl.classList.add("hidden");
    return;
  }
  let bytes = 0;
  let nFolders = 0;
  let nFiles = 0;
  selectedDeletes.forEach((kind, path) => {
    if (kind === "folder") {
      nFolders += 1;
      (lastResult.folder_groups || []).forEach((g) => {
        (g.delete || []).forEach((d) => {
          if (d.path === path) bytes += d.bytes_total || 0;
        });
      });
    } else {
      nFiles += 1;
      (lastResult.file_groups || []).forEach((g) => {
        (g.delete || []).forEach((d) => {
          if (d.path === path) bytes += d.size || 0;
        });
      });
    }
  });
  actionsEl.classList.remove("hidden");
  const bits = [];
  if (nFolders) bits.push(`${nFolders} dossier(s)`);
  if (nFiles) bits.push(`${nFiles} fichier(s)`);
  actionsMeta.textContent = `${bits.join(" · ")} cochés · ${formatSaved(bytes)}`;
}

function bindPick(root) {
  root.querySelectorAll("input.pick-cb").forEach((cb) => {
    cb.addEventListener("change", () => {
      const path = cb.dataset.path;
      const kind = cb.dataset.kind === "folder" ? "folder" : "file";
      if (cb.checked) selectedDeletes.set(path, kind);
      else selectedDeletes.delete(path);
      refreshActionsMeta();
    });
  });
}

function renderFolderCard(g) {
  const keep = g.keep || {};
  const card = document.createElement("div");
  card.className = "card";
  const delLanes = (g.delete || [])
    .map((d) => {
      selectedDeletes.set(d.path, "folder");
      return `
        <div class="lane del-lane">
          <div class="lane-label">Supprimer</div>
          <div>
            <div>${d.file_count || 0} media · ${formatSize(d.bytes_total)} · maj ${fmtDate(d.mtime_max)}</div>
            <div class="path">${escapeHtml(d.path)}</div>
            <label class="pick"><input class="pick-cb" type="checkbox" data-kind="folder" data-path="${escapeHtml(d.path)}" checked> Inclure dans la suppression</label>
          </div>
        </div>`;
    })
    .join("");
  card.innerHTML = `
    <div class="card-head">📁 <strong>Dossier en double</strong> · ${g.file_count || 0} media · ${formatSize(g.bytes_total)}</div>
    <div class="lane keep-lane">
      <div class="lane-label">Garder</div>
      <div>
        <div>maj ${fmtDate(keep.mtime_max)} · ${keep.file_count || 0} media</div>
        <div class="path">${escapeHtml(keep.path || "")}</div>
      </div>
    </div>
    ${delLanes}
  `;
  return card;
}

function renderFileCard(g) {
  const keep = g.keep || {};
  const card = document.createElement("div");
  card.className = "card";
  const kind = g.kind === "photo" ? "📷" : "🎬";
  const delLanes = (g.delete || [])
    .map((d) => {
      selectedDeletes.set(d.path, "file");
      return `
        <div class="lane del-lane">
          <div class="lane-label">Supprimer</div>
          <div>
            <div>${formatSize(d.size)}</div>
            <div class="path">${escapeHtml(d.path)}</div>
            <label class="pick"><input class="pick-cb" type="checkbox" data-kind="file" data-path="${escapeHtml(d.path)}" checked> Inclure dans la suppression</label>
          </div>
        </div>`;
    })
    .join("");
  card.innerHTML = `
    <div class="card-head">${kind} <strong>${escapeHtml(keep.name || g.name || "fichier")}</strong> · ${formatSize(g.size)} · ${g.count}×</div>
    <div class="lane keep-lane">
      <div class="lane-label">Garder</div>
      <div class="path">${escapeHtml(keep.path || "")}</div>
    </div>
    ${delLanes}
  `;
  return card;
}

function renderResult(data) {
  lastResult = data;
  selectedDeletes.clear();

  const nFolders = data.folder_group_count || 0;
  const nFiles = data.file_group_count || 0;
  const nMaybe = (data.similar_folders || []).length;
  const reclaim =
    (data.bytes_reclaimable_folders || 0) + (data.bytes_reclaimable_files || 0);

  summaryEl.classList.remove("hidden");
  summaryEl.innerHTML = `
    <div class="big"><strong>${data.files_scanned || 0}</strong> media analysés
      (${data.photos || 0} photos · ${data.videos || 0} vidéos)</div>
    <div style="margin-top:0.4rem">
      <strong>${nFolders}</strong> dossier(s) en double ·
      <strong>${nFiles}</strong> fichier(s) en double ·
      espace récupérable <strong>${formatSaved(reclaim)}</strong>
    </div>
    ${nMaybe ? `<div style="margin-top:0.35rem;color:var(--text-dim)">${nMaybe} cas ambigu(s) listés plus bas (pas de suppression proposée)</div>` : ""}
  `;
  scanMeta.textContent = data.work_dir || "";

  cleanList.innerHTML = "";
  const hasClean = nFolders > 0 || nFiles > 0;
  if (hasClean) {
    cleanSection.classList.remove("hidden");
    (data.folder_groups || []).forEach((g) => cleanList.appendChild(renderFolderCard(g)));
    (data.file_groups || []).forEach((g) => cleanList.appendChild(renderFileCard(g)));
    bindPick(cleanList);
  } else {
    cleanSection.classList.add("hidden");
  }

  maybeList.innerHTML = "";
  if (nMaybe) {
    maybeSection.classList.remove("hidden");
    maybeBody.classList.add("hidden");
    btnToggleMaybe.textContent = `▸ Voir ${nMaybe} cas ambigu(s) (lecture seule)`;
    (data.similar_folders || []).forEach((g) => {
      const el = document.createElement("div");
      el.className = "maybe-card";
      el.innerHTML = `
        <div><strong>Même fichiers, dossiers différents</strong></div>
        <div style="margin-top:0.35rem">A · <span class="path">${escapeHtml((g.keep && g.keep.path) || "")}</span></div>
        <div>B · <span class="path">${escapeHtml((g.other && g.other.path) || "")}</span>
          · ${formatSize((g.other && g.other.bytes_total) || 0)}</div>
      `;
      maybeList.appendChild(el);
    });
  } else {
    maybeSection.classList.add("hidden");
  }

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
      const r = st.result;
      if (
        !(r.file_group_count > 0) &&
        !(r.folder_group_count > 0) &&
        !(r.similar_folders || []).length
      ) {
        clearResults();
        scanMeta.textContent = `Rien à nettoyer (${r.files_scanned || 0} media).`;
      } else {
        renderResult(r);
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
  selectedDeletes.clear();
  cleanList.querySelectorAll("input.pick-cb").forEach((cb) => {
    cb.checked = false;
  });
  refreshActionsMeta();
});

if (btnToggleMaybe) {
  btnToggleMaybe.addEventListener("click", () => {
    const open = !maybeBody.classList.contains("hidden");
    maybeBody.classList.toggle("hidden", open);
    const n = (lastResult && lastResult.similar_folders) || [];
    btnToggleMaybe.textContent = open
      ? `▸ Voir ${n.length} cas ambigu(s) (lecture seule)`
      : `▾ Masquer les cas ambigus`;
  });
}

btnApply.addEventListener("click", async () => {
  if (!selectedDeletes.size) return;
  const folderPaths = [];
  const filePaths = [];
  selectedDeletes.forEach((kind, path) => {
    if (kind === "folder") folderPaths.push(path);
    else filePaths.push(path);
  });
  if (
    !confirm(
      `Supprimer définitivement ?\n${folderPaths.length} dossier(s)\n${filePaths.length} fichier(s)`
    )
  ) {
    return;
  }
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
    runRecap.innerHTML = `<strong>OK</strong> — ${res.deleted || 0} supprimé(s) · ${formatSaved(res.bytes_freed || 0)}`;
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
