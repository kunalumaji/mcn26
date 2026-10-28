#!/usr/bin/env bash
# Download files from the remote quicfile server under each ACK policy.
# Run on the client; starts/stops the server over SSH for every policy.
#
#   experiments/run_quicfile.sh                      # defaults below
#   FILES="20MB.bin 100MB.bin" REPS=5 experiments/run_quicfile.sh
#   POLICIES="default fixed:2 litequic" experiments/run_quicfile.sh
#
# Output: results/<timestamp>/{client.log,server.log,meta.txt}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="${SERVER:-kunal@10.21.232.3}"
SERVER_IP="${SERVER_IP:-${SERVER#*@}}"
SERVER_ROOT="${SERVER_ROOT:-Documents/mcn26}"
FILES="${FILES:-20MB.bin}"
POLICIES="${POLICIES:-default fixed:2 fixed:10 fixed:100 litequic}"
REPS="${REPS:-3}"
CC="${CC:-cubic}"
BIN="$ROOT/build/msquic/bin/Release"

remote() { ssh -o BatchMode=yes "$SERVER" "$@"; }

# Both ends must run the same committed code (results/ and data/ may differ).
# ALLOW_DIRTY=1 skips the check, e.g. while testing a local change.
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
    echo "date=$(date -Is) server=$SERVER files=$FILES policies=$POLICIES reps=$REPS cc=$CC"
    echo "client_commit=$CLIENT_COMMIT client_dirty=$CLIENT_DIRTY server_commit=$SERVER_COMMIT server_dirty=$SERVER_DIRTY"
    echo "client_kernel=$(uname -r) server_kernel=$(remote uname -r)"
} > "$OUT/meta.txt"

for spec in $POLICIES; do
    policy="${spec%%:*}"
    threshold=""
    [[ "$spec" == *:* ]] && threshold="-threshold:${spec#*:}"

    remote "pkill -u \$USER -x quicfile; cd $SERVER_ROOT && \
        setsid -f build/msquic/bin/Release/quicfile server -dir:data/files \
        -cert:data/cert.pem -key:data/key.pem -cc:$CC -policy:$policy $threshold -acklog:1 \
        > /tmp/qf-server.log 2>&1 < /dev/null"
    sleep 1

    for file in $FILES; do
        for rep in $(seq 1 "$REPS"); do
            line=$("$BIN/quicfile" client -target:"$SERVER_IP" -file:"$file" -cc:"$CC" \
                -policy:"$policy" $threshold 2>&1 | grep '^RESULT' || echo "RESULT file=$file FAILED")
            echo "spec=$spec rep=$rep $line" | tee -a "$OUT/client.log"
            sleep 0.5
        done
    done

    remote "pkill -u \$USER -x quicfile; sleep 0.3; cat /tmp/qf-server.log" \
        | sed "s/^/spec=$spec /" >> "$OUT/server.log"
done

echo "Results in $OUT"
