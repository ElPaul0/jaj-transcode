#!/bin/bash
set -euo pipefail
# Promote n8.1 build (NVENC 13.0 compatible + vidstab)
if [[ ! -x /opt/ffmpeg-btbn-n81/bin/ffmpeg ]]; then
  echo "n81 missing, run try-btbn-n81.sh first"
  exit 1
fi
rm -rf /opt/ffmpeg-btbn
mv /opt/ffmpeg-btbn-n81 /opt/ffmpeg-btbn

cat > /usr/local/bin/ffmpeg <<'EOF'
#!/bin/bash
export LD_LIBRARY_PATH=/opt/ffmpeg-btbn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
exec /opt/ffmpeg-btbn/bin/ffmpeg "$@"
EOF
cat > /usr/local/bin/ffprobe <<'EOF'
#!/bin/bash
export LD_LIBRARY_PATH=/opt/ffmpeg-btbn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
exec /opt/ffmpeg-btbn/bin/ffprobe "$@"
EOF
chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# Met à jour les chemins FFmpeg dans la config d'env si elle existe
ENV_FILE="/etc/jaj-transcode.env"
if [[ -f "$ENV_FILE" ]]; then
  sed -i 's|^JAJ_FFMPEG=.*|JAJ_FFMPEG=/opt/ffmpeg-btbn/bin/ffmpeg|' "$ENV_FILE"
  sed -i 's|^JAJ_FFPROBE=.*|JAJ_FFPROBE=/opt/ffmpeg-btbn/bin/ffprobe|' "$ENV_FILE"
  sed -i 's|^JAJ_LD_LIBRARY_PATH=.*|JAJ_LD_LIBRARY_PATH=/opt/ffmpeg-btbn/lib|' "$ENV_FILE"
fi

SVC=/etc/systemd/system/jaj-transcode.service
if [[ -f /opt/jaj-transcode/deploy/jaj-transcode.service ]]; then
  install -m 644 /opt/jaj-transcode/deploy/jaj-transcode.service "$SVC"
fi

systemctl daemon-reload
systemctl restart jaj-transcode
sleep 1
echo "=== nvenc ==="
ffmpeg -hide_banner -f lavfi -i color=c=black:s=320x240:d=0.3 -c:v hevc_nvenc -f null - 2>&1 | tail -6
echo "=== api ==="
curl -s http://127.0.0.1:8791/api/ffmpeg; echo
systemctl is-active jaj-transcode
