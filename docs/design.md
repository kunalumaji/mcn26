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

## Baselines (planned)

1. RFC 9000 default (threshold 2, 25 ms max ACK delay)
2. Fixed thresholds 10 and 100
3. msquic's built-in heuristic (see [msquic-notes.md](msquic-notes.md))
4. LiteQUIC's adaptive rule, reimplemented in msquic
5. Our approach

## Open questions

- Which direction do we pick?
- Sender-driven, receiver-driven or hybrid control?
- Testbed: two physical machines with fast NICs, or network namespaces on one host?