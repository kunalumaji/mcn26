# Results

One folder per run of `experiments/run_quicfile.sh` (file layout: see the main
[README](../README.md#4-outputs-resultstimestamp)). `meta.txt` in each folder has the exact settings
and the commit both machines ran; `tables.md` (where present) has the tables of
`analysis/summarize.py`, `figures/` the plots of `analysis/plot.py`.

## Runs of the report (Nov 14)

Server `sense-student@10.21.237.7` (AMD Ryzen 7 5800X, 16 logical CPUs, 1 Gbit/s Ethernet), client
on campus Wi-Fi (~600 Mbit/s). Server CPU load in the servers' own session (`LOAD_SCOPE=shared`),
so the busy loops compete with the QUIC threads. Created by `experiments/run_all.sh`.

| Run | Commit | Experiment | Runs | Notes |
|---|---|---|---|---|
| [`20261114-021601`](20261114-021601) | `6daabb6` | 1: downloads, 12 policy variants × 20 MB/100 MB/500 MB/1 GB × load 0/2x/4x × 3 | 432 (12 stalled) | Throughput per 100 ms in `tput.log` |
| [`20261114-032917`](20261114-032917) | `4076256` | 2: load sweep, 500 MB, 4 policies × load 0–4x (7 steps) × 2 | 56 (4 stalled) | `figures/load_sweep.png` |
| [`20261114-033839`](20261114-033839) | `4076256` | 3: video streaming, 8 policies × load 0/2x/4x × 3 | 72 (8 stalled) | Per-segment records in `segments.log`; QoE in `videos.csv` |

## Lossy-path runs (Nov 19–20)

Server `kunal@10.21.232.3` (Intel Core i7-14700, 28 logical CPUs, 100 Mbit/s Ethernet), client on
campus Wi-Fi. `tbf` + `netem` on the server's egress (`scripts/netem.sh`): 100 Mbit/s, +10 ms
(minimum RTT 11.5 ms), 0% or 0.1% random loss. 100 MB downloads, server CPU load in the servers'
session. Experiments 4 and 5 and the two lossy halves of experiment 6 were created by
`experiments/run_all.sh` (experiment 6 then ran 3 repetitions on the lossy path only, so it ran
twice); the clean half of experiment 6 by `experiments/run_quicfile.sh` with the settings of
`run_all.sh` (6 repetitions, `NETEM_PROFILES=100mbit:10ms:0`). Tables in `tables.md` from
`analysis/loss.py`. Figure 4 of report v1.6 (`20261120-033617/figures/threshold_loss.pdf`):
`analysis/loss.py --fig fig.pdf results/20261119-230609 results/20261120-002614 results/20261120-010759 results/20261120-033617`.
Report v1.5's Figure 4 used the first three of these (`20261120-010759/figures/loss.pdf`, drawn
by `analysis/loss.py` at commit `6f0237d`).

| Run | Commit | Experiment | Runs | Notes |
|---|---|---|---|---|
| [`20261119-230609`](20261119-230609) | `ad088e5` | 4: `fixed:2` … `fixed:100` (7 values) × 0%/0.1% loss × 3, no load | 42 (0 stalled) | |
| [`20261119-232105`](20261119-232105) | `ad088e5` | 5: 8 policies × 0%/0.1% loss × load 0/2x/4x × 3 | 144 (0 stalled) | Emulated qdiscs per level in `netem.log` |
| [`20261120-002614`](20261120-002614) | `2000450` | 6: `fixed:2` + `adaptive:C` (C = 2…100) × {none, `+phase`, `+delay`, `+net`, all} × 3, 0.1% loss, load 2x | 108 (0 stalled) | |
| [`20261120-010759`](20261120-010759) | `068509e` | 6 again (same settings) | 108 (0 stalled) | Pooled with the run above in the report |
| [`20261120-033617`](20261120-033617) | `6f0237d` | 6 on the clean path (0% loss), same policies, × 6 | 216 (0 stalled) | |

"Stalled": msquic's disconnect timeout (`0x6e`, no acknowledgement for 16 s); the client's output
is in `failures.log`, server restarts in `restarts.log`.

## Earlier runs (not used in the report's results)

Server `kunal@10.21.232.3` (Intel Core i7-14700, 28 cores). In these runs the CPU load ran in a
separate session, which made the machine busy but did not slow the QUIC threads (see the README,
"How the server CPU load is generated").

| Run | Commit | Policies | Downloads | Notes |
|---|---|---|---|---|
| [`20261028-223719`](20261028-223719) | `77a02e3` | default, fixed:2, fixed:10, fixed:100, litequic, adaptive | 144 (6 failed) | First full evaluation, client on Wi-Fi (~200 Mbps). Here `adaptive` is an **earlier version**: cwnd/4 ceiling up to 255 and loss back-off, closest to today's `adaptive+net` without the queueing rule. The 6 failures are one `fixed:2` server that stopped completing handshakes after a stall under load. `download_s` is derived from handshake + transfer time |
| [`20261029-014546`](20261029-014546) | `ee1f3f4` | fixed:2, adaptive, adaptive+net | 6 | Quick check of both `adaptive` modes (100 MB, 1 repetition) |
| [`20261029-014904`](20261029-014904) | `ee1f3f4` | default, fixed:2, fixed:10, fixed:100, litequic, adaptive (CPU only) | 144 (3 failed) | Evaluation of CPU-only `adaptive`, client on Wi-Fi (~600 Mbps) |
| [`20261112-012708`](20261112-012708) | `8f618e1` | 11 variants (as experiment 1 without `adaptive:255`) | 264 (3 failed) | Client on Wi-Fi (~600 Mbit/s), loads `0` and `2x` (2 busy loops per core). `cpu_freq_server.log`: server cores at 822 MHz idle, 2715 MHz loaded |
| `20261028-215219`, `20261028-221214`, `20261028-223437`, `20261028-223645`, `20261112-010428`, `20261112-012620` | various | – | few | Short pipeline trial runs |

Results from different runs are not directly comparable: server, load method and Wi-Fi link speed
differed.
