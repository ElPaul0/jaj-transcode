#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/opt/ffmpeg-btbn"
WRAPPER_DIR="/usr/local/bin"
SERVICE_FILE="/etc/systemd/system/jaj-transcode.service"
URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
WORKDIR="/tmp/ffmpeg-btbn-install"

echo "==> Telechargement BtbN FFmpeg GPL"
rm -rf "${WORKDIR}"
mkdir -p "${WORKDIR}" "${INSTALL_ROOT}"
curl -fsSL -o "${WORKDIR}/ffmpeg.tar.xz" "${URL}"

echo "==> Extraction vers ${INSTALL_ROOT}"
rm -rf "${INSTALL_ROOT:?}"/*
tar -xJf "${WORKDIR}/ffmpeg.tar.xz" -C "${WORKDIR}"
EXTRACTED="$(find "${WORKDIR}" -maxdepth 1 -type d -name 'ffmpeg-*-linux64-gpl*' | head -1)"
if [[ -z "${EXTRACTED}" ]]; then
  echo "ERREUR: repertoire extrait introuvable" >&2
  exit 1
fi
cp -a "${EXTRACTED}/." "${INSTALL_ROOT}/"

FFMPEG="${INSTALL_ROOT}/bin/ffmpeg"
FFPROBE="${INSTALL_ROOT}/bin/ffprobe"
if [[ ! -x "${FFMPEG}" ]]; then
  echo "ERREUR: ${FFMPEG} introuvable" >&2
  exit 1
fi

LIB_DIR=""
if [[ -d "${INSTALL_ROOT}/lib" ]]; then
  LIB_DIR="${INSTALL_ROOT}/lib"
fi

echo "==> Verification hevc_nvenc"
ENC_OUT="$("${FFMPEG}" -hide_banner -encoders 2>&1 || true)"
if ! grep -q hevc_nvenc <<< "${ENC_OUT}"; then
  echo "ERREUR: hevc_nvenc absent du build BtbN" >&2
  grep -i nvenc <<< "${ENC_OUT}" || true
  exit 1
fi
echo "  hevc_nvenc: OK"

echo "==> Verification vidstab"
FLT_OUT="$("${FFMPEG}" -hide_banner -filters 2>&1 || true)"
if ! grep -q vidstab <<< "${FLT_OUT}"; then
  echo "ERREUR: vidstab absent du build BtbN" >&2
  grep -i stab <<< "${FLT_OUT}" || true
  exit 1
fi
echo "  vidstab: OK"

echo "==> Wrappers ${WRAPPER_DIR}"
mkdir -p "${WRAPPER_DIR}"
if [[ -n "${LIB_DIR}" ]]; then
  LD_LINE="export LD_LIBRARY_PATH=\"${LIB_DIR}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\""
else
  LD_LINE=""
fi

cat > "${WRAPPER_DIR}/ffmpeg" <<EOF
#!/bin/sh
${LD_LINE}
exec ${FFMPEG} "\$@"
EOF

cat > "${WRAPPER_DIR}/ffprobe" <<EOF
#!/bin/sh
${LD_LINE}
exec ${FFPROBE} "\$@"
EOF

chmod 755 "${WRAPPER_DIR}/ffmpeg" "${WRAPPER_DIR}/ffprobe"

echo "==> Mise a jour jaj-transcode.service"
if [[ -f "${SERVICE_FILE}" ]]; then
  sed -i 's|^Environment=JAJ_FFMPEG=.*|Environment=JAJ_FFMPEG='"${FFMPEG}"'|' "${SERVICE_FILE}"
  sed -i 's|^Environment=JAJ_FFPROBE=.*|Environment=JAJ_FFPROBE='"${FFPROBE}"'|' "${SERVICE_FILE}"
  if [[ -n "${LIB_DIR}" ]]; then
    if grep -q '^Environment=JAJ_LD_LIBRARY_PATH=' "${SERVICE_FILE}"; then
      sed -i 's|^Environment=JAJ_LD_LIBRARY_PATH=.*|Environment=JAJ_LD_LIBRARY_PATH='"${LIB_DIR}"'|' "${SERVICE_FILE}"
    else
      sed -i '/^Environment=JAJ_FFPROBE=/a Environment=JAJ_LD_LIBRARY_PATH='"${LIB_DIR}" "${SERVICE_FILE}"
    fi
  fi
  systemctl daemon-reload
  systemctl restart jaj-transcode.service || true
fi

echo "==> Verification wrappers"
"${WRAPPER_DIR}/ffmpeg" -hide_banner -encoders 2>&1 | grep hevc_nvenc
"${WRAPPER_DIR}/ffmpeg" -hide_banner -filters 2>&1 | grep vidstab

echo "==> Termine"
