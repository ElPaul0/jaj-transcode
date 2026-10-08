"""Détection / suppression de doublons (même nom + même taille) sous le work_dir."""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings

# Conteneurs / extensions considérés pour le dédoublonnage (scan + .mp4 sorties)
DEDUP_EXTRA_EXT = frozenset({".mp4", ".mkv", ".webm", ".m4v"})

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, Any]], None]


def _dedup_extensions() -> set[str]:
    return get_settings().extension_set() | set(DEDUP_EXTRA_EXT)


def _iter_media_files(
    root: Path,
    log: LogFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    exts = _dedup_extensions()
    out: list[dict[str, Any]] = []
    n_dirs = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        if cancel_check and cancel_check():
            raise RuntimeError("Annulé")
        n_dirs += 1
        if log and n_dirs % 50 == 0:
            log(f"Parcours… {len(out)} fichiers media ({n_dirs} dossiers)")
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() not in exts:
                continue
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size <= 0:
                continue
            out.append(
                {
                    "path": str(p.resolve()),
                    "name": p.name,
                    "size": int(st.st_size),
                    "mtime": float(st.st_mtime),
                }
            )
    return out


def _keep_rank(item: dict[str, Any]) -> tuple:
    """Plus petit = à conserver. Préfère .mp4, noms propres, plus récent, chemin court."""
    path = Path(item["path"])
    name = path.name.lower()
    ext = path.suffix.lower()
    prefer_container = 0 if ext == ".mp4" else (1 if ext in {".mov", ".m4v"} else 2)
    copyish = 1 if any(
        tok in name
        for tok in (
            "copy",
            "copie",
            " duplicate",
            "(1)",
            "(2)",
            "(3)",
            " - copy",
            "_copy",
        )
    ) else 0
    return (
        prefer_container,
        copyish,
        -float(item.get("mtime") or 0),
        len(str(path)),
        str(path).lower(),
    )


def find_duplicate_groups(
    work_dir: str | None = None,
    *,
    log: LogFn | None = None,
    on_progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Groupe les fichiers de même nom (insensible à la casse) et même taille.
    Pas de hash — rapide même sur NAS.
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    def _prog(**kwargs: Any) -> None:
        if on_progress:
            on_progress(kwargs)

    if cancel_check and cancel_check():
        raise RuntimeError("Annulé")

    settings = get_settings()
    root = Path(work_dir or settings.work_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Répertoire introuvable: {root}")

    _log(f"Démarrage dédup (nom+taille) sous {root}")
    _prog(phase="list", pct=0, message="Inventaire des fichiers…")
    t0 = time.time()
    files = _iter_media_files(root, log=_log, cancel_check=cancel_check)
    _log(f"Inventaire: {len(files)} fichiers media ({time.time() - t0:.1f}s)")
    if cancel_check and cancel_check():
        raise RuntimeError("Annulé")

    _prog(phase="group", pct=70, message="Regroupement nom + taille…")
    by_key: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for f in files:
        key = (f["name"].lower(), int(f["size"]))
        by_key[key].append(f)

    groups: list[dict[str, Any]] = []
    bytes_reclaimable = 0
    delete_count = 0
    for (name_l, size), members in by_key.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_keep_rank)
        keep = ordered[0]
        to_delete = ordered[1:]
        reclaim = sum(int(m["size"]) for m in to_delete)
        bytes_reclaimable += reclaim
        delete_count += len(to_delete)
        groups.append(
            {
                "key": f"{name_l}|{size}",
                "name": keep["name"],
                "size": int(size),
                "count": len(members),
                "keep": keep,
                "delete": to_delete,
                "bytes_reclaimable": reclaim,
            }
        )

    groups.sort(key=lambda g: (-int(g["bytes_reclaimable"]), -int(g["count"])))
    _log(
        f"Terminé: {len(groups)} groupe(s), {delete_count} doublon(s), "
        f"{bytes_reclaimable / (1024**3):.2f} Go récupérables "
        f"({time.time() - t0:.1f}s total)"
    )
    _prog(phase="done", pct=100, message="Analyse terminée")
    return {
        "work_dir": str(root),
        "files_scanned": len(files),
        "groups": groups,
        "group_count": len(groups),
        "delete_count": delete_count,
        "bytes_reclaimable": bytes_reclaimable,
        "method": "name+size",
    }


def delete_duplicates(
    paths: list[str],
    work_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Supprime les chemins fournis (doivent être sous work_dir)."""
    settings = get_settings()
    base = Path(work_dir or settings.work_dir).resolve()
    results: list[dict[str, Any]] = []
    for raw in paths:
        rec: dict[str, Any] = {
            "path": raw,
            "ok": False,
            "deleted": False,
            "bytes": 0,
            "message": "",
        }
        try:
            target = Path(raw).resolve()
            target.relative_to(base)
        except (OSError, ValueError):
            rec["message"] = "Chemin hors du répertoire de travail"
            results.append(rec)
            continue
        if not target.is_file():
            rec["message"] = "Fichier introuvable (déjà absents ?)"
            results.append(rec)
            continue
        try:
            size = target.stat().st_size
            target.unlink()
            rec["ok"] = True
            rec["deleted"] = True
            rec["bytes"] = int(size)
            rec["message"] = "Supprimé"
        except OSError as e:
            rec["message"] = str(e)
        results.append(rec)
    return results


# --- Job async avec journal -------------------------------------------------

_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "state": job["state"],
        "phase": job.get("phase") or "",
        "pct": job.get("pct") or 0,
        "message": job.get("message") or "",
        "work_dir": job.get("work_dir") or "",
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error") or "",
        "log": list(job.get("log") or []),
        "result": job.get("result"),
    }


def get_dedup_job(job_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        if job_id:
            job = _jobs.get(job_id)
            return _job_snapshot(job) if job else None
        if not _jobs:
            return None
        latest = max(_jobs.values(), key=lambda j: float(j.get("started_at") or 0))
        return _job_snapshot(latest)


def is_dedup_running() -> bool:
    with _lock:
        return any(j.get("state") == "running" for j in _jobs.values())


def cancel_dedup_job(job_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id) if job_id else None
        if job is None and _jobs:
            job = max(_jobs.values(), key=lambda j: float(j.get("started_at") or 0))
        if not job:
            return None
        job["cancel"] = True
        if job["state"] == "running":
            job["message"] = "Annulation demandée…"
        return _job_snapshot(job)


def start_dedup_scan(work_dir: str | None = None) -> dict[str, Any]:
    """Lance l'analyse en arrière-plan ; retourne immédiatement le job."""
    settings = get_settings()
    root = str(Path(work_dir or settings.work_dir).resolve())

    with _lock:
        for j in _jobs.values():
            if j["state"] == "running":
                return _job_snapshot(j)

        job_id = str(uuid.uuid4())
        job: dict[str, Any] = {
            "id": job_id,
            "state": "running",
            "phase": "starting",
            "pct": 0,
            "message": "Démarrage…",
            "work_dir": root,
            "started_at": time.time(),
            "finished_at": None,
            "error": "",
            "cancel": False,
            "log": deque(maxlen=200),
            "result": None,
        }
        _jobs[job_id] = job

    def _append(msg: str) -> None:
        with _lock:
            job["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
            job["message"] = msg

    def _progress(info: dict[str, Any]) -> None:
        with _lock:
            if info.get("phase"):
                job["phase"] = info["phase"]
            if "pct" in info:
                job["pct"] = int(info["pct"])
            if info.get("message"):
                job["message"] = info["message"]

    def _run() -> None:
        try:
            result = find_duplicate_groups(
                root,
                log=_append,
                on_progress=_progress,
                cancel_check=lambda: bool(job.get("cancel")),
            )
            with _lock:
                if job.get("cancel"):
                    job["state"] = "cancelled"
                    job["error"] = "Annulé"
                    job["message"] = "Annulé"
                else:
                    job["state"] = "done"
                    job["result"] = result
                    job["pct"] = 100
                    job["phase"] = "done"
                    job["message"] = (
                        f"OK — {result.get('group_count', 0)} groupe(s), "
                        f"{result.get('delete_count', 0)} doublon(s)"
                    )
                job["finished_at"] = time.time()
        except Exception as e:
            with _lock:
                job["state"] = "cancelled" if job.get("cancel") else "error"
                job["error"] = str(e)
                job["message"] = str(e)
                job["finished_at"] = time.time()
                job["log"].append(f"{time.strftime('%H:%M:%S')} ERREUR: {e}")

    threading.Thread(target=_run, name=f"dedup-{job_id[:8]}", daemon=True).start()
    with _lock:
        return _job_snapshot(job)
