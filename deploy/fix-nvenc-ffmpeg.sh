#!/bin/bash
# Switch jaj-transcode to jellyfin-ffmpeg (NVENC OK on driver 580),
# then try older BtbN builds that still have vidstab + compatible NVENC.
set -euo pipefail

SVC=/etc/systemd/system/jaj-transcode.service

switch_to() {
  local ff="$1" probe="$2" ld="$3"
  sed -i "s|^Environment=JAJ_FFMPEG=.*|Environment=JAJ_FFMPEG=${ff}|" "$SVC"
  sed -i "s|^Environment=JAJ_FFPROBE=.*|Environment=JAJ_FFPROBE=${probe}|" "$SVC"
  sed -i "s|^Environment=JAJ_LD_LIBRARY_PATH=.*|Environment=JAJ_LD_LIBRARY_PATH=${ld}|" "$SVC"
  # ensure lines exist
  grep -q 'JAJ_FFMPEG=' "$SVC" || sed -i "/JAJ_WORK_DIR/a Environment=JAJ_FFMPEG=${ff}" "$SVC"
  systemctl daemon-reload
  systemctl restart jaj-transcode
  sleep 1
  echo "switched to $ff"
  curl -s http://127.0.0.1:8791/api/ffmpeg || true
  echo
}

test_nvenc() {
  local bin="$1" ld="$2"
  echo "TEST NVENC: $bin"
  LD_LIBRARY_PATH="$ld" "$bin" -hide_banner -f lavfi -i color=c=black:s=320x240:d=0.4 -c:v hevc_nvenc -f null - 2>&1 | tail -8
}

test_vidstab() {
  local bin="$1" ld="$2"
  LD_LIBRARY_PATH="$ld" "$bin" -hide_banner -filters 2>&1 | grep -E 'vidstabdetect|vidstabtransform' || echo "no vidstab"
}

echo "=== 1) jellyfin-ffmpeg (known good NVENC) ==="
JF=/opt/jellyfin-ffmpeg
JF_LD="${JF}/lib:${JF}/extra-libs"
# wrappers for CLI
cat > /usr/local/bin/ffmpeg <<EOF
#!/bin/bash
export LD_LIBRARY_PATH="${JF_LD}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec ${JF}/ffmpeg "\$@"
EOF
cat > /usr/local/bin/ffprobe <<EOF
#!/bin/bash
export LD_LIBRARY_PATH="${JF_LD}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec ${JF}/ffprobe "\$@"
EOF
chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
test_nvenc "$JF/ffmpeg" "$JF_LD"
test_vidstab "$JF/ffmpeg" "$JF_LD"
switch_to "$JF/ffmpeg" "$JF/ffprobe" "$JF_LD"

echo "=== 2) try older BtbN n7.1 / n6.1 gpl builds ==="
mkdir -p /opt/ffmpeg-btbn-candidates
cd /tmp
for name in \
  ffmpeg-n7.1-latest-linux64-gpl.tar.xz \
  ffmpeg-n7.0-latest-linux64-gpl.tar.xz \
  ffmpeg-n6.1-latest-linux64-gpl.tar.xz
do
  url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/${name}"
  echo "--- fetching $name ---"
  if ! curl -fsSL -o "$name" -L --retry 2 "$url"; then
    echo "download failed $name"
    continue
  fi
  dest="/opt/ffmpeg-btbn-candidates/${name%.tar.xz}"
  rm -rf "$dest"
  mkdir -p "$dest"
  tar -xJf "$name" -C "$dest" --strip-components=1
  bin="$dest/bin/ffmpeg"
  ld="$dest/lib"
  test_vidstab "$bin" "$ld"
  if LD_LIBRARY_PATH="$ld" "$bin" -hide_banner -f lavfi -i color=c=black:s=320x240:d=0.4 -c:v hevc_nvenc -f null - 2>&1 | tee /tmp/nvenc-try.log | grep -q "Driver does not support"; then
    echo "NVENC FAIL (API too new): $name"
    continue
  fi
  if LD_LIBRARY_PATH="$ld" "$bin" -hide_banner -f lavfi -i color=c=black:s=320x240:d=0.4 -c:v hevc_nvenc -f null - 2>&1 | grep -q "hevc_nvenc\|Lsize\|frame="; then
    if LD_LIBRARY_PATH="$ld" "$bin" -filters 2>&1 | grep -q vidstabdetect; then
      echo "WINNER: $name"
      rm -rf /opt/ffmpeg-btbn
      mv "$dest" /opt/ffmpeg-btbn
      cat > /usr/local/bin/ffmpeg <<EOF
#!/bin/bash
export LD_LIBRARY_PATH=/opt/ffmpeg-btbn/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
exec /opt/ffmpeg-btbn/bin/ffmpeg "\$@"
EOF
      cat > /usr/local/bin/ffprobe <<EOF
#!/bin/bash
export LD_LIBRARY_PATH=/opt/ffmpeg-btbn/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
exec /opt/ffmpeg-btbn/bin/ffprobe "\$@"
EOF
      chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
      switch_to /opt/ffmpeg-btbn/bin/ffmpeg /opt/ffmpeg-btbn/bin/ffprobe /opt/ffmpeg-btbn/lib
      test_nvenc /opt/ffmpeg-btbn/bin/ffmpeg /opt/ffmpeg-btbn/lib
      test_vidstab /opt/ffmpeg-btbn/bin/ffmpeg /opt/ffmpeg-btbn/lib
      curl -s http://127.0.0.1:8791/api/ffmpeg; echo
      exit 0
    fi
  fi
done

echo "No older BtbN matched. Staying on jellyfin-ffmpeg (encode OK, stab unavailable)."
curl -s http://127.0.0.1:8791/api/ffmpeg; echo
exit 0
