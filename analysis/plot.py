#!/usr/bin/env python3
"""Figures for one evaluation run (after analysis/parse_results.py).

    analysis/plot.py results/<timestamp>

Writes results/<timestamp>/figures/:
  acks_per_mb.png        ACK frames the server received per MB, per policy and load
  server_ack_cost.png    server receive-path time per MB (ACK processing), per policy and load
  client_ack_cost.png    client send-path time per MB (ACK generation), per policy and load
  download_time.png      download time relative to fixed:2, per file size, policy and load
  cost_vs_acks.png       every download: ACKs per MB vs server receive-path time per MB
  threshold_timeline.png threshold over time for the adaptive policies (one 2x-load download)

Needs matplotlib. Medians with interquartile ranges; failed downloads are excluded.
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

MB = 1024 * 1024

# Reference palette (dataviz skill, light mode), categorical slots in fixed order.
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
BASELINE, OURS = SLOTS[0], SLOTS[1]
LOAD_NAMES = {"0": "no server load", "2x": "server load 2x (all cores busy)"}

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": TEXT, "axes.labelcolor": TEXT_2, "xtick.color": TEXT_2, "ytick.color": TEXT,
    "axes.edgecolor": GRID, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold",
})


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_runs(out):
    rows = [r for r in csv.DictReader(open(out / "runs.csv")) if r["ok"] == "1"]
    for r in rows:
        r["mb"] = num(r["bytes"]) / MB
        r["acks_per_mb"] = num(r["server_recv_ack_frames"]) / r["mb"]
    return rows


def policy_order(out, rows):
    for line in (out / "meta.txt").read_text().splitlines():
        if line.startswith("policies="):
            return line.split("=", 1)[1].split()
    return list(dict.fromkeys(r["spec"] for r in rows))


def file_mb(name):
    """'20MB.bin' -> 20, '1GB.bin' -> 1024 (for sorting)."""
    size = num(name.replace(".bin", "").rstrip("MGB")) or 0
    return size * 1024 if "GB" in name else size


def is_ours(spec):
    return spec.startswith("adaptive") or "+" in spec


def iqr(values):
    values = sorted(values)
    if len(values) < 2:
        return values[0], values[0], values[0]
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q1, q2, q3


def bar_panels(rows, order, loads, metric, xlabel, title, path):
    """Horizontal bars (median, IQR whisker) per policy; one panel per load level."""
    fig, axes = plt.subplots(1, len(loads), figsize=(5.2 * len(loads), 0.42 * len(order) + 1.6),
                             sharey=True, squeeze=False)
    for ax, load in zip(axes[0], loads):
        for y, spec in enumerate(order):
            values = [num(r[metric]) for r in rows
                      if r["spec"] == spec and r["server_load"] == load and num(r[metric]) is not None]
            if not values:
                continue
            q1, q2, q3 = iqr(values)
            ax.barh(y, q2, height=0.62, color=OURS if is_ours(spec) else BASELINE,
                    edgecolor=SURFACE, linewidth=2)
            ax.plot([q1, q3], [y, y], color=TEXT, linewidth=1.2)
        ax.set_title(LOAD_NAMES.get(load, f"server load {load}"), loc="left")
        ax.set_xlabel(xlabel)
        ax.grid(axis="y", visible=False)
        ax.set_yticks(range(len(order)), order)
        ax.invert_yaxis()
    handles = [plt.Rectangle((0, 0), 1, 1, color=BASELINE), plt.Rectangle((0, 0), 1, 1, color=OURS),
               plt.Line2D([0], [0], color=TEXT, linewidth=1.2)]
    fig.legend(handles, ["baseline policy", "our policy / modifiers", "interquartile range"],
               loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.01))
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def download_time(rows, order, loads, path):
    """Dot = median download time / median fixed:2 time, whisker = IQR; panel per file and load."""
    files = sorted({r["file"] for r in rows}, key=file_mb)
    base = defaultdict(list)
    for r in rows:
        if r["spec"] == "fixed:2":
            base[(r["server_load"], r["file"])].append(num(r["download_s"]))
    base = {k: statistics.median(v) for k, v in base.items()}
    fig, axes = plt.subplots(len(loads), len(files),
                             figsize=(max(10, 3.6 * len(files) + 2.5), 0.32 * len(order) * len(loads) + 2.2),
                             sharey=True, squeeze=False)
    for i, load in enumerate(loads):
        for j, file in enumerate(files):
            ax = axes[i][j]
            ax.axvline(1.0, color=TEXT_2, linewidth=1, linestyle=(0, (3, 3)))
            for y, spec in enumerate(order):
                values = [num(r["download_s"]) / base[(load, file)] for r in rows
                          if r["spec"] == spec and r["server_load"] == load and r["file"] == file
                          and (load, file) in base]
                if not values:
                    continue
                q1, q2, q3 = iqr(values)
                color = OURS if is_ours(spec) else BASELINE
                ax.plot([q1, q3], [y, y], color=color, linewidth=2, solid_capstyle="round")
                ax.plot(q2, y, "o", color=color, markersize=7, markeredgecolor=SURFACE, markeredgewidth=1.5)
            ax.set_title(f"{file.replace('.bin', '')}, {LOAD_NAMES.get(load, load)}", loc="left", fontsize=10)
            ax.grid(axis="y", visible=False)
            ax.set_yticks(range(len(order)), order)
            ax.invert_yaxis()
            if i == len(loads) - 1:
                ax.set_xlabel("download time / fixed:2 (lower is faster)")
    fig.suptitle("Download time relative to fixed:2 (median, interquartile range)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold")
    handles = [plt.Line2D([0], [0], marker="o", color=BASELINE, linewidth=2),
               plt.Line2D([0], [0], marker="o", color=OURS, linewidth=2)]
    fig.legend(handles, ["baseline policy", "our policy / modifiers"], loc="lower center", ncol=2,
               frameon=False, bbox_to_anchor=(0.5, -0.005))
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def cost_vs_acks(rows, loads, path):
    """Each download: ACK frames per MB vs server receive-path time per MB (log-log)."""
    fig, ax = plt.subplots(figsize=(7, 4.6))
    for color, load in zip(SLOTS, loads):
        pts = [(r["acks_per_mb"], num(r["server_recv_us_per_mb"])) for r in rows
               if r["server_load"] == load and num(r["server_recv_us_per_mb"])]
        if pts:
            ax.scatter(*zip(*pts), s=36, color=color, edgecolor=SURFACE, linewidth=1.2,
                       label=LOAD_NAMES.get(load, load), alpha=0.9)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("ACK frames received by the server per MB")
    ax.set_ylabel("server receive-path time (µs per MB)")
    ax.set_title("Server ACK-handling cost vs ACKs per MB (one dot per download)", loc="left")
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def threshold_timeline(out, rows, path):
    """Threshold over time for the adaptive policies in one 2x-load download of the largest
    file: one panel per policy (shared time axis), plus the CPU pressure driving adaptive."""
    decisions = list(csv.DictReader(open(out / "ack_decisions.csv")))
    files = sorted({r["file"] for r in rows}, key=file_mb)
    file = files[-1].replace(".bin", "")
    specs = [s for s in ["litequic", "adaptive", "adaptive+phase+reorder+delay",
                         "adaptive+net+phase+reorder+delay"]
             if any(d["tag"] == f"L2x_{s}_{file}_r1" for d in decisions)]
    if not specs:
        print("threshold_timeline: no 2x-load download in this run, skipped")
        return
    fig, axes = plt.subplots(len(specs) + 1, 1, figsize=(9, 1.55 * (len(specs) + 1) + 1),
                             sharex=True)
    cpu_spec, cpu = None, []
    for ax, spec in zip(axes, specs):
        tag = f"L2x_{spec}_{file}_r1"
        points = sorted((num(d["t_ms"]) / 1000, num(d["new"])) for d in decisions if d["tag"] == tag)
        ax.step(*zip(*points), where="post", color=BASELINE if spec == "litequic" else OURS,
                linewidth=1.2)
        ax.set_ylim(0, 260)
        ax.set_yticks([0, 100, 200])
        ax.set_title(spec, loc="left", fontsize=10)
        if cpu_spec is None:
            cpu = sorted((num(d["t_ms"]) / 1000, num(d["cpu_pct"])) for d in decisions
                         if d["tag"] == tag and num(d.get("cpu_pct")) is not None)
            cpu_spec = spec if cpu else None
    axes[len(specs) // 2].set_ylabel("ACK threshold T")
    bottom = axes[-1]
    if cpu:
        bottom.plot(*zip(*cpu), color=OURS, linewidth=1.5)
    bottom.set_ylim(0, 105)
    bottom.set_title(f"{cpu_spec}: smoothed CPU pressure P (%) that drives T", loc="left", fontsize=10)
    bottom.set_xlabel("time since connection start (s)")
    fig.suptitle(f"ACK threshold over one {file} download, server load 2x", x=0.01, ha="left",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    out = Path(sys.argv[1])
    rows = load_runs(out)
    order = [s for s in policy_order(out, rows) if any(r["spec"] == s for r in rows)]
    loads = [l for l in ["0", "2x"] if any(r["server_load"] == l for r in rows)] or \
        sorted({r["server_load"] for r in rows})
    figs = out / "figures"
    figs.mkdir(exist_ok=True)
    bar_panels(rows, order, loads, "acks_per_mb", "ACK frames received by the server per MB",
               "ACKs per MB downloaded (fewer = less ACK work on both sides)", figs / "acks_per_mb.png")
    bar_panels(rows, order, loads, "server_recv_us_per_mb", "µs of server receive-path time per MB",
               "Server ACK-handling cost (receive path: decrypt + process ACK packets)",
               figs / "server_ack_cost.png")
    bar_panels(rows, order, loads, "client_send_us_per_mb", "µs of client send-path time per MB",
               "Client ACK-generation cost (send path: build + encrypt + send ACK packets)",
               figs / "client_ack_cost.png")
    download_time(rows, order, loads, figs / "download_time.png")
    cost_vs_acks(rows, loads, figs / "cost_vs_acks.png")
    threshold_timeline(out, rows, figs / "threshold_timeline.png")
    print(f"Figures in {figs}")


if __name__ == "__main__":
    main()
