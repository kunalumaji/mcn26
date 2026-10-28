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
| `adaptive` | **Ours, CPU only**: `T` scales with CPU pressure up to a fixed maximum (100) |
| `adaptive` + `-acknet:1` | **Ours, CPU + network**: the maximum comes from cwnd, queueing delay and loss |

### `adaptive`

The full algorithm, with a worked example, is in the README section
[How `adaptive` calculates the threshold](../README.md#how-adaptive-calculates-the-threshold).
In short, every `max(SRTT, 20 ms)`:

`T = 2 + (C − 2) · f(P)`, increases capped at 2× per window, where

- `P = max(QUIC thread pressure, whole-machine CPU)`, smoothed;
  `f(P)` ramps 0 → 1 between 50% and 90%;
- `C = 100` (CPU only), or with `-acknet:1`: `C = cwnd_packets / 4`, halved while the
  path is queueing (SRTT > 2× min RTT and > min RTT + 5 ms), within `[2, 100]`;
  a loss in the window also caps `T` at half its current value.

Design rationale and history:
- **Two CPU signals.** The first version used only the QUIC thread's pressure
  (`(on-CPU + runqueue wait) / wall time`). At Wi-Fi rates the thread needs only a
  fraction of a core and is scheduled promptly even on a fully loaded machine, so it
  stayed at 20–35% and the policy never reacted to high overall CPU. Whole-machine
  utilisation was added: when the server is busy, ACK work is taken from other work.
- **Network as an option.** `-acknet:1` separates the contribution of network
  awareness: with it off, `adaptive` is a pure CPU-driven policy that can be compared
  with `fixed:100` (same maximum) and `litequic`.
- **`cwnd / 4` ceiling.** Keeps at least 4 ACKs per congestion window, so congestion
  control and loss detection keep getting feedback; LiteQUIC has no such bound.
- **Queueing condition.** Requires both 2× inflation *and* 5 ms of extra delay: on
  LAN/loopback the minimum RTT is tens of microseconds, so the ratio alone is
  triggered by any jitter.

**msquic receiver detail:** reaching `T` only *marks* an ACK as needed; the ACK is
sent after the whole receive batch is processed. The effective ratio is therefore
about `max(T, packets per receive batch)`, and small `T` values have no effect at
high packet rates.

## Baselines

1. RFC 9000 behaviour: `fixed:2`
2. Fixed thresholds: `fixed:10`, `fixed:100`
3. msquic's built-in heuristic: `default` (see [msquic-notes.md](msquic-notes.md))
4. LiteQUIC's adaptive rule: `litequic`
5. Our approach: `adaptive` (CPU only) and `adaptive` + `-acknet:1` (CPU + network)

## Open questions

- Receiver-side CPU: the receiver must ACK at least as often as `T`, so client CPU
  cannot raise `T` without a signal back to the sender. Options: a hint frame, or
  sending `T_net` as a ceiling and letting the receiver choose within it.
- Event-driven feedback (`IMMEDIATE_ACK` at loss / slow start) on top of `adaptive`.
- Testbed: the client is on Wi-Fi; final numbers need a wired link.