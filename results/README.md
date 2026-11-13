# Results

One folder per run of `experiments/run_quicfile.sh` (file layout: see the main
[README](../README.md#4-outputs-resultstimestamp)). `meta.txt` in each folder has the exact settings
and the commit both machines ran.

| Run | Commit | Policies | Downloads | Notes |
|---|---|---|---|---|
| [`20261028-223719`](20261028-223719) | `77a02e3` | default, fixed:2, fixed:10, fixed:100, litequic, adaptive | 144 (6 failed) | First full evaluation, client on Wi-Fi (~200 Mbps). Here `adaptive` is an **earlier version**: cwnd/4 ceiling up to 255 and loss back-off, closest to today's `adaptive+net` without the queueing rule. The 6 failures are one `fixed:2` server that stopped completing handshakes after a stall under load. `download_s` is derived from handshake + transfer time |
| [`20261029-014546`](20261029-014546) | `ee1f3f4` | fixed:2, adaptive, adaptive+net | 6 | Quick check of both `adaptive` modes (100 MB, 1 repetition). Only data for `adaptive+net` so far |
| [`20261029-014904`](20261029-014904) | `ee1f3f4` | default, fixed:2, fixed:10, fixed:100, litequic, adaptive (CPU only) | 144 (3 failed) | Main evaluation of CPU-only `adaptive`, client on Wi-Fi (~600 Mbps). Failures: transfers stalled under load in 3 different policies; servers restarted (`restarts.log`) |
| [`20261112-012708`](20261112-012708) | `8f618e1` | same 11 variants | 264 (3 failed) | **Main run of the report.** Client on Wi-Fi (~600 Mbit/s), files 20 MB–1 GB, loads `0` and `2x`. Figures in `figures/`; `cpu_freq_server.log` has server core frequencies (822 MHz idle, 2715 MHz loaded). Failures: 1 GB transfers that stalled and hit the idle timeout, in 3 different policies (`failures.log`) |

Server load levels in all runs: `0` (none) and `2x` (2 busy-loop processes per core,
28-core server). Results from different runs are not directly comparable: the Wi-Fi
link speed differed between sessions.
