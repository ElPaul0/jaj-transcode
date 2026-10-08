"""Détection / suppression de doublons exacts (même contenu) sous le work_dir."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from app.config import get_settings

# Conteneurs / extensions considérés pour le dédoublonnage (scan + .mp4 sorties)
DEDUP_EXTRA_EXT = frozenset({".mp4", ".mkv", ".webm", ".m4v"})


def _dedup_extensions() -> set[str]:
    return get_settings().extension_set() | set(DEDUP_EXTRA_EXT)


def _iter_media_files(root: Path) -> list[dict[str, Any]]:
    exts = _dedup_extensions()
    out: list[dict[str, Any]] = []
    for dirpath, _dirnames, filenames in os.walk(root):
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


def file_digest(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Blake2b 128-bit — assez pour dédup local, plus rapide que SHA-256."""
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


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


def find_duplicate_groups(work_dir: str | None = None) -> dict[str, Any]:
    """
    Groupe les fichiers de même taille puis même hash.
    Retourne des groupes avec un fichier `keep` et des `delete` candidats.
    """
    settings = get_settings()
    root = Path(work_dir or settings.work_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Répertoire introuvable: {root}")

    files = _iter_media_files(root)
    by_size: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for f in files:
        by_size[int(f["size"])].append(f)

    # Uniquement les tailles en collision
    candidates: list[dict[str, Any]] = []
    for size, group in by_size.items():
        if len(group) < 2:
            continue
        candidates.extend(group)

    digests: dict[str, str] = {}
    if candidates:
        workers = min(8, max(2, (os.cpu_count() or 4)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(file_digest, Path(c["path"])): c["path"] for c in candidates
            }
            for fut in as_completed(futs):
                path = futs[fut]
                try:
                    digests[path] = fut.result()
                except OSError:
                    continue

    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        dig = digests.get(c["path"])
        if not dig:
            continue
        row = dict(c)
        row["hash"] = dig
        by_hash[dig].append(row)

    groups: list[dict[str, Any]] = []
    bytes_reclaimable = 0
    delete_count = 0
    for dig, members in by_hash.items():
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
                "hash": dig,
                "size": int(keep["size"]),
                "count": len(members),
                "keep": keep,
                "delete": to_delete,
                "bytes_reclaimable": reclaim,
            }
        )

    groups.sort(key=lambda g: (-int(g["bytes_reclaimable"]), -int(g["count"])))
    return {
        "work_dir": str(root),
        "files_scanned": len(files),
        "groups": groups,
        "group_count": len(groups),
        "delete_count": delete_count,
        "bytes_reclaimable": bytes_reclaimable,
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
            rec["message"] = "Fichier introuvable (déjà supprimé ?)"
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
