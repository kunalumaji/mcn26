#!/usr/bin/env python3
"""Turn one experiments/run_quicfile.sh output directory into CSVs.

    analysis/parse_results.py results/<timestamp>

Reads client.log and server.log; writes:
  runs.csv           one row per download (client RESULT joined with server SERVER by tag)
  ack_decisions.csv  every ACK policy decision ([mcn] lines), with the run's tag
  summary.csv        medians per (server load, policy, file); also printed as a table
cpu_client.csv / cpu_server.csv (written by scripts/cpumon.py) are left as they are.
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

MB = 1024 * 1024


def kv(tokens):
    """Parse key=value tokens; tokens without '=' are ignored."""
    return dict(t.split("=", 1) for t in tokens if "=" in t)


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_client(path):
    runs = {}
    for line in path.read_text().splitlines():
        if "RESULT" not in line:
            continue
        head, result = line.split("RESULT", 1)
        row = kv(head.split())
        row.update(kv(result.split()))
        row["ok"] = 0 if "FAILED" in row else 1
        runs[row["tag"]] = row
    return runs


def parse_server(path):
    """Returns ({tag: SERVER fields}, [decision rows])."""
    servers, decisions = {}, []
    pending = defaultdict(list)  # (spec, conn) -> decisions not yet tied to a tag
    for line in path.read_text().splitlines():
        tokens = line.split()
        if len(tokens) < 2:
            continue
        spec = tokens[0].split("=", 1)[1]
        if tokens[1] == "[mcn]":
            row = kv(tokens[2:])
            pending[(spec, row["conn"])].append(row)
        elif tokens[1] == "SERVER":
            row = kv(tokens[2:])
            tag = row.get("tag", "-")
            servers[tag] = row
            # Connections on one server are sequential, so the decisions seen
            # for this conn pointer since its last SERVER line belong to it.
            for d in pending.pop((spec, row["conn"]), []):
                d["tag"] = tag
                d["spec"] = spec
                decisions.append(d)
    return servers, decisions


def mean_threshold(decisions, duration_ms):
    """Time-weighted mean threshold over the connection."""
    if not decisions:
        return None
    points = sorted((float(d["t_ms"]), float(d["new"])) for d in decisions)
    end = max(duration_ms or 0, points[-1][0])
    total = weighted = 0.0
    for (t, value), nxt in zip(points, points[1:] + [(end, None)]):
        span = nxt[0] - t
        weighted += value * span
        total += span
    return weighted / total if total > 0 else points[-1][1]


def main():
    out = Path(sys.argv[1])
    client = parse_client(out / "client.log")
    servers, decisions = parse_server(out / "server.log")
    by_tag = defaultdict(list)
    for d in decisions:
        by_tag[d["tag"]].append(d)

    rows = []
    for tag, c in client.items():
        s = servers.get(tag, {})
        spec = c["spec"]
        bytes_ = num(c.get("bytes")) or num(s.get("bytes"))
        mb = bytes_ / MB if bytes_ else None
        sent = num(c.get("client_sent_pkts"))
        recv = num(c.get("client_recv_pkts"))
        server_cpu = num(s.get("cpu_ms"))
        client_cpu = num(c.get("cpu_ms"))
        rows.append({
            "tag": tag,
            "server_load": c["load"],
            "spec": spec,
            "policy": spec.split(":")[0],
            "file": c["file"],
            "rep": c["rep"],
            "ok": c["ok"],
            "start_ts": c["start_ts"],
            "end_ts": c["end_ts"],
            "bytes": bytes_,
            "goodput_mbps": num(c.get("goodput_mbps")),
            "transfer_ms": num(c.get("transfer_ms")),
            "handshake_ms": num(c.get("handshake_ms")),
            "client_cpu_ms": client_cpu,
            "client_cpu_ms_per_mb": client_cpu / mb if client_cpu and mb else None,
            "client_sys_cpu_pct": num(c.get("sys_cpu_pct")),
            "client_sent_pkts": sent,
            "client_recv_pkts": recv,
            "data_pkts_per_ack": recv / sent if sent and recv else None,
            "server_cpu_ms": server_cpu,
            "server_cpu_ms_per_mb": server_cpu / mb if server_cpu and mb else None,
            "server_sys_cpu_pct": num(s.get("sys_cpu_pct")),
            "server_recv_ack_frames": num(s.get("server_recv_ack_frames")),
            "server_sent_pkts": num(s.get("server_sent_pkts")),
            "server_lost_pkts": num(s.get("server_lost_pkts")),
            "server_congestion_events": num(s.get("server_congestion_events")),
            "rtt_us": num(c.get("client_rtt_us")),
            "min_rtt_us": num(c.get("client_min_rtt_us")),
            "threshold_decisions": len(by_tag.get(tag, [])),
            "threshold_mean": mean_threshold(by_tag.get(tag, []), num(s.get("duration_ms"))),
        })

    with open(out / "runs.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["tag"])
        writer.writeheader()
        writer.writerows(rows)

    fields = ["tag", "spec", "policy", "t_ms", "old", "new"]
    extra = sorted({k for d in decisions for k in d} - set(fields) - {"conn"})
    with open(out / "ack_decisions.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields + extra, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(decisions)

    metrics = ["goodput_mbps", "client_cpu_ms_per_mb", "server_cpu_ms_per_mb",
               "server_recv_ack_frames", "data_pkts_per_ack", "threshold_mean",
               "server_lost_pkts", "client_sys_cpu_pct", "server_sys_cpu_pct"]
    groups = defaultdict(list)
    for r in rows:
        if r["ok"]:
            groups[(r["server_load"], r["spec"], r["file"])].append(r)
    summary = []
    for (load, spec, file), group in groups.items():
        entry = {"server_load": load, "spec": spec, "file": file, "n": len(group)}
        for m in metrics:
            values = [r[m] for r in group if r[m] is not None]
            entry[m] = round(statistics.median(values), 2) if values else None
        summary.append(entry)
    order = {s: i for i, s in enumerate(dict.fromkeys(r["spec"] for r in rows))}
    summary.sort(key=lambda e: (e["server_load"], num(e["file"].rstrip("MGB.bin")) or 0,
                                "GB" in e["file"], order[e["spec"]]))
    with open(out / "summary.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["server_load", "spec", "file", "n"] + metrics)
        writer.writeheader()
        writer.writerows(summary)

    failed = sum(1 for r in rows if not r["ok"])
    print(f"{len(rows)} downloads ({failed} failed), {len(decisions)} ACK decisions")
    cols = ["server_load", "file", "spec", "n", "goodput_mbps", "client_cpu_ms_per_mb",
            "server_cpu_ms_per_mb", "data_pkts_per_ack", "threshold_mean", "server_sys_cpu_pct"]
    widths = [max(len(c), *(len(str(e[c])) for e in summary)) for c in cols] if summary else []
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for e in summary:
        print("  ".join(str(e[c]).ljust(w) for c, w in zip(cols, widths)))


if __name__ == "__main__":
    main()
