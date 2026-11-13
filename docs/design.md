# Design Notes

## Problem

At high rates, QUIC's user-space ACK generation and processing take a large share
of CPU. Lowering ACK frequency saves CPU but starves congestion control of
feedback, which hurts throughput with shallow buffers and on lossy paths
(see LiteQUIC; full write-up in [report.md](report.md)).

## Candidate directions (initial brainstorm)

1. **Event-driven feedback.** ACK sparsely by default. The sender uses
   `IMMEDIATE_ACK` or short `ACK_FREQUENCY` bursts to request dense feedback only
   when congestion control or loss recovery needs it (loss, slow start, end of a
   cwnd round, BBR probes).
2. **Multi-signal adaptive threshold.** Set the threshold from network state (cwnd
   in packets, RTT, loss, reordering) together with CPU pressure, with a lower
   bound such as "at least k ACKs per cwnd".
3. **Receiver-driven, CPU-aware ACKing.** The receiver picks its own threshold
   based on its CPU load, within an upper bound set by the sender.
4. **Server-wide ACK budget.** Divide a total ACK-processing budget across
   connections.

## Implemented policies

All policies run at the **data sender** (the server, for downloads) and reach the
receiver through the `ACK_FREQUENCY` frame (ack-eliciting threshold `T`). They are
selected per process with `quicfile -policy:...` (environment variable
`MCN_ACK_POLICY` inside msquic). Code: `msquic/src/core/connection.c`, marked `MCN26`.

| Policy | Rule |
|---|---|
| `default` | Unchanged msquic: raises `T` to its send batch size + 1 when scheduling-limited, resets to 2 on persistent congestion |
| `fixed:N` | `T = N` for the whole connection (`fixed:2` is the RFC 9000 behaviour) |
| `litequic` | LiteQUIC (MM'24): once per SRTT, `ACK_avg` = ACK frames per receive flush. `ACK_avg ≥ 2` → `T = T × ACK_avg`, else `T = max(T/2, 2)` |
| `adaptive[:N]` | **Ours, CPU only**: `T` scales with CPU pressure up to a ceiling `N` (default 100, at most 255) |
| `adaptive` + `-acknet:1` | **Ours, CPU + network**: the maximum comes from cwnd, queueing delay and loss |

### `adaptive`

The full algorithm is in the README section
[How `adaptive` calculates `T`](../README.md#how-adaptive-calculates-t).
In short, every `max(SRTT, 20 ms)`:

`T = 2 + (C − 2) · f(P)`, increases capped at 2× per window, where

- `P = max(QUIC thread pressure, whole-machine CPU)`, smoothed;
  `f(P)` ramps 0 → 1 between 50% and 90%;
- `C = 100` (CPU only), or with `-acknet:1`: `C = cwnd_packets / 4`, halved while the
  path is queueing (SRTT > 2× min RTT and > min RTT + 5 ms), within `[2, 100]`;
  a loss in the window also caps `T` at half its current value.

Design rationale and history:
- **Two CPU signals.** The first version used only the QUIC thread's pressure
  (`(on-CPU + runqueue wait) / wall time`). In our runs up to Nov 12 it stayed at 20–35% on a
  "fully loaded" server, so the policy never reacted. The reason (found Nov 14) was the load
  generator, not the signal: the busy loops ran in their own session, and Linux's scheduler
  autogroups share CPU fairly between sessions before threads, so the QUIC thread was still
  scheduled almost immediately. Whole-machine utilisation was added as a second signal (when
  the server is busy, ACK work is taken from other work); both are kept. Since Nov 14 the
  load runs in the servers' session (`scripts/server-session.sh`) and does compete with
  the QUIC threads.
- **Network as an option.** `-acknet:1` separates the contribution of network
  awareness: with it off, `adaptive` is a pure CPU-driven policy that can be compared
  with `fixed:100` (same maximum) and `litequic`.
- **`cwnd / 4` ceiling.** Keeps at least 4 ACKs per congestion window, so congestion
  control and loss detection keep getting feedback; LiteQUIC has no such bound.
- **Queueing condition.** Requires both 2× inflation *and* 5 ms of extra delay: on
  LAN/loopback the minimum RTT is tens of microseconds, so the ratio alone is
  triggered by any jitter.

## The threshold ceiling

Under full load `adaptive` sits at its ceiling, so the ceiling decides how far ACKs are
reduced. Why there must be one:

- msquic stores the threshold in 8 bits (`PeerPacketTolerance`, `PacketTolerance`), so 255
  is the maximum on both sides.
- With `T` above the packets in flight the receiver can only ACK on its max-ACK-delay timer
  (25 ms), so a window-limited sender waits up to 25 ms per round trip.
- Loss detection (RFC 9002) and Cubic's window growth and pacing run on ACK arrivals; sparse
  ACKs delay recovery and make sending burstier.
- Custura et al. keep several ACKs per RTT; `+net` keeps at least 4 per congestion window.

The default 100 (`MCN_ACK_MAX`) is LiteQUIC's largest tested value, not a tuned optimum.
`adaptive:N` (runner) / `-ackmax:N` (quicfile) sets it; the evaluation compares 100 with 255
and with the window-based ceiling of `+net`.

## Modifiers: `+phase`, `+reorder`, `+delay`

Chosen from a brainstorm of further signals (see "Deferred ideas" below) because each
addresses an observed problem and maps onto an `ACK_FREQUENCY` field msquic's receiver
already honours. They are applied in `QuicConnMcnAckSetThreshold` to every threshold the
`fixed`, `litequic` and `adaptive` policies choose, so each can be switched on separately
(ablation). All three fields are sent together in one `ACK_FREQUENCY` frame
(`QuicConnMcnAckUpdatePeer`); `fixed` re-evaluates every `max(SRTT, 20 ms)` when `+phase` or
`+delay` is on.

| Modifier | Rule | Observation that motivated it |
|---|---|---|
| `+phase` (`MCN_ACK_PHASE`) | Cubic slow start (`cwnd < ssthresh`): `T ≤ max(2, cwnd_packets / 4)`; recovery: `T = 2` | `fixed:100` made 20 MB downloads ~33% slower on Wi-Fi: with `T` > packets in flight, the receiver waits 25 ms in every slow-start round. msquic starts with an unlimited `ssthresh` and HyStart off, so slow start can be long; a blunt `T = 2` would cancel the savings, hence the cwnd/4 cap |
| `+reorder` (`MCN_ACK_REORDER=3`) | Request reordering threshold 3 (msquic sends 1) | Out-of-order arrivals bypass `T` and force an immediate ACK; the sender's loss detection needs 3 later packets acknowledged (`QUIC_PACKET_REORDER_THRESHOLD`, RFC 9002), so ACKs for smaller gaps carry no actionable signal (draft-ietf-quic-ack-frequency §9) |
| `+delay` (`MCN_ACK_DELAY`) | Request max ACK delay `clamp(SRTT/4, max(1 ms, peer min_ack_delay), 25 ms)` | Custura et al.: scale the ACK delay with the RTT; a fixed 25 ms timer is far longer than a LAN RTT |

## ACK-cost instrumentation

Whole-process CPU (getrusage) could not show ACK savings: ACK work is a small part of
it and it includes idle polling. perf needs root on the server. msquic therefore measures
directly (`clock_gettime`, ns) per connection and prints `[mcn-stats]` at shutdown:
time in `QuicConnFlushRecv` (receive path), in `QuicLossDetectionProcessAckFrame` (ACK
frames) and in `QuicSendFlush` (send path, via a wrapper). In a download the server
receives almost only ACK packets and the client sends almost only ACK packets, so these
are the ACK costs on each side. Kernel time in UDP system calls is not included.

**msquic receiver detail:** reaching `T` only *marks* an ACK as needed; the ACK is
sent after the whole receive batch is processed. The effective ratio is therefore
about `max(T, packets per receive batch)`, and small `T` values have no effect at
high packet rates.

## Baselines

1. RFC 9000 behaviour: `fixed:2`
2. Fixed threshold: `fixed:100` (LiteQUIC's largest tested value; `fixed:10` in earlier runs)
3. msquic's built-in heuristic: `default`
4. LiteQUIC's adaptive rule: `litequic`
5. Ours: `adaptive` (ceiling 100), `adaptive:255`, its modifiers, and `adaptive+net`

## Deferred ideas (brainstorm, not implemented)

| Idea | Why deferred |
|---|---|
| Client (receiver) CPU awareness | The receiver may only ACK *more* often than `T`; two-sided control needs a new CPU-hint frame |
| Event-driven `IMMEDIATE_ACK` (loss, tail, cwnd round) | msquic can receive but not send `IMMEDIATE_ACK`; needs a frame encoder and send flag |
| Flow-size awareness (small `T` for short transfers / last flight) | Mostly covered by `+phase` |
| BBR-specific ceiling (BBR tolerates sparse ACKs) | Needs BBR runs |
| Loss-rate (not only loss-event) scaling | Partly covered by `+net` |
| Server-wide ACK budget across connections | Needs a multi-connection workload |
| LiteQUIC's ACK merge | Lowers ACK cost, not frequency; would complete the LiteQUIC baseline |
| Adaptive reordering threshold (draft §9) | Only worth it if reordering is common; measure first |