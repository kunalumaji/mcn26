#!/usr/bin/env bash
# Create the files served by quicfile, plus a self-signed TLS certificate.
#
#   scripts/make-testdata.sh            # into data/
#   DATA_DIR=/path scripts/make-testdata.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$ROOT/data}"
SIZES_MB=(20 100 500 1024)

mkdir -p "$DATA_DIR/files"

if [[ ! -f "$DATA_DIR/cert.pem" ]]; then
    echo "==> Generating self-signed certificate"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
        -keyout "$DATA_DIR/key.pem" -out "$DATA_DIR/cert.pem" \
        -days 3650 -subj "/CN=mcn26" 2>/dev/null
fi

# Random content, so nothing along the path can compress it.
for mb in "${SIZES_MB[@]}"; do
    name=$([[ $mb -ge 1024 ]] && echo "$((mb / 1024))GB" || echo "${mb}MB")
    f="$DATA_DIR/files/$name.bin"
    if [[ ! -f "$f" || $(stat -c %s "$f") -ne $((mb * 1024 * 1024)) ]]; then
        echo "==> Creating $f"
        head -c "$((mb * 1024 * 1024))" /dev/urandom > "$f"
    fi
done

(cd "$DATA_DIR/files" && sha256sum *.bin > SHA256SUMS)
ls -l "$DATA_DIR/files"
