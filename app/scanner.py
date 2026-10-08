from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path

from app.config import get_settings

# Codecs déjà “propres” → remux possible (copie de flux)
MODERN_VIDEO = frozenset({"hevc", "h264", "av1", "vp9"})
# Audio compatibles conteneur MP4 en -c copy
MP4_AUDIO_COPY = frozenset(
    {"aac", "mp3", "ac3", "eac3", "alac", "mp2", "opus"}  # opus: ok selon lecteur
)
# Conteneurs où un remux vers .mp4 a du sens
REMUX_FROM_EXT = frozenset({".mov", ".mkv", ".avi", ".m4v", ".3gp"})


@dataclass
class VideoEntry:
    path: str
    name: str
    size: int
    mtime: float
    pending_tmp: bool
    video_codec: str = ""
    audio_codec: str = ""
    width: int = 0
    height: int = 0
    bitrate: int = 0
    duration: float = 0.0
    # encode | remux | unknown
    action: str = "encode"
    action_label: str = "À encoder"
    probe_error: str = ""


def temp_output_path(source: Path) -> Path:
    return source.parent / f"{source.stem}.jajtmp.mp4"


def final_output_path(source: Path) -> Path:
    return source.parent / f"{source.stem}.mp4"


def needs_list(path: Path, extensions: set[str]) -> bool:
    if path.suffix.lower() not in extensions:
        return False
    if not path.is_file():
        return False
    final = final_output_path(path)
    if final.is_file() and final.stat().st_size > 0:
        return False
    return True


def _probe_one(path: Path) -> dict:
    settings = get_settings()
    cmd = [
        settings.resolved_ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration,bit_rate,size:stream=codec_type,codec_name,width,height,bit_rate",
        "-of",
        "json",
        str(path),
    ]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=settings.ffmpeg_env(),
        timeout=60,
    )
    if r.returncode != 0:
        return {"error": (r.stderr or "ffprobe failed")[:200]}
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"error": "ffprobe json invalide"}


def suggest_action(path: str | Path) -> str:
    """encode | remux — classification rapide pour le mode auto."""
    p = Path(path)
    data = _probe_one(p)
    if data.get("error"):
        return "encode"
    vcodec = ""
    acodec = ""
    for stream in data.get("streams") or []:
        ctype = stream.get("codec_type")
        if ctype == "video" and not vcodec:
            vcodec = stream.get("codec_name") or ""
        elif ctype == "audio" and not acodec:
            acodec = stream.get("codec_name") or ""
    action, _label = classify_action(p, vcodec, acodec)
    return action


def classify_action(
    path: Path,
    video_codec: str,
    audio_codec: str,
) -> tuple[str, str]:
    """Retourne (action, label)."""
    ext = path.suffix.lower()
    vc = (video_codec or "").lower()
    ac = (audio_codec or "").lower()

    if not vc:
        return "encode", "À encoder (codec inconnu)"

    # Déjà en conteneur mp4 + codec moderne : pas besoin de le lister pour remux
    # (needs_list exclut déjà s'il existe un .mp4 voisin ; ici source est .mov etc.)

    if vc not in MODERN_VIDEO:
        return "encode", f"À encoder ({vc})"

    # HEVC / H.264 / AV1 / VP9 déjà compressés
    if ac.startswith("pcm"):
        return "encode", f"À encoder ({vc} + audio PCM)"

    if ext in REMUX_FROM_EXT:
        return "remux", f"Remux → MP4 ({vc}, copie streams)"

    # .mts/.m2ts rarement en hevc/h264 “propre”, mais si c'est le cas → remux
    if ext in {".mts", ".m2ts", ".mpg", ".mpeg"} and vc in MODERN_VIDEO:
        if ac in MP4_AUDIO_COPY or not ac:
            return "remux", f"Remux → MP4 ({vc}, copie streams)"

    return "encode", f"À encoder ({vc})"


def enrich_entry(entry: VideoEntry) -> VideoEntry:
    path = Path(entry.path)
    data = _probe_one(path)
    if data.get("error"):
        entry.probe_error = str(data["error"])
        entry.action = "encode"
        entry.action_label = "À encoder (probe échoué)"
        return entry

    fmt = data.get("format") or {}
    try:
        entry.duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        entry.duration = 0.0
    try:
        entry.bitrate = int(fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        entry.bitrate = 0

    vcodec = ""
    acodec = ""
    for stream in data.get("streams") or []:
        ctype = stream.get("codec_type")
        if ctype == "video" and not vcodec:
            vcodec = stream.get("codec_name") or ""
            try:
                entry.width = int(stream.get("width") or 0)
                entry.height = int(stream.get("height") or 0)
            except (TypeError, ValueError):
                pass
        elif ctype == "audio" and not acodec:
            acodec = stream.get("codec_name") or ""

    entry.video_codec = vcodec
    entry.audio_codec = acodec
    action, label = classify_action(path, vcodec, acodec)
    entry.action = action
    entry.action_label = label
    return entry


def scan_videos(root: str | None = None) -> list[VideoEntry]:
    settings = get_settings()
    base = Path(root or settings.work_dir)
    if not base.is_dir():
        raise FileNotFoundError(f"Répertoire introuvable: {base}")

    extensions = settings.extension_set()
    entries: list[VideoEntry] = []

    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            p = Path(dirpath) / name
            if not needs_list(p, extensions):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            tmp = temp_output_path(p)
            entries.append(
                VideoEntry(
                    path=str(p.resolve()),
                    name=p.name,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    pending_tmp=tmp.is_file(),
                )
            )

    # Probe parallèle
    if entries:
        workers = min(8, max(2, (os.cpu_count() or 4)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(enrich_entry, e): e for e in entries}
            done: list[VideoEntry] = []
            for fut in as_completed(futs):
                done.append(fut.result())
            entries = done

    entries.sort(key=lambda e: e.path.lower())
    return entries


def entry_to_dict(e: VideoEntry) -> dict:
    return asdict(e)
