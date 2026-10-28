#!/usr/bin/env bash
# Generate CPU load with busy-loop processes (no stress-ng / sudo needed).
#
#   scripts/cpu-load.sh <N|Kx> <seconds>
#     N   number of busy-loop processes
#     Kx  K x number of cores (e.g. 1x saturates every core, 2x oversubscribes 2:1)
#
# Stop early with:  pkill -u "$USER" -f "[m]cn-cpu-burn"
set -euo pipefail

count="${1:?usage: cpu-load.sh <N|Kx> <seconds>}"
seconds="${2:?usage: cpu-load.sh <N|Kx> <seconds>}"
if [[ "$count" == *x ]]; then
    count=$(awk -v k="${count%x}" -v c="$(nproc)" 'BEGIN { printf "%d", k * c + 0.5 }')
fi

for _ in $(seq 1 "$count"); do
    timeout "$seconds" sh -c ': mcn-cpu-burn; while :; do :; done' &
done
wait
