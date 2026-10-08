"""jaj-organize — analyse photos/vidéos et doublons (fichiers + dossiers entiers)."""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings

VIDEO_EXT = frozenset(
    {
        ".mts",
        ".m2ts",
        ".mov",
        ".avi",
        ".mpg",
        ".mpeg",
        ".vob",
        ".mod",
        ".tod",
        ".3gp",
        ".mp4",
        ".mkv",
        ".webm",
        ".m4v",
        ".wmv",
        ".flv",
        ".ts",
    }
)
PHOTO_EXT = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
        ".webp",
        ".raw",
        ".cr2",
        ".cr3",
        ".nef",
        ".arw",
        ".dng",
        ".orf",
        ".rw2",
    }
)
MEDIA_EXT = VIDEO_EXT | PHOTO_EXT

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, Any]], None]


def _media_kind(ext: str) -> str:
    e = ext.lower()
    if e in PHOTO_EXT:
        return "photo"
    if e in VIDEO_EXT:
        return "video"
    return "other"


def _iter_media(
    root: Path,
    log: LogFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    n_dirs = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        if cancel_check and cancel_check():
            raise RuntimeError("Annulé")
        n_dirs += 1
        if log and n_dirs % 40 == 0:
            log(f"Parcours… {len(out)} media ({n_dirs} dossiers)")
        for name in filenames:
            p = Path(dirpath) / name
            ext = p.suffix.lower()
            if ext not in MEDIA_EXT:
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
                    "ext": ext,
                    "kind": _media_kind(ext),
                    "size": int(st.st_size),
                    "mtime": float(st.st_mtime),
                    "dir": str(Path(dirpath).resolve()),
                }
            )
    return out


def _keep_file_rank(item: dict[str, Any]) -> tuple:
    path = Path(item["path"])
    name = path.name.lower()
    ext = path.suffix.lower()
    # Préférer conteneurs « finaux » et chemins plus courts / plus récents
    prefer_ext = 0 if ext in {".mp4", ".jpg", ".jpeg", ".png"} else 1
    copyish = 1 if any(
        tok in name
        for tok in ("copy", "copie", " duplicate", "(1)", "(2)", "(3)", " - copy", "_copy")
    ) else 0
    return (
        prefer_ext,
        copyish,
        -float(item.get("mtime") or 0),
        len(str(path)),
        str(path).lower(),
    )


def _keep_folder_rank(folder: dict[str, Any]) -> tuple:
    name = Path(folder["path"]).name.lower()
    copyish = 1 if any(
        tok in name
        for tok in ("copy", "copie", " duplicate", "(1)", "(2)", " - copy", "_copy", "backup")
    ) else 0
    return (
        copyish,
        -float(folder.get("mtime_max") or 0),
        -int(folder.get("file_count") or 0),
        len(folder["path"]),
        folder["path"].lower(),
    )


def _folder_stats(files: list[dict[str, Any]], root: Path) -> dict[str, dict[str, Any]]:
    """Agrège par dossier : signature contenu relatif + métadonnées."""
    by_dir: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in files:
        # Tous les ancêtres jusqu'à root portent ce fichier
        p = Path(f["path"])
        try:
            rel_root = p.relative_to(root)
        except ValueError:
            continue
        parts = rel_root.parts
        # dossier parent immédiat + tous les parents
        for i in range(len(parts)):
            folder = root.joinpath(*parts[:i]) if i > 0 else root
            # i==0 → root lui-même ; fichiers sous root/a/b.jpg : folders root, root/a
            folder_path = str(folder.resolve())
            # relative path from this folder to the file
            try:
                rel = str(p.relative_to(folder).as_posix())
            except ValueError:
                continue
            by_dir[folder_path].append({**f, "rel": rel})

    folders: dict[str, dict[str, Any]] = {}
    for dpath, members in by_dir.items():
        # Ne pas traiter la racine scannée comme candidat « doublé » seul
        if Path(dpath).resolve() == root.resolve():
            continue
        if len(members) < 2:
            # dossiers avec 1 seul fichier : moins utiles pour « dossier entier »
            # mais utiles si copiés à l'identique ailleurs — on garde si >= 1
            pass
        rel_sig_items = tuple(sorted((m["rel"].lower(), int(m["size"])) for m in members))
        name_sig_items = tuple(sorted((m["name"].lower(), int(m["size"])) for m in members))
        sig_rel = hashlib.blake2b(
            repr(rel_sig_items).encode("utf-8"), digest_size=16
        ).hexdigest()
        sig_names = hashlib.blake2b(
            repr(name_sig_items).encode("utf-8"), digest_size=16
        ).hexdigest()
        mtimes = [float(m["mtime"]) for m in members]
        sizes = [int(m["size"]) for m in members]
        folders[dpath] = {
            "path": dpath,
            "name": Path(dpath).name,
            "file_count": len(members),
            "bytes_total": sum(sizes),
            "mtime_max": max(mtimes) if mtimes else 0.0,
            "mtime_min": min(mtimes) if mtimes else 0.0,
            "sig_rel": sig_rel,
            "sig_names": sig_names,
            "photos": sum(1 for m in members if m["kind"] == "photo"),
            "videos": sum(1 for m in members if m["kind"] == "video"),
        }
    return folders


def analyze_tree(
    work_dir: str | None = None,
    *,
    log: LogFn | None = None,
    on_progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    def _prog(**kwargs: Any) -> None:
        if on_progress:
            on_progress(kwargs)

    settings = get_settings()
    root = Path(work_dir or settings.work_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Répertoire introuvable: {root}")

    _log(f"Analyse organise sous {root}")
    _prog(phase="list", pct=0, message="Inventaire photos + vidéos…")
    t0 = time.time()
    files = _iter_media(root, log=_log, cancel_check=cancel_check)
    photos = sum(1 for f in files if f["kind"] == "photo")
    videos = sum(1 for f in files if f["kind"] == "video")
    _log(f"Inventaire: {len(files)} media ({photos} photos, {videos} vidéos) en {time.time() - t0:.1f}s")
    if cancel_check and cancel_check():
        raise RuntimeError("Annulé")

    # --- Doublons fichiers (nom + taille) ---
    _prog(phase="files", pct=40, message="Doublons fichiers…")
    by_file: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for f in files:
        by_file[(f["name"].lower(), int(f["size"]))].append(f)

    file_groups: list[dict[str, Any]] = []
    file_delete_count = 0
    file_bytes = 0
    for (_name, size), members in by_file.items():
        if len(members) < 2:
            continue
        # Ignorer si tous dans le même dossier (souvent pas « copie ») — non, copies côte à côte existent
        ordered = sorted(members, key=_keep_file_rank)
        keep = ordered[0]
        to_delete = ordered[1:]
        reclaim = sum(int(m["size"]) for m in to_delete)
        file_bytes += reclaim
        file_delete_count += len(to_delete)
        file_groups.append(
            {
                "type": "file",
                "name": keep["name"],
                "size": int(size),
                "kind": keep["kind"],
                "count": len(members),
                "keep": keep,
                "delete": to_delete,
                "bytes_reclaimable": reclaim,
            }
        )
    file_groups.sort(key=lambda g: (-int(g["bytes_reclaimable"]), -int(g["count"])))
    _log(f"Fichiers: {len(file_groups)} groupe(s), {file_delete_count} à supprimer")

    if cancel_check and cancel_check():
        raise RuntimeError("Annulé")

    # --- Doublons dossiers (même arborescence relative + tailles) ---
    _prog(phase="folders", pct=70, message="Doublons dossiers…")
    folders = _folder_stats(files, root)
    by_sig: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fr in folders.values():
        # Au moins 2 fichiers media pour parler de « dossier »
        if int(fr["file_count"]) < 2:
            continue
        by_sig[fr["sig_rel"]].append(fr)

    folder_groups: list[dict[str, Any]] = []
    folder_delete_count = 0
    folder_bytes = 0
    for sig, members in by_sig.items():
        if len(members) < 2:
            continue
        # Écarter les paires parent/enfant (même contenu impossible en pratique si rel paths)
        # Filtrer chemins imbriqués : garder seulement dossiers non-ancêtres les uns des autres
        members = sorted(members, key=lambda m: m["path"])
        filtered: list[dict[str, Any]] = []
        for m in members:
            if any(
                m["path"] != o["path"]
                and (
                    m["path"].startswith(o["path"] + os.sep)
                    or o["path"].startswith(m["path"] + os.sep)
                )
                for o in members
            ):
                # Si A contient B et signatures identiques, c'est suspect — on garde les feuilles
                # Préférer le plus profond
                pass
            filtered.append(m)
        # Préférer les dossiers les plus profonds quand imbriqués
        deep_first = sorted(filtered, key=lambda m: (-m["path"].count(os.sep), m["path"]))
        uniq: list[dict[str, Any]] = []
        for m in deep_first:
            if any(
                m["path"] != u["path"]
                and (
                    m["path"].startswith(u["path"] + os.sep)
                    or u["path"].startswith(m["path"] + os.sep)
                )
                for u in uniq
            ):
                continue
            uniq.append(m)
        if len(uniq) < 2:
            continue
        ordered = sorted(uniq, key=_keep_folder_rank)
        keep = ordered[0]
        to_delete = ordered[1:]
        reclaim = sum(int(d["bytes_total"]) for d in to_delete)
        folder_bytes += reclaim
        folder_delete_count += len(to_delete)
        folder_groups.append(
            {
                "type": "folder",
                "sig": sig,
                "count": len(uniq),
                "file_count": keep["file_count"],
                "bytes_total": keep["bytes_total"],
                "keep": keep,
                "delete": to_delete,
                "bytes_reclaimable": reclaim,
                "reason": "Même contenu relatif (noms + tailles des media)",
            }
        )
    folder_groups.sort(key=lambda g: (-int(g["bytes_reclaimable"]), -int(g["count"])))
    _log(
        f"Dossiers: {len(folder_groups)} groupe(s), {folder_delete_count} dossier(s) à supprimer"
    )

    # Near-miss: même signature noms (sans chemins relatifs) — dossiers renommés / réorganisés
    by_names: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fr in folders.values():
        if int(fr["file_count"]) < 3:
            continue
        by_names[fr["sig_names"]].append(fr)
    similar_folders: list[dict[str, Any]] = []
    seen_pair: set[tuple[str, str]] = set()
    for _sig, members in by_names.items():
        if len(members) < 2:
            continue
        # Exclure ceux déjà en doublon exact rel
        exact_paths = {
            d["path"]
            for g in folder_groups
            for d in [g["keep"], *g["delete"]]
        }
        candidates = [m for m in members if m["path"] not in exact_paths]
        if len(candidates) < 2:
            continue
        ordered = sorted(candidates, key=_keep_folder_rank)
        keep = ordered[0]
        for other in ordered[1:]:
            pair = tuple(sorted((keep["path"], other["path"])))
            if pair in seen_pair:
                continue
            seen_pair.add(pair)
            similar_folders.append(
                {
                    "type": "folder_similar",
                    "keep": keep,
                    "other": other,
                    "file_count": keep["file_count"],
                    "bytes_reclaimable": int(other["bytes_total"]),
                    "reason": "Même ensemble nom+taille (arborescence différente) — vérifier avant suppression",
                }
            )
    similar_folders.sort(key=lambda g: -int(g["bytes_reclaimable"]))
    if similar_folders:
        _log(f"Dossiers similaires (à vérifier): {len(similar_folders)}")

    _prog(phase="done", pct=100, message="Analyse terminée")
    _log(
        f"Terminé en {time.time() - t0:.1f}s — "
        f"fichiers {file_bytes / (1024**3):.2f} Go + dossiers {folder_bytes / (1024**3):.2f} Go récupérables"
    )
    return {
        "work_dir": str(root),
        "files_scanned": len(files),
        "photos": photos,
        "videos": videos,
        "file_groups": file_groups,
        "folder_groups": folder_groups,
        "similar_folders": similar_folders[:50],
        "file_group_count": len(file_groups),
        "folder_group_count": len(folder_groups),
        "file_delete_count": file_delete_count,
        "folder_delete_count": folder_delete_count,
        "bytes_reclaimable_files": file_bytes,
        "bytes_reclaimable_folders": folder_bytes,
        "bytes_reclaimable": file_bytes + folder_bytes,
        "method": "name+size + folder signature",
    }


def delete_paths(
    paths: list[str],
    work_dir: str | None = None,
    *,
    recursive_dirs: bool = False,
) -> list[dict[str, Any]]:
    """Supprime fichiers ou dossiers (si recursive_dirs) sous work_dir."""
    import shutil

    settings = get_settings()
    base = Path(work_dir or settings.work_dir).resolve()
    results: list[dict[str, Any]] = []
    for raw in paths:
        rec: dict[str, Any] = {
            "path": raw,
            "ok": False,
            "deleted": False,
            "bytes": 0,
            "kind": "file",
            "message": "",
        }
        try:
            target = Path(raw).resolve()
            target.relative_to(base)
        except (OSError, ValueError):
            rec["message"] = "Chemin hors du répertoire de travail"
            results.append(rec)
            continue
        try:
            if target.is_dir() and recursive_dirs:
                rec["kind"] = "folder"
                # taille approx = somme déjà connue côté UI ; ici on estime via walk
                total = 0
                for dirpath, _dns, fnames in os.walk(target):
                    for fn in fnames:
                        try:
                            total += (Path(dirpath) / fn).stat().st_size
                        except OSError:
                            pass
                shutil.rmtree(target)
                rec["ok"] = True
                rec["deleted"] = True
                rec["bytes"] = int(total)
                rec["message"] = "Dossier supprimé"
            elif target.is_file():
                size = target.stat().st_size
                target.unlink()
                rec["ok"] = True
                rec["deleted"] = True
                rec["bytes"] = int(size)
                rec["message"] = "Fichier supprimé"
            else:
                rec["message"] = "Introuvable"
        except OSError as e:
            rec["message"] = str(e)
        results.append(rec)
    return results


# --- Jobs async -------------------------------------------------------------

_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}


def _snap(job: dict[str, Any]) -> dict[str, Any]:
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


def get_organize_job(job_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        if job_id and job_id in _jobs:
            return _snap(_jobs[job_id])
        if not _jobs:
            return None
        latest = max(_jobs.values(), key=lambda j: float(j.get("started_at") or 0))
        return _snap(latest)


def is_organize_running() -> bool:
    with _lock:
        return any(j.get("state") == "running" for j in _jobs.values())


def cancel_organize_job(job_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id) if job_id else None
        if job is None and _jobs:
            job = max(_jobs.values(), key=lambda j: float(j.get("started_at") or 0))
        if not job:
            return None
        job["cancel"] = True
        if job["state"] == "running":
            job["message"] = "Annulation demandée…"
        return _snap(job)


def start_organize_scan(work_dir: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    root = str(Path(work_dir or settings.work_dir).resolve())
    with _lock:
        for j in _jobs.values():
            if j["state"] == "running":
                return _snap(j)
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
            "log": deque(maxlen=300),
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
            result = analyze_tree(
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
                        f"OK — {result.get('file_group_count', 0)} grp fichiers, "
                        f"{result.get('folder_group_count', 0)} grp dossiers"
                    )
                job["finished_at"] = time.time()
        except Exception as e:
            with _lock:
                job["state"] = "cancelled" if job.get("cancel") else "error"
                job["error"] = str(e)
                job["message"] = str(e)
                job["finished_at"] = time.time()
                job["log"].append(f"{time.strftime('%H:%M:%S')} ERREUR: {e}")

    threading.Thread(target=_run, name=f"organize-{job_id[:8]}", daemon=True).start()
    with _lock:
        return _snap(job)
