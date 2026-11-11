#!/usr/bin/env python3
"""Markdown tables for the report from one run (after analysis/parse_results.py).

    analysis/summarize.py results/<timestamp> [> tables.md]

Table 1, per server load and policy, pooled over file sizes (medians; [IQR] where shown):
  ACKs/MB, data packets per ACK, mean T, server receive-path µs/MB (ACK processing),
  client send-path µs/MB (ACK generation), both relative to fixed:2, download time
  relative to fixed:2 for the same file and load, lost packets per MB.
Table 2: median download time (s) per file, policy and load.
"""
import csv
import statistics
import sys
from collections import defaultdict
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
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q1, statistics.median(values), q3


def main():
    out = Path(sys.argv[1])
    rows = [r for r in csv.DictReader(open(out / "runs.csv")) if r["ok"] == "1"]
    order = next((l.split("=", 1)[1].split() for l in (out / "meta.txt").read_text().splitlines()
                  if l.startswith("policies=")), list(dict.fromkeys(r["spec"] for r in rows)))
    loads = [l for l in ["0", "2x"] if any(r["server_load"] == l for r in rows)]
    files = sorted({r["file"] for r in rows},
                   key=lambda f: (num(f.replace(".bin", "").rstrip("MGB")) or 0) * (1024 if "GB" in f else 1))

    def median_of(spec, load, metric, file=None):
        return statistics.median(num(r[metric]) for r in rows
                                 if r["spec"] == spec and r["server_load"] == load
                                 and (file is None or r["file"] == file) and num(r[metric]) is not None)

    base_time = {(l, f): median_of("fixed:2", l, "download_s", f) for l in loads for f in files
                 if any(r["spec"] == "fixed:2" and r["server_load"] == l and r["file"] == f for r in rows)}

    failed = sum(1 for r in csv.DictReader(open(out / "runs.csv")) if r["ok"] != "1")
    print(f"Run `{out.name}`: {len(rows)} successful downloads, {failed} failed.\n")
    print("| Server load | Policy | n | ACKs/MB | Data pkts/ACK | Mean T | Server ACK µs/MB | vs fixed:2 "
          "| Client ACK µs/MB | vs fixed:2 | Download time vs fixed:2 [IQR] | Lost pkts/MB |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for load in loads:
        base_srv = median_of("fixed:2", load, "server_recv_us_per_mb")
        base_cli = median_of("fixed:2", load, "client_send_us_per_mb")
        for spec in order:
            rs = [r for r in rows if r["spec"] == spec and r["server_load"] == load]
            if not rs:
                continue
            acks = med_iqr(num(r["server_recv_ack_frames"]) / (num(r["bytes"]) / MB) for r in rs)[1]
            ppa = med_iqr(num(r["data_pkts_per_ack"]) for r in rs)[1]
            tmean = med_iqr(num(r["threshold_mean"]) for r in rs)[1]
            srv = med_iqr(num(r["server_recv_us_per_mb"]) for r in rs)[1]
            cli = med_iqr(num(r["client_send_us_per_mb"]) for r in rs)[1]
            rel = med_iqr(num(r["download_s"]) / base_time[(load, r["file"])] for r in rs
                          if (load, r["file"]) in base_time)
            lost = med_iqr(num(r["server_lost_pkts"]) / (num(r["bytes"]) / MB) for r in rs)[1]
            t = "–" if tmean is None else f"{tmean:.0f}"
            print(f"| {load} | `{spec}` | {len(rs)} | {acks:.1f} | {ppa:.1f} | {t} | {srv:.0f} | "
                  f"{srv / base_srv:.2f} | {cli:.0f} | {cli / base_cli:.2f} | "
                  f"{rel[1]:.3f} [{rel[0]:.3f}–{rel[2]:.3f}] | {lost:.2f} |")
    print()
    for load in loads:
        print(f"Median download time (s), server load {load}:\n")
        print("| Policy | " + " | ".join(f.replace(".bin", "") for f in files) + " |")
        print("|---|" + "---|" * len(files))
        for spec in order:
            cells = []
            for f in files:
                try:
                    cells.append(f"{median_of(spec, load, 'download_s', f):.2f}")
                except statistics.StatisticsError:
                    cells.append("–")
            print(f"| `{spec}` | " + " | ".join(cells) + " |")
        print()


if __name__ == "__main__":
    main()
