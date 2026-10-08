"""État partagé de la webapp (une seule instance logique, multi-fenêtres)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from app.config import get_settings, set_work_dir

_lock = threading.RLock()
_last_scan: dict[str, Any] | None = None
# path -> {selected, denoise, stabilize, cq, mode}
_row_state: dict[str, dict[str, Any]] = {}
_ui_filters: dict[str, Any] = {}
_revision: int = 0
_dirty = False
_last_save = 0.0
# Économies cumulées (uniquement après remplacement réel des originaux)
_savings: dict[str, Any] = {
    "files_replaced": 0,
    "bytes_original": 0,
    "bytes_encoded": 0,
    "bytes_saved": 0,
    "last_run": None,
}

def _state_dir() -> Path:
    from app.config import get_settings

    return get_settings().resolved_data_dir()


def _session_path() -> Path:
    return _state_dir() / "session.json"


def _bump() -> None:
    global _revision, _dirty
    _revision += 1
    _dirty = True


def revision() -> int:
    return _revision


def get_last_scan() -> dict[str, Any] | None:
    with _lock:
        return dict(_last_scan) if _last_scan else None


def set_last_scan(payload: dict[str, Any]) -> None:
    global _last_scan
    with _lock:
        _last_scan = payload
        _bump()
        _save_unlocked(force=True)


def get_row_state() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _row_state.items()}


def set_row_state(rows: dict[str, dict[str, Any]]) -> None:
    global _row_state
    with _lock:
        _row_state = {str(k): dict(v) for k, v in (rows or {}).items()}
        _bump()
        _save_unlocked(force=False)


def get_ui_filters() -> dict[str, Any]:
    with _lock:
        return dict(_ui_filters)


def set_ui_filters(filters: dict[str, Any]) -> None:
    global _ui_filters
    with _lock:
        _ui_filters = dict(filters or {})
        _bump()
        _save_unlocked(force=False)


def _save_unlocked(force: bool = False) -> None:
    global _dirty, _last_save
    now = time.time()
    if not force and not _dirty:
        return
    if not force and now - _last_save < 1.0:
        return
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    data = {
        "work_dir": settings.work_dir,
        "scan": _last_scan,
        "row_state": _row_state,
        "ui_filters": _ui_filters,
        "savings": _savings,
        "revision": _revision,
        "saved_at": now,
    }
    session_path = _session_path()
    tmp = session_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(session_path)
    _dirty = False
    _last_save = now


def save_session(force: bool = True) -> None:
    with _lock:
        _save_unlocked(force=force)


def load_session() -> None:
    global _last_scan, _row_state, _ui_filters, _revision, _dirty, _savings
    session_path = _session_path()
    if not session_path.is_file():
        return
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    with _lock:
        wd = data.get("work_dir")
        if wd:
            set_work_dir(str(wd))
        scan = data.get("scan")
        _last_scan = scan if isinstance(scan, dict) else None
        rows = data.get("row_state") or {}
        _row_state = {str(k): dict(v) for k, v in rows.items()} if isinstance(rows, dict) else {}
        filt = data.get("ui_filters") or {}
        _ui_filters = dict(filt) if isinstance(filt, dict) else {}
        sav = data.get("savings")
        if isinstance(sav, dict):
            _savings = {
                "files_replaced": int(sav.get("files_replaced") or 0),
                "bytes_original": int(sav.get("bytes_original") or 0),
                "bytes_encoded": int(sav.get("bytes_encoded") or 0),
                "bytes_saved": int(sav.get("bytes_saved") or 0),
                "last_run": sav.get("last_run"),
            }
        _revision = int(data.get("revision") or 0)
        _dirty = False


def get_savings() -> dict[str, Any]:
    with _lock:
        return {
            "files_replaced": int(_savings.get("files_replaced") or 0),
            "bytes_original": int(_savings.get("bytes_original") or 0),
            "bytes_encoded": int(_savings.get("bytes_encoded") or 0),
            "bytes_saved": int(_savings.get("bytes_saved") or 0),
            "last_run": dict(_savings["last_run"]) if isinstance(_savings.get("last_run"), dict) else None,
        }


def record_replacements(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Enregistre les remplacements réussis ; met à jour le cumul et last_run."""
    global _savings
    run_files = 0
    run_orig = 0
    run_enc = 0
    run_saved = 0
    details: list[dict[str, Any]] = []
    for it in items:
        if not it.get("replaced"):
            continue
        orig = int(it.get("bytes_original") or 0)
        enc = int(it.get("bytes_encoded") or 0)
        saved = orig - enc
        run_files += 1
        run_orig += orig
        run_enc += enc
        run_saved += saved
        details.append(
            {
                "source": it.get("source", ""),
                "bytes_original": orig,
                "bytes_encoded": enc,
                "bytes_saved": saved,
            }
        )
    with _lock:
        if run_files:
            _savings["files_replaced"] = int(_savings.get("files_replaced") or 0) + run_files
            _savings["bytes_original"] = int(_savings.get("bytes_original") or 0) + run_orig
            _savings["bytes_encoded"] = int(_savings.get("bytes_encoded") or 0) + run_enc
            _savings["bytes_saved"] = int(_savings.get("bytes_saved") or 0) + run_saved
            _savings["last_run"] = {
                "at": time.time(),
                "kind": "replace",
                "files_replaced": run_files,
                "bytes_original": run_orig,
                "bytes_encoded": run_enc,
                "bytes_saved": run_saved,
                "details": details,
            }
            _bump()
            _save_unlocked(force=True)
        total = {
            "files_replaced": int(_savings.get("files_replaced") or 0),
            "bytes_original": int(_savings.get("bytes_original") or 0),
            "bytes_encoded": int(_savings.get("bytes_encoded") or 0),
            "bytes_saved": int(_savings.get("bytes_saved") or 0),
            "last_run": dict(_savings["last_run"]) if isinstance(_savings.get("last_run"), dict) else None,
        }
        run = total["last_run"] if run_files else {
            "kind": "replace",
            "files_replaced": 0,
            "bytes_original": 0,
            "bytes_encoded": 0,
            "bytes_saved": 0,
            "details": [],
        }
        return {"run": run, "total": total}


def record_dedup(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Enregistre l'espace libéré par suppression de doublons (contenu identique)."""
    global _savings
    run_files = 0
    run_saved = 0
    details: list[dict[str, Any]] = []
    for it in items:
        if not it.get("deleted"):
            continue
        saved = int(it.get("bytes") or 0)
        run_files += 1
        run_saved += saved
        details.append(
            {
                "source": it.get("path", ""),
                "bytes_original": saved,
                "bytes_encoded": 0,
                "bytes_saved": saved,
            }
        )
    with _lock:
        if run_files:
            _savings["files_replaced"] = int(_savings.get("files_replaced") or 0) + run_files
            _savings["bytes_original"] = int(_savings.get("bytes_original") or 0) + run_saved
            # bytes_encoded inchangé (pas de fichier de remplacement)
            _savings["bytes_saved"] = int(_savings.get("bytes_saved") or 0) + run_saved
            _savings["last_run"] = {
                "at": time.time(),
                "kind": "dedup",
                "files_replaced": run_files,
                "bytes_original": run_saved,
                "bytes_encoded": 0,
                "bytes_saved": run_saved,
                "details": details,
            }
            _bump()
            _save_unlocked(force=True)
        total = get_savings()
        run = total.get("last_run") if run_files else {
            "kind": "dedup",
            "files_replaced": 0,
            "bytes_original": 0,
            "bytes_encoded": 0,
            "bytes_saved": 0,
            "details": [],
        }
        return {"run": run, "total": total}


def session_public() -> dict[str, Any]:
    """Vue légère (sans jobs — fournis par encoder)."""
    settings = get_settings()
    with _lock:
        return {
            "revision": _revision,
            "work_dir": settings.work_dir,
            "scan": dict(_last_scan) if _last_scan else None,
            "row_state": {k: dict(v) for k, v in _row_state.items()},
            "ui_filters": dict(_ui_filters),
            "savings": get_savings(),
        }
