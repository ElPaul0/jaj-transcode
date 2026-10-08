# jaj-transcode

Web UI for **batch H.265 (HEVC) NVENC** encoding on a Linux host with an NVIDIA GPU.

Scan a folder of camcorder / phone rushes (`.mts`, `.mov`, `.avi`, …), optionally denoise / stabilize, encode or remux to `.mp4` next to the originals, then delete sources once you have checked the results.

Built for homelab use (Proxmox host + NVIDIA NVENC), but runs on any Linux machine with a compatible FFmpeg build and GPU driver.

## Features

- FastAPI backend + lightweight static UI
- Parallel scan with `ffprobe` classification (`encode` vs `remux`)
- HEVC NVENC encode (`hevc_nvenc`), optional `hqdn3d` denoise and `vidstab` stabilisation
- Remux path for already-modern codecs (copy streams → MP4)
- Shared session across browser tabs (persisted jobs + UI state)
- Cancel / stop-all, progress + FFmpeg log
- Safe finalize: keep `.mp4` and delete originals, or undo encoded files
- Disk-space savings counter after replacements

## Requirements

- Linux + NVIDIA GPU with NVENC (tested with driver **580.x** / NVENC API 13.0)
- Python 3.11+
- FFmpeg build with:
  - `hevc_nvenc`
  - filters `hqdn3d` and `vidstab` (for denoise / stabilize)
- Recommended: [BtbN](https://github.com/BtbN/FFmpeg-Builds) **n8.1** GPL linux64  
  Avoid bleeding-edge “master” builds that require NVIDIA driver ≥ 610 if you are still on 580.x.

Helper scripts under `deploy/` can install a BtbN build under `/opt/ffmpeg-btbn`.

## Quick start

```bash
git clone https://github.com/ElPaul0/jaj-transcode.git
cd jaj-transcode
cp .env.example .env
# edit .env — at least JAJ_WORK_DIR and FFmpeg paths

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8791
```

Open `http://<host>:8791/`.

## Configuration

All personal / host-specific settings live in environment variables (prefix `JAJ_`) or in:

| File | Use |
|------|-----|
| `.env` | Local / git checkout |
| `/etc/jaj-transcode.env` | systemd production |

Start from `.env.example`. Nothing sensitive belongs in the repo.

| Variable | Default | Meaning |
|----------|---------|---------|
| `JAJ_WORK_DIR` | `/data/videos` | Default scan root (also editable in the UI) |
| `JAJ_FFMPEG` | `/opt/ffmpeg-btbn/bin/ffmpeg` | FFmpeg binary |
| `JAJ_FFPROBE` | `/opt/ffmpeg-btbn/bin/ffprobe` | FFprobe binary |
| `JAJ_LD_LIBRARY_PATH` | `/opt/ffmpeg-btbn/lib` | Extra libs for that build |
| `JAJ_HOST` | `0.0.0.0` | Bind address |
| `JAJ_PORT` | `8791` | HTTP port |
| `JAJ_DATA_DIR` | `<install>/data` | `session.json` / `jobs.json` |
| `JAJ_EXTENSIONS` | `.mts,.m2ts,.mov,…` | Extensions to scan |

## Production install (systemd)

As root, from a clone or release tree:

```bash
bash _deploy.sh
# INSTALL_DIR=/opt/jaj-transcode by default
```

This will:

1. Sync files to `/opt/jaj-transcode`
2. Create `/etc/jaj-transcode.env` from `.env.example` if missing (**edit it**)
3. Create the venv and install Python deps
4. Install and start `jaj-transcode.service`

```bash
systemctl status jaj-transcode
journalctl -u jaj-transcode -f
```

## Typical workflow

1. Set the work directory and click **Analyser**
2. Filter / sort, tweak denoise · stabilize · CQ per file
3. **Encoder la sélection** → writes `name.mp4` next to each source (`*.jajtmp.mp4` during encode)
4. Play the `.mp4` files to verify
5. Either **Supprimer les originaux** or **Annuler** (remove encoded `.mp4`)

With stabilisation: pass 1 = CPU `vidstabdetect`, pass 2 = NVENC encode (+ optional denoise).

## Project layout

```
app/           FastAPI app (scanner, encoder, session)
static/        Web UI
deploy/        FFmpeg install helpers + systemd unit
.env.example   Config template
_deploy.sh     Install / update on a host
```

## License

MIT — see [LICENSE](LICENSE).
