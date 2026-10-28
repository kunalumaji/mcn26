#!/usr/bin/env bash
# ACK policy evaluation: download files from the remote quicfile server under
# each ACK policy and server CPU load level, logging CPU on both machines.
# Run on the client; the server is controlled over SSH. See README.md.
#
# Environment (defaults in brackets):
#   FILES          files in data/files on the server  [20MB.bin 100MB.bin 500MB.bin 1GB.bin]
#   POLICIES       policy[:threshold] specs  [default fixed:2 fixed:10 fixed:100 litequic adaptive]
#   SERVER_LOADS   server CPU load levels for scripts/cpu-load.sh (N or Kx; 0 = none)  [0 2x]
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
FILES="${FILES:-20MB.bin 100MB.bin 500MB.bin 1GB.bin}"
POLICIES="${POLICIES:-default fixed:2 fixed:10 fixed:100 litequic adaptive}"
SERVER_LOADS="${SERVER_LOADS:-0 2x}"
CLIENT_LOAD="${CLIENT_LOAD:-0}"
REPS="${REPS:-3}"
CC="${CC:-cubic}"
BASE_PORT=4433
BIN="$ROOT/build/msquic/bin/Release"

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

OUT="$ROOT/results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
{
    echo "date=$(date -Is)"
    echo "server=$SERVER cc=$CC reps=$REPS"
    echo "files=$FILES"
    echo "policies=$POLICIES"
    echo "server_loads=$SERVER_LOADS client_load=$CLIENT_LOAD"
    echo "client_commit=$CLIENT_COMMIT client_dirty=$CLIENT_DIRTY server_commit=$SERVER_COMMIT server_dirty=$SERVER_DIRTY"
    echo "client_host=$(hostname) client_cores=$(nproc) client_kernel=$(uname -r)"
    echo "server_host=$(remote 'echo $(hostname) server_cores=$(nproc) server_kernel=$(uname -r)')"
} > "$OUT/meta.txt"

LOCAL_PIDS=()
cleanup() {
    remote "pkill -u \$USER -f '[m]cn-cpu-burn'; pkill -u \$USER -x quicfile; \
        pkill -u \$USER -f 'cpumon[.]py --out /tmp/mcn-cpu-server'" || true
    pkill -u "$USER" -f '[m]cn-cpu-burn' || true
    for pid in "${LOCAL_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
cleanup

# CPU monitors on both machines for the whole experiment.
remote "cd $SERVER_ROOT && setsid -f python3 scripts/cpumon.py --out /tmp/mcn-cpu-server.csv \
    > /dev/null 2>&1 < /dev/null"
python3 "$ROOT/scripts/cpumon.py" --out "$OUT/cpu_client.csv" & LOCAL_PIDS+=($!)

# One server per policy, each on its own port, so runs can be interleaved.
# Logs are appended so a restarted server keeps its earlier output.
declare -A PORT
start_server() {
    local spec="$1" port="${PORT[$1]}" policy="${1%%:*}" threshold=""
    [[ "$spec" == *:* ]] && threshold="-threshold:${spec#*:}"
    remote "cd $SERVER_ROOT && setsid -f build/msquic/bin/Release/quicfile server \
        -dir:data/files -cert:data/cert.pem -key:data/key.pem -port:$port -cc:$CC \
        -policy:$policy $threshold -acklog:1 >> /tmp/mcn-qf-$port.log 2>&1 < /dev/null"
}
port=$BASE_PORT
for spec in $POLICIES; do
    PORT[$spec]=$port
    remote "rm -f /tmp/mcn-qf-$port.log"
    start_server "$spec"
    port=$((port + 1))
done
sleep 1

if [[ "$CLIENT_LOAD" != 0 ]]; then
    "$ROOT/scripts/cpu-load.sh" "$CLIENT_LOAD" 86400 & LOCAL_PIDS+=($!)
fi

read -r -a SPECS <<< "$POLICIES"
for load in $SERVER_LOADS; do
    if [[ "$load" != 0 ]]; then
        remote "cd $SERVER_ROOT && setsid -f scripts/cpu-load.sh $load 86400 > /dev/null 2>&1 < /dev/null"
        sleep 2
    fi
    for rep in $(seq 1 "$REPS"); do
        for file in $FILES; do
            # Rotate the policy order every repetition to spread time-varying effects.
            for i in "${!SPECS[@]}"; do
                spec="${SPECS[$(( (i + rep - 1) % ${#SPECS[@]} ))]}"
                tag="L${load}_${spec/:/-}_${file%.bin}_r${rep}"
                start=$(date +%s.%N)
                line=$(timeout 900 "$BIN/quicfile" client -target:"$SERVER_IP" \
                    -port:"${PORT[$spec]}" -file:"$file" -tag:"$tag" -cc:"$CC" 2>&1 \
                    | grep '^RESULT' || echo "RESULT tag=$tag file=$file FAILED=1")
                end=$(date +%s.%N)
                echo "load=$load spec=$spec rep=$rep start_ts=$start end_ts=$end $line" \
                    | tee -a "$OUT/client.log" | cut -c1-200
                # A failed download may leave its server unusable (seen once: a
                # server stopped completing handshakes). Restart it so later
                # runs of this policy are not lost, and record the restart.
                if [[ "$line" == *FAILED* ]]; then
                    remote "pkill -u \$USER -f '[q]uicfile server .*-port:${PORT[$spec]} '" || true
                    sleep 1
                    start_server "$spec"
                    echo "$(date -Is) restarted server spec=$spec after tag=$tag" \
                        | tee -a "$OUT/restarts.log"
                fi
                sleep 1
            done
        done
    done
    if [[ "$load" != 0 ]]; then
        remote "pkill -u \$USER -f '[m]cn-cpu-burn'" || true
        sleep 2
    fi
done

# Collect server logs and CPU samples.
remote "pkill -u \$USER -x quicfile; pkill -u \$USER -f 'cpumon[.]py --out /tmp/mcn-cpu-server'; sleep 1"
for spec in $POLICIES; do
    remote "cat /tmp/mcn-qf-${PORT[$spec]}.log" | sed "s/^/spec=$spec /" >> "$OUT/server.log"
done
scp -q "$SERVER:/tmp/mcn-cpu-server.csv" "$OUT/cpu_server.csv"

python3 "$ROOT/analysis/parse_results.py" "$OUT"
echo "Results in $OUT"
