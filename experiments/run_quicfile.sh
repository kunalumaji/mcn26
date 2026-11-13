#!/usr/bin/env bash
# ACK policy evaluation: file downloads (or video sessions) from the remote quicfile
# server under each ACK policy and server CPU load level, logging CPU on both
# machines. Run on the client; the server is controlled over SSH. See README.md.
#
# Environment (defaults in brackets):
#   WORKLOAD       files: download each of FILES | video: one video session per run  [files]
#   FILES          files in data/files on the server  [20MB.bin 100MB.bin 500MB.bin 1GB.bin]
#   VIDEO_OPTS     quicfile client options for WORKLOAD=video
#                  [-ladder:25,50,100,200,300,400 -segments:20 -segdur:2 -bufmax:10]
#   POLICIES       policy specs: <base>[:N][+modifier...]
#                  base: default | fixed:N (threshold N) | litequic | adaptive[:N]
#                  (CPU only; N = highest threshold, default 100, at most 255)
#                  modifiers: +net (adaptive: network ceiling), +phase (slow start /
#                  recovery aware), +reorder (reordering threshold 3), +delay
#                  (max ACK delay SRTT/4); e.g. adaptive+phase+reorder+delay
#                  [default fixed:2 fixed:100 fixed:100+phase litequic adaptive adaptive:255
#                   adaptive+phase adaptive+reorder adaptive+delay adaptive+phase+reorder+delay
#                   adaptive+net+phase+reorder+delay]
#   SERVER_LOADS   server CPU load levels for scripts/cpu-load.sh (N or Kx; 0 = none)  [0 2x 4x]
#   LOAD_SCOPE     shared: the busy loops run in the servers' session and compete with the
#                  QUIC threads (see scripts/server-session.sh) | separate: in their own
#                  session, which only makes the machine look busy (runs before Nov 14)  [shared]
#   CLIENT_LOAD    client CPU load during all runs (same format)  [0]
#   REPS           repetitions per (load, file, policy)  [3]
#   CC             cubic | bbr  [cubic]
#   SERVER         SSH target  [kunal@10.21.232.3]
#   SERVER_ROOT    repo path on the server, relative to its home  [Documents/mcn26]
#   ALLOW_DIRTY=1  skip the same-commit check
#
# Output: results/<timestamp>/ (see analysis/parse_results.py for the CSVs)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="${SERVER:-kunal@10.21.232.3}"
SERVER_IP="${SERVER_IP:-${SERVER#*@}}"
SERVER_ROOT="${SERVER_ROOT:-Documents/mcn26}"
WORKLOAD="${WORKLOAD:-files}"
FILES="${FILES:-20MB.bin 100MB.bin 500MB.bin 1GB.bin}"
VIDEO_OPTS="${VIDEO_OPTS:--ladder:25,50,100,200,300,400 -segments:20 -segdur:2 -bufmax:10}"
POLICIES="${POLICIES:-default fixed:2 fixed:100 fixed:100+phase litequic adaptive adaptive:255 adaptive+phase adaptive+reorder adaptive+delay adaptive+phase+reorder+delay adaptive+net+phase+reorder+delay}"
SERVER_LOADS="${SERVER_LOADS:-0 2x 4x}"
LOAD_SCOPE="${LOAD_SCOPE:-shared}"
CLIENT_LOAD="${CLIENT_LOAD:-0}"
REPS="${REPS:-3}"
CC="${CC:-cubic}"
BASE_PORT=4433
BIN="$ROOT/build/msquic/bin/Release"

case "$WORKLOAD" in
    files) ITEMS="$FILES"; KIND=RESULT ;;
    video) ITEMS="video"; KIND=VIDEO ;;
    *) echo "WORKLOAD must be files or video" >&2; exit 1 ;;
esac
[[ "$LOAD_SCOPE" == shared || "$LOAD_SCOPE" == separate ]] ||
    { echo "LOAD_SCOPE must be shared or separate" >&2; exit 1; }

remote() { ssh -o BatchMode=yes "$SERVER" "$@"; }

# Both ends must run the same committed code (results/ and data/ may differ).
dirty_files() { git status --porcelain -- . ':!results' ':!data' | wc -l; }
CLIENT_COMMIT=$(git -C "$ROOT" rev-parse --short HEAD)
CLIENT_DIRTY=$(cd "$ROOT" && dirty_files)
read -r SERVER_COMMIT SERVER_DIRTY < <(remote "cd $SERVER_ROOT && \
    echo \$(git rev-parse --short HEAD) \$(git status --porcelain -- . ':!results' ':!data' | wc -l)")
if [[ "${ALLOW_DIRTY:-0}" != "1" ]] &&
   [[ "$CLIENT_COMMIT" != "$SERVER_COMMIT" || "$CLIENT_DIRTY" != 0 || "$SERVER_DIRTY" != 0 ]]; then
    echo "Commit mismatch or uncommitted changes:" >&2
    echo "  client $CLIENT_COMMIT ($CLIENT_DIRTY changed files)" >&2
    echo "  server $SERVER_COMMIT ($SERVER_DIRTY changed files)" >&2
    echo "Commit/push, then 'git pull && scripts/setup.sh' on the server (or ALLOW_DIRTY=1)." >&2
    exit 1
fi

# Policy spec -> quicfile options: <base>[:N][+modifier...], e.g.
# fixed:100+phase -> -policy:fixed -threshold:100 -ackphase:1; adaptive:255 -> -ackmax:255.
spec_options() {
    local spec="$1" base="${1%%+*}" opts modifier
    opts="-policy:${base%%:*}"
    if [[ "$base" == *:* ]]; then
        case "${base%%:*}" in
            fixed) opts+=" -threshold:${base#*:}" ;;
            adaptive) opts+=" -ackmax:${base#*:}" ;;
            *) echo "':N' is only defined for fixed and adaptive ('$spec')" >&2; return 1 ;;
        esac
    fi
    if [[ "$spec" == *+* ]]; then
        local -a modifiers
        IFS=+ read -r -a modifiers <<< "${spec#*+}"
        for modifier in "${modifiers[@]}"; do
            case "$modifier" in
                net) opts+=" -acknet:1" ;;
                phase) opts+=" -ackphase:1" ;;
                reorder) opts+=" -ackreorder:3" ;;
                delay) opts+=" -ackdelay:1" ;;
                *) echo "Unknown modifier '+$modifier' in policy '$spec'" >&2; return 1 ;;
            esac
        done
    fi
    echo "$opts"
}
for spec in $POLICIES; do
    spec_options "$spec" > /dev/null || exit 1
done

OUT="$ROOT/results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
{
    echo "date=$(date -Is)"
    echo "server=$SERVER cc=$CC reps=$REPS workload=$WORKLOAD"
    if [[ "$WORKLOAD" == video ]]; then echo "video_opts=$VIDEO_OPTS"; else echo "files=$FILES"; fi
    echo "policies=$POLICIES"
    echo "server_loads=$SERVER_LOADS load_scope=$LOAD_SCOPE client_load=$CLIENT_LOAD"
    echo "client_commit=$CLIENT_COMMIT client_dirty=$CLIENT_DIRTY server_commit=$SERVER_COMMIT server_dirty=$SERVER_DIRTY"
    echo "client_host=$(hostname) client_cores=$(nproc) client_kernel=$(uname -r)"
    echo "server_host=$(remote 'echo $(hostname) server_cores=$(nproc) server_kernel=$(uname -r)')"
} > "$OUT/meta.txt"

# One server per policy, each on its own port, so runs can be interleaved. All
# servers and the CPU load of the current load level run in one remote session
# (scripts/server-session.sh); it is replaced at every load level.
declare -A PORT
port=$BASE_PORT
for spec in $POLICIES; do
    PORT[$spec]=$port
    port=$((port + 1))
done

stop_session() {
    remote "if [[ -f /tmp/mcn-session.pid ]]; then pkill -u \$USER -s \$(cat /tmp/mcn-session.pid); fi; \
        pkill -u \$USER -f '[m]cn-cpu-burn'; sleep 1; pkill -u \$USER -x quicfile; \
        rm -f /tmp/mcn-session.pid; true"
}
start_session() {
    local load="$1" args="" spec session_load="$1"
    stop_session
    for spec in $POLICIES; do
        args+=" ${PORT[$spec]}=$(spec_options "$spec" | tr ' ' ',')"
    done
    [[ "$LOAD_SCOPE" == separate ]] && session_load=0
    remote "cd $SERVER_ROOT && setsid -f scripts/server-session.sh $session_load $CC$args \
        > /dev/null 2>&1 < /dev/null"
    if [[ "$LOAD_SCOPE" == separate && "$load" != 0 ]]; then
        remote "cd $SERVER_ROOT && setsid -f scripts/cpu-load.sh $load 86400 > /dev/null 2>&1 < /dev/null"
    fi
    sleep 3
}

LOCAL_PIDS=()
cleanup() {
    stop_session || true
    remote "pkill -u \$USER -f 'cpumon[.]py --out /tmp/mcn-cpu-server'" || true
    pkill -u "$USER" -f '[m]cn-cpu-burn' || true
    for pid in "${LOCAL_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
cleanup
for spec in $POLICIES; do
    remote "rm -f /tmp/mcn-qf-${PORT[$spec]}.log"
done

# CPU monitors on both machines for the whole experiment.
remote "cd $SERVER_ROOT && setsid -f python3 scripts/cpumon.py --out /tmp/mcn-cpu-server.csv \
    > /dev/null 2>&1 < /dev/null"
python3 "$ROOT/scripts/cpumon.py" --out "$OUT/cpu_client.csv" & LOCAL_PIDS+=($!)

if [[ "$CLIENT_LOAD" != 0 ]]; then
    "$ROOT/scripts/cpu-load.sh" "$CLIENT_LOAD" 86400 & LOCAL_PIDS+=($!)
fi

read -r -a SPECS <<< "$POLICIES"
for load in $SERVER_LOADS; do
    start_session "$load"
    for rep in $(seq 1 "$REPS"); do
        for item in $ITEMS; do
            # Rotate the policy order every repetition to spread time-varying effects.
            for i in "${!SPECS[@]}"; do
                spec="${SPECS[$(( (i + rep - 1) % ${#SPECS[@]} ))]}"
                tag="L${load}_${spec/:/-}_${item%.bin}_r${rep}"
                if [[ "$WORKLOAD" == video ]]; then
                    # shellcheck disable=SC2206 # options are split on purpose
                    what=(-video:1 $VIDEO_OPTS)
                else
                    what=(-file:"$item")
                fi
                start=$(date +%s.%N)
                output=$(timeout 900 "$BIN/quicfile" client -target:"$SERVER_IP" \
                    -port:"${PORT[$spec]}" "${what[@]}" -tag:"$tag" -cc:"$CC" \
                    -acklog:1 2>&1) || true
                end=$(date +%s.%N)
                prefix="load=$load spec=$spec rep=$rep"
                line=$(grep "^$KIND" <<< "$output" || echo "$KIND tag=$tag file=$item FAILED=1")
                # Client-side ACK-cost statistics ([mcn-stats]), keys prefixed with "cm_".
                cstats=$(grep -m1 '^\[mcn-stats\]' <<< "$output" \
                    | sed -E 's/^\[mcn-stats\] conn=[^ ]+ //; s/(^| )([a-z_]+)=/\1cm_\2=/g' || true)
                line="$line $cstats"
                # Throughput every 100 ms (downloads) and per-segment records (video).
                grep -E '^(TPUT|SEG) ' <<< "$output" | sed "s/^/$prefix /" \
                    >> "$OUT/$([[ $WORKLOAD == video ]] && echo segments || echo tput).log" || true
                if [[ "$line" == *FAILED* ]]; then
                    # Keep the client's own messages (e.g. shutdown status) for diagnosis.
                    { echo "== $(date -Is) tag=$tag"; echo "$output" | grep -vE '^(TPUT|SEG|\[mcn\]) ' || true; } \
                        >> "$OUT/failures.log"
                fi
                echo "$prefix start_ts=$start end_ts=$end $line" \
                    | tee -a "$OUT/client.log" | cut -c1-200
                # A failed download may leave its server unusable (seen once: a
                # server stopped completing handshakes). Kill it; its loop in the
                # session restarts it. Record the restart.
                if [[ "$line" == *FAILED* ]]; then
                    remote "pkill -KILL -u \$USER -f '[q]uicfile server .*-port:${PORT[$spec]} '" || true
                    sleep 3
                    echo "$(date -Is) restarted server spec=$spec after tag=$tag" \
                        | tee -a "$OUT/restarts.log"
                fi
                sleep 1
            done
        done
    done
done

# Collect server logs and CPU samples.
stop_session
remote "pkill -u \$USER -f 'cpumon[.]py --out /tmp/mcn-cpu-server'; sleep 1"
for spec in $POLICIES; do
    remote "cat /tmp/mcn-qf-${PORT[$spec]}.log" | sed "s/^/spec=$spec /" >> "$OUT/server.log"
done
scp -q "$SERVER:/tmp/mcn-cpu-server.csv" "$OUT/cpu_server.csv"

python3 "$ROOT/analysis/parse_results.py" "$OUT"
echo "Results in $OUT"
