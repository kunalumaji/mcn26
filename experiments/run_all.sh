#!/usr/bin/env bash
# All experiments of the report, in order, then figures and tables for each.
# Experiments 1-3: about 2-3 hours with the client on ~600 Mbit/s Wi-Fi (runtime scales
# with link speed). Experiments 4-5 (lossy path): about 1.5 hours.
# Run on the client; see README.md for the setup. Each experiment writes its own
# results/<timestamp>/ folder; their paths are printed at the end.
#
# Check the link first: both machines' interfaces must run at the expected speed
# (cat /sys/class/net/<iface>/speed); a gigabit port at 100 Mbit/s caps everything.
# Do not edit tracked files while it runs: every experiment checks that both machines
# are on the same clean commit.
#
#   EXPERIMENTS   which experiments to run  [1 2 3 4 5]
#   NETEM_SERVER  server of experiments 4-5: needs passwordless sudo for /usr/sbin/tc
#                 (scripts/netem.sh)  [kunal@10.21.232.3]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
EXPERIMENTS="${EXPERIMENTS:-1 2 3 4 5}"
NETEM_SERVER="${NETEM_SERVER:-kunal@10.21.232.3}"
# Emulated path of experiments 4-5: 100 Mbit/s bottleneck, +10 ms, 0% and 0.1% random loss.
LOSS_PATH="100mbit:10ms:0 100mbit:10ms:0.1"

runs=()
loss_runs=()
run() {
    local log out
    log=$(mktemp)
    env "$@" experiments/run_quicfile.sh 2>&1 | tee "$log"
    out=$(sed -n 's/^Results in //p' "$log" | tail -1)
    rm -f "$log"
    [[ -n "$out" ]] || { echo "experiment did not finish" >&2; exit 1; }
    runs+=("$out")
}

for experiment in $EXPERIMENTS; do
    case "$experiment" in
        # 1. Downloads: 12 policy variants x 4 files (20 MB-1 GB) x server load 0/2x/4x x 3 reps.
        1) run FILES="20MB.bin 100MB.bin 500MB.bin 1GB.bin" SERVER_LOADS="0 2x 4x" REPS=3 ;;
        # 2. Load sweep: how the threshold and goodput follow the server load.
        2) run FILES=500MB.bin REPS=2 SERVER_LOADS="0 0.25x 0.5x 0.75x 1x 2x 4x" \
               POLICIES="fixed:2 adaptive adaptive:255 adaptive+net+phase+reorder+delay" ;;
        # 3. Video streaming QoE: 40 s of video in 2 s segments, bitrate ladder 25-400 Mbit/s.
        3) run WORKLOAD=video SERVER_LOADS="0 2x 4x" REPS=3 \
               POLICIES="fixed:2 default fixed:100 fixed:100+phase litequic adaptive adaptive:255 adaptive+phase+reorder+delay" ;;
        # 4. Loss sensitivity of a fixed threshold: 100 MB over the emulated path, no CPU load.
        4) run SERVER="$NETEM_SERVER" FILES=100MB.bin REPS=3 SERVER_LOADS=0 \
               NETEM_PROFILES="$LOSS_PATH" \
               POLICIES="fixed:2 fixed:4 fixed:8 fixed:16 fixed:32 fixed:64 fixed:100"
           loss_runs+=("${runs[-1]}") ;;
        # 5. Policies on the lossy path, with server CPU load (the threshold the CPU asks for
        #    meets a path that needs timely ACKs).
        5) run SERVER="$NETEM_SERVER" FILES=100MB.bin REPS=3 SERVER_LOADS="0 2x 4x" \
               NETEM_PROFILES="$LOSS_PATH" \
               POLICIES="fixed:2 fixed:100 fixed:100+phase litequic adaptive adaptive+net adaptive+phase+reorder+delay adaptive+net+phase+reorder+delay"
           loss_runs+=("${runs[-1]}") ;;
        *) echo "unknown experiment $experiment" >&2; exit 1 ;;
    esac
done

for out in "${runs[@]}"; do
    if [[ " ${loss_runs[*]} " == *" $out "* ]]; then
        python3 analysis/loss.py "$out" > "$out/tables.md"
    else
        python3 analysis/plot.py "$out"
        python3 analysis/summarize.py "$out" > "$out/tables.md"
    fi
done
# Figure of experiments 4-5 together (as in the report).
if [[ ${#loss_runs[@]} == 2 ]]; then
    python3 analysis/loss.py --fig "${loss_runs[1]}/figures/loss.pdf" "${loss_runs[@]}" > /dev/null
fi
printf 'Results:\n'
printf '  %s\n' "${runs[@]}"
