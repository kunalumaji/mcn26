# mcn26: Reducing QUIC ACK Overhead

Modern Networks course project. QUIC spends a large share of CPU on ACK
processing at high rates ([Zhang et al., WWW'24](https://arxiv.org/abs/2310.09423);
[LiteQUIC, MM'24](https://doi.org/10.1145/3664647.3681670)). We study how to
reduce ACK frequency without hurting throughput, using the QUIC ACK Frequency
extension ([draft-ietf-quic-ack-frequency](https://datatracker.ietf.org/doc/draft-ietf-quic-ack-frequency/)).

**Status:** ACK policies `default`, `fixed:N`, `litequic` and our `adaptive`
policy are implemented in msquic and evaluated with file downloads under CPU load.
Policy details: [docs/design.md](docs/design.md).

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
(`default fixed:2 fixed:10 fixed:100 litequic adaptive`), 3 times, at 2 server CPU
load levels (none, and 2 busy-loop processes per core): 144 downloads. Over Wi-Fi
this takes about 1.5 hours. Everything is configurable through environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `SERVER` | `kunal@10.21.232.3` | SSH target of the server |
| `SERVER_ROOT` | `Documents/mcn26` | Repo path on the server (relative to its home) |
| `FILES` | `20MB.bin 100MB.bin 500MB.bin 1GB.bin` | Files to download |
| `POLICIES` | `default fixed:2 fixed:10 fixed:100 litequic adaptive` | ACK policies (`policy[:threshold]`) |
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
| `client.log` / `server.log` | Raw `RESULT` / `SERVER` lines and the policies' decision log |
| `runs.csv` | **One row per download**: goodput, transfer time, client and server process CPU (total and per MB), whole-machine CPU % on both sides, packets, ACKs, data packets per ACK, losses, RTT, mean ACK threshold |
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

`-policy:default|fixed|litequic|adaptive`, `-threshold:N` (for `fixed`), `-cc:cubic|bbr`,
`-port:N`, `-out:<path>` (client: save the file; by default it is discarded). When
starting the server over SSH, use `setsid -f ... < /dev/null`: a server started with
`nohup`/`timeout` is killed when the SSH session closes.

## Team

- Kunal Umaji
- Kaveenraj G
- Aravindh Sriram Kumar A G
