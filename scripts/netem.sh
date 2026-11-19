#!/usr/bin/env bash
# Emulate a network path on this machine's egress with tc netem (run on the
# data sender, i.e. the quicfile server). Needs sudo for tc, e.g. a sudoers rule
#   <user> ALL=(root) NOPASSWD: /usr/sbin/tc
#
#   scripts/netem.sh set <rate|none> <delay> <loss%> [iface]
#       rate:  tc rate (10mbit, 1gbit, ...) or "none" for no rate limit
#       delay: added to the RTT (only this direction is delayed): 20ms -> RTT ~ 20 ms + base
#       loss:  random loss in percent (0, 0.5, 1, ...)
#   scripts/netem.sh profile <rate:delay:loss|none> [iface]   # same, one argument
#   scripts/netem.sh clear [iface]
#   scripts/netem.sh show [iface]
#
# iface defaults to $NETEM_IFACE, else the interface of the route to
# $NETEM_PEER (the client's IP), else the default route's interface.
set -euo pipefail

TC="${TC:-sudo -n /usr/sbin/tc}"

iface() {
    if [[ -n "${1:-}" ]]; then echo "$1"; return; fi
    if [[ -n "${NETEM_IFACE:-}" ]]; then echo "$NETEM_IFACE"; return; fi
    local dst="${NETEM_PEER:-8.8.8.8}"
    ip -o route get "$dst" | sed -n 's/.* dev \([^ ]*\).*/\1/p'
}

# Layout with a rate limit:  root tbf (rate)  ->  child netem (delay, loss).
# msquic sends UDP GSO batches (up to ~43 packets per skb). netem alone counts
# a batch as one packet in its limit (so "1000 packets" can be 64 MB of queue)
# and drops whole batches. tbf splits batches larger than its burst into real
# packets before handing them to netem, so limit and loss apply per packet.
#
# Sizes (packets of 1500 B): netem limit = packets held in the delay line
# (rate x delay) + a bottleneck queue of 2 x BDP (BDP = rate x RTT, RTT ~ delay),
# at least 100. tbf burst: 10 packets, so any GSO batch above 10 packets is split.
qdisc_sizes() {
    local rate="$1" delay="$2"
    awk -v r="$rate" -v d="$delay" 'BEGIN {
        bps = r + 0
        if (r ~ /gbit$/) bps *= 1e9; else if (r ~ /mbit$/) bps *= 1e6; else if (r ~ /kbit$/) bps *= 1e3
        ms = d + 0; if (d ~ /us$/) ms /= 1000; else if (d ~ /s$/ && d !~ /ms$/) ms *= 1000
        if (ms < 1) ms = 1
        delay_pkts = bps * ms / 1000 / 8 / 1500
        queue_pkts = 2 * bps * (ms / 1000) / 8 / 1500
        if (queue_pkts < 100) queue_pkts = 100
        burst = 15140
        printf "%d %d %d\n", delay_pkts + queue_pkts + 1, burst, queue_pkts * 1500
    }'
}

set_netem() {
    local rate="$1" delay="$2" loss="$3" dev
    dev="$(iface "${4:-}")"
    $TC qdisc del dev "$dev" root 2>/dev/null || true
    if [[ "$rate" == none ]]; then
        $TC qdisc add dev "$dev" root handle 1: netem delay "$delay" loss "${loss}%" limit 10000
        echo "netem on $dev: delay $delay loss ${loss}% (no rate limit)"
        return
    fi
    local limit burst tbf_limit
    read -r limit burst tbf_limit < <(qdisc_sizes "$rate" "$delay")
    $TC qdisc add dev "$dev" root handle 1: tbf rate "$rate" burst "$burst" limit "$tbf_limit"
    $TC qdisc add dev "$dev" parent 1:1 handle 10: netem delay "$delay" loss "${loss}%" limit "$limit"
    echo "netem on $dev: rate $rate (tbf burst $burst) delay $delay loss ${loss}% limit $limit pkts"
}

cmd="${1:-}"
case "$cmd" in
    set)
        [[ $# -ge 4 ]] || { echo "usage: $0 set <rate|none> <delay> <loss%> [iface]" >&2; exit 1; }
        set_netem "$2" "$3" "$4" "${5:-}"
        ;;
    profile)
        [[ $# -ge 2 ]] || { echo "usage: $0 profile <rate:delay:loss|none> [iface]" >&2; exit 1; }
        if [[ "$2" == none ]]; then
            "$0" clear "${3:-}"
        else
            IFS=: read -r rate delay loss <<< "$2"
            set_netem "$rate" "${delay:-0ms}" "${loss:-0}" "${3:-}"
        fi
        ;;
    clear)
        dev="$(iface "${2:-}")"
        $TC qdisc del dev "$dev" root 2>/dev/null || true
        echo "netem cleared on $dev"
        ;;
    show)
        dev="$(iface "${2:-}")"
        /usr/sbin/tc qdisc show dev "$dev"
        ;;
    *)
        sed -n '2,16p' "$0" >&2
        exit 1
        ;;
esac
