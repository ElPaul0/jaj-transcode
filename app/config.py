from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EXTENSIONS = (
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
)


class Settings(BaseSettings):
    """Configuration via variables d'environnement `JAJ_*` et/ou fichier `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="JAJ_",
        env_file=(
            str(APP_ROOT / ".env"),
            "/etc/jaj-transcode.env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Répertoire scanné par défaut (surchargeable dans l'UI)
    work_dir: str = "/data/videos"
    # FFmpeg avec hevc_nvenc + vidstab + hqdn3d (ex. build BtbN n8.1 GPL)
    ffmpeg: str = "/opt/ffmpeg-btbn/bin/ffmpeg"
    ffprobe: str = "/opt/ffmpeg-btbn/bin/ffprobe"
    ld_library_path: str = "/opt/ffmpeg-btbn/lib"
    host: str = "0.0.0.0"
    port: int = 8791
    data_dir: str = str(APP_ROOT / "data")
    extensions: str = ",".join(DEFAULT_EXTENSIONS)
    tmp_suffix: str = ".jajtmp.mp4"
    thumb_seek: str = "00:00:05"

    def ffmpeg_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.ld_library_path:
            prev = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = (
                f"{self.ld_library_path}:{prev}" if prev else self.ld_library_path
            )
        return env

    def extension_set(self) -> set[str]:
        parts = [p.strip().lower() for p in self.extensions.split(",") if p.strip()]
        return {p if p.startswith(".") else f".{p}" for p in parts}

    def resolved_ffprobe(self) -> str:
        if self.ffprobe:
            return self.ffprobe
        ff = Path(self.ffmpeg)
        probe = ff.parent / "ffprobe"
        if probe.is_file():
            return str(probe)
        return "ffprobe"

    def resolved_data_dir(self) -> Path:
        return Path(self.data_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def set_work_dir(path: str) -> None:
    """Persiste le répertoire scanné sur l'instance Settings en cache."""
    settings = get_settings()
    object.__setattr__(settings, "work_dir", path)
