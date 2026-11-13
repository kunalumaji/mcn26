#!/usr/bin/env python3
"""Markdown tables for the report from one run (after analysis/parse_results.py).

    analysis/summarize.py results/<timestamp> [> tables.md]

Download runs (runs.csv), per server load and policy, pooled over file sizes (medians):
  Table 1, ACK work and cost: ACKs/MB, data packets per ACK, mean T, server receive-path
    µs/MB (ACK processing), client send-path µs/MB (ACK generation), server process CPU
    ms/MB; costs also relative to fixed:2 at the same load.
  Table 2, performance: goodput and 10th-percentile 100 ms throughput of the 500 MB and 1 GB
    downloads (steady state), goodput relative to fixed:2, download time relative to
    fixed:2 for the same file and load [IQR], lost packets per MB.
  Table 3: median download time (s) per file, policy and load.
Video runs (videos.csv), per server load and policy (medians):
  Table 4: average bitrate, startup delay, rebuffering, switches, QoE, ACKs/MB, mean T and
    ACK cost on both sides.
"""
import csv
import statistics
import sys
from pathlib import Path

MB = 1024 * 1024


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def med_iqr(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None, None, None
    if len(values) < 2:
        return values[0], values[0], values[0]
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q1, statistics.median(values), q3


def med(values):
    return med_iqr(values)[1]


def fmt(value, spec=".0f", missing="–"):
    return missing if value is None else format(value, spec)


def ratio(value, base):
    return None if value is None or not base else value / base


def file_mb(name):
    size = num(name.replace(".bin", "").rstrip("MGB")) or 0
    return size * 1024 if "GB" in name else size


def read_rows(path):
    if not path.exists():
        return [], 0
    rows = list(csv.DictReader(open(path)))
    ok = [r for r in rows if r.get("ok") == "1"]
    return ok, len(rows) - len(ok)


def meta_value(out, key):
    for line in (out / "meta.txt").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


def run_order(out, rows):
    policies = meta_value(out, "policies")
    order = policies.split() if policies else list(dict.fromkeys(r["spec"] for r in rows))
    loads_line = meta_value(out, "server_loads")
    loads = [t for t in loads_line.split() if "=" not in t] if loads_line else \
        sorted({r["server_load"] for r in rows})
    return order, [l for l in loads if any(r["server_load"] == l for r in rows)]


def downloads(out):
    rows, failed = read_rows(out / "runs.csv")
    if not rows:
        return
    order, loads = run_order(out, rows)
    files = sorted({r["file"] for r in rows}, key=file_mb)

    def select(spec, load, file=None):
        return [r for r in rows if r["spec"] == spec and r["server_load"] == load
                and (file is None or r["file"] == file)]

    base_time = {}
    for load in loads:
        for f in files:
            t = med(num(r["download_s"]) for r in select("fixed:2", load, f))
            if t:
                base_time[(load, f)] = t

    def per_mb_acks(r):
        return num(r["server_recv_ack_frames"]) / (num(r["bytes"]) / MB)

    print(f"Run `{out.name}`: {len(rows)} successful downloads, {failed} failed.\n")
    print("**ACK work and cost** (pooled over file sizes; ratios relative to `fixed:2` at the same load)\n")
    print("| Server load | Policy | n | ACKs/MB | Data pkts/ACK | Mean T | Server ACK µs/MB | vs fixed:2 "
          "| Client ACK µs/MB | vs fixed:2 | Server CPU ms/MB | vs fixed:2 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for load in loads:
        base = select("fixed:2", load)
        b_srv = med(num(r["server_recv_us_per_mb"]) for r in base)
        b_cli = med(num(r["client_send_us_per_mb"]) for r in base)
        b_cpu = med(num(r["server_cpu_ms_per_mb"]) for r in base)
        for spec in order:
            rs = select(spec, load)
            if not rs:
                continue
            srv = med(num(r["server_recv_us_per_mb"]) for r in rs)
            cli = med(num(r["client_send_us_per_mb"]) for r in rs)
            cpu = med(num(r["server_cpu_ms_per_mb"]) for r in rs)
            print(f"| {load} | `{spec}` | {len(rs)} | {fmt(med(per_mb_acks(r) for r in rs), '.1f')} | "
                  f"{fmt(med(num(r['data_pkts_per_ack']) for r in rs), '.1f')} | "
                  f"{fmt(med(num(r['threshold_mean']) for r in rs))} | {fmt(srv)} | "
                  f"{fmt(ratio(srv, b_srv), '.2f')} | {fmt(cli)} | {fmt(ratio(cli, b_cli), '.2f')} | "
                  f"{fmt(cpu, '.2f')} | {fmt(ratio(cpu, b_cpu), '.2f')} |")
    print()

    print("**Performance** (goodput and 100 ms throughput: 500 MB and 1 GB downloads; download time: "
          "all files, relative to `fixed:2` for the same file and load)\n")
    print("| Server load | Policy | Goodput Mbit/s [IQR] | vs fixed:2 | p10 of 100 ms throughput, Mbit/s "
          "| Download time vs fixed:2 [IQR] | Lost pkts/MB |")
    print("|---|---|---|---|---|---|---|")
    for load in loads:
        b_good = med(num(r["goodput_mbps"]) for r in select("fixed:2", load) if file_mb(r["file"]) >= 500)
        for spec in order:
            rs = select(spec, load)
            if not rs:
                continue
            large = [r for r in rs if file_mb(r["file"]) >= 500]
            good = med_iqr(num(r["goodput_mbps"]) for r in large)
            p10 = med(num(r.get("tput_p10_mbps")) for r in large)
            rel = med_iqr(num(r["download_s"]) / base_time[(load, r["file"])] for r in rs
                          if (load, r["file"]) in base_time)
            lost = med(num(r["server_lost_pkts"]) / (num(r["bytes"]) / MB) for r in rs)
            print(f"| {load} | `{spec}` | {fmt(good[1])} [{fmt(good[0])}–{fmt(good[2])}] | "
                  f"{fmt(ratio(good[1], b_good), '.2f')} | {fmt(p10)} | "
                  f"{fmt(rel[1], '.3f')} [{fmt(rel[0], '.3f')}–{fmt(rel[2], '.3f')}] | {fmt(lost, '.2f')} |")
    print()

    for load in loads:
        print(f"Median download time (s), server load {load}:\n")
        print("| Policy | " + " | ".join(f.replace(".bin", "") for f in files) + " |")
        print("|---|" + "---|" * len(files))
        for spec in order:
            cells = [fmt(med(num(r["download_s"]) for r in select(spec, load, f)), ".2f") for f in files]
            if any(c != "–" for c in cells):
                print(f"| `{spec}` | " + " | ".join(cells) + " |")
        print()


def videos(out):
    rows, failed = read_rows(out / "videos.csv")
    if not rows:
        return
    order, loads = run_order(out, rows)
    print(f"Run `{out.name}`: {len(rows)} successful video sessions, {failed} failed. "
          f"Settings: `{meta_value(out, 'video_opts')}`.\n")
    print("| Server load | Policy | n | Avg bitrate Mbit/s | Startup s | Rebuffer events | Rebuffer s "
          "| Switches | QoE/segment | ACKs/MB | Mean T | Server ACK µs/MB | Client ACK µs/MB |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for load in loads:
        for spec in order:
            rs = [r for r in rows if r["spec"] == spec and r["server_load"] == load]
            if not rs:
                continue

            def m(key, scale=1.0):
                value = med(num(r[key]) for r in rs)
                return None if value is None else value * scale
            acks = med(num(r["server_recv_ack_frames"]) / (num(r["bytes"]) / MB) for r in rs)
            print(f"| {load} | `{spec}` | {len(rs)} | {fmt(m('avg_bitrate_mbps'))} | "
                  f"{fmt(m('startup_ms', 1e-3), '.2f')} | {fmt(m('rebuffer_events'))} | "
                  f"{fmt(m('rebuffer_ms', 1e-3), '.2f')} | {fmt(m('switches'))} | {fmt(m('qoe'), '.1f')} | "
                  f"{fmt(acks, '.1f')} | {fmt(m('threshold_mean'))} | {fmt(m('server_recv_us_per_mb'))} | "
                  f"{fmt(m('client_send_us_per_mb'))} |")
    print()


def main():
    out = Path(sys.argv[1])
    downloads(out)
    videos(out)


if __name__ == "__main__":
    main()
