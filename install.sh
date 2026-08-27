#!/usr/bin/env bash
# Copyright 2026 Kevin Eales
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    exec sudo -- "$0" "$@"
fi

readonly SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly INSTALL_DIR=/opt/jetson-stats-pitft
readonly SERVICE_USER=${SUDO_USER:-quicksand}
readonly CUDA_DIR=/usr/local/cuda

if [[ ! -e /dev/spidev0.0 ]]; then
    echo "Warning: /dev/spidev0.0 does not exist; configure SPI1 first." >&2
fi

apt-get install -y python3-venv python3-pil python3-numpy python3-spidev python3-libgpiod busybox-static
install -d -m 0755 "${INSTALL_DIR}"
cp -a "${SOURCE_DIR}/jetson_stats_pitft" "${SOURCE_DIR}/pyproject.toml" "${SOURCE_DIR}/README.md" "${SOURCE_DIR}/LICENSE" "${INSTALL_DIR}/"
python3 -m venv --system-site-packages "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --no-deps "${INSTALL_DIR}"

install -m 0755 "${SOURCE_DIR}/scripts/agx-orin-pitft-input-pinmux" /usr/local/sbin/
install -m 0644 "${SOURCE_DIR}/systemd/agx-orin-pitft-input-pinmux.service" /etc/systemd/system/
sed "s/@SERVICE_USER@/${SERVICE_USER}/g" \
    "${SOURCE_DIR}/systemd/jetson-stats-pitft.service.in" \
    >/etc/systemd/system/jetson-stats-pitft.service
chmod 0644 /etc/systemd/system/jetson-stats-pitft.service

tensor_sentinel=0
if [[ -x ${CUDA_DIR}/bin/nvcc && -f ${CUDA_DIR}/extras/CUPTI/samples/pm_sampling/pm_sampling.h ]]; then
    "${CUDA_DIR}/bin/nvcc" -std=c++17 -O2 -arch=sm_87 \
        -I"${CUDA_DIR}/include" \
        -I"${CUDA_DIR}/extras/CUPTI/include" \
        -I"${CUDA_DIR}/extras/CUPTI/samples/common" \
        -I"${CUDA_DIR}/extras/CUPTI/samples/pm_sampling" \
        "${SOURCE_DIR}/tools/jetson-tensor-sentinel.cu" \
        -L"${CUDA_DIR}/extras/CUPTI/lib64" -L"${CUDA_DIR}/lib64" \
        -lcuda -lcupti -o /usr/local/sbin/jetson-tensor-sentinel
    install -m 0644 "${SOURCE_DIR}/systemd/jetson-tensor-sentinel.service" /etc/systemd/system/
    tensor_sentinel=1
else
    echo "Warning: CUDA/CUPTI development files absent; TRT indicator will remain blank." >&2
fi

getent group gpio >/dev/null && usermod -aG gpio "${SERVICE_USER}"
systemctl daemon-reload
systemctl enable agx-orin-pitft-input-pinmux.service jetson-stats-pitft.service
if (( tensor_sentinel )); then
    systemctl enable jetson-tensor-sentinel.service
    systemctl restart jetson-tensor-sentinel.service
fi
systemctl restart agx-orin-pitft-input-pinmux.service
systemctl restart jetson-stats-pitft.service
echo "Installed and started jetson-stats-pitft for ${SERVICE_USER}."
