#!/usr/bin/env bash
set -euo pipefail

JFF="/opt/jellyfin-ffmpeg"
EXTRA_LIB="${JFF}/extra-libs"
WRAPPER_DIR="/usr/local/bin"
LD_PATH="${JFF}/lib:${EXTRA_LIB}"

echo "==> Jellyfin FFmpeg dans ${JFF}"
mkdir -p "${JFF}" "${EXTRA_LIB}"
pct exec 300 -- tar -C /usr/lib/jellyfin-ffmpeg -cf - . | tar -C "${JFF}" -xf -

if [[ ! -x "${JFF}/ffmpeg" ]]; then
  echo "ERREUR: ${JFF}/ffmpeg absent apres copie depuis CT300" >&2
  exit 1
fi

echo "==> Dependances systeme depuis CT300 (ldd)"
mapfile -t deps < <(
  pct exec 300 -- bash -lc \
    'export LD_LIBRARY_PATH=/usr/lib/jellyfin-ffmpeg/lib; ldd /usr/lib/jellyfin-ffmpeg/ffmpeg' \
    | awk '/=> \// {print $3}' | sort -u
)
skip_libs="libc.so.6 libm.so.6 libgcc_s.so.1 libstdc++.so.6 libpthread.so.0 libdl.so.2 librt.so.1"
for path in "${deps[@]}"; do
  [[ -z "${path}" ]] && continue
  base="$(basename "${path}")"
  if [[ " ${skip_libs} " == *" ${base} "* ]]; then
    continue
  fi
  if [[ -f "${EXTRA_LIB}/${base}" ]]; then
    continue
  fi
  echo "  ${base} <- ${path}"
  pct exec 300 -- cat "${path}" > "${EXTRA_LIB}/${base}"
done

echo "==> Wrappers ${WRAPPER_DIR}/ffmpeg et ffprobe"
mkdir -p "${WRAPPER_DIR}"

cat > "${WRAPPER_DIR}/ffmpeg" <<EOF
#!/bin/sh
export LD_LIBRARY_PATH="${LD_PATH}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec ${JFF}/ffmpeg "\$@"
EOF

cat > "${WRAPPER_DIR}/ffprobe" <<EOF
#!/bin/sh
export LD_LIBRARY_PATH="${LD_PATH}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec ${JFF}/ffprobe "\$@"
EOF

chmod 755 "${WRAPPER_DIR}/ffmpeg" "${WRAPPER_DIR}/ffprobe"

echo "==> Verification encodeurs"
if "${WRAPPER_DIR}/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q hevc_nvenc; then
  echo "  hevc_nvenc: OK"
else
  echo "  hevc_nvenc: MANQUANT" >&2
  LD_LIBRARY_PATH="${LD_PATH}" ldd "${JFF}/ffmpeg" | grep 'not found' || true
fi

echo "==> Verification filtres"
"${WRAPPER_DIR}/ffmpeg" -hide_banner -filters 2>/dev/null | grep -E 'hqdn3d|vidstab' || echo "  (hqdn3d/vidstab: voir sortie ci-dessus)"

echo "==> OK"
