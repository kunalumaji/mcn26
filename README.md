# mcn26: Reducing QUIC ACK Overhead

Modern Networks course project. QUIC spends a large share of CPU on ACK
processing at high rates ([Zhang et al., WWW'24](https://arxiv.org/abs/2310.09423);
[LiteQUIC, MM'24](https://doi.org/10.1145/3664647.3681670)). We study how to
reduce ACK frequency without hurting throughput, using the QUIC ACK Frequency
extension ([draft-ietf-quic-ack-frequency](https://datatracker.ietf.org/doc/draft-ietf-quic-ack-frequency/)).

**Status:** ACK policies `default`, `fixed:N`, `litequic` and our `adaptive`
policy (CPU only, or CPU + network with `-acknet:1`) are implemented in msquic and
evaluated with file downloads under CPU load. See [ACK policies](#ack-policies) below
and [docs/design.md](docs/design.md).

## Layout

| Path | Contents |
|---|---|
| `msquic/` | Vendored [microsoft/msquic](https://github.com/microsoft/msquic) `v2.6.3` (commit `16ca4d8`) with our ACK policies (search for `MCN26`) |
| `apps/quicfile/` | File download client/server on msquic, used for the evaluation |
| `scripts/` | `setup.sh` (build), `make-testdata.sh` (test files + cert), `cpu-load.sh` (CPU load), `cpumon.py` (CPU logging) |
| `experiments/` | `run_quicfile.sh`: the evaluation runner |
| `analysis/` | `parse_results.py`: turns a run into CSVs |
| `results/` | One folder per evaluation run |
| `docs/` | Design notes, BibTeX |
| `proposal/` | Course proposal |

## Build (Linux)

```bash
git clone <repo-url> mcn26 && cd mcn26
scripts/setup.sh                 # INSTALL_DEPS=1 to apt-install dependencies first
```

This builds msquic, `secnetperf` and `quicfile` into `build/msquic/bin/Release/`.
On first run it also downloads quictls (msquic's TLS library) into
`msquic/submodules/quictls/`; it is not stored in the repo.

Requirements: CMake ≥ 3.16, GCC, Ninja, `libssl-dev`, `libnuma-dev`, `openssl`, Python 3
(standard library only). Sources plus build take about 250 MB of disk; the test files 1.6 GB.

## ACK policies

A QUIC receiver sends an ACK after every **threshold `T`** ack-eliciting packets
(RFC 9000: `T = 2`), or when its delayed-ACK timer fires (25 ms in msquic). With
the [ACK Frequency extension](https://datatracker.ietf.org/doc/draft-ietf-quic-ack-frequency/)
the **data sender** can tell the receiver which `T` to use by sending an
`ACK_FREQUENCY` frame. A larger `T` means fewer ACKs: less CPU spent creating,
encrypting, sending, receiving and processing them, but less timely feedback for
congestion control and loss detection.

In our evaluation the server sends the file, so **the server chooses `T`** and the
client (the receiver) follows it. The policy is chosen per server process:

| Policy | How the server chooses `T` |
|---|---|
| `default` | msquic's built-in behaviour (raises `T` to its send batch size + 1 when it can't send fast enough) |
| `fixed:N` | `T = N` for the whole connection. `fixed:2` is the RFC 9000 behaviour |
| `litequic` | LiteQUIC (MM'24): once per RTT, `ACK_avg` = ACK frames processed per receive batch. `ACK_avg ≥ 2` (treated as CPU-limited): `T = T × ACK_avg`; otherwise `T = max(T/2, 2)` |
| `adaptive` | **Ours, CPU only**: `T` rises with CPU pressure, up to a fixed maximum |
| `adaptive` + `-acknet:1` | **Ours, CPU + network** (`adaptive+net` in the runner): the maximum comes from network conditions instead |

### How `adaptive` calculates the threshold

The server re-evaluates `T` every `max(smoothed RTT, 20 ms)` while it is receiving ACKs.

**Step 1: CPU pressure `P` (0–100%)**, the higher of two measurements over the last window:
- **QUIC thread pressure**: for the msquic thread that processes this connection's ACKs,
  `(time running + time waiting for a CPU) / elapsed time`, from
  `/proc/thread-self/schedstat`. Near 100% when QUIC itself is CPU-bound or starved.
- **Whole-machine utilisation**, from `/proc/stat`. Near 100% when the server is busy
  overall, so every cycle spent on ACKs is taken from other work.

`P` is smoothed: `P = (P_previous + P_new) / 2`.

**Step 2: CPU scale `f(P)` (0–1)**: `0` below 50%, `1` above 90%, linear in between
(`f = (P − 50) / 40`).

**Step 3: ceiling `C`**, the largest `T` allowed:
- **CPU only** (`-acknet:0`, the default): `C = 100` (`-ackmax:N` to change).
- **CPU + network** (`-acknet:1`): `C` comes from the path:
  1. `C = cwnd_packets / 4`: the congestion window in packets divided by 4, so that
     congestion control still receives at least 4 ACKs per window.
  2. If the path is **queueing** (smoothed RTT > 2 × minimum RTT *and* more than
     5 ms above it), `C` is halved: when the path is congested, feedback must stay timely.
  3. `C` is kept between 2 and 100.

**Step 4: target** `T_target = 2 + (C − 2) × f(P)`.

**Step 5: safety rules**:
- **CPU + network only:** if a congestion event (loss) happened in the window,
  `T_target ≤ max(T_current / 2, 2)`.
- `T` may at most **double** per window (gradual increase); decreases apply at once.

The new `T` is sent to the client in an `ACK_FREQUENCY` frame when it changes.

**Worked example** (CPU + network): the server is busy (`P = 80%`, so
`f = (80 − 50) / 40 = 0.75`), the congestion window is 600 packets, smoothed RTT 12 ms,
minimum RTT 4 ms (3× and 8 ms above it, so queueing). Ceiling: `600 / 4 = 150`, halved
for queueing to `75`, within `[2, 100]`, so `C = 75`. Target: `2 + 73 × 0.75 = 56`. If
the current `T` is 20, the increase is limited to `40`. If a loss had occurred in the window,
`T` would instead be `min(56, 20 / 2) = 10`. With CPU only, the same `P` gives
`2 + 98 × 0.75 = 75`, limited to `40` by the doubling rule.

With an idle server (`P ≤ 50%`), `T = 2` in both modes: the RFC behaviour.

Every decision is logged with `-acklog:1` and ends up in `ack_decisions.csv`, with
its inputs: `cpu_pct` (`P`), `thread_cpu_pct`, `sys_cpu_pct`, `scale_pct` (`f`),
`ceiling` (`C`), `cwnd_pkts`, `t_net` (network ceiling), `srtt_us`, `min_rtt_us`,
`queueing`, `loss`, and `old`/`new` (`T`).

Tuning (environment variables on the server): `MCN_ACK_CPU_LO` (50), `MCN_ACK_CPU_HI`
(90), `MCN_ACK_MAX` (100, same as `-ackmax`), `MCN_ACK_PER_CWND` (4).
Code: `QuicConnMcnAckAdaptive` in [msquic/src/core/connection.c](msquic/src/core/connection.c).

**msquic receiver detail:** reaching `T` only *marks* an ACK as needed; msquic sends it
after processing the whole receive batch. So the actual ratio is about
`max(T, packets per receive batch)`, and small `T` values change little at high packet rates.

## Reproducing the ACK policy evaluation

The evaluation uses two Linux machines: a **server** that serves files and a
**client** that downloads them. Everything is started from the client, which
controls the server over SSH.

### 1. One-time setup

On **both** machines, at the same commit:

```bash
git clone <repo-url> ~/Documents/mcn26 && cd ~/Documents/mcn26
scripts/setup.sh
```

On the **server**, create the test files (20MB, 100MB, 500MB, 1GB of random
data) and a self-signed TLS certificate in `data/` (gitignored):

```bash
scripts/make-testdata.sh
```

On the **client**, make sure `ssh <user>@<server>` works without a password (SSH key)
and that UDP ports 4433–4440 on the server are reachable.

### 2. Run the evaluation (on the client)

```bash
SERVER=kunal@10.21.232.3 experiments/run_quicfile.sh
```

With the defaults, this downloads each of the 4 files with each of the 6 policies
(`default fixed:2 fixed:10 fixed:100 litequic adaptive adaptive+net`), 3 times, at
2 server CPU load levels (none, and 2 busy-loop processes per core): 168 downloads.
Over Wi-Fi this takes about an hour. Everything is configurable through environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `SERVER` | `kunal@10.21.232.3` | SSH target of the server |
| `SERVER_ROOT` | `Documents/mcn26` | Repo path on the server (relative to its home) |
| `FILES` | `20MB.bin 100MB.bin 500MB.bin 1GB.bin` | Files to download |
| `POLICIES` | `default fixed:2 fixed:10 fixed:100 litequic adaptive adaptive+net` | ACK policies: `default`, `fixed:N`, `litequic`, `adaptive` (CPU only), `adaptive+net` (CPU + network) |
| `SERVER_LOADS` | `0 2x` | Server CPU load levels: `0` none, `N` busy loops, `Kx` K per core |
| `CLIENT_LOAD` | `0` | CPU load on the client during all runs (same format) |
| `REPS` | `3` | Repetitions per (load, file, policy) |
| `CC` | `cubic` | Congestion control: `cubic` or `bbr` |
| `ALLOW_DIRTY` | unset | `1` skips the check that both machines are on the same clean commit |

A quick check of the whole pipeline (about 1 minute):

```bash
FILES=20MB.bin REPS=1 SERVER_LOADS="0 2x" experiments/run_quicfile.sh
```

How the runner works:
- It refuses to start unless client and server are on the **same commit** with no
  uncommitted changes, so results always come from identical code.
- It starts one server per policy on its own port (4433, 4434, …) and interleaves
  downloads across policies, rotating the order every repetition, so changes in
  network conditions over time affect all policies equally.
- Server CPU load comes from `scripts/cpu-load.sh` (busy loops at normal priority),
  which compete with the QUIC server for CPU.
- `scripts/cpumon.py` logs CPU on both machines every 200 ms.

### 3. Outputs

Each run writes `results/<timestamp>/`:

| File | Contents |
|---|---|
| `meta.txt` | Settings, commits, hosts, kernels |
| `restarts.log` / `failures.log` | Only if downloads failed: which servers were restarted, and the client's error output |
| `client.log` / `server.log` | Raw `RESULT` / `SERVER` lines and the policies' decision log |
| `runs.csv` | **One row per download**: `download_s` (connection start to last byte: the time to compare policies), `handshake_ms`, `ttfb_ms` (request to first byte), `transfer_ms` (request to last byte), goodput, client and server process CPU (total and per MB), whole-machine CPU % on both sides, packets, ACKs, data packets per ACK, losses, RTT, mean ACK threshold |
| `ack_decisions.csv` | Every threshold decision over time (`tag`, `t_ms`, `old`, `new`, and the policy's inputs, e.g. `cpu_pct`, `thread_cpu_pct`, `sys_cpu_pct`, `cwnd_pkts`, `t_net`, `loss` for `adaptive`) |
| `summary.csv` | Medians per (server load, policy, file); also printed at the end of the run |
| `cpu_client.csv` / `cpu_server.csv` | CPU time series: `ts`, `sys_cpu_pct` (whole machine), `proc_cpu_pct` (`quicfile`, % of one core), `loadavg1` |

`runs.csv` and `ack_decisions.csv` share the `tag` column
(`L<load>_<policy>_<file>_r<rep>`). The `start_ts`/`end_ts` columns of `runs.csv` (Unix time
on the client) locate each download in the CPU time series.
`analysis/parse_results.py results/<timestamp>` regenerates the CSVs.

### Running a single download by hand

```bash
# server
build/msquic/bin/Release/quicfile server -dir:data/files -cert:data/cert.pem -key:data/key.pem \
    -policy:adaptive -acklog:1
# client
build/msquic/bin/Release/quicfile client -target:<server-ip> -file:100MB.bin
```

`-policy:default|fixed|litequic|adaptive`, `-threshold:N` (for `fixed`), `-acknet:1` and
`-ackmax:N` (for `adaptive`), `-cc:cubic|bbr`,
`-port:N`, `-out:<path>` (client: save the file; by default it is discarded). When
starting the server over SSH, use `setsid -f ... < /dev/null`: a server started with
`nohup`/`timeout` is killed when the SSH session closes.

## Team

- Kunal Umaji
- Kaveenraj G
- Aravindh Sriram Kumar A G
