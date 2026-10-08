from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import get_settings, set_work_dir
from app.encoder import (
    FileEncodeOptions,
    batch_to_dict,
    bootstrap_jobs,
    cancel_encodes,
    cancel_job,
    clear_finished_jobs,
    delete_originals,
    ensure_worker,
    generate_thumbnail,
    get_batch,
    get_concurrency,
    global_batch_view,
    jobs_status,
    list_completed_for_replace,
    preview_replace_savings,
    probe_ffmpeg,
    probe_gpu,
    set_concurrency,
    start_batch,
    stop_all_jobs,
)
from app.dedup import delete_duplicates, find_duplicate_groups
from app.scanner import entry_to_dict, scan_videos
from app import session as app_session

APP_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_ROOT / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    app_session.load_session()
    bootstrap_jobs()
    await ensure_worker()
    yield


app = FastAPI(title="jaj-transcode", version="1.1.0", lifespan=lifespan)


class ScanRequest(BaseModel):
    work_dir: str | None = None


class FileOptions(BaseModel):
    denoise: bool = False
    stabilize: bool = False
    cq: int = Field(default=23, ge=18, le=35)
    # auto | encode | remux
    mode: str = "auto"


class EncodeRequest(BaseModel):
    files: list[str]
    options: dict[str, FileOptions] = Field(default_factory=dict)
    default_options: FileOptions = Field(default_factory=FileOptions)


class ReplaceRequest(BaseModel):
    batch_id: str | None = None
    items: list[dict[str, str]] | None = None
    # "delete_original" = garder le .mp4, supprimer la source
    # "cancel" = supprimer le .mp4 encodé, garder l'original
    mode: str = "delete_original"


class RowStateRequest(BaseModel):
    row_state: dict[str, dict] = Field(default_factory=dict)
    ui_filters: dict | None = None


class ConcurrencyRequest(BaseModel):
    max_nvenc: int | None = Field(default=None, ge=1, le=64)
    max_remux: int | None = Field(default=None, ge=1, le=64)


class DedupScanRequest(BaseModel):
    work_dir: str | None = None


class DedupDeleteRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    work_dir: str | None = None


def _resolve_allowed(path_str: str, work_dir: str | None = None) -> Path:
    settings = get_settings()
    base = Path(work_dir or settings.work_dir).resolve()
    target = Path(path_str).resolve()
    try:
        target.relative_to(base)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Chemin hors du répertoire de travail") from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    return target


@app.get("/api/health")
def health():
    settings = get_settings()
    return {
        "ok": True,
        "work_dir": settings.work_dir,
        "ffmpeg": settings.ffmpeg,
        "extensions": sorted(settings.extension_set()),
    }


@app.get("/api/gpu")
def gpu():
    return probe_gpu()


@app.get("/api/ffmpeg")
def ffmpeg_caps():
    return probe_ffmpeg()


@app.get("/api/config")
def config():
    settings = get_settings()
    return {
        "work_dir": settings.work_dir,
        "ffmpeg": settings.ffmpeg,
        "ld_library_path": settings.ld_library_path,
        "extensions": ",".join(sorted(settings.extension_set())),
        "port": settings.port,
        "concurrency": get_concurrency(),
    }


@app.get("/api/concurrency")
def api_get_concurrency():
    return get_concurrency()


@app.put("/api/concurrency")
def api_put_concurrency(body: ConcurrencyRequest):
    return set_concurrency(max_nvenc=body.max_nvenc, max_remux=body.max_remux)


@app.get("/api/session")
def api_session():
    """État unique partagé : scan, sélection, jobs (toutes fenêtres / après reload)."""
    base = app_session.session_public()
    jobs_view = global_batch_view()
    return {
        **base,
        "jobs": jobs_view.get("jobs") or [],
        "jobs_running": jobs_view.get("running", False),
        "jobs_status": jobs_view.get("jobs_status") or jobs_status(),
        "batch_ids": jobs_view.get("batch_ids") or [],
    }


@app.post("/api/session/ui")
def api_session_ui(body: RowStateRequest):
    app_session.set_row_state(body.row_state)
    if body.ui_filters is not None:
        app_session.set_ui_filters(body.ui_filters)
    return {"ok": True, "revision": app_session.revision()}


@app.post("/api/scan")
def scan(body: ScanRequest):
    settings = get_settings()
    root = body.work_dir or settings.work_dir
    set_work_dir(root)
    try:
        entries = scan_videos(root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    remux_n = sum(1 for e in entries if e.action == "remux")
    encode_n = sum(1 for e in entries if e.action == "encode")
    files = [entry_to_dict(e) for e in entries]
    payload = {
        "work_dir": root,
        "count": len(entries),
        "remux_count": remux_n,
        "encode_count": encode_n,
        "files": files,
    }
    # Prépare row_state par défaut pour les nouveaux fichiers
    rows = app_session.get_row_state()
    for f in files:
        path = f["path"]
        if path not in rows:
            rows[path] = {
                "selected": True,
                "denoise": False,
                "stabilize": False,
                "cq": 23,
                "mode": "auto",
            }
    # Retire les chemins qui ne sont plus dans le scan
    keep = {f["path"] for f in files}
    rows = {k: v for k, v in rows.items() if k in keep}
    app_session.set_row_state(rows)
    app_session.set_last_scan(payload)
    return {**payload, "revision": app_session.revision(), "row_state": rows}


@app.get("/api/thumbnail")
def thumbnail(
    path: str = Query(..., description="Chemin absolu du fichier vidéo"),
    work_dir: str | None = Query(None),
):
    resolved = _resolve_allowed(path, work_dir)
    data = generate_thumbnail(str(resolved))
    if not data:
        raise HTTPException(status_code=422, detail="Impossible de générer l'aperçu")
    return Response(content=data, media_type="image/jpeg")


@app.get("/api/jobs")
def jobs():
    return jobs_status()


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str):
    result = cancel_job(job_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Annulation impossible"))
    return {**result, "jobs_status": jobs_status()}


@app.post("/api/jobs/stop-all")
def api_stop_all():
    return stop_all_jobs()


@app.post("/api/jobs/clear-finished")
def api_clear_finished():
    return clear_finished_jobs()


@app.post("/api/encode")
async def encode(body: EncodeRequest):
    if not body.files:
        raise HTTPException(status_code=400, detail="Aucun fichier sélectionné")
    settings = get_settings()
    validated: list[str] = []
    opt_map: dict[str, FileEncodeOptions] = {}
    for f in body.files:
        p = _resolve_allowed(f, settings.work_dir)
        validated.append(str(p))
        fo = body.options.get(f) or body.options.get(str(p)) or body.default_options
        mode = (fo.mode or "auto").lower()
        if fo.denoise or fo.stabilize:
            mode = "encode"
        opt_map[str(p)] = FileEncodeOptions(
            denoise=fo.denoise,
            stabilize=fo.stabilize,
            cq=fo.cq,
            mode=mode,
        )
    batch_id = await start_batch(validated, opt_map)
    return {
        "batch_id": batch_id,
        "jobs_status": jobs_status(),
        "session": {
            "jobs": global_batch_view().get("jobs") or [],
            "jobs_running": True,
        },
    }


@app.get("/api/batch/{batch_id}")
def batch_status(batch_id: str):
    if batch_id == "all":
        return global_batch_view()
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Lot inconnu")
    return batch_to_dict(batch)


def _resolve_under_work(path_str: str, work_dir: str) -> Path:
    base = Path(work_dir).resolve()
    target = Path(path_str).resolve()
    try:
        target.relative_to(base)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Chemin hors du répertoire de travail") from e
    return target


@app.post("/api/dedup/scan")
def dedup_scan(body: DedupScanRequest):
    """Détecte les doublons exacts (même taille + même hash) sous work_dir."""
    settings = get_settings()
    root = body.work_dir or settings.work_dir
    if body.work_dir:
        set_work_dir(body.work_dir)
    try:
        return find_duplicate_groups(root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/dedup/delete")
def dedup_delete(body: DedupDeleteRequest):
    """Supprime les doublons choisis et ajoute l'espace au compteur d'économies."""
    if not body.paths:
        raise HTTPException(status_code=400, detail="Aucun chemin à supprimer")
    settings = get_settings()
    root = body.work_dir or settings.work_dir
    # Sécurité : chaque chemin doit être sous work_dir
    for p in body.paths:
        _resolve_allowed(p, root)
    results = delete_duplicates(body.paths, root)
    savings_update = app_session.record_dedup(results)
    return {
        "results": results,
        "deleted": sum(1 for r in results if r.get("deleted")),
        "bytes_freed": sum(int(r.get("bytes") or 0) for r in results if r.get("deleted")),
        "run_savings": savings_update.get("run"),
        "savings": savings_update.get("total") or app_session.get_savings(),
    }


@app.get("/api/replace/preview")
def replace_preview(batch_id: str | None = None):
    """Économie potentielle si on remplace les originaux (lecture seule)."""
    return preview_replace_savings(batch_id)


@app.post("/api/replace")
def replace(body: ReplaceRequest):
    items = body.items
    if not items:
        items = list_completed_for_replace(body.batch_id)
    if not items:
        raise HTTPException(status_code=400, detail="Rien à finaliser")
    mode = (body.mode or "delete_original").lower()
    # aliases
    if mode in ("replace", "delete_originals", "keep_encoded"):
        mode = "delete_original"
    if mode in ("alongside", "cancel_encoded", "undo"):
        mode = "cancel"
    if mode not in ("delete_original", "cancel"):
        raise HTTPException(
            status_code=400,
            detail="mode doit être delete_original ou cancel",
        )
    settings = get_settings()
    base = settings.work_dir
    for it in items:
        src = it.get("source", "")
        if src:
            # original may already be gone if re-clicked; only check when deleting encode
            try:
                _resolve_allowed(src, base)
            except HTTPException:
                if mode == "delete_original":
                    raise
        final = it.get("output_final", "")
        if final:
            _resolve_under_work(final, base)
        tmp = it.get("output_tmp", "")
        if tmp:
            _resolve_under_work(tmp, base)
    if mode == "cancel":
        results = cancel_encodes(items)
        clear_finished_jobs()
        return {
            "mode": mode,
            "results": results,
            "savings": app_session.get_savings(),
        }

    results = delete_originals(items)
    savings_update = app_session.record_replacements(results)
    clear_finished_jobs()
    return {
        "mode": mode,
        "results": results,
        "run_savings": savings_update.get("run"),
        "savings": savings_update.get("total") or app_session.get_savings(),
    }


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=500, detail="Interface statique manquante")
    return FileResponse(index_path)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def run():
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
