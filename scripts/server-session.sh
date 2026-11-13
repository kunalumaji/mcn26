#!/usr/bin/env bash
# Runs the quicfile servers of an experiment and the CPU load in ONE session.
#
#   setsid -f scripts/server-session.sh <load> <cc> <port>=<opt,opt,...> ...
#
#   load   CPU load for scripts/cpu-load.sh (N or Kx; 0 = none)
#   cc     congestion control (cubic | bbr)
#   each further argument: a server port and its quicfile options, comma-separated,
#          e.g. 4434=-policy:fixed,-threshold:2
#
# Why one session: with Linux's scheduler autogroups (on by default on desktop and
# server Ubuntu, /proc/sys/kernel/sched_autogroup_enabled), CPU time is shared fairly
# between sessions first and only then between the threads inside a session. Busy
# loops started in their own session therefore only make the machine *look* busy:
# the QUIC threads still get a CPU almost whenever they want one. Inside the same
# session every busy loop competes with the QUIC threads one-to-one, so "2x" (two busy
# loops per core) leaves a QUIC thread about a third of a core.
#
# Each server runs in a loop, so killing one (the runner does after a failed
# download) restarts it inside the same session. Logs: /tmp/mcn-qf-<port>.log
# (appended). The session ID is written to /tmp/mcn-session.pid; stop everything with
#   pkill -u "$USER" -s "$(cat /tmp/mcn-session.pid)"
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
load="$1"
cc="$2"
shift 2
echo $$ > /tmp/mcn-session.pid

if [[ "$load" != 0 ]]; then
    scripts/cpu-load.sh "$load" 86400 &
fi
for entry in "$@"; do
    port="${entry%%=*}"
    opts="${entry#*=}"
    (
        while :; do
            # shellcheck disable=SC2086 # options are split on purpose
            build/msquic/bin/Release/quicfile server -dir:data/files -cert:data/cert.pem \
                -key:data/key.pem -port:"$port" -cc:"$cc" ${opts//,/ } -acklog:1 \
                >> "/tmp/mcn-qf-$port.log" 2>&1 < /dev/null
            sleep 1
        done
    ) &
done
wait
