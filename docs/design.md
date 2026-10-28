# Design Notes

## Problem

At high rates, QUIC's user-space ACK generation and processing take a large share
of CPU. Lowering ACK frequency saves CPU but starves congestion control of
feedback, which hurts throughput with shallow buffers and on lossy paths
(see LiteQUIC in [related-work.md](related-work.md)).

## Candidate directions (not final)

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
| `adaptive` | **Ours**, described below |

### `adaptive`: CPU-pressure-scaled threshold with a network ceiling

Evaluated every `max(SRTT, 20 ms)` while ACKs are being received:

1. **CPU pressure `P`** of the msquic worker thread that processes this connection's
   ACKs, from `/proc/thread-self/schedstat`:
   `P = (Δ on-CPU time + Δ runqueue-wait time) / Δ wall time`, smoothed (EWMA ½).
   `P` is high both when the thread is saturated by its own work and when it is
   starved by other load. System-wide utilisation would miss the second case for a
   single thread on a many-core machine.
2. **Network ceiling** `T_net = cwnd_packets / K` (`K = 4`): congestion control
   still receives at least `K` ACKs per congestion window (LiteQUIC has no such bound).
3. **Threshold** `T = 2 + (T_net − 2) · f(P)`, where `f` ramps linearly from 0 at
   `P = 50%` to 1 at `P = 90%`. Idle CPU → frequent ACKs (best feedback);
   loaded CPU → as few ACKs as the network ceiling allows.
4. **Safety:** on a congestion event in the window, `T ≤ max(T_current/2, 2)`.
   Increases are capped at 2× per window; decreases apply at once.

Tunable through environment variables: `MCN_ACK_PER_CWND` (`K`), `MCN_ACK_CPU_LO`,
`MCN_ACK_CPU_HI`. With `-acklog:1` every decision is logged with its inputs
(`cpu_pct`, `cwnd_pkts`, `t_net`, `loss`).

**msquic receiver detail:** reaching `T` only *marks* an ACK as needed; the ACK is
sent after the whole receive batch is processed. The effective ratio is therefore
about `max(T, packets per receive batch)`, and small `T` values have no effect at
high packet rates.

## Baselines

1. RFC 9000 behaviour: `fixed:2`
2. Fixed thresholds: `fixed:10`, `fixed:100`
3. msquic's built-in heuristic: `default` (see [msquic-notes.md](msquic-notes.md))
4. LiteQUIC's adaptive rule: `litequic`
5. Our approach: `adaptive`

## Open questions

- Receiver-side CPU: the receiver must ACK at least as often as `T`, so client CPU
  cannot raise `T` without a signal back to the sender. Options: a hint frame, or
  sending `T_net` as a ceiling and letting the receiver choose within it.
- Event-driven feedback (`IMMEDIATE_ACK` at loss / slow start) on top of `adaptive`.
- Testbed: the client is on Wi-Fi; final numbers need a wired link.