#!/usr/bin/env bash
# All experiments of the report, in order, then figures and tables for each.
# About 2-3 hours with the client on ~600 Mbit/s Wi-Fi (runtime scales with link speed).
# Run on the client; see README.md for the setup. Each experiment writes its own
# results/<timestamp>/ folder; their paths are printed at the end.
#
# Check the link first: both machines' interfaces must run at the expected speed
# (cat /sys/class/net/<iface>/speed); a gigabit port at 100 Mbit/s caps everything.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

runs=()
run() {
    local out
    out=$(env "$@" experiments/run_quicfile.sh | tee /dev/stderr | sed -n 's/^Results in //p' | tail -1)
    runs+=("$out")
}

# 1. Downloads: 12 policy variants x 4 files (20 MB-1 GB) x server load 0/2x/4x x 3 repetitions.
run FILES="20MB.bin 100MB.bin 500MB.bin 1GB.bin" SERVER_LOADS="0 2x 4x" REPS=3

# 2. Load sweep: how the threshold and goodput follow the server load.
run FILES=500MB.bin REPS=2 SERVER_LOADS="0 0.25x 0.5x 0.75x 1x 2x 4x" \
    POLICIES="fixed:2 adaptive adaptive:255 adaptive+net+phase+reorder+delay"

# 3. Video streaming QoE: 40 s of video in 2 s segments, bitrate ladder 25-400 Mbit/s.
run WORKLOAD=video SERVER_LOADS="0 2x 4x" REPS=3 \
    POLICIES="fixed:2 default fixed:100 fixed:100+phase litequic adaptive adaptive:255 adaptive+phase+reorder+delay"

for out in "${runs[@]}"; do
    python3 analysis/plot.py "$out"
    python3 analysis/summarize.py "$out" > "$out/tables.md"
done
printf 'Results:\n'
printf '  %s\n' "${runs[@]}"
