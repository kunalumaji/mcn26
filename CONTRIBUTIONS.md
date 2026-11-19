# Individual Contributions

Who implemented what, with links to the exact lines of code. All MsQuic changes are marked
`MCN26`; most are in [`msquic/src/core/connection.c`](msquic/src/core/connection.c).
The split follows the "Individual contributions" paragraph of the report
([`docs/latex/report.tex`](docs/latex/report.tex#L438-L446)).

| Member | Part |
|---|---|
| [Kunal Umaji](#kunal-umaji) | Base MsQuic build, policy framework, `ACK_FREQUENCY` update path, ACK-cost instrumentation, run-time policy selection |
| [Kaveenraj G](#kaveenraj-g) | LiteQUIC-inspired policy, `+phase`, `+reorder` and `+delay` modifiers |
| [Aravindh Sriram Kumar A G](#aravindh-sriram-kumar-a-g) | CPU-aware `adaptive` policy and its network-aware `+net` ceiling |

---

## Kunal Umaji

**Base MsQuic, policy framework, `ACK_FREQUENCY` update path, ACK-cost instrumentation,
run-time policy selection.**

### Policy framework and run-time selection

| What | Code |
|---|---|
| Policy enum and documented environment variables | [`connection.h` L1735-L1764](msquic/src/core/connection.h#L1735-L1764) |
| Shared constants and configuration | [`connection.c` L6441-L6463](msquic/src/core/connection.c#L6441-L6463) |
| `QuicMcnAckEnvInt`: reads a bounded integer from the environment | [`connection.c` L6465-L6477](msquic/src/core/connection.c#L6465-L6477) |
| `QuicMcnAckInitialize`: picks the policy from `MCN_ACK_POLICY` | [`connection.c` L6479-L6494](msquic/src/core/connection.c#L6479-L6494) |
| `QuicMcnAckPolicy`: lazy one-time initialisation | [`connection.c` L6514-L6523](msquic/src/core/connection.c#L6514-L6523) |
| Hook: run the policy when the handshake is confirmed | [`crypto.c` L494-L497](msquic/src/core/crypto.c#L494-L497) |
| `QuicConnMcnAckOnHandshakeConfirmed` dispatcher and `fixed:N` start | [`connection.c` L6745-L6755](msquic/src/core/connection.c#L6745-L6755) |
| Hook: run the policy after each receive batch | [`connection.c` L6060-L6071](msquic/src/core/connection.c#L6060-L6071) |
| `QuicConnMcnAckOnRecvFlush` dispatcher | [`connection.c` L6927-L6938](msquic/src/core/connection.c#L6927-L6938) |
| `default` policy kept only when selected (send batching) | [`send.c` L1518-L1527](msquic/src/core/send.c#L1518-L1527) |
| `default` policy kept only when selected (persistent congestion) | [`loss_detection.c` L1052-L1058](msquic/src/core/loss_detection.c#L1052-L1058) |
| `quicfile`: command-line options to environment variables | [`quicfile.c` L134-L172](apps/quicfile/quicfile.c#L134-L172) |

### `ACK_FREQUENCY` update path

| What | Code |
|---|---|
| `QuicConnMcnAckUpdatePeer`: queues an `ACK_FREQUENCY` frame when a value changes | [`connection.c` L6545-L6571](msquic/src/core/connection.c#L6545-L6571) |
| Frame encoding in the send path | [`send.c` L918-L927](msquic/src/core/send.c#L918-L927) |

### ACK-cost instrumentation

| What | Code |
|---|---|
| Per-connection timing counters | [`connection.h` L527-L537](msquic/src/core/connection.h#L527-L537) |
| `QuicMcnNowNs`: nanosecond monotonic clock | [`connection.h` L1775-L1786](msquic/src/core/connection.h#L1775-L1786) |
| Timing of `QuicLossDetectionProcessAckFrame` (ACK processing) | [`connection.c` L4663-L4676](msquic/src/core/connection.c#L4663-L4676) |
| Timing of `QuicConnFlushRecv` (receive path) | [`connection.c` L6060-L6067](msquic/src/core/connection.c#L6060-L6067) |
| Timing of `QuicSendFlush` (send path, wrapper) | [`send.c` L1215](msquic/src/core/send.c#L1215), [`send.c` L1548-L1563](msquic/src/core/send.c#L1548-L1563) |
| `QuicConnMcnAckPrintStats`: `[mcn-stats]` line per connection | [`connection.c` L6635-L6661](msquic/src/core/connection.c#L6635-L6661) |
| Call at connection shutdown | [`connection.c` L1418-L1421](msquic/src/core/connection.c#L1418-L1421) |

---

## Kaveenraj G

**LiteQUIC-inspired policy and the `+phase`, `+reorder` and `+delay` modifiers; evaluation of
their effect on short transfers, video QoE and feedback timeliness.**

### `litequic` policy

| What | Code |
|---|---|
| Per-connection state (ACK frames per receive batch) | [`connection.h` L506-L510](msquic/src/core/connection.h#L506-L510) |
| Start at `T = 2` when the handshake is confirmed | [`connection.c` L6756-L6762](msquic/src/core/connection.c#L6756-L6762) |
| `QuicConnMcnAckLiteQuic`: once per SRTT, `T × ACK_avg` or `max(T/2, 2)` | [`connection.c` L6774-L6816](msquic/src/core/connection.c#L6774-L6816) |
| Called after each receive batch | [`connection.c` L6953-L6955](msquic/src/core/connection.c#L6953-L6955) |

### Modifiers: `+phase`, `+reorder`, `+delay`

| What | Code |
|---|---|
| Configuration variables | [`connection.c` L6459-L6461](msquic/src/core/connection.c#L6459-L6461), [`connection.c` L6501-L6503](msquic/src/core/connection.c#L6501-L6503) |
| `QuicConnMcnAckPhase`: Cubic slow start / recovery / congestion avoidance | [`connection.c` L6525-L6543](msquic/src/core/connection.c#L6525-L6543) |
| `QuicConnMcnAckSetThreshold`: applies the modifiers to every policy's threshold | [`connection.c` L6573-L6633](msquic/src/core/connection.c#L6573-L6633) |
| `+phase`: `T ≤ max(2, cwnd/4)` in slow start, `T = 2` in recovery | [`connection.c` L6587-L6602](msquic/src/core/connection.c#L6587-L6602) |
| `+reorder`: reordering threshold 3 | [`connection.c` L6604](msquic/src/core/connection.c#L6604) |
| `+delay`: max ACK delay `= SRTT/4`, within 1-25 ms | [`connection.c` L6606-L6615](msquic/src/core/connection.c#L6606-L6615) |
| Requested max ACK delay stored per connection | [`connection.h` L523-L526](msquic/src/core/connection.h#L523-L526) |
| Requested max ACK delay written into the frame | [`send.c` L923-L926](msquic/src/core/send.c#L923-L926) |
| `fixed` re-evaluated once per window when `+phase` or `+delay` is on | [`connection.c` L6939-L6952](msquic/src/core/connection.c#L6939-L6952) |
| `quicfile` options `-ackphase`, `-ackreorder`, `-ackdelay` | [`quicfile.c` L157-L159](apps/quicfile/quicfile.c#L157-L159) |

---

## Aravindh Sriram Kumar A G

**CPU-aware `adaptive` policy and its network-aware `+net` ceiling; evaluation of these policies and
analysis of why they succeed under moderate load and need feedback constraints under severe load
and loss.**

### CPU-pressure measurement

| What | Code |
|---|---|
| `fcntl.h` include for reading `/proc` | [`connection.c` L35](msquic/src/core/connection.c#L35) |
| Per-connection state: thread id, CPU counters, smoothed pressure, loss count | [`connection.h` L511-L522](msquic/src/core/connection.h#L511-L522) |
| `QuicMcnReadThreadSchedStat`: thread run + wait time from `/proc/thread-self/schedstat` | [`connection.c` L6663-L6691](msquic/src/core/connection.c#L6663-L6691) |
| `QuicMcnReadSystemCpu`: machine busy/total time from `/proc/stat` | [`connection.c` L6693-L6722](msquic/src/core/connection.c#L6693-L6722) |
| `QuicConnMcnAckAdaptiveReset`: starts a measurement window | [`connection.c` L6724-L6743](msquic/src/core/connection.c#L6724-L6743) |

### `adaptive` policy

| What | Code |
|---|---|
| Configuration: window, queueing delay, ceiling, ACKs per cwnd, CPU thresholds, `+net` | [`connection.c` L6449-L6450](msquic/src/core/connection.c#L6449-L6450), [`connection.c` L6454-L6458](msquic/src/core/connection.c#L6454-L6458) |
| Reading `MCN_ACK_MAX`, `MCN_ACK_NET`, `MCN_ACK_PER_CWND`, `MCN_ACK_CPU_LO/HI` | [`connection.c` L6498-L6500](msquic/src/core/connection.c#L6498-L6500), [`connection.c` L6504-L6507](msquic/src/core/connection.c#L6504-L6507) |
| Start at `T = 2` when the handshake is confirmed | [`connection.c` L6763-L6768](msquic/src/core/connection.c#L6763-L6768) |
| `QuicConnMcnAckAdaptive`: the full controller | [`connection.c` L6818-L6925](msquic/src/core/connection.c#L6818-L6925) |
| Step 0: wait for a window of `max(SRTT, 20 ms)` | [`connection.c` L6841-L6845](msquic/src/core/connection.c#L6841-L6845) |
| Step 1: CPU pressure `P = max(thread, machine)`, smoothed | [`connection.c` L6847-L6866](msquic/src/core/connection.c#L6847-L6866) |
| Step 2: `+net` ceiling: `cwnd/4`, halved while queueing, loss detection | [`connection.c` L6868-L6887](msquic/src/core/connection.c#L6868-L6887) |
| Step 3: scale `f(P)` from 50% to 90%, target `T = 2 + (C − 2) · f(P)` | [`connection.c` L6889-L6902](msquic/src/core/connection.c#L6889-L6902) |
| Step 4: halve `T` on loss (`+net`), at most double per window | [`connection.c` L6903-L6906](msquic/src/core/connection.c#L6903-L6906) |
| Step 5: log the inputs, apply `T`, start the next window | [`connection.c` L6908-L6924](msquic/src/core/connection.c#L6908-L6924) |
| Called after each receive batch | [`connection.c` L6956-L6958](msquic/src/core/connection.c#L6956-L6958) |
| `quicfile` options `-acknet`, `-ackmax` | [`quicfile.c` L155-L156](apps/quicfile/quicfile.c#L155-L156), [`quicfile.c` L168-L170](apps/quicfile/quicfile.c#L168-L170) |

Design rationale: [`docs/design.md`](docs/design.md) and the README section
[How `adaptive` calculates `T`](README.md#how-adaptive-calculates-t).
