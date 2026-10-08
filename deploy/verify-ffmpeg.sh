#!/usr/bin/env bash
set -euo pipefail

echo "=== /usr/local/bin/ffmpeg -encoders | grep hevc_nvenc ==="
/usr/local/bin/ffmpeg -hide_banner -encoders 2>&1 | grep hevc_nvenc || echo "MISSING"

echo "=== /usr/local/bin/ffmpeg -filters | grep vidstab ==="
/usr/local/bin/ffmpeg -hide_banner -filters 2>&1 | grep vidstab || echo "MISSING"

echo "=== curl /api/ffmpeg ==="
curl -s http://127.0.0.1:8791/api/ffmpeg
echo

echo "=== systemctl is-active jaj-transcode ==="
systemctl is-active jaj-transcode
