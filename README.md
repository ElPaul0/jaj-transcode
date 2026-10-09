# jaj-transcode

Web UI for **batch H.265 (HEVC) NVENC** encoding on a Linux host with an NVIDIA GPU — plus **jaj-organize**, a companion tool to find and remove photo/video duplicates (files and whole folders).

Scan camcorder / phone rushes (`.mts`, `.mov`, `.avi`, …), optionally denoise / stabilize, encode or remux to `.mp4` next to the originals, then delete sources once you have checked the results. Use `/organize/` when the same media exists in several places (inbox, copies, cloned folders).

Built for homelab use (Proxmox host + NVIDIA NVENC), but runs on any Linux machine with a compatible FFmpeg build and GPU driver.

Repo: [github.com/ElPaul0/jaj-transcode](https://github.com/ElPaul0/jaj-transcode)

## Features

### jaj-transcode (`/`)

- FastAPI backend + lightweight static UI
- Parallel scan with `ffprobe` classification (`encode` vs `remux` / Auto)
- HEVC NVENC encode (`hevc_nvenc`), optional `hqdn3d` denoise and `vidstab` stabilisation
- Remux path for already-modern codecs (copy streams → MP4)
- Metadata preserved when possible (ffmpeg tags + exiftool + file mtime)
- Shared session across browser tabs (persisted jobs + UI state)
- Configurable concurrent NVENC / remux jobs
- Cancel / stop-all, progress + FFmpeg log
- Safe finalize: keep `.mp4` and delete originals, or undo encoded files
- Disk-space savings counter after replacements

### jaj-organize (`/organize/`)

- Inventory of photos + videos under a root folder
- **Deprioritize folders** (e.g. “à trier”, Downloads): delete copies that already exist elsewhere
- Exact **folder clones** (same relative layout + sizes)
- Remaining **file duplicates** (same name + size)
- “Similar folders” listed for review only (no auto-delete)
- Confirm before delete; freed space counted in the shared savings badge

## Requirements

- Linux + NVIDIA GPU with NVENC (tested with driver **580.x** / NVENC API 13.0) — only needed for encode
- Python 3.11+
- FFmpeg build with:
  - `hevc_nvenc`
  - filters `hqdn3d` and `vidstab` (for denoise / stabilize)
- Optional: `exiftool` (`libimage-exiftool-perl`) for richer metadata copy after encode
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

- Encode UI: `http://<host>:8791/`
- Organize UI: `http://<host>:8791/organize/`

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
| `JAJ_EXTENSIONS` | `.mts,.m2ts,.mov,…` | Extensions for the encode scanner |
| `JAJ_MAX_REMUX_CAP` | (see `.env.example`) | Upper bound for concurrent remux jobs |

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

## Typical workflows

### Encode / remux

1. Set the work directory and click **Analyser**
2. Filter / sort, tweak denoise · stabilize · CQ (or use bulk Auto / Remux / Encode)
3. **Go** → writes `name.mp4` next to each source (`*.jajtmp.mp4` during encode)
4. Play the `.mp4` files to verify
5. Either **Remplacer les originaux** or **Annuler** (remove encoded `.mp4`)

With stabilisation: pass 1 = CPU `vidstabdetect`, pass 2 = NVENC encode (+ optional denoise).

### Organize (duplicates)

1. Open **jaj-organize** from the header link (or `/organize/`)
2. **Analyser** a root (photos + videos)
3. Tick folders to **deprioritize**, review “autres copies sûres”
4. **Supprimer les copies cochées** — originals elsewhere are kept

Duplicate matching uses **name + size** (fast on NAS). It is not a content hash: two different files with the same name and size would be treated as duplicates.

## Project layout

```
app/                 FastAPI (scanner, encoder, session, organize)
static/              Encode UI
static/organize/     jaj-organize UI
deploy/              FFmpeg install helpers + systemd unit
.env.example         Config template
_deploy.sh           Install / update on a host
```

## License

MIT — see [LICENSE](LICENSE).
