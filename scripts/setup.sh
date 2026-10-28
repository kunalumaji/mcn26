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

# msquic v2.6.3 and CLOG are vendored in the repo. quictls is large and never
# modified, so fetch it at the commit msquic v2.6.3 pins (gitignored).
QUICTLS_DIR="$ROOT/msquic/submodules/quictls"
QUICTLS_COMMIT="ff36838bb69801cad56823159a036977bcbe5c75"
if [[ ! -f "$QUICTLS_DIR/Configure" ]]; then
    echo "==> Fetching quictls @ ${QUICTLS_COMMIT:0:8}"
    rm -rf "$QUICTLS_DIR"
    git init -q "$QUICTLS_DIR"
    git -C "$QUICTLS_DIR" fetch -q --depth 1 https://github.com/quictls/openssl.git "$QUICTLS_COMMIT"
    git -C "$QUICTLS_DIR" checkout -q FETCH_HEAD
fi

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

echo "==> Building quicfile"
cmake -S "$ROOT/apps/quicfile" -B "$ROOT/build/quicfile" -G Ninja \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DMSQUIC_LIB_DIR="$BIN"
cmake --build "$ROOT/build/quicfile" -j "$JOBS"
cp "$ROOT/build/quicfile/quicfile" "$BIN/"

echo "==> Done. Binaries in $BIN"
ls "$BIN"
