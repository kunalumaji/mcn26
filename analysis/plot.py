#!/usr/bin/env python3
"""Figures for one evaluation run (after analysis/parse_results.py).

    analysis/plot.py results/<timestamp>

Writes results/<timestamp>/figures/. Download runs (runs.csv):
  acks_per_mb.png        ACK frames the server received per MB, per policy and load
  server_ack_cost.png    server receive-path time per MB (ACK processing), per policy and load
  client_ack_cost.png    client send-path time per MB (ACK generation), per policy and load
  goodput.png            goodput of the 500 MB and 1 GB downloads, per policy and load
  download_time.png      download time relative to fixed:2, per file size, policy and load
  cost_vs_acks.png       every download: ACKs per MB vs server receive-path time per MB
  threshold_timeline.png threshold over time for the adaptive policies (one download of the
                         largest file at the highest load)
  throughput_timeline.png throughput per 100 ms in that download, per policy (tput.log)
  load_sweep.png         mean threshold and goodput vs server load (runs with >= 4 load levels)
Video runs (videos.csv):
  video_qoe.png          bitrate, startup delay, rebuffering and QoE per policy and load
  video_ack.png          ACKs per MB and ACK cost on both sides per policy and load

Needs matplotlib. Medians with interquartile ranges; failed runs are excluded.
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

def load_name(load):
    if load == "0":
        return "no server load"
    if load.endswith("x"):
        return f"server load {load} ({load[:-1]} busy loops per core)"
    return f"server load {load} busy loops"

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


def load_runs(out, name="runs.csv"):
    path = out / name
    if not path.exists():
        return []
    rows = [r for r in csv.DictReader(open(path)) if r.get("ok") == "1"]
    for r in rows:
        r["mb"] = num(r["bytes"]) / MB
        r["acks_per_mb"] = num(r["server_recv_ack_frames"]) / r["mb"]
    return rows


def meta_loads(out, rows):
    """Load levels in the order the run used them (meta.txt), else from the rows."""
    for line in (out / "meta.txt").read_text().splitlines():
        if line.startswith("server_loads="):
            tokens = line.split("=", 1)[1].split()
            loads = [t for t in tokens if "=" not in t]
            return [l for l in loads if any(r["server_load"] == l for r in rows)]
    return sorted({r["server_load"] for r in rows})


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
        ax.set_title(load_name(load), loc="left")
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
            ax.set_title(f"{file.replace('.bin', '')}, {load_name(load)}", loc="left", fontsize=10)
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
                       label=load_name(load), alpha=0.9)
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


def tag_of(load, spec, file, rep=1):
    return f"L{load}_{spec.replace(':', '-')}_{file}_r{rep}"


def threshold_timeline(out, rows, loads, path):
    """Threshold over time for the adaptive policies in one download of the largest file at
    the highest load: one panel per policy (shared time axis), plus the CPU pressure."""
    decisions = list(csv.DictReader(open(out / "ack_decisions.csv")))
    files = sorted({r["file"] for r in rows}, key=file_mb)
    file = files[-1].replace(".bin", "")
    load = loads[-1]
    specs = [s for s in ["litequic", "adaptive", "adaptive:255", "adaptive+phase+reorder+delay",
                         "adaptive+net+phase+reorder+delay"]
             if any(d["tag"] == tag_of(load, s, file) for d in decisions)]
    if not specs or load == "0":
        print("threshold_timeline: no loaded download in this run, skipped")
        return
    fig, axes = plt.subplots(len(specs) + 1, 1, figsize=(9, 1.55 * (len(specs) + 1) + 1),
                             sharex=True)
    cpu_spec, cpu = None, []
    for ax, spec in zip(axes, specs):
        tag = tag_of(load, spec, file)
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
    fig.suptitle(f"ACK threshold over one {file} download, {load_name(load)}", x=0.01,
                 ha="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def throughput_timeline(out, loads, files, path):
    """Client throughput per 100 ms during one download of the largest file at the highest
    load, one panel per policy (shared axes)."""
    log = out / "tput.log"
    if not log.exists():
        print("throughput_timeline: no tput.log in this run, skipped")
        return
    file = files[-1].replace(".bin", "")
    load = loads[-1]
    series = {}
    for line in log.read_text().splitlines():
        fields = dict(t.split("=", 1) for t in line.split() if "=" in t)
        if fields.get("load") == load and fields.get("rep") == "1" and \
                fields.get("tag") == tag_of(load, fields.get("spec", ""), file) and fields.get("mbps"):
            series[fields["spec"]] = [float(v) for v in fields["mbps"].split(",")]
    specs = [s for s in ["fixed:2", "default", "fixed:100", "litequic", "adaptive", "adaptive:255"]
             if s in series]
    if not specs:
        print("throughput_timeline: no matching download, skipped")
        return
    top = max(max(v) for v in series.values())
    fig, axes = plt.subplots(len(specs), 1, figsize=(9, 1.3 * len(specs) + 1.2), sharex=True,
                             sharey=True)
    for ax, spec in zip(axes, specs):
        values = series[spec]
        ax.plot([i / 10 for i in range(len(values))], values, color=OURS if is_ours(spec) else BASELINE,
                linewidth=1.1)
        ax.set_ylim(0, top * 1.08)
        ax.set_title(f"{spec}: median {statistics.median(values):.0f} Mbit/s", loc="left", fontsize=10)
    axes[len(specs) // 2].set_ylabel("throughput (Mbit/s per 100 ms)")
    axes[-1].set_xlabel("time since request (s)")
    fig.suptitle(f"Client throughput during one {file} download, {load_name(load)}", x=0.01,
                 ha="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def load_sweep(rows, loads, path):
    """Mean threshold and goodput vs server load, one line per policy (<= 4 policies)."""
    specs = list(dict.fromkeys(r["spec"] for r in rows))[:4]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for color, spec in zip(SLOTS, specs):
        for ax, metric in zip(axes, ["threshold_mean", "goodput_mbps"]):
            ys = []
            for load in loads:
                values = [num(r[metric]) for r in rows if r["spec"] == spec and
                          r["server_load"] == load and num(r[metric]) is not None]
                ys.append(statistics.median(values) if values else float("nan"))
            ax.plot(range(len(loads)), ys, "o-", color=color, linewidth=2, markersize=7,
                    markeredgecolor=SURFACE, markeredgewidth=1.5, label=spec)
            ax.annotate(spec, (len(loads) - 1, ys[-1]), xytext=(6, 0), textcoords="offset points",
                        va="center", fontsize=9, color=TEXT)
    for ax, label in zip(axes, ["mean ACK threshold T", "goodput (Mbit/s)"]):
        ax.set_xticks(range(len(loads)), ["none" if l == "0" else l for l in loads])
        ax.set_xlabel("server load (busy loops per core)")
        ax.set_ylabel(label)
        ax.set_xlim(-0.3, len(loads) - 0.3 + 1.2)
    axes[0].set_ylim(0, 270)
    axes[1].set_ylim(bottom=0)
    axes[0].set_title("Threshold follows CPU load", loc="left")
    axes[1].set_title("Goodput", loc="left")
    fig.legend(frameon=False, loc="lower center", ncol=len(specs), bbox_to_anchor=(0.5, -0.01))
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def bar_grid(rows, order, loads, metrics, title, path):
    """Rows of panels (one per metric) x columns (one per load): horizontal bars per policy."""
    fig, axes = plt.subplots(len(metrics), len(loads),
                             figsize=(4.6 * len(loads), (0.3 * len(order) + 1.1) * len(metrics) + 0.8),
                             sharey=True, squeeze=False)
    for i, (metric, label) in enumerate(metrics):
        for j, load in enumerate(loads):
            ax = axes[i][j]
            for y, spec in enumerate(order):
                values = [num(r[metric]) for r in rows
                          if r["spec"] == spec and r["server_load"] == load and num(r[metric]) is not None]
                if not values:
                    continue
                q1, q2, q3 = iqr(values)
                ax.barh(y, q2, height=0.62, color=OURS if is_ours(spec) else BASELINE,
                        edgecolor=SURFACE, linewidth=2)
                ax.plot([q1, q3], [y, y], color=TEXT, linewidth=1.2)
            if i == 0:
                ax.set_title(load_name(load), loc="left")
            ax.set_xlabel(label)
            ax.grid(axis="y", visible=False)
            ax.set_yticks(range(len(order)), order)
            ax.invert_yaxis()
    handles = [plt.Rectangle((0, 0), 1, 1, color=BASELINE), plt.Rectangle((0, 0), 1, 1, color=OURS),
               plt.Line2D([0], [0], color=TEXT, linewidth=1.2)]
    fig.legend(handles, ["baseline policy", "our policy / modifiers", "interquartile range"],
               loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.005))
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    out = Path(sys.argv[1])
    figs = out / "figures"
    figs.mkdir(exist_ok=True)
    rows = load_runs(out)
    if rows:
        order = [s for s in policy_order(out, rows) if any(r["spec"] == s for r in rows)]
        loads = meta_loads(out, rows)
        files = sorted({r["file"] for r in rows}, key=file_mb)
        bar_panels(rows, order, loads, "acks_per_mb", "ACK frames received by the server per MB",
                   "ACKs per MB downloaded (fewer = less ACK work on both sides)", figs / "acks_per_mb.png")
        bar_panels(rows, order, loads, "server_recv_us_per_mb", "µs of server receive-path time per MB",
                   "Server ACK-handling cost (receive path: decrypt + process ACK packets)",
                   figs / "server_ack_cost.png")
        bar_panels(rows, order, loads, "client_send_us_per_mb", "µs of client send-path time per MB",
                   "Client ACK-generation cost (send path: build + encrypt + send ACK packets)",
                   figs / "client_ack_cost.png")
        large = [r for r in rows if file_mb(r["file"]) >= 500] or rows
        bar_panels(large, order, loads, "goodput_mbps", "goodput (Mbit/s), 500 MB and 1 GB downloads",
                   "Goodput measured at the client (higher is better)", figs / "goodput.png")
        download_time(rows, order, loads, figs / "download_time.png")
        cost_vs_acks(rows, loads, figs / "cost_vs_acks.png")
        threshold_timeline(out, rows, loads, figs / "threshold_timeline.png")
        throughput_timeline(out, loads, files, figs / "throughput_timeline.png")
        if len(loads) >= 4:
            load_sweep(rows, loads, figs / "load_sweep.png")
    videos = load_runs(out, "videos.csv")
    if videos:
        order = [s for s in policy_order(out, videos) if any(r["spec"] == s for r in videos)]
        loads = meta_loads(out, videos)
        for r in videos:
            r["rebuffer_s"] = (num(r["rebuffer_ms"]) or 0) / 1000
            r["startup_s"] = (num(r["startup_ms"]) or 0) / 1000
        bar_grid(videos, order, loads,
                 [("avg_bitrate_mbps", "average bitrate (Mbit/s)"),
                  ("startup_s", "startup delay (s)"),
                  ("rebuffer_s", "total rebuffering (s)"),
                  ("qoe", "QoE per segment")],
                 "Video streaming QoE per ACK policy (median, IQR)", figs / "video_qoe.png")
        bar_grid(videos, order, loads,
                 [("acks_per_mb", "ACK frames per MB"),
                  ("server_recv_us_per_mb", "server receive path µs/MB"),
                  ("client_send_us_per_mb", "client send path µs/MB")],
                 "Video streaming: ACK work per policy (median, IQR)", figs / "video_ack.png")
    print(f"Figures in {figs}")


if __name__ == "__main__":
    main()
