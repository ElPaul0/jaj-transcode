const workDirEl = document.getElementById("work-dir");
const btnScan = document.getElementById("btn-scan");
const btnStop = document.getElementById("btn-stop");
const btnApply = document.getElementById("btn-apply");
const btnDismiss = document.getElementById("btn-dismiss");
const btnToggleMaybe = document.getElementById("btn-toggle-maybe");
const scanMeta = document.getElementById("scan-meta");
const summaryEl = document.getElementById("summary");
const inboxSection = document.getElementById("inbox-section");
const inboxList = document.getElementById("inbox-list");
const inboxPlan = document.getElementById("inbox-plan");
const btnDepAll = document.getElementById("btn-dep-all");
const btnDepNone = document.getElementById("btn-dep-none");
/** Index du dernier dossier cliqué (pour Maj+clic) */
let lastDepIndex = null;
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
/** Chemins issus du plan « dossiers défavorisés » */
const folderPlanDeletes = new Set();

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
  folderPlanDeletes.clear();
  summaryEl.classList.add("hidden");
  inboxSection.classList.add("hidden");
  cleanSection.classList.add("hidden");
  maybeSection.classList.add("hidden");
  maybeBody.classList.add("hidden");
  inboxList.innerHTML = "";
  inboxPlan.classList.add("hidden");
  inboxPlan.textContent = "";
  cleanList.innerHTML = "";
  maybeList.innerHTML = "";
  actionsEl.classList.add("hidden");
}

function underFolder(path, folder) {
  if (!path || !folder) return false;
  const p = String(path).replace(/\\/g, "/");
  const f = String(folder).replace(/\\/g, "/").replace(/\/+$/, "");
  return p === f || p.startsWith(f + "/");
}

function underAny(path, folders) {
  for (const f of folders) {
    if (underFolder(path, f)) return true;
  }
  return false;
}

function getDeprioritized() {
  const set = new Set();
  inboxList.querySelectorAll("input.dep-cb:checked").forEach((cb) => {
    if (cb.dataset.path) set.add(cb.dataset.path);
  });
  return set;
}

function lookupDeleteBytes(path, kind) {
  if (!lastResult) return 0;
  if (kind === "folder") {
    for (const g of lastResult.folder_groups || []) {
      for (const d of g.delete || []) {
        if (d.path === path) return d.bytes_total || 0;
      }
    }
    return 0;
  }
  for (const g of lastResult.inbox_folders || []) {
    for (const d of g.delete || []) {
      if (d.path === path) return d.size || 0;
    }
  }
  for (const g of lastResult.file_groups || []) {
    for (const d of g.delete || []) {
      if (d.path === path) return d.size || 0;
    }
    for (const m of g.members || []) {
      if (m.path === path) return m.size || 0;
    }
  }
  return 0;
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
    if (kind === "folder") nFolders += 1;
    else nFiles += 1;
    bytes += lookupDeleteBytes(path, kind);
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

/** Rebuild file deletes from checked « défavoriser » folders. */
function applyFolderPrefs() {
  if (!lastResult) return;
  const dep = getDeprioritized();

  folderPlanDeletes.forEach((p) => selectedDeletes.delete(p));
  folderPlanDeletes.clear();

  let planBytes = 0;
  let planFiles = 0;
  const folders = lastResult.inbox_folders || [];

  for (const g of folders) {
    if (!dep.has(g.path)) continue;
    for (const f of g.delete || []) {
      const elsePaths = (f.elsewhere || []).map((e) => e.path || e).filter(Boolean);
      const safeKeep = elsePaths.filter((p) => !underAny(p, dep));
      if (safeKeep.length > 0) {
        selectedDeletes.set(f.path, "file");
        folderPlanDeletes.add(f.path);
        planBytes += f.size || 0;
        planFiles += 1;
        continue;
      }
      // Toutes les copies sont dans des dossiers défavorisés : en garder une
      if (!elsePaths.length) continue;
      const candidates = [f.path, ...elsePaths];
      const keep = candidates.slice().sort((a, b) => {
        const da = underAny(a, dep) ? 1 : 0;
        const db = underAny(b, dep) ? 1 : 0;
        if (da !== db) return da - db;
        return a.length - b.length || a.localeCompare(b);
      })[0];
      if (f.path !== keep) {
        selectedDeletes.set(f.path, "file");
        folderPlanDeletes.add(f.path);
        planBytes += f.size || 0;
        planFiles += 1;
      }
    }
  }

  inboxList.querySelectorAll(".folder-row").forEach((row) => {
    const cb = row.querySelector("input.dep-cb");
    row.classList.toggle("is-dep", !!(cb && cb.checked));
  });

  if (dep.size && planFiles) {
    inboxPlan.classList.remove("hidden");
    inboxPlan.innerHTML =
      `<strong>${dep.size}</strong> dossier(s) défavorisé(s) → ` +
      `<strong>${planFiles}</strong> fichier(s) à supprimer · ${formatSaved(planBytes)}`;
  } else if (dep.size) {
    inboxPlan.classList.remove("hidden");
    inboxPlan.textContent =
      "Aucun fichier supprimable : pas de copie sûre hors des dossiers cochés.";
  } else {
    inboxPlan.classList.add("hidden");
    inboxPlan.textContent = "";
  }

  renderCleanSection();
  refreshActionsMeta();
}

function depCheckboxes() {
  return [...inboxList.querySelectorAll("input.dep-cb")];
}

function setAllDep(checked) {
  depCheckboxes().forEach((cb) => {
    cb.checked = !!checked;
  });
  lastDepIndex = null;
  applyFolderPrefs();
}

function renderFolderPicker(folders) {
  inboxList.innerHTML = "";
  lastDepIndex = null;
  folders.forEach((g) => {
    const row = document.createElement("label");
    row.className = "folder-row" + (g.suggested ? " is-dep" : "");
    const pct = Math.round((g.ratio || 0) * 100);
    row.innerHTML = `
      <input type="checkbox" class="dep-cb" data-path="${escapeHtml(g.path)}"
        ${g.suggested ? "checked" : ""}>
      <div>
        <div class="fname">${escapeHtml(g.name || "Dossier")}
          ${g.suggested ? '<span class="badge-sug">suggéré</span>' : ""}</div>
        <div class="fpath">${escapeHtml(g.path)}</div>
      </div>
      <div class="stats">
        <div class="dup">${g.dup_count} déjà ailleurs</div>
        <div>${g.dup_count}/${g.file_count} · ${pct}%</div>
      </div>
      <div class="stats">
        <div>${formatSaved(g.bytes_reclaimable)}</div>
        <div style="color:var(--text-dim)">${g.unique_count || 0} unique(s)</div>
      </div>
    `;
    const cb = row.querySelector("input.dep-cb");
    cb.addEventListener("click", (e) => {
      const boxes = depCheckboxes();
      const idx = boxes.indexOf(cb);
      if (e.shiftKey && lastDepIndex != null && idx >= 0) {
        const state = cb.checked;
        const lo = Math.min(lastDepIndex, idx);
        const hi = Math.max(lastDepIndex, idx);
        for (let i = lo; i <= hi; i++) boxes[i].checked = state;
      }
      if (idx >= 0) lastDepIndex = idx;
      applyFolderPrefs();
    });
    inboxList.appendChild(row);
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
            <label class="pick"><input class="pick-cb" type="checkbox" data-kind="folder" data-path="${escapeHtml(d.path)}" checked> Inclure</label>
          </div>
        </div>`;
    })
    .join("");
  card.innerHTML = `
    <div class="card-head">📁 <strong>Dossier cloné à l’identique</strong> · ${g.file_count || 0} media · ${formatSize(g.bytes_total)}</div>
    <div class="lane keep-lane">
      <div class="lane-label">Garder</div>
      <div>
        <div>maj ${fmtDate(keep.mtime_max)}</div>
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
            <label class="pick"><input class="pick-cb" type="checkbox" data-kind="file" data-path="${escapeHtml(d.path)}" checked> Inclure</label>
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

function rankMember(m, dep) {
  const path = m.path || "";
  const depPen = underAny(path, dep) ? 1 : 0;
  const staging = m.dir && /à trier|a trier|download|télécharg/i.test(path) ? 1 : 0;
  return [depPen, staging, path.length, path.toLowerCase()];
}

function cmpRank(a, b) {
  for (let i = 0; i < a.length; i++) {
    if (a[i] < b[i]) return -1;
    if (a[i] > b[i]) return 1;
  }
  return 0;
}

function renderCleanSection() {
  if (!lastResult) return;
  const dep = getDeprioritized();

  // Retirer les anciennes sélections « clean » (dossiers clonés + fichiers hors plan)
  const toClear = [];
  selectedDeletes.forEach((kind, path) => {
    if (kind === "folder") toClear.push(path);
    else if (!folderPlanDeletes.has(path)) toClear.push(path);
  });
  toClear.forEach((p) => selectedDeletes.delete(p));

  cleanList.innerHTML = "";
  let nCards = 0;

  (lastResult.folder_groups || []).forEach((g) => {
    cleanList.appendChild(renderFolderCard(g));
    nCards += 1;
  });

  (lastResult.file_groups || []).forEach((g) => {
    const members = g.members && g.members.length
      ? g.members.slice()
      : [g.keep, ...(g.delete || [])].filter(Boolean);
    if (members.length < 2) return;

    // Si une copie est déjà planifiée via dossiers défavorisés, ne pas re-proposer ici
    if (members.some((m) => folderPlanDeletes.has(m.path))) return;

    const ordered = members.slice().sort((a, b) => cmpRank(rankMember(a, dep), rankMember(b, dep)));
    const keep = ordered[0];
    const toDelete = ordered.slice(1).filter((m) => !folderPlanDeletes.has(m.path));
    if (!toDelete.length) return;

    const card = renderFileCard({
      ...g,
      keep,
      delete: toDelete,
      count: members.length,
    });
    cleanList.appendChild(card);
    nCards += 1;
  });

  if (nCards) {
    cleanSection.classList.remove("hidden");
    bindPick(cleanList);
  } else {
    cleanSection.classList.add("hidden");
  }
}

function renderResult(data) {
  lastResult = data;
  selectedDeletes.clear();
  folderPlanDeletes.clear();

  const folders = data.inbox_folders || [];
  const nInbox = folders.length;
  const nFolders = data.folder_group_count || 0;
  const nFiles = data.file_group_count || 0;
  const nMaybe = (data.similar_folders || []).length;
  const suggested = folders.filter((g) => g.suggested).length;

  summaryEl.classList.remove("hidden");
  summaryEl.innerHTML = `
    <div class="big"><strong>${data.files_scanned || 0}</strong> media
      (${data.photos || 0} photos · ${data.videos || 0} vidéos)</div>
    <div style="margin-top:0.4rem">
      <strong>${nInbox}</strong> dossier(s) avec doublons
      (${suggested} suggéré(s)) ·
      <strong>${nFolders}</strong> dossier(s) clonés ·
      <strong>${nFiles}</strong> groupe(s) fichiers
    </div>
  `;
  scanMeta.textContent = data.work_dir || "";

  if (nInbox) {
    inboxSection.classList.remove("hidden");
    renderFolderPicker(folders);
  } else {
    inboxSection.classList.add("hidden");
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

  applyFolderPrefs();
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
        !(r.inbox_folder_count > 0) &&
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
      clearResults();
      scanMeta.textContent = st.error || "Erreur";
    } else if (st.state === "cancelled") {
      scanMeta.textContent = "Analyse annulée.";
    }
  } catch (e) {
    setRunning(false);
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    setLog(null, `Erreur: ${e.message}`);
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

if (btnDepAll) btnDepAll.addEventListener("click", () => setAllDep(true));
if (btnDepNone) btnDepNone.addEventListener("click", () => setAllDep(false));

btnDismiss.addEventListener("click", () => {
  setAllDep(false);
  selectedDeletes.clear();
  folderPlanDeletes.clear();
  document.querySelectorAll("input.pick-cb").forEach((cb) => {
    cb.checked = false;
  });
  inboxPlan.classList.add("hidden");
  inboxPlan.textContent = "";
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

(async () => {
  try {
    const cfg = await api("/api/config");
    if (cfg.work_dir && !workDirEl.value) workDirEl.value = cfg.work_dir;
  } catch (_) {}
})();
