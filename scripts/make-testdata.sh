#!/usr/bin/env bash
# Create the files served by quicfile (downloads and video segments), plus a
# self-signed TLS certificate.
#
#   scripts/make-testdata.sh            # into data/
#   DATA_DIR=/path scripts/make-testdata.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$ROOT/data}"
SIZES_MB=(20 100 500 1024)
# Video segments: one 2-second segment file per bitrate (Mbit/s), video_<rate>M.bin;
# the client requests the same file for every segment of that bitrate.
VIDEO_RATES_MBPS=(10 25 50 100 150 200 300 400)
VIDEO_SEGMENT_S=2

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

for rate in "${VIDEO_RATES_MBPS[@]}"; do
    f="$DATA_DIR/files/video_${rate}M.bin"
    bytes=$((rate * 1000000 * VIDEO_SEGMENT_S / 8))
    if [[ ! -f "$f" || $(stat -c %s "$f") -ne $bytes ]]; then
        echo "==> Creating $f"
        head -c "$bytes" /dev/urandom > "$f"
    fi
done

(cd "$DATA_DIR/files" && sha256sum *.bin > SHA256SUMS)
ls -l "$DATA_DIR/files"
