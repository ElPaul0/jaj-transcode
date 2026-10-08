#!/usr/bin/env bash
set -euo pipefail

JFF="/opt/jellyfin-ffmpeg"
EXTRA_LIB="${JFF}/extra-libs"
mkdir -p "${EXTRA_LIB}"

echo "==> Copie des bibliotheques liees (ldd CT300)"
mapfile -t deps < <(
  pct exec 300 -- bash -lc \
    'export LD_LIBRARY_PATH=/usr/lib/jellyfin-ffmpeg/lib; ldd /usr/lib/jellyfin-ffmpeg/ffmpeg' \
    | awk '/=> \// {print $3}' | sort -u
)

for path in "${deps[@]}"; do
  [[ -z "${path}" ]] && continue
  base="$(basename "${path}")"
  if [[ -f "${EXTRA_LIB}/${base}" ]]; then
    continue
  fi
  echo "  ${base} <- ${path}"
  pct exec 300 -- cat "${path}" > "${EXTRA_LIB}/${base}"
done

LD_PATH="${JFF}/lib:${EXTRA_LIB}"
echo "==> Bibliotheques encore manquantes:"
LD_LIBRARY_PATH="${LD_PATH}" ldd "${JFF}/ffmpeg" | grep 'not found' || echo "  (aucune)"

echo "==> Test hevc_nvenc"
export LD_LIBRARY_PATH="${LD_PATH}"
if "${JFF}/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q hevc_nvenc; then
  echo "  hevc_nvenc: OK"
else
  echo "  hevc_nvenc: ECHEC"
  exit 1
fi

echo "==> Filtres hqdn3d / vidstab"
"${JFF}/ffmpeg" -hide_banner -filters 2>/dev/null | grep -E 'hqdn3d|vidstab' || true
