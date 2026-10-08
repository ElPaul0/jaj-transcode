const workDirEl = document.getElementById("work-dir");
const btnScan = document.getElementById("btn-scan");
const btnSelectAll = document.getElementById("btn-select-all");
const btnSelectNone = document.getElementById("btn-select-none");
const btnEncode = document.getElementById("btn-encode");
const btnStopAll = document.getElementById("btn-stop-all");
const fileListEl = document.getElementById("file-list");
const scanMetaEl = document.getElementById("scan-meta");
const gpuBadge = document.getElementById("gpu-badge");
const ffmpegBadge = document.getElementById("ffmpeg-badge");
const jobsBadge = document.getElementById("jobs-badge");
const savingsBadge = document.getElementById("savings-badge");
const recapPanel = document.getElementById("recap-panel");
const recapBody = document.getElementById("recap-body");
const batchMeta = document.getElementById("batch-meta");
const btnDeleteOriginal = document.getElementById("btn-delete-original");
const btnCancelEncode = document.getElementById("btn-cancel-encode");
const finalizeActions = document.getElementById("finalize-actions");
const finalizeHint = document.getElementById("finalize-hint");
const savingsRecapEl = document.getElementById("savings-recap");
const pendingSavingsEl = document.getElementById("pending-savings");
const themeSelect = document.getElementById("theme-select");
const maxNvencSelect = document.getElementById("max-nvenc-select");
const maxRemuxSelect = document.getElementById("max-remux-select");
const bulkBar = document.getElementById("bulk-bar");
const bulkCount = document.getElementById("bulk-count");
const bulkCq = document.getElementById("bulk-cq");
const bulkCqVal = document.getElementById("bulk-cq-val");
const btnBulkDenoiseOn = document.getElementById("btn-bulk-denoise-on");
const btnBulkDenoiseOff = document.getElementById("btn-bulk-denoise-off");
const btnBulkStabOn = document.getElementById("btn-bulk-stab-on");
const btnBulkStabOff = document.getElementById("btn-bulk-stab-off");

let allFiles = [];
let files = [];
let sessionRevision = -1;
let sessionPollTimer = null;
let uiPersistTimer = null;
let applyingSession = false;
let encodeBusy = false;
let ffmpegCaps = { vidstab: false, hqdn3d: true, hevc_nvenc: false };
/** Index dans `files` pour Shift+clic (sélection plage) */
let lastSelectIndex = null;
/** @type {Map<string, {selected:boolean, denoise:boolean, stabilize:boolean, cq:number, mode:string}>} */
const rowState = new Map();
/** Dernière vue jobs pour éviter re-render DOM inutile */
let lastJobsSig = "";

const filterBar = document.getElementById("filter-bar");
const filterText = document.getElementById("filter-text");
const filterExt = document.getElementById("filter-ext");
const filterSizeMin = document.getElementById("filter-size-min");
const filterAction = document.getElementById("filter-action");
const filterSort = document.getElementById("filter-sort");
const filterOrder = document.getElementById("filter-order");
const filterMeta = document.getElementById("filter-meta");

function formatSize(bytes) {
  const n = Math.abs(Number(bytes) || 0);
  if (n < 1024) return `${n} o`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} Ko`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} Mo`;
  return `${(n / 1024 ** 3).toFixed(2)} Go`;
}

function formatSaved(bytes) {
  const n = Number(bytes) || 0;
  const sign = n >= 0 ? "−" : "+";
  return `${sign}${formatSize(Math.abs(n))}`;
}

function updateSavingsBadge(savings) {
  if (!savingsBadge || !savings) return;
  const n = savings.files_replaced || 0;
  const saved = savings.bytes_saved || 0;
  if (!n) {
    savingsBadge.textContent = "Économisé: —";
    savingsBadge.classList.remove("savings");
    return;
  }
  savingsBadge.textContent = `Économisé: ${formatSaved(saved)} · ${n} fichier${n > 1 ? "s" : ""}`;
  savingsBadge.classList.add("savings");
}

function renderRunSavings(run, total) {
  if (!savingsRecapEl) return;
  if (!run || !(run.files_replaced > 0)) {
    savingsRecapEl.classList.add("hidden");
    return;
  }
  const pct =
    run.bytes_original > 0
      ? ((100 * run.bytes_saved) / run.bytes_original).toFixed(0)
      : "0";
  const lines = (run.details || [])
    .slice(0, 40)
    .map((d) => {
      const name = (d.source || "").split(/[/\\]/).pop();
      return `· ${name}: ${formatSize(d.bytes_original)} → ${formatSize(d.bytes_encoded)} (${formatSaved(d.bytes_saved)})`;
    });
  const more =
    (run.details || []).length > 40
      ? `\n· … +${run.details.length - 40} autre(s)`
      : "";
  savingsRecapEl.classList.remove("hidden");
  savingsRecapEl.innerHTML = `
    <div><strong>Récap run</strong> — ${run.files_replaced} original(aux) remplacé(s)</div>
    <div>${formatSize(run.bytes_original)} → ${formatSize(run.bytes_encoded)} · <strong>${formatSaved(run.bytes_saved)}</strong> (${pct}%)</div>
    <div style="margin-top:0.4rem;color:var(--text-dim)">Cumul total: ${formatSaved((total && total.bytes_saved) || 0)} sur ${(total && total.files_replaced) || 0} fichier(s)</div>
    <pre style="margin:0.5rem 0 0;white-space:pre-wrap;font-size:0.8rem;color:var(--text-dim)">${escapeHtml(lines.join("\n") + more)}</pre>
  `;
  updateSavingsBadge(total);
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

function rowPath(row) {
  return row.getAttribute("data-path") || "";
}

function pruneRowState() {
  const keep = new Set(allFiles.map((f) => f.path));
  for (const path of [...rowState.keys()]) {
    if (!keep.has(path)) rowState.delete(path);
  }
}

function countSelected() {
  pruneRowState();
  let n = 0;
  for (const f of allFiles) {
    if (rowState.get(f.path)?.selected) n += 1;
  }
  return n;
}

function updateEncodeButton() {
  const n = countSelected();
  btnEncode.disabled = n === 0;
  if (bulkBar) bulkBar.classList.toggle("hidden", allFiles.length === 0);
  if (bulkCount) bulkCount.textContent = `${n} sélectionné(s)`;
  if (btnBulkStabOn) btnBulkStabOn.disabled = !ffmpegCaps.vidstab;
  if (btnBulkStabOff) btnBulkStabOff.disabled = !ffmpegCaps.vidstab;
}

function selectedPaths() {
  pruneRowState();
  const paths = [];
  for (const f of allFiles) {
    if (rowState.get(f.path)?.selected) paths.push(f.path);
  }
  return paths;
}

function applyToSelected(mutator) {
  rememberVisibleRowState();
  const paths = selectedPaths();
  if (!paths.length) return;
  paths.forEach((path) => {
    const st = rowState.get(path) || {
      selected: true,
      denoise: false,
      stabilize: false,
      cq: 23,
      mode: "encode",
    };
    mutator(st);
    if (st.denoise || st.stabilize) st.mode = "encode";
    rowState.set(path, st);
  });
  applyFiltersAndRender({ skipRemember: true });
  schedulePersistUi();
}

function fileExt(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function getRowOptions(path) {
  const row = fileListEl.querySelector(`[data-path="${CSS.escape(path)}"]`);
  if (row) {
    const denoise = row.querySelector(".opt-denoise").checked;
    const stabilize = row.querySelector(".opt-stabilize").checked;
    let mode = row.dataset.action === "remux" ? "remux" : "encode";
    if (denoise || stabilize) mode = "encode";
    const modeSel = row.querySelector(".opt-mode");
    if (modeSel) mode = modeSel.value;
    if (denoise || stabilize) mode = "encode";
    return {
      denoise,
      stabilize,
      cq: parseInt(row.querySelector(".opt-cq").value, 10),
      mode,
    };
  }
  const st = rowState.get(path);
  if (st) {
    return {
      denoise: st.denoise,
      stabilize: st.stabilize,
      cq: st.cq,
      mode: st.denoise || st.stabilize ? "encode" : st.mode || "encode",
    };
  }
  return { denoise: false, stabilize: false, cq: 23, mode: "encode" };
}

function rememberVisibleRowState() {
  fileListEl.querySelectorAll(".file-row").forEach((row) => {
    const path = rowPath(row);
    if (!path) return;
    const cb = row.querySelector('input[type="checkbox"]');
    if (!cb) return;
    const denoise = row.querySelector(".opt-denoise").checked;
    const stabilize = row.querySelector(".opt-stabilize").checked;
    const modeSel = row.querySelector(".opt-mode");
    let mode = modeSel ? modeSel.value : row.getAttribute("data-action") === "remux" ? "remux" : "encode";
    if (denoise || stabilize) mode = "encode";
    rowState.set(path, {
      selected: !!cb.checked,
      denoise,
      stabilize,
      cq: parseInt(row.querySelector(".opt-cq").value, 10) || 23,
      mode,
    });
  });
  schedulePersistUi();
}

function collectUiFilters() {
  return {
    text: filterText ? filterText.value : "",
    ext: filterExt ? filterExt.value : "",
    sizeMin: filterSizeMin ? filterSizeMin.value : "0",
    action: filterAction ? filterAction.value : "",
    sort: filterSort ? filterSort.value : "name",
    order: filterOrder ? filterOrder.value : "asc",
  };
}

function applyUiFilters(f) {
  if (!f) return;
  if (filterText && f.text != null) filterText.value = f.text;
  if (filterExt && f.ext != null) filterExt.value = f.ext;
  if (filterSizeMin && f.sizeMin != null) filterSizeMin.value = f.sizeMin;
  if (filterAction && f.action != null) filterAction.value = f.action;
  if (filterSort && f.sort != null) filterSort.value = f.sort;
  if (filterOrder && f.order != null) filterOrder.value = f.order;
}

function schedulePersistUi() {
  if (applyingSession) return;
  if (uiPersistTimer) clearTimeout(uiPersistTimer);
  uiPersistTimer = setTimeout(persistUiToServer, 400);
}

async function persistUiToServer() {
  const payload = {
    row_state: Object.fromEntries(rowState.entries()),
    ui_filters: collectUiFilters(),
  };
  try {
    const res = await api("/api/session/ui", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (res.revision != null) sessionRevision = res.revision;
  } catch (_) {}
}

function populateExtFilter(list) {
  const exts = [...new Set(list.map((f) => fileExt(f.name)).filter(Boolean))].sort();
  const prev = filterExt.value;
  filterExt.innerHTML = '<option value="">Toutes</option>';
  exts.forEach((ext) => {
    const opt = document.createElement("option");
    opt.value = ext;
    opt.textContent = ext;
    filterExt.appendChild(opt);
  });
  if (exts.includes(prev)) filterExt.value = prev;
}

function getFilteredSortedFiles() {
  const q = (filterText.value || "").trim().toLowerCase();
  const ext = filterExt.value;
  const minSize = parseInt(filterSizeMin.value, 10) || 0;
  const action = filterAction ? filterAction.value : "";
  const sortKey = filterSort.value || "name";
  const desc = filterOrder.value === "desc";

  let list = allFiles.filter((f) => {
    if (ext && fileExt(f.name) !== ext) return false;
    if (f.size < minSize) return false;
    if (action && (f.action || "encode") !== action) return false;
    if (q) {
      const hay = `${f.name} ${f.path} ${f.video_codec || ""} ${f.action_label || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const cmp = (a, b) => {
    let va;
    let vb;
    switch (sortKey) {
      case "size":
        va = a.size;
        vb = b.size;
        break;
      case "mtime":
        va = a.mtime || 0;
        vb = b.mtime || 0;
        break;
      case "ext":
        va = fileExt(a.name);
        vb = fileExt(b.name);
        break;
      case "path":
        va = a.path.toLowerCase();
        vb = b.path.toLowerCase();
        break;
      case "action":
        va = a.action || "";
        vb = b.action || "";
        break;
      default:
        va = a.name.toLowerCase();
        vb = b.name.toLowerCase();
    }
    if (va < vb) return desc ? 1 : -1;
    if (va > vb) return desc ? -1 : 1;
    return a.path.localeCompare(b.path);
  };
  list = list.slice().sort(cmp);
  return list;
}

function applyFiltersAndRender(opts = {}) {
  if (!opts.skipRemember) rememberVisibleRowState();
  files = getFilteredSortedFiles();
  if (filterMeta) {
    filterMeta.textContent =
      allFiles.length === files.length
        ? `${files.length} fichier(s)`
        : `${files.length} / ${allFiles.length} fichier(s)`;
  }
  renderFiles(files);
}

function renderFiles(list) {
  fileListEl.innerHTML = "";
  const workDir = workDirEl.value.trim();
  list.forEach((f) => {
    const defaultMode = f.action === "remux" ? "remux" : "encode";
    const st = rowState.get(f.path) || {
      selected: true,
      denoise: false,
      stabilize: false,
      cq: 23,
      mode: defaultMode,
    };
    const row = document.createElement("div");
    row.className = "file-row";
    row.setAttribute("data-path", f.path);
    row.setAttribute("data-action", f.action || "encode");

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!st.selected;
    cb.addEventListener("click", (ev) => {
      const idx = files.findIndex((x) => x.path === f.path);
      if (ev.shiftKey && lastSelectIndex != null && idx >= 0) {
        ev.preventDefault();
        const from = Math.min(lastSelectIndex, idx);
        const to = Math.max(lastSelectIndex, idx);
        rememberVisibleRowState();
        for (let i = from; i <= to; i++) {
          const p = files[i].path;
          const cur = rowState.get(p) || {
            selected: false,
            denoise: false,
            stabilize: false,
            cq: 23,
            mode: files[i].action === "remux" ? "remux" : "encode",
          };
          cur.selected = true;
          rowState.set(p, cur);
        }
        applyFiltersAndRender({ skipRemember: true });
        schedulePersistUi();
        updateEncodeButton();
        return;
      }
      lastSelectIndex = idx >= 0 ? idx : lastSelectIndex;
    });
    cb.addEventListener("change", () => {
      const cur = rowState.get(f.path) || {
        selected: false,
        denoise: false,
        stabilize: false,
        cq: 23,
        mode: f.action === "remux" ? "remux" : "encode",
      };
      cur.selected = !!cb.checked;
      rowState.set(f.path, cur);
      row.classList.toggle("selected", cur.selected);
      schedulePersistUi();
      updateEncodeButton();
      const idx = files.findIndex((x) => x.path === f.path);
      if (idx >= 0) lastSelectIndex = idx;
    });

    const img = document.createElement("img");
    img.className = "thumb";
    img.alt = "";
    img.loading = "lazy";
    img.src = `/api/thumbnail?path=${encodeURIComponent(f.path)}&work_dir=${encodeURIComponent(workDir)}`;
    img.onerror = () => {
      img.style.opacity = "0.3";
    };

    const info = document.createElement("div");
    info.className = "file-info";
    const when = f.mtime ? new Date(f.mtime * 1000).toLocaleString() : "";
    const codecBits = [f.video_codec, f.audio_codec].filter(Boolean).join(" + ");
    const badgeCls = f.action === "remux" ? "badge-remux" : "badge-encode";
    info.innerHTML = `<div class="name">${escapeHtml(f.name)} <span class="action-badge ${badgeCls}">${escapeHtml(f.action_label || f.action || "")}</span></div>
      <div class="path">${escapeHtml(f.path)}</div>
      <div class="path">${formatSize(f.size)} · ${escapeHtml(fileExt(f.name) || "?")}${codecBits ? " · " + escapeHtml(codecBits) : ""}${when ? " · " + escapeHtml(when) : ""}${f.pending_tmp ? " · temp .jajtmp présent" : ""}</div>`;

    const opts = document.createElement("div");
    opts.className = "file-opts";
    const isRemux = (st.mode || defaultMode) === "remux";
    opts.innerHTML = `
      <label>Traitement
        <select class="opt-mode">
          <option value="remux" ${isRemux ? "selected" : ""}>Remux MP4 (rapide)</option>
          <option value="encode" ${!isRemux ? "selected" : ""}>Réencoder HEVC</option>
        </select>
      </label>
      <label><input type="checkbox" class="opt-denoise"> Débruiter (hqdn3d)</label>
      <label class="opt-stabilize-label"><input type="checkbox" class="opt-stabilize"> Stabiliser (vidstab)</label>
      <label class="opt-cq-label">CQ <span class="cq-val">${st.cq}</span>
        <input type="range" class="opt-cq" min="18" max="35" value="${st.cq}">
      </label>
      <div class="progress-row hidden">
        <div class="progress-wrap"><div class="progress-bar"></div></div>
        <button type="button" class="btn-cancel-job danger" title="Annuler ce job">✕</button>
      </div>
      <div class="progress-label"></div>
    `;
    const modeSel = opts.querySelector(".opt-mode");
    // Si scan dit encode-only, pas d'option remux
    if (f.action !== "remux") {
      modeSel.innerHTML = '<option value="encode" selected>Réencoder HEVC</option>';
      modeSel.disabled = true;
    }
    opts.querySelector(".opt-denoise").checked = st.denoise;
    opts.querySelector(".opt-stabilize").checked = st.stabilize;
    const range = opts.querySelector(".opt-cq");
    const cqVal = opts.querySelector(".cq-val");
    const cqLabel = opts.querySelector(".opt-cq-label");

    function syncModeUi() {
      const remux = modeSel.value === "remux";
      if (remux) {
        opts.querySelector(".opt-denoise").checked = false;
        opts.querySelector(".opt-stabilize").checked = false;
      }
      opts.querySelector(".opt-denoise").disabled = remux;
      const stab = opts.querySelector(".opt-stabilize");
      if (remux) stab.disabled = true;
      else applyStabilizeAvailability(opts);
      if (cqLabel) cqLabel.style.opacity = remux ? "0.4" : "1";
      if (range) range.disabled = remux;
      rememberVisibleRowState();
    }

    range.addEventListener("input", () => {
      cqVal.textContent = range.value;
      rememberVisibleRowState();
    });
    opts.querySelector(".opt-denoise").addEventListener("change", () => {
      if (opts.querySelector(".opt-denoise").checked || opts.querySelector(".opt-stabilize").checked) {
        modeSel.value = "encode";
      }
      syncModeUi();
    });
    opts.querySelector(".opt-stabilize").addEventListener("change", () => {
      if (opts.querySelector(".opt-denoise").checked || opts.querySelector(".opt-stabilize").checked) {
        modeSel.value = "encode";
      }
      syncModeUi();
    });
    modeSel.addEventListener("change", syncModeUi);
    applyStabilizeAvailability(opts);
    syncModeUi();

    row.append(cb, img, info, opts);
    if (cb.checked) row.classList.add("selected");
    fileListEl.appendChild(row);
  });
  updateEncodeButton();
}

function applyStabilizeAvailability(optsRoot) {
  const stabLabel = optsRoot.querySelector(".opt-stabilize-label");
  const stabInput = optsRoot.querySelector(".opt-stabilize");
  if (!stabLabel || !stabInput) return;
  if (ffmpegCaps.vidstab) {
    stabLabel.classList.remove("disabled");
    stabInput.disabled = false;
    stabLabel.title = "";
  } else {
    stabLabel.classList.add("disabled");
    stabInput.checked = false;
    stabInput.disabled = true;
    stabLabel.title = "vidstab indisponible dans ce build FFmpeg";
  }
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function loadConfig() {
  const cfg = await api("/api/config");
  if (!workDirEl.value) workDirEl.value = cfg.work_dir;
  ffmpegBadge.textContent = cfg.ffmpeg;
  if (cfg.concurrency) applyConcurrencyUi(cfg.concurrency);
}

async function loadFfmpegCaps() {
  try {
    ffmpegCaps = await api("/api/ffmpeg");
    if (!ffmpegCaps.hevc_nvenc) {
      ffmpegBadge.classList.add("err");
      ffmpegBadge.textContent += " · NVENC absent";
    }
  } catch (e) {
    ffmpegCaps = { vidstab: false, hqdn3d: false, hevc_nvenc: false };
  }
}

async function loadGpu() {
  try {
    const g = await api("/api/gpu");
    if (g.available && g.gpus && g.gpus.length) {
      const lim = g.nvenc_session_limit;
      gpuBadge.textContent =
        lim != null
          ? `GPU: ${g.gpus[0].name} · NVENC×${lim}`
          : `GPU: ${g.gpus[0].name}`;
      gpuBadge.classList.add("ok");
    } else {
      gpuBadge.textContent = g.error || "GPU indisponible";
      gpuBadge.classList.add("err");
    }
  } catch (e) {
    gpuBadge.textContent = String(e.message);
    gpuBadge.classList.add("err");
  }
}

function updateJobsBadge(status) {
  if (!jobsBadge || !status) return;
  const re = status.running_encode ?? 0;
  const rr = status.running_remux ?? 0;
  const r = status.running || re + rr;
  const q = status.queued || 0;
  const maxE = status.max_nvenc || 2;
  const maxR = status.max_remux || 2;
  const base = `NVENC ${re}/${maxE} · Remux ${rr}/${maxR}`;
  jobsBadge.textContent = q > 0 ? `${base} (+${q} file)` : base;
  jobsBadge.title = `${r} job(s) actif(s)`;
  jobsBadge.classList.toggle("ok", r > 0);
  jobsBadge.classList.toggle("err", re >= maxE && rr >= maxR && q > 0);
  encodeBusy = r > 0 || q > 0;
  if (btnStopAll) btnStopAll.classList.toggle("hidden", !encodeBusy);
}

function fillConcurrencySelect(selectEl, selected, cap) {
  if (!selectEl) return;
  const c = Math.max(1, Number(cap) || 1);
  const cur = Math.max(1, Math.min(Number(selected) || 1, c));
  selectEl.innerHTML = "";
  for (let i = 1; i <= c; i++) {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = String(i);
    selectEl.appendChild(opt);
  }
  selectEl.value = String(cur);
}

function applyConcurrencyUi(conc) {
  if (!conc) return;
  fillConcurrencySelect(maxNvencSelect, conc.max_nvenc, conc.max_nvenc_cap);
  fillConcurrencySelect(maxRemuxSelect, conc.max_remux, conc.max_remux_cap);
  if (maxNvencSelect) {
    maxNvencSelect.title = `Max jobs NVENC (plafond GPU: ${conc.max_nvenc_cap}${conc.gpu_name ? " — " + conc.gpu_name : ""})`;
  }
  if (maxRemuxSelect) {
    maxRemuxSelect.title = `Max jobs remux (plafond: ${conc.max_remux_cap}, JAJ_MAX_REMUX_CAP)`;
  }
}

async function loadConcurrency() {
  try {
    const conc = await api("/api/concurrency");
    applyConcurrencyUi(conc);
  } catch (e) {
    /* ignore */
  }
}

async function saveConcurrencyFromUi() {
  if (!maxNvencSelect || !maxRemuxSelect) return;
  try {
    const conc = await api("/api/concurrency", {
      method: "PUT",
      body: JSON.stringify({
        max_nvenc: Number(maxNvencSelect.value),
        max_remux: Number(maxRemuxSelect.value),
      }),
    });
    applyConcurrencyUi(conc);
  } catch (e) {
    alert(`Concurrence: ${e.message}`);
  }
}

function applyScanFromSession(scan, rows) {
  if (!scan) {
    allFiles = [];
    filterBar.classList.add("hidden");
    scanMetaEl.textContent = "Choisissez un répertoire et lancez l'analyse.";
    fileListEl.innerHTML = "";
    return;
  }
  if (scan.work_dir) workDirEl.value = scan.work_dir;
  allFiles = scan.files || [];
  rowState.clear();
  const serverRows = rows || {};
  allFiles.forEach((f) => {
    const sr = serverRows[f.path];
    rowState.set(
      f.path,
      sr
        ? {
            selected: !!sr.selected,
            denoise: !!sr.denoise,
            stabilize: !!sr.stabilize,
            cq: parseInt(sr.cq, 10) || 23,
            mode: sr.mode || (f.action === "remux" ? "remux" : "encode"),
          }
        : {
            selected: true,
            denoise: false,
            stabilize: false,
            cq: 23,
            mode: f.action === "remux" ? "remux" : "encode",
          }
    );
  });
  populateExtFilter(allFiles);
  filterBar.classList.toggle("hidden", allFiles.length === 0);
  const remuxN = scan.remux_count ?? allFiles.filter((f) => f.action === "remux").length;
  const encN = scan.encode_count ?? allFiles.filter((f) => f.action === "encode").length;
  scanMetaEl.textContent = `${scan.count} fichier(s) dans ${scan.work_dir} — ${encN} à encoder, ${remuxN} remux MP4`;
  applyFiltersAndRender();
}

async function pollSession({ bootstrap = false } = {}) {
  try {
    const data = await api("/api/session");
    const remoteRev = data.revision ?? 0;
    if (bootstrap) {
      applyingSession = true;
      try {
        if (data.work_dir) workDirEl.value = data.work_dir;
        if (data.ui_filters) applyUiFilters(data.ui_filters);
        applyScanFromSession(data.scan, data.row_state);
        sessionRevision = remoteRev;
      } finally {
        applyingSession = false;
      }
    } else if (remoteRev !== sessionRevision) {
      // Autre fenêtre a scanné / changé la sélection
      applyingSession = true;
      try {
        if (data.work_dir) workDirEl.value = data.work_dir;
        if (data.ui_filters) applyUiFilters(data.ui_filters);
        applyScanFromSession(data.scan, data.row_state);
        sessionRevision = remoteRev;
      } finally {
        applyingSession = false;
      }
    }
    const jobs = data.jobs || [];
    const sig = JSON.stringify(
      jobs.map((j) => [j.id, j.state, j.progress_pct, j.phase, j.message, j.error])
    );
    if (sig !== lastJobsSig || bootstrap) {
      lastJobsSig = sig;
      if (jobs.length) {
        renderRecap({
          batch_id: "all",
          running: !!data.jobs_running,
          jobs,
          jobs_status: data.jobs_status,
        });
      } else if (bootstrap && recapBody) {
        recapBody.innerHTML = "";
        if (batchMeta) {
          batchMeta.textContent =
            "Aucun job en cours. Lancez un encodage pour suivre la progression ici.";
        }
      }
    }
    if (data.jobs_status) updateJobsBadge(data.jobs_status);
    if (data.savings) updateSavingsBadge(data.savings);
  } catch (e) {
    if (batchMeta) batchMeta.textContent = `Erreur sync: ${e.message}`;
  }
}

btnScan.addEventListener("click", async () => {
  scanMetaEl.textContent = "Analyse en cours…";
  try {
    const data = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify({ work_dir: workDirEl.value.trim() }),
    });
    applyingSession = true;
    allFiles = data.files || [];
    rowState.clear();
    const serverRows = data.row_state || {};
    allFiles.forEach((f) => {
      const sr = serverRows[f.path];
      rowState.set(
        f.path,
        sr || {
          selected: true,
          denoise: false,
          stabilize: false,
          cq: 23,
          mode: f.action === "remux" ? "remux" : "encode",
        }
      );
    });
    if (data.revision != null) sessionRevision = data.revision;
    populateExtFilter(allFiles);
    filterBar.classList.toggle("hidden", allFiles.length === 0);
    const remuxN = data.remux_count ?? allFiles.filter((f) => f.action === "remux").length;
    const encN = data.encode_count ?? allFiles.filter((f) => f.action === "encode").length;
    scanMetaEl.textContent = `${data.count} fichier(s) dans ${data.work_dir} — ${encN} à encoder, ${remuxN} remux MP4`;
    applyingSession = false;
    applyFiltersAndRender();
    await pollSession();
  } catch (e) {
    applyingSession = false;
    scanMetaEl.textContent = `Erreur: ${e.message}`;
    filterBar.classList.add("hidden");
  }
});

[filterText, filterExt, filterSizeMin, filterAction, filterSort, filterOrder].forEach((el) => {
  if (!el) return;
  el.addEventListener("input", applyFiltersAndRender);
  el.addEventListener("change", applyFiltersAndRender);
});

btnSelectAll.addEventListener("click", () => {
  rememberVisibleRowState();
  const targets = files.length ? files : allFiles;
  targets.forEach((f) => {
    const cur = rowState.get(f.path) || {
      selected: false,
      denoise: false,
      stabilize: false,
      cq: 23,
      mode: f.action === "remux" ? "remux" : "encode",
    };
    cur.selected = true;
    rowState.set(f.path, cur);
  });
  applyFiltersAndRender({ skipRemember: true });
  schedulePersistUi();
  updateEncodeButton();
});

btnSelectNone.addEventListener("click", () => {
  rememberVisibleRowState();
  const targets = files.length ? files : allFiles;
  targets.forEach((f) => {
    const cur = rowState.get(f.path);
    if (cur) {
      cur.selected = false;
      rowState.set(f.path, cur);
    }
  });
  pruneRowState();
  applyFiltersAndRender({ skipRemember: true });
  schedulePersistUi();
  updateEncodeButton();
});

function setRowProgress(path, pct, label, jobId, canCancel) {
  const row = fileListEl.querySelector(`[data-path="${CSS.escape(path)}"]`);
  if (!row) return;
  const progRow = row.querySelector(".progress-row");
  const bar = row.querySelector(".progress-bar");
  const lab = row.querySelector(".progress-label");
  const btn = row.querySelector(".btn-cancel-job");
  if (progRow) progRow.classList.remove("hidden");
  if (bar) bar.style.width = `${Math.min(100, pct)}%`;
  if (lab) lab.textContent = label || "";
  if (btn) {
    btn.classList.toggle("hidden", !canCancel);
    btn.dataset.jobId = jobId || "";
    if (!btn.dataset.bound) {
      btn.dataset.bound = "1";
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const id = btn.dataset.jobId;
        if (!id) return;
        if (!confirm("Annuler ce job et passer au suivant ?")) return;
        try {
          await api(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" });
          btn.disabled = true;
          if (lab) lab.textContent = "Annulation…";
          lastJobsSig = "";
          await pollSession();
        } catch (e) {
          alert(`Annulation: ${e.message}`);
        }
      });
    }
  }
}

function renderRecap(batch) {
  const active = (batch.jobs || []).filter((j) => j.state === "running" || j.state === "queued").length;
  const doneN = (batch.jobs || []).filter((j) => j.state === "done").length;
  batchMeta.textContent = batch.running
    ? `${active} actif(s) — file partagée (max 2 simultanés)`
    : `${doneN} terminé(s) — file partagée`;
  recapBody.innerHTML = "";
  let doneOk = 0;
  let logText = "";
  batch.jobs.forEach((j) => {
    const tr = document.createElement("tr");
    const name = j.source.split(/[/\\]/).pop();
    const stateCls =
      j.state === "done" ? "state-ok" : j.state === "error" || j.state === "cancelled" ? "state-err" : "";
    const phase = j.phase || j.message || j.state;
    const bits = [`${j.progress_pct}%`];
    if (j.out_time) bits.push(j.out_time);
    if (j.fps) bits.push(`${j.fps} fps`);
    if (j.speed) bits.push(`${j.speed}x`);
    if (j.frame) bits.push(`f${j.frame}`);
    const canCancel = j.state === "running" || j.state === "queued";
    tr.innerHTML = `
      <td>${escapeHtml(name)}</td>
      <td class="${stateCls}">${escapeHtml(phase)}${j.error ? "<br>" + escapeHtml(j.error.slice(0, 120)) : ""}</td>
      <td>
        ${escapeHtml(bits.join(" · "))}
        ${canCancel ? `<button type="button" class="btn-cancel-job danger" data-job-id="${escapeHtml(j.id)}">Annuler</button>` : ""}
      </td>
    `;
    recapBody.appendChild(tr);
    const cancelBtn = tr.querySelector(".btn-cancel-job");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", async () => {
        if (!confirm("Annuler ce job et passer au suivant ?")) return;
        try {
          await api(`/api/jobs/${encodeURIComponent(j.id)}/cancel`, { method: "POST", body: "{}" });
          await pollSession();
        } catch (e) {
          alert(`Annulation: ${e.message}`);
        }
      });
    }
    setRowProgress(j.source, j.progress_pct, `${j.progress_pct}% — ${phase}`, j.id, canCancel);
    if (j.state === "done") doneOk += 1;
    if (j.log && j.log.length) {
      logText += `=== ${name} ===\n` + j.log.join("\n") + "\n\n";
    }
  });
  const logEl = document.getElementById("job-log");
  if (logEl) {
    const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
    logEl.textContent = logText.trim() || "Pas encore de log…";
    if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  }
  const pending = batch.jobs.some((j) => j.state === "queued" || j.state === "running");
  const errN = batch.jobs.filter((j) => j.state === "error" || j.state === "cancelled").length;
  const showFinalize = !pending && doneOk > 0;
  if (finalizeActions) finalizeActions.classList.toggle("hidden", !showFinalize);
  if (finalizeHint) {
    finalizeHint.classList.toggle("hidden", !showFinalize);
    if (showFinalize) {
      finalizeHint.textContent =
        errN > 0
          ? `${doneOk} réussi(s) seront concernés — ${errN} échec(s) ignorés (originaux inchangés).`
          : `${doneOk} fichier(s) prêt(s) à finaliser.`;
    }
  }
  if (pendingSavingsEl) {
    if (showFinalize) refreshPendingSavings();
    else pendingSavingsEl.classList.add("hidden");
  }
  if (batch.jobs_status) updateJobsBadge(batch.jobs_status);
}

async function refreshPendingSavings() {
  if (!pendingSavingsEl) return;
  try {
    const p = await api("/api/replace/preview");
    if (!p || !(p.files > 0)) {
      pendingSavingsEl.classList.add("hidden");
      return;
    }
    const pct =
      p.bytes_original > 0
        ? ((100 * p.bytes_saved) / p.bytes_original).toFixed(0)
        : "0";
    pendingSavingsEl.classList.remove("hidden");
    pendingSavingsEl.innerHTML = `
      <div><strong>Si vous remplacez les originaux</strong> — ${p.files} fichier(s)</div>
      <div>${formatSize(p.bytes_original)} → ${formatSize(p.bytes_encoded)} ·
        <strong>économie ${formatSaved(p.bytes_saved)}</strong> (${pct}%)</div>
    `;
  } catch (_) {
    pendingSavingsEl.classList.add("hidden");
  }
}

function applyTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t);
  try {
    localStorage.setItem("jaj-theme", t);
  } catch (_) {}
  if (themeSelect) themeSelect.value = t;
}

function initTheme() {
  let saved = "dark";
  try {
    saved = localStorage.getItem("jaj-theme") || "dark";
  } catch (_) {}
  applyTheme(saved);
  if (themeSelect) {
    themeSelect.addEventListener("change", () => applyTheme(themeSelect.value));
  }
}

btnEncode.addEventListener("click", async () => {
  rememberVisibleRowState();
  const selected = [];
  const options = {};
  // Inclut aussi les lignes filtrées hors vue via rowState
  rowState.forEach((st, path) => {
    if (!st.selected) return;
    selected.push(path);
    options[path] = {
      denoise: !!st.denoise,
      stabilize: !!st.stabilize,
      cq: st.cq || 23,
      mode: st.denoise || st.stabilize ? "encode" : st.mode || "encode",
    };
  });
  // Sync options depuis les lignes visibles (plus à jour)
  fileListEl.querySelectorAll(".file-row").forEach((row) => {
    const cb = row.querySelector('input[type="checkbox"]');
    if (!cb.checked) return;
    const path = row.dataset.path;
    if (!selected.includes(path)) selected.push(path);
    options[path] = getRowOptions(path);
  });
  if (!selected.length) return;
  btnEncode.disabled = true;
  try {
    await persistUiToServer();
    await api("/api/encode", {
      method: "POST",
      body: JSON.stringify({ files: selected, options }),
    });
    lastJobsSig = "";
    await pollSession();
  } catch (e) {
    alert(`Encodage: ${e.message}`);
  } finally {
    updateEncodeButton();
  }
});

if (bulkCq) {
  bulkCq.addEventListener("input", () => {
    if (bulkCqVal) bulkCqVal.textContent = bulkCq.value;
  });
  bulkCq.addEventListener("change", () => {
    const cq = parseInt(bulkCq.value, 10) || 23;
    if (bulkCqVal) bulkCqVal.textContent = String(cq);
    applyToSelected((st) => {
      st.cq = cq;
      if (st.mode === "remux") st.mode = "encode";
    });
  });
}
if (btnBulkDenoiseOn) {
  btnBulkDenoiseOn.addEventListener("click", () => {
    applyToSelected((st) => {
      st.denoise = true;
      st.mode = "encode";
    });
  });
}
if (btnBulkDenoiseOff) {
  btnBulkDenoiseOff.addEventListener("click", () => {
    applyToSelected((st) => {
      st.denoise = false;
    });
  });
}
if (btnBulkStabOn) {
  btnBulkStabOn.addEventListener("click", () => {
    if (!ffmpegCaps.vidstab) {
      alert("vidstab indisponible dans ce build FFmpeg");
      return;
    }
    applyToSelected((st) => {
      st.stabilize = true;
      st.mode = "encode";
    });
  });
}
if (btnBulkStabOff) {
  btnBulkStabOff.addEventListener("click", () => {
    applyToSelected((st) => {
      st.stabilize = false;
    });
  });
}

async function finalizeBatch(mode) {
  const msg =
    mode === "cancel"
      ? "Supprimer les .mp4 réussis et garder tous les originaux ?"
      : "Supprimer uniquement les originaux des encodages réussis ? (les échecs ne sont pas touchés)";
  if (!confirm(msg)) return;
  try {
    const res = await api("/api/replace", {
      method: "POST",
      body: JSON.stringify({ batch_id: "all", mode }),
    });
    if (mode === "delete_original" || mode === "delete_originals") {
      renderRunSavings(res.run_savings, res.savings);
      const run = res.run_savings || {};
      const n = run.files_replaced || 0;
      const summary =
        n > 0
          ? `Run: ${n} remplacé(s) · ${formatSize(run.bytes_original || 0)} → ${formatSize(run.bytes_encoded || 0)} · ${formatSaved(run.bytes_saved || 0)}\nCumul: ${formatSaved((res.savings && res.savings.bytes_saved) || 0)} · ${(res.savings && res.savings.files_replaced) || 0} fichier(s)`
          : "Aucun original remplacé (déjà absents ou échecs).";
      alert(
        summary +
          "\n\n" +
          (res.results || [])
            .map((r) => `${r.source.split(/[/\\]/).pop()}: ${r.ok === "true" ? r.message || "OK" : r.message}`)
            .join("\n")
      );
    } else {
      if (savingsRecapEl) savingsRecapEl.classList.add("hidden");
      alert(
        (res.results || [])
          .map((r) => `${r.source.split(/[/\\]/).pop()}: ${r.ok === "true" ? r.message || "OK" : r.message}`)
          .join("\n")
      );
    }
    if (res.savings) updateSavingsBadge(res.savings);
    lastJobsSig = "";
    btnScan.click();
  } catch (e) {
    alert(`Finalisation: ${e.message}`);
  }
}

btnDeleteOriginal.addEventListener("click", () => finalizeBatch("delete_original"));
btnCancelEncode.addEventListener("click", () => finalizeBatch("cancel"));

btnStopAll.addEventListener("click", async () => {
  if (!confirm("Stopper tous les jobs en cours et en file d'attente ?")) return;
  try {
    const res = await api("/api/jobs/stop-all", { method: "POST", body: "{}" });
    updateJobsBadge(res.jobs_status);
    lastJobsSig = "";
    await pollSession();
    alert(
      `Stop: ${res.cancel_running || 0} en cours, ${res.cancelled_queued || 0} en file annulé(s).`
    );
  } catch (e) {
    alert(`Stop: ${e.message}`);
  }
});

// Sync sélection / filtres vers le serveur (multi-fenêtres)
[filterText, filterExt, filterSizeMin, filterAction, filterSort, filterOrder].forEach((el) => {
  if (!el) return;
  el.addEventListener("change", schedulePersistUi);
});

initTheme();
if (maxNvencSelect) maxNvencSelect.addEventListener("change", saveConcurrencyFromUi);
if (maxRemuxSelect) maxRemuxSelect.addEventListener("change", saveConcurrencyFromUi);
loadConfig()
  .then(loadConcurrency)
  .then(loadFfmpegCaps)
  .then(() => pollSession({ bootstrap: true }));
loadGpu();
sessionPollTimer = setInterval(() => pollSession(), 1500);
