from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.scanner import final_output_path, temp_output_path

def _state_dir() -> Path:
    return get_settings().resolved_data_dir()


def _jobs_path() -> Path:
    return _state_dir() / "jobs.json"
_persist_lock = threading.RLock()
_last_jobs_save = 0.0
_jobs_dirty = False


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class FileEncodeOptions:
    denoise: bool = False
    stabilize: bool = False
    cq: int = 23
    # auto | encode | remux — auto: remux si pas de filtres et source moderne
    mode: str = "auto"


@dataclass
class EncodeJob:
    id: str
    source: str
    options: FileEncodeOptions
    state: JobState = JobState.QUEUED
    progress_pct: float = 0.0
    speed: str = ""
    eta: str = ""
    message: str = ""
    phase: str = ""
    frame: str = ""
    fps: str = ""
    out_time: str = ""
    output_tmp: str = ""
    output_final: str = ""
    error: str = ""
    cancel_requested: bool = False
    log_lines: deque[str] = field(default_factory=lambda: deque(maxlen=80))

    def log(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        self.log_lines.append(line)


class JobCancelled(Exception):
    """Job annulé par l'utilisateur."""


_job_procs: dict[str, subprocess.Popen[str]] = {}


@dataclass
class EncodeBatch:
    batch_id: str
    jobs: list[EncodeJob] = field(default_factory=list)
    current_index: int = 0
    running: bool = False


_batches: dict[str, EncodeBatch] = {}
_batch_lock = asyncio.Lock()
_worker_task: asyncio.Task | None = None
MAX_CONCURRENT_JOBS = 2


class CapacityError(Exception):
    """Conservé pour compat ; la file n'est plus bloquée — max 2 en parallèle côté worker."""

    def __init__(self, running: int, limit: int = MAX_CONCURRENT_JOBS):
        self.running = running
        self.limit = limit
        super().__init__(
            f"Déjà {running} encodage(s) en cours (max {limit}). "
            "Les nouveaux jobs sont mis en file."
        )


def count_jobs_by_state(*states: JobState) -> int:
    n = 0
    for batch in _batches.values():
        for job in batch.jobs:
            if job.state in states:
                n += 1
    return n


def jobs_status() -> dict[str, Any]:
    running = count_jobs_by_state(JobState.RUNNING)
    queued = count_jobs_by_state(JobState.QUEUED)
    return {
        "running": running,
        "queued": queued,
        "max_concurrent": MAX_CONCURRENT_JOBS,
        # Toujours possible d'enfiler ; le worker limite le parallèle
        "can_start": True,
        "can_run_now": running < MAX_CONCURRENT_JOBS,
    }


def _job_to_dict(j: EncodeJob) -> dict[str, Any]:
    return {
        "id": j.id,
        "source": j.source,
        "state": j.state.value,
        "progress_pct": round(j.progress_pct, 1),
        "speed": j.speed,
        "fps": j.fps,
        "frame": j.frame,
        "out_time": j.out_time,
        "eta": j.eta,
        "phase": j.phase,
        "message": j.message,
        "error": j.error,
        "output_tmp": j.output_tmp,
        "output_final": j.output_final,
        "log": list(j.log_lines)[-40:],
        "options": {
            "denoise": j.options.denoise,
            "stabilize": j.options.stabilize,
            "cq": j.options.cq,
            "mode": j.options.mode,
        },
    }


def _job_from_dict(d: dict[str, Any]) -> EncodeJob:
    opts_d = d.get("options") or {}
    opts = FileEncodeOptions(
        denoise=bool(opts_d.get("denoise", False)),
        stabilize=bool(opts_d.get("stabilize", False)),
        cq=int(opts_d.get("cq", 23)),
        mode=str(opts_d.get("mode") or "auto"),
    )
    state_s = str(d.get("state") or "queued")
    try:
        state = JobState(state_s)
    except ValueError:
        state = JobState.QUEUED
    # Jobs "running" au moment du dump n'ont plus de process après redémarrage
    if state == JobState.RUNNING:
        state = JobState.QUEUED
        phase = "Repris après redémarrage"
        message = phase
        error = ""
        progress = float(d.get("progress_pct") or 0)
    else:
        phase = str(d.get("phase") or "")
        message = str(d.get("message") or "")
        error = str(d.get("error") or "")
        progress = float(d.get("progress_pct") or 0)
    logs = deque(maxlen=80)
    for line in d.get("log") or []:
        logs.append(str(line))
    return EncodeJob(
        id=str(d.get("id") or uuid.uuid4()),
        source=str(d["source"]),
        options=opts,
        state=state,
        progress_pct=progress,
        speed=str(d.get("speed") or ""),
        eta=str(d.get("eta") or ""),
        message=message,
        phase=phase,
        frame=str(d.get("frame") or ""),
        fps=str(d.get("fps") or ""),
        out_time=str(d.get("out_time") or ""),
        output_tmp=str(d.get("output_tmp") or ""),
        output_final=str(d.get("output_final") or ""),
        error=error,
        log_lines=logs,
    )


def save_jobs(force: bool = False) -> None:
    global _last_jobs_save, _jobs_dirty
    with _persist_lock:
        now = time.time()
        if not force and not _jobs_dirty:
            return
        if not force and now - _last_jobs_save < 2.0:
            return
        state_dir = _state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": now,
            "batches": [
                {
                    "batch_id": b.batch_id,
                    "jobs": [_job_to_dict(j) for j in b.jobs],
                }
                for b in _batches.values()
            ],
        }
        jobs_path = _jobs_path()
        tmp = jobs_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(jobs_path)
        _last_jobs_save = now
        _jobs_dirty = False


def mark_jobs_dirty() -> None:
    global _jobs_dirty
    _jobs_dirty = True


def load_jobs() -> None:
    """Charge les lots persistés (jobs running → re-queued)."""
    jobs_path = _jobs_path()
    if not jobs_path.is_file():
        return
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    batches = data.get("batches") or []
    for bd in batches:
        if not isinstance(bd, dict):
            continue
        bid = str(bd.get("batch_id") or uuid.uuid4())
        jobs = []
        for jd in bd.get("jobs") or []:
            if not isinstance(jd, dict) or not jd.get("source"):
                continue
            jobs.append(_job_from_dict(jd))
        if not jobs:
            continue
        batch = EncodeBatch(batch_id=bid, jobs=jobs)
        batch.running = any(j.state in (JobState.QUEUED, JobState.RUNNING) for j in jobs)
        _batches[bid] = batch
    mark_jobs_dirty()
    save_jobs(force=True)


def all_jobs() -> list[EncodeJob]:
    out: list[EncodeJob] = []
    for batch in _batches.values():
        out.extend(batch.jobs)
    return out


def global_batch_view() -> dict[str, Any]:
    """Vue agrégée de tous les jobs (une seule file logique pour l'UI)."""
    jobs = all_jobs()
    # Ordre: running, queued, puis terminés récents en dernier
    order = {
        JobState.RUNNING: 0,
        JobState.QUEUED: 1,
        JobState.DONE: 2,
        JobState.ERROR: 3,
        JobState.CANCELLED: 4,
    }
    jobs_sorted = sorted(jobs, key=lambda j: (order.get(j.state, 9), j.source))
    fake = EncodeBatch(batch_id="all", jobs=jobs_sorted)
    fake.running = any(j.state in (JobState.QUEUED, JobState.RUNNING) for j in jobs)
    d = batch_to_dict(fake)
    d["batch_ids"] = list(_batches.keys())
    return d


async def ensure_worker() -> None:
    global _worker_task
    async with _batch_lock:
        if _worker_task is None or _worker_task.done():
            if count_jobs_by_state(JobState.QUEUED, JobState.RUNNING) > 0:
                _worker_task = asyncio.create_task(_batch_worker())


def bootstrap_jobs() -> None:
    """Appelé au démarrage du process avant la boucle asyncio."""
    load_jobs()


def probe_ffmpeg() -> dict[str, Any]:
    settings = get_settings()
    env = settings.ffmpeg_env()
    try:
        enc = subprocess.run(
            [settings.ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        flt = subprocess.run(
            [settings.ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        enc_out = (enc.stdout or "") + (enc.stderr or "")
        flt_out = (flt.stdout or "") + (flt.stderr or "")
        has_nvenc = "hevc_nvenc" in enc_out
        has_vidstab = "vidstab" in flt_out
        has_hqdn3d = "hqdn3d" in flt_out
        ok_run = has_nvenc and has_hqdn3d
        return {
            "ok": ok_run,
            "ffmpeg": settings.ffmpeg,
            "hevc_nvenc": has_nvenc,
            "hqdn3d": has_hqdn3d,
            "vidstab": has_vidstab,
            "error": "" if ok_run else (enc.stderr or flt.stderr or "ffmpeg/echec")[:500],
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "ffmpeg": settings.ffmpeg,
            "hevc_nvenc": False,
            "hqdn3d": False,
            "vidstab": False,
            "error": "ffmpeg introuvable",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "ffmpeg": settings.ffmpeg,
            "hevc_nvenc": False,
            "hqdn3d": False,
            "vidstab": False,
            "error": "ffmpeg timeout",
        }


def probe_gpu() -> dict[str, Any]:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return {"available": False, "error": (r.stderr or r.stdout).strip()}
        lines = [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
        gpus = []
        for ln in lines:
            parts = [p.strip() for p in ln.split(",")]
            gpus.append(
                {
                    "name": parts[0] if parts else ln,
                    "driver": parts[1] if len(parts) > 1 else "",
                    "memory": parts[2] if len(parts) > 2 else "",
                }
            )
        return {"available": True, "gpus": gpus}
    except FileNotFoundError:
        return {"available": False, "error": "nvidia-smi introuvable"}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "nvidia-smi timeout"}


def _run_cmd(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def generate_thumbnail(source: str, max_width: int = 320) -> bytes | None:
    settings = get_settings()
    env = settings.ffmpeg_env()
    cmd = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        settings.thumb_seek,
        "-i",
        source,
        "-frames:v",
        "1",
        "-vf",
        f"scale={max_width}:-1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    r = subprocess.run(cmd, capture_output=True, env=env)
    if r.returncode != 0 or not r.stdout:
        cmd[5] = "00:00:01"
        r = subprocess.run(cmd, capture_output=True, env=env)
    if r.returncode != 0 or not r.stdout:
        return None
    return r.stdout


def _parse_progress(line: str, state: dict[str, str]) -> None:
    line = line.strip()
    if not line or "=" not in line:
        return
    key, _, val = line.partition("=")
    state[key] = val


def _out_time_seconds(prog: dict[str, str]) -> float | None:
    """FFmpeg progress: prefer out_time_us (recent), then out_time_ms, then out_time."""
    if "out_time_us" in prog:
        try:
            return int(prog["out_time_us"]) / 1_000_000.0
        except ValueError:
            pass
    if "out_time_ms" in prog:
        raw = prog["out_time_ms"]
        try:
            # Some builds: microseconds wrongly labeled _ms
            val = int(raw)
            if val > 10_000_000:  # clearly microseconds
                return val / 1_000_000.0
            return val / 1000.0
        except ValueError:
            pass
    if "out_time" in prog and prog["out_time"] not in ("N/A", ""):
        parts = prog["out_time"].split(":")
        try:
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
        except ValueError:
            pass
    return None


def _apply_progress(
    job: EncodeJob,
    prog: dict[str, str],
    duration: float | None,
    pct_offset: float,
    pct_span: float,
) -> None:
    t = _out_time_seconds(prog)
    if t is not None and duration and duration > 0:
        local = min(1.0, max(0.0, t / duration))
        job.progress_pct = round(pct_offset + local * pct_span, 1)
        job.out_time = prog.get("out_time") or f"{t:.1f}s"
    if "speed" in prog and prog["speed"] not in ("N/A", ""):
        job.speed = prog["speed"].rstrip("x")
    if "frame" in prog:
        job.frame = prog["frame"]
    if "fps" in prog and prog["fps"] not in ("N/A", ""):
        job.fps = prog["fps"]


def _duration_seconds(source: str) -> float | None:
    settings = get_settings()
    cmd = [
        settings.resolved_ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        source,
    ]
    r = _run_cmd(cmd, env=settings.ffmpeg_env())
    if r.returncode != 0:
        return None
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _vidstab_detect_vf(transforms_path: str) -> str:
    return f"vidstabdetect=shakiness=5:accuracy=15:result={transforms_path}"


def _build_vf_pass2(options: FileEncodeOptions, transforms_path: str | None) -> str:
    """Filtres encode : toujours forcer yuv420p (NVENC ne gère pas yuv422 MJPEG etc.)."""
    parts: list[str] = []
    if options.denoise:
        parts.append("hqdn3d=4:3:6:4.5")
    if options.stabilize and transforms_path:
        parts.append(f"vidstabtransform=input={transforms_path}:smoothing=30")
    # Conversion pixel format en fin de chaîne — requis pour hevc_nvenc
    parts.append("format=yuv420p")
    return ",".join(parts)


def _output_looks_valid(final_p: Path, source: str = "") -> tuple[bool, str]:
    """Refuse de finaliser si le .mp4 est vide / tronqué / illisible."""
    if not final_p.is_file():
        return False, f"Encodé introuvable ({final_p.name})"
    size = final_p.stat().st_size
    if size < 4096:
        return False, f"{final_p.name} trop petit ({size} o) — ignoré"
    # Optionnel : durée via ffprobe
    dur = _duration_seconds(str(final_p))
    if dur is not None and dur <= 0.05:
        return False, f"{final_p.name} durée nulle — ignoré"
    if dur is None:
        # Si on ne peut pas prober, on accepte si taille raisonnable
        if size < 32_768:
            return False, f"{final_p.name} illisible / trop petit — ignoré"
    return True, ""


def _drain_stderr(proc: subprocess.Popen[str], job: EncodeJob) -> None:
    assert proc.stderr is not None
    for line in proc.stderr:
        job.log(line)


def _kill_proc(proc: subprocess.Popen[str]) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_ffmpeg_progress(
    job: EncodeJob,
    cmd: list[str],
    env: dict[str, str],
    duration: float | None,
    pct_offset: float,
    pct_span: float,
    on_progress: Callable[[EncodeJob], None],
) -> None:
    if job.cancel_requested:
        raise JobCancelled()
    job.log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )
    _job_procs[job.id] = proc
    assert proc.stdout is not None
    err_thread = threading.Thread(target=_drain_stderr, args=(proc, job), daemon=True)
    err_thread.start()

    prog: dict[str, str] = {}
    last_push = 0.0
    try:
        for line in proc.stdout:
            if job.cancel_requested:
                job.log("annulation demandée — kill ffmpeg")
                _kill_proc(proc)
                raise JobCancelled()
            _parse_progress(line, prog)
            if line.strip() == "progress=continue" or line.strip() == "progress=end":
                _apply_progress(job, prog, duration, pct_offset, pct_span)
                now = time.monotonic()
                if now - last_push > 0.4 or line.strip() == "progress=end":
                    detail = []
                    if job.phase:
                        detail.append(job.phase)
                    if job.out_time:
                        detail.append(job.out_time)
                    if job.frame:
                        detail.append(f"frame {job.frame}")
                    if job.fps:
                        detail.append(f"{job.fps} fps")
                    if job.speed:
                        detail.append(f"{job.speed}x")
                    job.message = " · ".join(detail) if detail else job.message
                    on_progress(job)
                    last_push = now

        proc.wait()
        err_thread.join(timeout=5)
        if job.cancel_requested or proc.returncode in (-15, -9, 255):
            # SIGTERM/SIGKILL or cancel flag
            if job.cancel_requested:
                raise JobCancelled()
        if proc.returncode != 0:
            if job.cancel_requested:
                raise JobCancelled()
            tail = "\n".join(list(job.log_lines)[-30:])
            raise RuntimeError(tail or f"ffmpeg code {proc.returncode}")
    finally:
        _job_procs.pop(job.id, None)


def _want_remux(job: EncodeJob) -> bool:
    if job.options.denoise or job.options.stabilize:
        return False
    mode = (job.options.mode or "auto").lower()
    return mode == "remux"


def _probe_format_tags(path: str) -> dict[str, str]:
    settings = get_settings()
    env = settings.ffmpeg_env()
    try:
        r = subprocess.run(
            [
                settings.resolved_ffprobe(),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if r.returncode != 0:
            return {}
        data = json.loads(r.stdout or "{}")
        tags = (data.get("format") or {}).get("tags") or {}
        return {str(k): str(v) for k, v in tags.items()}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return {}


def _ffmpeg_metadata_args(source: str) -> list[str]:
    """Args ffmpeg pour conserver date / GPS / tags conteneur + chapitres."""
    args: list[str] = [
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
    ]
    tags = _probe_format_tags(source)
    # creation_time explicite (certains muxers MP4 la droppent sinon)
    creation = (
        tags.get("creation_time")
        or tags.get("com.apple.quicktime.creationdate")
        or tags.get("date")
        or tags.get("DateTimeOriginal")
    )
    if creation:
        args.extend(["-metadata", f"creation_time={creation}"])
    # Localisation (clés variables selon appareil)
    for key, val in tags.items():
        lk = key.lower()
        if "location" in lk or lk in {"com.apple.quicktime.location.iso6709", "location-eng"}:
            args.extend(["-metadata", f"{key}={val}"])
    return args


def _preserve_mtime(src: Path, dst: Path, job: EncodeJob | None = None) -> None:
    try:
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))
        if job:
            job.log(f"mtime conservée: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}")
    except OSError as e:
        if job:
            job.log(f"mtime non conservée: {e}")


def _exiftool_bin() -> str | None:
    for cand in ("exiftool", "/usr/bin/exiftool"):
        try:
            r = subprocess.run([cand, "-ver"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return cand
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def _copy_metadata_exiftool(src: Path, dst: Path, job: EncodeJob | None = None) -> bool:
    """Copie maximale des tags (EXIF/XMP/QuickTime) via exiftool si dispo."""
    bin_et = _exiftool_bin()
    if not bin_et:
        if job:
            job.log("exiftool absent — métadonnées via ffmpeg uniquement")
        return False
    cmd = [
        bin_et,
        "-overwrite_original",
        "-api",
        "QuickTimeUTC=1",
        "-TagsFromFile",
        str(src),
        "-All:All",
        "-FileModifyDate<FileModifyDate",
        "-FileCreateDate<FileCreateDate",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if job:
            out = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")
            if r.returncode == 0:
                job.log(f"exiftool: tags copiés ({out[:160]})")
            else:
                job.log(f"exiftool: échec {r.returncode} ({out[:200]})")
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as e:
        if job:
            job.log(f"exiftool: erreur {e}")
        return False


def _publish_output(job: EncodeJob, out_tmp: Path, out_final: Path, on_progress) -> None:
    if not out_tmp.is_file():
        raise RuntimeError("Fichier temporaire d'encodage absent après ffmpeg")
    if out_final.exists():
        out_final.unlink()
    out_tmp.rename(out_final)
    src = Path(job.source)
    job.phase = "Métadonnées…"
    job.message = job.phase
    on_progress(job)
    _copy_metadata_exiftool(src, out_final, job)
    _preserve_mtime(src, out_final, job)
    job.output_tmp = ""
    job.output_final = str(out_final)
    job.progress_pct = 100.0
    job.phase = "Enregistré à côté"
    job.message = f"{out_final.name} (original conservé — à vérifier)"
    job.log(f"publié: {out_final}")
    on_progress(job)


def _remux_blocking(job: EncodeJob, on_progress: Callable[[EncodeJob], None]) -> None:
    """Copie vidéo/audio sans réencodage (MOV/MKV/… → MP4)."""
    settings = get_settings()
    env = settings.ffmpeg_env()
    src = Path(job.source)
    out_tmp = temp_output_path(src)
    out_final = final_output_path(src)
    job.output_tmp = str(out_tmp)
    job.output_final = str(out_final)
    if out_tmp.exists():
        out_tmp.unlink()

    duration = _duration_seconds(job.source)
    job.log(f"durée source: {duration:.1f}s" if duration else "durée source: inconnue")
    job.phase = "Remux → MP4 (copie streams)"
    job.message = job.phase
    on_progress(job)

    cmd = [
        settings.ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        job.source,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        *_ffmpeg_metadata_args(job.source),
        "-movflags",
        "+faststart+use_metadata_tags",
        "-progress",
        "pipe:1",
        "-nostats",
        str(out_tmp),
    ]
    try:
        if job.cancel_requested:
            raise JobCancelled()
        _run_ffmpeg_progress(job, cmd, env, duration, 0.0, 100.0, on_progress)
        if job.cancel_requested:
            raise JobCancelled()
        _publish_output(job, out_tmp, out_final, on_progress)
    except JobCancelled:
        try:
            if out_tmp.is_file():
                out_tmp.unlink()
        except OSError:
            pass
        job.output_tmp = ""
        raise


def _encode_blocking(job: EncodeJob, on_progress: Callable[[EncodeJob], None]) -> None:
    if _want_remux(job):
        _remux_blocking(job, on_progress)
        return

    settings = get_settings()
    env = settings.ffmpeg_env()
    src = Path(job.source)
    out_tmp = temp_output_path(src)
    out_final = final_output_path(src)
    job.output_tmp = str(out_tmp)
    job.output_final = str(out_final)

    if out_tmp.exists():
        out_tmp.unlink()

    duration = _duration_seconds(job.source)
    job.log(f"durée source: {duration:.1f}s" if duration else "durée source: inconnue")
    transforms_file: str | None = None
    td: tempfile.TemporaryDirectory | None = None

    # With stabilize: pass1 = 0-35%, encode = 35-100%. Else encode = 0-100%.
    has_stab = bool(job.options.stabilize)
    analyze_span = 35.0 if has_stab else 0.0
    encode_offset = analyze_span
    encode_span = 100.0 - encode_offset

    try:
        if job.cancel_requested:
            raise JobCancelled()
        if has_stab:
            td = tempfile.TemporaryDirectory(prefix="jaj-stab-")
            transforms_file = str(Path(td.name) / "transforms.trf")
            job.phase = "Analyse stabilisation (pass 1/2)"
            job.message = job.phase
            on_progress(job)
            cmd1 = [
                settings.ffmpeg,
                "-hide_banner",
                "-y",
                "-i",
                job.source,
                "-vf",
                _vidstab_detect_vf(transforms_file),
                "-progress",
                "pipe:1",
                "-nostats",
                "-f",
                "null",
                "-",
            ]
            _run_ffmpeg_progress(
                job, cmd1, env, duration, 0.0, analyze_span, on_progress
            )
            if job.cancel_requested:
                raise JobCancelled()
            job.progress_pct = analyze_span
            job.log("analyse stab terminée")

        vf2 = _build_vf_pass2(job.options, transforms_file)
        cq = max(18, min(35, job.options.cq))
        job.phase = "Encodage HEVC NVENC"
        if job.options.denoise:
            job.phase += " + denoise"
        if has_stab:
            job.phase += " + stab (pass 2/2)"
        job.message = job.phase
        on_progress(job)
        if job.cancel_requested:
            raise JobCancelled()

        cmd = [
            settings.ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            job.source,
            "-vf",
            vf2,
            "-c:v",
            "hevc_nvenc",
            "-preset",
            "p5",
            "-rc",
            "vbr",
            "-cq",
            str(cq),
            "-b:v",
            "0",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            *_ffmpeg_metadata_args(job.source),
            "-movflags",
            "+faststart+use_metadata_tags",
            "-progress",
            "pipe:1",
            "-nostats",
            str(out_tmp),
        ]
        _run_ffmpeg_progress(
            job, cmd, env, duration, encode_offset, encode_span, on_progress
        )
        if job.cancel_requested:
            raise JobCancelled()

        _publish_output(job, out_tmp, out_final, on_progress)
    except JobCancelled:
        for p in (out_tmp, out_final):
            try:
                if p.is_file() and p.suffix.lower() in (".mp4",) and (
                    str(p).endswith(".jajtmp.mp4") or p == out_tmp
                ):
                    p.unlink()
            except OSError:
                pass
        try:
            if out_tmp.is_file():
                out_tmp.unlink()
        except OSError:
            pass
        job.output_tmp = ""
        raise
    finally:
        if td:
            td.cleanup()


def delete_originals(results: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Supprime les fichiers sources ; garde les .mp4 encodés (uniquement si .mp4 valide).

    Compte les économies uniquement quand l'original est réellement supprimé.
    """
    out: list[dict[str, Any]] = []
    for item in results:
        source = item.get("source", "")
        final = item.get("output_final", "")
        rec: dict[str, Any] = {
            "source": source,
            "ok": "false",
            "message": "",
            "replaced": False,
            "bytes_original": 0,
            "bytes_encoded": 0,
            "bytes_saved": 0,
        }
        try:
            src_p = Path(source)
            final_p = Path(final) if final else final_output_path(src_p)
            ok, why = _output_looks_valid(final_p, source)
            if not ok:
                rec["message"] = why or "Encodé invalide — original conservé"
                out.append(rec)
                continue
            enc_size = final_p.stat().st_size
            rec["bytes_encoded"] = enc_size
            if not src_p.is_file():
                rec["ok"] = "true"
                rec["message"] = "Original déjà absent"
                out.append(rec)
                continue
            orig_size = src_p.stat().st_size
            src_p.unlink()
            saved = orig_size - enc_size
            rec["ok"] = "true"
            rec["replaced"] = True
            rec["bytes_original"] = orig_size
            rec["bytes_saved"] = saved
            pct = (100.0 * saved / orig_size) if orig_size else 0.0
            rec["message"] = (
                f"Original supprimé — reste {final_p.name} "
                f"({_fmt_bytes(orig_size)} → {_fmt_bytes(enc_size)}, "
                f"{'-' if saved >= 0 else '+'}{_fmt_bytes(abs(saved))} / {pct:.0f}%)"
            )
        except OSError as e:
            rec["message"] = str(e)
        out.append(rec)
    return out


def _fmt_bytes(n: int) -> str:
    n = abs(int(n))
    if n < 1024:
        return f"{n} o"
    if n < 1024**2:
        return f"{n / 1024:.1f} Ko"
    if n < 1024**3:
        return f"{n / 1024**2:.1f} Mo"
    return f"{n / 1024**3:.2f} Go"


def cancel_encodes(results: list[dict[str, str]]) -> list[dict[str, str]]:
    """Supprime les .mp4 encodés ; conserve les originaux."""
    out: list[dict[str, str]] = []
    for item in results:
        source = item.get("source", "")
        final = item.get("output_final", "")
        tmp = item.get("output_tmp", "")
        rec: dict[str, str] = {"source": source, "ok": "false", "message": ""}
        try:
            src_p = Path(source)
            final_p = Path(final) if final else final_output_path(src_p)
            tmp_p = Path(tmp) if tmp else temp_output_path(src_p)
            removed = []
            if final_p.is_file():
                final_p.unlink()
                removed.append(final_p.name)
            if tmp_p.is_file():
                tmp_p.unlink()
                removed.append(tmp_p.name)
            if not removed:
                rec["message"] = "Rien à annuler"
            else:
                rec["ok"] = "true"
                rec["message"] = "Annulé: " + ", ".join(removed)
        except OSError as e:
            rec["message"] = str(e)
        out.append(rec)
    return out


# Compat anciens noms
def replace_originals(results: list[dict[str, str]]) -> list[dict[str, str]]:
    return delete_originals(results)


def save_alongside(results: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"source": r.get("source", ""), "ok": "true", "message": "déjà à côté"} for r in results]


async def start_batch(sources: list[str], options_map: dict[str, FileEncodeOptions]) -> str:
    global _worker_task
    batch_id = str(uuid.uuid4())
    jobs = []
    for src in sources:
        opts = options_map.get(src, FileEncodeOptions())
        jobs.append(EncodeJob(id=str(uuid.uuid4()), source=src, options=opts))
    batch = EncodeBatch(batch_id=batch_id, jobs=jobs)
    async with _batch_lock:
        # Toujours enfiler ; le worker cap à MAX_CONCURRENT_JOBS
        _batches[batch_id] = batch
        mark_jobs_dirty()
        save_jobs(force=True)
        if _worker_task is None or _worker_task.done():
            _worker_task = asyncio.create_task(_batch_worker())
    return batch_id


def get_batch(batch_id: str) -> EncodeBatch | None:
    if batch_id == "all":
        # synthétique — ne pas stocker
        return None
    return _batches.get(batch_id)


def list_completed_for_replace(batch_id: str | None = None) -> list[dict[str, str]]:
    """Uniquement les jobs DONE avec un .mp4 valide — jamais les erreurs."""
    if batch_id and batch_id != "all":
        batches = [_batches[batch_id]] if batch_id in _batches else []
    else:
        batches = list(_batches.values())
    rows = []
    for batch in batches:
        for job in batch.jobs:
            if job.state != JobState.DONE:
                continue
            final = job.output_final or str(final_output_path(Path(job.source)))
            final_p = Path(final)
            ok, _why = _output_looks_valid(final_p, job.source)
            if not ok:
                continue
            rows.append(
                {
                    "source": job.source,
                    "output_tmp": job.output_tmp or "",
                    "output_final": final,
                }
            )
    return rows


def preview_replace_savings(batch_id: str | None = None) -> dict[str, Any]:
    """Économie d'espace si on remplace les originaux (sans rien supprimer)."""
    items = list_completed_for_replace(batch_id)
    files = 0
    bytes_original = 0
    bytes_encoded = 0
    details: list[dict[str, Any]] = []
    for it in items:
        src = Path(it.get("source") or "")
        final = Path(it.get("output_final") or "")
        if not src.is_file() or not final.is_file():
            continue
        try:
            orig = src.stat().st_size
            enc = final.stat().st_size
        except OSError:
            continue
        files += 1
        bytes_original += orig
        bytes_encoded += enc
        details.append(
            {
                "source": str(src),
                "bytes_original": orig,
                "bytes_encoded": enc,
                "bytes_saved": orig - enc,
            }
        )
    return {
        "files": files,
        "bytes_original": bytes_original,
        "bytes_encoded": bytes_encoded,
        "bytes_saved": bytes_original - bytes_encoded,
        "details": details,
    }


async def _batch_worker() -> None:
    """Dispatch jusqu'à MAX_CONCURRENT_JOBS encodages en parallèle."""
    active: set[asyncio.Task[None]] = set()

    while True:
        # nettoyer les tâches finies
        done = {t for t in active if t.done()}
        for t in done:
            active.discard(t)
            try:
                t.result()
            except Exception:
                pass

        while len(active) < MAX_CONCURRENT_JOBS:
            picked = _claim_next_job()
            if not picked:
                break
            job, batch = picked
            active.add(asyncio.create_task(_run_claimed_job(job, batch)))

        _refresh_batch_running_flags()
        save_jobs(force=False)
        if not active and count_jobs_by_state(JobState.QUEUED) == 0:
            save_jobs(force=True)
            return
        await asyncio.sleep(0.25)


def _claim_next_job() -> tuple[EncodeJob, EncodeBatch] | None:
    for batch in _batches.values():
        for job in batch.jobs:
            if job.state == JobState.QUEUED:
                job.state = JobState.RUNNING
                job.message = "Encodage…"
                job.phase = "Démarrage…"
                batch.running = True
                mark_jobs_dirty()
                save_jobs(force=True)
                return job, batch
    return None


def _refresh_batch_running_flags() -> None:
    for batch in _batches.values():
        batch.running = any(
            j.state in (JobState.QUEUED, JobState.RUNNING) for j in batch.jobs
        )


async def _run_claimed_job(job: EncodeJob, batch: EncodeBatch) -> None:
    def on_progress(_j: EncodeJob) -> None:
        mark_jobs_dirty()
        save_jobs(force=False)

    try:
        await asyncio.to_thread(_encode_blocking, job, on_progress)
        if job.cancel_requested:
            raise JobCancelled()
        job.state = JobState.DONE
        if not job.message or job.message == "Encodage…":
            job.message = "Terminé"
    except JobCancelled:
        job.state = JobState.CANCELLED
        job.phase = "Annulé"
        job.message = "Annulé"
        job.log("job annulé")
    except Exception as e:
        if job.cancel_requested:
            job.state = JobState.CANCELLED
            job.phase = "Annulé"
            job.message = "Annulé"
        else:
            job.state = JobState.ERROR
            job.error = str(e)
            job.message = "Erreur"
            job.phase = "Erreur"
    finally:
        _refresh_batch_running_flags()
        mark_jobs_dirty()
        save_jobs(force=True)


def find_job(job_id: str) -> EncodeJob | None:
    for batch in _batches.values():
        for job in batch.jobs:
            if job.id == job_id:
                return job
    return None


def cancel_job(job_id: str) -> dict[str, Any]:
    """Annule un job (file ou en cours) ; le worker passera au suivant."""
    job = find_job(job_id)
    if not job:
        return {"ok": False, "message": "Job introuvable"}
    if job.state in (JobState.DONE, JobState.ERROR, JobState.CANCELLED):
        return {"ok": False, "message": f"Job déjà terminé ({job.state.value})"}
    job.cancel_requested = True
    if job.state == JobState.QUEUED:
        job.state = JobState.CANCELLED
        job.phase = "Annulé"
        job.message = "Annulé (file)"
        job.log("retiré de la file")
        _refresh_batch_running_flags()
        mark_jobs_dirty()
        save_jobs(force=True)
        return {"ok": True, "message": "Job retiré de la file", "job_id": job_id}
    proc = _job_procs.get(job_id)
    if proc and proc.poll() is None:
        _kill_proc(proc)
    mark_jobs_dirty()
    save_jobs(force=True)
    return {"ok": True, "message": "Annulation demandée", "job_id": job_id}


def stop_all_jobs() -> dict[str, Any]:
    """Annule tous les jobs en cours et en file."""
    cancelled_queued = 0
    cancel_running = 0
    for batch in _batches.values():
        for job in batch.jobs:
            if job.state == JobState.QUEUED:
                job.cancel_requested = True
                job.state = JobState.CANCELLED
                job.phase = "Annulé"
                job.message = "Stop général"
                cancelled_queued += 1
            elif job.state == JobState.RUNNING:
                job.cancel_requested = True
                job.message = "Stop général…"
                proc = _job_procs.get(job.id)
                if proc and proc.poll() is None:
                    _kill_proc(proc)
                cancel_running += 1
    _refresh_batch_running_flags()
    mark_jobs_dirty()
    save_jobs(force=True)
    return {
        "ok": True,
        "cancelled_queued": cancelled_queued,
        "cancel_running": cancel_running,
        "jobs_status": jobs_status(),
    }


def clear_finished_jobs() -> dict[str, Any]:
    """Retire les jobs terminés / annulés / erreur de la mémoire (après finalisation)."""
    removed = 0
    empty_batches: list[str] = []
    for bid, batch in list(_batches.items()):
        keep = [j for j in batch.jobs if j.state in (JobState.QUEUED, JobState.RUNNING)]
        removed += len(batch.jobs) - len(keep)
        batch.jobs = keep
        if not batch.jobs:
            empty_batches.append(bid)
    for bid in empty_batches:
        _batches.pop(bid, None)
    mark_jobs_dirty()
    save_jobs(force=True)
    return {"ok": True, "removed": removed, "jobs_status": jobs_status()}


def batch_to_dict(batch: EncodeBatch) -> dict[str, Any]:
    status = jobs_status()
    return {
        "batch_id": batch.batch_id,
        "running": batch.running,
        "jobs_status": status,
        "jobs": [_job_to_dict(j) for j in batch.jobs],
    }
