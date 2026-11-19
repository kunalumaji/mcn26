# mcn26: Adaptive ACK Frequency for QUIC

Modern Networks course project (team: Kunal Umaji, Kaveenraj G, Aravindh Sriram Kumar A G).

**Full write-up: [docs/report.md](docs/report.md).**

## The problem in one paragraph

QUIC runs in user space and encrypts everything, including acknowledgements (ACKs).
Every ACK costs CPU twice: the receiver must build, encrypt and send it, and the sender
must receive, decrypt and process it. Studies found that this is a main reason QUIC is
slower and more CPU-hungry than TCP on fast links ([Zhang et al., WWW'24](https://arxiv.org/abs/2310.09423);
[LiteQUIC, MM'24](https://doi.org/10.1145/3664647.3681670)). The IETF
[ACK Frequency extension](https://datatracker.ietf.org/doc/draft-ietf-quic-ack-frequency/)
lets the data sender ask the receiver to ACK less often, but it does not say *how often*.
Fewer ACKs save CPU, but too few starve congestion control and loss detection of feedback.
**This project builds and evaluates policies that choose the ACK frequency dynamically from
CPU and network conditions**, inside Microsoft's QUIC implementation, msquic.

## Key terms

| Term | Meaning |
|---|---|
| **ACK threshold `T`** | The receiver sends an ACK after every `T` ack-eliciting packets. RFC 9000 default: `T = 2` |
| **Max ACK delay** | If fewer than `T` packets arrive, the receiver still ACKs after this delay (25 ms in msquic) |
| **Reordering threshold** | A gap of this many packets makes the receiver ACK immediately (msquic default 1: any gap) |
| **`ACK_FREQUENCY` frame** | Carries the three values above from the data sender to the receiver |
| **Policy** | The rule the server (data sender) uses to choose these values during a connection |

## What we built

| Component | Where | What it does |
|---|---|---|
| ACK policies | `msquic/src/core/connection.c` (search `MCN26`) | `default`, `fixed:N`, `litequic` (reimplementation of LiteQUIC's rule), our `adaptive` |
| Policy modifiers | same | `+phase` (slow-start/recovery aware), `+reorder` (reordering threshold 3), `+delay` (RTT-scaled max ACK delay), `+net` (network ceiling for `adaptive`) |
| ACK-cost instrumentation | `connection.c`, `send.c` | Measures time spent in the receive path, ACK processing and the send path, per connection |
| `quicfile` | `apps/quicfile/` | Client/server on msquic for **file downloads** (with throughput every 100 ms) and **video streaming** (2 s segments, adaptive bitrate, QoE metrics); the policy is a command-line option |
| Evaluation pipeline | `scripts/`, `experiments/`, `analysis/` | Test data, server CPU load, CPU logging, a runner that drives both machines, CSVs, plots |

### The policies

In a download the **server sends data and chooses `T`**; the client (receiver) follows it.

| Policy | How the server chooses `T` |
|---|---|
| `default` | msquic's built-in behaviour (raises `T` to its send batch size + 1 when it cannot send fast enough) |
| `fixed:N` | `T = N` for the whole connection. `fixed:2` is the RFC 9000 behaviour |
| `litequic` | LiteQUIC's rule: once per RTT, `ACK_avg` = ACK frames processed per receive batch. `ACK_avg ≥ 2` (taken as CPU-limited): `T = T × ACK_avg`; otherwise `T = max(T/2, 2)` |
| `adaptive[:N]` | **Ours**: `T` rises with CPU pressure, from 2 (idle) to the ceiling `N` (busy; default 100, at most 255). Details below |

**Modifiers** can be added to `fixed`, `litequic` and `adaptive` (runner syntax `policy+modifier+...`):

| Modifier | Rule | Why |
|---|---|---|
| `+net` | `adaptive` only: the maximum `T` comes from the path: `cwnd_packets / 4`, halved while packets queue (smoothed RTT > 2× min RTT and > min RTT + 5 ms), and `T` halves on loss | Congestion control keeps at least 4 ACKs per window |
| `+phase` | Cubic slow start: `T ≤ max(2, cwnd_packets / 4)`; loss recovery: `T = 2` | A large `T` with a small window makes the receiver wait for its 25 ms timer in every slow-start round |
| `+reorder` | Reordering threshold 3 instead of 1 | The sender can only declare a packet lost after 3 later packets are acknowledged (RFC 9002), so ACKs for smaller gaps are wasted |
| `+delay` | Max ACK delay `= SRTT / 4`, between 1 and 25 ms | The timer fallback should scale with the path, not be a fixed 25 ms |

### How `adaptive` calculates `T`

Every `max(smoothed RTT, 20 ms)` while ACKs arrive, the server:

1. **Measures CPU pressure `P`** (0–100%), the higher of:
   - **QUIC thread pressure**: for the msquic thread processing this connection,
     `(time running + time waiting for a CPU) / elapsed time` (`/proc/thread-self/schedstat`);
   - **whole-machine CPU utilisation** (`/proc/stat`): when the server is busy, every cycle
     spent on ACKs is taken from other work.

   and smooths it: `P = (P_previous + P_new) / 2`.
2. **Scales**: `f(P) = 0` below 50%, `1` above 90%, linear in between: `f = (P − 50) / 40`.
3. **Chooses the ceiling `C`**: 100 (`adaptive:N` or `-ackmax:N`, at most 255 because msquic
   stores the threshold in 8 bits), or with `+net` the network ceiling above.
4. **Target**: `T = 2 + (C − 2) × f(P)`. Examples: `P ≤ 50% → 2`, `60% → 26`, `70% → 51`,
   `80% → 75`, `≥ 90% → 100`.
5. **Safety**: `T` at most doubles per window (decreases apply at once); with `+net`, a loss
   caps `T` at half its current value.

The new values go to the client in an `ACK_FREQUENCY` frame when they change.
Every decision is logged with its inputs (`ack_decisions.csv`). Environment variables
`MCN_ACK_CPU_LO` (50), `MCN_ACK_CPU_HI` (90), `MCN_ACK_MAX` (100) and `MCN_ACK_PER_CWND` (4)
tune it. Design rationale: [docs/design.md](docs/design.md).

### How ACK cost is measured

perf needs root on the server, so msquic itself measures (nanosecond clock) and prints one
`[mcn-stats]` line per connection (with `-acklog:1`):

| Metric | Measured around | Meaning in a download |
|---|---|---|
| `server_recv_us_per_mb` | `QuicConnFlushRecv` on the server | The server receives almost only ACK packets: **time to decrypt and process ACKs** |
| `server_ack_proc_us_per_mb` | `QuicLossDetectionProcessAckFrame` | Only the ACK-frame processing part of the above |
| `client_send_us_per_mb` | `QuicSendFlush` on the client | The client sends almost only ACK packets: **time to build, encrypt and send ACKs** |

Kernel time in the UDP system calls is not included (it runs in msquic's datapath threads).

### How the server CPU load is generated

`scripts/cpu-load.sh Kx` starts `K` busy-loop processes (`while :; do :; done`) per logical CPU
of the server, at normal priority and without root: `2x` on our 16-CPU server is 32 processes,
`4x` is 64. They stand in for the server's other work, such as other connections and the
application.

**Where the busy loops run matters.** Linux groups processes by session ("autogroup", enabled
by default on Ubuntu: `/proc/sys/kernel/sched_autogroup_enabled`) and shares CPU time fairly
between groups first, then between the threads inside a group.
- **Until Nov 12 (`LOAD_SCOPE=separate`)** the busy loops ran in their own session. The machine
  was 100% busy, but the QUIC server still got a CPU almost whenever it wanted one: its worker
  thread's CPU demand (running + waiting) was 24% under "2x" load vs 28% with no load. That
  load only made the server *look* busy, so download speed never changed.
- **Since Nov 14 (`LOAD_SCOPE=shared`, the default)** the runner starts the busy loops and all
  servers in **one** session (`scripts/server-session.sh`). Each busy loop now competes
  one-to-one with the QUIC threads: the worker thread's CPU demand rises from 12% (no load) to
  94% (2x) and 90% (4x), and at 4x the goodput of every policy drops (e.g. `fixed:2`: ~600 →
  ~400 Mbit/s). This models a server whose own work competes with ACK processing.

## Results

Three experiments (Nov 14), client on Wi-Fi (~600 Mbit/s), server AMD Ryzen 7 5800X (16 logical
CPUs) with 0, 2 or 4 competing busy loops per CPU:

| Experiment | Result folder | Runs |
|---|---|---|
| 1. File downloads, 12 policy variants, 20 MB–1 GB | [`20261114-021601`](results/20261114-021601) | 432 (420 completed) |
| 2. Load sweep, 4 policies, load 0 → 4x | [`20261114-032917`](results/20261114-032917) | 56 (52 completed) |
| 3. Video streaming, 8 policies | [`20261114-033839`](results/20261114-033839) | 72 (64 completed) |

Three more (Nov 19–20) on an emulated 100 Mbit/s path (+10 ms, 0% or 0.1% random loss), server Intel
i7-14700 (28 logical CPUs), same Wi-Fi client, 100 MB downloads:

| Experiment | Result folder | Runs |
|---|---|---|
| 4. Fixed threshold 2–100 vs loss, no CPU load | [`20261119-230609`](results/20261119-230609) | 42 (all completed) |
| 5. 8 policies on the lossy path, load 0/2x/4x | [`20261119-232105`](results/20261119-232105) | 144 (all completed) |
| 6. `adaptive`'s ceiling 2–100 × 5 modifier sets, 2x load: 0.1% loss (two runs, pooled) and 0% loss | [`20261120-002614`](results/20261120-002614), [`20261120-010759`](results/20261120-010759), [`20261120-033617`](results/20261120-033617) | 432 (all completed) |

Details, tables and discussion: [docs/report.md](docs/report.md).

1. **`adaptive` follows the CPU load.** Idle server: `T = 2` (RFC behaviour). `T` rises with the
   machine's utilisation (10 at 0.5x, 69 at 0.75x load) and reaches its ceiling of 100 when the
   machine is fully busy (1x and more).
2. **Under load it removes half to two thirds of the ACKs:** 52–67% fewer than `fixed:2`, with
   32–57% less client ACK-generation time and 10–33% less server ACK-processing time.
3. **At 2x load this costs nothing:** goodput 588 vs 582 Mbit/s (`fixed:2`), download time 1.03×,
   and the best video QoE (top bitrate, no rebuffering).
4. **The ceiling matters.** msquic allows at most 255. `adaptive:255` cut ACKs further (−72 to −84%)
   but caused the most throughput collapses and stalls on a CPU-starved server and 9% longer
   downloads at 2x. 100 is a reasonable fixed ceiling; better is a quarter of the congestion window
   (`+net`).
5. **A starved server needs timely feedback.** At 4x load, variants with an RTT-scaled ACK timer
   (`+delay`) or the window-based ceiling (`+net`) kept `fixed:2`'s goodput and video QoE with ~40%
   fewer ACKs and had no throughput collapses; thresholds of 100+ with msquic's 25 ms timer
   occasionally collapsed for seconds.
6. **`+phase` removes the small-transfer penalty of a high threshold** (20 MB: 0.28 s vs 0.39 s
   with `fixed:100`), also for video segments (startup 0.12 s vs 0.19 s).
7. **msquic already batches ACKs at high rates** (~50 data packets per ACK at `T = 2`), so the
   server's remaining ACK cost is mostly per acknowledged packet.
8. **LiteQUIC's rule misreads Wi-Fi:** it raised `T` on an *idle* server (mean 66) and changed it
   36–61 times per second.
9. **Loss needs network bounds** (experiments 4–5). On the emulated path `T = 2` gives one ACK per
   4–5 packets, so under load `adaptive` (`T = 100`) cuts server ACK-processing time by 60–65% and
   client ACK generation by >90% at unchanged goodput. With 0.1% loss, though, `T = 100` loses
   70–76% of goodput (16 vs 55–57 Mbit/s), whether set by `fixed:100` or by `adaptive`;
   `T <= 8` costs nothing. Sweeping `adaptive`'s ceiling (experiment 6): on the clean path every
   variant keeps 93 Mbit/s at every ceiling (82–94% fewer ACKs at 100); with loss, without
   modifiers (or with `+phase` only) goodput falls like a fixed threshold; `+net` (caps `T` at ~15), `+delay`
   (ACK within SRTT/4) and `adaptive+all` keep 42–55 Mbit/s at any ceiling, `adaptive+all`
   48–49 Mbit/s (94–96% of `fixed:2`) with 67–69% fewer ACKs. At ceiling 100 in experiment 5,
   `adaptive+all` kept 79–84%.
10. **Open issues:** 4.3% of Wi-Fi runs stalled (no packet in either direction for 16 s), almost all
   under load and with every kind of policy; the cause is unknown. `+reorder` saw no reordered
   packet in 472 downloads.

| ![Threshold over time](results/20261114-021601/figures/threshold_timeline.png) | ![Load sweep](results/20261114-032917/figures/load_sweep.png) |
|---|---|
| ![Goodput](results/20261114-021601/figures/goodput.png) | ![Video QoE](results/20261114-033839/figures/video_qoe.png) |

## Repository layout

| Path | Contents |
|---|---|
| `msquic/` | Vendored [microsoft/msquic](https://github.com/microsoft/msquic) `v2.6.3` (commit `16ca4d8`) with our changes, marked `MCN26` |
| `apps/quicfile/` | Download and video-streaming client/server used in all experiments |
| `scripts/setup.sh` | Builds msquic, `secnetperf` and `quicfile` |
| `scripts/make-testdata.sh` | Creates the test files (20 MB, 100 MB, 500 MB, 1 GB, video segments at 10–400 Mbit/s; random data) and a TLS certificate |
| `scripts/cpu-load.sh` | CPU load: `N` or `Kx` (K per logical CPU) busy-loop processes |
| `scripts/server-session.sh` | Runs all servers of a run and the CPU load in one session, so the load competes with the QUIC threads (see [Server CPU load](#how-the-server-cpu-load-is-generated)) |
| `scripts/cpumon.py` | Logs machine and `quicfile` CPU every 200 ms |
| `scripts/netem.sh` | Emulates a path (rate, delay, random loss) on the server's egress with `tc` `tbf` + `netem` |
| `experiments/run_quicfile.sh` | The evaluation runner (run on the client) |
| `analysis/parse_results.py` | Turns a run into `runs.csv` (downloads) or `videos.csv` (video sessions), `ack_decisions.csv`, `summary.csv` |
| `analysis/plot.py` | Draws the figures of a run into `results/<run>/figures/` |
| `analysis/summarize.py` | Prints the report's tables (markdown) for a run |
| `analysis/loss.py` | Tables and the report figure for the lossy-path experiments (4–6) |
| `results/` | One folder per evaluation run; index in [results/README.md](results/README.md) |
| `docs/` | [Report](docs/report.md), [design notes](docs/design.md), [BibTeX](docs/refs.bib) |
| `proposal/` | Course proposal |

## Reproducing the results

You need two Linux machines (we used Ubuntu 22.04 on the server and 24.04 on the client): a **server** that serves the files and a
**client** that downloads them. Everything is started on the client, which controls the
server over SSH.

### 1. Requirements

- CMake ≥ 3.16, GCC, Ninja, `libssl-dev`, `libnuma-dev`, `openssl`, Python 3.
  `INSTALL_DEPS=1 scripts/setup.sh` installs them with apt (needs sudo).
- `matplotlib` on the client for the figures (`pip install matplotlib`).
- Disk: about 250 MB for sources and build; 2 GB for the test files on the server.
- No root access is needed on either machine.

### 2. Set up both machines (same commit on both)

```bash
git clone https://github.com/kunalumaji/mcn26.git ~/Documents/mcn26
cd ~/Documents/mcn26
scripts/setup.sh            # builds into build/msquic/bin/Release/ (also fetches quictls)
```

On the **server** only, create the test files and certificate (in `data/`, not in git):

```bash
scripts/make-testdata.sh
```

On the **client**: `ssh <user>@<server>` must work without a password (SSH key), and UDP
ports 4433–4445 on the server must be reachable.

If the server cannot reach GitHub, push the commit to it from the client instead:

```bash
git push ssh://<user>@<server>/home/<user>/Documents/mcn26 main:refs/heads/from-client
ssh <user>@<server> 'cd ~/Documents/mcn26 && git merge --ff-only from-client && scripts/setup.sh'
```

### 3. Run the evaluation (on the client)

All experiments of the report, with figures and tables (about 2–3 hours over ~600 Mbit/s Wi-Fi
for experiments 1–3, about 2.5 hours for 4–6):

```bash
SERVER=<user>@<server-ip> NETEM_SERVER=<user>@<netem-server-ip> experiments/run_all.sh
EXPERIMENTS="4 5 6" experiments/run_all.sh   # only some of them
```

It runs six experiments, each into its own `results/<timestamp>/`:

| # | Experiment | Settings (see [`experiments/run_all.sh`](experiments/run_all.sh)) |
|---|---|---|
| 1 | File downloads | 12 policy variants × 4 files (20 MB–1 GB) × server load `0`, `2x`, `4x` × 3 repetitions = 432 downloads |
| 2 | Load sweep | 500 MB, 4 policies, server load `0` to `4x` in 7 steps, 2 repetitions |
| 3 | Video streaming | 8 policies × server load `0`, `2x`, `4x` × 3 repetitions; 40 s of video in 2 s segments, bitrates 25–400 Mbit/s |
| 4 | Loss sensitivity of a fixed threshold | 100 MB, `fixed:2` to `fixed:100` (7 values) on an emulated 100 Mbit/s path with +10 ms and 0% or 0.1% random loss, no CPU load, 3 repetitions |
| 5 | Policies on the lossy path | 100 MB, 8 policies on the same path × server load `0`, `2x`, `4x` × 3 repetitions |
| 6 | Ceiling sweep | 100 MB, 0% and 0.1% loss, server load `2x`: `adaptive:C` for C = 2…100 with no modifier, `+phase`, `+delay`, `+net` and all of them, plus `fixed:2`; 6 repetitions |

Experiments 4–6 emulate the path with `tc` (`tbf` + `netem`, [`scripts/netem.sh`](scripts/netem.sh))
on the egress of `NETEM_SERVER`, which needs a passwordless sudo rule for `tc`
(`<user> ALL=(root) NOPASSWD: /usr/sbin/tc`). Their tables and figure come from
[`analysis/loss.py`](analysis/loss.py).

Each experiment is one call of `experiments/run_quicfile.sh`, configured by environment
variables. For example, a 1-minute check of the pipeline:

```bash
FILES=20MB.bin REPS=1 SERVER_LOADS=0 POLICIES="fixed:2 fixed:100 adaptive" experiments/run_quicfile.sh
python3 analysis/plot.py results/<timestamp>        # figures into results/<timestamp>/figures/
python3 analysis/summarize.py results/<timestamp>   # the report's tables (markdown)
```

| Variable | Default | Meaning |
|---|---|---|
| `SERVER` | `sense-student@10.21.237.7` | SSH target of the server (our runs from Nov 14; earlier runs used `kunal@10.21.232.3`) |
| `SERVER_IP` | host part of `SERVER` | Address the client downloads from |
| `SERVER_ROOT` | `Documents/mcn26` | Repo path on the server, relative to its home |
| `WORKLOAD` | `files` | `files`: download each of `FILES`; `video`: one video session per run |
| `FILES` | `20MB.bin 100MB.bin 500MB.bin 1GB.bin` | Files to download (from the server's `data/files/`) |
| `VIDEO_OPTS` | `-ladder:25,50,100,200,300,400 -segments:20 -segdur:2 -bufmax:10` | Video settings: bitrates (Mbit/s), segment count, segment length (s), buffer size (s) |
| `POLICIES` | the 12 specs of experiment 1 | `default`, `fixed:N`, `litequic`, `adaptive[:N]`, each optionally `+net`, `+phase`, `+reorder`, `+delay` |
| `SERVER_LOADS` | `0 2x 4x` | Server CPU load levels: `0` none, `N` busy loops, `Kx` K per logical CPU |
| `LOAD_SCOPE` | `shared` | `shared`: load competes with the QUIC threads; `separate`: the pre-Nov-14 behaviour ([why](#how-the-server-cpu-load-is-generated)) |
| `NETEM_PROFILES` | `none` | Emulated paths on the server's egress, each run at every load level: `none` or `<rate>:<delay>:<loss%>`, e.g. `100mbit:10ms:0.1` (needs sudo for `tc` on the server) |
| `CLIENT_LOAD` | `0` | CPU load on the client during all runs |
| `REPS` | `3` | Repetitions per (load, file, policy) |
| `CC` | `cubic` | Congestion control: `cubic` or `bbr` |
| `ALLOW_DIRTY` | unset | `1` skips the check that both machines are on the same clean commit |

What the runner does:
- **Refuses to start** unless client and server are on the same commit with no uncommitted
  changes, so every result comes from identical code (`meta.txt` records both commits).
- Starts **one server per policy**, each on its own port, together with the CPU load in one
  session per load level, and **interleaves** runs across policies, rotating the order every
  repetition, so slow changes in the network affect all policies equally.
- Logs CPU on both machines every 200 ms (`scripts/cpumon.py`).
- If a download fails, saves the client's output in `failures.log` and restarts that policy's
  server (`restarts.log`).

### 4. Outputs (`results/<timestamp>/`)

| File | Contents |
|---|---|
| `meta.txt` | Settings, commits, hosts, kernels |
| `runs.csv` | **One row per download**: `download_s` (connection start to last byte), `goodput_mbps` (bytes / transfer time), `tput_p10/p50/p90_mbps` (percentiles of the throughput in 100 ms intervals), `data_pkts_per_ack`, `server_recv_ack_frames`, `server_recv_us_per_mb`, `server_ack_proc_us_per_mb`, `client_send_us_per_mb`, process CPU per MB, machine CPU %, losses, reordered packets, RTT, mean threshold `T` |
| `videos.csv` | **One row per video session** (`WORKLOAD=video`): `avg_bitrate_mbps`, `startup_ms`, `rebuffer_events`, `rebuffer_ms`, `switches`, `qoe`, segment throughput, plus the same ACK and CPU columns as `runs.csv` |
| `summary.csv` | Medians per (server load, emulated path if any, policy, file) |
| `ack_decisions.csv` | Every threshold decision over time with the policy's inputs |
| `tput.log` | Throughput in every 100 ms of every download (`TPUT` lines) |
| `segments.log` | One line per video segment: bitrate, download time, throughput, buffer level, stall (`SEG` lines) |
| `cpu_client.csv`, `cpu_server.csv` | CPU time series of both machines |
| `client.log`, `server.log` | Raw output (`RESULT`/`VIDEO`, `SERVER`, `[mcn]`, `[mcn-stats]` lines) |
| `netem.log` | Emulated-path runs: the `tc` qdiscs set for each path and load level |
| `figures/`, `tables.md` | Plots from `analysis/plot.py`, tables from `analysis/summarize.py` (lossy-path runs: `analysis/loss.py`) |

### 5. A single download or video session by hand

```bash
# server
build/msquic/bin/Release/quicfile server -dir:data/files -cert:data/cert.pem -key:data/key.pem \
    -policy:adaptive -ackphase:1 -ackreorder:3 -ackdelay:1 -acklog:1
# client: download
build/msquic/bin/Release/quicfile client -target:<server-ip> -file:100MB.bin -acklog:1
# client: video session
build/msquic/bin/Release/quicfile client -target:<server-ip> -video:1 \
    -ladder:25,50,100,200,300,400 -segments:20 -segdur:2 -bufmax:10
```

Options: `-policy:default|fixed|litequic|adaptive`, `-threshold:N` (fixed), `-acknet:1`,
`-ackmax:N` (adaptive's ceiling, ≤ 255), `-ackphase:1`, `-ackreorder:N`, `-ackdelay:1`,
`-cc:cubic|bbr`, `-port:N`, `-out:<path>` (client: save the file), `-tag:<id>`. The client prints
a `RESULT` and a `TPUT` line per download, or a `SEG` line per segment and a `VIDEO` line per
video session; the server prints a `SERVER` line per connection; `-acklog:1` adds `[mcn]`
decisions and `[mcn-stats]`.

**Video client.** One QUIC connection, one stream per 2 s segment (`video_<rate>M.bin`, created
by `make-testdata.sh`). Bitrate choice (throughput rule): the highest bitrate ≤ 0.9 × the
harmonic mean throughput of the last 3 segments, starting at the lowest. Playback starts when the
first segment has arrived; the client waits while the buffer holds more than `bufmax − segdur`
seconds. Reported: startup delay, rebuffering events and time, average bitrate, number and size
of bitrate switches, and the linear QoE of Yin et al. (SIGCOMM'15) per segment:
`(Σ bitrate − Σ |bitrate change| − top bitrate × (rebuffer s + startup s)) / segments`.

### Troubleshooting

- **Server started over SSH dies when the session ends**: start it with
  `setsid -f ... < /dev/null` (the runner does). `nohup`/`timeout` do not survive.
- **Link speed**: check `cat /sys/class/net/<iface>/speed` (or `ethtool <iface>`) on both machines
  before a run. A gigabit port that comes up at 100 Mbit/s although both ends support 1 Gbit/s
  usually has a damaged cable; results from different link speeds are not comparable.
- **Downloads stall under heavy load** (seen a few times): the runner restarts the server; the
  client's output is in `failures.log`.
