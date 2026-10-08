#!/bin/bash
set -euo pipefail
cd /tmp
name=ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz
url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/${name}"
echo "Downloading $name"
curl -fsSL -L -o "$name" "$url"
rm -rf /opt/ffmpeg-btbn-n81
mkdir -p /opt/ffmpeg-btbn-n81
tar -xJf "$name" -C /opt/ffmpeg-btbn-n81 --strip-components=1
bin=/opt/ffmpeg-btbn-n81/bin/ffmpeg
ld=/opt/ffmpeg-btbn-n81/lib
echo "=== filters ==="
LD_LIBRARY_PATH=$ld $bin -filters 2>&1 | grep vidstab || true
echo "=== nvenc test ==="
LD_LIBRARY_PATH=$ld $bin -hide_banner -f lavfi -i color=c=black:s=320x240:d=0.4 -c:v hevc_nvenc -f null - 2>&1 | tail -20
echo "=== x265 test ==="
LD_LIBRARY_PATH=$ld $bin -hide_banner -f lavfi -i color=c=black:s=320x240:d=0.4 -c:v libx265 -preset ultrafast -f null - 2>&1 | tail -12
