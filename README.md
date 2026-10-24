# mcn26: Reducing QUIC ACK Overhead

Modern Networks course project. QUIC spends a large share of CPU on ACK
processing at high rates ([Zhang et al., WWW'24](https://arxiv.org/abs/2310.09423);
[LiteQUIC, MM'24](https://doi.org/10.1145/3664647.3681670)). We study how to
reduce ACK frequency without hurting throughput, using the QUIC ACK Frequency
extension ([draft-ietf-quic-ack-frequency](https://datatracker.ietf.org/doc/draft-ietf-quic-ack-frequency/)).

**Status:** repository setup. Project direction is in [docs/design.md](docs/design.md).

## Layout

| Path | Contents |
|---|---|
| `msquic/` | Submodule: [microsoft/msquic](https://github.com/microsoft/msquic), pinned to `v2.6.3` |
| `scripts/` | `setup.sh` builds msquic, secnetperf and tools |
| `docs/` | Related work, design notes, msquic code map, BibTeX |
| `proposal/` | Course proposal |
| `testbed/` | Network namespace / netem scripts (TBD) |
| `experiments/` | Experiment configs and runners (TBD) |
| `results/` | Summarised results (raw output is gitignored) |
| `analysis/` | Plotting scripts (TBD) |

## Quickstart (Linux)

```bash
git clone --recurse-submodules=msquic <repo-url> mcn26 && cd mcn26
scripts/setup.sh                 # INSTALL_DEPS=1 to apt-install dependencies first
```

Binaries land in `build/msquic/bin/Release/`. Loopback smoke test:

```bash
cd build/msquic/bin/Release
./secnetperf -exec:maxtput &                                   # server
./secnetperf -target:127.0.0.1 -exec:maxtput \
    -scenario:download -download:3s -ptput:1 -pconn:1           # client
```

Requirements: CMake ≥ 3.16, GCC, Ninja, `libssl-dev`, `libnuma-dev`.
Sources plus build take about 250 MB of disk.

## Team

- Kunal Umaji
- Kaveenraj G
- Aravind Sriram Kumar
