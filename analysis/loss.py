#!/usr/bin/env python3
"""Tables and the report figure for the lossy-path experiments (run_all.sh 4 and 5).

    analysis/loss.py results/<run> [results/<run> ...]              # Markdown tables
    analysis/loss.py --fig out.pdf results/<exp4> results/<exp5>    # also the figure

Reads runs.csv (analysis/parse_results.py) of runs made with NETEM_PROFILES. Values are
medians over completed downloads, with interquartile ranges; ACK reduction and ACK-path
times are relative to fixed:2 on the same path and server load. A run in which every
policy is fixed:N is a threshold sweep (experiment 4); otherwise a policy comparison
(experiment 5).

Figure: (a) goodput vs fixed threshold on the clean and the lossy path (experiment 4);
(b) goodput and (c) ACK reduction of each policy on the lossy path by server load
(experiment 5).
"""
import argparse
import csv
import statistics
from pathlib import Path

MB = 1024 * 1024
LOAD_NAMES = {"0": "none"}

# Colours of the report's other figures (same colour = same policy); hatched = +phase.
STYLE = {
    "fixed:2": ("#2878b5", False, "fixed:2 (baseline)"),
    "fixed:100": ("#009e73", False, "fixed:100"),
    "fixed:100+phase": ("#009e73", True, "fixed:100+phase"),
    "litequic": ("#984ea3", False, "litequic"),
    "adaptive": ("#e69f00", False, "adaptive"),
    "adaptive+net": ("#cc79a7", False, "adaptive+net"),
    "adaptive+phase+reorder+delay": ("#d55e00", True, "adaptive+phase+reorder+delay"),
    "adaptive+net+phase+reorder+delay": ("#d55e00", False, "adaptive+all"),
}


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def med_iqr(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None, None, None
    if len(values) < 3:
        return statistics.median(values), None, None
    q = statistics.quantiles(values, n=4, method="inclusive")
    return statistics.median(values), q[0], q[2]


def med(values):
    return med_iqr(values)[0]


def fmt(value, spec=".0f"):
    return "–" if value is None else format(value, spec)


def loss_of(netem):
    """'100mbit:10ms:0.1' -> 0.1 (percent)."""
    parts = netem.split(":")
    return float(parts[2]) if len(parts) > 2 else 0.0


def acks_per_mb(r):
    frames, bytes_ = num(r["server_recv_ack_frames"]), num(r["bytes"])
    return frames / (bytes_ / MB) if frames and bytes_ else None


def threshold_of(spec):
    return int(spec.split(":")[1])


class Run:
    def __init__(self, out):
        self.out = Path(out)
        rows = list(csv.DictReader(open(self.out / "runs.csv")))
        self.rows = [r for r in rows if r["ok"] == "1"]
        self.failed = [r for r in rows if r["ok"] != "1"]
        self.specs = list(dict.fromkeys(r["spec"] for r in rows))
        self.loads = list(dict.fromkeys(r["server_load"] for r in rows))
        self.paths = sorted(dict.fromkeys(r["netem"] for r in rows), key=loss_of)
        self.sweep = all(s.startswith("fixed:") and "+" not in s for s in self.specs)
        if self.sweep:
            self.specs.sort(key=threshold_of)

    def select(self, spec, load, netem, failed=False):
        return [r for r in (self.failed if failed else self.rows)
                if r["spec"] == spec and r["server_load"] == load and r["netem"] == netem]

    def metric(self, spec, load, netem, key):
        fn = acks_per_mb if key == "acks_per_mb" else (lambda r: num(r[key]))
        return [fn(r) for r in self.select(spec, load, netem)]

    def reduction(self, spec, load, netem):
        """ACK frames per MiB removed relative to fixed:2 (%), median and IQR."""
        base = med(self.metric("fixed:2", load, netem, "acks_per_mb"))
        if not base:
            return None, None, None
        values = [100 * (1 - v / base) for v in self.metric(spec, load, netem, "acks_per_mb")
                  if v is not None]
        return med_iqr(values)


def print_tables(run):
    kind = "threshold sweep" if run.sweep else "policy comparison"
    print(f"Run `{run.out.name}` ({kind}): {len(run.rows)} completed downloads, "
          f"{len(run.failed)} stalled.\n")
    print("Medians [IQR]; ACK reduction and ACK-path times relative to `fixed:2` on the same "
          "path and load.\n")
    print("| Load | Path | Policy | n | Stalled | Goodput Mb/s | p10 Mb/s | Time s | ACKs/MiB "
          "| ACK reduction | Mean T | Server ACK µs/MiB | vs fixed:2 | Client ACK µs/MiB "
          "| vs fixed:2 | Lost pkts | Congestion events |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for load in run.loads:
        for netem in run.paths:
            b_srv = med(run.metric("fixed:2", load, netem, "server_recv_us_per_mb"))
            b_cli = med(run.metric("fixed:2", load, netem, "client_send_us_per_mb"))
            for spec in run.specs:
                rs = run.select(spec, load, netem)
                stalled = len(run.select(spec, load, netem, failed=True))
                if not rs and not stalled:
                    continue
                g, g1, g3 = med_iqr(run.metric(spec, load, netem, "goodput_mbps"))
                srv = med(run.metric(spec, load, netem, "server_recv_us_per_mb"))
                cli = med(run.metric(spec, load, netem, "client_send_us_per_mb"))
                iqr = f" [{fmt(g1)}–{fmt(g3)}]" if g1 is not None else ""
                print(f"| {LOAD_NAMES.get(load, load)} | {loss_of(netem):g}% loss | `{spec}` "
                      f"| {len(rs)} | {stalled} | {fmt(g, '.1f')}{iqr} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'tput_p10_mbps')), '.1f')} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'download_s')), '.1f')} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'acks_per_mb')), '.1f')} "
                      f"| {fmt(run.reduction(spec, load, netem)[0])}% "
                      f"| {fmt(med(run.metric(spec, load, netem, 'threshold_mean')))} "
                      f"| {fmt(srv)} | {fmt(srv / b_srv if srv and b_srv else None, '.2f')} "
                      f"| {fmt(cli)} | {fmt(cli / b_cli if cli and b_cli else None, '.2f')} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'server_lost_pkts')))} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'server_congestion_events')))} |")
    print()


def figure(sweep, policies, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    plt.rcParams.update({
        "font.family": "serif", "font.serif": ["Nimbus Roman", "Times New Roman", "Times"],
        "mathtext.fontset": "stix", "font.size": 10, "axes.labelsize": 10,
        "xtick.labelsize": 9, "ytick.labelsize": 9, "legend.fontsize": 9,
        "axes.linewidth": 0.8, "xtick.direction": "in", "ytick.direction": "in",
        "hatch.linewidth": 1.4, "pdf.fonttype": 42,
    })
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.1),
                             gridspec_kw={"width_ratios": [1, 1.25, 1.25], "wspace": 0.3})

    # (a) Fixed-threshold sweep on the clean and the lossy path (no CPU load).
    ax = axes[0]
    load = sweep.loads[0]
    ts = [threshold_of(s) for s in sweep.specs]
    styles = [("#222222", "o"), ("#6f8fa6", "s")]
    for (color, marker), netem in zip(styles, sweep.paths):
        stats = [med_iqr(sweep.metric(s, load, netem, "goodput_mbps")) for s in sweep.specs]
        y = [m for m, _, _ in stats]
        lo = [m - q1 if q1 is not None else 0 for m, q1, _ in stats]
        hi = [q3 - m if q3 is not None else 0 for m, _, q3 in stats]
        ax.errorbar(ts, y, yerr=[lo, hi], color=color, marker=marker, markersize=6, lw=1.8,
                    capsize=2.5, elinewidth=1, markeredgecolor="white", markeredgewidth=0.8,
                    label=f"{loss_of(netem):g}% loss", zorder=3)
    ax.set_xscale("log", base=2)
    ax.set_xticks(ts)
    ax.set_xticklabels([str(t) for t in ts])
    ax.minorticks_off()
    ax.set_xlabel("ACK threshold $T$ (packets)")
    ax.set_ylabel("Goodput (Mb/s)")
    ax.set_ylim(0, 105)
    ax.legend(loc="lower left", frameon=False, title="Fixed $T$, no load",
              title_fontsize=9)

    # (b), (c) Policies on the lossy path by server load.
    lossy = policies.paths[-1]
    specs = [s for s in policies.specs if s in STYLE]
    loads = policies.loads
    width = 0.8 / len(specs)
    for ax, key in [(axes[1], "goodput"), (axes[2], "reduction")]:
        for i, spec in enumerate(specs):
            color, hatched, _ = STYLE[spec]
            xs, ys, lo, hi = [], [], [], []
            for j, load in enumerate(loads):
                if key == "goodput":
                    m, q1, q3 = med_iqr(policies.metric(spec, load, lossy, "goodput_mbps"))
                else:
                    m, q1, q3 = policies.reduction(spec, load, lossy)
                if m is None:
                    continue
                xs.append(j - 0.4 + width * (i + 0.5))
                ys.append(m)
                lo.append(m - q1 if q1 is not None else 0)
                hi.append(q3 - m if q3 is not None else 0)
            ax.bar(xs, ys, width, color="white" if hatched else color,
                   edgecolor=color if hatched else "black", hatch="///" if hatched else None,
                   linewidth=0.8 if hatched else 0.6, zorder=2)
            ax.errorbar(xs, ys, yerr=[lo, hi], fmt="none", ecolor="black", elinewidth=0.9,
                        capsize=1.8, zorder=3)
        ax.set_xticks(range(len(loads)))
        ax.set_xticklabels([LOAD_NAMES.get(l, l) for l in loads])
        ax.set_xlim(-0.5, len(loads) - 0.5)
        ax.set_xlabel("Server CPU load")
        if key == "goodput":
            ax.set_ylabel(f"Goodput, {loss_of(lossy):g}% loss (Mb/s)")
            ax.set_ylim(bottom=0)
        else:
            ax.set_ylabel(f"ACK reduction vs fixed:2 (%)")
            ax.axhline(0, color="black", lw=0.8, zorder=1)

    for ax, label in zip(axes, "abc"):
        ax.grid(axis="y", linestyle=":", color="#b0b0b0", lw=0.7, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(top=True, right=True)
        ax.text(0.5, -0.24, f"({label})", transform=ax.transAxes, ha="center", va="top",
                fontsize=10)

    handles = [Patch(facecolor="white" if STYLE[s][1] else STYLE[s][0],
                     edgecolor=STYLE[s][0] if STYLE[s][1] else "black",
                     hatch="///" if STYLE[s][1] else None, label=STYLE[s][2]) for s in specs]
    # Legend read row by row in bar order (matplotlib fills columns first).
    ncol = min(4, len(handles))
    rows = -(-len(handles) // ncol)
    grid = [handles[r * ncol:(r + 1) * ncol] for r in range(rows)]
    ordered = [grid[r][c] for c in range(ncol) for r in range(rows) if c < len(grid[r])]
    fig.legend(handles=ordered, loc="upper center", ncol=ncol, frameon=False,
               bbox_to_anchor=(0.5, 1.12), columnspacing=1.2, handlelength=2.2)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    print(f"wrote {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--fig", help="write the report figure (needs one sweep and one "
                        "policy run)")
    args = parser.parse_args()
    runs = [Run(r) for r in args.runs]
    for run in runs:
        print_tables(run)
    if args.fig:
        sweeps = [r for r in runs if r.sweep]
        others = [r for r in runs if not r.sweep]
        if not sweeps or not others:
            parser.error("--fig needs a threshold-sweep run and a policy run")
        figure(sweeps[0], others[0], args.fig)


if __name__ == "__main__":
    main()
