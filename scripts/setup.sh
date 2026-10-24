#!/usr/bin/env bash
# Build msquic (pinned submodule) plus secnetperf and sample tools on Linux.
#
#   scripts/setup.sh            # Release build into build/msquic
#   BUILD_TYPE=Debug scripts/setup.sh
#   INSTALL_DEPS=1 scripts/setup.sh   # also apt-install build dependencies
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_TYPE="${BUILD_TYPE:-Release}"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/msquic}"
JOBS="${JOBS:-$(nproc)}"

if [[ "${INSTALL_DEPS:-0}" == "1" ]]; then
    sudo apt-get update
    sudo apt-get install -y build-essential cmake ninja-build libssl-dev libnuma-dev \
        iproute2 linux-tools-common python3-pip
fi

echo "==> Initialising submodules"
git -C "$ROOT" submodule update --init msquic
# Only the nested submodules needed for a Linux build (skip openssl, googletest, xdp-for-windows).
git -C "$ROOT/msquic" submodule update --init --depth 1 submodules/clog submodules/quictls

echo "==> Configuring msquic ($BUILD_TYPE) in $BUILD_DIR"
cmake -S "$ROOT/msquic" -B "$BUILD_DIR" -G Ninja \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DQUIC_TLS_LIB=quictls \
    -DQUIC_USE_SYSTEM_LIBCRYPTO=ON \
    -DQUIC_BUILD_TOOLS=ON \
    -DQUIC_BUILD_PERF=ON \
    -DQUIC_BUILD_TEST=OFF

echo "==> Building with $JOBS jobs"
cmake --build "$BUILD_DIR" -j "$JOBS"

BIN="$BUILD_DIR/bin/$BUILD_TYPE"
echo "==> Done. Binaries in $BIN"
ls "$BIN"
